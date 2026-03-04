from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

HEALTH_FILE_NAME = "health.json"
_DEGRADED_THRESHOLD = 3
_BRAIN_BACKOFF_BASE_ENV = "VOXERA_BRAIN_BACKOFF_BASE_S"
_BRAIN_BACKOFF_MAX_ENV = "VOXERA_BRAIN_BACKOFF_MAX_S"
_BRAIN_BACKOFF_BASE_DEFAULT_S = 2
_BRAIN_BACKOFF_MAX_DEFAULT_S = 60
_LAST_SHUTDOWN_REASON_MAX_LEN = 240
_LAST_SHUTDOWN_OUTCOMES = {"clean", "failed_shutdown", "startup_recovered"}


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_env_int(name: str, default: int) -> int:
    return _safe_int(os.getenv(name), default)


def compute_brain_backoff_s(consecutive_brain_failures: int) -> int:
    failures = max(_safe_int(consecutive_brain_failures, 0), 0)
    base_s = max(_read_env_int(_BRAIN_BACKOFF_BASE_ENV, _BRAIN_BACKOFF_BASE_DEFAULT_S), 0)
    max_s = max(_read_env_int(_BRAIN_BACKOFF_MAX_ENV, _BRAIN_BACKOFF_MAX_DEFAULT_S), 0)

    wait_s = 0
    if failures >= 10:
        wait_s = 15 * base_s
    elif failures >= 5:
        wait_s = 4 * base_s
    elif failures >= 3:
        wait_s = base_s

    wait_s = max(wait_s, 0)
    return min(wait_s, max_s)


def _normalize_health_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    consecutive_failures = _safe_int(normalized.get("consecutive_brain_failures", 0) or 0, 0)
    normalized["consecutive_brain_failures"] = max(consecutive_failures, 0)
    normalized["brain_backoff_wait_s"] = compute_brain_backoff_s(
        normalized["consecutive_brain_failures"]
    )
    normalized["brain_backoff_active"] = normalized["brain_backoff_wait_s"] > 0

    daemon_state = str(normalized.get("daemon_state") or "healthy").lower()
    if daemon_state not in {"healthy", "degraded"}:
        daemon_state = "healthy"
    normalized["daemon_state"] = daemon_state

    degraded_since_ts = normalized.get("degraded_since_ts")
    normalized["degraded_since_ts"] = (
        float(degraded_since_ts) if isinstance(degraded_since_ts, (int, float)) else None
    )

    degraded_reason = normalized.get("degraded_reason")
    normalized["degraded_reason"] = str(degraded_reason) if degraded_reason else None

    backoff_last_applied_s = _safe_int(normalized.get("brain_backoff_last_applied_s", 0) or 0, 0)
    normalized["brain_backoff_last_applied_s"] = max(backoff_last_applied_s, 0)

    backoff_last_applied_ts = normalized.get("brain_backoff_last_applied_ts")
    normalized["brain_backoff_last_applied_ts"] = (
        float(backoff_last_applied_ts)
        if isinstance(backoff_last_applied_ts, (int, float))
        else None
    )

    shutdown_outcome = normalized.get("last_shutdown_outcome")
    if shutdown_outcome in _LAST_SHUTDOWN_OUTCOMES:
        normalized["last_shutdown_outcome"] = shutdown_outcome
    else:
        normalized["last_shutdown_outcome"] = None

    shutdown_ts = normalized.get("last_shutdown_ts")
    normalized["last_shutdown_ts"] = (
        float(shutdown_ts) if isinstance(shutdown_ts, (int, float)) else None
    )

    shutdown_reason = normalized.get("last_shutdown_reason")
    normalized["last_shutdown_reason"] = str(shutdown_reason) if shutdown_reason else None

    shutdown_job = normalized.get("last_shutdown_job")
    normalized["last_shutdown_job"] = str(shutdown_job) if shutdown_job else None
    return normalized


def _compact_reason(reason: str | None) -> str | None:
    if not reason:
        return None
    compact = " ".join(str(reason).split()).strip()
    if not compact:
        return None
    if len(compact) <= _LAST_SHUTDOWN_REASON_MAX_LEN:
        return compact
    return compact[: _LAST_SHUTDOWN_REASON_MAX_LEN - 1] + "…"


def record_last_shutdown(
    queue_root: Path,
    *,
    outcome: str,
    reason: str | None,
    job: str | None,
    now_fn: Callable[[], float] = time.time,
) -> dict[str, Any]:
    normalized_outcome = outcome if outcome in _LAST_SHUTDOWN_OUTCOMES else "failed_shutdown"

    def _apply(payload: dict[str, Any]) -> dict[str, Any]:
        payload["last_shutdown_outcome"] = normalized_outcome
        payload["last_shutdown_ts"] = float(now_fn())
        payload["last_shutdown_reason"] = _compact_reason(reason)
        payload["last_shutdown_job"] = str(job) if job else None
        return payload

    return update_health_snapshot(queue_root, _apply)


def update_degradation_state(
    state: dict[str, Any],
    *,
    fallback_event: bool,
    mission_success: bool,
    now_fn: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Update health degradation fields from fallback/success events."""
    next_state = _normalize_health_snapshot(state)

    if mission_success:
        next_state["consecutive_brain_failures"] = 0
        next_state["daemon_state"] = "healthy"
        next_state["degraded_since_ts"] = None
        next_state["degraded_reason"] = None
        return next_state

    if not fallback_event:
        return next_state

    next_state["consecutive_brain_failures"] = (
        int(next_state["consecutive_brain_failures"] or 0) + 1
    )
    if int(next_state["consecutive_brain_failures"] or 0) >= _DEGRADED_THRESHOLD:
        if next_state.get("daemon_state") != "degraded":
            next_state["degraded_since_ts"] = float(now_fn())
        next_state["daemon_state"] = "degraded"
        next_state["degraded_reason"] = "brain_fallbacks"
    return next_state


def health_path(queue_root: Path) -> Path:
    return queue_root / HEALTH_FILE_NAME


def read_health_snapshot(queue_root: Path) -> dict[str, Any]:
    path = health_path(queue_root)
    if not path.exists():
        return _normalize_health_snapshot({})
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _normalize_health_snapshot({})
    if not isinstance(payload, dict):
        return _normalize_health_snapshot({})
    return _normalize_health_snapshot(payload)


def write_health_snapshot(queue_root: Path, payload: dict[str, Any]) -> None:
    queue_root.mkdir(parents=True, exist_ok=True)
    path = health_path(queue_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(_normalize_health_snapshot(payload), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(path)


def update_health_snapshot(
    queue_root: Path,
    updater: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> dict[str, Any]:
    current = read_health_snapshot(queue_root)
    updated = updater(dict(current))
    final_payload = _normalize_health_snapshot(updated if isinstance(updated, dict) else current)
    write_health_snapshot(queue_root, final_payload)
    return final_payload


def record_brain_fallback_attempt(
    queue_root: Path,
    *,
    now_fn: Callable[[], float] = time.time,
) -> dict[str, Any]:
    now_ms = int(now_fn() * 1000)

    def _apply(payload: dict[str, Any]) -> dict[str, Any]:
        updated = update_degradation_state(
            payload,
            fallback_event=True,
            mission_success=False,
            now_fn=now_fn,
        )
        updated["updated_at_ms"] = now_ms
        return updated

    return update_health_snapshot(queue_root, _apply)


def record_mission_success(queue_root: Path) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)

    def _apply(payload: dict[str, Any]) -> dict[str, Any]:
        updated = update_degradation_state(payload, fallback_event=False, mission_success=True)
        updated["updated_at_ms"] = now_ms
        return updated

    return update_health_snapshot(queue_root, _apply)


def record_brain_backoff_applied(
    queue_root: Path,
    *,
    wait_s: int,
    now_ts: float,
) -> dict[str, Any]:
    """Persist the last applied planning backoff delay for operators."""

    def _apply(payload: dict[str, Any]) -> dict[str, Any]:
        payload["brain_backoff_last_applied_s"] = max(_safe_int(wait_s, 0), 0)
        payload["brain_backoff_last_applied_ts"] = float(now_ts)
        return payload

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
