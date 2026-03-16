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

    result = _query_journal(
        normalized, lines=normalized_lines, since_minutes=normalized_since_minutes
    )
    if result.error is not None:
        return RunResult(
            ok=False,
            error=result.error,
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=result.summary,
                    machine_payload={"service": normalized, "tool": "journalctl"},
                    operator_note=result.operator_note,
                    next_action_hint=result.next_action_hint,
                    retryable=result.retryable,
                    blocked=False,
                    approval_status="none",
                    error=result.error,
                    error_class=result.error_class,
                )
            },
        )
    log_lines = result.lines
    scope = result.scope

    payload: dict[str, object] = {
        "service": normalized,
        "lines_requested": normalized_lines,
        "since_minutes": normalized_since_minutes,
        "line_count": len(log_lines),
        "logs": log_lines,
        "truncated": len(log_lines) >= normalized_lines,
        "scope": scope,
    }
    if result.scope_warning:
        payload["scope_warning"] = result.scope_warning

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


class _ScopeResult:
    """Outcome of querying a single journalctl scope."""

    __slots__ = ("lines", "error_kind")

    def __init__(self, *, lines: list[str] | None, error_kind: str = ""):
        self.lines = lines
        self.error_kind = error_kind  # "", "not_found", "query_failed", "timeout"


class _JournalResult:
    """Aggregated outcome from querying both scopes."""

    __slots__ = (
        "lines",
        "scope",
        "scope_warning",
        "error",
        "summary",
        "operator_note",
        "next_action_hint",
        "retryable",
        "error_class",
    )

    def __init__(
        self,
        *,
        lines: list[str] | None = None,
        scope: str = "unknown",
        scope_warning: str = "",
        error: str | None = None,
        summary: str = "",
        operator_note: str = "",
        next_action_hint: str = "",
        retryable: bool = False,
        error_class: str = "",
    ):
        self.lines = lines
        self.scope = scope
        self.scope_warning = scope_warning
        self.error = error
        self.summary = summary
        self.operator_note = operator_note
        self.next_action_hint = next_action_hint
        self.retryable = retryable
        self.error_class = error_class


def _run_journalctl(service: str, *, lines: int, since_minutes: int, user: bool) -> _ScopeResult:
    """Run journalctl for one scope. Distinguishes tool-missing from runtime errors."""
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
    except FileNotFoundError:
        return _ScopeResult(lines=None, error_kind="not_found")
    except subprocess.CalledProcessError as exc:
        _ = (exc.stderr or exc.stdout or "").strip()
        return _ScopeResult(lines=None, error_kind="query_failed")
    except subprocess.TimeoutExpired:
        return _ScopeResult(lines=None, error_kind="timeout")

    # Filter out empty lines and journalctl "-- No entries --" markers.
    parsed = [
        line
        for line in completed.stdout.splitlines()
        if line.strip() and line.strip() != "-- No entries --"
    ]
    return _ScopeResult(lines=parsed)


def _query_journal(service: str, *, lines: int, since_minutes: int) -> _JournalResult:
    """Try user-unit scope first, fall back to system scope.

    Returns a _JournalResult with either lines+scope or error details.
    """
    user_result = _run_journalctl(service, lines=lines, since_minutes=since_minutes, user=True)
    system_result = _run_journalctl(service, lines=lines, since_minutes=since_minutes, user=False)

    user_ok = user_result.lines is not None
    system_ok = system_result.lines is not None

    if not user_ok and not system_ok:
        # Both failed — classify by the most informative error kind.
        # Prefer tool-missing only when both scopes report it.
        error_kinds = {user_result.error_kind, system_result.error_kind}
        if error_kinds == {"not_found"}:
            return _JournalResult(
                error="journalctl command not found",
                summary="Service log tool unavailable",
                operator_note="Install journald tooling to inspect recent service logs.",
                next_action_hint="install_journald_tools",
                retryable=False,
                error_class="missing_dependency",
            )
        if "timeout" in error_kinds:
            return _JournalResult(
                error="recent logs query timed out",
                summary=f"Recent logs query timed out for {service}",
                operator_note="Log inspection exceeded the bounded timeout.",
                next_action_hint="retry_recent_logs",
                retryable=True,
                error_class="timeout",
            )
        return _JournalResult(
            error="service logs query failed in both scopes",
            summary=f"Recent logs query failed for {service}",
            operator_note="Service may not exist or logs are unavailable in this runtime.",
            next_action_hint="verify_service_and_retry",
            retryable=False,
            error_class="service_log_query_failed",
        )

    # At least one scope succeeded.  Prefer whichever has content; user scope first.
    # When one scope failed, warn the operator so results aren't mistaken for
    # a complete query.
    scope_warning = ""
    if user_ok and not system_ok:
        scope_warning = f"system scope query failed ({system_result.error_kind})"
    elif system_ok and not user_ok:
        scope_warning = f"user scope query failed ({user_result.error_kind})"

    user_count = len(user_result.lines) if user_ok else -1
    system_count = len(system_result.lines) if system_ok else -1

    if user_count > 0:
        return _JournalResult(lines=user_result.lines, scope="user", scope_warning=scope_warning)
    if system_count > 0:
        return _JournalResult(
            lines=system_result.lines, scope="system", scope_warning=scope_warning
        )
    # Both succeeded but returned no entries — prefer user scope.
    if user_ok:
        return _JournalResult(lines=user_result.lines, scope="user", scope_warning=scope_warning)
    assert system_result.lines is not None
    return _JournalResult(lines=system_result.lines, scope="system", scope_warning=scope_warning)
