from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from ..brain.fallback import classify_fallback_reason
from ..brain.gemini import GeminiBrain
from ..brain.json_recovery import recover_json_object
from ..brain.openai_compat import OpenAICompatBrain
from ..config import load_app_config
from ..core.queue_inspect import lookup_job
from ..core.queue_result_consumers import resolve_structured_execution
from . import session_store as vera_session_store
from .brave_search import BraveSearchClient
from .handoff import drafting_guidance, maybe_draft_job_payload, normalize_preview_payload
from .investigation_flow import (
    build_structured_investigation_results,
    format_web_investigation_answer,
    is_informational_web_query,
    maybe_handle_investigation_turn,
    normalize_web_query,
)
from .investigation_flow import (
    run_web_enrichment as _run_web_enrichment,
)
from .prompt import VERA_PREVIEW_BUILDER_PROMPT, VERA_SYSTEM_PROMPT
from .result_surfacing import extract_value_forward_text
from .saveable_artifacts import collect_recent_saveable_assistant_artifacts
from .weather import OpenMeteoWeatherClient, WeatherSnapshot
from .weather_flow import (
    extract_weather_followup_kind,
    extract_weather_location_from_message,
    is_weather_investigation_request,
    is_weather_question,
    maybe_handle_weather_turn,
    normalize_weather_location_candidate,
    weather_answer_for_followup,
    weather_context_has_pending_lookup,
    weather_context_is_waiting_for_location,
    weather_followup_is_active,
)

MAX_SESSION_TURNS = vera_session_store.MAX_SESSION_TURNS
_MAX_LINKED_JOB_TRACK = vera_session_store._MAX_LINKED_JOB_TRACK
_MAX_LINKED_COMPLETIONS = vera_session_store._MAX_LINKED_COMPLETIONS
_MAX_LINKED_NOTIFICATIONS = vera_session_store._MAX_LINKED_NOTIFICATIONS

new_session_id = vera_session_store.new_session_id
_session_path = vera_session_store._session_path
_read_session_payload = vera_session_store._read_session_payload
_write_session_payload = vera_session_store._write_session_payload
read_session_turns = vera_session_store.read_session_turns
read_session_updated_at_ms = vera_session_store.read_session_updated_at_ms
append_session_turn = vera_session_store.append_session_turn
read_session_preview = vera_session_store.read_session_preview
write_session_preview = vera_session_store.write_session_preview
write_session_handoff_state = vera_session_store.write_session_handoff_state
read_session_handoff_state = vera_session_store.read_session_handoff_state
clear_session_turns = vera_session_store.clear_session_turns
read_session_enrichment = vera_session_store.read_session_enrichment
write_session_enrichment = vera_session_store.write_session_enrichment
read_session_investigation = vera_session_store.read_session_investigation
read_session_weather_context = vera_session_store.read_session_weather_context
write_session_weather_context = vera_session_store.write_session_weather_context
write_session_investigation = vera_session_store.write_session_investigation
read_session_derived_investigation_output = (
    vera_session_store.read_session_derived_investigation_output
)
write_session_derived_investigation_output = (
    vera_session_store.write_session_derived_investigation_output
)
read_session_saveable_assistant_artifacts = (
    vera_session_store.read_session_saveable_assistant_artifacts
)
_write_session_saveable_assistant_artifacts = (
    vera_session_store._write_session_saveable_assistant_artifacts
)
session_debug_info = vera_session_store.session_debug_info
_read_linked_job_registry = vera_session_store._read_linked_job_registry
_write_linked_job_registry = vera_session_store._write_linked_job_registry
register_session_linked_job = vera_session_store.register_session_linked_job
read_linked_job_completions = vera_session_store.read_linked_job_completions
read_session_conversational_planning_active = (
    vera_session_store.read_session_conversational_planning_active
)
write_session_conversational_planning_active = (
    vera_session_store.write_session_conversational_planning_active
)
read_session_last_user_input_origin = vera_session_store.read_session_last_user_input_origin
read_session_context = vera_session_store.read_session_context
write_session_context = vera_session_store.write_session_context
update_session_context = vera_session_store.update_session_context
clear_session_context = vera_session_store.clear_session_context


def _read_json_dict(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


PREVIEW_BUILDER_MODEL = "gemini-3-flash-preview"
PREVIEW_BUILDER_FALLBACK_MODEL = "gemini-3.1-flash-lite-preview"


class HiddenCompilerDecision:
    def __init__(
        self,
        *,
        action: str,
        intent_type: str,
        updated_preview: dict[str, Any] | None = None,
        patch: dict[str, Any] | None = None,
    ) -> None:
        self.action = action
        self.intent_type = intent_type
        self.updated_preview = updated_preview
        self.patch = patch

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> HiddenCompilerDecision:
        allowed = {"action", "intent_type", "updated_preview", "patch"}
        if not set(payload).issubset(allowed):
            raise ValueError("hidden compiler decision contains unsupported keys")

        action = str(payload.get("action") or "").strip()
        intent_type = str(payload.get("intent_type") or "").strip()
        updated_preview = payload.get("updated_preview")
        patch = payload.get("patch")

        if action not in {"replace_preview", "patch_preview", "no_change"}:
            raise ValueError("action must be replace_preview, patch_preview, or no_change")
        if intent_type not in {"new_intent", "refinement", "unclear"}:
            raise ValueError("intent_type must be new_intent, refinement, or unclear")

        if action == "replace_preview":
            if not isinstance(updated_preview, dict):
                raise ValueError("replace_preview requires updated_preview object")
            if patch is not None:
                raise ValueError("replace_preview cannot include patch")
        elif action == "patch_preview":
            if not isinstance(patch, dict):
                raise ValueError("patch_preview requires patch object")
            if updated_preview is not None:
                raise ValueError("patch_preview cannot include updated_preview")
        else:
            if updated_preview is not None or patch is not None:
                raise ValueError("no_change cannot include updated_preview or patch")

        return cls(
            action=action,
            intent_type=intent_type,
            updated_preview=updated_preview if isinstance(updated_preview, dict) else None,
            patch=patch if isinstance(patch, dict) else None,
        )


# Weather-flow compatibility aliases kept here so existing tests/call sites can
# continue patching `vera.service` while orchestration lives in weather_flow.py.
_is_weather_investigation_request = is_weather_investigation_request
_is_weather_question = is_weather_question
_normalize_weather_location_candidate = normalize_weather_location_candidate
_extract_weather_location_from_message = extract_weather_location_from_message
_extract_weather_followup_kind = extract_weather_followup_kind
_weather_followup_is_active = weather_followup_is_active
_weather_context_has_pending_lookup = weather_context_has_pending_lookup
_weather_context_is_waiting_for_location = weather_context_is_waiting_for_location
_weather_answer_for_followup = weather_answer_for_followup

# Investigation-flow compatibility aliases kept here so existing tests/call sites can
# continue patching `vera.service` while orchestration lives in investigation_flow.py.
_is_informational_web_query = is_informational_web_query
_normalize_web_query = normalize_web_query
_build_structured_investigation_results = build_structured_investigation_results
_format_web_investigation_answer = format_web_investigation_answer
run_web_enrichment = _run_web_enrichment


def _service_weather_question(message: str) -> bool:
    try:
        return _is_weather_question(
            message,
            is_weather_investigation_request_hook=_is_weather_investigation_request,
        )
    except TypeError:
        return _is_weather_question(message)


def _service_extract_weather_location_from_message(message: str) -> str | None:
    try:
        return _extract_weather_location_from_message(
            message,
            normalize_weather_location_candidate_hook=_normalize_weather_location_candidate,
        )
    except TypeError:
        return _extract_weather_location_from_message(message)


async def _lookup_live_weather(
    location_query: str, *, followup_kind: str | None = None
) -> WeatherSnapshot:
    _ = followup_kind
    client = OpenMeteoWeatherClient()
    resolved = await client.resolve_location(location_query)
    if resolved is None:
        raise RuntimeError(
            "I couldn’t resolve that place into a structured weather location. Please give me a clearer location."
        )
    return await client.fetch_snapshot(resolved)


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
    registry["notification_outbox"] = outbox[-_MAX_LINKED_NOTIFICATIONS:]
    return existing


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
    if not _session_path(queue_root, session_id).exists():
        notification["delivery_status"] = "unavailable"
        notification["fallback_pending"] = True
        return False

    message = _format_completion_autosurface_message(completion)
    try:
        append_session_turn(queue_root, session_id, role="assistant", text=message)
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


def _is_terminal_queue_state(*, lifecycle_state: str, terminal_outcome: str, bucket: str) -> bool:
    lifecycle = lifecycle_state.strip().lower()
    outcome = terminal_outcome.strip().lower()
    return (
        bucket in {"done", "failed", "canceled"}
        or lifecycle in {"done", "failed", "canceled"}
        or outcome in {"succeeded", "failed", "blocked", "canceled"}
    )


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


def _format_diagnostics_values(*, structured: dict[str, Any], mission_id: str) -> list[str]:
    values: list[str] = []

    if mission_id == "system_diagnostics":
        host = _extract_step_machine_payload(structured, skill_id="system.host_info")
        hostname = str(host.get("hostname") or "").strip()
        uptime_seconds = host.get("uptime_seconds")
        if hostname and isinstance(uptime_seconds, (int, float)):
            uptime_hours = round(float(uptime_seconds) / 3600, 1)
            values.append(f"host={hostname}, uptime≈{uptime_hours}h")
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


def ingest_linked_job_completions(
    queue_root: Path,
    session_id: str,
    *,
    only_job_ref: str | None = None,
) -> list[dict[str, Any]]:
    registry = _read_linked_job_registry(queue_root, session_id)
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
        registry["tracked"] = tracked[-_MAX_LINKED_JOB_TRACK:]
        registry["completions"] = completions[-_MAX_LINKED_COMPLETIONS:]
        _write_linked_job_registry(queue_root, session_id, registry)

    return created


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


def maybe_auto_surface_linked_completion(queue_root: Path, session_id: str) -> str | None:
    registry = _read_linked_job_registry(queue_root, session_id)
    completions_raw = registry.get("completions")
    completions = completions_raw if isinstance(completions_raw, list) else []
    tracked_raw = registry.get("tracked")
    tracked = tracked_raw if isinstance(tracked_raw, list) else []
    handoff = read_session_handoff_state(queue_root, session_id) or {}
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
        registry["completions"] = completions[-_MAX_LINKED_COMPLETIONS:]
        registry["notification_outbox"] = outbox[-_MAX_LINKED_NOTIFICATIONS:]
        _write_linked_job_registry(queue_root, session_id, registry)
        return _format_completion_autosurface_message(completion)

    return None


def maybe_deliver_linked_completion_live(
    queue_root: Path, session_id: str, *, job_ref: str
) -> bool:
    ingest_linked_job_completions(queue_root, session_id, only_job_ref=job_ref)
    registry = _read_linked_job_registry(queue_root, session_id)
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
        registry["completions"] = completions[-_MAX_LINKED_COMPLETIONS:]
        outbox_raw = registry.get("notification_outbox")
        outbox = outbox_raw if isinstance(outbox_raw, list) else []
        registry["notification_outbox"] = outbox[-_MAX_LINKED_NOTIFICATIONS:]
        _write_linked_job_registry(queue_root, session_id, registry)
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
        registry = _read_linked_job_registry(queue_root, session_id)
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


# Injected into the user message on code/script draft turns to override
# Vera's default "not the payload drafter" stance and actually produce code.
_CODE_DRAFT_HINT = (
    "\n\n[System note for this request: You are being asked to write a code or "
    "script file. Write the complete, working code directly in your response "
    "inside a properly-fenced code block (e.g. ```python\\n...\\n```). "
    "The fenced block will be automatically extracted and stored as a governed "
    "preview file for the user to review and submit.]"
)

_WRITING_DRAFT_HINT = (
    "\n\n[System note for this request: You are being asked to draft a prose document "
    "artifact. Write the actual essay/article/writeup/explanation body directly in your "
    "response. Avoid hidden control markup. If you include a short conversational wrapper, "
    "place the full draft body after a blank line so it can be extracted into the governed "
    "preview file.]"
)


def build_vera_messages(
    *,
    turns: list[dict[str, str]],
    user_message: str,
    code_draft: bool = False,
    writing_draft: bool = False,
) -> list[dict[str, str]]:
    if code_draft and writing_draft:
        raise ValueError("code_draft and writing_draft are mutually exclusive")
    messages: list[dict[str, str]] = [{"role": "system", "content": VERA_SYSTEM_PROMPT}]
    for turn in turns[-MAX_SESSION_TURNS:]:
        messages.append({"role": turn["role"], "content": turn["text"]})
    content = user_message.strip()
    if code_draft:
        content = content + _CODE_DRAFT_HINT
    elif writing_draft:
        content = content + _WRITING_DRAFT_HINT
    messages.append({"role": "user", "content": content})
    return messages


def _recent_assistant_authored_content(turns: list[dict[str, str]]) -> list[str]:
    non_authored_markers = (
        "i submitted the job to voxeraos",
        "job id:",
        "the request is now in the queue",
        "execution has not completed yet",
        "check status and evidence",
        "approval status",
        "expected artifacts",
        "queue state",
    )
    authored: list[str] = []
    for turn in turns[-MAX_SESSION_TURNS:]:
        if str(turn.get("role") or "").strip().lower() != "assistant":
            continue
        text = str(turn.get("text") or "").strip()
        lowered = text.lower()
        if not text:
            continue
        if any(marker in lowered for marker in non_authored_markers):
            continue
        normalized = re.sub(r"\s+", " ", lowered.replace("—", "-")).strip()
        if any(
            normalized.startswith(prefix)
            for prefix in (
                "you're welcome",
                "youre welcome",
                "you're very welcome",
                "youre very welcome",
                "no problem",
                "anytime",
                "of course",
                "sure thing",
                "glad to help",
                "happy to help",
                "my pleasure",
            )
        ) and (
            len(normalized.split()) <= 24
            or any(
                phrase in normalized
                for phrase in (
                    "if you'd like",
                    "if you would like",
                    "let me know",
                    "feel free",
                    "i can save that",
                    "i can also",
                )
            )
        ):
            continue
        authored.append(text)
    return authored[-4:]


def _build_preview_builder_messages(
    *,
    turns: list[dict[str, str]],
    user_message: str,
    active_preview: dict[str, Any] | None,
    enrichment_context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    guidance = drafting_guidance()
    context_payload: dict[str, Any] = {
        "active_preview": active_preview,
        "latest_user_message": user_message.strip(),
        "recent_turns": turns[-MAX_SESSION_TURNS:],
        "recent_assistant_authored_content": _recent_assistant_authored_content(turns),
        "decision_contract": {
            "action": ["replace_preview", "patch_preview", "no_change"],
            "intent_type": ["new_intent", "refinement", "unclear"],
            "updated_preview": "object | null",
            "patch": "object | null",
        },
        "preview_schema": {
            "goal": "required string",
            "title": "optional string",
            "write_file": {
                "path": "required string",
                "content": "required string",
                "mode": "overwrite | append",
            },
            "enqueue_child": {
                "goal": "required string",
                "title": "optional string",
            },
            "file_organize": {
                "source_path": "required string (~/VoxeraOS/notes/ scope)",
                "destination_dir": "required string (~/VoxeraOS/notes/ scope)",
                "mode": "copy | move",
                "overwrite": "boolean (default false)",
                "delete_original": "boolean (default false)",
            },
            "steps": "optional array of {skill_id, args} for direct bounded file skill routing",
        },
        "guidance_base_shape": guidance.base_shape,
        "guidance_examples": guidance.examples,
    }
    if enrichment_context is not None:
        context_payload["enrichment_context"] = enrichment_context
    return [
        {"role": "system", "content": VERA_PREVIEW_BUILDER_PROMPT},
        {
            "role": "user",
            "content": json.dumps(context_payload, ensure_ascii=False),
        },
    ]


def _extract_hidden_compiler_decision(text: str) -> HiddenCompilerDecision | None:
    parsed, _ = recover_json_object(text)
    if not isinstance(parsed, dict):
        return None
    try:
        return HiddenCompilerDecision.from_payload(parsed)
    except ValueError:
        return None


def _apply_preview_patch(
    *,
    active_preview: dict[str, Any] | None,
    patch: dict[str, Any],
) -> dict[str, Any] | None:
    if active_preview is None:
        return None
    merged: dict[str, Any] = dict(active_preview)
    for key, value in patch.items():
        if key == "write_file" and isinstance(value, dict):
            current = merged.get("write_file")
            if isinstance(current, dict):
                merged["write_file"] = {**current, **value}
                continue
        if key == "enqueue_child" and isinstance(value, dict):
            current = merged.get("enqueue_child")
            if isinstance(current, dict):
                merged["enqueue_child"] = {**current, **value}
                continue
        merged[key] = value
    return merged


async def generate_preview_builder_update(
    *,
    turns: list[dict[str, str]],
    user_message: str,
    active_preview: dict[str, Any] | None,
    enrichment_context: dict[str, Any] | None = None,
    investigation_context: dict[str, Any] | None = None,
    recent_assistant_artifacts: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    cfg = load_app_config()
    api_key_ref = None
    if cfg.brain:
        for key in ("primary", "fallback", "fast", "reasoning"):
            provider = cfg.brain.get(key)
            if provider is not None and provider.api_key_ref:
                api_key_ref = provider.api_key_ref
                break

    attempts: list[GeminiBrain] = []
    if api_key_ref:
        attempts.append(GeminiBrain(model=PREVIEW_BUILDER_MODEL, api_key_ref=api_key_ref))
        if PREVIEW_BUILDER_FALLBACK_MODEL != PREVIEW_BUILDER_MODEL:
            attempts.append(
                GeminiBrain(model=PREVIEW_BUILDER_FALLBACK_MODEL, api_key_ref=api_key_ref)
            )

    recent_user_messages = [
        str(turn.get("text") or "")
        for turn in turns[-MAX_SESSION_TURNS:]
        if str(turn.get("role") or "").strip().lower() == "user"
    ]
    recent_assistant_messages = [
        str(turn.get("text") or "")
        for turn in turns[-MAX_SESSION_TURNS:]
        if str(turn.get("role") or "").strip().lower() == "assistant"
    ]

    deterministic_preview = maybe_draft_job_payload(
        user_message,
        active_preview=active_preview,
        recent_user_messages=recent_user_messages,
        enrichment_context=enrichment_context,
        investigation_context=investigation_context,
        recent_assistant_messages=recent_assistant_messages,
        recent_assistant_artifacts=(
            recent_assistant_artifacts
            if recent_assistant_artifacts is not None
            else collect_recent_saveable_assistant_artifacts(recent_assistant_messages)
        ),
    )

    if not attempts:
        if deterministic_preview is None:
            return None
        try:
            return normalize_preview_payload(deterministic_preview)
        except Exception:
            return None

    messages = _build_preview_builder_messages(
        turns=turns,
        user_message=user_message,
        active_preview=active_preview,
        enrichment_context=enrichment_context,
    )

    for brain in attempts:
        try:
            response = await brain.generate(messages, tools=[])
        except Exception:
            continue
        decision = _extract_hidden_compiler_decision(str(response.text or ""))
        if decision is None:
            continue
        candidate: dict[str, Any] | None = None
        if decision.action == "no_change":
            if deterministic_preview is not None:
                try:
                    return normalize_preview_payload(deterministic_preview)
                except Exception:
                    return active_preview
            return active_preview
        if decision.action == "replace_preview":
            candidate = decision.updated_preview
        elif decision.action == "patch_preview" and decision.patch is not None:
            candidate = _apply_preview_patch(active_preview=active_preview, patch=decision.patch)

        if candidate is None:
            if deterministic_preview is None:
                return active_preview
            try:
                return normalize_preview_payload(deterministic_preview)
            except Exception:
                return active_preview

        try:
            return normalize_preview_payload(candidate)
        except Exception:
            if deterministic_preview is None:
                return active_preview
            try:
                return normalize_preview_payload(deterministic_preview)
            except Exception:
                return active_preview

    if deterministic_preview is None:
        return active_preview
    try:
        return normalize_preview_payload(deterministic_preview)
    except Exception:
        return active_preview


def _create_brain(provider: Any) -> OpenAICompatBrain | GeminiBrain:
    if provider.type == "openai_compat":
        return OpenAICompatBrain(
            model=provider.model,
            base_url=provider.base_url or "https://openrouter.ai/api/v1",
            api_key_ref=provider.api_key_ref,
            extra_headers=provider.extra_headers,
        )
    if provider.type == "gemini":
        return GeminiBrain(model=provider.model, api_key_ref=provider.api_key_ref)
    raise ValueError(f"unsupported provider type: {provider.type}")


async def generate_vera_reply(
    *,
    turns: list[dict[str, str]],
    user_message: str,
    code_draft: bool = False,
    writing_draft: bool = False,
    weather_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = load_app_config()
    web_cfg = cfg.web_investigation

    weather_reply = await maybe_handle_weather_turn(
        user_message=user_message,
        weather_context=weather_context,
        code_draft=code_draft,
        writing_draft=writing_draft,
        lookup_weather=_lookup_live_weather,
        lookup_weather_followup=lambda location_query, followup_kind: _lookup_live_weather(
            location_query,
            followup_kind=followup_kind,
        ),
        is_weather_investigation_request_hook=_is_weather_investigation_request,
        extract_weather_followup_kind_hook=_extract_weather_followup_kind,
        is_weather_question_hook=_service_weather_question,
        extract_weather_location_from_message_hook=_service_extract_weather_location_from_message,
        weather_followup_is_active_hook=_weather_followup_is_active,
        weather_context_has_pending_lookup_hook=_weather_context_has_pending_lookup,
        weather_context_is_waiting_for_location_hook=_weather_context_is_waiting_for_location,
        normalize_weather_location_candidate_hook=_normalize_weather_location_candidate,
        weather_answer_for_followup_hook=_weather_answer_for_followup,
    )
    if weather_reply is not None:
        return weather_reply

    investigation_reply = await maybe_handle_investigation_turn(
        user_message=user_message,
        web_cfg=web_cfg,
        is_informational_web_query_hook=_is_informational_web_query,
        normalize_web_query_hook=_normalize_web_query,
        format_web_investigation_answer_hook=_format_web_investigation_answer,
        build_structured_investigation_results_hook=(
            lambda query, results: _build_structured_investigation_results(
                query=query,
                results=results,
            )
        ),
        brave_client_factory=BraveSearchClient,
    )
    if investigation_reply is not None:
        return investigation_reply

    attempts: list[tuple[str, Any]] = []
    for key in ("primary", "fallback"):
        provider = cfg.brain.get(key) if cfg.brain else None
        if provider is not None:
            attempts.append((key, provider))

    if not attempts:
        return {
            "answer": (
                "I’m in conversation-only mode right now because no model provider is configured. "
                "I can still help draft a VoxeraOS job request preview, but I cannot execute anything here."
            ),
            "status": "degraded_unavailable",
        }

    messages = build_vera_messages(
        turns=turns,
        user_message=user_message,
        code_draft=code_draft,
        writing_draft=writing_draft,
    )
    last_reason = "UNKNOWN"
    for name, provider in attempts:
        try:
            brain = _create_brain(provider)
            response = await brain.generate(messages, tools=[])
            text = str(response.text or "").strip()
            if text:
                return {"answer": text, "status": f"ok:{name}"}
        except Exception as exc:
            last_reason = classify_fallback_reason(exc)
            continue

    return {
        "answer": (
            "I couldn’t reach the current model backend, so I’m staying in safe preview mode. "
            "I can help you shape a VoxeraOS queue job request, but nothing has been executed."
        ),
        "status": f"degraded_error:{last_reason}",
    }
