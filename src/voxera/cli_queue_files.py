from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from .cli_common import console, queue_dir_path
from .cli_queue_payloads import (
    build_files_copy_move_args,
    build_files_find_args,
    build_files_grep_text_args,
    build_files_list_tree_args,
    build_files_queue_payload,
    build_files_rename_args,
)
from .core.inbox import add_inbox_payload
from .paths import queue_root_display

queue_files_app = typer.Typer(help="Queue-backed governed filesystem tool helpers")


def _enqueue_files_step(
    *,
    queue_dir: str,
    job_id: str | None,
    action: str,
    step_skill_id: str,
    step_args: dict[str, Any],
) -> Path:
    payload = build_files_queue_payload(
        action=action,
        step_skill_id=step_skill_id,
        step_args=step_args,
    )
    try:
        return add_inbox_payload(
            queue_dir_path(queue_dir),
            payload,
            job_id=job_id,
            source_lane="inbox_cli_files",
        )
    except (ValueError, FileExistsError) as exc:
        console.print(f"[red]ERROR:[/red] {exc}")
        raise typer.Exit(code=1) from exc


def _print_files_enqueue_result(*, created: Path, skill_id: str, step_args: dict[str, Any]) -> None:
    payload = json.loads(created.read_text(encoding="utf-8"))
    console.print(f"Enqueued filesystem job: {created}")
    console.print(f"ID: {payload.get('id', '')}")
    console.print(f"Skill: {skill_id}")
    console.print(f"Args: {json.dumps(step_args, sort_keys=True)}")
    console.print("Status: queued (job created in inbox; run daemon to execute)")


@queue_files_app.command("find")
def queue_files_find(
    root_path: str = typer.Option(
        ..., "--root-path", help="Root directory under allowed workspace."
    ),
    glob: str = typer.Option("*", "--glob", help="Glob filter for discovered paths."),
    name_contains: str | None = typer.Option(
        None, "--name-contains", help="Case-insensitive filename substring filter."
    ),
    max_depth: int = typer.Option(8, "--max-depth", min=0, help="Maximum recursive depth."),
    include_hidden: bool = typer.Option(
        False, "--include-hidden", help="Include dotfiles/directories."
    ),
    max_results: int = typer.Option(200, "--max-results", min=1, help="Maximum result count."),
    id: str | None = typer.Option(None, "--id", help="Optional queue job id."),
    queue_dir: str = typer.Option(
        queue_root_display(), "--queue-dir", help="Queue root directory."
    ),
):
    """Enqueue files.find as a governed queue job."""
    args = build_files_find_args(
        root_path=root_path,
        glob=glob,
        name_contains=name_contains,
        max_depth=max_depth,
        include_hidden=include_hidden,
        max_results=max_results,
    )
    created = _enqueue_files_step(
        queue_dir=queue_dir,
        job_id=id,
        action="find",
        step_skill_id="files.find",
        step_args=args,
    )
    _print_files_enqueue_result(created=created, skill_id="files.find", step_args=args)


@queue_files_app.command("grep")
def queue_files_grep_text(
    root_path: str = typer.Option(
        ..., "--root-path", help="Root directory under allowed workspace."
    ),
    pattern: str = typer.Option(..., "--pattern", help="Text pattern to search for."),
    case_sensitive: bool = typer.Option(
        False, "--case-sensitive", help="Match case-sensitively when true."
    ),
    max_depth: int = typer.Option(8, "--max-depth", min=0, help="Maximum recursive depth."),
    include_hidden: bool = typer.Option(
        False, "--include-hidden", help="Include dotfiles/directories."
    ),
    max_matches: int = typer.Option(200, "--max-matches", min=1, help="Maximum matches to return."),
    max_file_bytes: int = typer.Option(
        1_000_000, "--max-file-bytes", min=1, help="Skip files above this byte size."
    ),
    id: str | None = typer.Option(None, "--id", help="Optional queue job id."),
    queue_dir: str = typer.Option(
        queue_root_display(), "--queue-dir", help="Queue root directory."
    ),
):
    """Enqueue files.grep_text as a governed queue job."""
    args = build_files_grep_text_args(
        root_path=root_path,
        pattern=pattern,
        case_sensitive=case_sensitive,
        max_depth=max_depth,
        include_hidden=include_hidden,
        max_matches=max_matches,
        max_file_bytes=max_file_bytes,
    )
    created = _enqueue_files_step(
        queue_dir=queue_dir,
        job_id=id,
        action="grep_text",
        step_skill_id="files.grep_text",
        step_args=args,
    )
    _print_files_enqueue_result(created=created, skill_id="files.grep_text", step_args=args)


@queue_files_app.command("tree")
def queue_files_list_tree(
    root_path: str = typer.Option(
        ..., "--root-path", help="Root directory under allowed workspace."
    ),
    max_depth: int = typer.Option(4, "--max-depth", min=0, help="Maximum recursive depth."),
    include_hidden: bool = typer.Option(
        False, "--include-hidden", help="Include dotfiles/directories."
    ),
    max_entries: int = typer.Option(400, "--max-entries", min=1, help="Maximum tree entries."),
    id: str | None = typer.Option(None, "--id", help="Optional queue job id."),
    queue_dir: str = typer.Option(
        queue_root_display(), "--queue-dir", help="Queue root directory."
    ),
):
    """Enqueue files.list_tree as a governed queue job."""
    args = build_files_list_tree_args(
        root_path=root_path,
        max_depth=max_depth,
        include_hidden=include_hidden,
        max_entries=max_entries,
    )
    created = _enqueue_files_step(
        queue_dir=queue_dir,
        job_id=id,
        action="list_tree",
        step_skill_id="files.list_tree",
        step_args=args,
    )
    _print_files_enqueue_result(created=created, skill_id="files.list_tree", step_args=args)


@queue_files_app.command("copy")
def queue_files_copy(
    source_path: str = typer.Option(..., "--source-path", help="Source path."),
    destination_path: str = typer.Option(..., "--destination-path", help="Destination path."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite destination if present."),
    id: str | None = typer.Option(None, "--id", help="Optional queue job id."),
    queue_dir: str = typer.Option(
        queue_root_display(), "--queue-dir", help="Queue root directory."
    ),
):
    """Enqueue files.copy as a governed queue job."""
    args = build_files_copy_move_args(
        source_path=source_path,
        destination_path=destination_path,
        overwrite=overwrite,
    )
    created = _enqueue_files_step(
        queue_dir=queue_dir,
        job_id=id,
        action="copy",
        step_skill_id="files.copy",
        step_args=args,
    )
    _print_files_enqueue_result(created=created, skill_id="files.copy", step_args=args)


@queue_files_app.command("move")
def queue_files_move(
    source_path: str = typer.Option(..., "--source-path", help="Source path."),
    destination_path: str = typer.Option(..., "--destination-path", help="Destination path."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite destination if present."),
    id: str | None = typer.Option(None, "--id", help="Optional queue job id."),
    queue_dir: str = typer.Option(
        queue_root_display(), "--queue-dir", help="Queue root directory."
    ),
):
    """Enqueue files.move as a governed queue job."""
    args = build_files_copy_move_args(
        source_path=source_path,
        destination_path=destination_path,
        overwrite=overwrite,
    )
    created = _enqueue_files_step(
        queue_dir=queue_dir,
        job_id=id,
        action="move",
        step_skill_id="files.move",
        step_args=args,
    )
    _print_files_enqueue_result(created=created, skill_id="files.move", step_args=args)


@queue_files_app.command("rename")
def queue_files_rename(
    path: str = typer.Option(..., "--path", help="Existing path to rename."),
    new_name: str = typer.Option(..., "--new-name", help="New basename."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite destination if present."),
    id: str | None = typer.Option(None, "--id", help="Optional queue job id."),
    queue_dir: str = typer.Option(
        queue_root_display(), "--queue-dir", help="Queue root directory."
    ),
):
    """Enqueue files.rename as a governed queue job."""
    args = build_files_rename_args(path=path, new_name=new_name, overwrite=overwrite)
    created = _enqueue_files_step(
        queue_dir=queue_dir,
        job_id=id,
        action="rename",
        step_skill_id="files.rename",
        step_args=args,
    )
    _print_files_enqueue_result(created=created, skill_id="files.rename", step_args=args)
