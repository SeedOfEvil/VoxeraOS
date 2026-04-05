"""Early-exit intent handler dispatch for the Vera web chat flow.

This module owns the coherent cluster of special-intent / short-circuit
conditions that are evaluated at the top of ``chat()`` before the normal
LLM orchestration path is entered.  Each condition detects a specific intent,
computes a structured early response, and signals an early return.

Extraction rationale
--------------------
These branches were previously inline in the giant ``chat()`` function in
``app.py``.  They are self-contained and independent of each other: each
one either fires (returning an ``EarlyExitResult`` with ``matched=True``) or
falls through (the caller proceeds to the next stage).

Ownership boundaries
--------------------
This module is responsible for:
  - Evaluating every early-exit intent condition in their canonical order.
  - Deriving assistant text, status codes, and preview/state payloads.
  - Any read-only I/O (filesystem artifact reads) required to compute
    the result.

This module is NOT responsible for:
  - ``append_session_turn`` — final write stays in ``app.py``.
  - ``write_session_preview`` / ``write_session_handoff_state`` — write
    instructions are returned in the result; ``app.py`` performs them.
  - ``write_session_derived_investigation_output`` — same: write flag only.
  - ``update_session_context`` — context-update dict only; ``app.py`` writes.
  - ``_render_page`` — routing truth stays in ``app.py``.
  - Submit / handoff decisions (``_submit_handoff``) — truth-sensitive,
    always in ``app.py``.
  - Weather-context LLM lookup — requires async I/O; handled in ``app.py``.
  - Blocked-file intent check — ordering constraint (must come after submit
    checks); handled in ``app.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..vera.evidence_review import (
    ReviewedJobEvidence,
    draft_followup_preview,
    draft_revised_preview,
    draft_saveable_followup_preview,
    is_followup_preview_request,
    is_review_request,
    is_revise_from_evidence_request,
    is_save_followup_request,
    review_job_outcome,
    review_message,
)
from ..vera.investigation_derivations import (
    derive_investigation_comparison,
    derive_investigation_summary,
    draft_investigation_derived_save_preview,
    draft_investigation_save_preview,
    is_investigation_compare_request,
    is_investigation_expand_request,
    is_investigation_save_request,
    is_investigation_summary_request,
    select_investigation_results,
)
from ..vera.preview_drafting import (
    diagnostics_request_refusal,
)
from ..vera.preview_submission import is_near_miss_submit_phrase
from ..vera.reference_resolver import (
    ReferenceClass,
    classify_reference,
    resolve_job_id_from_context,
)
from ..vera.session_store import read_session_handoff_state


@dataclass
class EarlyExitResult:
    """Result returned by :func:`dispatch_early_exit_intent`.

    When ``matched`` is ``False`` no early-exit condition fired; the caller
    should continue through the normal LLM orchestration path.

    When ``matched`` is ``True`` the caller (``app.py``) must:

    1. If ``write_preview`` is True: call
       ``write_session_preview(root, session_id, preview_payload)``.
    2. If ``write_handoff_ready`` is True: call
       ``write_session_handoff_state(root, session_id, attempted=False,
       queue_path=..., status="preview_ready", error=None, job_id=None)``.
    3. If ``write_derived_output`` is True: call
       ``write_session_derived_investigation_output(root, session_id,
       derived_output)``.
    4. If ``context_updates`` is not None: call
       ``update_session_context(root, session_id, **context_updates)``.
    5. Call ``append_session_turn(root, session_id, role="assistant",
       text=assistant_text)``.
    6. Return ``_render_page(..., status=status)``.
    """

    matched: bool
    assistant_text: str = ""
    status: str = ""
    # Preview write instructions — app.py performs these writes
    preview_payload: dict[str, object] | None = None
    write_preview: bool = False
    write_handoff_ready: bool = False
    # Derived investigation output write instructions — app.py performs these writes
    derived_output: dict[str, object] | None = None
    write_derived_output: bool = False
    # Session-context update instructions — app.py performs these writes
    context_updates: dict[str, object] | None = None


def _followup_evidence_detail(evidence: ReviewedJobEvidence) -> str:
    """Return a short evidence-grounded detail line for the follow-up reply."""
    if evidence.state == "succeeded":
        summary = evidence.latest_summary or "completed successfully"
        return f"- Prior job outcome: succeeded — {summary}"
    if evidence.state == "failed":
        summary = evidence.failure_summary or evidence.latest_summary or "execution failed"
        return f"- Prior job outcome: failed — {summary}"
    if evidence.state == "awaiting_approval":
        return "- Prior job state: awaiting operator approval"
    if evidence.state == "canceled":
        return "- Prior job state: canceled (not failed)"
    return f"- Prior job state: {evidence.state}"


_AUTHORED_DRAFTING_SIGNAL_RE = re.compile(
    r"\b(?:write|create|draft|make|save)\s+(?:me\s+)?(?:a\s+)?(?:short\s+)?"
    r"(?:note|memo|document|letter|report|file)(?!\s+(?:called|named))\b",
    re.IGNORECASE,
)


def _looks_like_authored_drafting_request(message: str) -> bool:
    """Return True when the message has clear file-creation/note-writing signals.

    Used to suppress review/followup hint matching when the message is primarily
    an authored-drafting request that incidentally contains a hint substring
    (e.g. "Write me a note about what happened at the meeting" contains the
    review hint "what happened" but is clearly a drafting request).
    """
    return bool(_AUTHORED_DRAFTING_SIGNAL_RE.search(message))


def dispatch_early_exit_intent(
    *,
    message: str,
    diagnostics_service_turn: bool,
    requested_job_id: str | None,
    should_attempt_derived_save: bool,
    session_investigation: dict[str, object] | None,
    session_derived_output: dict[str, object] | None,
    queue_root: Path,
    session_id: str,
    session_context: dict[str, Any] | None = None,
) -> EarlyExitResult:
    """Evaluate message against all early-exit intent conditions in chat() order.

    Checks evaluated (in order):

    1. Diagnostics refusal — blocked system-diagnostics phrasing.
    2. Job review / evidence review — review request or explicit job ID.
    3. Follow-up preview request — draft follow-up from prior job evidence.
    4. Investigation derived-save — save the current derived artifact.
    5. Investigation compare — compare investigation result references.
    6. Investigation summary — summarise investigation result references.
    7. Investigation expand (error path) — invalid expand reference.
    8. Investigation save — save investigation findings to a governed preview.
    9. Near-miss submit phrase — fail-closed block on fuzzy submit phrasing.
    10. Stale draft reference — fail-closed when message references a draft
        but no active draft/preview exists in session context.

    Returns ``EarlyExitResult(matched=True)`` for the first condition that
    fires, or ``EarlyExitResult(matched=False)`` when none match.

    Intentionally **not** evaluated here (remain in ``app.py``):

    - Weather-context pending LLM lookup (requires async I/O).
    - Submit / handoff dispatch (``_submit_handoff`` — truth-sensitive).
    - Blocked-file intent check (ordering: must follow submit checks).
    """

    # ── 1. Diagnostics refusal ─────────────────────────────────────────────
    refusal = diagnostics_request_refusal(message)
    if refusal is not None:
        return EarlyExitResult(
            matched=True,
            assistant_text=refusal,
            status="blocked_diagnostics",
        )

    # ── 2. Job review / evidence review ───────────────────────────────────
    # An explicit job ID in the message always enters review (fail-closed if
    # evidence is missing).  Hint-based review requests enter the branch and
    # fail closed honestly when no job target is resolvable — this gives the
    # user a clear message rather than silent misrouting.
    #
    # Anti-hijack: when the message is primarily an authored-drafting request
    # (e.g. "Write me a note about what happened at the meeting") that
    # incidentally contains a review hint substring, suppress the hint match
    # so the drafting request proceeds normally.
    _is_drafting = _looks_like_authored_drafting_request(message)
    _review_hint_match = (
        is_review_request(message) and not diagnostics_service_turn and not _is_drafting
    )
    if requested_job_id is not None or _review_hint_match:
        target_job_id = requested_job_id
        if not target_job_id:
            handoff = read_session_handoff_state(queue_root, session_id) or {}
            target_job_id = str(handoff.get("job_id") or "") or None
        # Session-context fallback: resolve job ref from shared context when
        # neither explicit job ID nor handoff state provides one.
        if not target_job_id:
            target_job_id = resolve_job_id_from_context(session_context or {})

        evidence = review_job_outcome(queue_root=queue_root, requested_job_id=target_job_id)
        if evidence is None:
            return EarlyExitResult(
                matched=True,
                assistant_text=(
                    "I could not resolve a VoxeraOS job to review from canonical queue evidence. "
                    "No completed or in-progress linked job was found for this session. "
                    "Share a job id (for example `job-123.json`) or submit a job first so I can "
                    "inspect real results."
                ),
                status="review_missing_job",
            )
        _review_ctx: dict[str, object] = {"last_reviewed_job_ref": evidence.job_id}
        # When the reviewed job has reached a terminal state, also record it
        # as the last completed job so the debug surface stays lifecycle-fresh
        # even if the ingestion path hasn't run yet (e.g. user reviews before
        # the next turn triggers ingest_linked_job_completions).
        if evidence.state in {"succeeded", "failed", "canceled"}:
            _review_ctx["last_completed_job_ref"] = evidence.job_id
        return EarlyExitResult(
            matched=True,
            assistant_text=review_message(evidence),
            status="reviewed_job_outcome",
            context_updates=_review_ctx,
        )

    # ── 3. Follow-up preview request ───────────────────────────────────────
    # Follow-up hint phrases enter the branch and fail closed honestly when
    # no job target is resolvable.  Anti-hijack: suppress when the message
    # is primarily an authored-drafting request.
    if is_followup_preview_request(message) and not _is_drafting:
        handoff = read_session_handoff_state(queue_root, session_id) or {}
        _followup_job_id = str(handoff.get("job_id") or "") or None
        # Session-context fallback for follow-up evidence resolution.
        if not _followup_job_id:
            _followup_job_id = resolve_job_id_from_context(session_context or {})

        evidence = review_job_outcome(
            queue_root=queue_root,
            requested_job_id=_followup_job_id,
        )
        if evidence is None:
            return EarlyExitResult(
                matched=True,
                assistant_text=(
                    "I can draft a follow-up preview once we have a resolvable VoxeraOS job outcome. "
                    "No completed linked job could be resolved from this session. "
                    "Please give me a job id or submit a job first so I have canonical evidence to ground the follow-up."
                ),
                status="followup_missing_evidence",
            )
        followup_detail = _followup_evidence_detail(evidence)

        # ── 3a. Revise/update from evidence ──
        if is_revise_from_evidence_request(message):
            payload: dict[str, object] = {**draft_revised_preview(evidence)}
            return EarlyExitResult(
                matched=True,
                assistant_text=(
                    f"I've prepared a revised preview grounded in canonical evidence from `{evidence.job_id}`.\n"
                    f"{followup_detail}\n"
                    "This is preview-only — nothing has been submitted yet."
                ),
                status="revised_preview_ready",
                preview_payload=payload,
                write_preview=True,
                write_handoff_ready=True,
                context_updates={"last_reviewed_job_ref": evidence.job_id},
            )

        # ── 3b. Save follow-up as file ──
        if is_save_followup_request(message):
            payload = {**draft_saveable_followup_preview(evidence)}
            return EarlyExitResult(
                matched=True,
                assistant_text=(
                    f"I've prepared a saveable follow-up draft grounded in canonical evidence from `{evidence.job_id}`.\n"
                    f"{followup_detail}\n"
                    "The follow-up has been placed in the preview as a file draft. "
                    "This is preview-only — nothing has been submitted yet."
                ),
                status="save_followup_preview_ready",
                preview_payload=payload,
                write_preview=True,
                write_handoff_ready=True,
                context_updates={"last_reviewed_job_ref": evidence.job_id},
            )

        # ── 3c. General follow-up ──
        payload = {**draft_followup_preview(evidence)}
        return EarlyExitResult(
            matched=True,
            assistant_text=(
                f"I've prepared a follow-up preview grounded in canonical evidence from `{evidence.job_id}`.\n"
                f"{followup_detail}\n"
                "This is preview-only — nothing has been submitted yet."
            ),
            status="followup_preview_ready",
            preview_payload=payload,
            write_preview=True,
            write_handoff_ready=True,
            context_updates={"last_reviewed_job_ref": evidence.job_id},
        )

    # ── 4. Investigation derived-save request ──────────────────────────────
    if should_attempt_derived_save:
        derived_preview = draft_investigation_derived_save_preview(
            message,
            derived_output=session_derived_output,
        )
        if derived_preview is None:
            return EarlyExitResult(
                matched=True,
                assistant_text=(
                    "I couldn't find a current investigation comparison, summary, or expanded result "
                    "to save in this session. Ask me to compare, summarize, or expand a finding "
                    "first, then ask to save that output."
                ),
                status="investigation_derived_missing",
            )
        return EarlyExitResult(
            matched=True,
            assistant_text=(
                "I prepared a governed save-to-note preview from the latest investigation-derived "
                "text artifact. Nothing has been submitted yet."
            ),
            status="prepared_preview",
            preview_payload=derived_preview,
            write_preview=True,
            write_handoff_ready=True,
        )

    # ── 5. Investigation compare request ───────────────────────────────────
    if is_investigation_compare_request(message):
        comparison = derive_investigation_comparison(
            message,
            investigation_context=session_investigation,
        )
        if comparison is None:
            return EarlyExitResult(
                matched=True,
                assistant_text=(
                    "I couldn't resolve those result references for comparison in this session. "
                    "Run a fresh read-only investigation first, then compare valid result numbers "
                    "(for example: 'compare results 1 and 3' or 'compare all findings')."
                ),
                status="investigation_reference_invalid",
            )
        return EarlyExitResult(
            matched=True,
            assistant_text=str(comparison.get("answer") or ""),
            status="ok:investigation_comparison",
            derived_output=comparison,
            write_derived_output=True,
        )

    # ── 6. Investigation summary request ───────────────────────────────────
    if is_investigation_summary_request(message):
        summary = derive_investigation_summary(
            message,
            investigation_context=session_investigation,
        )
        if summary is None:
            return EarlyExitResult(
                matched=True,
                assistant_text=(
                    "I couldn't resolve those result references for summary in this session. "
                    "Run a fresh read-only investigation first, then summarize valid result numbers "
                    "(for example: 'summarize result 2' or 'summarize all findings')."
                ),
                status="investigation_reference_invalid",
            )
        return EarlyExitResult(
            matched=True,
            assistant_text=str(summary.get("answer") or ""),
            status="ok:investigation_summary",
            derived_output=summary,
            write_derived_output=True,
        )

    # ── 7. Investigation expand request — invalid-reference early exit ─────
    # NOTE: when the reference IS valid this branch does NOT return — the
    # caller continues to the normal LLM flow which performs the expansion.
    if is_investigation_expand_request(message):
        selected_results, selected_ids = select_investigation_results(
            message,
            investigation_context=session_investigation,
        )
        if (
            selected_results is None
            or selected_ids is None
            or len(selected_ids) != 1
            or not isinstance(session_investigation, dict)
        ):
            return EarlyExitResult(
                matched=True,
                assistant_text=(
                    "I couldn't resolve that investigation result for expansion in this session. "
                    "Run a fresh read-only investigation first, then expand one valid result number "
                    "(for example: 'expand result 1 please')."
                ),
                status="investigation_reference_invalid",
            )
        # Valid reference — fall through to LLM expansion in normal flow.

    # ── 8. Investigation save request ──────────────────────────────────────
    if is_investigation_save_request(message):
        investigation_preview = draft_investigation_save_preview(
            message,
            investigation_context=session_investigation,
        )
        if investigation_preview is None:
            return EarlyExitResult(
                matched=True,
                assistant_text=(
                    "I couldn't resolve those investigation result references in this session. "
                    "Run a fresh read-only investigation first, then refer to valid result numbers "
                    "(for example: 'save result 2 to a note' or 'save all findings')."
                ),
                status="investigation_reference_invalid",
            )
        return EarlyExitResult(
            matched=True,
            assistant_text=(
                "I prepared a governed save-to-note preview from your selected investigation findings. "
                "Nothing has been submitted yet."
            ),
            status="prepared_preview",
            preview_payload=investigation_preview,
            write_preview=True,
            write_handoff_ready=True,
        )

    # ── 9. Near-miss submit phrase (fail-closed) ───────────────────────────
    # Runs before the canonical submit path so a fuzzy near-submit never
    # reaches the LLM, which might overclaim submission.
    if is_near_miss_submit_phrase(message):
        return EarlyExitResult(
            matched=True,
            assistant_text=(
                "I did not submit the preview. "
                "That looked like a submit command but didn't match the expected phrasing. "
                'Try "send it", "submit it", or "hand it off" to submit.'
            ),
            status="near_miss_submit_rejected",
        )

    # ── 10. Stale draft reference (fail-closed) ────────────────────────────
    # When the message contains an explicit draft-class reference phrase
    # ("save that draft", "the draft", etc.) but session context has no
    # active draft or preview, fail closed rather than letting the builder
    # silently create a preview from recent assistant content.  This
    # prevents stale draft references after handoff + failed continuation.
    if classify_reference(message) == ReferenceClass.DRAFT:
        ctx = session_context if isinstance(session_context, dict) else {}
        _has_active_draft = bool(
            (isinstance(ctx.get("active_draft_ref"), str) and ctx["active_draft_ref"].strip())
            or (
                isinstance(ctx.get("active_preview_ref"), str) and ctx["active_preview_ref"].strip()
            )
        )
        if not _has_active_draft:
            return EarlyExitResult(
                matched=True,
                assistant_text=(
                    "There is no active draft or preview in this session. "
                    "The previous draft was already submitted and is no longer in play. "
                    "If you'd like to start a new draft, just ask."
                ),
                status="stale_draft_reference",
            )

    return EarlyExitResult(matched=False)
