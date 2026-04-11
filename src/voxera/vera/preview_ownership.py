"""Centralized ownership of Vera active-preview state transitions.

This module narrows the set of call sites that can mutate Vera's active
preview state. Before its introduction, ``app.py`` performed
``write_session_preview`` + ``write_session_handoff_state`` +
``context_on_preview_created`` as three separate calls in many places,
with subtly different orderings and ``draft_ref`` derivations. That
scattered ownership was the proximate cause of several preview
regressions (stale refs, wrong draft refs, silent overwrites, cross-lane
collisions).

Architectural rules preserved
-----------------------------
* There is **one** authoritative active preview at a time — the value in
  ``pending_job_preview`` on the session payload.
* Preview truth > Queue truth > Artifact truth > Session context.
  This module only touches preview truth and the session-context
  continuity aid; it never claims authority over queue or artifact truth.
* Ambiguous revisions fail closed: callers that cannot confidently
  produce a well-formed payload must NOT call into this module — they
  must return early instead.
* Automation lifecycle flows and queue/evidence review flows must not
  call into ``reset_active_preview`` for unrelated previews. They have
  their own narrow entry points (automation preview submit, follow-up
  preview from evidence).

Who may mutate preview state (after this module)
-----------------------------------------------
1. The early-exit dispatch layer, via ``reset_active_preview`` /
   ``record_followup_preview`` when its result indicates the caller
   should write a preview.
2. Automation preview drafting / revision, which constructs the
   authoritative automation preview payload itself and then calls
   ``reset_active_preview``.
3. The code/automation shell recovery lanes (post-clarification,
   direct automation) that synthesize an empty shell — they must call
   ``reset_active_preview`` with the shell payload so follow-up lanes
   recognize it.
4. The deterministic builder path and the post-LLM draft content
   binding path, which together author the governed preview for normal
   chat turns and call ``reset_active_preview``.
5. The rename/save-as deterministic fallback.
6. The preview submission path, which calls ``clear_active_preview`` or
   ``record_submit_success`` once the queue acknowledges the handoff.
7. The guardrail cleanup path, which calls ``clear_active_preview``
   when a false preview claim was stripped and the underlying shell
   should not outlive the reply.

Anyone else touching ``pending_job_preview`` directly is a policy
violation that the reviewers should flag in code review.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .context_lifecycle import (
    context_on_followup_preview_prepared,
    context_on_preview_cleared,
    context_on_preview_created,
)
from .session_store import (
    write_session_handoff_state,
    write_session_preview,
)

__all__ = [
    "PreviewTransition",
    "clear_active_preview",
    "derive_preview_draft_ref",
    "record_followup_preview",
    "record_submit_success",
    "reset_active_preview",
]


# ---------------------------------------------------------------------------
# Transition descriptor (debug-only, not a required argument)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreviewTransition:
    """Describes a preview state transition for debug/audit purposes.

    Call sites may optionally construct one of these to document *why*
    a transition is happening. The helpers below accept a ``reason``
    string directly so most call sites do not need to construct one of
    these.
    """

    kind: str  # one of: create, revise, replace, followup, clear, submit
    reason: str
    draft_ref: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def derive_preview_draft_ref(payload: dict[str, Any] | None) -> str:
    """Return a stable draft reference for the payload, or ``"preview"``.

    Used by callers so that ``context_on_preview_created`` gets a
    meaningful ref (normally the write_file path) without each call
    site repeating the same dict walk.
    """
    if not isinstance(payload, dict):
        return "preview"
    write_file = payload.get("write_file")
    if isinstance(write_file, dict):
        path = str(write_file.get("path") or "").strip()
        if path:
            return path
    goal = str(payload.get("goal") or "").strip()
    if goal:
        return goal[:80]
    return "preview"


# ---------------------------------------------------------------------------
# Create / revise / replace (the common preview creation pattern)
# ---------------------------------------------------------------------------


def reset_active_preview(
    queue_root: Path,
    session_id: str,
    payload: dict[str, Any],
    *,
    draft_ref: str | None = None,
    mark_handoff_ready: bool = True,
) -> None:
    """Install *payload* as the session's active preview.

    This is the canonical path for creating, revising, or replacing the
    active preview. It performs three coupled writes that used to live
    scattered throughout ``app.py``:

    1. ``write_session_preview`` — installs the payload as the active
       preview.
    2. ``write_session_handoff_state`` — resets handoff status to
       ``preview_ready`` so stale ``submitted`` / ``submit_failed`` state
       from a previous preview cannot leak into a new preview's
       lifecycle. Callers may set ``mark_handoff_ready=False`` when the
       handoff state should be managed elsewhere (currently unused; the
       default is the right answer for every caller).
    3. ``context_on_preview_created`` — refreshes the shared session
       context so reference resolution (``"that draft"``, ``"the note"``,
       ``"save it as …"``) resolves to the new preview.

    ``draft_ref`` defaults to the derived reference from the payload.
    """

    write_session_preview(queue_root, session_id, payload)
    if mark_handoff_ready:
        write_session_handoff_state(
            queue_root,
            session_id,
            attempted=False,
            queue_path=str(queue_root),
            status="preview_ready",
            error=None,
            job_id=None,
        )
    effective_ref = (
        draft_ref
        if (isinstance(draft_ref, str) and draft_ref.strip())
        else (derive_preview_draft_ref(payload))
    )
    context_on_preview_created(queue_root, session_id, draft_ref=effective_ref)


# ---------------------------------------------------------------------------
# Follow-up preview from evidence
# ---------------------------------------------------------------------------


def record_followup_preview(
    queue_root: Path,
    session_id: str,
    payload: dict[str, Any],
    *,
    source_job_id: str | None,
    draft_ref: str | None = None,
) -> None:
    """Install *payload* as the active preview from a follow-up lane.

    Identical to :func:`reset_active_preview` except that it records
    ``source_job_id`` on the shared session context so later turns can
    resolve ``"that job"`` / ``"the last result"`` to the follow-up
    source. Used by the evidence-review / follow-up branches in the
    early-exit dispatch.
    """

    write_session_preview(queue_root, session_id, payload)
    write_session_handoff_state(
        queue_root,
        session_id,
        attempted=False,
        queue_path=str(queue_root),
        status="preview_ready",
        error=None,
        job_id=None,
    )
    effective_ref = (
        draft_ref
        if (isinstance(draft_ref, str) and draft_ref.strip())
        else (derive_preview_draft_ref(payload))
    )
    context_on_followup_preview_prepared(
        queue_root,
        session_id,
        draft_ref=effective_ref,
        source_job_id=source_job_id,
    )


# ---------------------------------------------------------------------------
# Clear / submit cleanup
# ---------------------------------------------------------------------------


def clear_active_preview(
    queue_root: Path,
    session_id: str,
    *,
    reason: str = "cleared",  # noqa: ARG001 — retained for call-site documentation
) -> None:
    """Remove the session's active preview and reset continuity refs.

    Used by the guardrail cleanup path (when a false preview claim was
    stripped and the underlying shell should not outlive the reply) and
    by any other deterministic cleanup branch that explicitly decides
    the active preview is no longer in play.

    ``reason`` is accepted so call sites self-document; the current
    implementation logs nothing, but it reserves a stable hook for
    future operator-facing telemetry without churning call sites.
    """

    write_session_preview(queue_root, session_id, None)
    context_on_preview_cleared(queue_root, session_id)


def record_submit_success(
    queue_root: Path,
    session_id: str,
) -> None:
    """Clear the active preview after a successful queue handoff.

    The submission pipeline already updates the handoff state with the
    real job id; this helper only clears the preview slot so subsequent
    turns cannot re-submit a stale payload. Context refs are updated
    separately via ``context_on_handoff_submitted`` since that lifecycle
    point needs the ``job_id``, which the submission helper owns.
    """

    write_session_preview(queue_root, session_id, None)
