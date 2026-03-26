from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DEPENDENCY_ERROR_CLASSES = frozenset(
    {
        "dependency_missing",
        "missing_dependency",
        "missing_executable",
        "tool_not_found",
        "executable_not_found",
    }
)
_TERMINAL_OUTCOMES = frozenset({"succeeded", "failed", "blocked", "denied", "canceled"})


def _read_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _resolve_intent_route(
    *, artifacts_dir: Path, execution_result: dict[str, Any], state_payload: dict[str, Any]
) -> dict[str, Any] | None:
    execution_intent = execution_result.get("intent_route")
    if isinstance(execution_intent, dict):
        return execution_intent

    envelope = _read_json_dict(artifacts_dir / "execution_envelope.json")
    request_payload = envelope.get("request")
    if isinstance(request_payload, dict):
        simple_intent = request_payload.get("simple_intent")
        if isinstance(simple_intent, dict):
            return simple_intent

    plan_payload = _read_json_dict(artifacts_dir / "plan.json")
    plan_intent = plan_payload.get("intent_route")
    if isinstance(plan_intent, dict):
        return plan_intent

    for path in sorted(artifacts_dir.glob("plan.attempt-*.json"), reverse=True):
        attempt_payload = _read_json_dict(path)
        attempt_intent = attempt_payload.get("intent_route")
        if isinstance(attempt_intent, dict):
            return attempt_intent

    sidecar_intent = state_payload.get("intent_route")
    if isinstance(sidecar_intent, dict):
        return sidecar_intent

    return None


def _safe_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _sanitize_lineage_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _sanitize_lineage_int(value: Any, *, allow_zero: bool = True) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    minimum = 0 if allow_zero else 1
    return parsed if parsed >= minimum else None


def _normalize_lineage(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    parent_job_id = _sanitize_lineage_string(value.get("parent_job_id"))
    root_job_id = _sanitize_lineage_string(value.get("root_job_id"))
    orchestration_depth = _sanitize_lineage_int(value.get("orchestration_depth"))
    sequence_index = _sanitize_lineage_int(value.get("sequence_index"))
    lineage_role_raw = _sanitize_lineage_string(value.get("lineage_role"))
    lineage_role = (
        lineage_role_raw.lower()
        if lineage_role_raw and lineage_role_raw.lower() in {"root", "child"}
        else None
    )
    return {
        "parent_job_id": parent_job_id,
        "root_job_id": root_job_id,
        "orchestration_depth": orchestration_depth if orchestration_depth is not None else 0,
        "sequence_index": sequence_index,
        "lineage_role": lineage_role,
    }


def _resolve_lineage(
    *, artifacts_dir: Path, execution_result: dict[str, Any], state_payload: dict[str, Any]
) -> dict[str, Any] | None:
    direct = _normalize_lineage(execution_result.get("lineage"))
    if direct is not None:
        return direct

    envelope = _read_json_dict(artifacts_dir / "execution_envelope.json")
    envelope_job = envelope.get("job")
    if isinstance(envelope_job, dict):
        from_envelope = _normalize_lineage(envelope_job.get("lineage"))
        if from_envelope is not None:
            return from_envelope

    plan_payload = _read_json_dict(artifacts_dir / "plan.json")
    plan_lineage = _normalize_lineage(plan_payload.get("lineage"))
    if plan_lineage is not None:
        return plan_lineage

    state_job_payload = state_payload.get("payload")
    if isinstance(state_job_payload, dict):
        from_state_payload = _normalize_lineage(state_job_payload)
        if from_state_payload is not None:
            return from_state_payload

    return None


def _terminal_outcome_from_step_status(step_status: str) -> str:
    normalized = step_status.strip().lower()
    if normalized in {"succeeded", "failed", "blocked", "canceled"}:
        return normalized
    if normalized in {"pending_approval", "awaiting_approval"}:
        return "blocked"
    return ""


def _normalize_terminal_outcome(value: Any, *, approval_status: str) -> str:
    outcome = str(value or "").strip().lower()
    if outcome in {"awaiting_approval", "pending_approval"}:
        return "blocked"
    if approval_status == "denied" and not outcome:
        return "denied"
    if outcome in _TERMINAL_OUTCOMES:
        return outcome
    return ""


def _normalize_child_job_id(child_ref: dict[str, Any]) -> str | None:
    child_job_id = child_ref.get("child_job_id")
    if not isinstance(child_job_id, str):
        return None
    normalized = Path(child_job_id).name.strip()
    if not normalized:
        return None
    return normalized if normalized.endswith(".json") else f"{Path(normalized).stem}.json"


def _child_lookup(
    queue_root: Path, child_job_id: str
) -> tuple[str, Path, dict[str, Any], dict[str, Any]] | None:
    stem = Path(child_job_id).stem
    buckets: tuple[str, ...] = ("inbox", "pending", "done", "failed", "canceled")
    primary_path: Path | None = None
    bucket = ""
    for candidate_bucket in buckets:
        candidate = queue_root / candidate_bucket / child_job_id
        if candidate.exists():
            primary_path = candidate
            bucket = candidate_bucket
            break
    if primary_path is None:
        return None

    state_sidecar = {}
    for state_bucket in buckets:
        state_path = queue_root / state_bucket / f"{stem}.state.json"
        if not state_path.exists():
            continue
        state_sidecar = _read_json_dict(state_path)
        if state_sidecar:
            break

    approval = _read_json_dict(queue_root / "pending" / "approvals" / f"{stem}.approval.json")
    failed_sidecar = _read_json_dict(queue_root / "failed" / f"{stem}.error.json")
    return (
        bucket,
        queue_root / "artifacts" / stem,
        state_sidecar,
        {
            "approval": approval,
            "failed_sidecar": failed_sidecar,
        },
    )


def _classify_child_state(*, bucket: str, structured_execution: dict[str, Any]) -> str:
    approval_status = str(structured_execution.get("approval_status") or "")
    lifecycle_state = str(structured_execution.get("lifecycle_state") or "")
    terminal_outcome = str(structured_execution.get("terminal_outcome") or "")

    if approval_status == "pending" or lifecycle_state in {"awaiting_approval", "pending_approval"}:
        return "awaiting_approval"
    if terminal_outcome == "canceled" or lifecycle_state == "canceled" or bucket == "canceled":
        return "canceled"
    if (
        terminal_outcome in {"failed", "blocked"}
        or lifecycle_state == "failed"
        or bucket == "failed"
    ):
        return "failed"
    if terminal_outcome == "succeeded" or lifecycle_state == "done" or bucket == "done":
        return "done"
    if bucket in {"inbox", "pending", "approvals"}:
        return "pending"
    return "unknown"


def _resolve_child_summary(
    *, artifacts_dir: Path, child_refs: list[dict[str, Any]]
) -> dict[str, int]:
    summary: dict[str, int] = {
        "total": len(child_refs),
        "done": 0,
        "awaiting_approval": 0,
        "pending": 0,
        "failed": 0,
        "canceled": 0,
        "unknown": 0,
    }
    if not child_refs:
        return summary

    queue_root = artifacts_dir.parent.parent
    for child_ref in child_refs:
        child_job_id = _normalize_child_job_id(child_ref)
        if child_job_id is None:
            summary["unknown"] += 1
            continue
        resolved = _child_lookup(queue_root, child_job_id)
        if resolved is None:
            summary["unknown"] += 1
            continue
        bucket, child_artifacts_dir, state_sidecar, metadata = resolved
        structured = resolve_structured_execution(
            artifacts_dir=child_artifacts_dir,
            state_sidecar=state_sidecar,
            approval=metadata["approval"],
            failed_sidecar=metadata["failed_sidecar"],
        )
        summary[_classify_child_state(bucket=bucket, structured_execution=structured)] += 1
    return summary


def resolve_structured_execution(
    *,
    artifacts_dir: Path,
    state_sidecar: dict[str, Any] | None = None,
    approval: dict[str, Any] | None = None,
    failed_sidecar: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state_payload = state_sidecar if isinstance(state_sidecar, dict) else {}
    approval_payload = approval if isinstance(approval, dict) else {}
    failed_payload = failed_sidecar if isinstance(failed_sidecar, dict) else {}

    execution_result = _read_json_dict(artifacts_dir / "execution_result.json")
    step_results = [
        item for item in execution_result.get("step_results", []) if isinstance(item, dict)
    ]
    if not step_results:
        step_results = _read_json_list(artifacts_dir / "step_results.json")

    normalized_steps = sorted(
        step_results,
        key=lambda item: _safe_int(item.get("step_index")) or 0,
    )
    latest_step = normalized_steps[-1] if normalized_steps else {}

    last_attempted = (
        _safe_int(execution_result.get("last_attempted_step"))
        or _safe_int(state_payload.get("last_attempted_step"))
        or _safe_int(latest_step.get("step_index"))
        or _safe_int(state_payload.get("current_step_index"))
        or 0
    )
    last_completed = (
        _safe_int(execution_result.get("last_completed_step"))
        or _safe_int(state_payload.get("last_completed_step"))
        or max(
            [
                _safe_int(step.get("step_index")) or 0
                for step in normalized_steps
                if str(step.get("status") or "") == "succeeded"
            ],
            default=0,
        )
    )
    total_steps = (
        _safe_int(execution_result.get("total_steps"))
        or _safe_int(state_payload.get("total_steps"))
        or _safe_int(latest_step.get("step_total"))
        or 0
    )
    current_step = (
        _safe_int(execution_result.get("current_step_index"))
        or _safe_int(state_payload.get("current_step_index"))
        or last_attempted
        or 0
    )

    latest_summary = str(
        latest_step.get("summary")
        or latest_step.get("operator_note")
        or execution_result.get("review_summary", {}).get("latest_summary")
        or execution_result.get("latest_summary")
        or execution_result.get("error")
        or state_payload.get("failure_summary")
        or failed_payload.get("error")
        or failed_payload.get("message")
        or ""
    )
    latest_error_class = str(latest_step.get("error_class") or "").strip().lower()

    step_status = str(latest_step.get("status") or "")
    latest_blocked_reason_class = str(latest_step.get("blocked_reason_class") or "").strip().lower()
    terminal_outcome = _normalize_terminal_outcome(
        execution_result.get("terminal_outcome")
        or state_payload.get("terminal_outcome")
        or _terminal_outcome_from_step_status(step_status),
        approval_status=str(
            execution_result.get("approval_status")
            or latest_step.get("approval_status")
            or state_payload.get("approval_status")
            or ("pending" if approval_payload else "none")
        )
        .strip()
        .lower(),
    )
    lifecycle_state = str(
        execution_result.get("lifecycle_state") or state_payload.get("lifecycle_state") or ""
    )

    approval_status = (
        str(
            execution_result.get("approval_status")
            or latest_step.get("approval_status")
            or state_payload.get("approval_status")
            or ("pending" if approval_payload else "none")
        )
        .strip()
        .lower()
    )
    if approval_status not in {"none", "pending", "approved", "denied"}:
        approval_status = "none"
    if approval_status == "denied" and not terminal_outcome:
        terminal_outcome = "denied"
    blocked = bool(
        latest_step.get("blocked")
        or step_status == "blocked"
        or latest_error_class
        in {"path_blocked_scope", "capability_boundary_mismatch", "policy_denied"}
        or approval_status == "pending"
        or state_payload.get("lifecycle_state") in {"pending_approval", "blocked"}
    )
    blocked_reason_class = ""
    if blocked:
        blocked_reason_class = (
            latest_blocked_reason_class
            or str(execution_result.get("blocked_reason_class") or "").strip().lower()
            or str(state_payload.get("blocked_reason_class") or "").strip().lower()
            or latest_error_class
        )
    retryable: bool | None = (
        latest_step.get("retryable") if isinstance(latest_step.get("retryable"), bool) else None
    )
    if retryable is None and terminal_outcome == "failed":
        retryable = True

    raw_output_artifacts = latest_step.get("output_artifacts")
    output_artifacts: list[Any] = (
        raw_output_artifacts if isinstance(raw_output_artifacts, list) else []
    )
    machine_payload = (
        latest_step.get("machine_payload")
        if isinstance(latest_step.get("machine_payload"), dict)
        else {}
    )
    intent_route = _resolve_intent_route(
        artifacts_dir=artifacts_dir,
        execution_result=execution_result,
        state_payload=state_payload,
    )
    lineage = _resolve_lineage(
        artifacts_dir=artifacts_dir,
        execution_result=execution_result,
        state_payload=state_payload,
    )
    raw_child_refs = execution_result.get("child_refs")
    child_refs = (
        [item for item in raw_child_refs if isinstance(item, dict)]
        if isinstance(raw_child_refs, list)
        else []
    )
    child_summary = (
        _resolve_child_summary(artifacts_dir=artifacts_dir, child_refs=child_refs)
        if child_refs
        else None
    )
    review_summary: dict[str, Any] = {}
    review_summary_raw = execution_result.get("review_summary")
    if isinstance(review_summary_raw, dict):
        review_summary = review_summary_raw
    expected_artifact_status = str(review_summary.get("expected_artifact_status") or "")
    observed_expected_artifacts: list[Any] = []
    observed_expected_artifacts_raw = review_summary.get("observed_expected_artifacts")
    if isinstance(observed_expected_artifacts_raw, list):
        observed_expected_artifacts = observed_expected_artifacts_raw
    missing_expected_artifacts: list[Any] = []
    missing_expected_artifacts_raw = review_summary.get("missing_expected_artifacts")
    if isinstance(missing_expected_artifacts_raw, list):
        missing_expected_artifacts = missing_expected_artifacts_raw
    minimum_artifacts: dict[str, Any] | None = None
    minimum_artifacts_raw = review_summary.get("minimum_artifacts")
    if isinstance(minimum_artifacts_raw, dict):
        minimum_artifacts = minimum_artifacts_raw
    capability_boundary_violation = (
        review_summary.get("capability_boundary_violation")
        if isinstance(review_summary.get("capability_boundary_violation"), dict)
        else None
    )
    normalized_outcome_class = _classify_outcome(
        lifecycle_state=lifecycle_state,
        terminal_outcome=terminal_outcome,
        approval_status=approval_status,
        latest_error_class=latest_error_class,
        latest_error=str(latest_step.get("error") or execution_result.get("error") or ""),
        expected_artifact_status=expected_artifact_status,
        observed_expected_artifacts=observed_expected_artifacts,
        missing_expected_artifacts=missing_expected_artifacts,
        minimum_artifacts=minimum_artifacts,
        capability_boundary_violation=capability_boundary_violation,
        has_structured_sources=bool(execution_result or normalized_steps or state_payload),
    )

    return {
        "terminal_outcome": terminal_outcome,
        "lifecycle_state": lifecycle_state,
        "execution_lane": str(execution_result.get("execution_lane") or ""),
        "fast_lane": execution_result.get("fast_lane")
        if isinstance(execution_result.get("fast_lane"), dict)
        else None,
        "intent_route": intent_route,
        "lineage": lineage,
        "child_refs": child_refs,
        "child_summary": child_summary,
        "stop_reason": str(execution_result.get("stop_reason") or ""),
        "latest_summary": latest_summary,
        "last_attempted_step": int(last_attempted),
        "last_completed_step": int(last_completed),
        "current_step_index": int(current_step),
        "total_steps": int(total_steps),
        "approval_status": approval_status,
        "blocked": blocked,
        "blocked_reason_class": blocked_reason_class or None,
        "blocked_reason": (
            str(state_payload.get("blocked_reason") or "").strip()
            or str(execution_result.get("blocked_reason") or "").strip()
            or str(latest_step.get("error") or "").strip()
            or str(execution_result.get("error") or "").strip()
            or None
        ),
        "retryable": retryable,
        "operator_note": str(latest_step.get("operator_note") or ""),
        "next_action_hint": str(latest_step.get("next_action_hint") or ""),
        "output_artifacts": [str(item) for item in output_artifacts],
        "machine_payload": machine_payload,
        "normalized_outcome_class": normalized_outcome_class,
        "error": str(
            execution_result.get("error")
            or latest_step.get("error")
            or failed_payload.get("error")
            or ""
        ),
        "step_summaries": [
            {
                "step_index": _safe_int(step.get("step_index")) or 0,
                "skill_id": str(step.get("skill_id") or ""),
                "status": str(step.get("status") or ""),
                "summary": str(step.get("summary") or ""),
                "operator_note": str(step.get("operator_note") or ""),
                "next_action_hint": str(step.get("next_action_hint") or ""),
                "approval_status": str(step.get("approval_status") or ""),
                "blocked": bool(step.get("blocked")),
                "blocked_reason_class": str(step.get("blocked_reason_class") or "") or None,
                "retryable": step.get("retryable")
                if isinstance(step.get("retryable"), bool)
                else None,
                "output_artifacts": [
                    str(item)
                    for item in step.get("output_artifacts", [])
                    if isinstance(item, (str, int, float))
                ],
                "machine_payload": step.get("machine_payload")
                if isinstance(step.get("machine_payload"), dict)
                else {},
            }
            for step in normalized_steps
        ],
        "sources": {
            "execution_result": bool(execution_result),
            "step_results": bool(normalized_steps),
            "state_sidecar": bool(state_payload),
            "failed_sidecar": bool(failed_payload),
            "approval": bool(approval_payload),
        },
        "review_summary": review_summary if review_summary else None,
        "evidence_bundle": execution_result.get("evidence_bundle")
        if isinstance(execution_result.get("evidence_bundle"), dict)
        else None,
        "artifact_families": execution_result.get("artifact_families")
        if isinstance(execution_result.get("artifact_families"), list)
        else None,
        "artifact_refs": execution_result.get("artifact_refs")
        if isinstance(execution_result.get("artifact_refs"), list)
        else None,
        "execution_capabilities": execution_result.get("review_summary", {}).get(
            "execution_capabilities"
        )
        if isinstance(execution_result.get("review_summary"), dict)
        and isinstance(
            execution_result.get("review_summary", {}).get("execution_capabilities"), dict
        )
        else None,
        "expected_artifacts": execution_result.get("review_summary", {}).get("expected_artifacts")
        if isinstance(execution_result.get("review_summary"), dict)
        and isinstance(execution_result.get("review_summary", {}).get("expected_artifacts"), list)
        else None,
        "expected_artifact_status": expected_artifact_status,
        "observed_expected_artifacts": observed_expected_artifacts or None,
        "missing_expected_artifacts": missing_expected_artifacts or None,
        "minimum_artifacts": minimum_artifacts,
    }


def _classify_outcome(
    *,
    lifecycle_state: str,
    terminal_outcome: str,
    approval_status: str,
    latest_error_class: str,
    latest_error: str,
    expected_artifact_status: str,
    observed_expected_artifacts: list[Any],
    missing_expected_artifacts: list[Any],
    minimum_artifacts: dict[str, Any] | None,
    capability_boundary_violation: dict[str, Any] | None,
    has_structured_sources: bool,
) -> str:
    lifecycle = lifecycle_state.strip().lower()
    terminal = terminal_outcome.strip().lower()
    approval = approval_status.strip().lower()
    error_class = latest_error_class.strip().lower()
    error_text = latest_error.strip().lower()
    artifact_status = expected_artifact_status.strip().lower()

    if approval == "pending" or lifecycle in {"awaiting_approval", "pending_approval"}:
        return "approval_blocked"
    if approval == "denied" or error_class == "policy_denied" or "denied by policy" in error_text:
        return "policy_denied"
    if capability_boundary_violation is not None or error_class == "capability_boundary_mismatch":
        return "capability_boundary_mismatch"
    if error_class == "path_blocked_scope":
        return "path_blocked_scope"
    if terminal == "canceled" or lifecycle == "canceled":
        return "canceled"
    if terminal == "succeeded":
        if (
            isinstance(minimum_artifacts, dict)
            and str(minimum_artifacts.get("status")) == "missing"
        ):
            return "incomplete_evidence"
        if artifact_status == "partial" or (
            observed_expected_artifacts and missing_expected_artifacts
        ):
            return "partial_artifact_gap"
        if artifact_status == "missing" or missing_expected_artifacts:
            return "incomplete_evidence"
        return "succeeded"
    if terminal in {"failed", "blocked"} or lifecycle == "failed":
        if (
            error_class in _DEPENDENCY_ERROR_CLASSES
            or ("no such file or directory" in error_text and "executable" in error_text)
            or "command not found" in error_text
        ):
            return "runtime_dependency_missing"
        return "runtime_execution_failed"
    if (
        artifact_status == "missing"
        or missing_expected_artifacts
        or (
            isinstance(minimum_artifacts, dict)
            and str(minimum_artifacts.get("status") or "").strip().lower() == "missing"
        )
    ):
        return "incomplete_evidence"
    if artifact_status == "partial":
        return "partial_artifact_gap"
    if not has_structured_sources:
        return "incomplete_evidence"
    return "in_progress"
