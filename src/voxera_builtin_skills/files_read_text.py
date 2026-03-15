from __future__ import annotations

from pathlib import Path

from voxera.models import RunResult
from voxera.skills.path_boundaries import PathBoundaryError, normalize_confined_path
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result

ALLOWED_ROOT = Path.home() / "VoxeraOS" / "notes"

# Boundedness: max characters of file content stored in machine_payload.
_MAX_CONTENT_CHARS = 2048


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
                    summary="Rejected read outside allowlist",
                    machine_payload={"path": path, "allowed_root": str(ALLOWED_ROOT)},
                    operator_note="Read path must stay within allowed notes directory.",
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
        text = target.read_text(encoding="utf-8")
        byte_count = len(text.encode("utf-8"))
        line_count = text.count("\n") + (1 if text and not text.endswith("\n") else 0)

        # Include bounded content in machine_payload for answer-first surfacing.
        content_excerpt = text[:_MAX_CONTENT_CHARS]
        content_truncated = len(text) > _MAX_CONTENT_CHARS

        payload: dict[str, object] = {
            "path": str(target),
            "bytes": byte_count,
            "line_count": line_count,
            "content": content_excerpt,
            "content_truncated": content_truncated,
        }
        return RunResult(
            ok=True,
            output=text,
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"Read text from {target}",
                    machine_payload=payload,
                    operator_note="File content returned in output field and bounded in machine_payload.content.",
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
                    summary="Failed to read text file",
                    machine_payload={"path": str(target), "exception": repr(exc)},
                    operator_note="Inspect file permissions or encoding.",
                    next_action_hint="inspect_file_permissions",
                    retryable=True,
                    blocked=False,
                    approval_status="none",
                    error=repr(exc),
                    error_class="io_error",
                )
            },
        )
