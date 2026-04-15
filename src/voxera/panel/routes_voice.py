"""Panel routes for the operator-facing voice status and TTS generation surface.

Read-only diagnostic routes for STT/TTS configuration and availability,
plus a minimal operator-facing TTS generation form that exercises the
canonical ``synthesize_text(...)`` pipeline end to end.

The generation surface is artifact-oriented: it produces a real audio
file on success and reports the output path and key response fields.
No browser playback, no audio player, no live microphone UX.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..voice.flags import load_voice_foundation_flags
from ..voice.output import synthesize_text
from ..voice.tts_protocol import TTS_STATUS_SUCCEEDED, tts_response_as_dict
from ..voice.voice_status_summary import build_voice_status_summary


def register_voice_routes(
    app: FastAPI,
    *,
    templates: Any,
    require_operator_auth_from_request: Callable[[Request], None],
    require_mutation_guard: Callable[[Request], Awaitable[None]],
    csrf_cookie: str,
    request_value: Callable[..., Awaitable[str]],
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
        html = tmpl.render(summary=summary, error=error, csrf_token=csrf_token, tts_result=None)
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
