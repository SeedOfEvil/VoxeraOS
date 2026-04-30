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
from ..vera.first_run_tour import (
    advance_walkthrough,
    clear_walkthrough,
    is_first_run_tour_request,
    is_walkthrough_active,
    is_walkthrough_exit_request,
    start_walkthrough,
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
from ..vera.time_context import answer_time_question

_PREVIEW_INSPECTION_LIMIT = 1000
_PREVIEW_INSPECTION_PATTERNS = (
    re.compile(r"^\s*where(?:'s|\s+is)\s+(?:the\s+)?content\??\s*$", re.IGNORECASE),
    re.compile(r"^\s*what(?:'s|\s+is)\s+in\s+the\s+draft\??\s*$", re.IGNORECASE),
    re.compile(r"^\s*what(?:'s|\s+is)\s+in\s+the\s+preview\??\s*$", re.IGNORECASE),
    re.compile(r"^\s*show me the content\s*$", re.IGNORECASE),
    re.compile(r"^\s*show current preview content\s*$", re.IGNORECASE),
    re.compile(r"^\s*what content is in the draft\??\s*$", re.IGNORECASE),
    re.compile(r"^\s*what content is in the preview\??\s*$", re.IGNORECASE),
    re.compile(r"^\s*what will be written\??\s*$", re.IGNORECASE),
    re.compile(r"^\s*what are you going to write\??\s*$", re.IGNORECASE),
    re.compile(r"^\s*show me what will be saved\s*$", re.IGNORECASE),
    re.compile(r"^\s*show me the draft\s*$", re.IGNORECASE),
)


def _is_preview_content_inspection_request(message: str) -> bool:
    return any(p.match(message) for p in _PREVIEW_INSPECTION_PATTERNS)


def _extract_write_file_from_preview(
    active_preview: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(active_preview, dict):
        return None
    wf = active_preview.get("write_file")
    return wf if isinstance(wf, dict) else None


def _build_preview_inspection_response(active_preview: dict[str, Any] | None) -> str:
    if not isinstance(active_preview, dict):
        return "There is no active preview in this session right now."
    wf = _extract_write_file_from_preview(active_preview)
    if wf is None:
        kind = str(active_preview.get("kind") or "").strip().lower()
        if kind:
            return (
                "There is an active preview in this session, but it is not a write-file draft.\n\n"
                f"Active preview kind: {kind}"
            )
        return "There is an active preview in this session, but it is not a write-file draft."
    path = str(wf.get("path") or "(unknown path)")
    content = str(wf.get("content") or "")
    if not content:
        return (
            "Active write preview\n\n"
            f"Path: {path}\n\n"
            "Content is currently empty. I did not submit anything. "
            "Please provide content or revise the draft before submitting."
        )
    preview = content[:_PREVIEW_INSPECTION_LIMIT]
    truncated = len(content) - len(preview)
    out = f"Active write preview\n\nPath: {path}\n\nContent:\n\n{preview}"
    if truncated > 0:
        out += f"\n\n*(truncated — {truncated} more characters)*"
    return out


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
        return f"Prior result: succeeded — {summary}"
    if evidence.state == "failed":
        summary = evidence.failure_summary or evidence.latest_summary or "execution failed"
        return f"Prior result: failed — {summary}"
    if evidence.state == "awaiting_approval":
        return "Prior state: awaiting operator approval"
    if evidence.state == "canceled":
        return "Prior state: canceled (not failed)"
    return f"Prior state: {evidence.state}"


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
    active_preview_revision_in_flight: bool = False,
    active_preview: dict[str, Any] | None = None,
) -> EarlyExitResult:
    """Evaluate message against all early-exit intent conditions in chat() order.

    Checks evaluated (in order):

    0. Interactive walkthrough — active walkthrough advance, cancel,
       off-topic replay, or new tour start request.
    1. Time question — deterministic local-time / timezone answer.
    2. Diagnostics refusal — blocked system-diagnostics phrasing.
    3. Job review / evidence review — review request or explicit job ID.
    4. Follow-up preview request — draft follow-up from prior job evidence.
    5. Investigation derived-save — save the current derived artifact.
    6. Investigation compare — compare investigation result references.
    7. Investigation summary — summarise investigation result references.
    8. Investigation expand (error path) — invalid expand reference.
    9. Investigation save — save investigation findings to a governed preview.
    10. Near-miss submit phrase — fail-closed block on fuzzy submit phrasing.
    11. Stale draft reference — fail-closed when message references a draft
        but no active draft/preview exists in session context.

    Returns ``EarlyExitResult(matched=True)`` for the first condition that
    fires, or ``EarlyExitResult(matched=False)`` when none match.

    Intentionally **not** evaluated here (remain in ``app.py``):

    - Weather-context pending LLM lookup (requires async I/O).
    - Submit / handoff dispatch (``_submit_handoff`` — truth-sensitive).
    - Blocked-file intent check (ordering: must follow submit checks).

    Active-preview revision protection
    ----------------------------------
    When ``active_preview_revision_in_flight`` is ``True`` (the caller has
    detected a clear revision/follow-up mutation of a normal active
    preview via ``preview_routing.is_active_preview_revision_turn``),
    the preview-writing branches below are skipped so they cannot
    overwrite the active preview with an evidence-grounded follow-up.
    The non-mutating branches (time, diagnostics refusal, job review
    report, near-miss submit rejection, stale-draft reference) still
    run — they do not touch preview state. The read-only investigation
    compare/summary branches also still run because they only write
    ``derived_investigation_output``, not the preview.
    """

    # ── 0. Interactive walkthrough ───────────────────────────────────────
    # When the walkthrough is already active, handle cancel, advance, or
    # off-topic replay.  Checked BEFORE the tour-start pattern so that
    # messages containing "Voxera tour" mid-walkthrough do not restart.
    # The "submit it" message is NOT intercepted — advance returns None
    # at the final step so the normal EXPLICIT_SUBMIT lane handles it.
    if is_walkthrough_active(queue_root, session_id):
        if is_walkthrough_exit_request(message):
            clear_walkthrough(queue_root, session_id)
            return EarlyExitResult(
                matched=True,
                assistant_text=(
                    "Tour cancelled. The preview is still available if you want to "
                    "refine or submit it, or you can start fresh."
                ),
                status="walkthrough_cancelled",
            )
        result = advance_walkthrough(queue_root, session_id, message=message)
        if result is not None:
            text, status = result
            return EarlyExitResult(
                matched=True,
                assistant_text=text,
                status=status,
            )
        # result is None → final step reached, let submit flow through.

    # "Start VoxeraOS tour" begins the interactive walkthrough and creates
    # an initial write_file preview so the user can refine it step by step.
    # Only fires when no walkthrough is already active (guarded above).
    if is_first_run_tour_request(message):
        text, status = start_walkthrough(queue_root, session_id)
        return EarlyExitResult(
            matched=True,
            assistant_text=text,
            status=status,
        )

    # ── 1. Time question ──────────────────────────────────────────────────
    # Simple "what time is it?" / "what day is it?" questions are answered
    # deterministically from the system clock — no LLM needed.
    time_answer = answer_time_question(message)
    if time_answer is not None:
        return EarlyExitResult(
            matched=True,
            assistant_text=time_answer,
            status="ok:time_question",
        )
    # ── 1a. Active preview content inspection ─────────────────────────────
    # Deterministic truth path: report canonical active preview content
    # directly, without entering the normal LLM orchestration flow.
    if _is_preview_content_inspection_request(message):
        return EarlyExitResult(
            matched=True,
            assistant_text=_build_preview_inspection_response(active_preview),
            status="ok:active_preview_inspection",
        )

    # ── 2. Diagnostics refusal ─────────────────────────────────────────────
    refusal = diagnostics_request_refusal(message)
    if refusal is not None:
        return EarlyExitResult(
            matched=True,
            assistant_text=refusal,
            status="blocked_diagnostics",
        )

    # ── 3. Job review / evidence review ───────────────────────────────────
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
                    "No job could be resolved for review in this session. "
                    "Share a job ID (e.g. `job-123.json`) or submit a job first "
                    "so I can inspect real results."
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

    # ── 4. Follow-up preview request ───────────────────────────────────────
    # Follow-up hint phrases enter the branch and fail closed honestly when
    # no job target is resolvable.  Anti-hijack: suppress when the message
    # is primarily an authored-drafting request OR when the caller has
    # already determined that a clear active-preview revision is in flight
    # (e.g. "revise that based on the result" with an active normal preview
    # — the user is mutating the active preview, not spawning a new
    # evidence-grounded follow-up that would overwrite it).
    if (
        is_followup_preview_request(message)
        and not _is_drafting
        and not active_preview_revision_in_flight
    ):
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
                    "I can't draft a follow-up yet — no completed job could be resolved "
                    "from this session. Share a job ID or submit a job first so I have "
                    "evidence to ground it."
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
                    f"Here's a revised preview based on `{evidence.job_id}`.\n"
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
                    f"Here's a saveable follow-up based on `{evidence.job_id}`.\n"
                    f"{followup_detail}\n"
                    "It's ready as a file draft in the preview — nothing has been submitted yet."
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
                f"Here's a follow-up preview based on `{evidence.job_id}`.\n"
                f"{followup_detail}\n"
                "Review or refine it — this is preview-only, nothing has been submitted yet."
            ),
            status="followup_preview_ready",
            preview_payload=payload,
            write_preview=True,
            write_handoff_ready=True,
            context_updates={"last_reviewed_job_ref": evidence.job_id},
        )

    # ── 5. Investigation derived-save request ──────────────────────────────
    # Skipped when an active-preview revision is in flight so a derived
    # save cannot silently overwrite the preview the user is mutating.
    if should_attempt_derived_save and not active_preview_revision_in_flight:
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

    # ── 6. Investigation compare request ───────────────────────────────────
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

    # ── 7. Investigation summary request ───────────────────────────────────
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

    # ── 8. Investigation expand request — invalid-reference early exit ─────
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

    # ── 9. Investigation save request ──────────────────────────────────────
    # Skipped when an active-preview revision is in flight: a phrase like
    # "save that" on an active preview must mutate the active preview
    # (rename / revise), not spawn a new investigation-save preview that
    # would overwrite it.
    if is_investigation_save_request(message) and not active_preview_revision_in_flight:
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

    # ── 10. Near-miss submit phrase (fail-closed) ──────────────────────────
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

    # ── 11. Stale draft reference (fail-closed) ────────────────────────────
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
