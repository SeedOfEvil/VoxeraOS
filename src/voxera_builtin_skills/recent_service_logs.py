from __future__ import annotations

import re
import subprocess

from voxera.models import RunResult
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result

_SERVICE_PATTERN = re.compile(r"^[A-Za-z0-9_.@-]{1,120}\.service$")
_MAX_LINES = 200
_MAX_SINCE_MINUTES = 180


def _coerce_bounded_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    return None


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
    normalized_lines = _coerce_bounded_int(lines)
    normalized_since_minutes = _coerce_bounded_int(since_minutes)

    if not _SERVICE_PATTERN.fullmatch(normalized):
        return _reject(
            "service must match ^[A-Za-z0-9_.@-]{1,120}\\.service$",
            service=normalized,
            lines=normalized_lines if isinstance(normalized_lines, int) else -1,
            since_minutes=(
                normalized_since_minutes if isinstance(normalized_since_minutes, int) else -1
            ),
        )
    if (
        not isinstance(normalized_lines, int)
        or normalized_lines < 1
        or normalized_lines > _MAX_LINES
    ):
        return _reject(
            f"lines must be an integer between 1 and {_MAX_LINES}",
            service=normalized,
            lines=normalized_lines if isinstance(normalized_lines, int) else -1,
            since_minutes=(
                normalized_since_minutes if isinstance(normalized_since_minutes, int) else -1
            ),
        )
    if (
        not isinstance(normalized_since_minutes, int)
        or normalized_since_minutes < 1
        or normalized_since_minutes > _MAX_SINCE_MINUTES
    ):
        return _reject(
            f"since_minutes must be an integer between 1 and {_MAX_SINCE_MINUTES}",
            service=normalized,
            lines=normalized_lines if isinstance(normalized_lines, int) else -1,
            since_minutes=(
                normalized_since_minutes if isinstance(normalized_since_minutes, int) else -1
            ),
        )

    log_lines, scope = _query_journal(
        normalized, lines=normalized_lines, since_minutes=normalized_since_minutes
    )
    if log_lines is None:
        error = "journalctl command not found or both scopes failed"
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

    payload = {
        "service": normalized,
        "lines_requested": normalized_lines,
        "since_minutes": normalized_since_minutes,
        "line_count": len(log_lines),
        "logs": log_lines,
        "truncated": len(log_lines) >= normalized_lines,
        "scope": scope,
    }

    if log_lines:
        summary = f"Collected {len(log_lines)} recent log lines for {normalized} ({scope} scope)"
    else:
        summary = f"No recent logs for {normalized} in the last {normalized_since_minutes}m ({scope} scope)"

    return RunResult(
        ok=True,
        output=summary,
        data={
            **payload,
            SKILL_RESULT_KEY: build_skill_result(
                summary=summary,
                machine_payload=payload,
                operator_note="Bounded read-only journal slice for named service.",
                next_action_hint="continue",
                retryable=False,
                blocked=False,
                approval_status="none",
            ),
        },
    )


def _run_journalctl(
    service: str, *, lines: int, since_minutes: int, user: bool
) -> list[str] | None:
    """Run journalctl for one scope. Returns parsed log lines, or None on failure."""
    cmd = [
        "journalctl",
        "--no-pager",
        "--output=short-iso",
    ]
    if user:
        cmd += ["--user-unit", service]
    else:
        cmd += ["-u", service]
    cmd += ["--since", f"-{since_minutes} min", "-n", str(lines)]

    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None

    # Filter out empty lines and journalctl "-- No entries --" markers.
    return [
        line
        for line in completed.stdout.splitlines()
        if line.strip() and "-- No entries --" not in line
    ]


def _query_journal(service: str, *, lines: int, since_minutes: int) -> tuple[list[str] | None, str]:
    """Try user-unit scope first, fall back to system scope.

    Returns (log_lines, scope).  log_lines is None when both scopes fail entirely.
    """
    user_lines = _run_journalctl(service, lines=lines, since_minutes=since_minutes, user=True)
    system_lines = _run_journalctl(service, lines=lines, since_minutes=since_minutes, user=False)

    if user_lines is None and system_lines is None:
        return None, "unknown"

    # Prefer whichever scope actually has content; if both have content, prefer user scope.
    user_count = len(user_lines) if user_lines is not None else -1
    system_count = len(system_lines) if system_lines is not None else -1

    if user_count > 0:
        return user_lines, "user"
    if system_count > 0:
        return system_lines, "system"
    # Both succeeded but returned no entries — prefer user scope.
    if user_lines is not None:
        return user_lines, "user"
    assert system_lines is not None
    return system_lines, "system"
