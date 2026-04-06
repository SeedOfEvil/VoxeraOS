"""Linked completion delivery and autosurface subsystem.

Owns the cohesive pipeline for discovering completed linked jobs,
extracting canonical evidence, and surfacing results to users with
one-time idempotent delivery and fail-closed semantics.

Extracted from vera/service.py to improve navigability and ownership
clarity.  No behavioral change intended.

Design principles (inherited from service.py):
- Truth is grounded in queue evidence (artifacts, state sidecars),
  never invented.
- Bounded output: all excerpts truncated, list items capped,
  notification outbox limited.
- Fail-closed: missing evidence or unavailable sessions produce
  honest status text, never fabricated prose.
- Idempotent: a job completed multiple times produces one completion;
  auto-surface returns a message only once per completion.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..core.queue_inspect import lookup_job
from ..core.queue_result_consumers import resolve_structured_execution
from . import session_store as vera_session_store
from .result_surfacing import extract_value_forward_text

# ---------------------------------------------------------------------------
# Internal JSON helper (duplicated intentionally to avoid import cycle with
# service.py — this is a trivial 5-line utility)
# ---------------------------------------------------------------------------


def _read_json_dict(path: Path | None) -> dict[str, Any]:
    import json

    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


# ---------------------------------------------------------------------------
# Delivery eligibility
# ---------------------------------------------------------------------------


def _completion_delivery_eligible(completion: dict[str, Any]) -> bool:
    supported_policies = {"read_only_success", "mutating_success", "approval_blocked", "failed"}
    policy = str(completion.get("surfacing_policy") or "").strip().lower()
    if policy not in supported_policies:
        return False

    terminal_outcome = str(completion.get("terminal_outcome") or "").strip().lower()
    if policy == "read_only_success":
        return terminal_outcome == "succeeded"
    if policy == "approval_blocked":
        return terminal_outcome in {"blocked", "failed"}
    if policy == "failed":
        return terminal_outcome == "failed"
    if policy == "mutating_success":
        return terminal_outcome == "succeeded" and _is_true_terminal_completion(completion)
    return False


def _is_true_terminal_completion(completion: dict[str, Any]) -> bool:
    child_summary = completion.get("child_summary")
    child_refs_count = int(completion.get("child_refs_count") or 0)
    stop_reason = str(completion.get("stop_reason") or "").strip().lower()

    if isinstance(child_summary, dict):
        pending_like = sum(
            int(child_summary.get(key) or 0) for key in ("pending", "awaiting_approval", "unknown")
        )
        if pending_like > 0:
            return False
        total = int(child_summary.get("total") or 0)
        if child_refs_count > 0 and total <= 0:
            return False
    elif child_refs_count > 0:
        return False

    return not any(token in stop_reason for token in ("enqueue_child", "delegat", "handoff_child"))


# ---------------------------------------------------------------------------
# Surfacing policy classification
# ---------------------------------------------------------------------------


def _classify_surfacing_policy(payload: dict[str, Any]) -> str:
    terminal_outcome = str(payload.get("terminal_outcome") or "").strip().lower()
    approval_status = str(payload.get("approval_status") or "").strip().lower()
    outcome_class = str(payload.get("normalized_outcome_class") or "").strip().lower()
    request_kind = str(payload.get("request_kind") or "").strip().lower()
    side_effect_class = str(payload.get("side_effect_class") or "").strip().lower()
    read_only_requested = payload.get("read_only_requested") is True
    latest_summary = str(payload.get("latest_summary") or "")
    highlights = payload.get("result_highlights")
    result_highlight_count = len(highlights) if isinstance(highlights, list) else 0

    if approval_status == "pending" or outcome_class == "approval_blocked":
        return "approval_blocked"
    if terminal_outcome == "canceled":
        return "canceled"
    if terminal_outcome in {"failed", "blocked"}:
        if outcome_class in {"policy_denied", "capability_boundary_mismatch", "path_blocked_scope"}:
            return "manual_only"
        return "failed"
    if len(latest_summary) > 480 or result_highlight_count >= 6:
        return "noisy_large_result"
    if terminal_outcome == "succeeded":
        if read_only_requested:
            return "read_only_success"
        if side_effect_class in {"write", "execute", "mutating"}:
            return "mutating_success"
        if request_kind in {"write_file", "file_organize", "open_url", "open_app", "run_command"}:
            return "mutating_success"
        return "read_only_success"
    return "manual_only"


# ---------------------------------------------------------------------------
# Result highlight normalization
# ---------------------------------------------------------------------------


def _normalize_result_highlights(structured: dict[str, Any]) -> list[str]:
    highlights: list[str] = []
    artifact_families = structured.get("artifact_families")
    if isinstance(artifact_families, list) and artifact_families:
        compact = ", ".join(
            str(item).strip() for item in artifact_families[:4] if str(item).strip()
        )
        if compact:
            highlights.append(f"artifact_families={compact}")
    child_summary = structured.get("child_summary")
    if isinstance(child_summary, dict) and child_summary:
        summary_parts = [
            f"{key}:{int(value)}"
            for key, value in child_summary.items()
            if isinstance(value, int) and key in {"done", "failed", "pending", "canceled"}
        ]
        if summary_parts:
            highlights.append("child_summary=" + ", ".join(summary_parts))
    return highlights[:6]


# ---------------------------------------------------------------------------
# Step machine payload extraction
# ---------------------------------------------------------------------------


def _extract_step_machine_payload(structured: dict[str, Any], *, skill_id: str) -> dict[str, Any]:
    step_summaries = structured.get("step_summaries")
    if not isinstance(step_summaries, list):
        return {}
    for item in step_summaries:
        if not isinstance(item, dict):
            continue
        if str(item.get("skill_id") or "").strip() != skill_id:
            continue
        payload = item.get("machine_payload")
        if isinstance(payload, dict):
            return payload
    return {}


# ---------------------------------------------------------------------------
# Diagnostics formatting
# ---------------------------------------------------------------------------


def _format_diagnostics_values(*, structured: dict[str, Any], mission_id: str) -> list[str]:
    values: list[str] = []

    if mission_id == "system_diagnostics":
        host = _extract_step_machine_payload(structured, skill_id="system.host_info")
        hostname = str(host.get("hostname") or "").strip()
        uptime_seconds = host.get("uptime_seconds")
        if hostname and isinstance(uptime_seconds, (int, float)):
            uptime_hours = round(float(uptime_seconds) / 3600, 1)
            values.append(f"host={hostname}, uptime\u2248{uptime_hours}h")
        elif hostname:
            values.append(f"host={hostname}")

        memory = _extract_step_machine_payload(structured, skill_id="system.memory_usage")
        used_gib = memory.get("used_gib")
        total_gib = memory.get("total_gib")
        used_percent = memory.get("used_percent")
        if isinstance(used_gib, (int, float)) and isinstance(total_gib, (int, float)):
            mem_line = f"memory={used_gib}/{total_gib}GiB"
            if isinstance(used_percent, (int, float)):
                mem_line = f"{mem_line} ({used_percent}% used)"
            values.append(mem_line)

        load = _extract_step_machine_payload(structured, skill_id="system.load_snapshot")
        load_1m = load.get("load_1m")
        load_5m = load.get("load_5m")
        load_15m = load.get("load_15m")
        if all(isinstance(item, (int, float)) for item in (load_1m, load_5m, load_15m)):
            values.append(f"load(1/5/15m)={load_1m}/{load_5m}/{load_15m}")

        disk = _extract_step_machine_payload(structured, skill_id="system.disk_usage")
        used_pct = disk.get("used_percent")
        free_gb = disk.get("free_gb")
        if isinstance(used_pct, (int, float)):
            if isinstance(free_gb, (int, float)):
                values.append(f"disk={used_pct}% used, {free_gb}GB free")
            else:
                values.append(f"disk={used_pct}% used")

    service_status = _extract_step_machine_payload(structured, skill_id="system.service_status")
    if service_status:
        service = str(
            service_status.get("service") or service_status.get("Id") or "service"
        ).strip()
        active = str(service_status.get("ActiveState") or "unknown").strip()
        sub = str(service_status.get("SubState") or "unknown").strip()
        values.append(f"service_state={service}:{active}/{sub}")

    recent_logs = _extract_step_machine_payload(structured, skill_id="system.recent_service_logs")
    if recent_logs:
        service = str(recent_logs.get("service") or "service").strip()
        line_count = recent_logs.get("line_count")
        since_minutes = recent_logs.get("since_minutes")
        if isinstance(line_count, int) and isinstance(since_minutes, int):
            values.append(f"recent_logs={service}:{line_count} lines (last {since_minutes}m)")
        elif isinstance(line_count, int):
            values.append(f"recent_logs={service}:{line_count} lines")

    return values[:4]


# ---------------------------------------------------------------------------
# Autosurface message formatting
# ---------------------------------------------------------------------------


def _format_completion_autosurface_message(completion: dict[str, Any]) -> str:
    request_kind = str(completion.get("request_kind") or "request").strip().replace("_", " ")
    latest_summary = str(completion.get("latest_summary") or "").strip()
    failure_summary = str(completion.get("failure_summary") or "").strip()
    operator_note = str(completion.get("operator_note") or "").strip()
    next_action_hint = str(completion.get("next_action_hint") or "").strip()
    policy = str(completion.get("surfacing_policy") or "").strip().lower()

    if policy == "approval_blocked":
        message = "Your linked request is paused pending approval in VoxeraOS."
        if latest_summary:
            return f"{message} Latest summary: {latest_summary}".strip()
        if next_action_hint:
            return f"{message} Next action: {next_action_hint}".strip()
        return f"{message} Approve or reject it in VoxeraOS to continue.".strip()

    if policy == "failed":
        summary = failure_summary or latest_summary
        message = f"Your linked {request_kind} job failed."
        if summary:
            message = f"{message} Failure summary: {summary}"
        hint = next_action_hint or operator_note
        if hint and len(hint) <= 220:
            message = f"{message} Next action: {hint}"
        return message.strip()

    # Evidence-grounded value-forward surfacing: prefer concise result text
    # extracted from canonical step evidence over generic status messaging.
    value_forward = str(completion.get("value_forward_text") or "").strip()
    if value_forward:
        return value_forward

    diagnostics_values = completion.get("diagnostics_values")
    diagnostics_text = ""
    if isinstance(diagnostics_values, list):
        compact_values = [str(item).strip() for item in diagnostics_values[:4] if str(item).strip()]
        if compact_values:
            diagnostics_text = "; ".join(compact_values)

    highlights = completion.get("result_highlights")
    highlight_text = ""
    if isinstance(highlights, list):
        compact = "; ".join(str(item).strip() for item in highlights[:2] if str(item).strip())
        if compact:
            highlight_text = compact

    message = f"Your linked {request_kind} job completed successfully."
    if diagnostics_text:
        message = f"{message} Diagnostics snapshot: {diagnostics_text}."
    elif latest_summary:
        message = f"{message} {latest_summary}"
    elif highlight_text:
        message = f"{message} Canonical evidence highlights: {highlight_text}."
    else:
        message = f"{message} I have the canonical result available for follow-up."
    return message.strip()


# ---------------------------------------------------------------------------
# Completion notification management
# ---------------------------------------------------------------------------


def _build_completion_notification(
    *, session_id: str, completion: dict[str, Any]
) -> dict[str, Any]:
    created_at_ms = int(completion.get("completion_detected_at_ms") or int(time.time() * 1000))
    lineage = completion.get("lineage")
    root_job_id = None
    if isinstance(lineage, dict):
        root_job_id = str(lineage.get("root_job_id") or "").strip() or None
    return {
        "notification_id": f"{session_id}:{completion.get('job_ref')}:{created_at_ms}",
        "job_id": str(completion.get("job_ref") or ""),
        "session_id": session_id,
        "root_job_id": root_job_id,
        "outcome_class": str(completion.get("normalized_outcome_class") or "").strip() or None,
        "is_user_terminal": _completion_delivery_eligible(completion),
        "message": _format_completion_autosurface_message(completion),
        "created_at_ms": created_at_ms,
        "delivery_status": "pending",
        "surfaced_in_chat": completion.get("surfaced_in_chat") is True,
        "surfaced_at_ms": completion.get("surfaced_at_ms"),
        "fallback_pending": completion.get("surfaced_in_chat") is not True,
    }


def _upsert_completion_notification(
    *, session_id: str, registry: dict[str, Any], completion: dict[str, Any]
) -> dict[str, Any]:
    outbox_raw = registry.get("notification_outbox")
    outbox = outbox_raw if isinstance(outbox_raw, list) else []
    job_ref = str(completion.get("job_ref") or "")
    existing: dict[str, Any] | None = None
    for item in outbox:
        if isinstance(item, dict) and str(item.get("job_id") or "") == job_ref:
            existing = item
            break
    if existing is None:
        existing = _build_completion_notification(session_id=session_id, completion=completion)
        outbox.append(existing)
    else:
        existing["is_user_terminal"] = _completion_delivery_eligible(completion)
        existing["message"] = _format_completion_autosurface_message(completion)
        existing["surfaced_in_chat"] = completion.get("surfaced_in_chat") is True
        existing["surfaced_at_ms"] = completion.get("surfaced_at_ms")
        if completion.get("surfaced_in_chat") is True:
            existing["fallback_pending"] = False
            if str(existing.get("delivery_status") or "").strip().lower() == "pending":
                existing["delivery_status"] = "delivered"
        else:
            existing["fallback_pending"] = True
    registry["notification_outbox"] = outbox[-vera_session_store._MAX_LINKED_NOTIFICATIONS :]
    return existing


# ---------------------------------------------------------------------------
# Live delivery
# ---------------------------------------------------------------------------


def _attempt_live_delivery(
    *, queue_root: Path, session_id: str, completion: dict[str, Any], notification: dict[str, Any]
) -> bool:
    if completion.get("surfaced_in_chat") is True:
        notification["delivery_status"] = "delivered"
        notification["fallback_pending"] = False
        return True
    if not _completion_delivery_eligible(completion):
        notification["delivery_status"] = "pending"
        notification["fallback_pending"] = True
        return False
    if not vera_session_store._session_path(queue_root, session_id).exists():
        notification["delivery_status"] = "unavailable"
        notification["fallback_pending"] = True
        return False

    message = _format_completion_autosurface_message(completion)
    try:
        vera_session_store.append_session_turn(
            queue_root, session_id, role="assistant", text=message
        )
    except Exception:
        notification["delivery_status"] = "unavailable"
        notification["fallback_pending"] = True
        return False
    now_ms = int(time.time() * 1000)
    completion["surfaced_in_chat"] = True
    completion["surfaced_at_ms"] = now_ms
    notification["delivery_status"] = "delivered"
    notification["fallback_pending"] = False
    notification["surfaced_in_chat"] = True
    notification["surfaced_at_ms"] = now_ms
    return True


# ---------------------------------------------------------------------------
# Terminal state detection
# ---------------------------------------------------------------------------


def _is_terminal_queue_state(*, lifecycle_state: str, terminal_outcome: str, bucket: str) -> bool:
    lifecycle = lifecycle_state.strip().lower()
    outcome = terminal_outcome.strip().lower()
    return (
        bucket in {"done", "failed", "canceled"}
        or lifecycle in {"done", "failed", "canceled"}
        or outcome in {"succeeded", "failed", "blocked", "canceled"}
    )


# ---------------------------------------------------------------------------
# Completion payload construction
# ---------------------------------------------------------------------------


def _build_completion_payload(
    *,
    session_id: str,
    job_ref: str,
    bucket: str,
    structured: dict[str, Any],
    state_payload: dict[str, Any],
    job_payload: dict[str, Any],
) -> dict[str, Any]:
    raw_job_intent = job_payload.get("job_intent")
    job_intent: dict[str, Any] = raw_job_intent if isinstance(raw_job_intent, dict) else {}
    request_kind = str(
        job_intent.get("request_kind")
        or job_payload.get("request_kind")
        or job_payload.get("kind")
        or "unknown"
    )
    mission_id = str(job_intent.get("mission_id") or job_payload.get("mission_id") or "").strip()
    lifecycle_state = str(
        structured.get("lifecycle_state") or state_payload.get("lifecycle_state") or ""
    ).strip()
    terminal_outcome = str(
        structured.get("terminal_outcome") or state_payload.get("terminal_outcome") or ""
    ).strip()
    approval_status = str(
        structured.get("approval_status") or state_payload.get("approval_status") or "none"
    ).strip()
    execution_capabilities_raw = structured.get("execution_capabilities")
    execution_capabilities: dict[str, Any] = (
        execution_capabilities_raw if isinstance(execution_capabilities_raw, dict) else {}
    )
    lineage_raw = structured.get("lineage")
    lineage: dict[str, Any] | None = lineage_raw if isinstance(lineage_raw, dict) else None
    child_refs_raw = structured.get("child_refs")
    child_refs: list[Any] = child_refs_raw if isinstance(child_refs_raw, list) else []
    child_summary_raw = structured.get("child_summary")
    child_summary: dict[str, Any] | None = (
        child_summary_raw if isinstance(child_summary_raw, dict) else None
    )

    payload = {
        "job_ref": job_ref,
        "linked_session_id": session_id,
        "linked_thread_id": session_id,
        "lifecycle_state": lifecycle_state,
        "terminal_outcome": terminal_outcome,
        "approval_status": approval_status,
        "normalized_outcome_class": str(structured.get("normalized_outcome_class") or "").strip(),
        "request_kind": request_kind,
        "read_only_requested": job_payload.get("read_only") is True
        or job_intent.get("read_only") is True,
        "mission_id": mission_id or None,
        "latest_summary": str(structured.get("latest_summary") or "").strip(),
        "failure_summary": str(structured.get("error") or "").strip(),
        "operator_note": str(structured.get("operator_note") or "").strip(),
        "next_action_hint": str(structured.get("next_action_hint") or "").strip(),
        "result_highlights": _normalize_result_highlights(structured),
        "diagnostics_values": _format_diagnostics_values(
            structured=structured,
            mission_id=mission_id,
        ),
        "side_effect_class": str(execution_capabilities.get("side_effect_class") or "").strip(),
        "lineage": lineage,
        "child_refs_count": len(child_refs),
        "child_summary": child_summary,
        "stop_reason": str(structured.get("stop_reason") or "").strip(),
        "queue_bucket": bucket,
        "completion_detected_at_ms": int(time.time() * 1000),
        "surfaced_in_chat": False,
        "surfaced_at_ms": None,
        "value_forward_text": extract_value_forward_text(
            structured=structured,
            mission_id=mission_id,
        ),
    }
    payload["surfacing_policy"] = _classify_surfacing_policy(payload)
    return payload


# ---------------------------------------------------------------------------
# Public API — completion ingestion
# ---------------------------------------------------------------------------


def ingest_linked_job_completions(
    queue_root: Path,
    session_id: str,
    *,
    only_job_ref: str | None = None,
) -> list[dict[str, Any]]:
    registry = vera_session_store._read_linked_job_registry(queue_root, session_id)
    tracked_raw = registry.get("tracked")
    tracked = tracked_raw if isinstance(tracked_raw, list) else []
    completions_raw = registry.get("completions")
    completions = completions_raw if isinstance(completions_raw, list) else []
    completion_job_refs = {
        str(item.get("job_ref") or "")
        for item in completions
        if isinstance(item, dict) and str(item.get("job_ref") or "")
    }

    created: list[dict[str, Any]] = []
    changed = False
    only_ref = Path(only_job_ref).name.strip() if only_job_ref else None
    for item in tracked:
        if not isinstance(item, dict):
            continue
        job_ref = str(item.get("job_ref") or "").strip()
        if only_ref and job_ref != only_ref:
            continue
        if not job_ref or job_ref in completion_job_refs:
            continue
        found = lookup_job(queue_root, job_ref)
        if found is None:
            continue
        state_payload = _read_json_dict(
            found.primary_path.with_name(f"{found.primary_path.stem}.state.json")
        )
        approval_payload = _read_json_dict(found.approval_path) if found.approval_path else {}
        failed_payload = (
            _read_json_dict(found.failed_sidecar_path) if found.failed_sidecar_path else {}
        )
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
        if not _is_terminal_queue_state(
            lifecycle_state=lifecycle_state,
            terminal_outcome=terminal_outcome,
            bucket=found.bucket,
        ):
            continue
        job_payload = _read_json_dict(found.primary_path)
        completion = _build_completion_payload(
            session_id=session_id,
            job_ref=job_ref,
            bucket=found.bucket,
            structured=structured,
            state_payload=state_payload,
            job_payload=job_payload,
        )
        completions.append(completion)
        completion_job_refs.add(job_ref)
        item["completion_ingested"] = True
        item["completion_detected_at_ms"] = int(completion.get("completion_detected_at_ms") or 0)
        created.append(completion)
        changed = True

    if changed:
        registry["tracked"] = tracked[-vera_session_store._MAX_LINKED_JOB_TRACK :]
        registry["completions"] = completions[-vera_session_store._MAX_LINKED_COMPLETIONS :]
        vera_session_store._write_linked_job_registry(queue_root, session_id, registry)

    return created


# ---------------------------------------------------------------------------
# Public API — autosurface (fallback delivery on chat cycle)
# ---------------------------------------------------------------------------


def maybe_auto_surface_linked_completion(queue_root: Path, session_id: str) -> str | None:
    registry = vera_session_store._read_linked_job_registry(queue_root, session_id)
    completions_raw = registry.get("completions")
    completions = completions_raw if isinstance(completions_raw, list) else []
    tracked_raw = registry.get("tracked")
    tracked = tracked_raw if isinstance(tracked_raw, list) else []
    handoff = vera_session_store.read_session_handoff_state(queue_root, session_id) or {}
    latest_handoff_ref = ""
    handoff_job_id = str(handoff.get("job_id") or "").strip()
    if handoff_job_id:
        latest_handoff_ref = f"inbox-{handoff_job_id}.json"

    if latest_handoff_ref:
        latest_completion_exists = any(
            isinstance(item, dict) and str(item.get("job_ref") or "").strip() == latest_handoff_ref
            for item in completions
        )
        if not latest_completion_exists:
            latest_tracked_pending = any(
                isinstance(item, dict)
                and str(item.get("job_ref") or "").strip() == latest_handoff_ref
                and item.get("completion_ingested") is not True
                for item in tracked
            )
            if latest_tracked_pending:
                return None

    outbox_raw = registry.get("notification_outbox")
    outbox = outbox_raw if isinstance(outbox_raw, list) else []

    def _completion_priority(item: dict[str, Any]) -> tuple[int, int]:
        completion_ref = str(item.get("job_ref") or "").strip()
        matches_latest_handoff = int(
            bool(latest_handoff_ref) and completion_ref == latest_handoff_ref
        )
        detected_at_ms = int(item.get("completion_detected_at_ms") or 0)
        return (matches_latest_handoff, detected_at_ms)

    completion_candidates = [item for item in completions if isinstance(item, dict)]
    if latest_handoff_ref:
        scoped = [
            item
            for item in completion_candidates
            if str(item.get("job_ref") or "").strip() == latest_handoff_ref
        ]
        completion_candidates = scoped

    for completion in sorted(
        completion_candidates,
        key=_completion_priority,
        reverse=True,
    ):
        notification = _upsert_completion_notification(
            session_id=session_id,
            registry=registry,
            completion=completion,
        )
        if completion.get("surfaced_in_chat") is True:
            continue
        if not _completion_delivery_eligible(completion):
            continue

        completion["surfaced_in_chat"] = True
        completion["surfaced_at_ms"] = int(time.time() * 1000)
        notification["delivery_status"] = "fallback_delivered"
        notification["fallback_pending"] = False
        notification["surfaced_in_chat"] = True
        notification["surfaced_at_ms"] = completion["surfaced_at_ms"]
        registry["completions"] = completions[-vera_session_store._MAX_LINKED_COMPLETIONS :]
        registry["notification_outbox"] = outbox[-vera_session_store._MAX_LINKED_NOTIFICATIONS :]
        vera_session_store._write_linked_job_registry(queue_root, session_id, registry)
        return _format_completion_autosurface_message(completion)

    return None


# ---------------------------------------------------------------------------
# Public API — live delivery (immediate post to active session)
# ---------------------------------------------------------------------------


def maybe_deliver_linked_completion_live(
    queue_root: Path, session_id: str, *, job_ref: str
) -> bool:
    ingest_linked_job_completions(queue_root, session_id, only_job_ref=job_ref)
    registry = vera_session_store._read_linked_job_registry(queue_root, session_id)
    completions_raw = registry.get("completions")
    completions = completions_raw if isinstance(completions_raw, list) else []
    changed = False
    delivered = False
    for completion in completions:
        if not isinstance(completion, dict):
            continue
        if str(completion.get("job_ref") or "") != Path(job_ref).name:
            continue
        notification = _upsert_completion_notification(
            session_id=session_id,
            registry=registry,
            completion=completion,
        )
        if _attempt_live_delivery(
            queue_root=queue_root,
            session_id=session_id,
            completion=completion,
            notification=notification,
        ):
            delivered = True
            changed = True
        else:
            changed = True
    if changed:
        registry["completions"] = completions[-vera_session_store._MAX_LINKED_COMPLETIONS :]
        outbox_raw = registry.get("notification_outbox")
        outbox = outbox_raw if isinstance(outbox_raw, list) else []
        registry["notification_outbox"] = outbox[-vera_session_store._MAX_LINKED_NOTIFICATIONS :]
        vera_session_store._write_linked_job_registry(queue_root, session_id, registry)
    return delivered


def maybe_deliver_linked_completion_live_for_job(queue_root: Path, *, job_ref: str) -> int:
    normalized = Path(job_ref).name.strip()
    if not normalized:
        return 0
    sessions_dir = queue_root / "artifacts" / "vera_sessions"
    if not sessions_dir.exists():
        return 0
    delivered_count = 0
    for session_file in sorted(sessions_dir.glob("*.json")):
        session_id = session_file.stem.strip()
        if not session_id:
            continue
        registry = vera_session_store._read_linked_job_registry(queue_root, session_id)
        tracked = registry.get("tracked")
        tracked_list = tracked if isinstance(tracked, list) else []
        if not any(
            isinstance(item, dict) and str(item.get("job_ref") or "") == normalized
            for item in tracked_list
        ):
            continue
        if maybe_deliver_linked_completion_live(queue_root, session_id, job_ref=normalized):
            delivered_count += 1
    return delivered_count
