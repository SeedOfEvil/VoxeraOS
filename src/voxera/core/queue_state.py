from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .queue_object_model import COMPLETED_AT_LIFECYCLE_STATES, QueueLifecycleState

JOB_STATE_SCHEMA_VERSION = 1


def job_state_sidecar_path(
    job_ref: str,
    *,
    inbox: Path,
    pending: Path,
    done: Path,
    failed: Path,
    canceled: Path,
) -> Path:
    stem = Path(job_ref).stem
    bucket_dirs = (inbox, pending, done, failed, canceled)
    for bucket in bucket_dirs:
        candidate = bucket / f"{stem}.state.json"
        if candidate.exists():
            return candidate
    job_path = Path(job_ref)
    if job_path.parent in set(bucket_dirs):
        return job_path.with_name(f"{stem}.state.json")
    return pending / f"{stem}.state.json"


def read_job_state(
    job_ref: str,
    *,
    inbox: Path,
    pending: Path,
    done: Path,
    failed: Path,
    canceled: Path,
) -> dict[str, Any]:
    path = job_state_sidecar_path(
        job_ref,
        inbox=inbox,
        pending=pending,
        done=done,
        failed=failed,
        canceled=canceled,
    )
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def write_job_state(
    job_ref: str,
    payload: dict[str, Any],
    *,
    inbox: Path,
    pending: Path,
    done: Path,
    failed: Path,
    canceled: Path,
    write_text_atomic: Callable[[Path, str], None],
) -> None:
    path = job_state_sidecar_path(
        job_ref,
        inbox=inbox,
        pending=pending,
        done=done,
        failed=failed,
        canceled=canceled,
    )
    write_text_atomic(path, json.dumps(payload, indent=2))


def update_job_state_snapshot(
    job_ref: str,
    *,
    lifecycle_state: QueueLifecycleState | str,
    current: dict[str, Any],
    now_ms: int,
    payload: dict[str, Any] | None = None,
    mission: Any | None = None,
    rr_data: dict[str, Any] | None = None,
    terminal_outcome: str | None = None,
    failure_summary: str | None = None,
    blocked_reason: str | None = None,
    approval_status: str | None = None,
) -> dict[str, Any]:
    """Build a queue-owned lifecycle snapshot for a submitted job.

    Queue state sidecars are the authoritative submitted lifecycle surface.
    Runtime outcome truth is later grounded by artifacts/evidence attached to
    this same job id.
    """
    started_at_ms = int(current.get("started_at_ms") or now_ms)
    raw_transitions = current.get("transitions")
    transitions: dict[str, Any] = dict(raw_transitions) if isinstance(raw_transitions, dict) else {}
    transitions[lifecycle_state] = now_ms

    mission_payload = (
        {
            "mission_id": mission.id,
            "title": mission.title,
            "goal": mission.goal,
        }
        if mission is not None
        else current.get("mission")
        if isinstance(current.get("mission"), dict)
        else {}
    )
    total_steps = (
        len(mission.steps) if mission is not None else int(current.get("total_steps") or 0)
    )

    rr = rr_data if isinstance(rr_data, dict) else {}
    current_step = int(rr.get("current_step_index") or current.get("current_step_index") or 0)
    last_completed = int(rr.get("last_completed_step") or current.get("last_completed_step") or 0)
    last_attempted = int(rr.get("last_attempted_step") or current.get("last_attempted_step") or 0)
    step_outcomes = (
        rr.get("step_outcomes")
        if isinstance(rr.get("step_outcomes"), list)
        else current.get("step_outcomes")
        if isinstance(current.get("step_outcomes"), list)
        else []
    )
    resolved_terminal = (
        terminal_outcome
        if terminal_outcome is not None
        else rr.get("terminal_outcome") or current.get("terminal_outcome")
    )
    if lifecycle_state == "done" and not resolved_terminal:
        resolved_terminal = "succeeded"

    snapshot: dict[str, Any] = {
        "schema_version": JOB_STATE_SCHEMA_VERSION,
        "job_id": f"{Path(job_ref).stem}.json",
        "lifecycle_state": lifecycle_state,
        "current_step_index": current_step,
        "total_steps": total_steps,
        "last_completed_step": last_completed,
        "last_attempted_step": last_attempted,
        "terminal_outcome": resolved_terminal,
        "failure_summary": failure_summary
        if failure_summary is not None
        else current.get("failure_summary"),
        "blocked_reason": blocked_reason
        if blocked_reason is not None
        else current.get("blocked_reason"),
        "approval_status": approval_status
        if approval_status is not None
        else current.get("approval_status"),
        "mission": mission_payload,
        "step_outcomes": step_outcomes,
        "started_at_ms": started_at_ms,
        "updated_at_ms": now_ms,
        "completed_at_ms": now_ms if lifecycle_state in COMPLETED_AT_LIFECYCLE_STATES else None,
        "transitions": transitions,
    }
    if payload is not None:
        snapshot["payload"] = payload
    elif isinstance(current.get("payload"), dict):
        snapshot["payload"] = current["payload"]

    return snapshot
