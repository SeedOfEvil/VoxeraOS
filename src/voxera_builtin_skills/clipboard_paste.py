from __future__ import annotations

import subprocess

from voxera.models import RunResult
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result


def run() -> RunResult:
    commands = [
        ["wl-paste", "--no-newline"],
        ["xclip", "-selection", "clipboard", "-o"],
    ]
    for cmd in commands:
        try:
            out = subprocess.check_output(cmd, text=True)
            return RunResult(
                ok=True,
                output=out,
                data={
                    SKILL_RESULT_KEY: build_skill_result(
                        summary="Pasted clipboard text",
                        machine_payload={"launcher": cmd[0], "chars": len(out)},
                        operator_note="Clipboard content returned in output field.",
                        next_action_hint="continue",
                        retryable=False,
                        blocked=False,
                        approval_status="none",
                    )
                },
            )
        except FileNotFoundError:
            continue
        except subprocess.CalledProcessError as exc:
            return RunResult(
                ok=False,
                error=f"Clipboard command failed: {exc}",
                data={
                    SKILL_RESULT_KEY: build_skill_result(
                        summary="Clipboard paste command failed",
                        machine_payload={"launcher": cmd[0]},
                        operator_note="Clipboard utility returned a non-zero exit code.",
                        next_action_hint="inspect_clipboard_tooling",
                        retryable=True,
                        blocked=False,
                        approval_status="none",
                        error=f"Clipboard command failed: {exc}",
                        error_class="clipboard_command_failed",
                    )
                },
            )

    error = "No clipboard tool found (wl-paste/xclip)"
    return RunResult(
        ok=False,
        error=error,
        data={
            SKILL_RESULT_KEY: build_skill_result(
                summary="No supported clipboard paste tool found",
                machine_payload={"candidates": ["wl-paste", "xclip"]},
                operator_note="Install wl-paste or xclip to enable clipboard paste.",
                next_action_hint="install_clipboard_tool",
                retryable=False,
                blocked=False,
                approval_status="none",
                error=error,
                error_class="missing_dependency",
            )
        },
    )
