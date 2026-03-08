from __future__ import annotations

import shutil
import subprocess

from voxera.models import RunResult
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result


def run(keep_open: bool = True) -> RunResult:
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

    lines = [
        "clear",
        "echo '--- Voxera Terminal Demo ---'",
        "echo 'Command: echo \"Hello, world!\"'",
        'echo "Hello, world!"',
    ]
    if keep_open:
        lines.append("read -rp 'Press Enter to close...'")

    script = "; ".join(lines)

    subprocess.Popen(
        ["gnome-terminal", "--", "bash", "-lc", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return RunResult(
        ok=True,
        output="Opened terminal and ran hello-world demo",
        data={
            SKILL_RESULT_KEY: build_skill_result(
                summary="Opened terminal hello-world demo",
                machine_payload={"launcher": "gnome-terminal", "keep_open": keep_open},
                operator_note="A new terminal window was requested.",
                next_action_hint="continue",
                retryable=False,
                blocked=False,
                approval_status="none",
            )
        },
    )
