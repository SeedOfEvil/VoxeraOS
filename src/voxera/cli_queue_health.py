from __future__ import annotations

import json
import time
from typing import Any

import typer
from rich.table import Table

from .cli_common import console, now_ms, queue_dir_path
from .cli_queue_payloads import (
    build_health_reset_event_name,
    build_health_reset_log_payload,
)
from .core.queue_daemon import MissionQueueDaemon
from .health_reset import EVENT_BY_SCOPE, HealthResetError, reset_health_snapshot
from .health_semantics import build_health_semantic_sections
from .paths import queue_root_display


def queue_health(
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    watch: bool = typer.Option(False, "--watch", "-w", help="Refresh output continuously."),
    interval_s: float = typer.Option(
        2.0,
        "--interval",
        min=0.2,
        help="Refresh interval in seconds used with --watch.",
    ),
):
    """Show queue/daemon health grouped by current state, recent history, and historical counters."""
    daemon = MissionQueueDaemon(queue_root=queue_dir_path(queue_dir))

    def _snapshot_with_sections() -> dict[str, Any]:
        status = daemon.status_snapshot(approvals_limit=3, failed_limit=3)
        health_like = {
            "daemon_state": status.get("daemon_state", "healthy"),
            "daemon_pid": status.get("daemon_pid"),
            "daemon_started_at_ms": status.get("daemon_started_at_ms"),
            "updated_at_ms": status.get("updated_at_ms"),
            "lock_state": status.get("lock_state"),
            "consecutive_brain_failures": status.get("consecutive_brain_failures", 0),
            "degraded_since_ts": status.get("degraded_since_ts"),
            "degraded_reason": status.get("degraded_reason"),
            "brain_backoff_wait_s": status.get("brain_backoff_wait_s", 0),
            "brain_backoff_active": bool(status.get("brain_backoff_active", False)),
            "brain_backoff_last_applied_s": status.get("brain_backoff_last_applied_s", 0),
            "brain_backoff_last_applied_ts": status.get("brain_backoff_last_applied_ts"),
            "last_ok_event": status.get("last_ok_event", ""),
            "last_ok_ts_ms": status.get("last_ok_ts_ms"),
            "last_error": status.get("last_error", ""),
            "last_error_ts_ms": status.get("last_error_ts_ms"),
            "last_fallback_reason": status.get("last_fallback_reason"),
            "last_fallback_from": status.get("last_fallback_from"),
            "last_fallback_to": status.get("last_fallback_to"),
            "last_fallback_ts_ms": status.get("last_fallback_ts_ms"),
            "last_shutdown_outcome": status.get("last_shutdown_outcome"),
            "last_shutdown_ts": status.get("last_shutdown_ts"),
            "last_shutdown_reason": status.get("last_shutdown_reason"),
            "last_shutdown_job": status.get("last_shutdown_job"),
            "panel_auth": status.get("panel_auth")
            if isinstance(status.get("panel_auth"), dict)
            else {},
            "counters": status.get("health_counters")
            if isinstance(status.get("health_counters"), dict)
            else {},
        }
        grouped = build_health_semantic_sections(
            health_like,
            queue_context={
                "queue_root": status.get("queue_root"),
                "health_path": status.get("health_path"),
                "intake_glob": status.get("intake_glob"),
                "paused": bool(status.get("paused", False)),
            },
            lock_status=status.get("lock_status")
            if isinstance(status.get("lock_status"), dict)
            else {},
            daemon_lock_counters=status.get("daemon_lock_counters")
            if isinstance(status.get("daemon_lock_counters"), dict)
            else {},
            now_ms=now_ms(),
        )
        status["panel_auth_lockouts"] = grouped["current_state"].get("panel_auth_lockouts", {})
        status["current_state"] = grouped["current_state"]
        status["recent_history"] = grouped["recent_history"]
        status["historical_counters"] = grouped["historical_counters"]
        status["counters"] = grouped["historical_counters"]
        return status

    def _render(snapshot: dict[str, Any]) -> None:
        current = snapshot["current_state"]
        history = snapshot["recent_history"]
        counters = snapshot["historical_counters"]

        def _history_pair(value: Any, ts: Any) -> str:
            value_text = str(value).strip() if value is not None else ""
            if not value_text and not ts:
                return "-"
            return f"{value_text or '-'} @ {ts if ts is not None else '-'}"

        def _display(value: Any) -> str:
            return "-" if value is None or str(value).strip() == "" else str(value)

        daemon_state = str(current.get("daemon_state", "healthy"))
        degradation = (
            current.get("degradation", {}) if isinstance(current.get("degradation"), dict) else {}
        )
        if daemon_state == "healthy":
            console.print("Current state is HEALTHY")
        else:
            console.print(
                "Current state is DEGRADED "
                f"(reason={degradation.get('degraded_reason') or '-'}, "
                f"failures={degradation.get('consecutive_brain_failures', 0)})"
            )
        console.print(
            "Recent history and historical counters are context and can include past incidents."
        )

        current_table = Table(title="Current State")
        current_table.add_column("Field")
        current_table.add_column("Value")
        current_table.add_row("queue_root", str(current.get("queue_root", "")))
        current_table.add_row("health_path", str(current.get("health_path", "")))
        current_table.add_row("intake_glob", str(current.get("intake_glob", "")))
        current_table.add_row("paused", str(current.get("paused", False)))
        current_table.add_row("daemon_state", daemon_state)
        current_table.add_row("daemon_pid", _display(current.get("daemon_pid")))
        current_table.add_row("daemon_started_at_ms", _display(current.get("daemon_started_at_ms")))
        current_table.add_row("snapshot_updated_at_ms", _display(current.get("updated_at_ms")))
        lock = current.get("lock", {}) if isinstance(current.get("lock"), dict) else {}
        current_table.add_row(
            "lock",
            f"state={_display(lock.get('state'))} exists={lock.get('exists')} pid={_display(lock.get('pid'))} alive={lock.get('alive')}",
        )
        current_table.add_row(
            "degradation",
            f"failures={degradation.get('consecutive_brain_failures', 0)} reason={degradation.get('degraded_reason') or '-'} since={_display(degradation.get('degraded_since_ts'))}",
        )
        current_table.add_row(
            "brain_backoff",
            f"active={degradation.get('brain_backoff_active', False)} wait_s={degradation.get('brain_backoff_wait_s', 0)} last_applied_s={degradation.get('brain_backoff_last_applied_s', 0)} last_applied_ts={_display(degradation.get('brain_backoff_last_applied_ts'))}",
        )
        lockouts = current.get("panel_auth_lockouts", {})
        current_table.add_row(
            "panel_auth_lockouts",
            f"locked_out_ips={lockouts.get('locked_out_ips', 0)} next_expiry_ts_ms={_display(lockouts.get('next_expiry_ts_ms'))}",
        )
        console.print(current_table)

        history_table = Table(title="Recent History")
        history_table.add_column("Field")
        history_table.add_column("Value")
        history_table.add_row(
            "Last OK", _history_pair(history.get("last_ok_event"), history.get("last_ok_ts_ms"))
        )
        history_table.add_row(
            "Last Error", _history_pair(history.get("last_error"), history.get("last_error_ts_ms"))
        )
        fallback = history.get("last_brain_fallback", {})
        fallback_reason = fallback.get("reason") if isinstance(fallback, dict) else None
        fallback_from = fallback.get("from") if isinstance(fallback, dict) else None
        fallback_to = fallback.get("to") if isinstance(fallback, dict) else None
        fallback_ts = fallback.get("ts_ms") if isinstance(fallback, dict) else None
        if not any([fallback_reason, fallback_from, fallback_to, fallback_ts]):
            fallback_text = "-"
        else:
            fallback_text = (
                f"reason={fallback_reason or '-'} "
                f"from={fallback_from or '-'} "
                f"to={fallback_to or '-'} "
                f"ts_ms={fallback_ts if fallback_ts is not None else '-'}"
            )
        history_table.add_row("Last Brain Fallback", fallback_text)
        history_table.add_row("Degraded Since", _display(history.get("degraded_since_ts")))
        history_table.add_row(
            "Backoff Last Applied",
            f"wait_s={history.get('brain_backoff_last_applied_s', 0)} ts={_display(history.get('brain_backoff_last_applied_ts'))}",
        )
        shutdown_outcome = history.get("last_shutdown_outcome")
        shutdown_reason = history.get("last_shutdown_reason")
        shutdown_job = history.get("last_shutdown_job")
        shutdown_ts = history.get("last_shutdown_ts")
        if not any([shutdown_outcome, shutdown_reason, shutdown_job, shutdown_ts]):
            shutdown_text = "-"
        else:
            shutdown_text = (
                f"outcome={shutdown_outcome or '-'} "
                f"reason={shutdown_reason or '-'} "
                f"job={shutdown_job or '-'} "
                f"ts={shutdown_ts if shutdown_ts is not None else '-'}"
            )
        history_table.add_row("Last Shutdown", shutdown_text)
        console.print(history_table)

        counters_table = Table(title="Historical Counters")
        counters_table.add_column("Counter")
        counters_table.add_column("Value", justify="right")
        for key in sorted(counters.keys()):
            counters_table.add_row(key, str(counters.get(key, 0)))
        console.print(counters_table)

    if json_output:
        typer.echo(json.dumps(_snapshot_with_sections(), sort_keys=True))
        return

    if not watch:
        _render(_snapshot_with_sections())
        return

    try:
        while True:
            console.clear()
            _render(_snapshot_with_sections())
            console.print(f"Refreshing every {interval_s:.1f}s (Ctrl+C to exit)")
            time.sleep(interval_s)
    except KeyboardInterrupt:
        console.print("Stopped watch mode.")


def queue_health_reset(
    scope: str = typer.Option(
        "current_and_recent",
        "--scope",
        help="Reset scope: current_state, recent_history, or current_and_recent.",
    ),
    counter_group: str | None = typer.Option(
        None,
        "--counter-group",
        help=(
            "Optional historical counter reset group: panel_auth_counters, "
            "brain_fallback_counters, or all_historical_counters."
        ),
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output."),
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Safely reset operator health current-state/recent-history fields."""
    queue_root = queue_dir_path(queue_dir)
    try:
        summary = reset_health_snapshot(
            queue_root,
            scope=scope,
            counter_group=counter_group,
            actor_surface="cli",
        )
    except HealthResetError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    event_name = build_health_reset_event_name(
        scope=scope,
        counter_group=counter_group,
        event_by_scope=EVENT_BY_SCOPE,
    )
    from . import cli as cli_root

    cli_root.log(
        build_health_reset_log_payload(
            event_name=event_name,
            scope=scope,
            counter_group=counter_group,
            changed_fields=summary["changed_fields"],
            timestamp_ms=summary["timestamp_ms"],
        )
    )

    if json_output:
        typer.echo(json.dumps(summary, sort_keys=True))
        return

    console.print(f"Health reset scope applied: {scope}")
    if counter_group:
        console.print(f"Historical counter reset group: {counter_group}")
    else:
        console.print("Historical counters preserved by default.")
    if summary["changed_fields"]:
        console.print("Changed fields:")
        for field in summary["changed_fields"]:
            console.print(f"- {field}")
    else:
        console.print("No field values changed (already reset).")
