from __future__ import annotations

from pathlib import Path

import typer

from .cli_common import OUT_PATH_OPTION, console, queue_dir_path
from .incident_bundle import BundleError, build_job_bundle, build_system_bundle
from .paths import queue_root_display


def queue_bundle(
    job_id: str | None = typer.Argument(None),
    system: bool = typer.Option(False, "--system", help="Export overall system bundle."),
    out: Path = OUT_PATH_OPTION,
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
) -> None:
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
