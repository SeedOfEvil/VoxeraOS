from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.queue_inspect import lookup_job
from ..core.queue_result_consumers import resolve_structured_execution

_REVIEW_HINTS = (
    "what happened",
    "what happened to that job",
    "did it work",
    "did it succeed",
    "status",
    "check the last job",
    "last job",
    "what should i do next",
    "why did",
    "why did it fail",
    "why is it stuck",
    "stuck",
    "awaiting approval",
)
_FOLLOWUP_HINTS = (
    "prepare the next step",
    "draft the next step",
    "prepare next step",
    "draft follow-up",
    "prepare a follow-up",
    # correction intents
    "draft the correction",
    "draft a correction",
    "fix it and try again",
    "fix that and try",
    "correct it and try",
    # safer-version intents
    "make a safer version",
    "make it safer",
    "try a safer",
    "prepare a safer",
    "safer alternative",
    "safer version",
    # retry-with-different-target intents
    "retry that with a different target",
    "retry with a different target",
    "do the same but on another",
    "do that on another",
    "with a different target",
    # continuation intents
    "revise that based on what happened",
    "fix it and try again",
    "continue from that result",
    "continue from the result",
)

# Intent-classification hint sets (subset of _FOLLOWUP_HINTS)
_CORRECTION_HINTS = (
    "draft the correction",
    "draft a correction",
    "fix it and try again",
    "fix that and try",
    "correct it and try",
)
_SAFER_VERSION_HINTS = (
    "make a safer version",
    "make it safer",
    "try a safer",
    "prepare a safer",
    "safer alternative",
    "safer version",
)
_RETRY_DIFFERENT_TARGET_HINTS = (
    "retry that with a different target",
    "retry with a different target",
    "do the same but on another",
    "do that on another",
    "with a different target",
)


def _classify_followup_intent(message: str) -> str:
    """Return the follow-up intent: 'retry_different_target', 'safer_version',
    'correction', or 'next_step' (default)."""
    lowered = message.strip().lower()
    if any(h in lowered for h in _RETRY_DIFFERENT_TARGET_HINTS):
        return "retry_different_target"
    if any(h in lowered for h in _SAFER_VERSION_HINTS):
        return "safer_version"
    if any(h in lowered for h in _CORRECTION_HINTS):
        return "correction"
    return "next_step"


@dataclass(frozen=True)
class ReviewedJobEvidence:
    job_id: str
    state: str
    lifecycle_state: str
    terminal_outcome: str
    approval_status: str
    latest_summary: str
    failure_summary: str
    child_summary: dict[str, int] | None
    original_goal: str = field(default="")


def _read_json_dict(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def maybe_extract_job_id(message: str) -> str | None:
    match = re.search(
        r"\b((?:job|inbox|pending|done|failed|canceled)[\w.-]*)(?:\.json)?\b", message
    )
    candidate = match.group(1).strip() if match else ""
    if candidate and candidate.lower() not in {
        "job",
        "inbox",
        "pending",
        "done",
        "failed",
        "canceled",
    }:
        return f"{Path(candidate).stem}.json"

    short_match = re.search(r"\b(\d{10,}-[a-f0-9]{6,})\b", message.lower())
    if short_match:
        return short_match.group(1)
    return None


def is_review_request(message: str) -> bool:
    lowered = message.strip().lower()
    return bool(lowered) and any(hint in lowered for hint in _REVIEW_HINTS)


def is_followup_preview_request(message: str) -> bool:
    lowered = message.strip().lower()
    return bool(lowered) and any(hint in lowered for hint in _FOLLOWUP_HINTS)


def _classify_state(
    *, bucket: str, lifecycle_state: str, terminal_outcome: str, approval_status: str
) -> str:
    normalized_lifecycle = lifecycle_state.strip().lower()
    normalized_outcome = terminal_outcome.strip().lower()
    normalized_approval = approval_status.strip().lower()
    if normalized_approval == "pending" or normalized_lifecycle in {
        "awaiting_approval",
        "pending_approval",
    }:
        return "awaiting_approval"
    if normalized_outcome == "succeeded" or normalized_lifecycle == "done" or bucket == "done":
        return "succeeded"
    if (
        normalized_outcome in {"failed", "blocked"}
        or normalized_lifecycle == "failed"
        or bucket == "failed"
    ):
        return "failed"
    if (
        normalized_outcome == "canceled"
        or normalized_lifecycle == "canceled"
        or bucket == "canceled"
    ):
        return "canceled"
    if bucket == "inbox":
        return "submitted"
    if bucket == "pending":
        return "pending"
    return "pending"


def review_job_outcome(
    *, queue_root: Path, requested_job_id: str | None
) -> ReviewedJobEvidence | None:
    if not requested_job_id:
        return None
    found = _resolve_lookup_with_aliases(queue_root=queue_root, requested_job_id=requested_job_id)
    if found is None:
        return None

    job_payload = _read_json_dict(found.primary_path)
    original_goal = str(job_payload.get("goal") or "")
    state_payload = _read_json_dict(
        found.primary_path.with_name(f"{found.primary_path.stem}.state.json")
    )
    approval_payload = _read_json_dict(found.approval_path)
    failed_payload = _read_json_dict(found.failed_sidecar_path)
    structured = resolve_structured_execution(
        artifacts_dir=found.artifacts_dir,
        state_sidecar=state_payload,
        approval=approval_payload,
        failed_sidecar=failed_payload,
    )
    lifecycle_state = str(
        structured.get("lifecycle_state") or state_payload.get("lifecycle_state") or ""
    )
    terminal_outcome = str(
        structured.get("terminal_outcome") or state_payload.get("terminal_outcome") or ""
    )
    approval_status = str(
        structured.get("approval_status") or state_payload.get("approval_status") or "none"
    )
    latest_summary = str(structured.get("latest_summary") or "")
    failure_summary = str(
        structured.get("error")
        or state_payload.get("failure_summary")
        or failed_payload.get("error")
        or ""
    )
    child_summary = structured.get("child_summary")
    return ReviewedJobEvidence(
        job_id=found.job_id,
        state=_classify_state(
            bucket=found.bucket,
            lifecycle_state=lifecycle_state,
            terminal_outcome=terminal_outcome,
            approval_status=approval_status,
        ),
        lifecycle_state=lifecycle_state,
        terminal_outcome=terminal_outcome,
        approval_status=approval_status,
        latest_summary=latest_summary,
        failure_summary=failure_summary,
        child_summary=child_summary if isinstance(child_summary, dict) else None,
        original_goal=original_goal,
    )


def _candidate_job_ids(requested_job_id: str) -> list[str]:
    raw = Path(str(requested_job_id).strip()).name
    if not raw:
        return []
    stem = Path(raw).stem

    candidates: list[str] = []

    def _add(value: str) -> None:
        normalized = value.strip()
        if normalized and normalized not in candidates:
            candidates.append(normalized)

    _add(raw)
    _add(f"{stem}.json")

    raw_stem = Path(raw).stem
    if raw_stem.startswith("inbox-"):
        short = raw_stem.removeprefix("inbox-")
        _add(short)
        _add(f"{short}.json")
    else:
        _add(f"inbox-{raw_stem}")
        _add(f"inbox-{raw_stem}.json")
    return candidates


def _resolve_lookup_with_aliases(*, queue_root: Path, requested_job_id: str):
    for candidate in _candidate_job_ids(requested_job_id):
        found = lookup_job(queue_root, candidate)
        if found is not None:
            return found
    return None


def review_message(evidence: ReviewedJobEvidence) -> str:
    lines = [
        f"I reviewed canonical VoxeraOS evidence for `{evidence.job_id}`.",
        f"- State: `{evidence.state}`",
        f"- Lifecycle state: `{evidence.lifecycle_state or 'unknown'}`",
        f"- Terminal outcome: `{evidence.terminal_outcome or 'not terminal yet'}`",
        f"- Approval status: `{evidence.approval_status or 'none'}`",
        f"- Latest summary: {evidence.latest_summary or 'No summary is available yet.'}",
    ]
    if evidence.failure_summary:
        lines.append(f"- Failure summary: {evidence.failure_summary}")
    if evidence.child_summary:
        lines.append(f"- Child summary: {evidence.child_summary}")
    lines.append(f"Next step: {_next_step(evidence)}")
    return "\n".join(lines)


def _next_step(evidence: ReviewedJobEvidence) -> str:
    if evidence.state == "awaiting_approval":
        return "Approve or reject the pending approval in VoxeraOS; chat cannot bypass this gate."
    if evidence.state == "submitted":
        return "The job is only submitted so far; wait for VoxeraOS to start execution and check progress again."
    if evidence.state == "pending":
        return "The job is still running in VoxeraOS; check progress again after more execution evidence is written."
    if evidence.state == "succeeded":
        return "No action is required unless you want me to draft a follow-up preview grounded in these results."
    if evidence.state == "failed":
        return "Use the failure summary to correct the request, then I can draft a safer follow-up preview for you."
    if evidence.state == "canceled":
        return "If you still want this outcome, I can draft a new preview for resubmission."
    return (
        "Evidence is incomplete; verify in the VoxeraOS panel/queue before deciding on a follow-up."
    )


def draft_followup_preview(
    evidence: ReviewedJobEvidence, *, user_message: str = ""
) -> dict[str, Any]:
    """Generate a follow-up preview payload grounded in canonical VoxeraOS evidence.

    Uses the follow-up intent from the user message and the original job goal to
    produce a specific, actionable draft rather than a generic placeholder.
    """
    intent = _classify_followup_intent(user_message)
    original = evidence.original_goal.strip()

    if intent == "retry_different_target":
        # Preserve the original draft so the user has a starting point to edit.
        # The assistant message (see followup_preview_message) tells them to update the target.
        goal = original if original else "retry with a different target"

    elif intent == "safer_version":
        failure_ctx = evidence.failure_summary or evidence.latest_summary
        if original and failure_ctx:
            goal = f"{original} (safer version: address {failure_ctx})"
        elif original:
            goal = f"{original} (safer version)"
        else:
            goal = "prepare a safer version of the previous request"

    elif intent == "correction":
        failure_ctx = evidence.failure_summary or evidence.latest_summary
        if original and failure_ctx:
            goal = f"{original} (correcting: {failure_ctx})"
        elif original:
            goal = f"retry: {original}"
        else:
            failure_ctx = failure_ctx or "unknown failure"
            goal = f"prepare a corrected retry after: {failure_ctx}"

    else:  # next_step / generic
        if evidence.state == "awaiting_approval":
            goal = (
                f"review approval requirements for: {original}"
                if original
                else f"review approval requirements for {evidence.job_id}"
            )
        elif evidence.state == "failed":
            failure_ctx = evidence.failure_summary or evidence.latest_summary or "the prior failure"
            goal = (
                f"retry: {original} after addressing: {failure_ctx}"
                if original
                else f"prepare corrected retry after: {failure_ctx}"
            )
        elif evidence.state == "succeeded":
            summary = evidence.latest_summary or "the completed result"
            goal = (
                f"prepare the next step after: {original}"
                if original
                else f"inspect and act on: {summary}"
            )
        else:
            goal = (
                f"prepare next action for: {original}"
                if original
                else f"check status and evidence for {evidence.job_id}"
            )

    return {"goal": goal}


def followup_preview_message(
    evidence: ReviewedJobEvidence,
    payload: dict[str, Any],
    *,
    user_message: str = "",
) -> str:
    """Format the assistant reply for a follow-up preview grounded in VoxeraOS evidence."""
    intent = _classify_followup_intent(user_message)
    intent_label = {
        "correction": "correction after failure",
        "safer_version": "safer version",
        "retry_different_target": "retry with different target",
        "next_step": "next step",
    }.get(intent, "next step")

    lines = [
        f"I drafted a follow-up preview grounded in VoxeraOS evidence for `{evidence.job_id}` "
        f"(state: `{evidence.state}`, intent: {intent_label}).",
        "",
        f"```json\n{json.dumps(payload, indent=2)}\n```",
        "",
    ]

    if intent == "retry_different_target" and evidence.original_goal:
        lines.append(
            "Update the target in the draft before submitting — the goal above "
            f"is based on your previous request (`{evidence.original_goal}`)."
        )
    elif evidence.state == "failed" and evidence.failure_summary:
        lines.append(
            f"The previous attempt failed with: {evidence.failure_summary}. "
            "Revise the draft to correct the issue before submitting."
        )
    elif evidence.state == "awaiting_approval":
        lines.append(
            "The previous attempt is pending operator approval. "
            "Revise the draft to narrow scope or clarify intent if needed."
        )
    elif evidence.state == "succeeded" and evidence.latest_summary:
        lines.append(f"Previous result: {evidence.latest_summary}.")

    lines.append("This is preview-only. I did not submit anything to VoxeraOS.")
    return "\n".join(lines)
