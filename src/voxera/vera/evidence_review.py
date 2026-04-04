from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.queue_inspect import lookup_job
from ..core.queue_result_consumers import resolve_structured_execution
from .result_surfacing import extract_value_forward_text

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
    # Result inspection / summary phrases
    "summarize the result",
    "summarize that result",
    "summarize the job result",
    "inspect output",
    "inspect output details",
    "inspect the output",
    "inspect the output details",
    "review the result",
    "review that result",
    "show me the result",
    "show the result",
    "show me the output",
    "show the output",
    "what was the outcome",
    "what was the result",
    "what was the output",
)
_FOLLOWUP_HINTS = (
    "prepare the next step",
    "draft the next step",
    "prepare next step",
    "draft follow-up",
    "draft a follow-up",
    "draft the follow-up",
    "write a follow-up",
    "write the follow-up",
    "create a follow-up",
    "make a follow-up",
    "prepare a follow-up",
    "prepare the follow-up",
    "based on that job",
    "based on the job",
    "based on that result",
    "based on the result",
    "based on that outcome",
    "based on the outcome",
    "now do the follow-up",
    "now prepare the follow-up",
    "now draft the follow-up",
    "queue the next step",
    "queue a follow-up",
    "queue the follow-up",
    "do the next step",
    "do the follow-up",
    "let's do the next step",
    "okay now do the follow-up",
    "what should we do next based on that",
    "what's the next step based on that",
    "what should we do next",
    "what's the next step",
    "what next based on that",
    # Revise-from-evidence phrases
    "revise that based on the result",
    "revise based on the result",
    "revise that based on the evidence",
    "revise that based on the outcome",
    "revise based on evidence",
    "update that based on the result",
    "update based on the result",
    "update that based on the output",
    "update based on the output",
    "revise that based on the output",
    "revise based on the output",
    # Save follow-up phrases
    "save the follow-up",
    "save that follow-up",
    "save the follow-up as a file",
)

_REVISE_FROM_EVIDENCE_HINTS = (
    "revise that based on the result",
    "revise based on the result",
    "revise that based on the evidence",
    "revise that based on the outcome",
    "revise based on evidence",
    "update that based on the result",
    "update based on the result",
    "update that based on the output",
    "update based on the output",
    "revise that based on the output",
    "revise based on the output",
)

_SAVE_FOLLOWUP_HINTS = (
    "save the follow-up",
    "save that follow-up",
    "save the follow-up as a file",
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
    artifact_families: tuple[str, ...]
    artifact_refs: tuple[str, ...]
    evidence_trace: tuple[str, ...]
    child_summary: dict[str, int] | None
    execution_capabilities: dict[str, Any] | None
    capability_boundary_violation: dict[str, Any] | None
    expected_artifacts: tuple[str, ...]
    observed_expected_artifacts: tuple[str, ...]
    missing_expected_artifacts: tuple[str, ...]
    expected_artifact_status: str
    normalized_outcome_class: str
    value_forward_text: str


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


def is_revise_from_evidence_request(message: str) -> bool:
    """Return True when the message asks to revise/update prior output based on job evidence."""
    lowered = message.strip().lower()
    return bool(lowered) and any(hint in lowered for hint in _REVISE_FROM_EVIDENCE_HINTS)


def is_save_followup_request(message: str) -> bool:
    """Return True when the message asks to save the follow-up as a file or preview."""
    lowered = message.strip().lower()
    return bool(lowered) and any(hint in lowered for hint in _SAVE_FOLLOWUP_HINTS)


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
    if normalized_lifecycle == "queued":
        return "queued"
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
    if normalized_lifecycle in {"planning"}:
        return "planning"
    if normalized_lifecycle in {"running", "advisory_running"}:
        return "running"
    if normalized_lifecycle in {"resumed"}:
        return "resumed"
    if bucket == "inbox":
        return "submitted"
    if bucket == "pending":
        return "pending"
    return "pending"


def _normalize_artifact_refs(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    refs: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        family = str(item.get("artifact_family") or "").strip() or "unknown"
        path = str(item.get("artifact_path") or "").strip() or "unknown"
        refs.append(f"{family}:{path}")
    return tuple(sorted(set(refs)))


def _normalize_families(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    families = sorted({str(item).strip() for item in value if str(item).strip()})
    return tuple(families)


def _normalize_strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    normalized = sorted({str(item).strip() for item in value if str(item).strip()})
    return tuple(normalized)


def _evidence_trace(structured: dict[str, Any]) -> tuple[str, ...]:
    review_summary: dict[str, Any] = {}
    review_summary_raw = structured.get("review_summary")
    if isinstance(review_summary_raw, dict):
        review_summary = review_summary_raw

    evidence_bundle = structured.get("evidence_bundle")
    trace_payload = evidence_bundle.get("trace") if isinstance(evidence_bundle, dict) else {}
    if not isinstance(trace_payload, dict):
        trace_payload = {}
    trace = {
        f"terminal_outcome={str(trace_payload.get('terminal_outcome') or review_summary.get('terminal_outcome') or '').strip()}",
        f"execution_lane={str(trace_payload.get('execution_lane') or review_summary.get('execution_lane') or '').strip()}",
        f"attempt_index={str(trace_payload.get('attempt_index') or review_summary.get('attempt_index') or '').strip()}",
        f"lifecycle_state={str(trace_payload.get('lifecycle_state') or review_summary.get('lifecycle_state') or structured.get('lifecycle_state') or '').strip()}",
        f"approval_status={str(trace_payload.get('approval_status') or review_summary.get('approval_status') or structured.get('approval_status') or '').strip()}",
    }
    return tuple(sorted(item for item in trace if not item.endswith("=")))


def _select_latest_summary(*, structured: dict[str, Any], state_payload: dict[str, Any]) -> str:
    review_summary: dict[str, Any] = {}
    review_summary_raw = structured.get("review_summary")
    if isinstance(review_summary_raw, dict):
        review_summary = review_summary_raw

    evidence_bundle: dict[str, Any] = {}
    evidence_bundle_raw = structured.get("evidence_bundle")
    if isinstance(evidence_bundle_raw, dict):
        evidence_bundle = evidence_bundle_raw

    nested_summary: dict[str, Any] = {}
    nested_summary_raw = evidence_bundle.get("review_summary")
    if isinstance(nested_summary_raw, dict):
        nested_summary = nested_summary_raw
    preferred = str(
        review_summary.get("latest_summary")
        or nested_summary.get("latest_summary")
        or structured.get("latest_summary")
    )
    if preferred:
        return preferred

    terminal_outcome = (
        str(structured.get("terminal_outcome") or state_payload.get("terminal_outcome") or "")
        .strip()
        .lower()
    )
    if terminal_outcome in {"failed", "blocked"}:
        return str(state_payload.get("failure_summary") or "")
    return ""


def _select_failure_summary(
    *, structured: dict[str, Any], state_payload: dict[str, Any], failed_payload: dict[str, Any]
) -> str:
    review_summary = structured.get("review_summary")
    review_summary_dict = review_summary if isinstance(review_summary, dict) else {}

    evidence_bundle = structured.get("evidence_bundle")
    evidence_bundle_dict = evidence_bundle if isinstance(evidence_bundle, dict) else {}
    nested_summary = evidence_bundle_dict.get("review_summary")
    nested_summary_dict = nested_summary if isinstance(nested_summary, dict) else {}

    return str(
        review_summary_dict.get("failure_summary")
        or nested_summary_dict.get("failure_summary")
        or structured.get("error")
        or state_payload.get("failure_summary")
        or failed_payload.get("error")
        or ""
    )


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
    latest_summary = _select_latest_summary(structured=structured, state_payload=state_payload)
    failure_summary = _select_failure_summary(
        structured=structured,
        state_payload=state_payload,
        failed_payload=failed_payload,
    )
    child_summary = structured.get("child_summary")
    evidence_bundle: dict[str, Any] = {}
    evidence_bundle_raw = structured.get("evidence_bundle")
    if isinstance(evidence_bundle_raw, dict):
        evidence_bundle = evidence_bundle_raw
    artifact_families = _normalize_families(
        structured.get("artifact_families") or evidence_bundle.get("artifact_families") or []
    )
    artifact_refs = _normalize_artifact_refs(
        structured.get("artifact_refs") or evidence_bundle.get("artifact_refs") or []
    )
    job_payload = _read_json_dict(found.primary_path)
    raw_job_intent = job_payload.get("job_intent")
    job_intent: dict[str, Any] = raw_job_intent if isinstance(raw_job_intent, dict) else {}
    mission_id = str(job_intent.get("mission_id") or job_payload.get("mission_id") or "").strip()
    vf_text = extract_value_forward_text(structured=structured, mission_id=mission_id)

    review_summary = structured.get("review_summary")
    review_summary_dict = review_summary if isinstance(review_summary, dict) else {}
    execution_capabilities = review_summary_dict.get("execution_capabilities")
    capability_boundary_violation = review_summary_dict.get("capability_boundary_violation")
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
        artifact_families=artifact_families,
        artifact_refs=artifact_refs,
        evidence_trace=_evidence_trace(structured),
        child_summary=child_summary if isinstance(child_summary, dict) else None,
        execution_capabilities=(
            execution_capabilities if isinstance(execution_capabilities, dict) else None
        ),
        capability_boundary_violation=(
            capability_boundary_violation
            if isinstance(capability_boundary_violation, dict)
            else None
        ),
        expected_artifacts=_normalize_strings(review_summary_dict.get("expected_artifacts") or []),
        observed_expected_artifacts=_normalize_strings(
            review_summary_dict.get("observed_expected_artifacts") or []
        ),
        missing_expected_artifacts=_normalize_strings(
            review_summary_dict.get("missing_expected_artifacts") or []
        ),
        expected_artifact_status=str(review_summary_dict.get("expected_artifact_status") or ""),
        normalized_outcome_class=str(structured.get("normalized_outcome_class") or ""),
        value_forward_text=vf_text or "",
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
        f"- Normalized outcome class: `{evidence.normalized_outcome_class or 'unknown'}`",
        f"- Latest summary: {evidence.latest_summary or 'No summary is available yet.'}",
    ]
    if evidence.value_forward_text:
        lines.append(f"- Result: {evidence.value_forward_text}")
    if evidence.failure_summary:
        lines.append(f"- Failure summary: {evidence.failure_summary}")
    if evidence.child_summary:
        lines.append(f"- Child summary: {evidence.child_summary}")
    if evidence.artifact_families:
        lines.append(f"- Artifact families: {', '.join(evidence.artifact_families)}")
    if evidence.artifact_refs:
        lines.append(f"- Artifact refs: {', '.join(evidence.artifact_refs[:5])}")
    if evidence.evidence_trace:
        lines.append(f"- Evidence trace: {', '.join(evidence.evidence_trace)}")
    if evidence.execution_capabilities:
        lines.append(
            "- Execution capabilities: "
            f"side_effect_class={evidence.execution_capabilities.get('side_effect_class', 'unknown')}, "
            f"network_scope={evidence.execution_capabilities.get('network_scope', 'unknown')}, "
            f"fs_scope={evidence.execution_capabilities.get('fs_scope', 'unknown')}, "
            f"sandbox_profile={evidence.execution_capabilities.get('sandbox_profile', 'unknown')}"
        )
    if evidence.capability_boundary_violation:
        lines.append(
            "- Capability boundary violation: "
            f"boundary={evidence.capability_boundary_violation.get('boundary', 'unknown')}, "
            f"declared_network_scope={evidence.capability_boundary_violation.get('declared_network_scope', 'unknown')}, "
            f"requested_network={evidence.capability_boundary_violation.get('requested_network', 'unknown')}"
        )
    artifact_observation = _artifact_observation_line(evidence)
    if artifact_observation:
        lines.append(f"- {artifact_observation}")

    if evidence.expected_artifacts:
        lines.append(
            f"- Expected artifacts ({evidence.expected_artifact_status or 'unknown'}): "
            + ", ".join(evidence.expected_artifacts)
        )
        if evidence.observed_expected_artifacts:
            lines.append(
                "- Observed expected artifacts: " + ", ".join(evidence.observed_expected_artifacts)
            )
        if evidence.missing_expected_artifacts:
            lines.append(
                "- Missing expected artifacts: " + ", ".join(evidence.missing_expected_artifacts)
            )
    else:
        lines.append("- Expected artifacts: none declared for this job.")
    lines.append(f"Next step: {_next_step(evidence)}")
    return "\n".join(lines)


def _artifact_observation_line(evidence: ReviewedJobEvidence) -> str:
    if not evidence.expected_artifacts:
        return ""

    expected = ", ".join(evidence.expected_artifacts)
    observed = ", ".join(evidence.observed_expected_artifacts) or "none"
    missing = ", ".join(evidence.missing_expected_artifacts) or "none"
    status = evidence.expected_artifact_status.strip().lower()

    if status == "observed" and not evidence.missing_expected_artifacts:
        return f"Expected artifacts were fully observed ({expected})."
    if status == "partial" or (
        evidence.observed_expected_artifacts and evidence.missing_expected_artifacts
    ):
        return (
            f"Expected artifacts were partially observed: observed={observed}; missing={missing}."
        )
    if status == "missing" or (
        not evidence.observed_expected_artifacts and evidence.missing_expected_artifacts
    ):
        return f"Expected artifacts were not observed: missing={missing}."
    return f"Expected artifact observation is '{status or 'unknown'}': observed={observed}; missing={missing}."


def _next_step(evidence: ReviewedJobEvidence) -> str:
    lifecycle = evidence.lifecycle_state.strip().lower()
    approval = evidence.approval_status.strip().lower()
    outcome_class = evidence.normalized_outcome_class.strip().lower()
    if evidence.state == "awaiting_approval":
        if evidence.expected_artifacts and evidence.missing_expected_artifacts:
            return (
                "Job is blocked on operator approval; missing runtime outputs are expected until approval allows execution to continue. "
                "Approve or reject in VoxeraOS first."
            )
        return "Job is blocked on operator approval; approve or reject in VoxeraOS before execution can continue."
    if evidence.state == "queued":
        return "The job is accepted and queued; wait for planning/running evidence before asking for outcome."
    if evidence.state == "submitted":
        return "The job is submitted but not yet queued for active execution; check queue intake and poll again."
    if evidence.state == "planning":
        return "VoxeraOS is planning now; wait for runtime step evidence before judging execution outcome."
    if evidence.state == "running":
        return "VoxeraOS is actively running; do not treat this as done yet—check again after new evidence is persisted."
    if evidence.state == "resumed":
        return "The job resumed after interruption/approval; wait for terminal queue and evidence state before concluding."
    if evidence.state == "pending":
        if lifecycle == "running":
            return "The job remains in-flight; wait for a terminal queue state and fresh evidence before concluding."
        if approval == "pending":
            return "The job is pending because approval is still required; approve or reject in VoxeraOS."
        return "The job is non-terminal and evidence is incomplete; poll queue progress before deciding next action."
    if evidence.state == "succeeded":
        if outcome_class == "incomplete_evidence":
            return (
                "The job succeeded but expected outputs were not observed; inspect execution_result, step logs, "
                "and artifact output paths, then rerun if evidence capture is required."
            )
        if outcome_class == "partial_artifact_gap":
            return (
                "The job succeeded with partial expected outputs; inspect execution_result and logs to verify whether "
                "the missing artifacts are benign evidence gaps or require a rerun for complete capture."
            )
        if evidence.missing_expected_artifacts:
            if evidence.observed_expected_artifacts:
                return (
                    "The job succeeded with partial expected outputs; inspect execution_result and logs to verify whether "
                    "the missing artifacts are benign evidence gaps or require a rerun for complete capture."
                )
            return (
                "The job succeeded but expected outputs were not observed; inspect execution_result, step logs, "
                "and artifact output paths, then rerun if evidence capture is required."
            )
        return "No action is required unless you want me to draft a follow-up preview grounded in these results."
    if evidence.state == "failed":
        if outcome_class == "policy_denied":
            return "Execution was denied by policy; adjust policy/approval scope or submit a lower-risk preview before retrying."
        if outcome_class == "capability_boundary_mismatch":
            return "Execution hit a capability boundary mismatch; align declared capabilities with runtime requests before rerunning."
        if outcome_class == "path_blocked_scope":
            return "Execution was blocked by path scope controls; choose an allowed workspace path or update the plan to avoid control-plane paths."
        if outcome_class == "runtime_dependency_missing":
            return "Execution failed because a required runtime dependency/tool is missing; install or correct the executable/tool reference, then rerun."
        if evidence.missing_expected_artifacts:
            if evidence.observed_expected_artifacts:
                return (
                    "Execution failed with partial expected outputs; inspect stderr and step_results first, then validate "
                    "declared output paths and approval decisions before retrying."
                )
            return (
                "Execution failed and expected outputs were not observed; inspect stderr and step_results first, then "
                "check approval decisions and declared output paths before retrying with corrected inputs."
            )
        return "Execution failed; use the grounded failure summary to correct inputs/permissions, then submit a revised preview."
    if evidence.state == "canceled":
        if evidence.expected_artifacts and evidence.missing_expected_artifacts:
            return (
                "Execution was canceled (not failed); missing expected outputs may be caused by cancellation before artifact "
                "production. If still needed, submit a new preview and rerun intentionally."
            )
        return "Execution was canceled (not failed); if still needed, submit a new preview and rerun intentionally."
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
        goal = (
            f"draft a follow-up step grounded in completed evidence from "
            f"{evidence.job_id}: {summary}"
        )
    elif evidence.state == "canceled":
        goal = f"draft a replacement step for canceled job {evidence.job_id}"
    else:
        goal = f"check status and evidence for {evidence.job_id}"
    return {"goal": goal}


def draft_revised_preview(evidence: ReviewedJobEvidence) -> dict[str, str]:
    """Produce a revision-oriented preview goal grounded in completed job evidence.

    Used when the user asks to revise/update prior output based on the result.
    The goal explicitly names the revision intent so downstream preview handling
    and the operator can distinguish it from a generic follow-up.
    """
    summary = evidence.latest_summary or evidence.failure_summary or "the completed result"
    if evidence.state == "succeeded":
        goal = f"revise prior output based on completed evidence from {evidence.job_id}: {summary}"
    elif evidence.state == "failed":
        goal = f"revise prior output to address failure from {evidence.job_id}: {summary}"
    else:
        goal = (
            f"revise prior output based on evidence from "
            f"{evidence.job_id} (state: {evidence.state}): {summary}"
        )
    return {"goal": goal}


def draft_saveable_followup_preview(evidence: ReviewedJobEvidence) -> dict[str, object]:
    """Produce a saveable follow-up preview with a write_file payload.

    Used when the user asks to save the follow-up (e.g. "save the follow-up",
    "save the follow-up as a file"). Unlike the bare-goal follow-up, this
    creates a structured preview with a write_file entry so the preview pane
    shows a concrete saveable artifact.
    """
    summary = evidence.latest_summary or evidence.failure_summary or "the completed result"
    if evidence.state == "succeeded":
        goal = (
            f"save follow-up draft grounded in completed evidence from {evidence.job_id}: {summary}"
        )
        content = (
            f"# Follow-up: {evidence.job_id}\n\n"
            f"Prior job completed successfully.\n\n"
            f"**Result summary**: {summary}\n\n"
            f"## Proposed next step\n\n"
            f"(Operator: describe the follow-up action grounded in the above result.)\n"
        )
    elif evidence.state == "failed":
        goal = f"save corrective follow-up for failed job {evidence.job_id}: {summary}"
        content = (
            f"# Corrective follow-up: {evidence.job_id}\n\n"
            f"Prior job failed.\n\n"
            f"**Failure summary**: {summary}\n\n"
            f"## Proposed correction\n\n"
            f"(Operator: describe the corrective action to address the failure above.)\n"
        )
    else:
        goal = f"save follow-up draft for {evidence.job_id} (state: {evidence.state})"
        content = (
            f"# Follow-up: {evidence.job_id}\n\n"
            f"Prior job state: {evidence.state}.\n\n"
            f"**Summary**: {summary}\n\n"
            f"## Proposed next step\n\n"
            f"(Operator: describe the follow-up action.)\n"
        )
    job_stem = evidence.job_id.replace(".json", "")
    return {
        "goal": goal,
        "write_file": {
            "path": f"~/VoxeraOS/notes/followup-{job_stem}.md",
            "content": content,
            "mode": "overwrite",
        },
    }
