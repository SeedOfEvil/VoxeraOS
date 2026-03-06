from __future__ import annotations

import time
from pathlib import Path

import typer
from rich.console import Console

console = Console()

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
