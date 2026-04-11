"""Panel health-view and formatting helper cluster.

This module owns the narrow cluster that shapes the two health-facing
read-only views the operator home page renders: the Daemon Health widget
payload (``daemon_health_view``) and the Performance Stats tab payload
(``performance_stats_view``). It also owns the tiny formatting /
history-line helpers those views depend on (``format_ts``,
``format_ts_seconds``, ``format_age``, ``history_value``,
``history_pair``) because they only exist to support those two views.

It was extracted from ``panel/app.py`` as **PR E** — the fifth small,
behavior-preserving step of decomposing that composition root — after
PR A (``auth_enforcement``), PR B (``queue_mutation_bridge``), PR C
(``security_health_helpers``), and PR D (``job_detail_sections`` /
``job_presentation``). ``panel/app.py`` remains the composition root: it
still defines the FastAPI app, mounts ``/static``, constructs the Jinja
environment, owns the shared ``_settings`` / ``_now_ms`` / ``_queue_root``
wrappers, and registers every route family. The thin
``_daemon_health_view`` / ``_performance_stats_view`` wrappers in
``panel/app.py`` forward into this module and preserve the exact
``(health) -> dict`` / ``(queue, health) -> dict`` route-callback
signatures that ``register_home_routes(daemon_health_view=...,
performance_stats_view=...)`` already expects. The ``_format_ts``
wrapper also forwards here so ``register_automation_routes(
format_ts_ms=_format_ts)`` keeps the same callable identity seam.

Explicit-args design (matches PR B / PR C / PR D, not PR A): every
function in this module takes its inputs as explicit positional
arguments (``health``, ``queue``, ``ts_ms``, ``value``, ...). There is
no hidden module-level state, no reach-back into ``panel.app``, and the
helpers are easy to unit-test in isolation.

Semantics preserved exactly:

* ``format_ts`` — returns ``"—"`` (em-dash) for ``None`` / non-positive
  millis; otherwise formats as UTC ``"%Y-%m-%d %H:%M:%S UTC"``. Byte-for
  -byte identical to the original ``panel.app._format_ts``.
* ``format_ts_seconds`` — same as ``format_ts`` but reads epoch seconds.
* ``format_age`` — ``"—"`` for ``None`` / negative; ``"{n}s"`` under a
  minute; ``"{m}m {s}s"`` when seconds remain; ``"{m}m"`` on the
  boundary. Byte-for-byte identical.
* ``history_value`` — trims string/None to ``"-"`` when empty, else the
  stripped ``str(value)``.
* ``history_pair`` — returns ``"-"`` only when both the value and the
  timestamp label are empty; otherwise ``"{val} @ {ts}"``.
* ``daemon_health_view`` — preserves the lock-status precedence (``lock_status``
  dict sub-key > derived from ``lock_state``), the fallback/recovery/
  shutdown ``present`` booleans, and the same field-for-field render
  dict used by ``home.html``'s Daemon Health widget.
* ``performance_stats_view`` — preserves the queue counts sub-dict, the
  ``build_health_semantic_sections`` composition (``current_state`` /
  ``recent_history`` / ``historical_counters``), the ``last_fallback_line``
  / ``last_error_line`` / ``last_shutdown_line`` rendered strings, and
  every historical counter key that ``home.html``'s Performance Stats
  tab reads. Byte-for-byte identical to the original in-app
  implementation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..health_semantics import build_health_semantic_sections
from .helpers import coerce_int

__all__ = [
    "daemon_health_view",
    "format_age",
    "format_ts",
    "format_ts_seconds",
    "history_pair",
    "history_value",
    "performance_stats_view",
]


def format_ts(ts_ms: int | None) -> str:
    """Format an epoch-millis timestamp as a UTC string (em-dash fallback)."""

    if ts_ms is None or ts_ms <= 0:
        return "—"
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def format_ts_seconds(ts_s: float | None) -> str:
    """Format an epoch-seconds timestamp as a UTC string (em-dash fallback)."""

    if ts_s is None or ts_s <= 0:
        return "—"
    return datetime.fromtimestamp(ts_s, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def format_age(age_s: int | None) -> str:
    """Format a non-negative age in seconds as a compact label."""

    if age_s is None or age_s < 0:
        return "—"
    if age_s < 60:
        return f"{age_s}s"
    minutes, seconds = divmod(age_s, 60)
    if seconds:
        return f"{minutes}m {seconds}s"
    return f"{minutes}m"


def history_value(value: Any) -> str:
    """Render a history-line value, falling back to ``"-"`` when empty."""

    text = str(value).strip() if value is not None else ""
    return text or "-"


def history_pair(value: Any, ts_label: str) -> str:
    """Render a ``"{value} @ {ts}"`` history pair with ``"-"`` fallback."""

    val = history_value(value)
    ts = ts_label.strip() if ts_label else "-"
    if val == "-" and ts in {"-", "—"}:
        return "-"
    return f"{val} @ {ts}"


def daemon_health_view(health: dict[str, Any]) -> dict[str, Any]:
    """Shape the Daemon Health widget payload from a raw health snapshot."""

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

    lock_pid = coerce_int(lock.get("pid"))
    if lock_pid is None:
        lock_pid = coerce_int(health.get("lock_holder_pid"))

    fallback_reason = health.get("last_fallback_reason")
    fallback_tier = health.get("last_fallback_to")
    fallback_ts = coerce_int(health.get("last_fallback_ts_ms"))
    has_fallback = any([fallback_reason, fallback_tier, fallback_ts])

    startup_recovery = health.get("last_startup_recovery")
    if isinstance(startup_recovery, dict):
        recovery_counts = startup_recovery.get("counts")
        recovery_ts = coerce_int(startup_recovery.get("ts_ms"))
    else:
        recovery_counts = health.get("last_startup_recovery_counts")
        recovery_ts = coerce_int(health.get("last_startup_recovery_ts"))
    counts = recovery_counts if isinstance(recovery_counts, dict) else {}
    recovery_job_count = coerce_int(counts.get("jobs_failed")) or 0
    orphan_count = (coerce_int(counts.get("orphan_approvals_quarantined")) or 0) + (
        coerce_int(counts.get("orphan_state_files_quarantined")) or 0
    )
    has_recovery = any([recovery_job_count, orphan_count, recovery_ts])

    shutdown_outcome = str(health.get("last_shutdown_outcome") or "").strip() or "unknown"
    shutdown_ts_raw = health.get("last_shutdown_ts")
    shutdown_ts = float(shutdown_ts_raw) if isinstance(shutdown_ts_raw, (int, float)) else None
    shutdown_reason = str(health.get("last_shutdown_reason") or "").strip() or "—"
    shutdown_job = str(health.get("last_shutdown_job") or "").strip() or "—"

    stale_age_s = coerce_int(lock.get("stale_age_s"))

    return {
        "lock_status": lock_status,
        "lock_pid": lock_pid,
        "lock_stale_age_s": stale_age_s,
        "lock_stale_age_label": format_age(stale_age_s),
        "last_brain_fallback": {
            "present": has_fallback,
            "tier": str(fallback_tier or "—"),
            "reason": str(fallback_reason or "—"),
            "ts": format_ts(fallback_ts),
        },
        "last_startup_recovery": {
            "present": has_recovery,
            "job_count": recovery_job_count,
            "orphan_count": orphan_count,
            "ts": format_ts(recovery_ts),
        },
        "last_shutdown": {
            "present": shutdown_outcome != "unknown" or shutdown_ts is not None,
            "outcome": shutdown_outcome,
            "ts": format_ts_seconds(shutdown_ts),
            "reason": shutdown_reason,
            "job": shutdown_job,
        },
        "daemon_state": str(health.get("daemon_state") or "healthy"),
    }


def performance_stats_view(queue: dict[str, Any], health: dict[str, Any]) -> dict[str, Any]:
    """Shape the Performance Stats tab payload from queue + health snapshots."""

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
                    f"{history_value(fallback.get('reason'))} "
                    f"({history_value(fallback.get('from'))} → {history_value(fallback.get('to'))}) "
                    f"@ {format_ts(coerce_int(fallback.get('ts_ms')))}"
                )
            ),
            "last_error_line": history_pair(
                recent_history.get("last_error"),
                format_ts(coerce_int(recent_history.get("last_error_ts_ms"))),
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
                    f"{history_value(recent_history.get('last_shutdown_outcome'))} / "
                    f"{history_value(recent_history.get('last_shutdown_reason'))} / "
                    f"{history_value(recent_history.get('last_shutdown_job'))} @ "
                    f"{format_ts_seconds(shutdown_ts)}"
                )
            ),
            "degraded_since_ts": history_value(recent_history.get("degraded_since_ts")),
            "brain_backoff_last_applied_s": int(
                recent_history.get("brain_backoff_last_applied_s", 0) or 0
            ),
            "brain_backoff_last_applied_ts": history_value(
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
