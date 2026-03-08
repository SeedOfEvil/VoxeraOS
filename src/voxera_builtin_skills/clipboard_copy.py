from __future__ import annotations

import subprocess

from voxera.models import RunResult
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result


def run(text: str) -> RunResult:
    payload = text if text is not None else ""
    commands = [
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
    ]
    for cmd in commands:
        try:
            subprocess.run(cmd, input=payload, text=True, check=True)
            return RunResult(
                ok=True,
                output="Copied text to clipboard",
                data={
                    SKILL_RESULT_KEY: build_skill_result(
                        summary="Copied text to clipboard",
                        machine_payload={"launcher": cmd[0], "chars": len(payload)},
                        operator_note="Clipboard was updated.",
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
                        summary="Clipboard copy command failed",
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

    error = "No clipboard tool found (wl-copy/xclip)"
    return RunResult(
        ok=False,
        error=error,
        data={
            SKILL_RESULT_KEY: build_skill_result(
                summary="No supported clipboard copy tool found",
                machine_payload={"candidates": ["wl-copy", "xclip"]},
                operator_note="Install wl-copy or xclip to enable clipboard copy.",
                next_action_hint="install_clipboard_tool",
                retryable=False,
                blocked=False,
                approval_status="none",
                error=error,
                error_class="missing_dependency",
            )
        },
    )
