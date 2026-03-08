from __future__ import annotations

import os
import platform
from pathlib import Path

from voxera.models import RunResult
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result


def run() -> RunResult:
    info = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cwd": str(Path.cwd()),
        "user": os.getenv("USER") or os.getenv("USERNAME") or "unknown",
    }
    return RunResult(
        ok=True,
        output=str(info),
        data={
            **info,
            SKILL_RESULT_KEY: build_skill_result(
                summary="Collected system status snapshot",
                machine_payload=info,
                operator_note="Read-only system metadata captured.",
                next_action_hint="continue",
                retryable=False,
                blocked=False,
                approval_status="none",
            ),
        },
    )
