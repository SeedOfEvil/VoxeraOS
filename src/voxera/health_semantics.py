from __future__ import annotations

import time
from typing import Any


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def active_panel_auth_lockouts(
    panel_auth: dict[str, Any], *, now_ms: int | None = None
) -> dict[str, Any]:
    lockouts_raw = panel_auth.get("lockouts_by_ip")
    lockouts = lockouts_raw if isinstance(lockouts_raw, dict) else {}
    current_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    active_lockouts = {
        ip: row
        for ip, row in lockouts.items()
        if isinstance(row, dict) and current_ms < _as_int(row.get("until_ts_ms"), 0)
    }
    next_expiry = min(
        (_as_int(row.get("until_ts_ms"), 0) for row in active_lockouts.values()), default=None
    )
    return {
        "active": active_lockouts,
        "count": len(active_lockouts),
        "next_expiry_ts_ms": next_expiry,
    }


def build_health_semantic_sections(
    health: dict[str, Any],
    *,
    queue_context: dict[str, Any] | None = None,
    lock_status: dict[str, Any] | None = None,
    daemon_lock_counters: dict[str, Any] | None = None,
    now_ms: int | None = None,
) -> dict[str, dict[str, Any]]:
    context = queue_context if isinstance(queue_context, dict) else {}
    lock = lock_status if isinstance(lock_status, dict) else {}
    health_counters_raw = health.get("counters")
    health_counters = health_counters_raw if isinstance(health_counters_raw, dict) else {}
    panel_auth_raw = health.get("panel_auth")
    panel_auth = panel_auth_raw if isinstance(panel_auth_raw, dict) else {}
    lockouts = active_panel_auth_lockouts(panel_auth, now_ms=now_ms)

    current_state = {
        "queue_root": context.get("queue_root"),
        "health_path": context.get("health_path"),
        "intake_glob": context.get("intake_glob"),
        "paused": bool(context.get("paused", False)),
        "daemon_state": health.get("daemon_state", "healthy"),
        "daemon_pid": health.get("daemon_pid"),
        "daemon_started_at_ms": health.get("daemon_started_at_ms"),
        "updated_at_ms": health.get("updated_at_ms"),
        "lock": {
            "state": health.get("lock_state"),
            "exists": bool(lock.get("exists", False)),
            "pid": lock.get("pid"),
            "alive": bool(lock.get("alive", False)),
        },
        "degradation": {
            "consecutive_brain_failures": _as_int(health.get("consecutive_brain_failures"), 0),
            "degraded_since_ts": health.get("degraded_since_ts"),
            "degraded_reason": health.get("degraded_reason"),
            "brain_backoff_wait_s": _as_int(health.get("brain_backoff_wait_s"), 0),
            "brain_backoff_active": bool(health.get("brain_backoff_active", False)),
            "brain_backoff_last_applied_s": _as_int(health.get("brain_backoff_last_applied_s"), 0),
            "brain_backoff_last_applied_ts": health.get("brain_backoff_last_applied_ts"),
        },
        "panel_auth_lockouts": {
            "locked_out_ips": lockouts["count"],
            "next_expiry_ts_ms": lockouts["next_expiry_ts_ms"],
        },
    }

    recent_history = {
        "last_ok_event": health.get("last_ok_event"),
        "last_ok_ts_ms": health.get("last_ok_ts_ms"),
        "last_error": health.get("last_error"),
        "last_error_ts_ms": health.get("last_error_ts_ms"),
        "last_brain_fallback": {
            "reason": health.get("last_fallback_reason"),
            "from": health.get("last_fallback_from"),
            "to": health.get("last_fallback_to"),
            "ts_ms": health.get("last_fallback_ts_ms"),
        },
        "degraded_since_ts": health.get("degraded_since_ts"),
        "brain_backoff_last_applied_s": _as_int(health.get("brain_backoff_last_applied_s"), 0),
        "brain_backoff_last_applied_ts": health.get("brain_backoff_last_applied_ts"),
        "last_shutdown_outcome": health.get("last_shutdown_outcome"),
        "last_shutdown_ts": health.get("last_shutdown_ts"),
        "last_shutdown_reason": health.get("last_shutdown_reason"),
        "last_shutdown_job": health.get("last_shutdown_job"),
    }

    counters: dict[str, Any] = {}
    daemon_counters = daemon_lock_counters if isinstance(daemon_lock_counters, dict) else {}
    counters.update(daemon_counters)
    counters.update(health_counters)

    return {
        "current_state": current_state,
        "recent_history": recent_history,
        "historical_counters": counters,
    }
