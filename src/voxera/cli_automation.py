"""Operator CLI for automation inspection, control, and runner invocation.

Commands:

- ``voxera automation list``        — show saved definitions from storage.
- ``voxera automation show <id>``   — detailed view of a single definition.
- ``voxera automation enable <id>`` — set ``enabled=True`` and persist.
- ``voxera automation disable <id>`` — set ``enabled=False`` and persist.
- ``voxera automation history <id>`` — show history records for a definition.
- ``voxera automation run-now <id>`` — immediately process a definition
  through the existing runner (queue-submitting only).
- ``voxera automation run-due-once`` — evaluate all due definitions once.

All commands inspect and manage saved automation definitions. The ``run-now``
and ``run-due-once`` commands submit through the existing canonical runner /
inbox path — the queue remains the execution boundary.
"""

from __future__ import annotations

import json

import typer
from rich.table import Table

from .automation.history import list_history_records
from .automation.runner import (
    AutomationRunResult,
    process_automation_definition,
    run_automation_once,
    run_due_automations_locked,
)
from .automation.store import (
    AutomationNotFoundError,
    AutomationStoreError,
    delete_automation_definition,
    list_automation_definitions,
    load_automation_definition,
    save_automation_definition,
)
from .cli_common import console, queue_dir_path
from .paths import queue_root_display

automation_app = typer.Typer(
    help=(
        "Automation operator CLI. Inspect, control, and run saved "
        "automation definitions. Queue remains the execution boundary."
    )
)


def _render_results_table(results: list[AutomationRunResult]) -> None:
    table = Table(title="Automation Runner Results")
    table.add_column("Automation ID")
    table.add_column("Trigger")
    table.add_column("Outcome")
    table.add_column("Queue Job Ref")
    table.add_column("Message")
    if not results:
        table.add_row("-", "-", "-", "-", "No automation definitions found")
    else:
        for result in results:
            table.add_row(
                result.automation_id,
                result.trigger_kind or "-",
                result.outcome,
                result.queue_job_ref or "-",
                result.message,
            )
    console.print(table)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@automation_app.command("list")
def automation_list(
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
) -> None:
    """List saved automation definitions."""
    queue_root = queue_dir_path(queue_dir)
    definitions = list_automation_definitions(queue_root)
    table = Table(title="Automation Definitions")
    table.add_column("ID")
    table.add_column("Enabled")
    table.add_column("Trigger Kind")
    table.add_column("Next Run At (ms)")
    table.add_column("Last Run At (ms)")
    table.add_column("Last Job Ref")
    if not definitions:
        table.add_row("-", "-", "-", "-", "-", "-")
    else:
        for defn in definitions:
            table.add_row(
                defn.id,
                str(defn.enabled),
                defn.trigger_kind,
                str(defn.next_run_at_ms) if defn.next_run_at_ms is not None else "-",
                str(defn.last_run_at_ms) if defn.last_run_at_ms is not None else "-",
                defn.last_job_ref or "-",
            )
    console.print(table)


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@automation_app.command("show")
def automation_show(
    automation_id: str = typer.Argument(..., help="Automation definition id."),
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
) -> None:
    """Show a single automation definition in detail."""
    queue_root = queue_dir_path(queue_dir)
    try:
        defn = load_automation_definition(automation_id, queue_root)
    except AutomationNotFoundError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except AutomationStoreError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print_json(json.dumps(defn.model_dump(mode="json"), indent=2, sort_keys=True))


# ---------------------------------------------------------------------------
# enable / disable
# ---------------------------------------------------------------------------


@automation_app.command("enable")
def automation_enable(
    automation_id: str = typer.Argument(..., help="Automation definition id."),
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
) -> None:
    """Enable a saved automation definition."""
    queue_root = queue_dir_path(queue_dir)
    try:
        defn = load_automation_definition(automation_id, queue_root)
    except AutomationNotFoundError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except AutomationStoreError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if defn.enabled:
        console.print(f"Automation [bold]{automation_id}[/bold] is already enabled.")
        return

    updated = defn.model_copy(update={"enabled": True})
    try:
        save_automation_definition(updated, queue_root)
    except (AutomationStoreError, OSError) as exc:
        console.print(f"[red]ERROR:[/red] failed to save: {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"Automation [bold]{automation_id}[/bold] enabled.")


@automation_app.command("disable")
def automation_disable(
    automation_id: str = typer.Argument(..., help="Automation definition id."),
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
) -> None:
    """Disable a saved automation definition."""
    queue_root = queue_dir_path(queue_dir)
    try:
        defn = load_automation_definition(automation_id, queue_root)
    except AutomationNotFoundError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except AutomationStoreError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if not defn.enabled:
        console.print(f"Automation [bold]{automation_id}[/bold] is already disabled.")
        return

    updated = defn.model_copy(update={"enabled": False})
    try:
        save_automation_definition(updated, queue_root)
    except (AutomationStoreError, OSError) as exc:
        console.print(f"[red]ERROR:[/red] failed to save: {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"Automation [bold]{automation_id}[/bold] disabled.")


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@automation_app.command("delete")
def automation_delete(
    automation_id: str = typer.Argument(..., help="Automation definition id."),
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
) -> None:
    """Delete a saved automation definition.

    Only the definition file is removed. History records under
    automations/history/ are preserved as audit trail.
    """
    queue_root = queue_dir_path(queue_dir)
    try:
        delete_automation_definition(automation_id, queue_root)
    except AutomationNotFoundError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except AutomationStoreError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"Automation [bold]{automation_id}[/bold] deleted. History records preserved.")


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


@automation_app.command("history")
def automation_history(
    automation_id: str = typer.Argument(..., help="Automation definition id."),
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
) -> None:
    """Show run history records for an automation definition."""
    queue_root = queue_dir_path(queue_dir)
    # Validate that the automation exists before listing history.
    try:
        load_automation_definition(automation_id, queue_root)
    except AutomationNotFoundError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except AutomationStoreError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    records = list_history_records(queue_root, automation_id)
    table = Table(title=f"History for {automation_id}")
    table.add_column("Run ID")
    table.add_column("Triggered At (ms)")
    table.add_column("Outcome")
    table.add_column("Queue Job Ref")
    table.add_column("Message")
    if not records:
        table.add_row("-", "-", "-", "-", "No history records found")
    else:
        for record in records:
            table.add_row(
                str(record.get("run_id", "-")),
                str(record.get("triggered_at_ms", "-")),
                str(record.get("outcome", "-")),
                str(record.get("queue_job_ref") or "-"),
                str(record.get("message", "-")),
            )
    console.print(table)


# ---------------------------------------------------------------------------
# run-now
# ---------------------------------------------------------------------------


@automation_app.command("run-now")
def automation_run_now(
    automation_id: str = typer.Argument(..., help="Automation definition id."),
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
) -> None:
    """Force an immediate run of a single automation through the runner.

    This command bypasses the due-time check and fires the definition
    immediately through the existing canonical runner / inbox path. The
    queue remains the execution boundary — no skills are executed
    directly. Disabled definitions and unsupported trigger kinds are
    still rejected.
    """
    queue_root = queue_dir_path(queue_dir)
    try:
        defn = load_automation_definition(automation_id, queue_root)
    except AutomationNotFoundError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except AutomationStoreError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    result = process_automation_definition(defn, queue_root, force=True)
    _render_results_table([result])


# ---------------------------------------------------------------------------
# run-due-once (existing)
# ---------------------------------------------------------------------------


@automation_app.command("run-due-once")
def automation_run_due_once(
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
    automation_id: str | None = typer.Option(
        None,
        "--id",
        help=(
            "Restrict the run to a single automation id. When omitted, every "
            "valid definition under the queue root is considered once."
        ),
    ),
) -> None:
    """Evaluate due automation definitions once and emit queue jobs.

    Acquires the automation runner single-writer lock before evaluating
    definitions. If the lock is already held (another runner is active),
    exits cleanly with an operator-facing message and exit code 0 so
    periodic schedulers (e.g. the systemd timer) do not treat a busy
    skip as a failure.
    """
    queue_root = queue_dir_path(queue_dir)
    if automation_id is not None:
        try:
            result = run_automation_once(automation_id, queue_root)
        except AutomationNotFoundError as exc:
            console.print(f"[red]ERROR:[/red] {exc}")
            raise typer.Exit(code=1) from exc
        except AutomationStoreError as exc:
            console.print(f"[red]ERROR:[/red] {exc}")
            raise typer.Exit(code=1) from exc
        _render_results_table([result])
        return

    pass_result = run_due_automations_locked(queue_root)
    if pass_result.status == "busy":
        console.print(f"[yellow]BUSY:[/yellow] {pass_result.message}")
        return
    _render_results_table(pass_result.results)
