from __future__ import annotations

from pathlib import Path

from voxera.models import RunResult
from voxera.skills.path_boundaries import PathBoundaryError, normalize_confined_path
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result

ALLOWED_ROOT = Path.home() / "VoxeraOS" / "notes"


def _resolve_safe_path(path: str) -> Path:
    return normalize_confined_path(path=path, allowed_root=ALLOWED_ROOT, must_exist=True)


def run(path: str, missing_ok: bool = False) -> RunResult:
    try:
        target = _resolve_safe_path(path)
    except FileNotFoundError:
        if missing_ok:
            return RunResult(
                ok=True,
                output=f"File already absent: {path}",
                data={
                    SKILL_RESULT_KEY: build_skill_result(
                        summary=f"File already absent: {path}",
                        machine_payload={"path": path, "deleted": False, "missing_ok": True},
                        operator_note="No delete needed because file was absent.",
                        next_action_hint="continue",
                        retryable=False,
                        blocked=False,
                        approval_status="none",
                    )
                },
            )
        return RunResult(
            ok=False,
            error=f"File not found: {path}",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"File not found: {path}",
                    machine_payload={"path": path, "missing_ok": missing_ok},
                    operator_note="The requested file does not exist.",
                    next_action_hint="provide_existing_path",
                    retryable=True,
                    blocked=False,
                    approval_status="none",
                    error=f"File not found: {path}",
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
                    summary="Rejected delete outside allowlist",
                    machine_payload={"path": path, "allowed_root": str(ALLOWED_ROOT)},
                    operator_note="Delete path must stay within allowed notes directory.",
                    next_action_hint="provide_allowed_path",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error=str(exc),
                    error_class=exc.error_class,
                )
            },
        )

    if not target.is_file():
        return RunResult(
            ok=False,
            error=f"Path is not a file: {target}",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Rejected delete of non-file path",
                    machine_payload={"path": str(target)},
                    operator_note="This skill only deletes regular files.",
                    next_action_hint="provide_file_path",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error=f"Path is not a file: {target}",
                    error_class="invalid_input",
                )
            },
        )

    try:
        size_bytes = target.stat().st_size
        target.unlink()
        return RunResult(
            ok=True,
            output=f"Deleted file {target}",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"Deleted file {target}",
                    machine_payload={
                        "path": str(target),
                        "deleted": True,
                        "size_bytes": size_bytes,
                        "missing_ok": missing_ok,
                    },
                    operator_note="File deletion completed in confined notes scope.",
                    next_action_hint="continue",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                )
            },
        )
    except FileNotFoundError:
        if missing_ok:
            return RunResult(
                ok=True,
                output=f"File already absent: {target}",
                data={
                    SKILL_RESULT_KEY: build_skill_result(
                        summary=f"File already absent: {target}",
                        machine_payload={"path": str(target), "deleted": False, "missing_ok": True},
                        operator_note="No delete needed because file was absent.",
                        next_action_hint="continue",
                        retryable=False,
                        blocked=False,
                        approval_status="none",
                    )
                },
            )
        return RunResult(
            ok=False,
            error=f"File not found: {target}",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"File not found: {target}",
                    machine_payload={"path": str(target), "missing_ok": missing_ok},
                    operator_note="The file was deleted by another process.",
                    next_action_hint="retry",
                    retryable=True,
                    blocked=False,
                    approval_status="none",
                    error=f"File not found: {target}",
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
                    summary="Failed to delete file",
                    machine_payload={"path": str(target), "exception": repr(exc)},
                    operator_note="Inspect file permissions.",
                    next_action_hint="inspect_file_permissions",
                    retryable=True,
                    blocked=False,
                    approval_status="none",
                    error=repr(exc),
                    error_class="io_error",
                )
            },
        )
