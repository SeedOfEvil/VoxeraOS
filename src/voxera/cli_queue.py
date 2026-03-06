from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.table import Table

from .cli_common import OUT_PATH_OPTION, console, now_ms, queue_dir_path
from .core.artifacts import format_bytes, prune_artifacts
from .core.inbox import add_inbox_job, list_inbox_jobs
from .core.queue_daemon import MissionQueueDaemon, QueueLockError
from .core.queue_hygiene import TERMINAL_BUCKETS, prune_queue_buckets
from .core.queue_reconcile import quarantine_reconcile_fixes, reconcile_queue
from .health_reset import EVENT_BY_SCOPE, HealthResetError, reset_health_snapshot
from .health_semantics import build_health_semantic_sections
from .incident_bundle import BundleError, build_job_bundle, build_system_bundle
from .paths import queue_root_display

queue_app = typer.Typer(help="Queue job utilities")
queue_approvals_app = typer.Typer(help="Resolve pending queue approvals")
queue_lock_app = typer.Typer(help="Queue daemon lock utilities")
inbox_app = typer.Typer(help="Human-friendly queue inbox")
artifacts_app = typer.Typer(help="Artifact management utilities")

queue_app.add_typer(queue_approvals_app, name="approvals")
queue_app.add_typer(queue_lock_app, name="lock")


def register(app: typer.Typer) -> None:
    app.add_typer(artifacts_app, name="artifacts")
    app.add_typer(queue_app, name="queue")
    app.add_typer(inbox_app, name="inbox")


@queue_approvals_app.command("list")
def queue_approvals_list(
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """List pending queue approvals."""
    daemon = MissionQueueDaemon(queue_root=queue_dir_path(queue_dir))
    approvals = daemon.approvals_list()
    if not approvals:
        console.print("No pending approvals.")
        return

    table = Table(title="Queue Approval Inbox")
    table.add_column("Job")
    table.add_column("Approve As")
    table.add_column("Step")
    table.add_column("Skill")
    table.add_column("Capability")
    table.add_column("Reason")
    table.add_column("Target")
    table.add_column("Scope")
    for item in approvals:
        target = item.get("target", {}) if isinstance(item.get("target"), dict) else {}
        scope = item.get("scope", {}) if isinstance(item.get("scope"), dict) else {}
        table.add_row(
            str(item.get("job", "")),
            " | ".join(str(v) for v in item.get("approve_refs", [])[:2]),
            str(item.get("step", "")),
            str(item.get("skill", "")),
            str(item.get("capability", "")),
            str(item.get("policy_reason", item.get("reason", ""))),
            f"{target.get('type', 'unknown')}: {target.get('value', '')}",
            f"fs={scope.get('fs_scope', '-')}, net={scope.get('needs_network', False)}",
        )
    console.print(table)


@queue_app.command("bundle")
def queue_bundle(
    job_id: str | None = typer.Argument(None),
    system: bool = typer.Option(False, "--system", help="Export overall system bundle."),
    out: Path = OUT_PATH_OPTION,
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Export a deterministic incident bundle for a job or the whole system."""
    root = queue_dir_path(queue_dir)
    if system:
        data = build_system_bundle(root)
    else:
        if not job_id:
            raise typer.BadParameter("Provide <job_id> or use --system")
        try:
            data = build_job_bundle(root, job_id)
        except BundleError as exc:
            console.print(f"[red]ERROR:[/red] {exc}")
            raise typer.Exit(code=1) from exc
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    console.print(f"Bundle written: {out}")


@queue_app.command("init")
def queue_init(
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Create queue directories (safe mkdir -p; does not delete data)."""
    daemon = MissionQueueDaemon(queue_root=queue_dir_path(queue_dir))
    daemon.ensure_dirs()
    console.print(f"Initialized queue directories under: {daemon.queue_root}")
    console.print(f"- inbox/: {daemon.inbox}")
    console.print(f"- pending/: {daemon.pending}")
    console.print(f"- pending/approvals/: {daemon.approvals}")
    console.print(f"- done/: {daemon.done}")
    console.print(f"- failed/: {daemon.failed}")
    console.print(f"- canceled/: {daemon.canceled}")
    console.print(f"- artifacts/: {daemon.artifacts}")


@queue_app.command("status")
def queue_status(
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Show queue health, pending approvals, and recent failures."""
    daemon = MissionQueueDaemon(queue_root=queue_dir_path(queue_dir))
    status = daemon.status_snapshot(approvals_limit=8, failed_limit=8)

    counts = status["counts"]
    counts_table = Table(title="Queue Status")
    counts_table.add_column("Bucket")
    counts_table.add_column("Count", justify="right")
    counts_table.add_row("inbox/", str(counts["inbox"]))
    counts_table.add_row("pending/", str(counts["pending"]))
    counts_table.add_row("pending/approvals/", str(counts["pending_approvals"]))
    counts_table.add_row("done/", str(counts["done"]))
    counts_table.add_row("failed/", str(counts["failed"]))
    counts_table.add_row("canceled/", str(counts.get("canceled", 0)))
    counts_table.add_row("failed metadata valid", str(status.get("failed_sidecars_valid", 0)))
    counts_table.add_row("failed metadata invalid", str(status.get("failed_sidecars_invalid", 0)))
    counts_table.add_row("failed metadata missing", str(status.get("failed_sidecars_missing", 0)))
    retention = status.get("failed_retention", {})
    counts_table.add_row(
        "failed retention max age (s)",
        str(retention.get("max_age_s")) if retention.get("max_age_s") is not None else "(unset)",
    )
    counts_table.add_row(
        "failed retention max count",
        str(retention.get("max_count")) if retention.get("max_count") is not None else "(unset)",
    )
    console.print(counts_table)
    console.print(f"Queue intake: {status.get('intake_glob', '')}")
    console.print(f"Daemon paused: {status.get('paused', False)}")

    prune = status.get("failed_prune_last", {})
    prune_table = Table(title="Failed Retention (latest prune event)")
    prune_table.add_column("Field")
    prune_table.add_column("Value")
    prune_table.add_row("removed jobs", str(prune.get("removed_jobs", 0)))
    prune_table.add_row("removed sidecars", str(prune.get("removed_sidecars", 0)))
    prune_table.add_row(
        "event max age (s)",
        str(prune.get("max_age_s")) if prune.get("max_age_s") is not None else "(n/a)",
    )
    prune_table.add_row(
        "event max count",
        str(prune.get("max_count")) if prune.get("max_count") is not None else "(n/a)",
    )
    console.print(prune_table)
    console.print(f"Artifacts root: {status.get('artifacts_root', '')}")

    lock_counters = status.get("daemon_lock_counters", {})
    lock_table = Table(title="Daemon Lock Counters")
    lock_table.add_column("Event")
    lock_table.add_column("Count", justify="right")
    lock_table.add_row("acquire ok", str(lock_counters.get("lock_acquire_ok", 0)))
    lock_table.add_row("acquire fail", str(lock_counters.get("lock_acquire_fail", 0)))
    lock_table.add_row("reclaimed", str(lock_counters.get("lock_reclaimed", 0)))
    lock_table.add_row("released", str(lock_counters.get("lock_released", 0)))
    lock_table.add_row("unlock refused", str(lock_counters.get("unlock_refused", 0)))
    lock_table.add_row("unlock ok", str(lock_counters.get("unlock_ok", 0)))
    lock_table.add_row("force unlock", str(lock_counters.get("force_unlock_count", 0)))
    console.print(lock_table)

    if not status["exists"]:
        console.print(f"[yellow]Hint:[/yellow] queue root not found yet: {status['queue_root']}")

    approvals = status["pending_approvals"]
    approvals_table = Table(title="Pending Approvals")
    approvals_table.add_column("Job")
    approvals_table.add_column("Step")
    approvals_table.add_column("Skill")
    approvals_table.add_column("Reason")
    approvals_table.add_column("Target")
    approvals_table.add_column("Scope")
    if approvals:
        for item in approvals:
            target = item.get("target", {}) if isinstance(item.get("target"), dict) else {}
            scope = item.get("scope", {}) if isinstance(item.get("scope"), dict) else {}
            approvals_table.add_row(
                str(item.get("job", "")),
                str(item.get("step", "")),
                str(item.get("skill", "")),
                str(item.get("policy_reason", item.get("reason", ""))),
                f"{target.get('type', 'unknown')}: {target.get('value', '')}",
                f"fs={scope.get('fs_scope', '-')}, net={scope.get('needs_network', False)}",
            )
    else:
        approvals_table.add_row("-", "-", "-", "No pending approvals", "-", "-")
    console.print(approvals_table)

    lifecycle_rows: list[tuple[str, str]] = []
    for bucket_name, bucket_dir in (
        ("inbox", daemon.inbox),
        ("pending", daemon.pending),
        ("done", daemon.done),
        ("failed", daemon.failed),
        ("canceled", daemon.canceled),
    ):
        for job in sorted(bucket_dir.glob("*.json")):
            if job.name.endswith(
                (
                    ".pending.json",
                    ".approval.json",
                    ".error.json",
                    ".tmp.json",
                    ".partial.json",
                    ".state.json",
                )
            ):
                continue
            state_path = bucket_dir / f"{job.stem}.state.json"
            if not state_path.exists():
                state_path = daemon.pending / f"{job.stem}.state.json"
            if not state_path.exists():
                lifecycle_rows.append((job.name, f"{bucket_name}: -"))
                continue
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                lifecycle_rows.append((job.name, f"{bucket_name}: invalid-state"))
                continue
            lifecycle = str(state.get("lifecycle_state") or "-")
            outcome = str(state.get("terminal_outcome") or "")
            current_step = int(state.get("current_step_index") or 0)
            total_steps = int(state.get("total_steps") or 0)
            progress = f" {current_step}/{total_steps}" if total_steps else ""
            suffix = f" · {outcome}" if outcome else ""
            lifecycle_rows.append((job.name, f"{bucket_name}: {lifecycle}{progress}{suffix}"))
            if len(lifecycle_rows) >= 8:
                break
        if len(lifecycle_rows) >= 8:
            break

    lifecycle_table = Table(title="Job Lifecycle Snapshot")
    lifecycle_table.add_column("Job")
    lifecycle_table.add_column("State")
    if lifecycle_rows:
        for job_name, state_label in lifecycle_rows:
            lifecycle_table.add_row(job_name, state_label)
    else:
        lifecycle_table.add_row("-", "No jobs")
    console.print(lifecycle_table)

    failed = status["recent_failed"]
    failed_table = Table(title="Recent Failed Jobs")
    failed_table.add_column("Job")
    failed_table.add_column("Error Summary")
    if failed:
        for item in failed:
            failed_table.add_row(
                str(item.get("job", "")), str(item.get("error", "") or "(no audit error summary)")
            )
    else:
        failed_table.add_row("-", "No failed jobs")
    console.print(failed_table)


def _render_lock_status(status: dict[str, Any]) -> None:
    lock = status.get("lock_status", {}) if isinstance(status.get("lock_status"), dict) else {}
    lock_table = Table(title="Lock Status")
    lock_table.add_column("Field")
    lock_table.add_column("Value")
    lock_table.add_row("lock path", str(lock.get("lock_path", "")))
    lock_table.add_row("lock exists", str(lock.get("exists", False)))
    lock_table.add_row("lock pid", str(lock.get("pid", 0)))
    lock_table.add_row("lock pid alive", str(lock.get("alive", False)))
    console.print(lock_table)


@queue_lock_app.command("status")
def queue_lock_status(
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Show queue daemon lock status table."""
    daemon = MissionQueueDaemon(queue_root=queue_dir_path(queue_dir))
    status = daemon.status_snapshot(approvals_limit=3, failed_limit=3)
    _render_lock_status(status)


@queue_app.command("health")
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
    import time

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


@queue_app.command("health-reset")
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

    event_name = (
        "health_reset_historical_counters"
        if counter_group
        else EVENT_BY_SCOPE.get(scope, "health_reset")
    )
    from . import cli as cli_root

    cli_root.log(
        {
            "event": event_name,
            "scope": scope,
            "counter_group": counter_group,
            "actor_surface": "cli",
            "fields_changed": summary["changed_fields"],
            "timestamp_ms": summary["timestamp_ms"],
        }
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


@queue_app.command("cancel")
def queue_cancel(
    ref: str,
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Cancel a queue job by id or filename."""
    daemon = MissionQueueDaemon(queue_root=queue_dir_path(queue_dir))
    try:
        moved = daemon.cancel_job(ref)
    except FileNotFoundError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"Cancelled: {moved.name} (moved to canceled/)")


@queue_app.command("retry")
def queue_retry(
    ref: str,
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Retry a failed queue job by id or filename."""
    daemon = MissionQueueDaemon(queue_root=queue_dir_path(queue_dir))
    try:
        moved = daemon.retry_job(ref)
    except FileNotFoundError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"Re-queued: {moved.name} (inbox/)")


@queue_app.command("unlock")
def queue_unlock(
    force: bool = typer.Option(
        False,
        "--force",
        help="Force-remove lock even if held by a live daemon (dangerous).",
    ),
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Remove stale/dead daemon lock, or force-remove with --force."""
    daemon = MissionQueueDaemon(queue_root=queue_dir_path(queue_dir))
    if force:
        if daemon.force_unlock():
            console.print("Force-removed daemon lock.")
            return
        console.print("No daemon lock was present.")
        return

    try:
        result = daemon.try_unlock_stale()
    except QueueLockError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if not result.get("removed"):
        console.print("No daemon lock was present.")
        return

    pid = int(result.get("pid") or 0)
    alive = bool(result.get("alive"))
    stale = bool(result.get("stale"))
    if stale:
        age_s = int(float(result.get("age_s") or 0.0))
        console.print(f"Removed stale daemon lock (age_s={age_s}, pid={pid}, alive={alive}).")
    else:
        console.print("Removed orphaned daemon lock (pid not alive).")


@queue_app.command("pause")
def queue_pause(
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Pause queue processing."""
    daemon = MissionQueueDaemon(queue_root=queue_dir_path(queue_dir))
    daemon.pause()
    console.print("Queue processing paused.")


@queue_app.command("resume")
def queue_resume(
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Resume queue processing."""
    daemon = MissionQueueDaemon(queue_root=queue_dir_path(queue_dir))
    daemon.resume()
    console.print("Queue processing resumed.")


@queue_approvals_app.command("approve")
def queue_approvals_approve(
    ref: str,
    always: bool = typer.Option(
        False, "--always", help="Approve and grant always-allow for this skill+scope."
    ),
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Approve a pending queue job by filename or id."""
    daemon = MissionQueueDaemon(queue_root=queue_dir_path(queue_dir))
    try:
        ok = daemon.resolve_approval(ref, approve=True, approve_always=always)
    except FileNotFoundError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(
        "Approved and resumed." if ok else "Approval processed; job still pending another approval."
    )


@queue_approvals_app.command("deny")
def queue_approvals_deny(
    ref: str,
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Deny a pending queue job by filename or id."""
    daemon = MissionQueueDaemon(queue_root=queue_dir_path(queue_dir))
    try:
        daemon.resolve_approval(ref, approve=False)
    except FileNotFoundError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print("Denied. Job moved to failed/.")


@inbox_app.command("add")
def inbox_add(
    goal: str,
    id: str | None = typer.Option(
        None, "--id", help="Optional job id (defaults to generated timestamp+hash)."
    ),
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """Create an inbox queue job from plain goal text."""
    try:
        created = add_inbox_job(queue_dir_path(queue_dir), goal, job_id=id)
    except (ValueError, FileExistsError) as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    payload = json.loads(created.read_text(encoding="utf-8"))
    console.print(f"Created inbox job: {created}")
    console.print(f"ID: {payload.get('id', '')}")
    console.print(f"Goal: {payload.get('goal', '')}")


@inbox_app.command("list")
def inbox_list(
    n: int = typer.Option(20, "--n", min=1, help="Number of recent inbox jobs to show."),
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
):
    """List inbox-created jobs across queue states."""
    jobs, missing_dirs = list_inbox_jobs(queue_dir_path(queue_dir), limit=n)

    table = Table(title="Inbox Jobs")
    table.add_column("State")
    table.add_column("Job")
    table.add_column("ID")
    table.add_column("Goal")
    if jobs:
        for job in jobs:
            table.add_row(job.state, job.filename, job.job_id, job.goal)
    else:
        table.add_row("-", "-", "-", "No inbox jobs found")
    console.print(table)

    for missing in missing_dirs:
        console.print(f"[yellow]Hint:[/yellow] missing directory: {missing}")


@artifacts_app.command("prune")
def artifacts_prune(
    max_age_days: int | None = typer.Option(
        None,
        "--max-age-days",
        min=1,
        help="Prune artifacts older than this many days.",
    ),
    max_count: int | None = typer.Option(
        None,
        "--max-count",
        min=1,
        help="Keep newest N artifacts; prune the rest.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Perform deletion. Without this flag, only a dry-run preview is shown.",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON summary.",
    ),
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing the artifacts/ subdirectory.",
    ),
) -> None:
    """Prune job artifacts. Dry-run by default; use --yes to delete.

    Scans notes/queue/artifacts/ (or <queue-dir>/artifacts/) for stale entries.
    Selection policy: union — an artifact is pruned if it exceeds *either*
    --max-age-days OR is outside the newest --max-count entries.

    CLI flags override values from ~/.config/voxera/config.json:
      artifacts_retention_days, artifacts_retention_max_count.

    If neither flags nor config is set, prints a message and exits 0 (safe default).
    """
    from .config import load_config as load_runtime_config

    cfg = load_runtime_config()
    effective_age_days = max_age_days if max_age_days is not None else cfg.artifacts_retention_days
    effective_max_count = max_count if max_count is not None else cfg.artifacts_retention_max_count

    artifacts_root = queue_dir_path(queue_dir) / "artifacts"
    max_age_s = float(effective_age_days) * 86400.0 if effective_age_days is not None else None

    result = prune_artifacts(
        artifacts_root,
        max_age_s=max_age_s,
        max_count=effective_max_count,
        dry_run=not yes,
    )

    if json_out:
        typer.echo(json.dumps(result, indent=2, sort_keys=True))
        return

    status = result["status"]

    if status == "no_artifacts_dir":
        console.print(f"No artifacts directory at {artifacts_root} — nothing to prune.")
        return

    if status == "no_rules":
        console.print(
            "No pruning rules configured. Set --max-age-days or --max-count, "
            "or add artifacts_retention_days / artifacts_retention_max_count to "
            "~/.config/voxera/config.json."
        )
        return

    dry_run: bool = result["dry_run"]
    total: int = result["total_candidates"]
    pruned: int = result["pruned_count"]
    reclaimed: int = result["reclaimed_bytes"]

    prefix = "[dim](dry-run)[/dim] " if dry_run else ""
    action = "Would prune" if dry_run else "Pruned"

    console.print(f"{prefix}Artifacts root: {artifacts_root}")
    console.print(f"{prefix}Total candidates: {total}")
    console.print(f"{prefix}{action}: {pruned}")
    console.print(f"{prefix}Reclaimed: {format_bytes(reclaimed)}")

    top: list[dict[str, Any]] = result.get("top_entries", [])
    if top:
        table = Table(title="Top Artifacts by Size")
        table.add_column("Name")
        table.add_column("Size", justify="right")
        for entry in top:
            table.add_row(entry["name"], format_bytes(entry["bytes"]))
        console.print(table)

    if dry_run and pruned > 0:
        console.print("[yellow]Hint:[/yellow] Run with --yes to perform deletion.")


@queue_app.command("prune")
def queue_prune(
    max_age_days: int | None = typer.Option(
        None,
        "--max-age-days",
        min=1,
        help="Prune jobs older than this many days (terminal buckets only).",
    ),
    max_count: int | None = typer.Option(
        None,
        "--max-count",
        min=1,
        help="Keep newest N jobs per bucket; prune the rest.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Perform deletion. Without this flag, only a dry-run preview is shown.",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON summary.",
    ),
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue root directory containing done/, failed/, canceled/ subdirectories.",
    ),
) -> None:
    """Prune terminal queue buckets. Dry-run by default; use --yes to delete."""
    from .config import load_config as load_runtime_config

    cfg = load_runtime_config()
    effective_age_days = max_age_days if max_age_days is not None else cfg.queue_prune_max_age_days
    effective_max_count = max_count if max_count is not None else cfg.queue_prune_max_count

    queue_root_path = queue_dir_path(queue_dir)
    result = prune_queue_buckets(
        queue_root_path,
        buckets=TERMINAL_BUCKETS,
        max_age_days=effective_age_days,
        max_count=effective_max_count,
        dry_run=not yes,
    )

    if json_out:
        per_bucket_json: dict[str, dict[str, int]] = result["per_bucket"]
        removed_jobs = int(
            sum(int((per_bucket_json.get(b) or {}).get("pruned", 0) or 0) for b in per_bucket_json)
        )
        output: dict[str, Any] = {
            "status": "dry_run" if result["dry_run"] else "deleted",
            "queue_dir": result["queue_dir"],
            "buckets_processed": list(TERMINAL_BUCKETS),
            "per_bucket": per_bucket_json,
            "by_bucket": per_bucket_json,
            "removed_jobs": 0 if result["dry_run"] else removed_jobs,
            "would_remove_jobs": removed_jobs if result["dry_run"] else 0,
            "removed_sidecars": 0,
            "reclaimed_bytes": result["reclaimed_bytes"],
            "errors": result["errors"],
            "ts_ms": now_ms(),
        }
        if result["status"] == "no_rules":
            output["status"] = "no_rules"
        typer.echo(json.dumps(output, indent=2, sort_keys=True))
        return

    if result["status"] == "no_rules":
        console.print(
            "No pruning rules configured. Set --max-age-days or --max-count, "
            "or add queue_prune_max_age_days / queue_prune_max_count to "
            "~/.config/voxera/config.json."
        )
        return

    dry_run: bool = result["dry_run"]
    prefix = "[dim](dry-run)[/dim] " if dry_run else ""
    action = "Would prune" if dry_run else "Pruned"
    total_selected = 0
    total_reclaimed: int = result["reclaimed_bytes"]

    console.print(f"{prefix}Queue root: {queue_root_path}")
    console.print(f"{prefix}Buckets: {', '.join(TERMINAL_BUCKETS)}")

    per_bucket: dict[str, dict[str, int]] = result["per_bucket"]
    for bucket in TERMINAL_BUCKETS:
        counts = per_bucket.get(bucket, {"candidates": 0, "selected": 0, "pruned": 0})
        candidates = counts["candidates"]
        selected = counts["selected"]
        pruned = counts["pruned"]
        total_selected += pruned
        console.print(
            f"{prefix}  {bucket}/: {candidates} candidates, {selected} selected, {pruned} {action.lower()}"
        )

    console.print(f"{prefix}Total {action.lower()}: {total_selected}")
    console.print(f"{prefix}Reclaimed: {format_bytes(total_reclaimed)}")

    errors: list[str] = result.get("errors", [])
    for err in errors:
        console.print(f"[red]Warning:[/red] {err}")

    if dry_run and total_selected > 0:
        console.print("[yellow]Hint:[/yellow] Run with --yes to perform deletion.")


@queue_app.command("reconcile")
def queue_reconcile(
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON report.",
    ),
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue root directory to scan for hygiene issues.",
    ),
    fix: bool = typer.Option(
        False,
        "--fix",
        help="Enable fix mode: quarantine safe orphan files. Without --yes, runs as a dry-run preview.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Actually perform quarantine moves. Required with --fix to apply changes.",
    ),
    quarantine_dir: str = typer.Option(
        "",
        "--quarantine-dir",
        help=(
            "Directory to quarantine orphan files into. "
            "Must be within --queue-dir. "
            "Default: <queue-dir>/quarantine/reconcile-YYYYMMDD-HHMMSS/"
        ),
    ),
) -> None:
    """Scan queue directory and report hygiene issues. Report-only by default; no changes made."""
    from .core.queue_reconcile import _default_quarantine_dir

    queue_root_path = queue_dir_path(queue_dir)
    report = reconcile_queue(queue_root_path)

    q_dir: Path | None = None
    if fix:
        if quarantine_dir:
            q_dir = Path(quarantine_dir).expanduser().resolve()
        else:
            q_dir = _default_quarantine_dir(queue_root_path)
        try:
            q_dir.relative_to(queue_root_path)
        except ValueError as exc:
            typer.echo(
                f"Error: --quarantine-dir must be within --queue-dir ({queue_root_path}).",
                err=True,
            )
            raise typer.Exit(code=1) from exc

    dry_run = not yes
    mode = "report" if not fix else ("fix_preview" if dry_run else "fix_applied")

    fix_results: dict[str, Any] = {}
    if fix and q_dir is not None:
        fix_results = quarantine_reconcile_fixes(queue_root_path, q_dir, dry_run=dry_run)

    if json_out:
        out: dict[str, Any] = {
            "status": "ok",
            "queue_dir": str(queue_root_path),
            "mode": mode,
            "quarantine_dir": str(q_dir) if q_dir is not None else None,
            "issue_counts": report["issue_counts"],
            "examples": report["examples"],
            "ts_ms": now_ms(),
        }
        if fix_results:
            out["fix_counts"] = {
                "orphan_sidecars_quarantined": fix_results["orphan_sidecars_quarantined"],
                "orphan_sidecars_would_quarantine": fix_results["orphan_sidecars_would_quarantine"],
                "orphan_approvals_quarantined": fix_results["orphan_approvals_quarantined"],
                "orphan_approvals_would_quarantine": fix_results[
                    "orphan_approvals_would_quarantine"
                ],
            }
            out["quarantined_paths"] = fix_results["quarantined_paths"]
        else:
            out["fix_counts"] = {
                "orphan_sidecars_quarantined": 0,
                "orphan_sidecars_would_quarantine": 0,
                "orphan_approvals_quarantined": 0,
                "orphan_approvals_would_quarantine": 0,
            }
            out["quarantined_paths"] = []
        typer.echo(json.dumps(out, indent=2, sort_keys=True))
        return

    counts = report["issue_counts"]
    examples = report["examples"]

    console.print(f"Queue root: {queue_root_path}")
    if fix:
        console.print(f"Quarantine dir: {q_dir}")
        if dry_run:
            console.print("[yellow]Mode: fix preview (dry-run — no changes made)[/yellow]")
        else:
            console.print("[cyan]Mode: fix applied[/cyan]")
    else:
        console.print("[dim]Mode: report-only[/dim]")
    console.print()

    total = sum(counts.values())
    if total == 0:
        console.print("[green]Queue looks clean — no hygiene issues detected.[/green]")
    else:
        console.print(f"[yellow]Issues found:[/yellow] {total} total")
    console.print()

    path_issue_labels: list[tuple[str, str]] = [
        ("orphan_sidecars", "Orphan sidecars (terminal buckets)"),
        ("orphan_approvals", "Orphan approvals (pending/approvals/)"),
        ("orphan_artifacts_candidate", "Orphan artifact candidates (artifacts/) [report-only]"),
    ]
    for key, label in path_issue_labels:
        count = counts[key]
        console.print(f"  {label}: {count}")
        for path in examples[key]:
            console.print(f"    {path}")

    dup_count = counts["duplicate_jobs"]
    console.print(f"  Duplicate job filenames across buckets: {dup_count} [report-only]")
    for entry in examples["duplicate_jobs"]:
        buckets_str = ", ".join(entry["buckets"])
        console.print(f"    {entry['job_name']} — buckets: {buckets_str}")

    if fix and fix_results:
        console.print()
        if dry_run:
            console.print(
                f"  Would quarantine orphan sidecars: {fix_results['orphan_sidecars_would_quarantine']}"
            )
            console.print(
                f"  Would quarantine orphan approvals: {fix_results['orphan_approvals_would_quarantine']}"
            )
            paths = fix_results["quarantined_paths"]
            if paths:
                console.print("  Preview (up to 10 paths that would move):")
                for p in paths:
                    console.print(f"    {p}")
            console.print()
            console.print("[yellow]Hint:[/yellow] Run with --fix --yes to apply quarantine.")
        else:
            console.print(
                f"  Quarantined orphan sidecars: {fix_results['orphan_sidecars_quarantined']}"
            )
            console.print(
                f"  Quarantined orphan approvals: {fix_results['orphan_approvals_quarantined']}"
            )
            paths = fix_results["quarantined_paths"]
            if paths:
                console.print("  Quarantined paths (up to 10):")
                for p in paths:
                    console.print(f"    {p}")
            if fix_results.get("errors"):
                for err in fix_results["errors"]:
                    console.print(f"  [red]Warning:[/red] {err}")

    console.print()
    if fix and not dry_run:
        console.print(
            "[dim]No deletions performed; quarantined files can be restored manually.[/dim]"
        )
    else:
        console.print("[dim]Report-only; no changes made.[/dim]")
