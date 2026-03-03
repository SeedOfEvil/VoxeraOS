from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

HEALTH_FILE_NAME = "health.json"
DEGRADED_FAILURE_THRESHOLD = 3
DEGRADED_REASON_CONSECUTIVE_FALLBACKS = "consecutive_brain_fallbacks"


def _coerce_consecutive_brain_failures(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def normalize_health_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    consecutive = _coerce_consecutive_brain_failures(normalized.get("consecutive_brain_failures"))
    daemon_state = str(normalized.get("daemon_state") or "healthy").strip().lower()
    if daemon_state not in {"healthy", "degraded"}:
        daemon_state = "degraded" if consecutive >= DEGRADED_FAILURE_THRESHOLD else "healthy"
    if daemon_state == "healthy" and consecutive >= DEGRADED_FAILURE_THRESHOLD:
        daemon_state = "degraded"

    degraded_since = normalized.get("degraded_since_ts")
    degraded_reason = normalized.get("degraded_reason")
    if daemon_state == "healthy":
        degraded_since = None
        degraded_reason = None
    else:
        if not isinstance(degraded_since, (int, float)):
            degraded_since = None
        degraded_reason = str(degraded_reason).strip() if degraded_reason is not None else None
        if not degraded_reason:
            degraded_reason = DEGRADED_REASON_CONSECUTIVE_FALLBACKS

    normalized["consecutive_brain_failures"] = consecutive
    normalized["daemon_state"] = daemon_state
    normalized["degraded_since_ts"] = degraded_since
    normalized["degraded_reason"] = degraded_reason
    return normalized


def update_degradation_state(
    state: dict[str, Any],
    *,
    fallback_event: bool,
    mission_success: bool,
    now_fn: Callable[[], float] = time.time,
) -> dict[str, Any]:
    next_state = normalize_health_snapshot(state)
    if mission_success:
        next_state["consecutive_brain_failures"] = 0
        next_state["daemon_state"] = "healthy"
        next_state["degraded_since_ts"] = None
        next_state["degraded_reason"] = None
        return next_state

    if not fallback_event:
        return next_state

    failures = int(next_state.get("consecutive_brain_failures", 0) or 0) + 1
    next_state["consecutive_brain_failures"] = failures
    if failures >= DEGRADED_FAILURE_THRESHOLD:
        entering_degraded = str(next_state.get("daemon_state")) != "degraded"
        next_state["daemon_state"] = "degraded"
        next_state["degraded_reason"] = DEGRADED_REASON_CONSECUTIVE_FALLBACKS
        if entering_degraded or next_state.get("degraded_since_ts") is None:
            next_state["degraded_since_ts"] = float(now_fn())
    return normalize_health_snapshot(next_state)


def health_path(queue_root: Path) -> Path:
    return queue_root / HEALTH_FILE_NAME


def read_health_snapshot(queue_root: Path) -> dict[str, Any]:
    path = health_path(queue_root)
    if not path.exists():
        return normalize_health_snapshot({})
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return normalize_health_snapshot({})
    return normalize_health_snapshot(payload if isinstance(payload, dict) else {})


def write_health_snapshot(queue_root: Path, payload: dict[str, Any]) -> None:
    queue_root.mkdir(parents=True, exist_ok=True)
    path = health_path(queue_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(normalize_health_snapshot(payload), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(path)


def update_health_snapshot(
    queue_root: Path,
    updater: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> dict[str, Any]:
    current = read_health_snapshot(queue_root)
    updated = updater(dict(current))
    final_payload = normalize_health_snapshot(updated if isinstance(updated, dict) else current)
    write_health_snapshot(queue_root, final_payload)
    return final_payload


def record_plan_attempt_fallback(queue_root: Path) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)

    def _apply(payload: dict[str, Any]) -> dict[str, Any]:
        next_payload = update_degradation_state(payload, fallback_event=True, mission_success=False)
        next_payload["updated_at_ms"] = now_ms
        return next_payload

    return update_health_snapshot(queue_root, _apply)


def record_mission_success(queue_root: Path) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)

    def _apply(payload: dict[str, Any]) -> dict[str, Any]:
        next_payload = update_degradation_state(payload, fallback_event=False, mission_success=True)
        next_payload["updated_at_ms"] = now_ms
        return next_payload

    return update_health_snapshot(queue_root, _apply)


def increment_health_counter(
    queue_root: Path,
    key: str,
    *,
    amount: int = 1,
    last_error: str | None = None,
) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)

    def _apply(payload: dict[str, Any]) -> dict[str, Any]:
        counters_raw = payload.get("counters")
        counters: dict[str, Any] = counters_raw if isinstance(counters_raw, dict) else {}
        counters[key] = int(counters.get(key, 0) or 0) + amount
        payload["counters"] = counters
        payload["updated_at_ms"] = now_ms
        if last_error:
            payload["last_error"] = last_error
            payload["last_error_ts_ms"] = now_ms
        return payload

    return update_health_snapshot(queue_root, _apply)


def record_health_ok(queue_root: Path, event_name: str) -> None:
    now_ms = int(time.time() * 1000)
    event = event_name.strip() if event_name else "ok"

    def _apply(payload: dict[str, Any]) -> dict[str, Any]:
        payload["last_ok_event"] = event
        payload["last_ok_ts_ms"] = now_ms
        payload["updated_at_ms"] = now_ms
        return payload

    update_health_snapshot(queue_root, _apply)


def record_health_error(queue_root: Path, msg: str) -> None:
    now_ms = int(time.time() * 1000)
    clean = " ".join(msg.split()) if msg else "error"

    def _apply(payload: dict[str, Any]) -> dict[str, Any]:
        payload["last_error"] = clean
        payload["last_error_ts_ms"] = now_ms
        payload["updated_at_ms"] = now_ms
        return payload

    update_health_snapshot(queue_root, _apply)


def record_fallback_transition(
    queue_root: Path,
    *,
    from_tier: str,
    to_tier: str,
    reason: str,
) -> dict[str, Any]:
    """Record a brain fallback transition in health counters + snapshot."""
    now_ms = int(time.time() * 1000)
    reason_lower = reason.lower()

    def _apply(payload: dict[str, Any]) -> dict[str, Any]:
        counters_raw = payload.get("counters")
        counters: dict[str, Any] = counters_raw if isinstance(counters_raw, dict) else {}

        counters["brain_fallback_count"] = int(counters.get("brain_fallback_count", 0) or 0) + 1
        reason_key = f"brain_fallback_reason_{reason_lower}"
        counters[reason_key] = int(counters.get(reason_key, 0) or 0) + 1

        payload["counters"] = counters
        payload["last_fallback_reason"] = reason
        payload["last_fallback_from"] = from_tier
        payload["last_fallback_to"] = to_tier
        payload["last_fallback_ts_ms"] = now_ms
        payload["updated_at_ms"] = now_ms
        return payload

    return update_health_snapshot(queue_root, _apply)
