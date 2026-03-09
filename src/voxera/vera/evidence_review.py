from __future__ import annotations

import json
import re
from dataclasses import dataclass
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
)


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


def draft_followup_preview(evidence: ReviewedJobEvidence) -> dict[str, str]:
    if evidence.state == "awaiting_approval":
        goal = f"review approval requirements for {evidence.job_id}"
    elif evidence.state == "failed":
        summary = evidence.failure_summary or evidence.latest_summary or "the prior failure"
        goal = f"prepare a corrected retry for {evidence.job_id} after addressing: {summary}"
    elif evidence.state == "succeeded":
        summary = evidence.latest_summary or "the completed result"
        goal = f"inspect output details from {evidence.job_id}: {summary}"
    else:
        goal = f"check status and evidence for {evidence.job_id}"
    return {"goal": goal}
