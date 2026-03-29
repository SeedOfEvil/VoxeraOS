from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..audit import log, tail
from ..config import load_config as load_runtime_config
from ..core.queue_inspect import lookup_job, queue_snapshot
from ..core.queue_job_intent import enrich_queue_job_payload
from ..core.queue_result_consumers import resolve_structured_execution
from ..health import increment_health_counter, read_health_snapshot, update_health_snapshot
from ..health_semantics import build_health_semantic_sections
from ..version import get_version
from . import routes_assistant as _routes_assistant
from .helpers import coerce_int as _coerce_int
from .helpers import request_value as _request_value
from .job_presentation import evidence_summary_rows as _evidence_summary_rows
from .job_presentation import job_artifact_inventory as _job_artifact_inventory
from .job_presentation import job_context_summary as _job_context_summary
from .job_presentation import job_recent_timeline as _job_recent_timeline
from .job_presentation import operator_outcome_summary as _operator_outcome_summary
from .job_presentation import policy_rationale_rows as _policy_rationale_rows
from .job_presentation import why_stopped_rows as _why_stopped_rows
from .routes_bundle import register_bundle_routes
from .routes_home import register_home_routes
from .routes_hygiene import register_hygiene_routes
from .routes_jobs import register_job_routes
from .routes_missions import register_mission_routes
from .routes_queue_control import register_queue_control_routes
from .routes_recovery import register_recovery_routes

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
PANEL_AUTH_FAIL_THRESHOLD = 10
PANEL_AUTH_WINDOW_S = 60
PANEL_AUTH_LOCKOUT_S = 60
_PANEL_AUTH_PRUNE_TTL_MS = 10 * 60 * 1000
_PANEL_AUTH_MAX_TRACKED_IPS = 200
_RECOVERY_ZIP_MAX_FILES = 5000
_RECOVERY_ZIP_MAX_TOTAL_BYTES = 250 * 1024 * 1024


class _RequestUrlLike(Protocol):
    @property
    def path(self) -> str: ...


class _RequestClientLike(Protocol):
    @property
    def host(self) -> str: ...


class _PanelSecurityRequestLike(Protocol):
    @property
    def url(self) -> _RequestUrlLike: ...

    @property
    def method(self) -> str: ...

    @property
    def client(self) -> _RequestClientLike | None: ...


def _settings():
    return load_runtime_config()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _client_ip(request: Request) -> str:
    trust_proxy = os.getenv("VOXERA_PANEL_TRUST_PROXY", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _prune_panel_auth_maps(store: dict[str, Any], *, now_ms: int) -> None:
    cutoff_ms = now_ms - _PANEL_AUTH_PRUNE_TTL_MS

    failures_raw = store.get("failures_by_ip")
    failures = failures_raw if isinstance(failures_raw, dict) else {}
    for ip, row in list(failures.items()):
        if not isinstance(row, dict):
            failures.pop(ip, None)
            continue
        if int(row.get("last_ts_ms", 0) or 0) < cutoff_ms:
            failures.pop(ip, None)
    if len(failures) > _PANEL_AUTH_MAX_TRACKED_IPS:
        ordered = sorted(failures.items(), key=lambda item: int(item[1].get("last_ts_ms", 0) or 0))
        for ip, _ in ordered[: len(failures) - _PANEL_AUTH_MAX_TRACKED_IPS]:
            failures.pop(ip, None)

    lockouts_raw = store.get("lockouts_by_ip")
    lockouts = lockouts_raw if isinstance(lockouts_raw, dict) else {}
    for ip, row in list(lockouts.items()):
        if not isinstance(row, dict):
            lockouts.pop(ip, None)
            continue
        last_event_ts = int(row.get("last_event_ts_ms", 0) or 0)
        until_ts = int(row.get("until_ts_ms", 0) or 0)
        if max(last_event_ts, until_ts) < cutoff_ms:
            lockouts.pop(ip, None)
    if len(lockouts) > _PANEL_AUTH_MAX_TRACKED_IPS:
        ordered = sorted(
            lockouts.items(), key=lambda item: int(item[1].get("last_event_ts_ms", 0) or 0)
        )
        for ip, _ in ordered[: len(lockouts) - _PANEL_AUTH_MAX_TRACKED_IPS]:
            lockouts.pop(ip, None)

    store["failures_by_ip"] = failures
    store["lockouts_by_ip"] = lockouts


def _panel_auth_state_update(
    queue_root: Path | None,
    *,
    ip: str,
    now_ms: int,
    auth_success: bool,
) -> dict[str, Any]:
    ip_key = ip.strip() or "unknown"
    window_ms = PANEL_AUTH_WINDOW_S * 1000
    lockout_ms = PANEL_AUTH_LOCKOUT_S * 1000

    def _apply(payload: dict[str, Any]) -> dict[str, Any]:
        panel_auth_raw = payload.get("panel_auth")
        panel_auth = panel_auth_raw if isinstance(panel_auth_raw, dict) else {}
        _prune_panel_auth_maps(panel_auth, now_ms=now_ms)

        failures = panel_auth.get("failures_by_ip", {})
        lockouts = panel_auth.get("lockouts_by_ip", {})

        if auth_success:
            failures.pop(ip_key, None)
            lockout = lockouts.get(ip_key)
            if isinstance(lockout, dict) and now_ms >= int(lockout.get("until_ts_ms", 0) or 0):
                lockouts.pop(ip_key, None)
        else:
            row = failures.get(ip_key)
            if not isinstance(row, dict):
                row = {"count": 0, "first_ts_ms": now_ms, "last_ts_ms": now_ms}
            first_ts = int(row.get("first_ts_ms", now_ms) or now_ms)
            count = int(row.get("count", 0) or 0)
            if now_ms - first_ts > window_ms:
                count = 1
                first_ts = now_ms
            else:
                count += 1
            failures[ip_key] = {"count": count, "first_ts_ms": first_ts, "last_ts_ms": now_ms}
            if count >= PANEL_AUTH_FAIL_THRESHOLD:
                prev = lockouts.get(ip_key)
                prev_count = int(prev.get("count", 0) or 0) if isinstance(prev, dict) else 0
                lockouts[ip_key] = {
                    "until_ts_ms": now_ms + lockout_ms,
                    "count": prev_count + 1,
                    "last_event_ts_ms": now_ms,
                }

        panel_auth["failures_by_ip"] = failures
        panel_auth["lockouts_by_ip"] = lockouts
        payload["panel_auth"] = panel_auth
        payload["updated_at_ms"] = now_ms
        return payload

    return update_health_snapshot(queue_root, _apply)


def _panel_auth_state_prune(queue_root: Path | None, *, now_ms: int) -> dict[str, Any]:
    def _apply(payload: dict[str, Any]) -> dict[str, Any]:
        panel_auth_raw = payload.get("panel_auth")
        panel_auth = panel_auth_raw if isinstance(panel_auth_raw, dict) else {}
        _prune_panel_auth_maps(panel_auth, now_ms=now_ms)
        payload["panel_auth"] = panel_auth
        payload["updated_at_ms"] = now_ms
        return payload

    return update_health_snapshot(queue_root, _apply)


def _active_lockout_until_ms(*, queue_root: Path | None, ip: str, now_ms: int) -> int | None:
    payload = _panel_auth_state_prune(queue_root, now_ms=now_ms)
    panel_auth_raw = payload.get("panel_auth")
    panel_auth = panel_auth_raw if isinstance(panel_auth_raw, dict) else {}
    lockouts_raw = panel_auth.get("lockouts_by_ip")
    lockouts = lockouts_raw if isinstance(lockouts_raw, dict) else {}
    row = lockouts.get(ip.strip() or "unknown")
    if not isinstance(row, dict):
        return None
    until = int(row.get("until_ts_ms", 0) or 0)
    return until if now_ms < until else None


def _queue_root() -> Path:
    return _settings().queue_root


def _health_queue_root() -> Path | None:
    isolated_health = os.getenv("VOXERA_HEALTH_PATH", "").strip()
    if not isolated_health:
        return _queue_root()

    # Keep production/runtime semantics unchanged for explicit queue roots.
    if os.getenv("VOXERA_QUEUE_ROOT", "").strip():
        return _queue_root()

    configured_root = _queue_root().expanduser().resolve()
    repo_operator_root = (Path.cwd() / "notes" / "queue").resolve()
    # Test-only safety net: when panel would target the repo default queue root,
    # route health writes through VOXERA_HEALTH_PATH instead.
    if configured_root == repo_operator_root:
        return None
    return _queue_root()


def _missions_dir() -> Path:
    return Path.home() / ".config" / "voxera" / "missions"


def _allow_get_mutations() -> bool:
    return _settings().panel_enable_get_mutations


def _request_meta(request: _PanelSecurityRequestLike) -> dict[str, Any]:
    return {
        "path": request.url.path,
        "method": request.method,
        "remote": (request.client.host if request.client else "unknown"),
    }


def _log_panel_security_event(
    event: str,
    *,
    request: _PanelSecurityRequestLike,
    reason: str,
    status_code: int,
) -> None:
    meta = _request_meta(request)
    log(
        {
            "event": event,
            "ts_ms": int(time.time() * 1000),
            "path": meta["path"],
            "method": meta["method"],
            "remote": meta["remote"],
            "reason": reason,
            "status_code": status_code,
        }
    )


def _panel_security_counter_incr(key: str, *, last_error: str | None = None) -> None:
    increment_health_counter(_health_queue_root(), key, last_error=last_error)


def _panel_security_snapshot() -> dict[str, Any]:
    payload = read_health_snapshot(_health_queue_root())
    counters = payload.get("counters")
    return counters if isinstance(counters, dict) else {}


def _auth_setup_banner() -> dict[str, str] | None:
    settings = _settings()
    if settings.panel_operator_password not in {None, ""}:
        return None
    config_path_hint = str(settings.config_path.expanduser())
    return {
        "title": "Setup required: panel operator password is not configured.",
        "detail": (
            "Mutation routes require Basic auth. Set VOXERA_PANEL_OPERATOR_PASSWORD in your "
            "user service environment and restart panel + daemon. If VOXERA_LOAD_DOTENV=1, "
            ".env may override file settings."
        ),
        "path_hint": f"Config file: {config_path_hint}",
        "commands": (
            "systemctl --user edit voxera-panel.service\n"
            "# add [Service] Environment=VOXERA_PANEL_OPERATOR_PASSWORD=<set-a-strong-password>\n"
            "systemctl --user daemon-reload\n"
            "systemctl --user restart voxera-panel.service voxera-daemon.service"
        ),
    }


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


def _operator_credentials(request: _PanelSecurityRequestLike) -> tuple[str, str]:
    settings = _settings()
    user = settings.panel_operator_user
    password = settings.panel_operator_password
    if not password:
        _panel_security_counter_incr("panel_401_count", last_error="operator password missing")
        _log_panel_security_event(
            "panel_operator_config_error",
            request=request,
            reason="operator_password_missing",
            status_code=503,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VOXERA_PANEL_OPERATOR_PASSWORD must be set",
        )
    return user, password


def _require_operator_basic_auth(request: Request, authorization: str | None) -> None:
    user, password = _operator_credentials(request)
    now_ms = _now_ms()
    ip = _client_ip(request)
    lockout_until_ms = _active_lockout_until_ms(
        queue_root=_health_queue_root(), ip=ip, now_ms=now_ms
    )
    if lockout_until_ms is not None:
        _panel_security_counter_incr("panel_429_count")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many authentication attempts",
            headers={"Retry-After": str(PANEL_AUTH_LOCKOUT_S)},
        )

    if not authorization:
        _panel_auth_state_update(
            queue_root=_health_queue_root(), ip=ip, now_ms=now_ms, auth_success=False
        )
        _panel_security_counter_incr("panel_401_count", last_error="missing authorization")
        _log_panel_security_event(
            "panel_auth_missing", request=request, reason="missing_authorization", status_code=401
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="operator authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )
    import base64

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "basic" or not token:
        _panel_auth_state_update(
            queue_root=_health_queue_root(), ip=ip, now_ms=now_ms, auth_success=False
        )
        _panel_security_counter_incr(
            "panel_auth_invalid", last_error="invalid authentication scheme"
        )
        _panel_security_counter_incr("panel_401_count")
        _log_panel_security_event(
            "panel_auth_invalid", request=request, reason="invalid_scheme", status_code=401
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid authentication scheme",
            headers={"WWW-Authenticate": "Basic"},
        )
    try:
        decoded = base64.b64decode(token).decode("utf-8")
    except Exception as exc:
        _panel_auth_state_update(
            queue_root=_health_queue_root(), ip=ip, now_ms=now_ms, auth_success=False
        )
        _panel_security_counter_incr(
            "panel_auth_invalid", last_error="invalid authorization header"
        )
        _panel_security_counter_incr("panel_401_count")
        _log_panel_security_event(
            "panel_auth_invalid", request=request, reason="invalid_header", status_code=401
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid authorization header",
            headers={"WWW-Authenticate": "Basic"},
        ) from exc
    got_user, _, got_password = decoded.partition(":")
    if not (
        secrets.compare_digest(got_user, user) and secrets.compare_digest(got_password, password)
    ):
        payload = _panel_auth_state_update(
            queue_root=_health_queue_root(), ip=ip, now_ms=now_ms, auth_success=False
        )
        panel_auth_raw = payload.get("panel_auth")
        panel_auth = panel_auth_raw if isinstance(panel_auth_raw, dict) else {}
        failures_raw = panel_auth.get("failures_by_ip")
        failures = failures_raw if isinstance(failures_raw, dict) else {}
        ip_key = ip.strip() or "unknown"
        failure_row_raw = failures.get(ip_key)
        failure_row = failure_row_raw if isinstance(failure_row_raw, dict) else {}
        lockouts_raw = panel_auth.get("lockouts_by_ip")
        lockouts = lockouts_raw if isinstance(lockouts_raw, dict) else {}
        lockout_row = lockouts.get(ip_key) if isinstance(lockouts.get(ip_key), dict) else None
        if lockout_row is not None and now_ms < int(lockout_row.get("until_ts_ms", 0) or 0):
            attempt_count = int(failure_row.get("count", 0) or 0)
            _panel_security_counter_incr("panel_429_count")
            log(
                {
                    "event": "panel_auth_lockout",
                    "ts_ms": now_ms,
                    "ip": ip,
                    "attempt_count": attempt_count,
                    "window_s": PANEL_AUTH_WINDOW_S,
                    "lockout_s": PANEL_AUTH_LOCKOUT_S,
                }
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="too many authentication attempts",
                headers={"Retry-After": str(PANEL_AUTH_LOCKOUT_S)},
            )

        _panel_security_counter_incr(
            "panel_auth_invalid", last_error="invalid operator credentials"
        )
        _panel_security_counter_incr("panel_401_count")
        _log_panel_security_event(
            "panel_auth_invalid", request=request, reason="invalid_credentials", status_code=401
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid operator credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

    _panel_auth_state_update(
        queue_root=_health_queue_root(), ip=ip, now_ms=now_ms, auth_success=True
    )


async def _require_mutation_guard(request: Request) -> None:
    _require_operator_auth_from_request(request)
    if not _settings().panel_csrf_enabled:
        _panel_security_counter_incr("panel_mutation_allowed")
        _log_panel_security_event(
            "panel_mutation_allowed",
            request=request,
            reason="auth_valid_csrf_disabled",
            status_code=200,
        )
        return
    cookie_token = request.cookies.get(CSRF_COOKIE, "")
    request_token = (request.headers.get("x-csrf-token") or "").strip() or (
        await _request_value(request, CSRF_FORM_KEY, "")
    ).strip()
    if not cookie_token or not request_token:
        _panel_security_counter_incr("panel_403_count", last_error="csrf token missing")
        _panel_security_counter_incr("panel_csrf_missing")
        _log_panel_security_event(
            "panel_csrf_missing", request=request, reason="csrf_token_missing", status_code=403
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="csrf validation failed")
    if not secrets.compare_digest(cookie_token, request_token):
        _panel_security_counter_incr("panel_403_count", last_error="csrf token mismatch")
        _panel_security_counter_incr("panel_csrf_invalid")
        _log_panel_security_event(
            "panel_csrf_invalid", request=request, reason="csrf_token_mismatch", status_code=403
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="csrf validation failed")
    _panel_security_counter_incr("panel_mutation_allowed")
    _log_panel_security_event(
        "panel_mutation_allowed",
        request=request,
        reason="auth_and_csrf_valid",
        status_code=200,
    )


def _require_operator_auth_from_request(request: Request) -> None:
    _require_operator_basic_auth(request, request.headers.get("authorization"))


def _enforce_get_mutations_enabled() -> None:
    if not _allow_get_mutations():
        raise HTTPException(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            detail="GET mutation endpoints are disabled",
        )


def _write_queue_job(payload: dict[str, Any]) -> str:
    queue_root = _queue_root()
    inbox = queue_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    job_id = f"job-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    tmp_path = inbox / f".{job_id}.tmp.json"
    final_path = inbox / f"{job_id}.json"
    enriched = enrich_queue_job_payload(payload, source_lane="panel_queue_create")
    tmp_path.write_text(json.dumps(enriched, indent=2), encoding="utf-8")
    tmp_path.replace(final_path)
    return final_path.name


def _write_panel_mission_job(*, prompt: str, approval_required: bool) -> tuple[str, str]:
    queue_root = _queue_root()
    inbox = queue_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    normalized_prompt = prompt.strip()
    slug = re.sub(r"[^a-z0-9_-]+", "-", normalized_prompt.lower()).strip("-")
    slug = slug[:32] or "mission"
    ts = int(time.time())
    suffix = hashlib.sha1(normalized_prompt.encode("utf-8")).hexdigest()[:6]
    mission_id = re.sub(r"[^a-z0-9_-]+", "-", f"{slug}-{suffix}-{ts}").strip("-")

    payload = enrich_queue_job_payload(
        {
            "id": mission_id,
            "goal": normalized_prompt,
            "approval_required": approval_required,
            "summary": "Panel mission prompt queued for planner",
            "approval_hints": ["manual" if approval_required else "none"],
            "expected_artifacts": [
                "plan.json",
                "execution_envelope.json",
                "execution_result.json",
                "step_results.json",
            ],
        },
        source_lane="panel_mission_prompt",
    )

    base_name = f"job-panel-mission-{slug}-{ts}"
    final_path = inbox / f"{base_name}.json"
    counter = 1
    while final_path.exists():
        final_path = inbox / f"{base_name}-{counter}.json"
        counter += 1

    tmp_path = inbox / f".{final_path.stem}.tmp.json"
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(final_path)
    return final_path.name, mission_id


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


def _trim_tail(value: str, *, max_chars: int = 2000) -> str:
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _repo_root_for_panel_subprocess() -> Path:
    env_root = os.getenv("VOXERA_REPO_ROOT", "").strip()
    if env_root:
        candidate = Path(env_root).expanduser().resolve()
        if candidate.exists() and candidate.is_dir():
            return candidate

    default_root = Path(__file__).resolve().parents[3]
    if default_root.exists() and default_root.is_dir():
        return default_root
    return Path.cwd()


def _run_queue_hygiene_command(queue_root: Path, args: list[str]) -> dict[str, Any]:
    run_cwd = _repo_root_for_panel_subprocess()
    commands = [
        [sys.executable, "-m", "voxera.cli", *args, "--queue-dir", str(queue_root)],
        ["voxera", *args, "--queue-dir", str(queue_root)],
    ]
    attempted: list[dict[str, Any]] = []

    for cmd in commands:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=run_cwd)
        except FileNotFoundError as exc:
            attempted.append(
                {
                    "cmd": cmd,
                    "cwd": str(run_cwd),
                    "exit_code": None,
                    "stderr_tail": _trim_tail(str(exc)),
                    "stdout_tail": "",
                }
            )
            continue

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        stdout_tail = _trim_tail(stdout)
        stderr_tail = _trim_tail(stderr)

        result: dict[str, Any] = {
            "ok": False,
            "result": {},
            "exit_code": int(proc.returncode),
            "stderr_tail": stderr_tail,
            "stdout_tail": stdout_tail,
            "cmd": cmd,
            "cwd": str(run_cwd),
            "attempted": attempted,
            "error": "",
        }

        if proc.returncode != 0:
            result["error"] = _trim_tail(stderr or stdout or "command failed")
        else:
            if not stdout.strip():
                result["error"] = "no json output"
            else:
                try:
                    parsed = json.loads(stdout)
                except json.JSONDecodeError:
                    result["error"] = "json parse failed"
                else:
                    if not isinstance(parsed, dict):
                        result["error"] = "json parse failed"
                    else:
                        result["ok"] = True
                        result["result"] = parsed

        if not result["ok"]:
            log(
                {
                    "event": "panel_hygiene_command_failed",
                    "cmd": cmd,
                    "rc": int(proc.returncode),
                    "stderr_tail": stderr_tail,
                    "stdout_tail": stdout_tail,
                    "error": result["error"],
                    "cwd": str(run_cwd),
                }
            )
        return result

    last_attempt = attempted[-1] if attempted else {}
    error_tail = _trim_tail(
        str(last_attempt.get("stderr_tail") or "voxera CLI executable not found")
    )
    failure = {
        "ok": False,
        "result": {},
        "exit_code": None,
        "stderr_tail": error_tail,
        "stdout_tail": "",
        "cmd": last_attempt.get("cmd", commands[0]),
        "cwd": str(run_cwd),
        "attempted": attempted,
        "error": error_tail,
    }
    log(
        {
            "event": "panel_hygiene_command_failed",
            "cmd": failure["cmd"],
            "rc": None,
            "stderr_tail": error_tail,
            "stdout_tail": "",
            "error": failure["error"],
            "cwd": str(run_cwd),
        }
    )
    return failure


def _write_hygiene_result(queue_root: Path, key: str, result: dict[str, Any]) -> None:
    def _apply(payload: dict[str, Any]) -> dict[str, Any]:
        payload[key] = result
        payload["updated_at_ms"] = _now_ms()
        return payload

    update_health_snapshot(queue_root, _apply)


def _job_detail_payload(queue_root: Path, job_id: str) -> dict[str, Any]:
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
    artifact_inventory, artifact_anomalies = _job_artifact_inventory(
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
    context_summary = _job_context_summary(
        primary,
        state_sidecar=state_sidecar,
        approval=approval,
        failed_sidecar=failed_sidecar,
        structured_execution=structured_execution,
    )
    operator_summary = _operator_outcome_summary(
        bucket=bucket,
        execution=structured_execution,
        state_sidecar=state_sidecar,
        job_context=context_summary,
        has_approval=bool(approval),
    )
    policy_rationale = _policy_rationale_rows(
        execution=structured_execution,
        state_sidecar=state_sidecar,
        approval=approval,
        has_approval=bool(approval),
    )
    evidence_summary = _evidence_summary_rows(
        artifacts_dir=artifacts_dir,
        approval_path=approval_path if approval_path and approval_path.exists() else None,
        failed_sidecar_path=failed_sidecar_path
        if failed_sidecar_path and failed_sidecar_path.exists()
        else None,
        state_sidecar_paths=state_candidates,
    )
    why_stopped = _why_stopped_rows(
        execution=structured_execution,
        state_sidecar=state_sidecar,
        job_context=context_summary,
    )
    audit_timeline = relevant_events[:40]
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
        "job_context": context_summary,
        "lineage": lineage,
        "child_refs": structured_execution.get("child_refs")
        if isinstance(structured_execution.get("child_refs"), list)
        else [],
        "child_summary": structured_execution.get("child_summary")
        if isinstance(structured_execution.get("child_summary"), dict)
        else None,
        "execution": structured_execution,
        "operator_summary": operator_summary,
        "policy_rationale": policy_rationale,
        "evidence_summary": evidence_summary,
        "why_stopped": why_stopped,
        "recent_timeline": _job_recent_timeline(actions, audit_timeline),
        "artifacts_dir": str(artifacts_dir),
        "audit_timeline": audit_timeline,
        "has_approval": bool(approval),
        "can_cancel": bucket in {"inbox", "pending", "approvals"},
        "can_retry": bucket in {"failed", "canceled"},
        "can_delete": bucket in {"done", "failed", "canceled"},
    }


def _job_progress_payload(queue_root: Path, job_id: str) -> dict[str, Any]:
    payload = _job_detail_payload(queue_root, job_id)

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
    operator_summary = _operator_outcome_summary(
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


def _job_artifact_flags(queue_root: Path, job_id: str) -> dict[str, bool]:
    artifacts_dir = queue_root / "artifacts" / Path(job_id).stem
    return {
        "plan": (artifacts_dir / "plan.json").exists(),
        "actions": (artifacts_dir / "actions.jsonl").exists(),
        "stdout": (artifacts_dir / "stdout.txt").exists(),
        "stderr": (artifacts_dir / "stderr.txt").exists(),
    }


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
