from __future__ import annotations

from voxera.models import RunResult
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result


def _parse_meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    with open("/proc/meminfo", encoding="utf-8") as handle:
        for line in handle:
            if ":" not in line:
                continue
            key, raw = line.split(":", 1)
            parts = raw.strip().split()
            if not parts:
                continue
            try:
                values[key.strip()] = int(parts[0])
            except ValueError:
                continue
    return values


def run() -> RunResult:
    try:
        mem = _parse_meminfo()
        total_kib = int(mem.get("MemTotal", 0))
        avail_kib = int(mem.get("MemAvailable", mem.get("MemFree", 0)))
        used_kib = max(total_kib - avail_kib, 0)
        used_pct = round((used_kib / total_kib) * 100, 1) if total_kib > 0 else 0.0
        info = {
            "total_kib": total_kib,
            "available_kib": avail_kib,
            "used_kib": used_kib,
            "total_gib": round(total_kib / (1024**2), 2),
            "available_gib": round(avail_kib / (1024**2), 2),
            "used_gib": round(used_kib / (1024**2), 2),
            "used_percent": used_pct,
        }
        return RunResult(
            ok=True,
            output=(f"Memory: {info['used_gib']}GiB / {info['total_gib']}GiB ({used_pct}% used)"),
            data={
                **info,
                SKILL_RESULT_KEY: build_skill_result(
                    summary=(
                        f"Memory usage snapshot: {info['used_gib']}GiB used "
                        f"of {info['total_gib']}GiB ({used_pct}%)"
                    ),
                    machine_payload=info,
                    operator_note="Read-only memory snapshot from /proc/meminfo.",
                    next_action_hint="continue",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                ),
            },
        )
    except FileNotFoundError:
        error = "/proc/meminfo is unavailable"
        return RunResult(
            ok=False,
            error=error,
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Memory usage source unavailable",
                    machine_payload={"source": "/proc/meminfo"},
                    operator_note="Unable to read host memory info source.",
                    next_action_hint="inspect_runtime",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error=error,
                    error_class="missing_dependency",
                )
            },
        )
    except Exception as exc:
        error = f"Failed to read memory usage: {exc}"
        return RunResult(
            ok=False,
            error=error,
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Memory usage query failed",
                    machine_payload={"exception": repr(exc)},
                    operator_note="Memory inspection failed unexpectedly.",
                    next_action_hint="retry_memory_usage",
                    retryable=True,
                    blocked=False,
                    approval_status="none",
                    error=error,
                    error_class="io_error",
                )
            },
        )
