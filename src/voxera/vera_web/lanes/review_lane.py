"""Review lane — extracted from ``voxera.vera_web.app``.

The review / evidence / job-review *decision* logic lives in
:mod:`voxera.vera_web.chat_early_exit_dispatch` (time, diagnostics,
review, follow-up from evidence, investigation, near-miss submit,
stale-draft reference).  This module holds the narrow pieces of
review-related glue that previously lived inline in ``chat()``:

1. **Active-preview revision-in-flight computation** — the canonical
   :func:`voxera.vera_web.preview_routing.is_active_preview_revision_turn`
   gate plus the review/evidence belt-and-suspenders that prevents
   ambiguous ``save that`` / ``revise from the result`` phrases from
   hijacking a normal active preview via the early-exit follow-up
   branches. This is explicitly review-lane protection.
2. **Early-exit state-write application** — the preview-installation,
   review context shortcut, and derived-output write choreography that
   runs when :func:`dispatch_early_exit_intent` returns ``matched=True``.
   Every preview mutation flows through the approved
   :mod:`voxera.vera.preview_ownership` helpers.

Ownership boundaries
--------------------
* ``app.py`` remains the top-level orchestrator. It still calls
  :func:`voxera.vera_web.chat_early_exit_dispatch.dispatch_early_exit_intent`
  and owns the final ``append_session_turn`` / routing-debug /
  ``_render_page`` flow.
* The review/evidence truth boundary is preserved: this module never
  fabricates evidence and never writes to preview state outside the
  approved ownership helpers.
* Ambiguous phrases still fail closed: when a normal active preview is
  present and the message is an ambiguous save/revise-from-evidence
  phrase, the revision-in-flight flag is set to True so later lanes
  step aside rather than silently replace the user's preview.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...vera.context_lifecycle import (
    context_on_review_performed,
)
from ...vera.evidence_review import (
    is_revise_from_evidence_request,
    is_save_followup_request,
)
from ...vera.investigation_derivations import (
    is_investigation_derived_save_request,
    is_investigation_save_request,
)
from ...vera.preview_ownership import (
    derive_preview_draft_ref,
    record_followup_preview,
    reset_active_preview,
)
from ...vera.session_store import (
    update_session_context,
    write_session_derived_investigation_output,
)
from ..chat_early_exit_dispatch import EarlyExitResult
from ..preview_routing import is_active_preview_revision_turn, is_normal_preview

__all__ = [
    "compute_active_preview_revision_in_flight",
    "apply_early_exit_state_writes",
]


def compute_active_preview_revision_in_flight(
    message: str,
    *,
    pending_preview: dict[str, Any] | None,
) -> bool:
    """Return True when a clear active-preview revision is in flight.

    Combines the canonical
    :func:`voxera.vera_web.preview_routing.is_active_preview_revision_turn`
    gate with a belt-and-suspenders check for review/evidence and
    investigation save phrases.  The belt-and-suspenders guard fires
    when a **normal** active preview is in play and the message is one
    of the ambiguous phrases:

    * :func:`is_save_followup_request`
    * :func:`is_revise_from_evidence_request`
    * :func:`is_investigation_save_request`
    * :func:`is_investigation_derived_save_request`

    These phrases can mean either "mutate the active preview" or
    "spawn a new evidence-grounded / investigation-derived follow-up".
    When a normal preview is active, the fail-closed choice is to
    treat them as revision candidates so the early-exit follow-up
    branches cannot silently replace the active preview.
    """
    if is_active_preview_revision_turn(message, active_preview=pending_preview):
        return True
    if not is_normal_preview(pending_preview):
        return False
    # Investigation-save detector is extremely broad: any phrase with
    # save/write/export + results/findings fires it. When a normal
    # active preview is in play, phrases like "make it save the results
    # to a file" or "save the scan results to a file" are script
    # enhancements, not investigation-export requests — the
    # investigation-save branch would otherwise fail closed with a
    # confusing "couldn't resolve investigation result references"
    # reply and steal the turn.  Fail-closed choice: treat them as
    # revision in flight so lane 4 / lane 8 / the derived-save lane all
    # step aside and the script enhancement lands on the active
    # preview instead.
    return bool(
        is_save_followup_request(message)
        or is_revise_from_evidence_request(message)
        or is_investigation_save_request(message)
        or is_investigation_derived_save_request(message)
    )


def apply_early_exit_state_writes(
    result: EarlyExitResult,
    *,
    queue_root: Path,
    session_id: str,
) -> None:
    """Apply the preview / context / derived-output writes for a matched
    early-exit result.

    The choreography is the same as the inlined block that lived in
    ``app.py``:

    * When ``write_preview`` is set, install the payload through the
      approved ownership helper.  Follow-up previews (identified by a
      ``last_reviewed_job_ref`` in ``context_updates``) go through
      :func:`record_followup_preview` so source-job continuity is
      preserved; everything else goes through
      :func:`reset_active_preview`.
    * When ``write_preview`` is not set but ``context_updates`` is
      present, prefer :func:`context_on_review_performed` for the
      single-key ``last_reviewed_job_ref`` shortcut; fall back to
      :func:`update_session_context` for multi-key updates.
    * When ``write_derived_output`` is set, persist the derived
      investigation output.

    This function is a no-op when ``result.matched`` is ``False``; the
    caller is expected to gate on ``matched`` before calling it but the
    guard is cheap insurance.
    """
    if not result.matched:
        return

    if result.write_preview and isinstance(result.preview_payload, dict):
        # Follow-up previews record a source job so "that job" / "the
        # last result" still resolves correctly on later turns.
        source_job = (
            str((result.context_updates or {}).get("last_reviewed_job_ref") or "").strip() or None
        )
        draft_ref = derive_preview_draft_ref(result.preview_payload)
        if source_job:
            record_followup_preview(
                queue_root,
                session_id,
                result.preview_payload,
                source_job_id=source_job,
                draft_ref=draft_ref,
            )
        else:
            reset_active_preview(
                queue_root,
                session_id,
                result.preview_payload,
                draft_ref=draft_ref,
            )
    elif result.context_updates:
        # Non-preview early-exit with context updates (e.g. job review).
        review_job = (
            str((result.context_updates or {}).get("last_reviewed_job_ref") or "").strip() or None
        )
        if review_job and len(result.context_updates) == 1:
            context_on_review_performed(queue_root, session_id, job_id=review_job)
        else:
            update_session_context(queue_root, session_id, **result.context_updates)

    if result.write_derived_output:
        write_session_derived_investigation_output(queue_root, session_id, result.derived_output)
