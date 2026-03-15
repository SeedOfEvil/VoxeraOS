from __future__ import annotations

import re
import subprocess

from voxera.models import RunResult
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result

_SERVICE_PATTERN = re.compile(r"^[A-Za-z0-9_.@-]{1,120}\.service$")


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


def run(service: str) -> RunResult:
    normalized = str(service or "").strip()
    if not _SERVICE_PATTERN.fullmatch(normalized):
        return _invalid_input_result(
            normalized,
            "service must match ^[A-Za-z0-9_.@-]{1,120}\\.service$",
        )

    try:
        completed = subprocess.run(
            [
                "systemctl",
                "show",
                normalized,
                "--property=Id,LoadState,ActiveState,SubState,UnitFileState",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except FileNotFoundError:
        error = "systemctl command not found"
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
    except subprocess.CalledProcessError as exc:
        error = (exc.stderr or exc.stdout or str(exc)).strip() or "service query failed"
        return RunResult(
            ok=False,
            error=error,
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"Service status query failed for {normalized}",
                    machine_payload={"service": normalized, "stderr": (exc.stderr or "").strip()},
                    operator_note="Service may not exist or cannot be inspected in this runtime.",
                    next_action_hint="verify_service_name",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error=error,
                    error_class="service_query_failed",
                )
            },
        )
    except subprocess.TimeoutExpired:
        error = "service status query timed out"
        return RunResult(
            ok=False,
            error=error,
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"Service status timed out for {normalized}",
                    machine_payload={"service": normalized, "tool": "systemctl"},
                    operator_note="Service inspection exceeded the bounded timeout.",
                    next_action_hint="retry_service_status",
                    retryable=True,
                    blocked=False,
                    approval_status="none",
                    error=error,
                    error_class="timeout",
                )
            },
        )

    status: dict[str, str] = {"service": normalized}
    for line in completed.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        status[key] = value

    summary = (
        f"Service {normalized}: {status.get('ActiveState', 'unknown')}/"
        f"{status.get('SubState', 'unknown')}"
    )
    return RunResult(
        ok=True,
        output=summary,
        data={
            **status,
            SKILL_RESULT_KEY: build_skill_result(
                summary=summary,
                machine_payload=status,
                operator_note="Read-only systemd service status snapshot.",
                next_action_hint="continue",
                retryable=False,
                blocked=False,
                approval_status="none",
            ),
        },
    )
