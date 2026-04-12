from __future__ import annotations

import sys
import time
from pathlib import Path

import typer
from rich.console import Console

console = Console()

_CONFIG_GUARD_MESSAGE = "No configuration found. Run voxera setup to get started."


def require_config(*, config_path: Path | None = None) -> None:
    """Exit with a clear hint when user config has not been created yet.

    Intended for runtime command surfaces that cannot do useful work
    without a config.yml.  Setup, doctor, version, config-show, and help
    flows must remain unguarded.

    Skips the check when ``--help`` is present on the command line so that
    help text remains accessible before first-run setup.
    """
    if "--help" in sys.argv:
        return

    from .config import default_config_path

    path = config_path or default_config_path()
    if not path.exists():
        console.print(_CONFIG_GUARD_MESSAGE)
        raise typer.Exit(code=1)


RUN_ARG_OPTION = typer.Option(None, "--arg", help="Key=Value args (repeat --arg for multiple).")
OUT_PATH_OPTION = typer.Option(..., "--out", help="Output zip file path.")
OPS_BUNDLE_ARCHIVE_DIR_OPTION = typer.Option(
    None,
    "--dir",
    help="Archive directory for ops bundle outputs. Defaults to VOXERA_OPS_BUNDLE_DIR or notes/queue/_archive/<timestamp>/.",
)
SNAPSHOT_PATH_OPTION = typer.Option(
    None,
    "--path",
    "--out",
    help="Snapshot output file path. Defaults to <queue_root>/_ops/config_snapshot.json.",
)
DEMO_QUEUE_DIR_OPTION = typer.Option(None, "--queue-dir", help="Queue directory path.")


def now_ms() -> int:
    return int(time.time() * 1000)


def queue_dir_path(queue_dir: str) -> Path:
    return Path(queue_dir).expanduser().resolve()
