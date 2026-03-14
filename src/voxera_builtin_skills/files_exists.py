from __future__ import annotations

from pathlib import Path

from voxera.models import RunResult
from voxera.skills.path_boundaries import PathBoundaryError, normalize_confined_path
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result

ALLOWED_ROOT = Path.home() / "VoxeraOS" / "notes"


def _resolve_safe_path(path: str) -> Path:
    return normalize_confined_path(path=path, allowed_root=ALLOWED_ROOT, must_exist=False)


def run(path: str) -> RunResult:
    try:
        target = _resolve_safe_path(path)
    except PathBoundaryError as exc:
        return RunResult(
            ok=False,
            error=str(exc),
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Rejected exists check outside allowlist",
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

    exists = target.exists()
    kind = "directory" if target.is_dir() else "file" if target.is_file() else "other"
    return RunResult(
        ok=True,
        output=f"Path exists={exists}: {target}",
        data={
            SKILL_RESULT_KEY: build_skill_result(
                summary=f"Checked path existence for {target}",
                machine_payload={"path": str(target), "exists": exists, "kind": kind},
                operator_note="Existence status returned in machine payload.",
                next_action_hint="continue",
                retryable=False,
                blocked=False,
                approval_status="none",
            )
        },
    )
