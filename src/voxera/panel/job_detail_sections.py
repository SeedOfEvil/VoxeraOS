"""Panel job-detail shaping cluster.

This module owns the job-detail payload-building surface: the big
``build_job_detail_payload`` / ``build_job_progress_payload`` builders
that power ``GET /jobs/{job_id}`` and ``GET /jobs/{job_id}/progress``,
plus the narrow ``build_job_detail_sections`` composition helper that
bundles the job-detail section slices (context, operator summary,
policy rationale, evidence, why-stopped, timeline) from
``job_presentation.py``.

It was extracted from ``panel/app.py`` as PR D — the fourth small,
behavior-preserving step of decomposing that composition root — so the
panel composition root stays visible while the job-detail shaping logic
lives in a clean, dedicated builder surface. This separation is the
prerequisite for a future shared session-context / ``vera_context``
block in the panel job-detail surface.

``panel/app.py`` remains the composition root: it still defines the
FastAPI app, registers routes, and owns the shared ``_settings`` /
``_queue_root`` / ``_now_ms`` wrappers, plus the thin
``_job_detail_payload`` / ``_job_progress_payload`` /
``_job_artifact_flags`` wrappers that ``routes_jobs`` reaches back for.
This module takes ``queue_root`` as an explicit positional argument on
every entry point (matching PR B's ``queue_mutation_bridge`` and PR C's
``security_health_helpers``), so there is no hidden module-level state
and no import of ``panel.app``.

Semantics preserved exactly:

* Queue-truth precedence — ``lifecycle_state`` / ``terminal_outcome`` /
  ``current_step_index`` / etc. prefer the structured execution artifact
  (``resolve_structured_execution``) over the state sidecar, and the
  state sidecar over the raw job payload, exactly as before.
* Approval-status inference — ``execution.approval_status`` wins over
  ``job_context.approval_status`` wins over ``"pending" if approval
  else "none"``.
* Missing / malformed / partial data — every ``isinstance(... , dict)``
  / ``isinstance(... , list)`` guard is preserved byte-for-byte so
  partial artifacts keep shaping the same payload fields to the same
  fallback values.
* Terminal-outcome filtering of ``recent_timeline`` — success-terminal
  jobs still drop ``queue_job_failed`` / ``assistant_advisory_failed``
  events from the progress timeline; failed-terminal jobs still drop
  ``queue_job_done`` / ``assistant_job_done`` events.
* Lineage precedence — ``structured_execution.lineage`` wins over
  ``_payload_lineage(primary)`` wins over
  ``_payload_lineage(state_sidecar.payload)``.
* 404 semantics — ``build_job_detail_payload`` raises
  ``HTTPException(404, "job not found")`` when ``lookup_job`` returns
  ``None`` AND the artifacts directory does not exist, matching the
  original in-app behavior.

Shared-session ``vera_context`` block (read-only, supplemental):

* ``build_job_detail_payload`` optionally attaches a ``vera_context``
  dict to the job-detail payload when the job belongs to a Vera session
  that has a usable shared context (see
  ``voxera.vera.session_store.read_session_context``). This is a
  continuity aid only — canonical queue / artifact truth remains
  primary, and the panel is strictly read-only with respect to the
  shared context surface. The lookup is fail-soft: missing session
  directory, missing session file, malformed session payload, empty
  context, or no owning session all produce ``vera_context: None``
  without raising. Wrong-session isolation is enforced by matching the
  job filename against each session's
  ``linked_queue_jobs.tracked[].job_ref`` — context from a session that
  did not submit this job never leaks into the payload.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from ..audit import tail
from ..core.queue_inspect import lookup_job, queue_snapshot
from ..core.queue_result_consumers import resolve_structured_execution
from ..vera.session_store import read_session_context
from .job_presentation import (
    evidence_summary_rows,
    job_artifact_inventory,
    job_context_summary,
    job_recent_timeline,
    operator_outcome_summary,
    policy_rationale_rows,
    why_stopped_rows,
)

__all__ = [
    "build_job_detail_payload",
    "build_job_detail_sections",
    "build_job_progress_payload",
]


def _artifact_text(path: Path, *, max_chars: int = 8000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[:max_chars] + ("\n...[truncated]..." if len(text) > max_chars else "")


def _safe_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_actions(path: Path, *, limit: int = 200) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    actions: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except Exception:
            event = {"raw": line}
        if isinstance(event, dict):
            actions.append(event)
    return list(reversed(actions[-limit:]))


def _read_generated_files(artifacts_dir: Path) -> list[str]:
    generated = artifacts_dir / "outputs" / "generated_files.json"
    if not generated.exists():
        return []
    try:
        payload = json.loads(generated.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    return [str(item) for item in payload] if isinstance(payload, list) else []


def _find_vera_session_id_for_job(queue_root: Path, job_name: str) -> str | None:
    """Return the Vera session id that tracks ``job_name``, if any.

    Fail-soft, read-only scan of ``queue_root/artifacts/vera_sessions/*.json``
    for a session whose ``linked_queue_jobs.tracked[].job_ref`` matches the
    job filename. Returns ``None`` when no session tracks the job, when the
    sessions directory does not exist, or when any session file is
    unreadable / malformed. Never raises. Never writes.

    The panel must only ever *read* shared session context, so this lookup
    strictly isolates the job-to-session binding: only a session that has
    explicitly registered this job via ``register_session_linked_job`` is
    a valid match. A context from any other session must not leak into
    the job-detail payload.
    """

    needle = Path(job_name).name.strip()
    if not needle:
        return None
    sessions_dir = queue_root / "artifacts" / "vera_sessions"
    if not sessions_dir.exists() or not sessions_dir.is_dir():
        return None
    try:
        session_files = sorted(sessions_dir.glob("*.json"))
    except OSError:
        return None
    for session_file in session_files:
        if not session_file.is_file():
            continue
        payload = _safe_json(session_file)
        if not payload:
            continue
        registry = payload.get("linked_queue_jobs")
        if not isinstance(registry, dict):
            continue
        tracked = registry.get("tracked")
        if not isinstance(tracked, list):
            continue
        for item in tracked:
            if not isinstance(item, dict):
                continue
            job_ref = str(item.get("job_ref") or "").strip()
            if job_ref == needle:
                session_id = str(payload.get("session_id") or session_file.stem).strip()
                return session_id or None
    return None


def _build_vera_context(
    queue_root: Path,
    job_name: str,
    *,
    state_sidecar: dict[str, Any],
) -> dict[str, Any] | None:
    """Shape the optional read-only ``vera_context`` block for job detail.

    Returns ``None`` when there is no owning Vera session, when the
    session has no shared context yet, or when the stored context is the
    canonical empty shape. The panel is strictly read-only w.r.t. shared
    session context — this helper never writes, and a missing or wrong
    session must not leak any other session's context.

    Staleness is computed conservatively against the state-sidecar
    ``completed_at_ms`` terminal timestamp:

    * ``is_stale`` is ``True`` when the context was last updated strictly
      before the job's terminal completion time (the context has not
      caught up to the job's terminal outcome);
    * ``is_stale`` is ``False`` when the context's ``updated_at_ms`` is
      at or after the terminal time;
    * ``is_stale`` is ``None`` when there is not enough data to judge
      safely — for example the job has not reached a terminal state yet,
      or the context has no ``updated_at_ms`` stamp. We deliberately do
      not invent a timestamp or guess.
    """

    session_id = _find_vera_session_id_for_job(queue_root, job_name)
    if not session_id:
        return None
    try:
        context = read_session_context(queue_root, session_id)
    except Exception:
        return None
    active_topic = context.get("active_topic")
    active_draft_ref = context.get("active_draft_ref")
    updated_at_ms_raw = context.get("updated_at_ms")
    updated_at_ms = int(updated_at_ms_raw) if isinstance(updated_at_ms_raw, int) else 0
    has_usable_signal = bool(active_topic) or bool(active_draft_ref) or updated_at_ms > 0
    if not has_usable_signal:
        return None

    terminal_raw = state_sidecar.get("completed_at_ms") if isinstance(state_sidecar, dict) else None
    terminal_at_ms = int(terminal_raw) if isinstance(terminal_raw, int) and terminal_raw > 0 else 0

    if terminal_at_ms > 0 and updated_at_ms > 0:
        is_stale: bool | None = updated_at_ms < terminal_at_ms
    else:
        is_stale = None

    return {
        "session_id": session_id,
        "active_topic": active_topic if isinstance(active_topic, str) and active_topic else None,
        "active_draft_ref": active_draft_ref
        if isinstance(active_draft_ref, str) and active_draft_ref
        else None,
        "updated_at_ms": updated_at_ms,
        "is_stale": is_stale,
    }


def _payload_lineage(payload: dict[str, Any]) -> dict[str, Any] | None:
    lineage_keys = (
        "parent_job_id",
        "root_job_id",
        "orchestration_depth",
        "sequence_index",
        "lineage_role",
    )
    if not any(key in payload for key in lineage_keys):
        return None

    def _clean_str(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        return cleaned or None

    def _clean_int(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    role_raw = _clean_str(payload.get("lineage_role"))
    role = role_raw.lower() if role_raw and role_raw.lower() in {"root", "child"} else None
    depth = _clean_int(payload.get("orchestration_depth"))
    return {
        "parent_job_id": _clean_str(payload.get("parent_job_id")),
        "root_job_id": _clean_str(payload.get("root_job_id")),
        "orchestration_depth": depth if depth is not None else 0,
        "sequence_index": _clean_int(payload.get("sequence_index")),
        "lineage_role": role,
    }


def build_job_detail_sections(
    *,
    primary: dict[str, Any],
    state_sidecar: dict[str, Any],
    approval: dict[str, Any],
    failed_sidecar: dict[str, Any],
    structured_execution: dict[str, Any],
    artifacts_dir: Path,
    approval_path: Path | None,
    failed_sidecar_path: Path | None,
    state_sidecar_paths: list[Path],
    bucket: str,
    actions: list[dict[str, Any]],
    audit_timeline: list[dict[str, Any]],
) -> dict[str, Any]:
    has_approval = bool(approval)
    context_summary = job_context_summary(
        primary,
        state_sidecar=state_sidecar,
        approval=approval,
        failed_sidecar=failed_sidecar,
        structured_execution=structured_execution,
    )
    return {
        "job_context": context_summary,
        "operator_summary": operator_outcome_summary(
            bucket=bucket,
            execution=structured_execution,
            state_sidecar=state_sidecar,
            job_context=context_summary,
            has_approval=has_approval,
        ),
        "policy_rationale": policy_rationale_rows(
            execution=structured_execution,
            state_sidecar=state_sidecar,
            approval=approval,
            has_approval=has_approval,
        ),
        "evidence_summary": evidence_summary_rows(
            artifacts_dir=artifacts_dir,
            approval_path=approval_path,
            failed_sidecar_path=failed_sidecar_path,
            state_sidecar_paths=state_sidecar_paths,
        ),
        "why_stopped": why_stopped_rows(
            execution=structured_execution,
            state_sidecar=state_sidecar,
            job_context=context_summary,
        ),
        "recent_timeline": job_recent_timeline(actions, audit_timeline),
    }


def build_job_detail_payload(queue_root: Path, job_id: str) -> dict[str, Any]:
    """Build the full job-detail payload for ``GET /jobs/{job_id}``.

    Mirrors the original ``panel.app._job_detail_payload`` byte-for-byte:
    resolves the job via ``lookup_job`` (falling back to an
    artifacts-only lookup when the job file has been aged out), loads
    the primary / state / approval / failed-sidecar JSON, computes the
    artifact inventory + anomalies, resolves the structured execution
    artifact, builds the detail sections, derives the lineage, and
    returns the complete payload dict with queue-truth precedence
    preserved exactly.
    """

    lookup = lookup_job(queue_root, job_id)
    if lookup is None:
        stem = Path(job_id).stem
        artifacts_dir = queue_root / "artifacts" / stem
        if not artifacts_dir.exists():
            raise HTTPException(status_code=404, detail="job not found")
        primary: dict[str, Any] = {}
        approval: dict[str, Any] = {}
        failed_sidecar: dict[str, Any] = {}
        bucket = "unknown"
        job_name = f"{stem}.json"
    else:
        primary = _safe_json(lookup.primary_path)
        approval = _safe_json(lookup.approval_path) if lookup.approval_path else {}
        failed_sidecar = (
            _safe_json(lookup.failed_sidecar_path) if lookup.failed_sidecar_path else {}
        )
        artifacts_dir = lookup.artifacts_dir
        bucket = lookup.bucket
        job_name = lookup.job_id
        approval_path = lookup.approval_path
        failed_sidecar_path = lookup.failed_sidecar_path

    if lookup is None:
        approval_path = (
            queue_root / "pending" / "approvals" / f"{Path(job_name).stem}.approval.json"
        )
        failed_sidecar_path = queue_root / "failed" / f"{Path(job_name).stem}.error.json"

    state_sidecar: dict[str, Any] = {}
    stem = Path(job_name).stem
    state_candidates = [
        queue_root / bucket / f"{stem}.state.json"
        for bucket in ("pending", "inbox", "done", "failed", "canceled")
    ]
    for state_path in state_candidates:
        if not state_path.exists():
            continue
        loaded = _safe_json(state_path)
        if loaded:
            state_sidecar = loaded
            break

    artifact_files = (
        [
            child.relative_to(artifacts_dir).as_posix()
            for child in sorted(artifacts_dir.rglob("*"))
            if child.is_file()
        ]
        if artifacts_dir.exists()
        else []
    )

    snapshot = queue_snapshot(queue_root)
    relevant_events = [
        item
        for item in reversed(tail(200))
        if job_name in str(item.get("job", ""))
        or item.get("event") in {"queue_job_failed", "queue_job_done"}
    ]
    actions = _load_actions(artifacts_dir / "actions.jsonl")
    artifact_inventory, artifact_anomalies = job_artifact_inventory(
        artifacts_dir=artifacts_dir,
        approval_path=approval_path if approval_path and approval_path.exists() else None,
        failed_sidecar_path=failed_sidecar_path
        if failed_sidecar_path and failed_sidecar_path.exists()
        else None,
        state_sidecar_paths=state_candidates,
        bucket=bucket,
    )
    structured_execution = resolve_structured_execution(
        artifacts_dir=artifacts_dir,
        state_sidecar=state_sidecar,
        approval=approval,
        failed_sidecar=failed_sidecar,
    )
    audit_timeline = relevant_events[:40]
    detail_sections = build_job_detail_sections(
        primary=primary,
        state_sidecar=state_sidecar,
        approval=approval,
        failed_sidecar=failed_sidecar,
        structured_execution=structured_execution,
        artifacts_dir=artifacts_dir,
        approval_path=approval_path if approval_path and approval_path.exists() else None,
        failed_sidecar_path=failed_sidecar_path
        if failed_sidecar_path and failed_sidecar_path.exists()
        else None,
        state_sidecar_paths=state_candidates,
        bucket=bucket,
        actions=actions,
        audit_timeline=audit_timeline,
    )
    lineage = (
        structured_execution.get("lineage")
        if isinstance(structured_execution.get("lineage"), dict)
        else None
    )
    if lineage is None:
        lineage = _payload_lineage(primary)
    state_job_payload = state_sidecar.get("payload")
    if lineage is None and isinstance(state_job_payload, dict):
        lineage = _payload_lineage(state_job_payload)
    vera_context = _build_vera_context(
        queue_root,
        job_name,
        state_sidecar=state_sidecar,
    )
    return {
        "job_id": job_name,
        "bucket": bucket,
        "job": primary,
        "approval": approval,
        "state": state_sidecar,
        "failed_sidecar": failed_sidecar,
        "lock": snapshot.get("lock_status", {}),
        "paused": snapshot.get("paused", False),
        "plan": _safe_json(artifacts_dir / "plan.json"),
        "actions": actions,
        "stdout": _artifact_text(artifacts_dir / "stdout.txt", max_chars=64 * 1024),
        "stderr": _artifact_text(artifacts_dir / "stderr.txt", max_chars=64 * 1024),
        "generated_files": _read_generated_files(artifacts_dir),
        "artifact_files": artifact_files,
        "artifact_inventory": artifact_inventory,
        "artifact_anomalies": artifact_anomalies,
        "job_context": detail_sections["job_context"],
        "lineage": lineage,
        "child_refs": structured_execution.get("child_refs")
        if isinstance(structured_execution.get("child_refs"), list)
        else [],
        "child_summary": structured_execution.get("child_summary")
        if isinstance(structured_execution.get("child_summary"), dict)
        else None,
        "execution": structured_execution,
        "operator_summary": detail_sections["operator_summary"],
        "policy_rationale": detail_sections["policy_rationale"],
        "evidence_summary": detail_sections["evidence_summary"],
        "why_stopped": detail_sections["why_stopped"],
        "recent_timeline": detail_sections["recent_timeline"],
        "artifacts_dir": str(artifacts_dir),
        "audit_timeline": audit_timeline,
        "has_approval": bool(approval),
        "can_cancel": bucket in {"inbox", "pending", "approvals"},
        "can_retry": bucket in {"failed", "canceled"},
        "can_delete": bucket in {"done", "failed", "canceled"},
        "vera_context": vera_context,
    }


def build_job_progress_payload(queue_root: Path, job_id: str) -> dict[str, Any]:
    """Build the progress payload for ``GET /jobs/{job_id}/progress``.

    Mirrors the original ``panel.app._job_progress_payload`` byte-for-byte:
    first resolves the full detail payload via ``build_job_detail_payload``,
    then shapes the progress subset with queue-truth precedence
    (``execution.*`` > ``state_sidecar.*`` > ``bucket``), terminal-outcome
    filtering of the recent timeline, operator outcome summary, lineage
    passthrough, and the minimum-artifacts review sub-dict.
    """

    payload = build_job_detail_payload(queue_root, job_id)

    execution_raw = payload.get("execution")
    execution: dict[str, Any] = execution_raw if isinstance(execution_raw, dict) else {}

    job_context_raw = payload.get("job_context")
    job_context: dict[str, Any] = job_context_raw if isinstance(job_context_raw, dict) else {}

    state_raw = payload.get("state")
    state_payload: dict[str, Any] = state_raw if isinstance(state_raw, dict) else {}

    approval_raw = payload.get("approval")
    approval: dict[str, Any] = approval_raw if isinstance(approval_raw, dict) else {}

    timeline_raw = payload.get("recent_timeline")
    timeline: list[Any] = timeline_raw if isinstance(timeline_raw, list) else []

    lifecycle_state = str(
        execution.get("lifecycle_state")
        or state_payload.get("lifecycle_state")
        or payload.get("bucket")
        or "unknown"
    )
    terminal_outcome = str(
        execution.get("terminal_outcome") or state_payload.get("terminal_outcome") or ""
    )
    bucket = str(payload.get("bucket") or "unknown")

    is_success_terminal = (
        terminal_outcome == "succeeded" or lifecycle_state == "done" or bucket == "done"
    )
    is_failed_terminal = terminal_outcome in {"failed", "blocked", "canceled"} or bucket in {
        "failed",
        "canceled",
    }

    raw_failure_summary = str(job_context.get("failure_summary") or execution.get("error") or "")
    failure_summary: str | None = (
        raw_failure_summary if is_failed_terminal and raw_failure_summary else None
    )

    raw_stop_reason = str(execution.get("stop_reason") or "")
    stop_reason: str | None = raw_stop_reason if is_failed_terminal and raw_stop_reason else None

    filtered_timeline: list[Any] = []
    for item in timeline:
        if not isinstance(item, dict):
            continue
        event_name = str(item.get("event") or "")
        if is_success_terminal and event_name in {"queue_job_failed", "assistant_advisory_failed"}:
            continue
        if is_failed_terminal and event_name in {"queue_job_done", "assistant_job_done"}:
            continue
        filtered_timeline.append(item)

    fast_lane_raw = execution.get("fast_lane")
    intent_route_raw = execution.get("intent_route")
    review_summary_raw = execution.get("review_summary")
    review_summary = review_summary_raw if isinstance(review_summary_raw, dict) else {}
    minimum_artifacts_raw = review_summary.get("minimum_artifacts")
    minimum_artifacts = minimum_artifacts_raw if isinstance(minimum_artifacts_raw, dict) else None
    operator_summary = operator_outcome_summary(
        bucket=bucket,
        execution=execution,
        state_sidecar=state_payload,
        job_context=job_context,
        has_approval=bool(approval),
    )

    return {
        "ok": True,
        "job_id": payload.get("job_id") or f"{Path(job_id).stem}.json",
        "bucket": bucket,
        "lifecycle_state": lifecycle_state,
        "terminal_outcome": terminal_outcome,
        "current_step_index": int(
            execution.get("current_step_index") or state_payload.get("current_step_index") or 0
        ),
        "total_steps": int(execution.get("total_steps") or state_payload.get("total_steps") or 0),
        "last_attempted_step": int(
            execution.get("last_attempted_step") or state_payload.get("last_attempted_step") or 0
        ),
        "last_completed_step": int(
            execution.get("last_completed_step") or state_payload.get("last_completed_step") or 0
        ),
        "approval_status": str(
            execution.get("approval_status")
            or job_context.get("approval_status")
            or ("pending" if approval else "none")
        ),
        "execution_lane": str(execution.get("execution_lane") or ""),
        "fast_lane": fast_lane_raw if isinstance(fast_lane_raw, dict) else None,
        "intent_route": intent_route_raw if isinstance(intent_route_raw, dict) else None,
        "lineage": payload.get("lineage") if isinstance(payload.get("lineage"), dict) else None,
        "child_refs": payload.get("child_refs")
        if isinstance(payload.get("child_refs"), list)
        else [],
        "child_summary": payload.get("child_summary")
        if isinstance(payload.get("child_summary"), dict)
        else None,
        "parent_job_id": (
            payload.get("lineage", {}).get("parent_job_id")
            if isinstance(payload.get("lineage"), dict)
            else None
        ),
        "root_job_id": (
            payload.get("lineage", {}).get("root_job_id")
            if isinstance(payload.get("lineage"), dict)
            else None
        ),
        "orchestration_depth": (
            payload.get("lineage", {}).get("orchestration_depth")
            if isinstance(payload.get("lineage"), dict)
            else None
        ),
        "sequence_index": (
            payload.get("lineage", {}).get("sequence_index")
            if isinstance(payload.get("lineage"), dict)
            else None
        ),
        "latest_summary": str(execution.get("latest_summary") or ""),
        "operator_note": str(execution.get("operator_note") or ""),
        "operator_summary": operator_summary,
        "failure_summary": failure_summary,
        "stop_reason": stop_reason,
        "artifacts": {
            "plan": bool(payload.get("plan")),
            "actions": bool(payload.get("actions")),
            "stdout": bool(payload.get("stdout")),
            "stderr": bool(payload.get("stderr")),
            "minimum_contract": minimum_artifacts,
        },
        "step_summaries": execution.get("step_summaries")
        if isinstance(execution.get("step_summaries"), list)
        else [],
        "recent_timeline": filtered_timeline[:12],
    }
