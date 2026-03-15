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


def _query_scope(service: str, *, user: bool) -> dict[str, str] | None:
    """Query systemctl for a single scope. Returns parsed properties or None on failure."""
    cmd = ["systemctl"]
    if user:
        cmd.append("--user")
    cmd += ["show", service, f"--property={_SYSTEMCTL_PROPS}", "--no-pager"]
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    props: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        props[key] = value
    return props


def _is_active(props: dict[str, str] | None) -> bool:
    if not props:
        return False
    return props.get("ActiveState", "").strip().lower() in {"active", "activating", "reloading"}


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
    system_props = _query_scope(normalized, user=False)
    user_props = _query_scope(normalized, user=True)

    system_ok = system_props is not None
    user_ok = user_props is not None

    if not system_ok and not user_ok:
        error = "systemctl command not found or both scopes failed"
        return RunResult(
            ok=False,
            error=error,
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Service status tool unavailable",
                    machine_payload={"service": normalized, "tool": "systemctl"},
                    operator_note="Install systemd tooling to inspect service state.",
                    next_action_hint="install_systemd_tools",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error=error,
                    error_class="missing_dependency",
                )
            },
        )

    system_active = _is_active(system_props)
    user_active = _is_active(user_props)

    # Choose the primary scope: prefer user scope when active, else system scope.
    # This ensures Voxera user services are correctly reported as running.
    if user_active:
        primary_props = user_props
        scope = "user"
    elif system_active:
        primary_props = system_props
        scope = "system"
    elif user_ok:
        primary_props = user_props
        scope = "user"
    else:
        primary_props = system_props
        scope = "system"

    assert primary_props is not None  # guaranteed by earlier checks

    active_state = primary_props.get("ActiveState", "unknown")
    sub_state = primary_props.get("SubState", "unknown")

    status: dict[str, str] = {"service": normalized, "scope": scope}
    status.update(primary_props)

    # If scopes differ, note the other scope's state for operator awareness.
    if system_ok and user_ok and system_active != user_active:
        other_scope = "system" if scope == "user" else "user"
        other_props = system_props if scope == "user" else user_props
        assert other_props is not None
        other_active = other_props.get("ActiveState", "unknown")
        other_sub = other_props.get("SubState", "unknown")
        status["other_scope"] = other_scope
        status["other_ActiveState"] = other_active
        status["other_SubState"] = other_sub

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
