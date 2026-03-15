from __future__ import annotations

import re
import subprocess

from voxera.models import RunResult
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result

_SERVICE_PATTERN = re.compile(r"^[A-Za-z0-9_.@-]{1,120}\.service$")

_SYSTEMCTL_PROPS = "Id,LoadState,ActiveState,SubState,UnitFileState"


def _invalid_input_result(service: str, reason: str) -> RunResult:
    return RunResult(
        ok=False,
        error=reason,
        data={
            SKILL_RESULT_KEY: build_skill_result(
                summary="Rejected unsafe or invalid service target",
                machine_payload={"service": service},
                operator_note="Provide a bounded systemd unit name like voxera-daemon.service.",
                next_action_hint="provide_valid_service",
                retryable=False,
                blocked=True,
                approval_status="none",
                error=reason,
                error_class="invalid_input",
            )
        },
    )


class _ScopeResult:
    """Outcome of querying a single systemctl scope."""

    __slots__ = ("props", "error_kind")

    def __init__(self, *, props: dict[str, str] | None, error_kind: str = ""):
        self.props = props
        self.error_kind = error_kind  # "", "not_found", "query_failed", "timeout"


def _query_scope(service: str, *, user: bool) -> _ScopeResult:
    """Query systemctl for a single scope. Distinguishes tool-missing from runtime errors."""
    cmd = ["systemctl"]
    if user:
        cmd.append("--user")
    cmd += ["show", service, f"--property={_SYSTEMCTL_PROPS}", "--no-pager"]
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=True)
    except FileNotFoundError:
        return _ScopeResult(props=None, error_kind="not_found")
    except subprocess.CalledProcessError:
        return _ScopeResult(props=None, error_kind="query_failed")
    except subprocess.TimeoutExpired:
        return _ScopeResult(props=None, error_kind="timeout")
    props: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        props[key] = value
    return _ScopeResult(props=props)


def _is_active(props: dict[str, str] | None) -> bool:
    if not props:
        return False
    return props.get("ActiveState", "").strip().lower() in {"active", "activating", "reloading"}


def _is_noteworthy(props: dict[str, str] | None) -> bool:
    """Return True if the state carries actionable information beyond inactive/dead."""
    if not props:
        return False
    state = props.get("ActiveState", "").strip().lower()
    return state not in {"", "inactive"}


def run(service: str) -> RunResult:
    normalized = str(service or "").strip()
    if not _SERVICE_PATTERN.fullmatch(normalized):
        return _invalid_input_result(
            normalized,
            "service must match ^[A-Za-z0-9_.@-]{1,120}\\.service$",
        )

    # Query both system and user scopes to surface the correct state.
    # Voxera services typically run in user scope; querying only system scope
    # would misleadingly report inactive/dead for running user services.
    system_result = _query_scope(normalized, user=False)
    user_result = _query_scope(normalized, user=True)

    system_props = system_result.props
    user_props = user_result.props
    system_ok = system_props is not None
    user_ok = user_props is not None

    if not system_ok and not user_ok:
        # Both failed — classify by the most informative error kind.
        error_kinds = {system_result.error_kind, user_result.error_kind}
        if error_kinds == {"not_found"}:
            error = "systemctl command not found"
            error_class = "missing_dependency"
            operator_note = "Install systemd tooling to inspect service state."
            next_action_hint = "install_systemd_tools"
            retryable = False
        elif "timeout" in error_kinds:
            error = "service status query timed out"
            error_class = "timeout"
            operator_note = "Service inspection exceeded the bounded timeout."
            next_action_hint = "retry_service_status"
            retryable = True
        else:
            error = "service status query failed in both scopes"
            error_class = "service_query_failed"
            operator_note = "Service may not exist or cannot be inspected in this runtime."
            next_action_hint = "verify_service_name"
            retryable = False
        return RunResult(
            ok=False,
            error=error,
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"Service status query failed for {normalized}",
                    machine_payload={"service": normalized, "tool": "systemctl"},
                    operator_note=operator_note,
                    next_action_hint=next_action_hint,
                    retryable=retryable,
                    blocked=False,
                    approval_status="none",
                    error=error,
                    error_class=error_class,
                )
            },
        )

    system_active = _is_active(system_props)
    user_active = _is_active(user_props)

    # Choose the primary scope:
    # 1. Prefer whichever scope is active (user scope first).
    # 2. When neither is active, prefer the scope with a noteworthy state
    #    (e.g. failed > inactive) so operators see real failures.
    # 3. Fall back to system scope (the broader default).
    if user_active:
        primary_props = user_props
        scope = "user"
    elif system_active or (system_ok and _is_noteworthy(system_props)):
        primary_props = system_props
        scope = "system"
    elif user_ok and _is_noteworthy(user_props):
        primary_props = user_props
        scope = "user"
    elif system_ok:
        primary_props = system_props
        scope = "system"
    else:
        primary_props = user_props
        scope = "user"

    assert primary_props is not None  # guaranteed by earlier checks

    active_state = primary_props.get("ActiveState", "unknown")
    sub_state = primary_props.get("SubState", "unknown")

    status: dict[str, str] = {"service": normalized, "scope": scope}
    status.update(primary_props)

    # Surface cross-scope context whenever both scopes responded and differ,
    # so operators see e.g. "failed in system scope" even when neither is active.
    if system_ok and user_ok:
        other_scope = "system" if scope == "user" else "user"
        other_props = system_props if scope == "user" else user_props
        assert other_props is not None
        other_active_state = other_props.get("ActiveState", "unknown")
        other_sub_state = other_props.get("SubState", "unknown")
        if other_active_state != active_state or other_sub_state != sub_state:
            status["other_scope"] = other_scope
            status["other_ActiveState"] = other_active_state
            status["other_SubState"] = other_sub_state

    # When one scope failed, warn the operator so they know the result is partial.
    failed_result = None
    if system_ok and not user_ok:
        failed_result = user_result
        failed_scope = "user"
    elif user_ok and not system_ok:
        failed_result = system_result
        failed_scope = "system"
    if failed_result is not None:
        status["scope_warning"] = f"{failed_scope} scope query failed ({failed_result.error_kind})"

    summary = f"Service {normalized}: {active_state}/{sub_state} ({scope} service)"
    return RunResult(
        ok=True,
        output=summary,
        data={
            **status,
            SKILL_RESULT_KEY: build_skill_result(
                summary=summary,
                machine_payload=status,
                operator_note="Read-only systemd service status snapshot (checked both system and user scopes).",
                next_action_hint="continue",
                retryable=False,
                blocked=False,
                approval_status="none",
            ),
        },
    )
