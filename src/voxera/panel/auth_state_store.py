from __future__ import annotations

from typing import Any

PANEL_AUTH_FAIL_THRESHOLD = 10
PANEL_AUTH_WINDOW_S = 60
PANEL_AUTH_LOCKOUT_S = 60
_PANEL_AUTH_PRUNE_TTL_MS = 10 * 60 * 1000
_PANEL_AUTH_MAX_TRACKED_IPS = 200


def _ip_key(ip: str) -> str:
    return ip.strip() or "unknown"


def _int_value(value: Any, *, default: int = 0) -> int:
    return int(value or default)


def prune_panel_auth_maps(store: dict[str, Any], *, now_ms: int) -> None:
    cutoff_ms = now_ms - _PANEL_AUTH_PRUNE_TTL_MS

    failures_raw = store.get("failures_by_ip")
    failures = failures_raw if isinstance(failures_raw, dict) else {}
    for ip, row in list(failures.items()):
        if not isinstance(row, dict):
            failures.pop(ip, None)
            continue
        if _int_value(row.get("last_ts_ms")) < cutoff_ms:
            failures.pop(ip, None)
    if len(failures) > _PANEL_AUTH_MAX_TRACKED_IPS:
        ordered = sorted(failures.items(), key=lambda item: _int_value(item[1].get("last_ts_ms")))
        for ip, _ in ordered[: len(failures) - _PANEL_AUTH_MAX_TRACKED_IPS]:
            failures.pop(ip, None)

    lockouts_raw = store.get("lockouts_by_ip")
    lockouts = lockouts_raw if isinstance(lockouts_raw, dict) else {}
    for ip, row in list(lockouts.items()):
        if not isinstance(row, dict):
            lockouts.pop(ip, None)
            continue
        last_event_ts = _int_value(row.get("last_event_ts_ms"))
        until_ts = _int_value(row.get("until_ts_ms"))
        if max(last_event_ts, until_ts) < cutoff_ms:
            lockouts.pop(ip, None)
    if len(lockouts) > _PANEL_AUTH_MAX_TRACKED_IPS:
        ordered = sorted(
            lockouts.items(), key=lambda item: _int_value(item[1].get("last_event_ts_ms"))
        )
        for ip, _ in ordered[: len(lockouts) - _PANEL_AUTH_MAX_TRACKED_IPS]:
            lockouts.pop(ip, None)

    store["failures_by_ip"] = failures
    store["lockouts_by_ip"] = lockouts


def apply_panel_auth_state_prune(payload: dict[str, Any], *, now_ms: int) -> dict[str, Any]:
    panel_auth_raw = payload.get("panel_auth")
    panel_auth = panel_auth_raw if isinstance(panel_auth_raw, dict) else {}
    prune_panel_auth_maps(panel_auth, now_ms=now_ms)
    payload["panel_auth"] = panel_auth
    payload["updated_at_ms"] = now_ms
    return payload


def apply_panel_auth_state_update(
    payload: dict[str, Any], *, ip: str, now_ms: int, auth_success: bool
) -> dict[str, Any]:
    window_ms = PANEL_AUTH_WINDOW_S * 1000
    lockout_ms = PANEL_AUTH_LOCKOUT_S * 1000

    panel_auth_raw = payload.get("panel_auth")
    panel_auth = panel_auth_raw if isinstance(panel_auth_raw, dict) else {}
    prune_panel_auth_maps(panel_auth, now_ms=now_ms)

    failures_raw = panel_auth.get("failures_by_ip")
    failures = failures_raw if isinstance(failures_raw, dict) else {}
    lockouts_raw = panel_auth.get("lockouts_by_ip")
    lockouts = lockouts_raw if isinstance(lockouts_raw, dict) else {}

    ip_key = _ip_key(ip)
    if auth_success:
        failures.pop(ip_key, None)
        lockout = lockouts.get(ip_key)
        if isinstance(lockout, dict) and now_ms >= _int_value(lockout.get("until_ts_ms")):
            lockouts.pop(ip_key, None)
    else:
        row = failures.get(ip_key)
        if not isinstance(row, dict):
            row = {"count": 0, "first_ts_ms": now_ms, "last_ts_ms": now_ms}
        first_ts = _int_value(row.get("first_ts_ms"), default=now_ms)
        count = _int_value(row.get("count"))
        if now_ms - first_ts > window_ms:
            count = 1
            first_ts = now_ms
        else:
            count += 1
        failures[ip_key] = {"count": count, "first_ts_ms": first_ts, "last_ts_ms": now_ms}
        if count >= PANEL_AUTH_FAIL_THRESHOLD:
            prev = lockouts.get(ip_key)
            prev_count = _int_value(prev.get("count")) if isinstance(prev, dict) else 0
            lockouts[ip_key] = {
                "until_ts_ms": now_ms + lockout_ms,
                "count": prev_count + 1,
                "last_event_ts_ms": now_ms,
            }

    panel_auth["failures_by_ip"] = failures
    panel_auth["lockouts_by_ip"] = lockouts
    payload["panel_auth"] = panel_auth
    payload["updated_at_ms"] = now_ms
    return payload


def active_lockout_until_ms(panel_auth: dict[str, Any], *, ip: str, now_ms: int) -> int | None:
    lockouts_raw = panel_auth.get("lockouts_by_ip")
    lockouts = lockouts_raw if isinstance(lockouts_raw, dict) else {}
    row = lockouts.get(_ip_key(ip))
    if not isinstance(row, dict):
        return None
    until = _int_value(row.get("until_ts_ms"))
    return until if now_ms < until else None


def auth_failure_snapshot(panel_auth: dict[str, Any], *, ip: str) -> tuple[int, int]:
    failures_raw = panel_auth.get("failures_by_ip")
    failures = failures_raw if isinstance(failures_raw, dict) else {}
    row = failures.get(_ip_key(ip))
    failure_row = row if isinstance(row, dict) else {}
    attempt_count = _int_value(failure_row.get("count"))

    lockouts_raw = panel_auth.get("lockouts_by_ip")
    lockouts = lockouts_raw if isinstance(lockouts_raw, dict) else {}
    lockout_row = lockouts.get(_ip_key(ip))
    lockout_until = (
        _int_value(lockout_row.get("until_ts_ms")) if isinstance(lockout_row, dict) else 0
    )
    return attempt_count, lockout_until
