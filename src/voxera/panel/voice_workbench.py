"""Voice Workbench orchestrator (panel-side).

Bounded transcript -> Vera conversational turn. The Voice Workbench
is the first real voice-to-Vera workflow on the panel. It does one
thing: given a real transcript, it appends a ``voice_transcript``-origin
user turn to the canonical Vera session, asks Vera for a reply, and
appends the reply as an assistant turn.

This module is deliberately narrow:

- It is conversational only. It does **not** create queue previews,
  does **not** submit queue jobs, and does **not** trigger any queued
  or real-world execution. If the transcript describes an action that
  would affect the real world, Vera's reply is still just text; the
  operator must use the canonical ``/vera`` chat path so the action
  flows through preview/handoff/governed rails.
- It DOES persist to the canonical Vera session JSON file (same path
  and turn shape that ``vera_web`` writes) — that is what
  "conversational turn" means. A session started here is trivially
  continued in the canonical Vera surface. No other state is written.
- It fails closed and truthfully: disabled voice input, empty
  transcript, Vera errors, and empty Vera answers all produce a
  typed :class:`VoiceWorkbenchVeraResult` with ``ok=False`` and a
  concrete ``status``/``error`` that the UI surfaces as-is.  Stale
  results are impossible because every run is a single request whose
  result is owned by the caller; nothing is cached on the orchestrator.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..vera.service import generate_vera_reply
from ..vera.session_store import append_session_turn, read_session_turns
from ..voice.flags import VoiceFoundationFlags
from ..voice.input import VoiceInputDisabledError, ingest_voice_transcript
from ..voice.models import InputOrigin

# Canonical status values for the workbench -> Vera step.  Kept narrow
# and operator-readable.
STATUS_OK = "ok"
STATUS_VOICE_INPUT_DISABLED = "voice_input_disabled"
STATUS_VOICE_INPUT_INVALID = "voice_input_invalid"
STATUS_VERA_ERROR = "vera_error"
STATUS_VERA_EMPTY_ANSWER = "vera_empty_answer"

GenerateReplyFn = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class VoiceWorkbenchVeraResult:
    """Typed result of a transcript -> Vera turn.

    ``ok=True`` means a real assistant reply was persisted. In every
    other case ``ok=False`` and ``status``/``error`` describe why.

    ``preview_snapshot`` and ``handoff_snapshot`` are reserved for a
    truthful surface of governed preview/handoff state if a future
    extension routes voice turns through the full canonical pipeline.
    Today the workbench lane is conversational only, so both fields are
    always ``None``.
    """

    ok: bool
    status: str
    transcript_text: str | None
    vera_answer: str | None
    vera_status: str | None
    error: str | None = None
    preview_snapshot: dict[str, Any] | None = None
    handoff_snapshot: dict[str, Any] | None = None


async def run_transcript_to_vera_turn(
    *,
    transcript_text: str,
    session_id: str,
    queue_root: Path,
    flags: VoiceFoundationFlags,
    generate_reply: GenerateReplyFn | None = None,
) -> VoiceWorkbenchVeraResult:
    """Send a transcript into Vera as a voice-origin conversational turn.

    Fail-closed semantics:
    - Voice input disabled -> ``voice_input_disabled``, nothing persisted.
    - Empty/whitespace-only transcript -> ``voice_input_invalid``,
      nothing persisted.
    - Vera raises -> ``vera_error``, the user turn is persisted
      (we honestly record what was sent) but no assistant turn.
    - Vera returns an empty/whitespace answer -> ``vera_empty_answer``,
      the user turn is persisted but no assistant turn.
    - Success -> ``ok``, both user and assistant turns persisted.
    """
    reply_fn: GenerateReplyFn = generate_reply or generate_vera_reply

    try:
        ingested = ingest_voice_transcript(
            transcript_text=transcript_text,
            voice_input_enabled=flags.voice_input_enabled,
        )
    except VoiceInputDisabledError as exc:
        return VoiceWorkbenchVeraResult(
            ok=False,
            status=STATUS_VOICE_INPUT_DISABLED,
            transcript_text=None,
            vera_answer=None,
            vera_status=None,
            error=str(exc),
        )
    except ValueError as exc:
        return VoiceWorkbenchVeraResult(
            ok=False,
            status=STATUS_VOICE_INPUT_INVALID,
            transcript_text=None,
            vera_answer=None,
            vera_status=None,
            error=str(exc),
        )

    normalized_transcript = ingested.transcript_text

    append_session_turn(
        queue_root,
        session_id,
        role="user",
        text=normalized_transcript,
        input_origin=InputOrigin.VOICE_TRANSCRIPT.value,
    )

    try:
        reply = await reply_fn(
            turns=read_session_turns(queue_root, session_id),
            user_message=normalized_transcript,
        )
    except Exception as exc:
        return VoiceWorkbenchVeraResult(
            ok=False,
            status=STATUS_VERA_ERROR,
            transcript_text=normalized_transcript,
            vera_answer=None,
            vera_status=None,
            error=f"{type(exc).__name__}: {exc}",
        )

    answer_raw = reply.get("answer") if isinstance(reply, dict) else None
    answer = str(answer_raw or "").strip()
    vera_status = (
        str(reply.get("status") or "").strip() or None if isinstance(reply, dict) else None
    )

    if not answer:
        return VoiceWorkbenchVeraResult(
            ok=False,
            status=STATUS_VERA_EMPTY_ANSWER,
            transcript_text=normalized_transcript,
            vera_answer=None,
            vera_status=vera_status,
            error="Vera returned no answer.",
        )

    append_session_turn(queue_root, session_id, role="assistant", text=answer)

    return VoiceWorkbenchVeraResult(
        ok=True,
        status=STATUS_OK,
        transcript_text=normalized_transcript,
        vera_answer=answer,
        vera_status=vera_status,
    )
