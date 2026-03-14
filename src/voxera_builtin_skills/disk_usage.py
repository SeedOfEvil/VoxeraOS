from __future__ import annotations

import shutil
from pathlib import Path

from voxera.models import RunResult
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result


def run() -> RunResult:
    try:
        usage = shutil.disk_usage(Path.home())
        total_gb = round(usage.total / (1024**3), 2)
        used_gb = round(usage.used / (1024**3), 2)
        free_gb = round(usage.free / (1024**3), 2)
        used_pct = round((usage.used / usage.total) * 100, 1) if usage.total > 0 else 0.0
        info = {
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "used_percent": used_pct,
            "mount_path": str(Path.home()),
        }
        return RunResult(
            ok=True,
            output=f"Disk: {used_gb}GB / {total_gb}GB ({used_pct}% used), {free_gb}GB free",
            data={
                **info,
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"Disk usage: {used_pct}% used, {free_gb}GB free",
                    machine_payload=info,
                    operator_note="Read-only disk usage snapshot from home partition.",
                    next_action_hint="continue",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                ),
            },
        )
    except Exception as exc:
        error = f"Failed to read disk usage: {exc}"
        return RunResult(
            ok=False,
            error=error,
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Disk usage query failed",
                    machine_payload={"exception": repr(exc)},
                    operator_note="Unable to query disk usage for home partition.",
                    next_action_hint="inspect_filesystem",
                    retryable=True,
                    blocked=False,
                    approval_status="none",
                    error=error,
                    error_class="io_error",
                )
            },
        )
