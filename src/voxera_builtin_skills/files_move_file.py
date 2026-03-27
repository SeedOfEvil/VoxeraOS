from __future__ import annotations

import shutil
from pathlib import Path

from voxera.models import RunResult
from voxera.skills.path_boundaries import PathBoundaryError, normalize_confined_path
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result

ALLOWED_ROOT = Path.home() / "VoxeraOS" / "notes"


def _resolve_existing_safe_path(path: str) -> Path:
    return normalize_confined_path(path=path, allowed_root=ALLOWED_ROOT, must_exist=True)


def _resolve_new_safe_path(path: str) -> Path:
    return normalize_confined_path(path=path, allowed_root=ALLOWED_ROOT, must_exist=False)


def run(source_path: str, destination_path: str, overwrite: bool = False) -> RunResult:
    try:
        source = _resolve_existing_safe_path(source_path)
        destination = _resolve_new_safe_path(destination_path)
    except FileNotFoundError:
        return RunResult(
            ok=False,
            error=f"Source path not found: {source_path}",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"Source path not found: {source_path}",
                    machine_payload={
                        "source_path": source_path,
                        "destination_path": destination_path,
                    },
                    operator_note="Provide an existing source path under notes scope.",
                    next_action_hint="provide_existing_source",
                    retryable=True,
                    blocked=False,
                    approval_status="none",
                    error=f"Source path not found: {source_path}",
                    error_class="not_found",
                )
            },
        )
    except PathBoundaryError as exc:
        return RunResult(
            ok=False,
            error=str(exc),
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Rejected file move outside allowlist",
                    machine_payload={
                        "source_path": source_path,
                        "destination_path": destination_path,
                        "allowed_root": str(ALLOWED_ROOT),
                    },
                    operator_note="Move source and destination must stay within allowed notes directory.",
                    next_action_hint="provide_allowed_path",
                    retryable=False,
                    blocked=exc.error_class == "path_blocked_scope",
                    approval_status="none",
                    error=str(exc),
                    error_class=exc.error_class,
                )
            },
        )

    if destination.exists() and not overwrite:
        return RunResult(
            ok=False,
            error=f"Destination already exists: {destination}",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Rejected move because destination exists",
                    machine_payload={"destination_path": str(destination), "overwrite": overwrite},
                    operator_note="Set overwrite=true to replace existing destination.",
                    next_action_hint="set_overwrite_true",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error=f"Destination already exists: {destination}",
                    error_class="already_exists",
                )
            },
        )

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and overwrite:
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()
        source.replace(destination)
        return RunResult(
            ok=True,
            output=f"Moved path to {destination}",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"Moved path to {destination}",
                    machine_payload={
                        "source_path": str(source),
                        "destination_path": str(destination),
                        "overwrite": overwrite,
                    },
                    operator_note="Move completed in confined notes scope.",
                    next_action_hint="continue",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                )
            },
        )
    except Exception as exc:
        return RunResult(
            ok=False,
            error=repr(exc),
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Failed to move file",
                    machine_payload={
                        "source_path": str(source),
                        "destination_path": str(destination),
                        "exception": repr(exc),
                    },
                    operator_note="Inspect file permissions and destination path.",
                    next_action_hint="inspect_file_permissions",
                    retryable=True,
                    blocked=False,
                    approval_status="none",
                    error=repr(exc),
                    error_class="io_error",
                )
            },
        )
