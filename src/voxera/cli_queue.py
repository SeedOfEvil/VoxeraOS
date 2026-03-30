from __future__ import annotations

import json
from typing import Any

import typer
from rich.table import Table

from .cli_common import console, queue_dir_path
from .cli_queue_approvals import (
    queue_approvals_approve,
    queue_approvals_deny,
    queue_approvals_list,
)
from .cli_queue_bundle import queue_bundle
from .cli_queue_files import queue_files_app
from .cli_queue_health import queue_health, queue_health_reset
from .cli_queue_hygiene import artifacts_prune, queue_prune, queue_reconcile
from .cli_queue_inbox import inbox_add, inbox_list
from .cli_queue_lifecycle import (
    queue_cancel,
    queue_pause,
    queue_resume,
    queue_retry,
    queue_unlock,
)
from .core.queue_daemon import MissionQueueDaemon
from .core.queue_result_consumers import resolve_structured_execution
from .paths import queue_root_display

queue_app = typer.Typer(help="Queue job utilities")
queue_approvals_app = typer.Typer(help="Resolve pending queue approvals")
queue_lock_app = typer.Typer(help="Queue daemon lock utilities")
inbox_app = typer.Typer(help="Human-friendly queue inbox")
artifacts_app = typer.Typer(help="Artifact management utilities")

queue_app.add_typer(queue_approvals_app, name="approvals")
queue_app.add_typer(queue_lock_app, name="lock")
queue_app.add_typer(queue_files_app, name="files")


def register(app: typer.Typer) -> None:
    app.add_typer(artifacts_app, name="artifacts")
    app.add_typer(queue_app, name="queue")
    app.add_typer(inbox_app, name="inbox")


# ---------------------------------------------------------------------------
# Bundle command-family registration (before @-decorated queue_app commands
# to preserve subcommand ordering in help output)
# ---------------------------------------------------------------------------

queue_app.command("bundle")(queue_bundle)

# ---------------------------------------------------------------------------
# Approvals command-family registration (handlers in cli_queue_approvals.py)
# ---------------------------------------------------------------------------

queue_approvals_app.command("list")(queue_approvals_list)
queue_approvals_app.command("approve")(queue_approvals_approve)
queue_approvals_app.command("deny")(queue_approvals_deny)

# ---------------------------------------------------------------------------
# Inbox command-family registration (handlers in cli_queue_inbox.py)
# ---------------------------------------------------------------------------

inbox_app.command("add")(inbox_add)
inbox_app.command("list")(inbox_list)


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
            state: dict[str, Any] = {}
            if state_path.exists():
                try:
                    loaded_state = json.loads(state_path.read_text(encoding="utf-8"))
                    state = loaded_state if isinstance(loaded_state, dict) else {}
                except Exception:
                    lifecycle_rows.append((job.name, f"{bucket_name}: invalid-state"))
                    continue
            structured = resolve_structured_execution(
                artifacts_dir=daemon.artifacts / job.stem,
                state_sidecar=state,
                failed_sidecar=daemon._read_failed_error_sidecar(job)
                if bucket_name == "failed"
                else {},
            )
            lifecycle = str(
                structured.get("lifecycle_state") or state.get("lifecycle_state") or "-"
            )
            outcome = str(structured.get("terminal_outcome") or state.get("terminal_outcome") or "")
            current_step = int(
                structured.get("current_step_index") or state.get("current_step_index") or 0
            )
            total_steps = int(structured.get("total_steps") or state.get("total_steps") or 0)
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


# Health command-family registration (after queue_status to preserve
# subcommand ordering in help output)
queue_app.command("health")(queue_health)
queue_app.command("health-reset")(queue_health_reset)

# ---------------------------------------------------------------------------
# Lifecycle command-family registration (handlers in cli_queue_lifecycle.py)
# ---------------------------------------------------------------------------

queue_app.command("cancel")(queue_cancel)
queue_app.command("retry")(queue_retry)
queue_app.command("unlock")(queue_unlock)
queue_app.command("pause")(queue_pause)
queue_app.command("resume")(queue_resume)


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


# ---------------------------------------------------------------------------
# Hygiene command-family registration (after all @-decorated commands to
# preserve subcommand ordering in help output)
# ---------------------------------------------------------------------------

queue_app.command("prune")(queue_prune)
queue_app.command("reconcile")(queue_reconcile)
artifacts_app.command("prune")(artifacts_prune)
