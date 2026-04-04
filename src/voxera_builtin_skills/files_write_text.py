from __future__ import annotations

from pathlib import Path
from typing import Literal

from voxera.models import RunResult
from voxera.skills.path_boundaries import PathBoundaryError, normalize_confined_path
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result

ALLOWED_ROOT = Path.home() / "VoxeraOS" / "notes"

# Bounded content excerpt limit — same as files_read_text.
_MAX_CONTENT_CHARS = 2048


def _resolve_safe_path(path: str) -> Path:
    return normalize_confined_path(path=path, allowed_root=ALLOWED_ROOT, must_exist=False)


def run(path: str, text: str, mode: Literal["append", "overwrite"] = "overwrite") -> RunResult:
    try:
        target = _resolve_safe_path(path)
    except PathBoundaryError as exc:
        return RunResult(
            ok=False,
            error=str(exc),
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Rejected write outside allowlist",
                    machine_payload={"path": path, "allowed_root": str(ALLOWED_ROOT)},
                    operator_note="Write path must stay within allowed notes directory.",
                    next_action_hint="provide_allowed_path",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error=str(exc),
                    error_class=exc.error_class,
                )
            },
        )
    except Exception as exc:
        return RunResult(ok=False, error=repr(exc))

    if mode not in {"append", "overwrite"}:
        return RunResult(
            ok=False,
            error="mode must be append or overwrite",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Rejected invalid write mode",
                    machine_payload={"mode": mode},
                    operator_note="Supported modes are append or overwrite.",
                    next_action_hint="provide_supported_mode",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error="mode must be append or overwrite",
                    error_class="invalid_input",
                )
            },
        )

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        write_mode = "a" if mode == "append" else "w"
        with target.open(write_mode, encoding="utf-8") as f:
            f.write(text)
        bytes_written = len(text.encode("utf-8"))
        action = "Appended" if mode == "append" else "Wrote"
        # Include bounded content excerpt for answer-first output surfacing.
        content_excerpt = text[:_MAX_CONTENT_CHARS]
        content_truncated = len(text) > _MAX_CONTENT_CHARS
        return RunResult(
            ok=True,
            output=f"{action} text to {target}",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"{action} text to {target}",
                    machine_payload={
                        "path": str(target),
                        "mode": mode,
                        "bytes": bytes_written,
                        "content": content_excerpt,
                        "content_truncated": content_truncated,
                    },
                    operator_note="Write completed in confined notes scope.",
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
                    summary="Failed to write text file",
                    machine_payload={"path": str(target), "exception": repr(exc)},
                    operator_note="Inspect file permissions and available disk space.",
                    next_action_hint="inspect_file_permissions",
                    retryable=True,
                    blocked=False,
                    approval_status="none",
                    error=repr(exc),
                    error_class="io_error",
                )
            },
        )
