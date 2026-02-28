from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

HEALTH_FILE_NAME = "health.json"


def health_path(queue_root: Path) -> Path:
    return queue_root / HEALTH_FILE_NAME


def read_health_snapshot(queue_root: Path) -> dict[str, Any]:
    path = health_path(queue_root)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_health_snapshot(queue_root: Path, payload: dict[str, Any]) -> None:
    queue_root.mkdir(parents=True, exist_ok=True)
    path = health_path(queue_root)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def update_health_snapshot(
    queue_root: Path,
    updater: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> dict[str, Any]:
    current = read_health_snapshot(queue_root)
    updated = updater(dict(current))
    final_payload = updated if isinstance(updated, dict) else current
    write_health_snapshot(queue_root, final_payload)
    return final_payload


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
