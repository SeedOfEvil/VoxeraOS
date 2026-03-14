from __future__ import annotations

import subprocess

from voxera.models import RunResult
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result


def run() -> RunResult:
    try:
        output = subprocess.check_output(
            ["ps", "-eo", "pid,user,%cpu,%mem,comm", "--no-headers"],
            text=True,
            timeout=10,
        )
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        processes: list[dict[str, str]] = []
        for line in lines:
            parts = line.split(None, 4)
            if len(parts) >= 5:
                processes.append(
                    {
                        "pid": parts[0],
                        "user": parts[1],
                        "cpu_pct": parts[2],
                        "mem_pct": parts[3],
                        "command": parts[4],
                    }
                )
        return RunResult(
            ok=True,
            output=f"Listed {len(processes)} processes",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"Listed {len(processes)} running processes",
                    machine_payload={
                        "count": len(processes),
                        "processes": processes[:50],
                        "truncated": len(processes) > 50,
                    },
                    operator_note="Read-only process snapshot (top 50 by listing order).",
                    next_action_hint="continue",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                ),
            },
        )
    except FileNotFoundError:
        error = "ps command not found"
        return RunResult(
            ok=False,
            error=error,
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Process listing tool unavailable",
                    machine_payload={"tool": "ps"},
                    operator_note="The ps command is not available on this system.",
                    next_action_hint="install_procps",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error=error,
                    error_class="missing_dependency",
                )
            },
        )
    except subprocess.TimeoutExpired:
        error = "ps command timed out"
        return RunResult(
            ok=False,
            error=error,
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Process listing timed out",
                    machine_payload={"tool": "ps"},
                    operator_note="ps did not complete within timeout.",
                    next_action_hint="retry_process_list",
                    retryable=True,
                    blocked=False,
                    approval_status="none",
                    error=error,
                    error_class="timeout",
                )
            },
        )
    except subprocess.CalledProcessError as exc:
        error = f"ps failed: {exc}"
        return RunResult(
            ok=False,
            error=error,
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Process listing command failed",
                    machine_payload={"tool": "ps"},
                    operator_note="ps returned a non-zero exit code.",
                    next_action_hint="inspect_process_tool",
                    retryable=True,
                    blocked=False,
                    approval_status="none",
                    error=error,
                    error_class="process_query_failed",
                )
            },
        )
