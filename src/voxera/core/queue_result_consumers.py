from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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


def _safe_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _terminal_outcome_from_step_status(step_status: str) -> str:
    normalized = step_status.strip().lower()
    if normalized in {"succeeded", "failed", "blocked", "canceled"}:
        return normalized
    if normalized in {"pending_approval", "awaiting_approval"}:
        return "blocked"
    return ""


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
        or execution_result.get("error")
        or state_payload.get("failure_summary")
        or failed_payload.get("error")
        or failed_payload.get("message")
        or ""
    )

    step_status = str(latest_step.get("status") or "")
    terminal_outcome = str(
        execution_result.get("terminal_outcome")
        or state_payload.get("terminal_outcome")
        or _terminal_outcome_from_step_status(step_status)
    )
    lifecycle_state = str(
        execution_result.get("lifecycle_state") or state_payload.get("lifecycle_state") or ""
    )

    approval_status = str(
        execution_result.get("approval_status")
        or latest_step.get("approval_status")
        or state_payload.get("approval_status")
        or ("pending" if approval_payload else "none")
    )
    blocked = bool(
        latest_step.get("blocked")
        or step_status == "blocked"
        or approval_status == "pending"
        or state_payload.get("lifecycle_state") in {"pending_approval", "blocked"}
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

    return {
        "terminal_outcome": terminal_outcome,
        "lifecycle_state": lifecycle_state,
        "latest_summary": latest_summary,
        "last_attempted_step": int(last_attempted),
        "last_completed_step": int(last_completed),
        "current_step_index": int(current_step),
        "total_steps": int(total_steps),
        "approval_status": approval_status,
        "blocked": blocked,
        "retryable": retryable,
        "operator_note": str(latest_step.get("operator_note") or ""),
        "next_action_hint": str(latest_step.get("next_action_hint") or ""),
        "output_artifacts": [str(item) for item in output_artifacts],
        "machine_payload": machine_payload,
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
    }
