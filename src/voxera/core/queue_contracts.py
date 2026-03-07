from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .missions import MissionTemplate

EXECUTION_ENVELOPE_SCHEMA_VERSION = 1
STEP_RESULT_SCHEMA_VERSION = 1
EXECUTION_RESULT_SCHEMA_VERSION = 1


def detect_request_kind(payload: dict[str, Any]) -> str:
    intent = payload.get("job_intent")
    if isinstance(intent, dict):
        intent_kind = str(intent.get("request_kind") or "").strip()
        if intent_kind:
            return intent_kind
    kind = str(payload.get("kind") or "").strip()
    if kind:
        return kind
    if payload.get("mission_id") or payload.get("mission"):
        return "mission_id"
    if payload.get("goal") or payload.get("plan_goal"):
        return "goal"
    if payload.get("steps"):
        return "inline_steps"
    return "unknown"


def build_execution_envelope(
    *,
    job_ref: str,
    payload: dict[str, Any],
    mission: MissionTemplate,
    queue_root: Path,
    artifact_root: Path,
    normalized_mode: str,
) -> dict[str, Any]:
    return {
        "schema_version": EXECUTION_ENVELOPE_SCHEMA_VERSION,
        "envelope_kind": "queue_execution",
        "job": {
            "id": f"{Path(job_ref).stem}.json",
            "filename": Path(job_ref).name,
            "source_ref": job_ref,
            "request_kind": detect_request_kind(payload),
        },
        "execution": {
            "mode": normalized_mode,
            "total_steps": len(mission.steps),
            "steps": [
                {
                    "step_index": idx,
                    "total_steps": len(mission.steps),
                    "skill_id": step.skill_id,
                    "effective_args": step.args,
                }
                for idx, step in enumerate(mission.steps, start=1)
            ],
        },
        "request": {
            "mission_id": payload.get("mission_id"),
            "goal": payload.get("goal"),
            "title": payload.get("title"),
            "approval_required": payload.get("approval_required") is True,
            "job_intent": payload.get("job_intent")
            if isinstance(payload.get("job_intent"), dict)
            else None,
        },
        "mission": {
            "id": mission.id,
            "title": mission.title,
            "goal": mission.goal,
            "notes": mission.notes,
        },
        "queue": {
            "queue_root": str(queue_root),
            "artifacts_root": str(artifact_root),
            "job_artifacts": str(artifact_root / Path(job_ref).stem),
        },
        "generated_at_ms": int(time.time() * 1000),
    }


def build_structured_step_results(
    rr_data: dict[str, Any],
    *,
    total_steps: int,
    existing_results: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    raw_results_any = rr_data.get("results")
    raw_results: list[Any] = raw_results_any if isinstance(raw_results_any, list) else []
    raw_outcomes_any = rr_data.get("step_outcomes")
    raw_outcomes: list[Any] = raw_outcomes_any if isinstance(raw_outcomes_any, list) else []
    outcome_by_step: dict[int, dict[str, Any]] = {}
    for item in raw_outcomes:
        if not isinstance(item, dict):
            continue
        try:
            step_no = int(item.get("step") or 0)
        except (TypeError, ValueError):
            continue
        if step_no <= 0:
            continue
        outcome_by_step[step_no] = item

    structured: list[dict[str, Any]] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        step_index = int(item.get("step") or 0)
        if step_index <= 0:
            continue
        outcome = outcome_by_step.get(step_index, {})
        status = str(outcome.get("outcome") or ("succeeded" if item.get("ok") else "failed"))
        started_at_ms = int(item.get("started_at_ms") or 0) or None
        finished_at_ms = int(item.get("finished_at_ms") or 0) or None
        duration_ms = int(item.get("duration_ms") or 0) or None
        structured.append(
            {
                "schema_version": STEP_RESULT_SCHEMA_VERSION,
                "step_index": step_index,
                "step_total": int(total_steps),
                "skill_id": str(item.get("skill") or ""),
                "effective_args": item.get("args") if isinstance(item.get("args"), dict) else {},
                "status": status,
                "started_at_ms": started_at_ms,
                "finished_at_ms": finished_at_ms,
                "duration_ms": duration_ms,
                "summary": str(
                    item.get("summary") or item.get("output") or item.get("error") or ""
                ),
                "output_artifacts": item.get("output_artifacts")
                if isinstance(item.get("output_artifacts"), list)
                else [],
                "machine_payload": item.get("machine_payload")
                if isinstance(item.get("machine_payload"), dict)
                else {},
                "operator_note": item.get("operator_note"),
                "next_action_hint": item.get("next_action_hint"),
                "retryable": item.get("retryable")
                if isinstance(item.get("retryable"), bool)
                else None,
                "blocked": status == "blocked",
                "approval_status": outcome.get("approval_status"),
                "error": str(item.get("error") or "") or None,
                "error_class": item.get("error_class"),
            }
        )

    if structured:
        return structured
    if existing_results:
        return existing_results
    return []


def build_execution_result(
    *,
    job_ref: str,
    rr_data: dict[str, Any],
    step_results: list[dict[str, Any]],
    terminal_outcome: str,
    ok: bool,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": EXECUTION_RESULT_SCHEMA_VERSION,
        "job": Path(job_ref).name,
        "ok": ok,
        "terminal_outcome": terminal_outcome,
        "lifecycle_state": rr_data.get("lifecycle_state"),
        "current_step_index": rr_data.get("current_step_index"),
        "last_completed_step": rr_data.get("last_completed_step"),
        "last_attempted_step": rr_data.get("last_attempted_step"),
        "total_steps": rr_data.get("total_steps"),
        "approval_status": (
            "pending"
            if rr_data.get("status") == "pending_approval"
            else "approved"
            if terminal_outcome == "succeeded"
            else None
        ),
        "step_results": step_results,
        "error": error,
        "updated_at_ms": int(time.time() * 1000),
    }
