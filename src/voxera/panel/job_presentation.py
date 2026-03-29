from __future__ import annotations

from pathlib import Path
from typing import Any


def job_context_summary(
    primary: dict[str, Any],
    *,
    state_sidecar: dict[str, Any],
    approval: dict[str, Any],
    failed_sidecar: dict[str, Any],
    structured_execution: dict[str, Any],
) -> dict[str, str]:
    mission_id = str(primary.get("mission_id") or "")
    mission_title = str(primary.get("title") or "")
    goal = str(primary.get("goal") or primary.get("plan_goal") or "")
    payload_shape = "goal"
    if mission_id:
        payload_shape = "mission"
    elif primary:
        payload_shape = "custom"

    approval_status = str(
        structured_execution.get("approval_status")
        or state_sidecar.get("approval_status")
        or ("pending" if approval else "none")
        or "none"
    )
    blocked_reason = str(
        state_sidecar.get("blocked_reason")
        or failed_sidecar.get("error")
        or failed_sidecar.get("reason")
        or ""
    )
    failure_summary = str(
        structured_execution.get("latest_summary")
        or state_sidecar.get("failure_summary")
        or failed_sidecar.get("error")
        or failed_sidecar.get("message")
        or ""
    )
    recovery_reason = str(
        state_sidecar.get("recovery_reason")
        or state_sidecar.get("resume_reason")
        or failed_sidecar.get("recovery_reason")
        or ""
    )
    return {
        "mission_id": mission_id,
        "mission_title": mission_title,
        "goal": goal,
        "payload_shape": payload_shape,
        "approval_status": approval_status,
        "blocked_reason": blocked_reason,
        "failure_summary": failure_summary,
        "recovery_reason": recovery_reason,
    }


def job_artifact_inventory(
    *,
    artifacts_dir: Path,
    approval_path: Path | None,
    failed_sidecar_path: Path | None,
    state_sidecar_paths: list[Path],
    bucket: str,
) -> tuple[list[dict[str, str | bool]], list[str]]:
    rows: list[dict[str, str | bool]] = []

    def _add(
        key: str,
        label: str,
        path: Path,
        *,
        expected: bool,
        notes: str = "",
    ) -> None:
        rows.append(
            {
                "key": key,
                "label": label,
                "relative_path": path.name if path.parent == artifacts_dir else str(path),
                "present": path.exists(),
                "expected": expected,
                "contract_level": "required" if expected else "optional",
                "notes": notes,
            }
        )

    _add("plan", "plan.json", artifacts_dir / "plan.json", expected=False)
    _add(
        "assistant_response",
        "assistant_response.json",
        artifacts_dir / "assistant_response.json",
        expected=False,
        notes="present for assistant jobs",
    )
    _add("stdout", "stdout.txt", artifacts_dir / "stdout.txt", expected=False)
    _add("stderr", "stderr.txt", artifacts_dir / "stderr.txt", expected=False)
    _add("actions", "actions.jsonl", artifacts_dir / "actions.jsonl", expected=False)
    _add(
        "approval_sidecar",
        "approval artifact",
        approval_path or Path("pending/approvals/(missing).approval.json"),
        expected=bucket == "approvals" or approval_path is not None,
        notes="stored in pending/approvals/",
    )
    _add(
        "failed_sidecar",
        "failed sidecar",
        failed_sidecar_path or Path("failed/(missing).error.json"),
        expected=bucket == "failed" or failed_sidecar_path is not None,
        notes="stored in failed/",
    )

    state_match = next((item for item in state_sidecar_paths if item.exists()), None)
    _add(
        "state_sidecar",
        "state sidecar",
        state_match or state_sidecar_paths[0],
        expected=bucket in {"pending", "approvals", "done", "failed", "canceled"},
        notes="stored next to job file",
    )

    anomalies = [
        f"Expected artifact missing: {str(row['label'])}"
        for row in rows
        if bool(row["expected"]) and not bool(row["present"])
    ]
    return rows, anomalies


def job_recent_timeline(
    actions: list[dict[str, Any]], audit_timeline: list[dict[str, Any]]
) -> list[dict[str, str]]:
    timeline: list[dict[str, str]] = []
    for item in actions[:12]:
        timeline.append(
            {
                "source": "action",
                "event": str(item.get("event") or item.get("action") or "unknown"),
                "detail": str(item.get("status") or item.get("result") or ""),
            }
        )
    for item in audit_timeline[:12]:
        timeline.append(
            {
                "source": "audit",
                "event": str(item.get("event") or ""),
                "detail": str(item.get("step") or item.get("goal") or ""),
            }
        )
    return timeline[:20]


def operator_outcome_summary(
    *,
    bucket: str,
    execution: dict[str, Any],
    state_sidecar: dict[str, Any],
    job_context: dict[str, Any],
    has_approval: bool,
) -> dict[str, Any]:
    lifecycle_state = str(
        execution.get("lifecycle_state") or state_sidecar.get("lifecycle_state") or bucket
    ).strip()
    terminal_outcome = str(
        execution.get("terminal_outcome") or state_sidecar.get("terminal_outcome") or ""
    ).strip()
    approval_status = str(
        execution.get("approval_status") or job_context.get("approval_status") or "none"
    ).strip()
    blocked = bool(execution.get("blocked"))
    blocked_reason = str(
        execution.get("blocked_reason") or job_context.get("blocked_reason") or ""
    ).strip()
    blocked_reason_class = str(execution.get("blocked_reason_class") or "").strip()
    failure_summary = str(
        job_context.get("failure_summary") or execution.get("error") or ""
    ).strip()
    next_action_hint = str(execution.get("next_action_hint") or "").strip()
    retryable = execution.get("retryable") if isinstance(execution.get("retryable"), bool) else None

    key = "in_progress"
    label = "In progress"
    headline = "Job is still running."
    next_action = next_action_hint or "Wait for completion."
    severity = "info"
    next_action_source = "panel_default"

    if approval_status == "pending" or lifecycle_state in {"awaiting_approval", "pending_approval"}:
        key = "awaiting_approval"
        label = "Awaiting approval"
        headline = "Waiting for operator approval."
        next_action = next_action_hint or "Review the approval record and approve or deny."
        next_action_source = "execution.next_action_hint" if next_action_hint else "panel_default"
        severity = "warn"
    elif approval_status == "denied" or terminal_outcome == "denied":
        key = "denied"
        label = "Denied"
        headline = "Stopped because approval was denied."
        next_action = (
            next_action_hint or "Adjust scope or intent, then retry with a compliant request."
        )
        next_action_source = "execution.next_action_hint" if next_action_hint else "panel_default"
        severity = "danger"
    elif terminal_outcome == "succeeded" or lifecycle_state == "done" or bucket == "done":
        key = "succeeded"
        label = "Succeeded"
        headline = "Completed successfully."
        next_action = next_action_hint or "No operator action required."
        next_action_source = "execution.next_action_hint" if next_action_hint else "panel_default"
        severity = "success"
    elif terminal_outcome == "canceled" or lifecycle_state == "canceled" or bucket == "canceled":
        key = "canceled"
        label = "Canceled"
        headline = "Canceled before completion."
        next_action = next_action_hint or "Retry only if work is still needed."
        next_action_source = "execution.next_action_hint" if next_action_hint else "panel_default"
        severity = "warn"
    elif blocked and (
        blocked_reason_class
        in {"path_blocked_scope", "capability_boundary_mismatch", "policy_denied"}
        or "block" in blocked_reason.lower()
        or "scope" in blocked_reason.lower()
        or "policy" in blocked_reason.lower()
        or terminal_outcome == "blocked"
    ):
        key = "blocked_boundary"
        label = "Blocked by boundary/policy"
        headline = "Stopped by a control-plane boundary or policy restriction."
        next_action = next_action_hint or "Adjust scope/policy or run from an allowed boundary."
        next_action_source = "execution.next_action_hint" if next_action_hint else "panel_default"
        severity = "danger"
    elif terminal_outcome == "failed" or lifecycle_state == "failed" or bucket == "failed":
        if retryable is not False:
            key = "retryable_failure"
            label = "Failed (retryable)"
            headline = "Failed with an operator-fixable/runtime cause."
            next_action = next_action_hint or "Fix the reported cause, then retry."
            next_action_source = (
                "execution.next_action_hint" if next_action_hint else "panel_default"
            )
        else:
            key = "failed"
            label = "Failed"
            headline = "Failed with a terminal runtime error."
            next_action = next_action_hint or "Inspect failure details before retrying."
            next_action_source = (
                "execution.next_action_hint" if next_action_hint else "panel_default"
            )
        severity = "danger"
    elif next_action_hint:
        next_action_source = "execution.next_action_hint"

    reason = blocked_reason or failure_summary or str(execution.get("latest_summary") or "").strip()
    if key == "awaiting_approval" and has_approval:
        reason = reason or "Approval artifact is present in pending/approvals."

    return {
        "key": key,
        "label": label,
        "headline": headline,
        "reason": reason,
        "next_action": next_action,
        "next_action_source": next_action_source,
        "severity": severity,
        "lifecycle_state": lifecycle_state,
        "terminal_outcome": terminal_outcome or None,
        "approval_status": approval_status,
        "blocked_reason_class": blocked_reason_class or None,
        "retryable": retryable,
    }


def policy_rationale_rows(
    *,
    execution: dict[str, Any],
    state_sidecar: dict[str, Any],
    approval: dict[str, Any],
    has_approval: bool,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    approval_status = str(
        execution.get("approval_status") or state_sidecar.get("approval_status") or "none"
    ).strip()
    terminal_outcome = str(
        execution.get("terminal_outcome") or state_sidecar.get("terminal_outcome") or ""
    ).strip()
    blocked_reason_class = str(
        execution.get("blocked_reason_class") or state_sidecar.get("blocked_reason_class") or ""
    ).strip()
    blocked_reason = str(
        execution.get("blocked_reason") or state_sidecar.get("blocked_reason") or ""
    )
    execution_capabilities: dict[str, Any] = {}
    execution_capabilities_raw = execution.get("execution_capabilities")
    if isinstance(execution_capabilities_raw, dict):
        execution_capabilities = execution_capabilities_raw
    side_effect_class = str(execution_capabilities.get("side_effect_class") or "").strip()
    policy_reason = str(approval.get("policy_reason") or approval.get("reason") or "").strip()

    if approval_status == "pending" or has_approval:
        decision = "Approval required before execution can continue."
    elif (
        approval_status == "denied"
        or terminal_outcome == "denied"
        or blocked_reason_class == "policy_denied"
    ):
        decision = "Denied by policy/approval decision."
    elif blocked_reason_class in {"path_blocked_scope", "capability_boundary_mismatch"}:
        decision = "Blocked by runtime boundary constraints."
    elif blocked_reason_class == "policy_denied":
        decision = "Blocked by policy decision."
    elif terminal_outcome == "failed":
        decision = "Policy allowed execution; runtime failed afterward."
    elif terminal_outcome == "succeeded":
        decision = "Policy allowed execution and run completed."
    else:
        decision = "Policy decision inferred from current lifecycle state."
    rows.append({"label": "Decision", "value": decision})

    if policy_reason:
        rows.append({"label": "Policy reason", "value": policy_reason})
    if approval_status and approval_status != "none":
        rows.append({"label": "Approval status", "value": approval_status})
    if blocked_reason_class:
        rows.append({"label": "Blocked class", "value": blocked_reason_class})
    if blocked_reason.strip():
        rows.append({"label": "Blocked detail", "value": blocked_reason.strip()})

    required_caps_raw = execution_capabilities.get("required_capabilities")
    required_caps = (
        [str(item) for item in required_caps_raw if str(item).strip()]
        if isinstance(required_caps_raw, list)
        else []
    )
    if required_caps:
        rows.append({"label": "Required capabilities", "value": ", ".join(required_caps)})
    if side_effect_class:
        rows.append({"label": "Side-effect class", "value": side_effect_class})

    boundary_violation = execution_capabilities.get("runtime_boundary_violation")
    if isinstance(boundary_violation, dict):
        boundary = str(boundary_violation.get("boundary") or "").strip()
        detail = str(boundary_violation.get("detail") or "").strip()
        if boundary or detail:
            rows.append(
                {
                    "label": "Boundary context",
                    "value": f"{boundary}: {detail}" if boundary and detail else boundary or detail,
                }
            )

    return rows


def evidence_summary_rows(
    *,
    artifacts_dir: Path,
    approval_path: Path | None,
    failed_sidecar_path: Path | None,
    state_sidecar_paths: list[Path],
) -> list[dict[str, str | bool]]:
    rows: list[dict[str, str | bool]] = []

    def _add(path: Path, label: str, proof: str, *, required: bool = False) -> None:
        rows.append(
            {
                "label": label,
                "path": path.name if path.parent == artifacts_dir else str(path),
                "present": path.exists(),
                "required": required,
                "proof": proof,
            }
        )

    _add(
        artifacts_dir / "execution_result.json",
        "execution_result.json",
        "Proves final lifecycle/terminal outcome and structured evaluation fields.",
    )
    _add(
        artifacts_dir / "step_results.json",
        "step_results.json",
        "Proves per-step status, errors, retryability, and next action hints.",
    )
    _add(
        artifacts_dir / "execution_envelope.json",
        "execution_envelope.json",
        "Proves execution plan/envelope shape and run context.",
    )
    _add(
        artifacts_dir / "review_summary.json",
        "review_summary.json",
        "Proves expected/minimum artifact review and capability context.",
    )
    _add(
        artifacts_dir / "evidence_bundle.json",
        "evidence_bundle.json",
        "Bundles canonical trace + artifact refs for evidence review.",
    )
    if approval_path is not None:
        _add(
            approval_path,
            "approval artifact",
            "Proves approval gating details and policy rationale.",
        )
    if failed_sidecar_path is not None:
        _add(
            failed_sidecar_path,
            "failed sidecar",
            "Proves failure-sidecar error payload captured by queue daemon.",
        )
    state_match = next((item for item in state_sidecar_paths if item.exists()), None)
    if state_match is not None:
        _add(
            state_match,
            "state sidecar",
            "Proves persisted lifecycle/approval state seen by the queue.",
        )
    return rows


def why_stopped_rows(
    *,
    execution: dict[str, Any],
    state_sidecar: dict[str, Any],
    job_context: dict[str, Any],
) -> list[dict[str, str]]:
    candidates: list[tuple[str, str]] = [
        (
            "Lifecycle",
            str(execution.get("lifecycle_state") or state_sidecar.get("lifecycle_state") or ""),
        ),
        (
            "Terminal outcome",
            str(execution.get("terminal_outcome") or state_sidecar.get("terminal_outcome") or ""),
        ),
        (
            "Approval status",
            str(execution.get("approval_status") or job_context.get("approval_status") or ""),
        ),
        (
            "Blocked reason class",
            str(
                execution.get("blocked_reason_class")
                or state_sidecar.get("blocked_reason_class")
                or ""
            ),
        ),
        (
            "Blocked reason",
            str(execution.get("blocked_reason") or job_context.get("blocked_reason") or ""),
        ),
        (
            "Failure summary",
            str(job_context.get("failure_summary") or execution.get("error") or ""),
        ),
        ("Latest step summary", str(execution.get("latest_summary") or "")),
        ("Next action hint", str(execution.get("next_action_hint") or "")),
        ("Stop reason", str(execution.get("stop_reason") or "")),
    ]
    return [{"label": label, "value": value} for label, value in candidates if value.strip()]
