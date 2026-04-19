"""Panel routes for the operator-facing voice status, TTS generation, and STT transcription surfaces.

Read-only diagnostic routes for STT/TTS configuration and availability,
plus minimal operator-facing generation/transcription forms that exercise
the canonical ``synthesize_text(...)`` and ``transcribe_audio_file(...)``
pipelines end to end.

The TTS generation surface is artifact-oriented: it produces a real audio
file on success and reports the output path and key response fields.
The STT transcription surface is file-oriented: it accepts an audio file
path, runs transcription, and renders the truthful result inline.

The Voice Workbench surface chains STT -> Vera -> optional TTS into a
single bounded operator flow. It is conversational only: it never
creates previews, submits jobs, or mutates real-world state. It accepts
two input sources:

* a typed audio file path (the original, unchanged lane), and
* a bounded browser microphone recording delivered as a raw binary POST
  to ``/voice/workbench/mic-upload``. The route writes the captured
  bytes to a short-lived temp file and feeds it into the exact same
  STT -> Vera -> optional TTS pipeline as the file-path lane. No
  parallel pipeline, no hidden mic use, no always-on listening.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse

from ..config import DEFAULT_VERA_WEB_BASE_URL
from ..vera import session_store
from ..vera.session_store import append_session_turn, new_session_id, read_session_turns
from ..voice.flags import load_voice_foundation_flags
from ..voice.input import (
    VoiceInputDisabledError,
    ingest_voice_transcript,
    transcribe_audio_file,
)
from ..voice.models import InputOrigin
from ..voice.output import synthesize_text, synthesize_text_async
from ..voice.stt_protocol import STT_STATUS_SUCCEEDED, STTResponse, stt_response_as_dict
from ..voice.tts_protocol import TTS_STATUS_SUCCEEDED, TTSResponse, tts_response_as_dict
from ..voice.voice_status_summary import build_voice_status_summary
from . import voice_workbench
from .voice_workbench_classifier import (
    CLASSIFICATION_ACTION_ORIENTED,
    classify_workbench_transcript,
)
from .voice_workbench_lifecycle import (
    LIFECYCLE_ACTION_NONE,
    classify_lifecycle_phrase,
    dispatch_spoken_lifecycle_command,
)
from .voice_workbench_preview import (
    maybe_draft_canonical_preview_for_workbench,
    summarize_canonical_preview,
)

# Source labels for Voice Workbench runs. Operator-facing surfaces read
# these to render truthful origin framing without inventing anything
# about how the audio was captured. The typed-path lane has always been
# "file_path"; the new browser-capture lane is "microphone".
WORKBENCH_SOURCE_FILE_PATH = "file_path"
WORKBENCH_SOURCE_MICROPHONE = "microphone"

# Bounded cap on the size of a single microphone upload. 25 MiB comfortably
# covers short operator utterances (a minute of uncompressed 16 kHz PCM is
# under 2 MiB; compressed WebM/Opus is far smaller) while still preventing
# arbitrary large uploads from parking bytes on disk or in memory.
_MIC_UPLOAD_MAX_BYTES = 25 * 1024 * 1024

# Prefix for mic-upload temp files.  Used so operator telemetry,
# housekeeping, and disk audits can recognize workbench mic captures at a
# glance.
_MIC_UPLOAD_PREFIX = "voxera_workbench_mic_"

# Narrow content-type -> suffix map.  We do NOT trust the client to pick
# the extension, but we surface a hint in the suffix so STT backend logs
# and operator diagnostics reflect what the browser sent. Anything
# unrecognized falls back to ``.webm`` — the default MediaRecorder
# container on Chromium/Firefox — rather than an extensionless temp
# file.
_MIC_UPLOAD_SUFFIX_BY_CONTENT_TYPE: dict[str, str] = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".mp4",
    "audio/aac": ".aac",
    "audio/flac": ".flac",
}


def _mic_upload_suffix_for(content_type: str | None) -> str:
    """Return the temp-file suffix for a mic-upload ``Content-Type`` header.

    The browser's ``MediaRecorder`` container hint is advisory only — it
    never changes pipeline behaviour — but it keeps diagnostic surfaces
    honest about what was actually captured.  Parameter segments (e.g.
    ``audio/webm;codecs=opus``) are stripped before lookup so the
    mapping is robust to real browser output.
    """
    if not content_type:
        return ".webm"
    base = content_type.split(";", 1)[0].strip().lower()
    return _MIC_UPLOAD_SUFFIX_BY_CONTENT_TYPE.get(base, ".webm")


def _is_audio_content_type(content_type: str | None) -> bool:
    """Return True if the request claims an ``audio/*`` body.

    The mic-upload route routes bytes to the canonical STT pipeline, so
    anything that is not audio is a misrouted request. Rejecting it with
    ``415`` up front keeps operator diagnostics honest — non-audio bodies
    would otherwise be written to a ``.webm`` temp file and only fail
    deep inside the STT backend with a misleading error.
    """
    if not content_type:
        return False
    base = content_type.split(";", 1)[0].strip().lower()
    return base.startswith("audio/")


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

    async def _execute_workbench_pipeline(
        *,
        audio_path: str,
        language: str | None,
        session_id: str,
        send_to_vera: bool,
        speak_response: bool,
        input_source: str,
    ) -> tuple[dict[str, Any], Any, str | None, int]:
        """Run the full Voice Workbench STT -> Vera -> optional TTS pipeline.

        Shared by the file-path-input route (``/voice/workbench/run``)
        and the browser-microphone-input route
        (``/voice/workbench/mic-upload``).  Both input sources funnel
        into this one helper so the canonical STT path, lifecycle
        classification, preview drafting, and trust framing stay
        identical — there is no parallel voice pipeline.

        Returns ``(workbench_result, summary, page_error, prior_turn_count)``
        so the caller can render the same ``voice.html`` template with
        the same context shape regardless of input source.  ``input_source``
        is carried through into the result ("file_path" or "microphone")
        so the UI can render truthful origin labels without fabricating
        anything about the capture.
        """
        flags = None
        try:
            flags = load_voice_foundation_flags()
            summary = build_voice_status_summary(flags)
            page_error: str | None = None
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
            "input_source": input_source,
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

        # ── Step 2a: Spoken lifecycle command (bounded, canonical-state-only) ──
        # Before routing into Vera, inspect the transcript for a short
        # bounded lifecycle phrase ("submit it", "approve it", "deny it",
        # etc.).  When one matches AND the operator opted in to Vera
        # processing, dispatch the matching canonical lifecycle helper
        # instead of a normal Vera conversational turn.  The voice
        # transcript is still persisted as a ``voice_transcript``-origin
        # user turn so the session's turn log truthfully records what
        # the operator said; the assistant turn records the dispatcher's
        # ack (submit ok / missing preview / ambiguous approval / …).
        # Nothing is fabricated: the submit path goes through the
        # canonical ``submit_active_preview_for_session`` seam, the
        # approve/deny path goes through the canonical queue-daemon
        # ``resolve_approval`` seam, and every fail-closed branch
        # surfaces a truthful negative status.
        lifecycle_handled = False
        lifecycle_speak_text: str | None = None
        if stt_ok and transcript_text and send_to_vera:
            assert flags is not None  # noqa: S101 — invariant: stt_ok implies flags loaded
            classification = classify_lifecycle_phrase(transcript_text)
            if classification.kind != LIFECYCLE_ACTION_NONE:
                lifecycle_handled = True
                try:
                    ingested = ingest_voice_transcript(
                        transcript_text=transcript_text,
                        voice_input_enabled=flags.voice_input_enabled,
                    )
                except VoiceInputDisabledError as exc:
                    workbench_result["lifecycle"] = {
                        "action": classification.kind,
                        "matched_phrase": classification.matched_phrase,
                        "reason": classification.reason,
                        "ok": False,
                        "status": "voice_input_disabled",
                        "ack": None,
                        "job_id": None,
                        "approval_ref": None,
                        "error": str(exc),
                    }
                except ValueError as exc:
                    workbench_result["lifecycle"] = {
                        "action": classification.kind,
                        "matched_phrase": classification.matched_phrase,
                        "reason": classification.reason,
                        "ok": False,
                        "status": "voice_input_invalid",
                        "ack": None,
                        "job_id": None,
                        "approval_ref": None,
                        "error": str(exc),
                    }
                else:
                    append_session_turn(
                        current_queue_root,
                        session_id,
                        role="user",
                        text=ingested.transcript_text,
                        input_origin=InputOrigin.VOICE_TRANSCRIPT.value,
                    )
                    dispatch_result = dispatch_spoken_lifecycle_command(
                        classification=classification,
                        session_id=session_id,
                        queue_root=current_queue_root,
                    )
                    if dispatch_result.ack:
                        append_session_turn(
                            current_queue_root,
                            session_id,
                            role="assistant",
                            text=dispatch_result.ack,
                        )
                        lifecycle_speak_text = dispatch_result.ack
                    workbench_result["lifecycle"] = {
                        "action": dispatch_result.action,
                        "matched_phrase": classification.matched_phrase,
                        "reason": classification.reason,
                        "ok": dispatch_result.ok,
                        "status": dispatch_result.status,
                        "ack": dispatch_result.ack,
                        "job_id": dispatch_result.job_id,
                        "approval_ref": dispatch_result.approval_ref,
                        "error": dispatch_result.error,
                    }

        # ── Step 2b: Vera (only when a real transcript exists AND operator opted in) ──
        # ``flags is not None`` is guaranteed here: stt_ok cannot be True under
        # the flag-load-failed branch above.  We keep the runtime assertion
        # narrow and explicit so mypy and the reader see the same invariant.
        # Lifecycle phrases short-circuit the Vera call: the dispatcher has
        # already persisted the user/assistant turns for this run.
        vera_ok = False
        vera_answer: str | None = None
        if stt_ok and transcript_text and send_to_vera and not lifecycle_handled:
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

        # ── Step 3: optional TTS on Vera's reply (or on the lifecycle ack) ──
        # When a lifecycle phrase was handled, the dispatcher produced a
        # short operator-facing ack (the same string persisted as the
        # assistant turn); prefer it so the operator hears the truthful
        # result of the lifecycle action rather than a stale / absent
        # Vera answer.
        tts_source_text: str | None = None
        if lifecycle_handled and lifecycle_speak_text and speak_response:
            tts_source_text = lifecycle_speak_text
        elif vera_ok and vera_answer and speak_response:
            tts_source_text = vera_answer
        if tts_source_text:
            # Invariant: any TTS source text (a lifecycle ack or a Vera
            # answer) can only exist when ``stt_ok and transcript_text
            # and send_to_vera`` was true earlier in the handler, which
            # itself requires ``flags`` to have loaded successfully.
            assert flags is not None  # noqa: S101
            try:
                start_ms = int(time.time() * 1000)
                tts_response = await synthesize_text_async(
                    text=tts_source_text,
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
        action_classification = classify_workbench_transcript(transcript_text)
        workbench_result["classification"] = {
            "kind": action_classification.kind,
            "is_action_oriented": action_classification.is_action_oriented,
            "reason": action_classification.reason,
            "matched_signals": list(action_classification.matched_signals),
        }

        # ── Optional canonical preview drafting (action-oriented only) ──
        # When the classifier flags the run as action-oriented AND the
        # operator opted in to Vera processing, attempt to draft a real
        # canonical preview via the narrow seam.  The seam reuses the
        # canonical Vera deterministic drafting + normalization +
        # preview-ownership writes, so the preview lands in the same
        # session the workbench is already writing into.  Never submits
        # to the queue — that boundary is enforced by the seam itself.
        #
        # Attribution rule: the "Governed preview drafted" block claims
        # that *this run* drafted a preview.  We therefore only populate
        # ``workbench_result["preview"]`` when the seam itself reports
        # success (``preview_result.ok``).  If an unrelated preview was
        # already sitting on the session from a prior canonical Vera
        # turn, the session still has it — the operator sees it when
        # they follow the "Continue in Vera" link — but this voice
        # surface does not claim agency it does not have.
        preview_attempted = bool(
            stt_ok
            and transcript_text
            and send_to_vera
            and not lifecycle_handled
            and action_classification.kind == CLASSIFICATION_ACTION_ORIENTED
        )
        if preview_attempted:
            assert transcript_text is not None  # noqa: S101 — stt_ok + transcript guard
            preview_result = maybe_draft_canonical_preview_for_workbench(
                transcript_text=transcript_text,
                session_id=session_id,
                queue_root=current_queue_root,
            )
            workbench_result["preview_attempt"] = {
                "ok": preview_result.ok,
                "status": preview_result.status,
                "draft_ref": preview_result.draft_ref,
                "error": preview_result.error,
            }
            if preview_result.ok:
                try:
                    canonical_preview = session_store.read_session_preview(
                        current_queue_root, session_id
                    )
                except Exception:
                    canonical_preview = None
                preview_summary = summarize_canonical_preview(canonical_preview)
                if preview_summary is not None:
                    workbench_result["preview"] = preview_summary

        # Only render the generic action-oriented guidance block when we
        # did NOT successfully draft a real canonical preview AND the
        # run was not handled as a bounded lifecycle command.  When a
        # preview exists, the operator sees a stronger, more specific
        # "Governed preview drafted" block; when a lifecycle command
        # dispatched, the "Spoken lifecycle command" block is the
        # truthful surface for that run.
        workbench_result["show_action_guidance"] = bool(
            stt_ok
            and transcript_text
            and not lifecycle_handled
            and action_classification.kind == CLASSIFICATION_ACTION_ORIENTED
            and not workbench_result.get("preview")
        )

        return workbench_result, summary, page_error, prior_turn_count

    def _render_workbench_response(
        request: Request,
        *,
        workbench_result: dict[str, Any],
        summary: Any,
        page_error: str | None,
        session_id: str,
        prior_turn_count: int,
    ) -> HTMLResponse:
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

        workbench_result, summary, page_error, prior_turn_count = await _execute_workbench_pipeline(
            audio_path=audio_path,
            language=language,
            session_id=session_id,
            send_to_vera=send_to_vera,
            speak_response=speak_response,
            input_source=WORKBENCH_SOURCE_FILE_PATH,
        )
        return _render_workbench_response(
            request,
            workbench_result=workbench_result,
            summary=summary,
            page_error=page_error,
            session_id=session_id,
            prior_turn_count=prior_turn_count,
        )

    @app.post("/voice/workbench/mic-upload", response_class=HTMLResponse)
    async def voice_workbench_mic_upload(request: Request) -> HTMLResponse:
        """Accept a bounded browser-microphone recording and run the workbench.

        Operator-initiated browser capture only. The browser records a
        short utterance via the standard ``MediaRecorder`` API and POSTs
        the resulting audio blob as the raw request body. Additional
        pipeline options (``workbench_session_id``, ``workbench_language``,
        ``workbench_send_to_vera``, ``workbench_speak_response``) ride on
        the query string so the request stays a single binary body.

        The route writes the uploaded bytes to a temp file, feeds that
        file into the canonical Voice Workbench pipeline, and then
        removes the temp file in a ``finally`` block. There is no
        parallel voice pipeline: the same STT -> Vera -> optional TTS
        helper runs for mic-origin audio as for file-path-origin audio,
        and ``voice_transcript`` is still the canonical turn origin.

        Fail-closed: an empty or oversized body returns a truthful
        ``400`` with a short operator-facing error and never creates a
        temp file. CSRF is still enforced via ``x-csrf-token`` on the
        request headers, the same way the form lane enforces it.
        """
        await require_mutation_guard(request)

        content_type_header = request.headers.get("content-type")
        if not _is_audio_content_type(content_type_header):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Microphone upload requires an audio/* Content-Type.",
            )

        raw_body = await request.body()
        if not raw_body:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Empty microphone upload body.",
            )
        if len(raw_body) > _MIC_UPLOAD_MAX_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=(f"Microphone upload exceeds {_MIC_UPLOAD_MAX_BYTES} byte cap."),
            )

        language = (await request_value(request, "workbench_language", "")).strip() or None
        session_id_raw = (await request_value(request, "workbench_session_id", "")).strip()
        session_id = session_id_raw or new_session_id()
        send_to_vera_raw = (await request_value(request, "workbench_send_to_vera", "")).strip()
        speak_response_raw = (await request_value(request, "workbench_speak_response", "")).strip()
        send_to_vera = send_to_vera_raw.lower() in {"1", "true", "on", "yes"}
        speak_response = speak_response_raw.lower() in {"1", "true", "on", "yes"}

        suffix = _mic_upload_suffix_for(content_type_header)
        tmp_fd, tmp_path = tempfile.mkstemp(prefix=_MIC_UPLOAD_PREFIX, suffix=suffix)
        try:
            try:
                with os.fdopen(tmp_fd, "wb") as handle:
                    handle.write(raw_body)
            except Exception:
                with contextlib.suppress(OSError):
                    os.close(tmp_fd)
                raise
            (
                workbench_result,
                summary,
                page_error,
                prior_turn_count,
            ) = await _execute_workbench_pipeline(
                audio_path=tmp_path,
                language=language,
                session_id=session_id,
                send_to_vera=send_to_vera,
                speak_response=speak_response,
                input_source=WORKBENCH_SOURCE_MICROPHONE,
            )
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)

        return _render_workbench_response(
            request,
            workbench_result=workbench_result,
            summary=summary,
            page_error=page_error,
            session_id=session_id,
            prior_turn_count=prior_turn_count,
        )
