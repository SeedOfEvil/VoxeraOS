from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import typer

from .cli_common import queue_dir_path
from .ops_bundle import build_job_bundle as build_ops_job_bundle
from .ops_bundle import build_system_bundle as build_ops_system_bundle
from .skills.registry import SkillRegistry


def ops_capabilities_impl(
    *, skill_registry_cls: type[SkillRegistry], generate_capabilities_snapshot: Callable
) -> None:
    reg = skill_registry_cls()
    snapshot = generate_capabilities_snapshot(reg)
    typer.echo(json.dumps(snapshot, sort_keys=True))


def ops_bundle_system_impl(*, queue_dir: str, archive_dir: Path | None) -> None:
    queue_root = queue_dir_path(queue_dir)
    out = build_ops_system_bundle(
        queue_root,
        archive_dir=archive_dir,
        prefer_queue_root_archive=True,
    )
    typer.echo(str(out.resolve()))


def ops_bundle_job_impl(*, job_ref: str, queue_dir: str, archive_dir: Path | None) -> None:
    queue_root = queue_dir_path(queue_dir)
    out = build_ops_job_bundle(
        queue_root,
        job_ref,
        archive_dir=archive_dir,
        prefer_queue_root_archive=True,
    )
    typer.echo(str(out.resolve()))
