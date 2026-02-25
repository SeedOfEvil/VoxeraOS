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
