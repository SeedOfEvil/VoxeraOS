from __future__ import annotations

import shutil
import subprocess

from voxera.models import RunResult
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result


def run(keep_open: bool = True) -> RunResult:
    """Open a terminal window without running demo/bootstrap commands.

    keep_open is retained for backward compatibility and currently has no effect
    because we intentionally launch a plain terminal session only.
    """
    if not shutil.which("gnome-terminal"):
        return RunResult(
            ok=False,
            error="gnome-terminal not found",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Terminal launcher unavailable",
                    machine_payload={"launcher": "gnome-terminal"},
                    operator_note="Install gnome-terminal or choose a different terminal skill.",
                    next_action_hint="install_launcher",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error="gnome-terminal not found",
                    error_class="missing_dependency",
                )
            },
        )

    subprocess.Popen(
        ["gnome-terminal"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return RunResult(
        ok=True,
        output="Opened terminal window",
        data={
            SKILL_RESULT_KEY: build_skill_result(
                summary="Opened terminal window",
                machine_payload={"launcher": "gnome-terminal", "keep_open": keep_open},
                operator_note="A new terminal window was requested. No command was executed.",
                next_action_hint="continue",
                retryable=False,
                blocked=False,
                approval_status="none",
            )
        },
    )
