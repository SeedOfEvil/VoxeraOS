from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from voxera.models import RunResult
from voxera.skills.path_boundaries import PathBoundaryError, normalize_confined_path
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result

ALLOWED_ROOT = Path.home() / "VoxeraOS" / "notes"


def _resolve_safe_path(path: str) -> Path:
    return normalize_confined_path(path=path, allowed_root=ALLOWED_ROOT, must_exist=True)


def _as_utc_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def run(path: str) -> RunResult:
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
                    summary="Rejected stat outside allowlist",
                    machine_payload={"path": path, "allowed_root": str(ALLOWED_ROOT)},
                    operator_note="Path must stay within allowed notes directory.",
                    next_action_hint="provide_allowed_path",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error=str(exc),
                    error_class=exc.error_class,
                )
            },
        )

    try:
        stat_result = target.stat()
        kind = "directory" if target.is_dir() else "file" if target.is_file() else "other"
        return RunResult(
            ok=True,
            output=f"Collected stat for {target}",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"Collected stat for {target}",
                    machine_payload={
                        "path": str(target),
                        "kind": kind,
                        "size_bytes": stat_result.st_size,
                        "modified_ts": _as_utc_iso(stat_result.st_mtime),
                        "created_ts": _as_utc_iso(stat_result.st_ctime),
                    },
                    operator_note="Basic metadata returned in machine payload.",
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
                    summary="Failed to stat path",
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
