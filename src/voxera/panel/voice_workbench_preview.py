"""Voice Workbench canonical preview drafting seam.

When the Voice Workbench classifier flags a run as action-oriented, the
operator is doing more than asking a question — they are asking for real
governed work.  Prior to this module the workbench stopped at showing a
truthful guidance block; the next product step is real voice-to-preview,
without weakening the trust model.

This module is the narrow bridge that lets an action-oriented transcript
produce a real canonical preview in the **same** Vera session the
workbench is already writing into, so ``Continue in Vera`` lands on a
session where the preview actually exists and canonical Vera can review,
refine, or submit it through the normal governed rails.

Trust model, re-stated
----------------------
* Voice Workbench **never** submits a queue job.  That boundary is
  non-negotiable and is enforced by the fact that this module only calls
  :func:`voxera.vera.preview_ownership.reset_active_preview` — never any
  submission helper.
* Preview drafting reuses the canonical Vera deterministic drafting path
  (:func:`voxera.vera.preview_drafting.maybe_draft_job_payload`) and the
  canonical normalization (:func:`voxera.vera.preview_submission.normalize_preview_payload`).
  There is no parallel preview mechanism.
* Fail-closed: if drafting returns ``None``, if normalization raises, or
  if writing the session preview state raises, this helper reports
  ``ok=False`` and makes no claim that a preview exists.  Callers must
  read canonical preview truth (``read_session_preview``) after calling
  this module — this helper never fabricates a ``preview_snapshot``.
* Informational runs never reach this module because the route gates on
  the classifier's ``is_action_oriented`` signal first.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..vera import session_store
from ..vera.preview_drafting import maybe_draft_job_payload
from ..vera.preview_ownership import derive_preview_draft_ref, reset_active_preview
from ..vera.preview_submission import normalize_preview_payload

# Canonical status values for the workbench preview-drafting step.  Kept
# narrow and operator-readable for both tests and debug surfaces.
PREVIEW_STATUS_DRAFTED = "drafted"
PREVIEW_STATUS_NO_DRAFT = "no_draft"
PREVIEW_STATUS_NORMALIZE_FAILED = "normalize_failed"
PREVIEW_STATUS_PERSIST_FAILED = "persist_failed"
PREVIEW_STATUS_ERROR = "error"


@dataclass(frozen=True)
class VoiceWorkbenchPreviewResult:
    """Typed result of a workbench preview-drafting attempt.

    ``ok=True`` means a normalized preview payload was persisted to the
    session's ``pending_job_preview`` slot via the canonical Vera
    preview-ownership helper, and that canonical preview truth now
    reflects it.  In every other case ``ok=False`` and ``status`` /
    ``error`` describe why no claim should be made.
    """

    ok: bool
    status: str
    draft_ref: str | None = None
    error: str | None = None


def maybe_draft_canonical_preview_for_workbench(
    *,
    transcript_text: str,
    session_id: str,
    queue_root: Path,
) -> VoiceWorkbenchPreviewResult:
    """Attempt to draft a real canonical preview from a transcript.

    This reuses the canonical Vera deterministic drafting path so the
    resulting preview lives in the same session Vera would have drafted
    into from chat.  The caller is expected to have already persisted
    the transcript as a ``voice_transcript``-origin user turn on the
    same session before calling this helper — that ordering matches the
    canonical preview lifecycle (turn first, preview refreshed against
    current session state).

    Fail-closed semantics:
    - ``maybe_draft_job_payload`` returns ``None`` -> ``no_draft``.
    - ``normalize_preview_payload`` raises -> ``normalize_failed``.
    - persisting via :func:`reset_active_preview` raises ->
      ``persist_failed``.
    - any other unexpected exception -> ``error``.
    In all failure cases, no preview is claimed and canonical session
    preview truth is left untouched by this helper.
    """
    normalized_transcript = (transcript_text or "").strip()
    if not normalized_transcript:
        return VoiceWorkbenchPreviewResult(ok=False, status=PREVIEW_STATUS_NO_DRAFT)

    # ── Gather canonical session context (read-only) ─────────────────
    # Split from the drafting call so a session-store read failure is
    # diagnosable on its own (``error`` with an ``exc`` pointing at a
    # read helper), instead of being conflated with a drafter-internal
    # exception.
    try:
        active_preview = session_store.read_session_preview(queue_root, session_id)
        session_context = session_store.read_session_context(queue_root, session_id)
        turns = session_store.read_session_turns(queue_root, session_id)
        recent_assistant_artifacts = session_store.read_session_saveable_assistant_artifacts(
            queue_root, session_id
        )
        investigation_context = session_store.read_session_investigation(queue_root, session_id)
    except Exception as exc:
        return VoiceWorkbenchPreviewResult(
            ok=False,
            status=PREVIEW_STATUS_ERROR,
            error=f"session_context_read_failed: {type(exc).__name__}: {exc}",
        )

    recent_user_messages = [
        str(turn.get("text") or "")
        for turn in turns
        if str(turn.get("role") or "").strip().lower() == "user"
    ]
    recent_assistant_messages = [
        str(turn.get("text") or "")
        for turn in turns
        if str(turn.get("role") or "").strip().lower() == "assistant"
    ]

    # ── Deterministic preview drafting (canonical path) ──────────────
    try:
        candidate = maybe_draft_job_payload(
            normalized_transcript,
            active_preview=active_preview,
            recent_user_messages=recent_user_messages,
            recent_assistant_messages=recent_assistant_messages,
            recent_assistant_artifacts=recent_assistant_artifacts or None,
            investigation_context=investigation_context,
            session_context=session_context,
        )
    except Exception as exc:
        return VoiceWorkbenchPreviewResult(
            ok=False,
            status=PREVIEW_STATUS_ERROR,
            error=f"drafter_raised: {type(exc).__name__}: {exc}",
        )

    if not isinstance(candidate, dict):
        return VoiceWorkbenchPreviewResult(ok=False, status=PREVIEW_STATUS_NO_DRAFT)

    try:
        normalized_payload = normalize_preview_payload(candidate)
    except Exception as exc:
        return VoiceWorkbenchPreviewResult(
            ok=False,
            status=PREVIEW_STATUS_NORMALIZE_FAILED,
            error=f"{type(exc).__name__}: {exc}",
        )

    # Let ``reset_active_preview`` derive its own draft_ref from the
    # normalized payload — its default (via ``derive_preview_draft_ref``)
    # is the same logic this seam would use.  We derive it once here
    # only to surface it in the returned ``VoiceWorkbenchPreviewResult``
    # for debug visibility; the two derivations share the same helper
    # so they cannot drift.
    try:
        reset_active_preview(queue_root, session_id, normalized_payload)
    except Exception as exc:
        return VoiceWorkbenchPreviewResult(
            ok=False,
            status=PREVIEW_STATUS_PERSIST_FAILED,
            error=f"{type(exc).__name__}: {exc}",
        )

    return VoiceWorkbenchPreviewResult(
        ok=True,
        status=PREVIEW_STATUS_DRAFTED,
        draft_ref=derive_preview_draft_ref(normalized_payload),
    )


def summarize_canonical_preview(
    preview: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return a bounded operator-facing snapshot of canonical preview state.

    Reads from the canonical ``pending_job_preview`` payload and returns
    a small dict the template can render without leaking internal
    structure.  Returns ``None`` when no canonical preview exists — the
    caller must treat that as "no preview to claim".

    Coverage is intentionally narrow.  The two structural shapes this
    surface explicitly extracts are ``write_file`` and ``file_organize``
    because those are the payload shapes produced by the current voice
    drafting lane for the Voice Workbench; every other canonical shape
    (investigation saves, authored-followup drafts, generic multi-step
    payloads, automation previews) falls back to ``goal`` + optional
    ``title`` / ``mission_id`` / ``step_count``.  That is deliberate:
    the workbench operator is not expected to review unfamiliar shapes
    on this surface — they are directed to ``Continue in Vera``, where
    the canonical preview UI renders the full payload.  If a future
    shape becomes common enough in voice runs, extend the summary here
    rather than widening the template branching.
    """
    if not isinstance(preview, dict):
        return None
    goal = str(preview.get("goal") or "").strip()
    if not goal:
        return None
    summary: dict[str, Any] = {"goal": goal}
    title = str(preview.get("title") or "").strip()
    if title:
        summary["title"] = title
    mission_id = str(preview.get("mission_id") or "").strip()
    if mission_id:
        summary["mission_id"] = mission_id
    write_file = preview.get("write_file")
    if isinstance(write_file, dict):
        path = str(write_file.get("path") or "").strip()
        mode = str(write_file.get("mode") or "").strip().lower() or "overwrite"
        if path:
            summary["write_file"] = {"path": path, "mode": mode}
    file_organize = preview.get("file_organize")
    if isinstance(file_organize, dict):
        source_path = str(file_organize.get("source_path") or "").strip()
        destination_dir = str(file_organize.get("destination_dir") or "").strip()
        mode = str(file_organize.get("mode") or "").strip().lower() or "copy"
        if source_path and destination_dir:
            summary["file_organize"] = {
                "source_path": source_path,
                "destination_dir": destination_dir,
                "mode": mode,
            }
    steps = preview.get("steps")
    if isinstance(steps, list) and steps:
        summary["step_count"] = len(steps)
    return summary
