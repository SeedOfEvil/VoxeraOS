from __future__ import annotations

import subprocess

from voxera.models import RunResult
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result


def run() -> RunResult:
    try:
        output = subprocess.check_output(["wmctrl", "-l"], text=True)
        windows = [line.strip() for line in output.splitlines() if line.strip()]
        return RunResult(
            ok=True,
            data={
                "windows": windows,
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"Listed {len(windows)} open windows",
                    machine_payload={"count": len(windows), "windows": windows},
                    operator_note="Window list captured from wmctrl.",
                    next_action_hint="continue",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                ),
            },
            output=f"Found {len(windows)} windows",
        )
    except FileNotFoundError:
        error = "wmctrl not found"
        return RunResult(
            ok=False,
            error=error,
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Window listing tool unavailable",
                    machine_payload={"launcher": "wmctrl"},
                    operator_note="Install wmctrl to list window handles.",
                    next_action_hint="install_window_tool",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error=error,
                    error_class="missing_dependency",
                )
            },
        )
    except subprocess.CalledProcessError as exc:
        error = f"wmctrl failed: {exc}"
        return RunResult(
            ok=False,
            error=error,
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Window listing command failed",
                    machine_payload={"launcher": "wmctrl"},
                    operator_note="wmctrl returned a non-zero exit code.",
                    next_action_hint="inspect_window_manager",
                    retryable=True,
                    blocked=False,
                    approval_status="none",
                    error=error,
                    error_class="window_query_failed",
                )
            },
        )
