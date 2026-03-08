from __future__ import annotations

from pathlib import Path

from voxera.models import RunResult
from voxera.skills.path_boundaries import PathBoundaryError, normalize_confined_path
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result

ALLOWED_ROOT = Path.home() / "VoxeraOS" / "notes"


def _resolve_safe_path(path: str) -> Path:
    return normalize_confined_path(path=path, allowed_root=ALLOWED_ROOT, must_exist=True)


def run(path: str) -> RunResult:
    try:
        target = _resolve_safe_path(path)
    except FileNotFoundError:
        return RunResult(
            ok=False,
            error=f"File not found: {path}",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"File not found: {path}",
                    machine_payload={"path": path},
                    operator_note="The requested file does not exist yet.",
                    next_action_hint="create_file_then_retry",
                    retryable=True,
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
                    summary="Rejected read outside allowlist",
                    machine_payload={"path": path, "allowed_root": str(ALLOWED_ROOT)},
                    operator_note="Read path must stay within allowed notes directory.",
                    next_action_hint="provide_allowed_path",
                    retryable=False,
                    error_class=exc.error_class,
                )
            },
        )

    try:
        text = target.read_text(encoding="utf-8")
        return RunResult(
            ok=True,
            output=text,
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"Read text from {target}",
                    machine_payload={"path": str(target), "bytes": len(text.encode("utf-8"))},
                    operator_note="File content returned in output field.",
                    next_action_hint="continue",
                )
            },
        )
    except Exception as exc:
        return RunResult(
            ok=False,
            error=repr(exc),
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Failed to read text file",
                    machine_payload={"path": str(target), "exception": repr(exc)},
                    operator_note="Inspect file permissions or encoding.",
                    next_action_hint="inspect_file_permissions",
                    retryable=True,
                    error_class="io_error",
                )
            },
        )
