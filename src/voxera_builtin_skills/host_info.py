from __future__ import annotations

import platform
import socket
from pathlib import Path

from voxera.models import RunResult
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result


def _read_uptime_seconds() -> float | None:
    path = Path("/proc/uptime")
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8").strip().split()
    if not raw:
        return None
    try:
        return round(float(raw[0]), 2)
    except ValueError:
        return None


def run() -> RunResult:
    info = {
        "hostname": socket.gethostname(),
        "fqdn": socket.getfqdn(),
        "os": platform.platform(),
        "kernel": platform.release(),
        "python": platform.python_version(),
        "uptime_seconds": _read_uptime_seconds(),
    }
    return RunResult(
        ok=True,
        output=f"Host {info['hostname']} on {info['os']}",
        data={
            **info,
            SKILL_RESULT_KEY: build_skill_result(
                summary=f"Host info captured for {info['hostname']}",
                machine_payload=info,
                operator_note="Read-only host identity and uptime snapshot.",
                next_action_hint="continue",
                retryable=False,
                blocked=False,
                approval_status="none",
            ),
        },
    )
