from __future__ import annotations

import typer

from .cli_common import console, queue_dir_path
from .core.queue_daemon import MissionQueueDaemon, QueueLockError
from .paths import queue_root_display


def queue_cancel(
    ref: str,
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
) -> None:
    """Cancel a queue job by id or filename."""
    daemon = MissionQueueDaemon(queue_root=queue_dir_path(queue_dir))
    try:
        moved = daemon.cancel_job(ref)
    except FileNotFoundError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"Cancelled: {moved.name} (moved to canceled/)")


def queue_retry(
    ref: str,
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
) -> None:
    """Retry a failed queue job by id or filename."""
    daemon = MissionQueueDaemon(queue_root=queue_dir_path(queue_dir))
    try:
        moved = daemon.retry_job(ref)
    except FileNotFoundError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"Re-queued: {moved.name} (inbox/)")


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
) -> None:
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


def queue_pause(
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
) -> None:
    """Pause queue processing."""
    daemon = MissionQueueDaemon(queue_root=queue_dir_path(queue_dir))
    daemon.pause()
    console.print("Queue processing paused.")


def queue_resume(
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
) -> None:
    """Resume queue processing."""
    daemon = MissionQueueDaemon(queue_root=queue_dir_path(queue_dir))
    daemon.resume()
    console.print("Queue processing resumed.")
