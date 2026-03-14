from __future__ import annotations

from pathlib import Path

from voxera.models import RunResult
from voxera.skills.path_boundaries import PathBoundaryError, normalize_confined_path
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result

ALLOWED_ROOT = Path.home() / "VoxeraOS" / "notes"


def _resolve_safe_path(path: str) -> Path:
    return normalize_confined_path(path=path, allowed_root=ALLOWED_ROOT, must_exist=True)


def run(path: str, include_hidden: bool = False) -> RunResult:
    try:
        target = _resolve_safe_path(path)
    except FileNotFoundError:
        return RunResult(
            ok=False,
            error=f"Path not found: {path}",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"Path not found: {path}",
                    machine_payload={"path": path},
                    operator_note="The requested path does not exist.",
                    next_action_hint="provide_existing_path",
                    retryable=True,
                    blocked=False,
                    approval_status="none",
                    error=f"Path not found: {path}",
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
                    summary="Rejected list outside allowlist",
                    machine_payload={"path": path, "allowed_root": str(ALLOWED_ROOT)},
                    operator_note="List path must stay within allowed notes directory.",
                    next_action_hint="provide_allowed_path",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error=str(exc),
                    error_class=exc.error_class,
                )
            },
        )

    if not target.is_dir():
        return RunResult(
            ok=False,
            error=f"Path is not a directory: {target}",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Rejected non-directory path",
                    machine_payload={"path": str(target)},
                    operator_note="Provide a directory path to list files.",
                    next_action_hint="provide_directory_path",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error=f"Path is not a directory: {target}",
                    error_class="invalid_input",
                )
            },
        )

    try:
        entries = []
        for child in sorted(target.iterdir(), key=lambda item: item.name.lower()):
            if not include_hidden and child.name.startswith("."):
                continue
            relative = child.relative_to(ALLOWED_ROOT.expanduser().resolve(strict=False))
            entries.append(
                {
                    "name": child.name,
                    "path": str(relative),
                    "is_dir": child.is_dir(),
                    "size_bytes": child.stat().st_size,
                }
            )
        return RunResult(
            ok=True,
            output=f"Listed {len(entries)} entries under {target}",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"Listed {len(entries)} entries under {target}",
                    machine_payload={
                        "path": str(target),
                        "entries": entries,
                        "entry_count": len(entries),
                        "include_hidden": include_hidden,
                    },
                    operator_note="Directory listing returned in machine payload.",
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
                    summary="Failed to list directory",
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
