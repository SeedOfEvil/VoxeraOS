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

from ..config import DEFAULT_VERA_WEB_BASE_URL
from ..vera.session_store import new_session_id, read_session_turns
from ..voice.flags import load_voice_foundation_flags
from ..voice.input import transcribe_audio_file
from ..voice.output import synthesize_text, synthesize_text_async
from ..voice.stt_protocol import STT_STATUS_SUCCEEDED, STTResponse, stt_response_as_dict
from ..voice.tts_protocol import TTS_STATUS_SUCCEEDED, TTSResponse, tts_response_as_dict
from ..voice.voice_status_summary import build_voice_status_summary
from . import voice_workbench
from .voice_workbench_classifier import (
    CLASSIFICATION_ACTION_ORIENTED,
    classify_workbench_transcript,
)


def _continue_in_vera_url(session_id: str, base_url: str) -> str:
    """Build a continuation link into the canonical Vera web surface.

    The panel (default 8844) and the canonical Vera web app (default
    8790) run as two separate uvicorn processes, so a relative
    ``/vera`` link 404s on the panel host in the supported deployment
    model.  Build the link against the configured canonical Vera web
    base URL (``vera_web_base_url``) and target ``GET /``, which
    already accepts ``?session_id=<id>`` for cross-surface handoff.

    The session id is clamped to a single path component through
    ``Path(...).name`` so the link can only ever resolve to a session
    file under ``artifacts/vera_sessions/``.  Empty, invalid, or
    traversal-shaped values produce a bare ``{base}/`` link (no query
    string) so the canonical Vera surface falls back to its own
    cookie/new-session behaviour.

    An unusable base URL (blank or not ``http``/``https``) collapses
    to the canonical default so the link can never degrade to a
    broken ``/vera`` relative path on the panel host.
    """
    base = (base_url or "").strip().rstrip("/")
    if not (base.startswith("http://") or base.startswith("https://")):
        base = DEFAULT_VERA_WEB_BASE_URL
    clamped = Path(session_id or ".").name
    if not clamped or clamped == ".":
        return f"{base}/"
    return f"{base}/?session_id={clamped}"


def _safe_prior_turn_count(queue_root: Path, session_id: str) -> int:
    """Return the number of persisted turns in this Vera session.

    Returns ``0`` on any read error so the UI can render a truthful
    "new session" banner instead of crashing when a session file is
    malformed or missing.
    """
    try:
        return len(read_session_turns(queue_root, session_id))
    except Exception:
        return 0


def _persist_vera_session_cookie(response: HTMLResponse, session_id: str) -> None:
    """Persist the resolved Vera session id onto the response.

    The voice routes resolve the workbench session id from either the
    ``vera_session_id`` cookie or (as a fallback) a freshly-minted id.
    Without writing the cookie back, a minted id is lost on the next
    page load and the continuity banner jumps between session ids —
    the exact confusion the continuity polish is meant to remove.
    Match the ``samesite="lax"`` / ``httponly=False`` posture used by
    the panel ``/vera`` route and the canonical Vera web app so every
    surface shares the same cookie semantics.
    """
    if session_id:
        response.set_cookie("vera_session_id", session_id, httponly=False, samesite="lax")


def _display_status_for_stt(response: STTResponse, *, ok: bool) -> str:
    """Return the operator-facing status label for the STT result card.

    Protects against adapter pathology where ``status`` reads ``succeeded``
    but no transcript was produced — the card is failure-styled, so the
    badge must never read ``succeeded``.
    """
    if ok:
        return response.status
    if response.status == STT_STATUS_SUCCEEDED:
        return "no_transcript"
    return response.status or "failed"


def _display_status_for_tts(response: TTSResponse, *, ok: bool) -> str:
    """Return the operator-facing status label for the TTS result card.

    Protects against adapter pathology where ``status`` reads ``succeeded``
    but no ``audio_path`` was produced — the card is failure-styled, so the
    badge must never read ``succeeded``.
    """
    if ok:
        return response.status
    if response.status == TTS_STATUS_SUCCEEDED:
        return "no_audio_artifact"
    return response.status or "failed"


def register_voice_routes(
    app: FastAPI,
    *,
    templates: Any,
    require_operator_auth_from_request: Callable[[Request], None],
    require_mutation_guard: Callable[[Request], Awaitable[None]],
    csrf_cookie: str,
    request_value: Callable[..., Awaitable[str]],
    queue_root: Callable[[], Path],
    vera_web_base_url: Callable[[], str],
) -> None:
    def _continue_url(session_id: str) -> str:
        return _continue_in_vera_url(session_id, vera_web_base_url())

    def _resolve_session(request: Request) -> tuple[str, int]:
        """Resolve the operator's Vera session id and its prior turn count.

        Prefers the existing ``vera_session_id`` cookie so the Voice
        Workbench and canonical Vera chat share the same session out of
        the box; falls back to a freshly-minted id when no cookie is
        present.  Returns the session id plus the number of turns
        already persisted under that id (``0`` on any read error).
        """
        session_id = (request.cookies.get("vera_session_id") or "").strip() or new_session_id()
        return session_id, _safe_prior_turn_count(queue_root(), session_id)

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
        session_id, prior_turn_count = _resolve_session(request)
        tmpl = templates.get_template("voice.html")
        html = tmpl.render(
            summary=summary,
            error=error,
            csrf_token=csrf_token,
            tts_result=None,
            stt_result=None,
            workbench_result=None,
            workbench_session_id=session_id,
            workbench_session_prior_turn_count=prior_turn_count,
            workbench_continue_in_vera_url=_continue_url(session_id),
        )
        response = HTMLResponse(content=html)
        response.set_cookie(csrf_cookie, csrf_token, httponly=False, samesite="strict")
        _persist_vera_session_cookie(response, session_id)
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
        session_id, prior_turn_count = _resolve_session(request)
        tmpl = templates.get_template("voice.html")
        html = tmpl.render(
            summary=summary,
            error=page_error,
            csrf_token=csrf_token,
            tts_result=tts_result,
            stt_result=None,
            workbench_result=None,
            workbench_session_id=session_id,
            workbench_session_prior_turn_count=prior_turn_count,
            workbench_continue_in_vera_url=_continue_url(session_id),
        )
        resp = HTMLResponse(content=html)
        resp.set_cookie(csrf_cookie, csrf_token, httponly=False, samesite="strict")
        _persist_vera_session_cookie(resp, session_id)
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
        session_id, prior_turn_count = _resolve_session(request)
        tmpl = templates.get_template("voice.html")
        html = tmpl.render(
            summary=summary,
            error=page_error,
            csrf_token=csrf_token,
            tts_result=None,
            stt_result=stt_result,
            workbench_result=None,
            workbench_session_id=session_id,
            workbench_session_prior_turn_count=prior_turn_count,
            workbench_continue_in_vera_url=_continue_url(session_id),
        )
        resp = HTMLResponse(content=html)
        resp.set_cookie(csrf_cookie, csrf_token, httponly=False, samesite="strict")
        _persist_vera_session_cookie(resp, session_id)
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

        # Count turns that existed *before* this run began so the UI can
        # distinguish "new session" from "continuing an existing N-turn
        # session".  Wraps ``read_session_turns`` with a fail-safe so a
        # malformed session file never breaks the page.
        current_queue_root = queue_root()
        prior_turn_count = _safe_prior_turn_count(current_queue_root, session_id)
        run_started_at_ms = int(time.time() * 1000)

        workbench_result: dict[str, Any] = {
            "session_id": session_id,
            "input_audio_path": audio_path,
            "input_language": language,
            "send_to_vera_requested": send_to_vera,
            "speak_response_requested": speak_response,
            "stt": None,
            "vera": None,
            "tts": None,
            # Continuity framing — these fields let the template render
            # the session banner ("new session" vs "continuing N-turn
            # session") and anchor the result block to this specific run.
            "session_prior_turn_count": prior_turn_count,
            "session_turn_count": prior_turn_count,
            "run_started_at_ms": run_started_at_ms,
            "continue_in_vera_url": _continue_url(session_id),
        }

        # Flag-load failure is the single early gate: if the voice config
        # failed to load, no downstream step can run, and the operator sees
        # the truthful "voice configuration failed to load" error once.
        if flags is None:
            workbench_result["stt"] = {
                "success": False,
                "status": "unavailable",
                "display_status": "unavailable",
                "error": "Cannot transcribe: voice configuration failed to load.",
            }
            stt_ok = False
            transcript_text: str | None = None
        elif not audio_path:
            workbench_result["stt"] = {
                "success": False,
                "status": "failed",
                "display_status": "failed",
                "error": "Audio file path is required.",
            }
            stt_ok = False
            transcript_text = None
        else:
            # ── Step 1: STT ──────────────────────────────────────────
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
                    "display_status": _display_status_for_stt(stt_response, ok=stt_ok),
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
                    "display_status": "failed",
                    "error": f"Unexpected error: {type(exc).__name__}: {exc}",
                }
                stt_ok = False
                transcript_text = None

        # ── Step 2: Vera (only when a real transcript exists AND operator opted in) ──
        # ``flags is not None`` is guaranteed here: stt_ok cannot be True under
        # the flag-load-failed branch above.  We keep the runtime assertion
        # narrow and explicit so mypy and the reader see the same invariant.
        vera_ok = False
        vera_answer: str | None = None
        if stt_ok and transcript_text and send_to_vera:
            assert flags is not None  # noqa: S101 — invariant: stt_ok implies flags loaded
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
                "display_status": (
                    vera_result.status if not vera_result.ok else voice_workbench.STATUS_OK
                ),
                "answer": vera_result.vera_answer,
                "vera_status": vera_result.vera_status,
                "error": vera_result.error,
            }

        # ── Step 3: optional TTS on Vera's reply ────────────────────
        if vera_ok and vera_answer and speak_response:
            assert flags is not None  # noqa: S101 — invariant: vera_ok implies flags loaded
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
                    "display_status": _display_status_for_tts(tts_response, ok=tts_ok),
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
                    "display_status": "failed",
                    "error": str(exc),
                }
            except Exception as exc:
                workbench_result["tts"] = {
                    "success": False,
                    "status": "failed",
                    "display_status": "failed",
                    "error": f"Unexpected error: {type(exc).__name__}: {exc}",
                }

        # Refresh the total-turn count after the Vera step so the
        # continuity banner reflects the real state on disk (not just the
        # snapshot taken before this run).  Read errors fall back to the
        # pre-run count so we never over-claim.
        workbench_result["session_turn_count"] = _safe_prior_turn_count(
            current_queue_root, session_id
        )

        # ── Action-oriented classification (truth-preserving guidance) ──
        # Deterministic, bounded scan of the real transcript only.  Never
        # implies a preview exists or a job was created; only decides
        # whether the UI should render a stronger "continue in Vera"
        # guidance block.  Informational runs stay clean.
        #
        # The ``classification`` sub-dict is retained for debug / inspection
        # surfaces (it lets operators reason about *why* a run did or did
        # not flip action-oriented by reading ``reason`` and
        # ``matched_signals``); the template only consumes the top-level
        # ``show_action_guidance`` flag below.
        classification = classify_workbench_transcript(transcript_text)
        workbench_result["classification"] = {
            "kind": classification.kind,
            "is_action_oriented": classification.is_action_oriented,
            "reason": classification.reason,
            "matched_signals": list(classification.matched_signals),
        }
        workbench_result["show_action_guidance"] = bool(
            stt_ok and transcript_text and classification.kind == CLASSIFICATION_ACTION_ORIENTED
        )

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
            workbench_session_prior_turn_count=prior_turn_count,
            workbench_continue_in_vera_url=_continue_url(session_id),
        )
        resp = HTMLResponse(content=html)
        resp.set_cookie(csrf_cookie, csrf_token, httponly=False, samesite="strict")
        _persist_vera_session_cookie(resp, session_id)
        return resp
