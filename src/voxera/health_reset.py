from __future__ import annotations

import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .health import update_health_snapshot

CURRENT_STATE_FIELDS: tuple[str, ...] = (
    "daemon_state",
    "consecutive_brain_failures",
    "brain_backoff_wait_s",
    "brain_backoff_last_applied_s",
    "brain_backoff_last_applied_ts",
    "degraded_since_ts",
    "degraded_reason",
)

RECENT_HISTORY_FIELDS: tuple[str, ...] = (
    "last_error",
    "last_error_ts_ms",
    "last_fallback_reason",
    "last_fallback_from",
    "last_fallback_to",
    "last_fallback_ts_ms",
    "last_shutdown_outcome",
    "last_shutdown_ts",
    "last_shutdown_reason",
    "last_shutdown_job",
)

COUNTER_GROUPS: dict[str, tuple[str, ...] | None] = {
    "panel_auth_counters": (
        "panel_auth_invalid",
        "panel_401_count",
        "panel_403_count",
        "panel_429_count",
        "panel_csrf_missing",
        "panel_csrf_invalid",
        "panel_mutation_allowed",
        "panel_4xx_count",
    ),
    "brain_fallback_counters": (
        "brain_fallback_count",
        "brain_fallback_reason_timeout",
        "brain_fallback_reason_auth",
        "brain_fallback_reason_rate_limit",
        "brain_fallback_reason_malformed",
        "brain_fallback_reason_network",
        "brain_fallback_reason_unknown",
    ),
    "all_historical_counters": None,
}

RESET_GROUPS: dict[str, tuple[str, ...]] = {
    "current_state": CURRENT_STATE_FIELDS,
    "recent_history": RECENT_HISTORY_FIELDS,
    "current_and_recent": CURRENT_STATE_FIELDS + RECENT_HISTORY_FIELDS,
}

EVENT_BY_SCOPE = {
    "current_state": "health_reset_current_state",
    "recent_history": "health_reset_recent_history",
    "current_and_recent": "health_reset_current_and_recent",
}


class HealthResetError(ValueError):
    pass


def _dedupe(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item not in out:
            out.append(item)
    return out


def reset_health_snapshot(
    queue_root: Path | None,
    *,
    scope: str,
    counter_group: str | None = None,
    actor_surface: str,
    now_ms: int | None = None,
) -> dict[str, Any]:
    if scope not in RESET_GROUPS:
        raise HealthResetError(f"Unsupported reset scope: {scope}")
    if counter_group is not None and counter_group not in COUNTER_GROUPS:
        raise HealthResetError(f"Unsupported counter reset group: {counter_group}")

    changed: list[dict[str, Any]] = []
    reset_fields = list(RESET_GROUPS[scope])
    reset_counter_keys: list[str] = []

    ts_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)

    def _apply(payload: dict[str, Any]) -> dict[str, Any]:
        counters_raw = payload.get("counters")
        counters: dict[str, Any] = counters_raw if isinstance(counters_raw, dict) else {}

        for field in reset_fields:
            before = payload.get(field)
            after = "healthy" if field == "daemon_state" else None
            if before != after:
                payload[field] = after
                changed.append({"field": field, "before": before, "after": after})
            elif field not in payload:
                payload[field] = after

        if counter_group is not None:
            keys = COUNTER_GROUPS[counter_group]
            if keys is None:
                keys = tuple(counters.keys())
            for key in keys:
                before = int(counters.get(key, 0) or 0)
                if before != 0:
                    counters[key] = 0
                    changed.append(
                        {
                            "field": f"counters.{key}",
                            "before": before,
                            "after": 0,
                        }
                    )
                elif key in counters:
                    counters[key] = 0
                reset_counter_keys.append(key)
            payload["counters"] = counters

        payload["updated_at_ms"] = ts_ms
        return payload

    updated = update_health_snapshot(queue_root, _apply)
    return {
        "scope": scope,
        "counter_group": counter_group,
        "actor_surface": actor_surface,
        "timestamp_ms": ts_ms,
        "reset_fields": reset_fields,
        "reset_counter_keys": _dedupe(reset_counter_keys),
        "changed_fields": [row["field"] for row in changed],
        "changed": changed,
        "changed_count": len(changed),
        "preserved_historical_counters": counter_group is None,
        "health": updated,
    }
