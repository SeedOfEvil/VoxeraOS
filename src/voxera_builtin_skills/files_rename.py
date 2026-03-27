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


def run(path: str, new_name: str, overwrite: bool = False) -> RunResult:
    cleaned_name = new_name.strip()
    if (
        not cleaned_name
        or cleaned_name in {".", ".."}
        or "/" in cleaned_name
        or "\\" in cleaned_name
    ):
        return RunResult(
            ok=False,
            error=f"Invalid new_name: {new_name}",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Rejected invalid rename target",
                    machine_payload={"path": path, "new_name": new_name},
                    operator_note="new_name must be a single filename component.",
                    next_action_hint="provide_valid_new_name",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error=f"Invalid new_name: {new_name}",
                    error_class="invalid_input",
                )
            },
        )

    try:
        source = _resolve_existing_safe_path(path)
        destination = _resolve_new_safe_path(str(source.parent / cleaned_name))
    except FileNotFoundError:
        return RunResult(
            ok=False,
            error=f"Source path not found: {path}",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"Source path not found: {path}",
                    machine_payload={"path": path, "new_name": new_name},
                    operator_note="Provide an existing source path under notes scope.",
                    next_action_hint="provide_existing_source",
                    retryable=True,
                    blocked=False,
                    approval_status="none",
                    error=f"Source path not found: {path}",
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
                    summary="Rejected rename outside allowlist",
                    machine_payload={
                        "path": path,
                        "new_name": new_name,
                        "allowed_root": str(ALLOWED_ROOT),
                    },
                    operator_note="Rename path must stay within allowed notes directory.",
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
                    summary="Rejected rename because destination exists",
                    machine_payload={
                        "source_path": str(source),
                        "destination_path": str(destination),
                        "overwrite": overwrite,
                    },
                    operator_note="Set overwrite=true to replace existing destination name.",
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
        if destination.exists() and overwrite:
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()
        source.replace(destination)
        return RunResult(
            ok=True,
            output=f"Renamed path to {destination.name}",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"Renamed path to {destination.name}",
                    machine_payload={
                        "source_path": str(source),
                        "destination_path": str(destination),
                        "new_name": destination.name,
                        "overwrite": overwrite,
                    },
                    operator_note="Rename completed in confined notes scope.",
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
                    summary="Failed to rename path",
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
