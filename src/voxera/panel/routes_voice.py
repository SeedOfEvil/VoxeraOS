"""Panel routes for the operator-facing voice status, TTS generation, and STT transcription surfaces.

Read-only diagnostic routes for STT/TTS configuration and availability,
plus minimal operator-facing generation/transcription forms that exercise
the canonical ``synthesize_text(...)`` and ``transcribe_audio_file(...)``
pipelines end to end.

The TTS generation surface is artifact-oriented: it produces a real audio
file on success and reports the output path and key response fields.
The STT transcription surface is file-oriented: it accepts an audio file
path, runs transcription, and renders the truthful result inline.
No browser playback, no audio player, no live microphone UX.

The Voice Workbench surface chains STT -> Vera -> optional TTS into a
single bounded operator flow. It is conversational only: it never
creates previews, submits jobs, or mutates real-world state.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..vera.session_store import new_session_id
from ..voice.flags import load_voice_foundation_flags
from ..voice.input import transcribe_audio_file
from ..voice.output import synthesize_text, synthesize_text_async
from ..voice.stt_protocol import STT_STATUS_SUCCEEDED, stt_response_as_dict
from ..voice.tts_protocol import TTS_STATUS_SUCCEEDED, tts_response_as_dict
from ..voice.voice_status_summary import build_voice_status_summary
from . import voice_workbench


def register_voice_routes(
    app: FastAPI,
    *,
    templates: Any,
    require_operator_auth_from_request: Callable[[Request], None],
    require_mutation_guard: Callable[[Request], Awaitable[None]],
    csrf_cookie: str,
    request_value: Callable[..., Awaitable[str]],
    queue_root: Callable[[], Path],
) -> None:
    @app.get("/voice/status", response_class=HTMLResponse)
    def voice_status_page(request: Request) -> HTMLResponse:
        require_operator_auth_from_request(request)
        try:
            flags = load_voice_foundation_flags()
            summary = build_voice_status_summary(flags)
            error = None
        except Exception as exc:
            summary = None
            error = f"Failed to load voice status: {type(exc).__name__}: {exc}"

        csrf_token = request.cookies.get(csrf_cookie) or secrets.token_urlsafe(24)
        tmpl = templates.get_template("voice.html")
        html = tmpl.render(
            summary=summary,
            error=error,
            csrf_token=csrf_token,
            tts_result=None,
            stt_result=None,
            workbench_result=None,
            workbench_session_id=new_session_id(),
        )
        response = HTMLResponse(content=html)
        response.set_cookie(csrf_cookie, csrf_token, httponly=False, samesite="strict")
        return response

    @app.get("/voice/status.json")
    def voice_status_json(request: Request) -> JSONResponse:
        require_operator_auth_from_request(request)
        try:
            flags = load_voice_foundation_flags()
            summary = build_voice_status_summary(flags)
            return JSONResponse({"ok": True, "voice": summary})
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                status_code=500,
            )

    @app.post("/voice/tts/generate", response_class=HTMLResponse)
    async def voice_tts_generate(request: Request) -> HTMLResponse:
        await require_mutation_guard(request)

        text = (await request_value(request, "tts_text", "")).strip()
        voice_id = (await request_value(request, "tts_voice_id", "")).strip() or None
        language = (await request_value(request, "tts_language", "")).strip() or None

        # Reload status summary for the page context
        flags = None
        try:
            flags = load_voice_foundation_flags()
            summary = build_voice_status_summary(flags)
            page_error = None
        except Exception as exc:
            summary = None
            page_error = f"Failed to load voice status: {type(exc).__name__}: {exc}"

        tts_result: dict[str, object]

        # Validate text input
        if not text:
            tts_result = {
                "success": False,
                "error": "Text input is required.",
                "status": "failed",
            }
        elif flags is None:
            tts_result = {
                "success": False,
                "error": "Cannot synthesize: voice configuration failed to load.",
                "status": "unavailable",
            }
        else:
            try:
                start_ms = int(time.time() * 1000)
                response = synthesize_text(
                    text=text,
                    flags=flags,
                    voice_id=voice_id,
                    language=language,
                )
                elapsed_ms = int(time.time() * 1000) - start_ms

                succeeded = response.status == TTS_STATUS_SUCCEEDED and response.audio_path
                tts_result = {
                    "success": bool(succeeded),
                    "status": response.status,
                    "audio_path": response.audio_path if succeeded else None,
                    "backend": response.backend,
                    "error": response.error if not succeeded else None,
                    "error_class": response.error_class if not succeeded else None,
                    "audio_duration_ms": response.audio_duration_ms,
                    "inference_ms": response.inference_ms,
                    "elapsed_ms": elapsed_ms,
                    "request_id": response.request_id,
                    "input_text": text,
                    "input_voice_id": voice_id,
                    "input_language": language,
                    "response_dict": tts_response_as_dict(response),
                }
            except ValueError as exc:
                tts_result = {
                    "success": False,
                    "error": str(exc),
                    "status": "failed",
                }
            except Exception as exc:
                tts_result = {
                    "success": False,
                    "error": f"Unexpected error: {type(exc).__name__}: {exc}",
                    "status": "failed",
                }

        csrf_token = request.cookies.get(csrf_cookie) or secrets.token_urlsafe(24)
        tmpl = templates.get_template("voice.html")
        html = tmpl.render(
            summary=summary,
            error=page_error,
            csrf_token=csrf_token,
            tts_result=tts_result,
            stt_result=None,
            workbench_result=None,
            workbench_session_id=new_session_id(),
        )
        resp = HTMLResponse(content=html)
        resp.set_cookie(csrf_cookie, csrf_token, httponly=False, samesite="strict")
        return resp

    @app.post("/voice/tts/generate.json")
    async def voice_tts_generate_json(request: Request) -> JSONResponse:
        await require_mutation_guard(request)

        text = (await request_value(request, "tts_text", "")).strip()
        voice_id = (await request_value(request, "tts_voice_id", "")).strip() or None
        language = (await request_value(request, "tts_language", "")).strip() or None

        if not text:
            return JSONResponse(
                {"ok": False, "error": "Text input is required."},
                status_code=400,
            )

        try:
            flags = load_voice_foundation_flags()
            response = synthesize_text(
                text=text,
                flags=flags,
                voice_id=voice_id,
                language=language,
            )
            succeeded = response.status == TTS_STATUS_SUCCEEDED and response.audio_path
            return JSONResponse(
                {
                    "ok": bool(succeeded),
                    "tts": tts_response_as_dict(response),
                }
            )
        except ValueError as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc)},
                status_code=400,
            )
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                status_code=500,
            )

    @app.post("/voice/stt/transcribe", response_class=HTMLResponse)
    async def voice_stt_transcribe(request: Request) -> HTMLResponse:
        await require_mutation_guard(request)

        audio_path = (await request_value(request, "stt_audio_path", "")).strip()
        language = (await request_value(request, "stt_language", "")).strip() or None

        # Reload status summary for the page context
        flags = None
        try:
            flags = load_voice_foundation_flags()
            summary = build_voice_status_summary(flags)
            page_error = None
        except Exception as exc:
            summary = None
            page_error = f"Failed to load voice status: {type(exc).__name__}: {exc}"

        stt_result: dict[str, object]

        # Validate audio path input
        if not audio_path:
            stt_result = {
                "success": False,
                "error": "Audio file path is required.",
                "status": "failed",
            }
        elif flags is None:
            stt_result = {
                "success": False,
                "error": "Cannot transcribe: voice configuration failed to load.",
                "status": "unavailable",
            }
        else:
            try:
                start_ms = int(time.time() * 1000)
                response = transcribe_audio_file(
                    audio_path=audio_path,
                    flags=flags,
                    language=language,
                )
                elapsed_ms = int(time.time() * 1000) - start_ms

                succeeded = response.status == STT_STATUS_SUCCEEDED and response.transcript
                stt_result = {
                    "success": bool(succeeded),
                    "status": response.status,
                    "transcript": response.transcript if succeeded else None,
                    "language": response.language if succeeded else None,
                    "backend": response.backend,
                    "error": response.error if not succeeded else None,
                    "error_class": response.error_class if not succeeded else None,
                    "audio_duration_ms": response.audio_duration_ms,
                    "inference_ms": response.inference_ms,
                    "elapsed_ms": elapsed_ms,
                    "request_id": response.request_id,
                    "input_audio_path": audio_path,
                    "input_language": language,
                    "response_dict": stt_response_as_dict(response),
                }
            except Exception as exc:
                stt_result = {
                    "success": False,
                    "error": f"Unexpected error: {type(exc).__name__}: {exc}",
                    "status": "failed",
                }

        csrf_token = request.cookies.get(csrf_cookie) or secrets.token_urlsafe(24)
        tmpl = templates.get_template("voice.html")
        html = tmpl.render(
            summary=summary,
            error=page_error,
            csrf_token=csrf_token,
            tts_result=None,
            stt_result=stt_result,
            workbench_result=None,
            workbench_session_id=new_session_id(),
        )
        resp = HTMLResponse(content=html)
        resp.set_cookie(csrf_cookie, csrf_token, httponly=False, samesite="strict")
        return resp

    @app.post("/voice/stt/transcribe.json")
    async def voice_stt_transcribe_json(request: Request) -> JSONResponse:
        await require_mutation_guard(request)

        audio_path = (await request_value(request, "stt_audio_path", "")).strip()
        language = (await request_value(request, "stt_language", "")).strip() or None

        if not audio_path:
            return JSONResponse(
                {"ok": False, "error": "Audio file path is required."},
                status_code=400,
            )

        try:
            flags = load_voice_foundation_flags()
            response = transcribe_audio_file(
                audio_path=audio_path,
                flags=flags,
                language=language,
            )
            succeeded = response.status == STT_STATUS_SUCCEEDED and response.transcript
            return JSONResponse(
                {
                    "ok": bool(succeeded),
                    "stt": stt_response_as_dict(response),
                }
            )
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                status_code=500,
            )

    @app.post("/voice/workbench/run", response_class=HTMLResponse)
    async def voice_workbench_run(request: Request) -> HTMLResponse:
        """Run the bounded Voice Workbench: STT -> Vera -> optional TTS.

        Conversational only: this lane never creates previews, submits
        queue jobs, or implies real-world side effects.  The transcript
        is persisted as a ``voice_transcript``-origin turn using the
        canonical Vera session store, and Vera's textual reply is
        rendered inline.  The "Speak response" toggle runs canonical
        TTS; text stays authoritative even if TTS fails.
        """
        await require_mutation_guard(request)

        audio_path = (await request_value(request, "workbench_audio_path", "")).strip()
        language = (await request_value(request, "workbench_language", "")).strip() or None
        session_id_raw = (await request_value(request, "workbench_session_id", "")).strip()
        session_id = session_id_raw or new_session_id()
        send_to_vera_raw = (await request_value(request, "workbench_send_to_vera", "")).strip()
        speak_response_raw = (await request_value(request, "workbench_speak_response", "")).strip()
        send_to_vera = send_to_vera_raw.lower() in {"1", "true", "on", "yes"}
        speak_response = speak_response_raw.lower() in {"1", "true", "on", "yes"}

        flags = None
        try:
            flags = load_voice_foundation_flags()
            summary = build_voice_status_summary(flags)
            page_error = None
        except Exception as exc:
            summary = None
            page_error = f"Failed to load voice status: {type(exc).__name__}: {exc}"

        workbench_result: dict[str, Any] = {
            "session_id": session_id,
            "input_audio_path": audio_path,
            "input_language": language,
            "send_to_vera_requested": send_to_vera,
            "speak_response_requested": speak_response,
            "stt": None,
            "vera": None,
            "tts": None,
        }

        # ── Step 1: STT ──────────────────────────────────────────────
        stt_ok = False
        transcript_text: str | None = None
        if not audio_path:
            workbench_result["stt"] = {
                "success": False,
                "status": "failed",
                "error": "Audio file path is required.",
            }
        elif flags is None:
            workbench_result["stt"] = {
                "success": False,
                "status": "unavailable",
                "error": "Cannot transcribe: voice configuration failed to load.",
            }
        else:
            try:
                start_ms = int(time.time() * 1000)
                stt_response = transcribe_audio_file(
                    audio_path=audio_path,
                    flags=flags,
                    language=language,
                    session_id=session_id,
                )
                elapsed_ms = int(time.time() * 1000) - start_ms
                stt_ok = bool(
                    stt_response.status == STT_STATUS_SUCCEEDED and stt_response.transcript
                )
                transcript_text = stt_response.transcript if stt_ok else None
                workbench_result["stt"] = {
                    "success": stt_ok,
                    "status": stt_response.status,
                    "transcript": transcript_text,
                    "language": stt_response.language if stt_ok else None,
                    "backend": stt_response.backend,
                    "error": stt_response.error if not stt_ok else None,
                    "error_class": stt_response.error_class if not stt_ok else None,
                    "audio_duration_ms": stt_response.audio_duration_ms,
                    "inference_ms": stt_response.inference_ms,
                    "elapsed_ms": elapsed_ms,
                    "request_id": stt_response.request_id,
                    "response_dict": stt_response_as_dict(stt_response),
                }
            except Exception as exc:
                workbench_result["stt"] = {
                    "success": False,
                    "status": "failed",
                    "error": f"Unexpected error: {type(exc).__name__}: {exc}",
                }

        # ── Step 2: Vera (only when a real transcript exists AND operator opted in) ──
        vera_ok = False
        vera_answer: str | None = None
        if stt_ok and transcript_text and send_to_vera and flags is not None:
            vera_result = await voice_workbench.run_transcript_to_vera_turn(
                transcript_text=transcript_text,
                session_id=session_id,
                queue_root=queue_root(),
                flags=flags,
            )
            vera_ok = vera_result.ok
            vera_answer = vera_result.vera_answer
            workbench_result["vera"] = {
                "success": vera_result.ok,
                "status": vera_result.status,
                "answer": vera_result.vera_answer,
                "vera_status": vera_result.vera_status,
                "error": vera_result.error,
            }
        elif stt_ok and transcript_text and send_to_vera and flags is None:
            workbench_result["vera"] = {
                "success": False,
                "status": voice_workbench.STATUS_VOICE_INPUT_DISABLED,
                "answer": None,
                "vera_status": None,
                "error": "Cannot send to Vera: voice configuration failed to load.",
            }

        # ── Step 3: optional TTS on Vera's reply ────────────────────
        if vera_ok and vera_answer and speak_response and flags is not None:
            try:
                start_ms = int(time.time() * 1000)
                tts_response = await synthesize_text_async(
                    text=vera_answer,
                    flags=flags,
                    session_id=session_id,
                )
                elapsed_ms = int(time.time() * 1000) - start_ms
                tts_ok = bool(
                    tts_response.status == TTS_STATUS_SUCCEEDED and tts_response.audio_path
                )
                workbench_result["tts"] = {
                    "success": tts_ok,
                    "status": tts_response.status,
                    "audio_path": tts_response.audio_path if tts_ok else None,
                    "backend": tts_response.backend,
                    "error": tts_response.error if not tts_ok else None,
                    "error_class": tts_response.error_class if not tts_ok else None,
                    "audio_duration_ms": tts_response.audio_duration_ms,
                    "inference_ms": tts_response.inference_ms,
                    "elapsed_ms": elapsed_ms,
                    "request_id": tts_response.request_id,
                    "response_dict": tts_response_as_dict(tts_response),
                }
            except ValueError as exc:
                workbench_result["tts"] = {
                    "success": False,
                    "status": "failed",
                    "error": str(exc),
                }
            except Exception as exc:
                workbench_result["tts"] = {
                    "success": False,
                    "status": "failed",
                    "error": f"Unexpected error: {type(exc).__name__}: {exc}",
                }
        elif vera_ok and speak_response and flags is None:
            workbench_result["tts"] = {
                "success": False,
                "status": "unavailable",
                "error": "Cannot synthesize: voice configuration failed to load.",
            }

        csrf_token = request.cookies.get(csrf_cookie) or secrets.token_urlsafe(24)
        tmpl = templates.get_template("voice.html")
        html = tmpl.render(
            summary=summary,
            error=page_error,
            csrf_token=csrf_token,
            tts_result=None,
            stt_result=None,
            workbench_result=workbench_result,
            workbench_session_id=session_id,
        )
        resp = HTMLResponse(content=html)
        resp.set_cookie(csrf_cookie, csrf_token, httponly=False, samesite="strict")
        return resp
