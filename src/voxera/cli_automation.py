"""Minimal operator CLI for the PR2 automation runner.

This is intentionally narrow: just enough to let an operator drain the
due automation definitions once from the command line and see what the
runner decided. No daemon, no watch loop, no authoring commands.
"""

from __future__ import annotations

import typer
from rich.table import Table

from .automation.runner import run_automation_once, run_due_automations
from .automation.store import AutomationNotFoundError, AutomationStoreError
from .cli_common import console, queue_dir_path
from .paths import queue_root_display

automation_app = typer.Typer(
    help=(
        "Minimal automation runner (PR2). Only once_at and delay triggers "
        "are active; everything else is skipped."
    )
)


def _render_results_table(results: list) -> None:
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
    """Evaluate due automation definitions once and emit queue jobs."""
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

    results = run_due_automations(queue_root)
    _render_results_table(results)
