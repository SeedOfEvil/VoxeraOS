from __future__ import annotations

import asyncio
import re
import subprocess  # noqa: F401 (re-export so tests monkeypatching panel.app.subprocess.run still drive queue_mutation_bridge)
import sys  # noqa: F401 (re-export so tests asserting panel.app.sys.executable still resolve)
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..audit import log
from ..config import load_config as load_runtime_config
from ..health_semantics import build_health_semantic_sections
from ..version import get_version
from . import routes_assistant as _routes_assistant
from .auth_enforcement import _operator_credentials  # noqa: F401 (re-export for contract tests)
from .auth_enforcement import require_mutation_guard as _require_mutation_guard
from .auth_enforcement import require_operator_basic_auth as _require_operator_basic_auth
from .helpers import coerce_int as _coerce_int
from .helpers import request_value as _request_value
from .job_detail_sections import build_job_detail_payload as _build_job_detail_payload_impl
from .job_detail_sections import build_job_progress_payload as _build_job_progress_payload_impl
from .job_presentation import job_artifact_flags as _job_artifact_flags_impl
from .job_presentation import (
    operator_outcome_summary as _operator_outcome_summary,  # noqa: F401 (re-export for tests/test_panel.py::test_operator_outcome_summary_semantics_precedence_characterization)
)
from .queue_mutation_bridge import (
    run_queue_hygiene_command,
    write_hygiene_result,
    write_panel_mission_job,
    write_queue_job,
)
from .routes_automations import register_automation_routes
from .routes_bundle import register_bundle_routes
from .routes_home import register_home_routes
from .routes_hygiene import register_hygiene_routes
from .routes_jobs import register_job_routes
from .routes_missions import register_mission_routes
from .routes_queue_control import register_queue_control_routes
from .routes_recovery import register_recovery_routes
from .security_health_helpers import (
    auth_setup_banner as _auth_setup_banner_impl,
)
from .security_health_helpers import (
    health_queue_root as _health_queue_root_impl,
)
from .security_health_helpers import (
    panel_security_counter_incr as _panel_security_counter_incr_impl,
)
from .security_health_helpers import (
    panel_security_snapshot as _panel_security_snapshot_impl,
)

app = FastAPI(title="Voxera Panel", version=get_version())

HERE = Path(__file__).resolve().parent
templates = Environment(
    loader=FileSystemLoader(str(HERE / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

APPROVALS: list[dict[str, Any]] = []
MISSION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
load_app_config = _routes_assistant.load_app_config
enqueue_assistant_question = _routes_assistant.enqueue_assistant_question
_assistant_stalled_degraded_reason = _routes_assistant._assistant_stalled_degraded_reason
_create_panel_assistant_brain = _routes_assistant._create_panel_assistant_brain
_persist_degraded_assistant_result = _routes_assistant._persist_degraded_assistant_result


async def _generate_degraded_assistant_answer_async(
    question: str,
    context: dict[str, Any],
    *,
    thread_turns: list[dict[str, Any]],
    degraded_reason: str,
) -> dict[str, Any]:
    _routes_assistant.load_app_config = load_app_config
    _routes_assistant._create_panel_assistant_brain = _create_panel_assistant_brain
    return await _routes_assistant._generate_degraded_assistant_answer_async(
        question,
        context,
        thread_turns=thread_turns,
        degraded_reason=degraded_reason,
    )


def _generate_degraded_assistant_answer(
    question: str,
    context: dict[str, Any],
    *,
    thread_turns: list[dict[str, Any]],
    degraded_reason: str,
) -> dict[str, Any]:
    return asyncio.run(
        _generate_degraded_assistant_answer_async(
            question,
            context,
            thread_turns=thread_turns,
            degraded_reason=degraded_reason,
        )
    )


def _enqueue_assistant_question(*args: Any, **kwargs: Any) -> tuple[str, str]:
    return enqueue_assistant_question(*args, **kwargs)


ERROR_MESSAGES = {
    "goal_required": "Goal is required when queue type is goal.",
    "mission_id_required": "Mission ID is required.",
    "queue_kind_invalid": "Queue type must be either goal or mission.",
    "mission_id_invalid": "Mission ID must be 2-64 characters and use lowercase letters, numbers, '_' or '-'.",
    "steps_json_invalid": "Steps JSON must be valid JSON.",
    "steps_json_not_list": "Steps JSON must decode to a JSON list.",
    "mission_schema_invalid": "Mission template failed schema validation.",
    "get_mutation_disabled": "GET mutation endpoints are disabled; submit the form normally.",
    "panel_prompt_required": "Prompt / Goal is required.",
}

FLASH_MESSAGES = {
    "approved": "Approval granted.",
    "approved_always": "Approval granted and remembered for matching scope.",
    "denied": "Approval denied.",
    "canceled": "Job moved to canceled/.",
    "retried": "Job re-enqueued into inbox/.",
    "deleted": "Terminal job deleted.",
    "cancel_not_found": "Cannot cancel: job was not found in active queue buckets.",
    "cannot_cancel_terminal": "Cannot cancel terminal jobs. Use retry/delete for failed/canceled/done.",
    "approval_not_found": "Approval/job reference was not found.",
    "approval_invalid": "Approval request was invalid.",
    "health_reset_current_state": "Health current state reset completed.",
    "health_reset_recent_history": "Health recent history reset completed.",
    "health_reset_current_and_recent": "Health current state and recent history reset completed.",
    "health_reset_historical_counters": "Historical counter reset completed.",
}

CSRF_COOKIE = "voxera_panel_csrf"
CSRF_FORM_KEY = "csrf_token"
_RECOVERY_ZIP_MAX_FILES = 5000
_RECOVERY_ZIP_MAX_TOTAL_BYTES = 250 * 1024 * 1024


def _settings():
    return load_runtime_config()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _queue_root() -> Path:
    return _settings().queue_root


def _health_queue_root() -> Path | None:
    # Thin wrapper over the extracted security_health_helpers entry point so
    # route modules (``health_queue_root=_health_queue_root``) and
    # ``auth_enforcement`` (which reaches back via ``panel.app._health_queue_root``)
    # continue to resolve to the same behavior.
    return _health_queue_root_impl(_queue_root())


def _missions_dir() -> Path:
    return Path.home() / ".config" / "voxera" / "missions"


def _allow_get_mutations() -> bool:
    return _settings().panel_enable_get_mutations


def _panel_security_counter_incr(key: str, *, last_error: str | None = None) -> None:
    # Thin wrapper so route-registration callbacks keep the same
    # ``(key, *, last_error=None)`` signature while the counter logic
    # lives in ``security_health_helpers.panel_security_counter_incr``.
    _panel_security_counter_incr_impl(_health_queue_root(), key, last_error=last_error)


def _panel_security_snapshot() -> dict[str, Any]:
    # Thin wrapper so route-registration callbacks keep the same
    # ``() -> dict`` signature while the snapshot read lives in
    # ``security_health_helpers.panel_security_snapshot``.
    return _panel_security_snapshot_impl(_health_queue_root())


def _auth_setup_banner() -> dict[str, str] | None:
    # Thin wrapper so route-registration callbacks keep the same
    # ``() -> dict | None`` signature while the banner decision logic
    # lives in ``security_health_helpers.auth_setup_banner``.
    return _auth_setup_banner_impl(_settings())


def _format_ts(ts_ms: int | None) -> str:
    if ts_ms is None or ts_ms <= 0:
        return "—"
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _format_ts_seconds(ts_s: float | None) -> str:
    if ts_s is None or ts_s <= 0:
        return "—"
    return datetime.fromtimestamp(ts_s, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _format_age(age_s: int | None) -> str:
    if age_s is None or age_s < 0:
        return "—"
    if age_s < 60:
        return f"{age_s}s"
    minutes, seconds = divmod(age_s, 60)
    if seconds:
        return f"{minutes}m {seconds}s"
    return f"{minutes}m"


def _history_value(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    return text or "-"


def _history_pair(value: Any, ts_label: str) -> str:
    val = _history_value(value)
    ts = ts_label.strip() if ts_label else "-"
    if val == "-" and ts in {"-", "—"}:
        return "-"
    return f"{val} @ {ts}"


def _daemon_health_view(health: dict[str, Any]) -> dict[str, Any]:
    lock_raw = health.get("lock_status")
    lock: dict[str, Any] = lock_raw if isinstance(lock_raw, dict) else {}
    lock_state = str(health.get("lock_state") or "").strip().lower()
    lock_status = str(lock.get("status") or "").strip().lower()
    if lock_status not in {"held", "stale", "clear"}:
        if lock_state in {"active", "locked_by_other"}:
            lock_status = "held"
        elif lock_state in {"stale", "reclaimed"}:
            lock_status = "stale"
        else:
            lock_status = "clear"

    lock_pid = _coerce_int(lock.get("pid"))
    if lock_pid is None:
        lock_pid = _coerce_int(health.get("lock_holder_pid"))

    fallback_reason = health.get("last_fallback_reason")
    fallback_tier = health.get("last_fallback_to")
    fallback_ts = _coerce_int(health.get("last_fallback_ts_ms"))
    has_fallback = any([fallback_reason, fallback_tier, fallback_ts])

    startup_recovery = health.get("last_startup_recovery")
    if isinstance(startup_recovery, dict):
        recovery_counts = startup_recovery.get("counts")
        recovery_ts = _coerce_int(startup_recovery.get("ts_ms"))
    else:
        recovery_counts = health.get("last_startup_recovery_counts")
        recovery_ts = _coerce_int(health.get("last_startup_recovery_ts"))
    counts = recovery_counts if isinstance(recovery_counts, dict) else {}
    recovery_job_count = _coerce_int(counts.get("jobs_failed")) or 0
    orphan_count = (_coerce_int(counts.get("orphan_approvals_quarantined")) or 0) + (
        _coerce_int(counts.get("orphan_state_files_quarantined")) or 0
    )
    has_recovery = any([recovery_job_count, orphan_count, recovery_ts])

    shutdown_outcome = str(health.get("last_shutdown_outcome") or "").strip() or "unknown"
    shutdown_ts_raw = health.get("last_shutdown_ts")
    shutdown_ts = float(shutdown_ts_raw) if isinstance(shutdown_ts_raw, (int, float)) else None
    shutdown_reason = str(health.get("last_shutdown_reason") or "").strip() or "—"
    shutdown_job = str(health.get("last_shutdown_job") or "").strip() or "—"

    stale_age_s = _coerce_int(lock.get("stale_age_s"))

    return {
        "lock_status": lock_status,
        "lock_pid": lock_pid,
        "lock_stale_age_s": stale_age_s,
        "lock_stale_age_label": _format_age(stale_age_s),
        "last_brain_fallback": {
            "present": has_fallback,
            "tier": str(fallback_tier or "—"),
            "reason": str(fallback_reason or "—"),
            "ts": _format_ts(fallback_ts),
        },
        "last_startup_recovery": {
            "present": has_recovery,
            "job_count": recovery_job_count,
            "orphan_count": orphan_count,
            "ts": _format_ts(recovery_ts),
        },
        "last_shutdown": {
            "present": shutdown_outcome != "unknown" or shutdown_ts is not None,
            "outcome": shutdown_outcome,
            "ts": _format_ts_seconds(shutdown_ts),
            "reason": shutdown_reason,
            "job": shutdown_job,
        },
        "daemon_state": str(health.get("daemon_state") or "healthy"),
    }


def _performance_stats_view(queue: dict[str, Any], health: dict[str, Any]) -> dict[str, Any]:
    counts_raw = queue.get("counts")
    counts = counts_raw if isinstance(counts_raw, dict) else {}
    grouped = build_health_semantic_sections(
        health,
        queue_context={
            "queue_root": queue.get("queue_root"),
            "health_path": queue.get("health_path"),
            "intake_glob": queue.get("intake_glob"),
            "paused": bool(queue.get("paused", False)),
        },
        lock_status=queue.get("lock_status") if isinstance(queue.get("lock_status"), dict) else {},
        daemon_lock_counters=queue.get("daemon_lock_counters")
        if isinstance(queue.get("daemon_lock_counters"), dict)
        else {},
    )
    current_state = grouped["current_state"]
    recent_history = grouped["recent_history"]
    historical_counters = grouped["historical_counters"]
    shutdown_ts_raw = recent_history.get("last_shutdown_ts")
    shutdown_ts = float(shutdown_ts_raw) if isinstance(shutdown_ts_raw, (int, float)) else None
    fallback = recent_history.get("last_brain_fallback", {})

    return {
        "queue_counts": {
            "inbox": int(counts.get("inbox", 0) or 0),
            "pending": int(counts.get("pending", 0) or 0),
            "pending_approvals": int(counts.get("pending_approvals", 0) or 0),
            "done": int(counts.get("done", 0) or 0),
            "failed": int(counts.get("failed", 0) or 0),
            "canceled": int(counts.get("canceled", 0) or 0),
        },
        "current_state": current_state,
        "recent_history": {
            "last_fallback_line": (
                "-"
                if not isinstance(fallback, dict)
                or not any(
                    [
                        fallback.get("reason"),
                        fallback.get("from"),
                        fallback.get("to"),
                        fallback.get("ts_ms"),
                    ]
                )
                else (
                    f"{_history_value(fallback.get('reason'))} "
                    f"({_history_value(fallback.get('from'))} → {_history_value(fallback.get('to'))}) "
                    f"@ {_format_ts(_coerce_int(fallback.get('ts_ms')))}"
                )
            ),
            "last_error_line": _history_pair(
                recent_history.get("last_error"),
                _format_ts(_coerce_int(recent_history.get("last_error_ts_ms"))),
            ),
            "last_shutdown_line": (
                "-"
                if not any(
                    [
                        recent_history.get("last_shutdown_outcome"),
                        recent_history.get("last_shutdown_reason"),
                        recent_history.get("last_shutdown_job"),
                        recent_history.get("last_shutdown_ts"),
                    ]
                )
                else (
                    f"{_history_value(recent_history.get('last_shutdown_outcome'))} / "
                    f"{_history_value(recent_history.get('last_shutdown_reason'))} / "
                    f"{_history_value(recent_history.get('last_shutdown_job'))} @ "
                    f"{_format_ts_seconds(shutdown_ts)}"
                )
            ),
            "degraded_since_ts": _history_value(recent_history.get("degraded_since_ts")),
            "brain_backoff_last_applied_s": int(
                recent_history.get("brain_backoff_last_applied_s", 0) or 0
            ),
            "brain_backoff_last_applied_ts": _history_value(
                recent_history.get("brain_backoff_last_applied_ts")
            ),
        },
        "historical_counters": {
            "panel_auth_invalid": int(historical_counters.get("panel_auth_invalid", 0) or 0),
            "panel_401_count": int(historical_counters.get("panel_401_count", 0) or 0),
            "panel_403_count": int(historical_counters.get("panel_403_count", 0) or 0),
            "panel_429_count": int(historical_counters.get("panel_429_count", 0) or 0),
            "panel_csrf_missing": int(historical_counters.get("panel_csrf_missing", 0) or 0),
            "panel_csrf_invalid": int(historical_counters.get("panel_csrf_invalid", 0) or 0),
            "panel_mutation_allowed": int(
                historical_counters.get("panel_mutation_allowed", 0) or 0
            ),
            "brain_fallback_count": int(historical_counters.get("brain_fallback_count", 0) or 0),
            "brain_fallback_reason_timeout": int(
                historical_counters.get("brain_fallback_reason_timeout", 0) or 0
            ),
            "brain_fallback_reason_auth": int(
                historical_counters.get("brain_fallback_reason_auth", 0) or 0
            ),
            "brain_fallback_reason_rate_limit": int(
                historical_counters.get("brain_fallback_reason_rate_limit", 0) or 0
            ),
            "brain_fallback_reason_malformed": int(
                historical_counters.get("brain_fallback_reason_malformed", 0) or 0
            ),
            "brain_fallback_reason_network": int(
                historical_counters.get("brain_fallback_reason_network", 0) or 0
            ),
            "brain_fallback_reason_unknown": int(
                historical_counters.get("brain_fallback_reason_unknown", 0) or 0
            ),
        },
    }


def _require_operator_auth_from_request(request: Request) -> None:
    # Thin wrapper over the extracted auth_enforcement entry point so
    # existing route-registration callback names (passed by keyword to each
    # register_* helper below) continue to resolve to the same behavior.
    _require_operator_basic_auth(request)


def _enforce_get_mutations_enabled() -> None:
    if not _allow_get_mutations():
        raise HTTPException(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            detail="GET mutation endpoints are disabled",
        )


def _write_queue_job(payload: dict[str, Any]) -> str:
    # Thin wrapper so route-registration callbacks keep the same
    # (payload,) signature while the bridge logic lives in
    # queue_mutation_bridge.write_queue_job.
    return write_queue_job(_queue_root(), payload)


def _write_panel_mission_job(*, prompt: str, approval_required: bool) -> tuple[str, str]:
    # Thin wrapper so route-registration callbacks keep the same
    # (*, prompt, approval_required) signature while the bridge logic
    # lives in queue_mutation_bridge.write_panel_mission_job.
    return write_panel_mission_job(
        _queue_root(),
        prompt=prompt,
        approval_required=approval_required,
    )


def _run_queue_hygiene_command(queue_root: Path, args: list[str]) -> dict[str, Any]:
    # Thin wrapper so route-registration callbacks keep the same
    # (queue_root, args) signature while the bridge logic lives in
    # queue_mutation_bridge.run_queue_hygiene_command.
    return run_queue_hygiene_command(queue_root, args)


def _write_hygiene_result(queue_root: Path, key: str, result: dict[str, Any]) -> None:
    # Thin wrapper that forwards to queue_mutation_bridge.write_hygiene_result
    # and passes the ``_now_ms`` module-level callable so tests that
    # monkeypatch ``panel.app._now_ms`` still drive the ``updated_at_ms``
    # stamp exactly as before.
    write_hygiene_result(queue_root, key, result, now_ms=_now_ms)


def _job_detail_payload(queue_root: Path, job_id: str) -> dict[str, Any]:
    # Thin wrapper over the extracted job_detail_sections entry point so
    # ``register_job_routes(job_detail_payload=_job_detail_payload)`` keeps
    # the same ``(queue_root, job_id) -> dict`` signature while the builder
    # logic lives in ``job_detail_sections.build_job_detail_payload``.
    return _build_job_detail_payload_impl(queue_root, job_id)


def _job_progress_payload(queue_root: Path, job_id: str) -> dict[str, Any]:
    # Thin wrapper over the extracted job_detail_sections entry point so
    # ``register_job_routes(job_progress_payload=_job_progress_payload)``
    # keeps the same ``(queue_root, job_id) -> dict`` signature while the
    # builder logic lives in ``job_detail_sections.build_job_progress_payload``.
    return _build_job_progress_payload_impl(queue_root, job_id)


def _job_artifact_flags(queue_root: Path, job_id: str) -> dict[str, bool]:
    # Thin wrapper over the extracted job_presentation entry point so
    # ``register_job_routes(job_artifact_flags=_job_artifact_flags)`` keeps
    # the same ``(queue_root, job_id) -> dict[str, bool]`` signature while
    # the helper logic lives in ``job_presentation.job_artifact_flags``.
    return _job_artifact_flags_impl(queue_root, job_id)


def _last_activity(artifacts_dir: Path) -> str:
    actions = artifacts_dir / "actions.jsonl"
    if not actions.exists():
        return ""
    lines = actions.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in reversed(lines):
        if line.strip():
            return line[:180]
    return ""


def _job_ref_bucket(row: dict[str, Any]) -> str:
    bucket = str(row.get("bucket") or "")
    if bucket == "approvals":
        return "pending/approvals"
    return bucket


def _build_activity(
    audit_events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    active: dict[str, dict[str, Any]] = {}
    recent: list[dict[str, Any]] = []
    for event in audit_events:
        event_name = str(event.get("event", ""))
        job = Path(str(event.get("job", ""))).name if event.get("job") else ""
        mission = str(event.get("mission") or "")
        goal = str(event.get("goal") or "")

        if event_name == "queue_job_started" and job:
            active[job] = {
                "job": job,
                "mission": mission,
                "goal": goal,
                "state": "running",
            }
        if event_name in {"queue_job_done", "queue_job_failed"} and job:
            active.pop(job, None)

        if event_name.startswith("queue_") or event_name.startswith("mission_"):
            recent.append(
                {
                    "event": event_name,
                    "job": job,
                    "mission": mission,
                    "step": event.get("step", ""),
                }
            )

    return list(active.values())[:8], list(reversed(recent[-12:]))


register_home_routes(
    app,
    templates=templates,
    csrf_cookie=CSRF_COOKIE,
    approvals=APPROVALS,
    error_messages=ERROR_MESSAGES,
    allow_get_mutations=_allow_get_mutations,
    queue_root=_queue_root,
    build_activity=_build_activity,
    daemon_health_view=_daemon_health_view,
    performance_stats_view=_performance_stats_view,
    panel_security_snapshot=_panel_security_snapshot,
    auth_setup_banner=_auth_setup_banner,
    enforce_get_mutations_enabled=_enforce_get_mutations_enabled,
    require_operator_auth_from_request=_require_operator_auth_from_request,
    require_mutation_guard=_require_mutation_guard,
    write_queue_job=_write_queue_job,
)

register_job_routes(
    app,
    templates=templates,
    csrf_cookie=CSRF_COOKIE,
    flash_messages=FLASH_MESSAGES,
    queue_root=_queue_root,
    require_mutation_guard=_require_mutation_guard,
    panel_security_counter_incr=_panel_security_counter_incr,
    job_ref_bucket=_job_ref_bucket,
    job_artifact_flags=_job_artifact_flags,
    last_activity=_last_activity,
    job_detail_payload=_job_detail_payload,
    job_progress_payload=_job_progress_payload,
    auth_setup_banner=_auth_setup_banner,
)


register_recovery_routes(
    app,
    templates=templates,
    queue_root=_queue_root,
    require_operator_auth_from_request=_require_operator_auth_from_request,
    health_queue_root=_health_queue_root,
    recovery_zip_max_files=_RECOVERY_ZIP_MAX_FILES,
    recovery_zip_max_total_bytes=_RECOVERY_ZIP_MAX_TOTAL_BYTES,
)

register_hygiene_routes(
    app,
    templates=templates,
    csrf_cookie=CSRF_COOKIE,
    flash_messages=FLASH_MESSAGES,
    queue_root=_queue_root,
    health_queue_root=_health_queue_root,
    require_operator_auth_from_request=_require_operator_auth_from_request,
    require_mutation_guard=_require_mutation_guard,
    run_queue_hygiene_command=_run_queue_hygiene_command,
    write_hygiene_result=_write_hygiene_result,
    now_ms=_now_ms,
    audit_log=lambda event: log(event),
)


_routes_assistant.register_assistant_routes(
    app,
    templates=templates,
    csrf_cookie=CSRF_COOKIE,
    queue_root=_queue_root,
    require_operator_auth_from_request=_require_operator_auth_from_request,
    require_mutation_guard=_require_mutation_guard,
    request_value=_request_value,
    enqueue_assistant_question_fn=_enqueue_assistant_question,
    assistant_stalled_degraded_reason_fn=_assistant_stalled_degraded_reason,
    generate_degraded_assistant_answer_fn=_generate_degraded_assistant_answer,
    generate_degraded_assistant_answer_async_fn=_generate_degraded_assistant_answer_async,
    persist_degraded_assistant_result_fn=_persist_degraded_assistant_result,
)

register_mission_routes(
    app,
    enforce_get_mutations_enabled=_enforce_get_mutations_enabled,
    require_operator_auth_from_request=_require_operator_auth_from_request,
    require_mutation_guard=_require_mutation_guard,
    request_value=_request_value,
    write_panel_mission_job=_write_panel_mission_job,
)

register_bundle_routes(
    app,
    queue_root=_queue_root,
    require_operator_auth_from_request=_require_operator_auth_from_request,
)

register_queue_control_routes(
    app,
    queue_root=_queue_root,
    require_mutation_guard=_require_mutation_guard,
)

register_automation_routes(
    app,
    templates=templates,
    csrf_cookie=CSRF_COOKIE,
    queue_root=_queue_root,
    require_mutation_guard=_require_mutation_guard,
    panel_security_counter_incr=_panel_security_counter_incr,
    auth_setup_banner=_auth_setup_banner,
    format_ts_ms=_format_ts,
)
