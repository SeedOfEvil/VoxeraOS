from __future__ import annotations

import re
import subprocess

from voxera.models import RunResult
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result

_SERVICE_PATTERN = re.compile(r"^[A-Za-z0-9_.@-]{1,120}\.service$")
_MAX_LINES = 200
_MAX_SINCE_MINUTES = 180


def _reject(reason: str, *, service: str, lines: int, since_minutes: int) -> RunResult:
    return RunResult(
        ok=False,
        error=reason,
        data={
            SKILL_RESULT_KEY: build_skill_result(
                summary="Rejected unsafe diagnostics log request",
                machine_payload={
                    "service": service,
                    "lines": lines,
                    "since_minutes": since_minutes,
                },
                operator_note="Log requests must target a safe .service unit with bounded limits.",
                next_action_hint="provide_valid_log_query",
                retryable=False,
                blocked=True,
                approval_status="none",
                error=reason,
                error_class="invalid_input",
            )
        },
    )


def run(service: str, lines: int = 50, since_minutes: int = 15) -> RunResult:
    normalized = str(service or "").strip()
    if not _SERVICE_PATTERN.fullmatch(normalized):
        return _reject(
            "service must match ^[A-Za-z0-9_.@-]{1,120}\\.service$",
            service=normalized,
            lines=lines,
            since_minutes=since_minutes,
        )
    if not isinstance(lines, int) or lines < 1 or lines > _MAX_LINES:
        return _reject(
            f"lines must be an integer between 1 and {_MAX_LINES}",
            service=normalized,
            lines=lines,
            since_minutes=since_minutes,
        )
    if (
        not isinstance(since_minutes, int)
        or since_minutes < 1
        or since_minutes > _MAX_SINCE_MINUTES
    ):
        return _reject(
            f"since_minutes must be an integer between 1 and {_MAX_SINCE_MINUTES}",
            service=normalized,
            lines=lines,
            since_minutes=since_minutes,
        )

    try:
        completed = subprocess.run(
            [
                "journalctl",
                "--no-pager",
                "--output=short-iso",
                "-u",
                normalized,
                "--since",
                f"-{since_minutes} min",
                "-n",
                str(lines),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except FileNotFoundError:
        error = "journalctl command not found"
        return RunResult(
            ok=False,
            error=error,
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Service log tool unavailable",
                    machine_payload={"service": normalized, "tool": "journalctl"},
                    operator_note="Install journald tooling to inspect recent service logs.",
                    next_action_hint="install_journald_tools",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error=error,
                    error_class="missing_dependency",
                )
            },
        )
    except subprocess.CalledProcessError as exc:
        error = (exc.stderr or exc.stdout or str(exc)).strip() or "service logs query failed"
        return RunResult(
            ok=False,
            error=error,
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"Recent logs query failed for {normalized}",
                    machine_payload={"service": normalized, "stderr": (exc.stderr or "").strip()},
                    operator_note="Service may not exist or logs are unavailable in this runtime.",
                    next_action_hint="verify_service_and_retry",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error=error,
                    error_class="service_log_query_failed",
                )
            },
        )
    except subprocess.TimeoutExpired:
        error = "recent logs query timed out"
        return RunResult(
            ok=False,
            error=error,
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"Recent logs query timed out for {normalized}",
                    machine_payload={"service": normalized, "tool": "journalctl"},
                    operator_note="Log inspection exceeded the bounded timeout.",
                    next_action_hint="retry_recent_logs",
                    retryable=True,
                    blocked=False,
                    approval_status="none",
                    error=error,
                    error_class="timeout",
                )
            },
        )

    log_lines = [line for line in completed.stdout.splitlines() if line.strip()]
    payload = {
        "service": normalized,
        "lines_requested": lines,
        "since_minutes": since_minutes,
        "line_count": len(log_lines),
        "logs": log_lines,
        "truncated": len(log_lines) >= lines,
    }
    return RunResult(
        ok=True,
        output=f"Collected {len(log_lines)} recent log lines for {normalized}",
        data={
            **payload,
            SKILL_RESULT_KEY: build_skill_result(
                summary=f"Collected {len(log_lines)} recent logs for {normalized}",
                machine_payload=payload,
                operator_note="Bounded read-only journal slice for named service.",
                next_action_hint="continue",
                retryable=False,
                blocked=False,
                approval_status="none",
            ),
        },
    )
