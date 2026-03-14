from __future__ import annotations

from pathlib import Path

from voxera.models import RunResult
from voxera.skills.path_boundaries import PathBoundaryError, normalize_confined_path
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result

ALLOWED_ROOT = Path.home() / "VoxeraOS" / "notes"


def _resolve_safe_path(path: str) -> Path:
    return normalize_confined_path(path=path, allowed_root=ALLOWED_ROOT, must_exist=False)


def run(path: str, parents: bool = True, exist_ok: bool = True) -> RunResult:
    try:
        target = _resolve_safe_path(path)
    except PathBoundaryError as exc:
        return RunResult(
            ok=False,
            error=str(exc),
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Rejected mkdir outside allowlist",
                    machine_payload={"path": path, "allowed_root": str(ALLOWED_ROOT)},
                    operator_note="Directory path must stay within allowed notes directory.",
                    next_action_hint="provide_allowed_path",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error=str(exc),
                    error_class=exc.error_class,
                )
            },
        )

    already_exists = target.exists()
    if already_exists and not target.is_dir():
        return RunResult(
            ok=False,
            error=f"Path exists and is not a directory: {target}",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Rejected mkdir due to non-directory target",
                    machine_payload={"path": str(target)},
                    operator_note="Choose a path that is absent or already a directory.",
                    next_action_hint="provide_directory_path",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error=f"Path exists and is not a directory: {target}",
                    error_class="invalid_input",
                )
            },
        )

    if already_exists and not exist_ok:
        return RunResult(
            ok=False,
            error=f"Directory already exists: {target}",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Rejected mkdir because directory exists",
                    machine_payload={"path": str(target), "exist_ok": exist_ok},
                    operator_note="Set exist_ok=true to allow existing directories.",
                    next_action_hint="set_exist_ok_true",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error=f"Directory already exists: {target}",
                    error_class="already_exists",
                )
            },
        )

    try:
        target.mkdir(parents=parents, exist_ok=exist_ok)
        created = not already_exists
        return RunResult(
            ok=True,
            output=f"Directory ready at {target}",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"Directory ready at {target}",
                    machine_payload={
                        "path": str(target),
                        "created": created,
                        "parents": parents,
                        "exist_ok": exist_ok,
                    },
                    operator_note="Directory create/check completed in confined notes scope.",
                    next_action_hint="continue",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                )
            },
        )
    except FileNotFoundError:
        return RunResult(
            ok=False,
            error=f"Parent directory does not exist: {target.parent}",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Failed to create directory due to missing parent",
                    machine_payload={"path": str(target), "parents": parents},
                    operator_note="Set parents=true or provide an existing parent directory.",
                    next_action_hint="set_parents_true",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error=f"Parent directory does not exist: {target.parent}",
                    error_class="not_found",
                )
            },
        )
    except Exception as exc:
        return RunResult(
            ok=False,
            error=repr(exc),
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Failed to create directory",
                    machine_payload={"path": str(target), "exception": repr(exc)},
                    operator_note="Inspect directory permissions.",
                    next_action_hint="inspect_file_permissions",
                    retryable=True,
                    blocked=False,
                    approval_status="none",
                    error=repr(exc),
                    error_class="io_error",
                )
            },
        )
