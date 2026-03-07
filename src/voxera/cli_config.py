from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer

LoadRuntimeConfig = Callable[[], Any]


def config_show_impl(*, load_runtime_config: LoadRuntimeConfig) -> None:
    cfg = load_runtime_config()
    typer.echo(json.dumps(cfg.to_safe_dict(), sort_keys=True))


def config_snapshot_impl(
    *,
    load_runtime_config: LoadRuntimeConfig,
    write_config_snapshot: Any,
    write_config_fingerprint: Any,
    path: Path | None,
) -> None:
    cfg = load_runtime_config()
    target = (
        path.expanduser().resolve()
        if path is not None
        else cfg.queue_root / "_ops" / "config_snapshot.json"
    )
    written = write_config_snapshot(target.parent, cfg, filename=target.name)
    write_config_fingerprint(cfg.queue_root, cfg)
    typer.echo(str(written.resolve()))


def config_validate_impl(*, load_runtime_config: LoadRuntimeConfig) -> None:
    try:
        cfg = load_runtime_config()
    except ValueError as exc:
        typer.echo(f"ERROR: {exc}")
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps({"status": "ok", "config_path": str(cfg.config_path)}, sort_keys=True))
