from __future__ import annotations

import json

import typer
from rich.table import Table

from .cli_common import console, queue_dir_path
from .core.inbox import add_inbox_job, list_inbox_jobs
from .paths import queue_root_display


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
) -> None:
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


def inbox_list(
    n: int = typer.Option(20, "--n", min=1, help="Number of recent inbox jobs to show."),
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
) -> None:
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
