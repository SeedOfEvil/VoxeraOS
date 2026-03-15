from __future__ import annotations

import os

from voxera.models import RunResult
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result


def run() -> RunResult:
    try:
        load_1, load_5, load_15 = os.getloadavg()
        cpu_count = os.cpu_count() or 0
        normalized = round(load_1 / cpu_count, 2) if cpu_count > 0 else None
        info = {
            "load_1m": round(load_1, 2),
            "load_5m": round(load_5, 2),
            "load_15m": round(load_15, 2),
            "cpu_count": cpu_count,
            "normalized_1m_per_cpu": normalized,
        }
        return RunResult(
            ok=True,
            output=f"Load avg: {info['load_1m']} {info['load_5m']} {info['load_15m']}",
            data={
                **info,
                SKILL_RESULT_KEY: build_skill_result(
                    summary=(
                        f"Load snapshot captured (1m={info['load_1m']}, "
                        f"5m={info['load_5m']}, 15m={info['load_15m']})"
                    ),
                    machine_payload=info,
                    operator_note="Read-only CPU/load snapshot from host metrics.",
                    next_action_hint="continue",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                ),
            },
        )
    except Exception as exc:
        error = f"Failed to read load snapshot: {exc}"
        return RunResult(
            ok=False,
            error=error,
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Load snapshot query failed",
                    machine_payload={"exception": repr(exc)},
                    operator_note="Unable to read host load averages.",
                    next_action_hint="retry_load_snapshot",
                    retryable=True,
                    blocked=False,
                    approval_status="none",
                    error=error,
                    error_class="io_error",
                )
            },
        )
