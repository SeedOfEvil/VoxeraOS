from __future__ import annotations

import typer
from rich.table import Table

from .cli_common import console, queue_dir_path
from .core.queue_daemon import MissionQueueDaemon
from .paths import queue_root_display


def queue_approvals_list(
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
) -> None:
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
) -> None:
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


def queue_approvals_deny(
    ref: str,
    queue_dir: str = typer.Option(
        queue_root_display(),
        "--queue-dir",
        help="Queue directory containing JSON mission jobs.",
    ),
) -> None:
    """Deny a pending queue job by filename or id."""
    daemon = MissionQueueDaemon(queue_root=queue_dir_path(queue_dir))
    try:
        daemon.resolve_approval(ref, approve=False)
    except FileNotFoundError as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print("Denied. Job moved to failed/.")
