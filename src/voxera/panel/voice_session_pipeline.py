"""Shared voice-session pipeline seam.

A single bounded STT -> (lifecycle | Vera + optional preview drafting)
-> optional TTS helper consumed by every voice-origin surface:

* the panel Voice Workbench (file-path and browser-mic lanes), and
* the canonical Vera web app's dictation button.

There is no parallel voice pipeline.  Both surfaces call into this
module so the canonical STT path, lifecycle dispatch, canonical
preview drafting, and trust framing stay identical regardless of
where the audio was captured.

Trust model, re-stated
----------------------
* Turns are persisted as ``voice_transcript``-origin turns on the
  canonical Vera session store — the same shape canonical Vera writes
  from typed chat, so a session started here is trivially continued in
  the canonical Vera surface.
* Preview drafting reuses the canonical Vera deterministic drafting
  path via :func:`voxera.panel.voice_workbench_preview.maybe_draft_canonical_preview_for_workbench`.
* Lifecycle phrases dispatch through the bounded canonical-state-only
  seam in :mod:`voxera.panel.voice_workbench_lifecycle` (same as the
  workbench).
* Fail-closed at every step: voice input disabled, empty transcript,
  STT error, Vera error/empty, TTS error, normalization error, and
  persist error each surface as a typed negative result.  The pipeline
  never fabricates a preview, never submits a queue job on its own
  (lifecycle submit goes through the canonical submit seam), and never
  claims a TTS artifact exists when none was produced.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..vera import session_store
from ..vera.session_store import append_session_turn
from ..voice.flags import VoiceFoundationFlags
from ..voice.input import (
    VoiceInputDisabledError,
    ingest_voice_transcript,
    transcribe_audio_file_async,
)
from ..voice.models import InputOrigin
from ..voice.output import synthesize_text_async
from ..voice.stt_protocol import STT_STATUS_SUCCEEDED, stt_response_as_dict
from ..voice.tts_protocol import TTS_STATUS_SUCCEEDED, tts_response_as_dict
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


@dataclass(frozen=True)
class VoiceSessionPipelineResult:
    """Typed end-to-end result of one voice-session turn.

    Every field reflects a real step the pipeline ran.  When a step did
    not run (e.g. ``send_to_vera`` was false), the corresponding field
    is ``None``.  Callers adapt this shape to their own UI without
    teaching the pipeline about UI keys.
    """

    stt: dict[str, Any]
    transcript_text: str | None
    stt_ok: bool
    classification: dict[str, Any]
    lifecycle: dict[str, Any] | None = None
    lifecycle_handled: bool = False
    lifecycle_speak_text: str | None = None
    vera: dict[str, Any] | None = None
    vera_ok: bool = False
    vera_answer: str | None = None
    preview_attempt: dict[str, Any] | None = None
    preview: dict[str, Any] | None = None
    tts: dict[str, Any] | None = None
    show_action_guidance: bool = False
    turns_appended: list[dict[str, Any]] = field(default_factory=list)


def _display_status_for_stt(status: str, *, ok: bool) -> str:
    if ok:
        return status
    if status == STT_STATUS_SUCCEEDED:
        return "no_transcript"
    return status or "failed"


def _display_status_for_tts(status: str, *, ok: bool) -> str:
    if ok:
        return status
    if status == TTS_STATUS_SUCCEEDED:
        return "no_audio_artifact"
    return status or "failed"


async def run_voice_session_turn(
    *,
    audio_path: str,
    language: str | None,
    session_id: str,
    flags: VoiceFoundationFlags,
    queue_root: Path,
    send_to_vera: bool,
    speak_response: bool,
) -> VoiceSessionPipelineResult:
    """Run a single voice-session turn through the canonical pipeline.

    ``audio_path`` must point at a readable audio file — both the
    workbench's file-path lane and the mic-upload / Vera dictation
    lanes produce such a path (the mic lane writes a short-lived temp
    file; the caller is responsible for its lifetime).

    Fail-closed semantics:
    - Empty ``audio_path`` -> STT dict with ``failed`` status.
    - STT backend raises -> STT dict with ``failed`` status and the
      exception rendered into ``error``; Vera/TTS skipped.
    - Voice input disabled but a lifecycle phrase was spoken -> lifecycle
      dict with ``voice_input_disabled`` / ``voice_input_invalid`` and
      the rest of the pipeline skipped (no Vera call, no preview,
      TTS only on the lifecycle ack if present — which it isn't in the
      disabled branch).
    - Vera raises -> Vera dict reflects the error; preview drafting and
      TTS skipped.
    - TTS raises -> TTS dict reflects the error; text stays authoritative.
    """
    # ── Step 1: STT ─────────────────────────────────────────────────
    stt_ok = False
    transcript_text: str | None = None
    stt_dict: dict[str, Any]
    if not audio_path:
        stt_dict = {
            "success": False,
            "status": "failed",
            "display_status": "failed",
            "error": "Audio file path is required.",
        }
    else:
        try:
            start_ms = int(time.time() * 1000)
            stt_response = await transcribe_audio_file_async(
                audio_path=audio_path,
                flags=flags,
                language=language,
                session_id=session_id,
            )
            elapsed_ms = int(time.time() * 1000) - start_ms
            stt_ok = bool(stt_response.status == STT_STATUS_SUCCEEDED and stt_response.transcript)
            transcript_text = stt_response.transcript if stt_ok else None
            stt_dict = {
                "success": stt_ok,
                "status": stt_response.status,
                "display_status": _display_status_for_stt(stt_response.status, ok=stt_ok),
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
            stt_dict = {
                "success": False,
                "status": "failed",
                "display_status": "failed",
                "error": f"Unexpected error: {type(exc).__name__}: {exc}",
            }

    # ── Step 2: bounded spoken-lifecycle dispatch ───────────────────
    lifecycle_handled = False
    lifecycle_speak_text: str | None = None
    lifecycle_dict: dict[str, Any] | None = None
    if stt_ok and transcript_text and send_to_vera:
        classification = classify_lifecycle_phrase(transcript_text)
        if classification.kind != LIFECYCLE_ACTION_NONE:
            lifecycle_handled = True
            try:
                ingested = ingest_voice_transcript(
                    transcript_text=transcript_text,
                    voice_input_enabled=flags.voice_input_enabled,
                )
            except VoiceInputDisabledError as exc:
                lifecycle_dict = {
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
                lifecycle_dict = {
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
                    queue_root,
                    session_id,
                    role="user",
                    text=ingested.transcript_text,
                    input_origin=InputOrigin.VOICE_TRANSCRIPT.value,
                )
                dispatch_result = dispatch_spoken_lifecycle_command(
                    classification=classification,
                    session_id=session_id,
                    queue_root=queue_root,
                )
                if dispatch_result.ack:
                    append_session_turn(
                        queue_root,
                        session_id,
                        role="assistant",
                        text=dispatch_result.ack,
                    )
                    lifecycle_speak_text = dispatch_result.ack
                lifecycle_dict = {
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

    # ── Step 3: Vera conversational turn (non-lifecycle, opt-in only) ──
    vera_ok = False
    vera_answer: str | None = None
    vera_dict: dict[str, Any] | None = None
    if stt_ok and transcript_text and send_to_vera and not lifecycle_handled:
        vera_result = await voice_workbench.run_transcript_to_vera_turn(
            transcript_text=transcript_text,
            session_id=session_id,
            queue_root=queue_root,
            flags=flags,
        )
        vera_ok = vera_result.ok
        vera_answer = vera_result.vera_answer
        vera_dict = {
            "success": vera_result.ok,
            "status": vera_result.status,
            "display_status": (
                vera_result.status if not vera_result.ok else voice_workbench.STATUS_OK
            ),
            "answer": vera_result.vera_answer,
            "vera_status": vera_result.vera_status,
            "error": vera_result.error,
        }

    # ── Step 4: action-oriented canonical preview drafting ─────────
    action_classification = classify_workbench_transcript(transcript_text)
    classification_dict = {
        "kind": action_classification.kind,
        "is_action_oriented": action_classification.is_action_oriented,
        "reason": action_classification.reason,
        "matched_signals": list(action_classification.matched_signals),
    }
    preview_attempt_dict: dict[str, Any] | None = None
    preview_dict: dict[str, Any] | None = None
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
            queue_root=queue_root,
        )
        preview_attempt_dict = {
            "ok": preview_result.ok,
            "status": preview_result.status,
            "draft_ref": preview_result.draft_ref,
            "error": preview_result.error,
        }
        if preview_result.ok:
            try:
                canonical_preview = session_store.read_session_preview(queue_root, session_id)
            except Exception:
                canonical_preview = None
            preview_dict = summarize_canonical_preview(canonical_preview)

    show_action_guidance = bool(
        stt_ok
        and transcript_text
        and not lifecycle_handled
        and action_classification.kind == CLASSIFICATION_ACTION_ORIENTED
        and not preview_dict
    )

    # ── Step 5: optional TTS on the lifecycle ack or Vera answer ───
    tts_dict: dict[str, Any] | None = None
    tts_source_text: str | None = None
    if lifecycle_handled and lifecycle_speak_text and speak_response:
        tts_source_text = lifecycle_speak_text
    elif vera_ok and vera_answer and speak_response:
        tts_source_text = vera_answer
    if tts_source_text:
        try:
            start_ms = int(time.time() * 1000)
            tts_response = await synthesize_text_async(
                text=tts_source_text,
                flags=flags,
                session_id=session_id,
            )
            elapsed_ms = int(time.time() * 1000) - start_ms
            tts_ok = bool(tts_response.status == TTS_STATUS_SUCCEEDED and tts_response.audio_path)
            tts_dict = {
                "success": tts_ok,
                "status": tts_response.status,
                "display_status": _display_status_for_tts(tts_response.status, ok=tts_ok),
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
            tts_dict = {
                "success": False,
                "status": "failed",
                "display_status": "failed",
                "error": str(exc),
            }
        except Exception as exc:
            tts_dict = {
                "success": False,
                "status": "failed",
                "display_status": "failed",
                "error": f"Unexpected error: {type(exc).__name__}: {exc}",
            }

    return VoiceSessionPipelineResult(
        stt=stt_dict,
        transcript_text=transcript_text,
        stt_ok=stt_ok,
        classification=classification_dict,
        lifecycle=lifecycle_dict,
        lifecycle_handled=lifecycle_handled,
        lifecycle_speak_text=lifecycle_speak_text,
        vera=vera_dict,
        vera_ok=vera_ok,
        vera_answer=vera_answer,
        preview_attempt=preview_attempt_dict,
        preview=preview_dict,
        tts=tts_dict,
        show_action_guidance=show_action_guidance,
    )
