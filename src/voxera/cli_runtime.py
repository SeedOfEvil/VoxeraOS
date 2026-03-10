from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer
from rich.table import Table

from .cli_common import console, queue_dir_path
from .config import AppConfig
from .core.queue_daemon import MissionQueueDaemon, QueueLockError


def setup_impl(*, run_setup: Callable[[], Any]) -> None:
    asyncio.run(run_setup())


def demo_cmd_impl(
    *,
    run_demo: Callable[..., dict[str, Any]],
    queue_dir: Path | None,
    online: bool,
    yes: bool,
    json_output: bool,
) -> None:
    result = run_demo(queue_dir=queue_dir, online=online, yes=yes)
    if json_output:
        typer.echo(json.dumps(result, sort_keys=True))
        return

    table = Table(title="Voxera Demo Checklist")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    for check in result["checks"]:
        table.add_row(str(check["name"]), str(check["status"]), str(check["detail"]))
    console.print(table)

    if result["created_jobs"]:
        console.print("Created demo jobs:")
        for job_name in result["created_jobs"]:
            console.print(f"- {job_name}")

    cleanup = result["cleanup"]
    if cleanup["performed"]:
        console.print(f"Optional cleanup removed {cleanup['removed']} demo-scoped item(s).")
    else:
        console.print(
            "Optional cleanup skipped (run with --yes to remove demo-* items created for demos)."
        )

    console.print(f"Overall demo status: {result['status']}")


def status_impl(*, load_config: Callable[[], AppConfig]) -> None:
    cfg = load_config()
    table = Table(title="Voxera Status")
    table.add_column("Key")
    table.add_column("Value")
    table.add_row("mode", cfg.mode)
    table.add_row("cloud_allowed", str(cfg.privacy.cloud_allowed))
    table.add_row("redact_logs", str(cfg.privacy.redact_logs))
    table.add_row("brains", ", ".join(cfg.brain.keys()) if cfg.brain else "(not configured)")
    console.print(table)


def audit_impl(*, tail: Callable[[int], list[dict[str, Any]]], n: int) -> None:
    events = tail(n)
    for event in events:
        console.print(event)


def panel_impl(
    *,
    load_runtime_config: Callable[..., Any],
    host: str | None,
    port: int | None,
) -> None:
    import uvicorn

    runtime_cfg = load_runtime_config(overrides={"panel_host": host, "panel_port": port})
    uvicorn.run(
        "voxera.panel.app:app",
        host=runtime_cfg.panel_host,
        port=runtime_cfg.panel_port,
        reload=False,
    )


def daemon_impl(
    *,
    MissionQueueDaemon_cls: type[MissionQueueDaemon],
    queue_lock_error_cls: type[QueueLockError],
    once: bool,
    queue_dir: str,
    poll_interval: float,
    auto_approve_ask: bool,
) -> None:
    daemon = MissionQueueDaemon_cls(
        queue_root=queue_dir_path(queue_dir),
        poll_interval=poll_interval,
        auto_approve_ask=auto_approve_ask,
    )
    try:
        daemon.run(once=once)
    except queue_lock_error_cls as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except KeyboardInterrupt:
        console.print("Queue daemon stopped.")


def vera_impl(*, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(
        "voxera.vera_web.app:app",
        host=host,
        port=port,
        reload=False,
    )
