from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.background import BackgroundTask

from ..audit import log, tail
from ..brain.fallback import AUTH, MALFORMED, NETWORK, RATE_LIMIT, TIMEOUT, classify_fallback_reason
from ..brain.gemini import GeminiBrain
from ..brain.openai_compat import OpenAICompatBrain
from ..config import load_app_config
from ..config import load_config as load_runtime_config
from ..core.missions import MissionTemplate, _parse_mission_file
from ..core.queue_daemon import MissionQueueDaemon
from ..core.queue_inspect import lookup_job, queue_snapshot
from ..health import increment_health_counter, read_health_snapshot, update_health_snapshot
from ..health_reset import EVENT_BY_SCOPE, HealthResetError, reset_health_snapshot
from ..health_semantics import build_health_semantic_sections
from ..incident_bundle import BundleError
from ..operator_assistant import (
    append_thread_turn,
    build_assistant_messages,
    build_operator_assistant_context,
    fallback_operator_answer,
    new_thread_id,
    normalize_thread_id,
)
from ..ops_bundle import build_job_bundle, build_system_bundle
from ..version import get_version
from .assistant import (
    enqueue_assistant_question,
    read_assistant_result,
    read_assistant_thread_turns,
)
from .helpers import coerce_int as _coerce_int
from .helpers import request_value as _request_value
from .routes_home import register_home_routes
from .routes_jobs import register_job_routes

app = FastAPI(title="Voxera Panel", version=get_version())

HERE = Path(__file__).resolve().parent
templates = Environment(
    loader=FileSystemLoader(str(HERE / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

APPROVALS: list[dict[str, Any]] = []
MISSION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")

ERROR_MESSAGES = {
    "goal_required": "Goal is required when queue type is goal.",
    "mission_id_required": "Mission ID is required.",
    "queue_kind_invalid": "Queue type must be either goal or mission.",
    "mission_id_invalid": "Mission ID must be 2-64 characters and use lowercase letters, numbers, '_' or '-'.",
    "steps_json_invalid": "Steps JSON must be valid JSON.",
    "steps_json_not_list": "Steps JSON must decode to a JSON list.",
    "mission_schema_invalid": "Mission template failed schema validation.",
    "get_mutation_disabled": "GET mutation endpoints are disabled; submit the form normally.",
    "panel_prompt_required": "Prompt / Goal is required.",
}

FLASH_MESSAGES = {
    "approved": "Approval granted.",
    "approved_always": "Approval granted and remembered for matching scope.",
    "denied": "Approval denied.",
    "canceled": "Job moved to canceled/.",
    "retried": "Job re-enqueued into inbox/.",
    "deleted": "Terminal job deleted.",
    "cancel_not_found": "Cannot cancel: job was not found in active queue buckets.",
    "cannot_cancel_terminal": "Cannot cancel terminal jobs. Use retry/delete for failed/canceled/done.",
    "approval_not_found": "Approval/job reference was not found.",
    "approval_invalid": "Approval request was invalid.",
    "health_reset_current_state": "Health current state reset completed.",
    "health_reset_recent_history": "Health recent history reset completed.",
    "health_reset_current_and_recent": "Health current state and recent history reset completed.",
    "health_reset_historical_counters": "Historical counter reset completed.",
}

CSRF_COOKIE = "voxera_panel_csrf"
CSRF_FORM_KEY = "csrf_token"
PANEL_AUTH_FAIL_THRESHOLD = 10
PANEL_AUTH_WINDOW_S = 60
PANEL_AUTH_LOCKOUT_S = 60
_PANEL_AUTH_PRUNE_TTL_MS = 10 * 60 * 1000
_PANEL_AUTH_MAX_TRACKED_IPS = 200
_RECOVERY_ZIP_MAX_FILES = 5000
_RECOVERY_ZIP_MAX_TOTAL_BYTES = 250 * 1024 * 1024
_ASSISTANT_STALL_TIMEOUT_MS = 120_000
_ASSISTANT_FALLBACK_REASONS = frozenset({TIMEOUT, AUTH, RATE_LIMIT, MALFORMED, NETWORK})
_ASSISTANT_UNAVAILABLE_STATES = frozenset(
    {
        "unknown",
        "stopped",
        "unavailable",
        "offline",
        "error",
        "failed",
        "unhealthy",
    }
)


def _assistant_request_ts_ms(request_id: str) -> int | None:
    name = Path(request_id).name
    match = re.match(r"^job-assistant-(\d+)\.json$", name)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _assistant_stalled_degraded_reason(
    context: dict[str, Any], request_result: dict[str, Any], *, now_ms: int
) -> str | None:
    if not request_result:
        return None
    if str(request_result.get("advisory_mode") or "") == "degraded_brain_only":
        return None
    if str(request_result.get("answer") or "").strip():
        return None

    status = str(request_result.get("status") or "")
    if status not in {"queued", "thinking", "thinking through Voxera"}:
        return None

    if bool(context.get("queue_paused")):
        return "daemon_paused"

    current_state = context.get("health_current_state")
    daemon_state = ""
    if isinstance(current_state, dict):
        daemon_state = str(current_state.get("daemon_state") or "").strip().lower()
    if daemon_state in _ASSISTANT_UNAVAILABLE_STATES:
        return "daemon_unavailable"

    updated_at_ms = _coerce_int(request_result.get("updated_at_ms"))
    if (
        updated_at_ms is not None
        and updated_at_ms > 0
        and now_ms - updated_at_ms >= _ASSISTANT_STALL_TIMEOUT_MS
    ):
        return "queue_processing_timeout"

    request_id = str(request_result.get("request_id") or "")
    request_ts = _assistant_request_ts_ms(request_id)
    if request_ts is not None and now_ms - request_ts >= _ASSISTANT_STALL_TIMEOUT_MS:
        return "advisory_transport_stalled"

    return None


def _create_panel_assistant_brain(provider: Any) -> OpenAICompatBrain | GeminiBrain:
    if provider.type == "openai_compat":
        return OpenAICompatBrain(
            model=provider.model,
            base_url=provider.base_url or "https://openrouter.ai/api/v1",
            api_key_ref=provider.api_key_ref,
            extra_headers=provider.extra_headers,
        )
    if provider.type == "gemini":
        return GeminiBrain(model=provider.model, api_key_ref=provider.api_key_ref)
    raise ValueError(f"unsupported assistant provider type: {provider.type}")


def _degraded_mode_disclosure(degraded_reason: str, context: dict[str, Any]) -> str:
    daemon_state = "unknown"
    current_state = context.get("health_current_state")
    if isinstance(current_state, dict):
        daemon_state = str(current_state.get("daemon_state") or "unknown")
    if degraded_reason == "daemon_paused":
        return (
            "The advisory queue lane is paused right now, so I'm answering in model-only recovery mode. "
            "This is still read-only and grounded in current runtime context."
        )
    if degraded_reason in {"queue_processing_timeout", "advisory_transport_stalled"}:
        return (
            "The normal advisory queue transport is stalled, so this answer is coming through degraded "
            "model-only recovery mode while staying read-only and grounded."
        )
    if degraded_reason == "daemon_unavailable":
        return (
            f"I can still read the current control-plane picture, but daemon state looks '{daemon_state}', "
            "so this response is through degraded model-only recovery mode."
        )
    return (
        "Queue-backed advisory transport is unavailable, so I'm responding in degraded model-only recovery "
        "mode (still read-only and grounded)."
    )


async def _generate_degraded_assistant_answer_async(
    question: str,
    context: dict[str, Any],
    *,
    thread_turns: list[dict[str, Any]],
    degraded_reason: str,
) -> dict[str, Any]:
    cfg = load_app_config()
    disclosure = _degraded_mode_disclosure(degraded_reason, context)
    prompt = f"{question.strip()}\n\nMode context: {disclosure}"
    messages = build_assistant_messages(prompt, context, thread_turns=thread_turns)

    attempts: list[tuple[str, Any]] = []
    for key in ("primary", "fallback"):
        provider = cfg.brain.get(key) if cfg.brain else None
        if provider is not None:
            attempts.append((key, provider))

    primary_attempt: tuple[str, Any] | None = attempts[0] if attempts else None
    fallback_attempt: tuple[str, Any] | None = attempts[1] if len(attempts) > 1 else None

    if primary_attempt is not None:
        primary_name, primary_provider = primary_attempt
        try:
            brain = _create_panel_assistant_brain(primary_provider)
            resp = await brain.generate(messages, tools=[])
            text = str(resp.text or "").strip()
            if text:
                return {
                    "answer": f"{disclosure}\n\n{text}",
                    "provider": primary_name,
                    "model": str(primary_provider.model),
                    "fallback_used": False,
                    "fallback_from": None,
                    "fallback_reason": None,
                    "error_class": None,
                    "deterministic_used": False,
                }
            raise ValueError("assistant degraded primary provider returned empty response")
        except Exception as exc:
            reason = classify_fallback_reason(exc)
            if fallback_attempt is None or reason not in _ASSISTANT_FALLBACK_REASONS:
                fallback_attempt = None
            else:
                primary_error = (
                    reason,
                    type(exc).__name__,
                    primary_name,
                    str(primary_provider.model),
                )
            if fallback_attempt is None:
                primary_error = (
                    reason,
                    type(exc).__name__,
                    primary_name,
                    str(primary_provider.model),
                )
    else:
        primary_error = ("UNKNOWN", "RuntimeError", "none", "none")

    if fallback_attempt is not None:
        fallback_name, fallback_provider = fallback_attempt
        try:
            brain = _create_panel_assistant_brain(fallback_provider)
            resp = await brain.generate(messages, tools=[])
            text = str(resp.text or "").strip()
            if text:
                return {
                    "answer": f"{disclosure}\n\n{text}",
                    "provider": fallback_name,
                    "model": str(fallback_provider.model),
                    "fallback_used": True,
                    "fallback_from": {
                        "provider": primary_error[2],
                        "model": primary_error[3],
                    },
                    "fallback_reason": primary_error[0],
                    "error_class": primary_error[1],
                    "deterministic_used": False,
                }
            raise ValueError("assistant degraded fallback provider returned empty response")
        except Exception as exc:
            final_reason = classify_fallback_reason(exc)
            final_class = type(exc).__name__
    else:
        final_reason = primary_error[0]
        final_class = primary_error[1]

    fallback_text = fallback_operator_answer(question, context)
    return {
        "answer": f"{disclosure}\n\n{fallback_text}",
        "provider": "deterministic_fallback",
        "model": "fallback_operator_answer",
        "fallback_used": False,
        "fallback_from": None,
        "fallback_reason": final_reason,
        "error_class": final_class,
        "deterministic_used": True,
    }


def _generate_degraded_assistant_answer(
    question: str,
    context: dict[str, Any],
    *,
    thread_turns: list[dict[str, Any]],
    degraded_reason: str,
) -> dict[str, Any]:
    return asyncio.run(
        _generate_degraded_assistant_answer_async(
            question,
            context,
            thread_turns=thread_turns,
            degraded_reason=degraded_reason,
        )
    )


def _persist_degraded_assistant_result(
    queue_root: Path,
    *,
    request_id: str,
    thread_id: str,
    question: str,
    degraded_answer: dict[str, Any],
    degraded_reason: str,
    context: dict[str, Any],
    ts_ms: int,
) -> dict[str, Any]:
    normalized = f"{Path(request_id).stem}.json"
    artifact_dir = queue_root / "artifacts" / Path(normalized).stem
    artifact_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 2,
        "kind": "assistant_question",
        "thread_id": thread_id,
        "question": question,
        "answer": str(degraded_answer.get("answer") or ""),
        "updated_at_ms": ts_ms,
        "answered_at_ms": ts_ms,
        "provider": degraded_answer.get("provider"),
        "model": degraded_answer.get("model"),
        "fallback_used": bool(degraded_answer.get("fallback_used")),
        "fallback_from": degraded_answer.get("fallback_from"),
        "fallback_reason": degraded_answer.get("fallback_reason"),
        "error_class": degraded_answer.get("error_class"),
        "advisory_mode": "degraded_brain_only",
        "degraded_reason": degraded_reason,
        "degraded_at_ms": ts_ms,
        "deterministic_fallback_used": bool(degraded_answer.get("deterministic_used")),
        "context": context,
    }
    (artifact_dir / "assistant_response.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    return payload


class _RequestUrlLike(Protocol):
    @property
    def path(self) -> str: ...


class _RequestClientLike(Protocol):
    @property
    def host(self) -> str: ...


class _PanelSecurityRequestLike(Protocol):
    @property
    def url(self) -> _RequestUrlLike: ...

    @property
    def method(self) -> str: ...

    @property
    def client(self) -> _RequestClientLike | None: ...


def _settings():
    return load_runtime_config()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _client_ip(request: Request) -> str:
    trust_proxy = os.getenv("VOXERA_PANEL_TRUST_PROXY", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _prune_panel_auth_maps(store: dict[str, Any], *, now_ms: int) -> None:
    cutoff_ms = now_ms - _PANEL_AUTH_PRUNE_TTL_MS

    failures_raw = store.get("failures_by_ip")
    failures = failures_raw if isinstance(failures_raw, dict) else {}
    for ip, row in list(failures.items()):
        if not isinstance(row, dict):
            failures.pop(ip, None)
            continue
        if int(row.get("last_ts_ms", 0) or 0) < cutoff_ms:
            failures.pop(ip, None)
    if len(failures) > _PANEL_AUTH_MAX_TRACKED_IPS:
        ordered = sorted(failures.items(), key=lambda item: int(item[1].get("last_ts_ms", 0) or 0))
        for ip, _ in ordered[: len(failures) - _PANEL_AUTH_MAX_TRACKED_IPS]:
            failures.pop(ip, None)

    lockouts_raw = store.get("lockouts_by_ip")
    lockouts = lockouts_raw if isinstance(lockouts_raw, dict) else {}
    for ip, row in list(lockouts.items()):
        if not isinstance(row, dict):
            lockouts.pop(ip, None)
            continue
        last_event_ts = int(row.get("last_event_ts_ms", 0) or 0)
        until_ts = int(row.get("until_ts_ms", 0) or 0)
        if max(last_event_ts, until_ts) < cutoff_ms:
            lockouts.pop(ip, None)
    if len(lockouts) > _PANEL_AUTH_MAX_TRACKED_IPS:
        ordered = sorted(
            lockouts.items(), key=lambda item: int(item[1].get("last_event_ts_ms", 0) or 0)
        )
        for ip, _ in ordered[: len(lockouts) - _PANEL_AUTH_MAX_TRACKED_IPS]:
            lockouts.pop(ip, None)

    store["failures_by_ip"] = failures
    store["lockouts_by_ip"] = lockouts


def _panel_auth_state_update(
    queue_root: Path | None,
    *,
    ip: str,
    now_ms: int,
    auth_success: bool,
) -> dict[str, Any]:
    ip_key = ip.strip() or "unknown"
    window_ms = PANEL_AUTH_WINDOW_S * 1000
    lockout_ms = PANEL_AUTH_LOCKOUT_S * 1000

    def _apply(payload: dict[str, Any]) -> dict[str, Any]:
        panel_auth_raw = payload.get("panel_auth")
        panel_auth = panel_auth_raw if isinstance(panel_auth_raw, dict) else {}
        _prune_panel_auth_maps(panel_auth, now_ms=now_ms)

        failures = panel_auth.get("failures_by_ip", {})
        lockouts = panel_auth.get("lockouts_by_ip", {})

        if auth_success:
            failures.pop(ip_key, None)
            lockout = lockouts.get(ip_key)
            if isinstance(lockout, dict) and now_ms >= int(lockout.get("until_ts_ms", 0) or 0):
                lockouts.pop(ip_key, None)
        else:
            row = failures.get(ip_key)
            if not isinstance(row, dict):
                row = {"count": 0, "first_ts_ms": now_ms, "last_ts_ms": now_ms}
            first_ts = int(row.get("first_ts_ms", now_ms) or now_ms)
            count = int(row.get("count", 0) or 0)
            if now_ms - first_ts > window_ms:
                count = 1
                first_ts = now_ms
            else:
                count += 1
            failures[ip_key] = {"count": count, "first_ts_ms": first_ts, "last_ts_ms": now_ms}
            if count >= PANEL_AUTH_FAIL_THRESHOLD:
                prev = lockouts.get(ip_key)
                prev_count = int(prev.get("count", 0) or 0) if isinstance(prev, dict) else 0
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

    return update_health_snapshot(queue_root, _apply)


def _panel_auth_state_prune(queue_root: Path | None, *, now_ms: int) -> dict[str, Any]:
    def _apply(payload: dict[str, Any]) -> dict[str, Any]:
        panel_auth_raw = payload.get("panel_auth")
        panel_auth = panel_auth_raw if isinstance(panel_auth_raw, dict) else {}
        _prune_panel_auth_maps(panel_auth, now_ms=now_ms)
        payload["panel_auth"] = panel_auth
        payload["updated_at_ms"] = now_ms
        return payload

    return update_health_snapshot(queue_root, _apply)


def _active_lockout_until_ms(*, queue_root: Path | None, ip: str, now_ms: int) -> int | None:
    payload = _panel_auth_state_prune(queue_root, now_ms=now_ms)
    panel_auth_raw = payload.get("panel_auth")
    panel_auth = panel_auth_raw if isinstance(panel_auth_raw, dict) else {}
    lockouts_raw = panel_auth.get("lockouts_by_ip")
    lockouts = lockouts_raw if isinstance(lockouts_raw, dict) else {}
    row = lockouts.get(ip.strip() or "unknown")
    if not isinstance(row, dict):
        return None
    until = int(row.get("until_ts_ms", 0) or 0)
    return until if now_ms < until else None


def _queue_root() -> Path:
    return _settings().queue_root


def _health_queue_root() -> Path | None:
    isolated_health = os.getenv("VOXERA_HEALTH_PATH", "").strip()
    if not isolated_health:
        return _queue_root()

    # Keep production/runtime semantics unchanged for explicit queue roots.
    if os.getenv("VOXERA_QUEUE_ROOT", "").strip():
        return _queue_root()

    configured_root = _queue_root().expanduser().resolve()
    repo_operator_root = (Path.cwd() / "notes" / "queue").resolve()
    # Test-only safety net: when panel would target the repo default queue root,
    # route health writes through VOXERA_HEALTH_PATH instead.
    if configured_root == repo_operator_root:
        return None
    return _queue_root()


def _missions_dir() -> Path:
    return Path.home() / ".config" / "voxera" / "missions"


def _allow_get_mutations() -> bool:
    return _settings().panel_enable_get_mutations


def _request_meta(request: _PanelSecurityRequestLike) -> dict[str, Any]:
    return {
        "path": request.url.path,
        "method": request.method,
        "remote": (request.client.host if request.client else "unknown"),
    }


def _log_panel_security_event(
    event: str,
    *,
    request: _PanelSecurityRequestLike,
    reason: str,
    status_code: int,
) -> None:
    meta = _request_meta(request)
    log(
        {
            "event": event,
            "ts_ms": int(time.time() * 1000),
            "path": meta["path"],
            "method": meta["method"],
            "remote": meta["remote"],
            "reason": reason,
            "status_code": status_code,
        }
    )


def _panel_security_counter_incr(key: str, *, last_error: str | None = None) -> None:
    increment_health_counter(_health_queue_root(), key, last_error=last_error)


def _panel_security_snapshot() -> dict[str, Any]:
    payload = read_health_snapshot(_health_queue_root())
    counters = payload.get("counters")
    return counters if isinstance(counters, dict) else {}


def _auth_setup_banner() -> dict[str, str] | None:
    settings = _settings()
    if settings.panel_operator_password not in {None, ""}:
        return None
    config_path_hint = str(settings.config_path.expanduser())
    return {
        "title": "Setup required: panel operator password is not configured.",
        "detail": (
            "Mutation routes require Basic auth. Set VOXERA_PANEL_OPERATOR_PASSWORD in your "
            "user service environment and restart panel + daemon. If VOXERA_LOAD_DOTENV=1, "
            ".env may override file settings."
        ),
        "path_hint": f"Config file: {config_path_hint}",
        "commands": (
            "systemctl --user edit voxera-panel.service\n"
            "# add [Service] Environment=VOXERA_PANEL_OPERATOR_PASSWORD=<set-a-strong-password>\n"
            "systemctl --user daemon-reload\n"
            "systemctl --user restart voxera-panel.service voxera-daemon.service"
        ),
    }


def _format_ts(ts_ms: int | None) -> str:
    if ts_ms is None or ts_ms <= 0:
        return "—"
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _format_ts_seconds(ts_s: float | None) -> str:
    if ts_s is None or ts_s <= 0:
        return "—"
    return datetime.fromtimestamp(ts_s, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _format_age(age_s: int | None) -> str:
    if age_s is None or age_s < 0:
        return "—"
    if age_s < 60:
        return f"{age_s}s"
    minutes, seconds = divmod(age_s, 60)
    if seconds:
        return f"{minutes}m {seconds}s"
    return f"{minutes}m"


def _history_value(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    return text or "-"


def _history_pair(value: Any, ts_label: str) -> str:
    val = _history_value(value)
    ts = ts_label.strip() if ts_label else "-"
    if val == "-" and ts in {"-", "—"}:
        return "-"
    return f"{val} @ {ts}"


def _daemon_health_view(health: dict[str, Any]) -> dict[str, Any]:
    lock_raw = health.get("lock_status")
    lock: dict[str, Any] = lock_raw if isinstance(lock_raw, dict) else {}
    lock_state = str(health.get("lock_state") or "").strip().lower()
    lock_status = str(lock.get("status") or "").strip().lower()
    if lock_status not in {"held", "stale", "clear"}:
        if lock_state in {"active", "locked_by_other"}:
            lock_status = "held"
        elif lock_state in {"stale", "reclaimed"}:
            lock_status = "stale"
        else:
            lock_status = "clear"

    lock_pid = _coerce_int(lock.get("pid"))
    if lock_pid is None:
        lock_pid = _coerce_int(health.get("lock_holder_pid"))

    fallback_reason = health.get("last_fallback_reason")
    fallback_tier = health.get("last_fallback_to")
    fallback_ts = _coerce_int(health.get("last_fallback_ts_ms"))
    has_fallback = any([fallback_reason, fallback_tier, fallback_ts])

    startup_recovery = health.get("last_startup_recovery")
    if isinstance(startup_recovery, dict):
        recovery_counts = startup_recovery.get("counts")
        recovery_ts = _coerce_int(startup_recovery.get("ts_ms"))
    else:
        recovery_counts = health.get("last_startup_recovery_counts")
        recovery_ts = _coerce_int(health.get("last_startup_recovery_ts"))
    counts = recovery_counts if isinstance(recovery_counts, dict) else {}
    recovery_job_count = _coerce_int(counts.get("jobs_failed")) or 0
    orphan_count = (_coerce_int(counts.get("orphan_approvals_quarantined")) or 0) + (
        _coerce_int(counts.get("orphan_state_files_quarantined")) or 0
    )
    has_recovery = any([recovery_job_count, orphan_count, recovery_ts])

    shutdown_outcome = str(health.get("last_shutdown_outcome") or "").strip() or "unknown"
    shutdown_ts_raw = health.get("last_shutdown_ts")
    shutdown_ts = float(shutdown_ts_raw) if isinstance(shutdown_ts_raw, (int, float)) else None
    shutdown_reason = str(health.get("last_shutdown_reason") or "").strip() or "—"
    shutdown_job = str(health.get("last_shutdown_job") or "").strip() or "—"

    stale_age_s = _coerce_int(lock.get("stale_age_s"))

    return {
        "lock_status": lock_status,
        "lock_pid": lock_pid,
        "lock_stale_age_s": stale_age_s,
        "lock_stale_age_label": _format_age(stale_age_s),
        "last_brain_fallback": {
            "present": has_fallback,
            "tier": str(fallback_tier or "—"),
            "reason": str(fallback_reason or "—"),
            "ts": _format_ts(fallback_ts),
        },
        "last_startup_recovery": {
            "present": has_recovery,
            "job_count": recovery_job_count,
            "orphan_count": orphan_count,
            "ts": _format_ts(recovery_ts),
        },
        "last_shutdown": {
            "present": shutdown_outcome != "unknown" or shutdown_ts is not None,
            "outcome": shutdown_outcome,
            "ts": _format_ts_seconds(shutdown_ts),
            "reason": shutdown_reason,
            "job": shutdown_job,
        },
        "daemon_state": str(health.get("daemon_state") or "healthy"),
    }


def _performance_stats_view(queue: dict[str, Any], health: dict[str, Any]) -> dict[str, Any]:
    counts_raw = queue.get("counts")
    counts = counts_raw if isinstance(counts_raw, dict) else {}
    grouped = build_health_semantic_sections(
        health,
        queue_context={
            "queue_root": queue.get("queue_root"),
            "health_path": queue.get("health_path"),
            "intake_glob": queue.get("intake_glob"),
            "paused": bool(queue.get("paused", False)),
        },
        lock_status=queue.get("lock_status") if isinstance(queue.get("lock_status"), dict) else {},
        daemon_lock_counters=queue.get("daemon_lock_counters")
        if isinstance(queue.get("daemon_lock_counters"), dict)
        else {},
    )
    current_state = grouped["current_state"]
    recent_history = grouped["recent_history"]
    historical_counters = grouped["historical_counters"]
    shutdown_ts_raw = recent_history.get("last_shutdown_ts")
    shutdown_ts = float(shutdown_ts_raw) if isinstance(shutdown_ts_raw, (int, float)) else None
    fallback = recent_history.get("last_brain_fallback", {})

    return {
        "queue_counts": {
            "inbox": int(counts.get("inbox", 0) or 0),
            "pending": int(counts.get("pending", 0) or 0),
            "pending_approvals": int(counts.get("pending_approvals", 0) or 0),
            "done": int(counts.get("done", 0) or 0),
            "failed": int(counts.get("failed", 0) or 0),
            "canceled": int(counts.get("canceled", 0) or 0),
        },
        "current_state": current_state,
        "recent_history": {
            "last_fallback_line": (
                "-"
                if not isinstance(fallback, dict)
                or not any(
                    [
                        fallback.get("reason"),
                        fallback.get("from"),
                        fallback.get("to"),
                        fallback.get("ts_ms"),
                    ]
                )
                else (
                    f"{_history_value(fallback.get('reason'))} "
                    f"({_history_value(fallback.get('from'))} → {_history_value(fallback.get('to'))}) "
                    f"@ {_format_ts(_coerce_int(fallback.get('ts_ms')))}"
                )
            ),
            "last_error_line": _history_pair(
                recent_history.get("last_error"),
                _format_ts(_coerce_int(recent_history.get("last_error_ts_ms"))),
            ),
            "last_shutdown_line": (
                "-"
                if not any(
                    [
                        recent_history.get("last_shutdown_outcome"),
                        recent_history.get("last_shutdown_reason"),
                        recent_history.get("last_shutdown_job"),
                        recent_history.get("last_shutdown_ts"),
                    ]
                )
                else (
                    f"{_history_value(recent_history.get('last_shutdown_outcome'))} / "
                    f"{_history_value(recent_history.get('last_shutdown_reason'))} / "
                    f"{_history_value(recent_history.get('last_shutdown_job'))} @ "
                    f"{_format_ts_seconds(shutdown_ts)}"
                )
            ),
            "degraded_since_ts": _history_value(recent_history.get("degraded_since_ts")),
            "brain_backoff_last_applied_s": int(
                recent_history.get("brain_backoff_last_applied_s", 0) or 0
            ),
            "brain_backoff_last_applied_ts": _history_value(
                recent_history.get("brain_backoff_last_applied_ts")
            ),
        },
        "historical_counters": {
            "panel_auth_invalid": int(historical_counters.get("panel_auth_invalid", 0) or 0),
            "panel_401_count": int(historical_counters.get("panel_401_count", 0) or 0),
            "panel_403_count": int(historical_counters.get("panel_403_count", 0) or 0),
            "panel_429_count": int(historical_counters.get("panel_429_count", 0) or 0),
            "panel_csrf_missing": int(historical_counters.get("panel_csrf_missing", 0) or 0),
            "panel_csrf_invalid": int(historical_counters.get("panel_csrf_invalid", 0) or 0),
            "panel_mutation_allowed": int(
                historical_counters.get("panel_mutation_allowed", 0) or 0
            ),
            "brain_fallback_count": int(historical_counters.get("brain_fallback_count", 0) or 0),
            "brain_fallback_reason_timeout": int(
                historical_counters.get("brain_fallback_reason_timeout", 0) or 0
            ),
            "brain_fallback_reason_auth": int(
                historical_counters.get("brain_fallback_reason_auth", 0) or 0
            ),
            "brain_fallback_reason_rate_limit": int(
                historical_counters.get("brain_fallback_reason_rate_limit", 0) or 0
            ),
            "brain_fallback_reason_malformed": int(
                historical_counters.get("brain_fallback_reason_malformed", 0) or 0
            ),
            "brain_fallback_reason_network": int(
                historical_counters.get("brain_fallback_reason_network", 0) or 0
            ),
            "brain_fallback_reason_unknown": int(
                historical_counters.get("brain_fallback_reason_unknown", 0) or 0
            ),
        },
    }


def _operator_credentials(request: _PanelSecurityRequestLike) -> tuple[str, str]:
    settings = _settings()
    user = settings.panel_operator_user
    password = settings.panel_operator_password
    if not password:
        _panel_security_counter_incr("panel_401_count", last_error="operator password missing")
        _log_panel_security_event(
            "panel_operator_config_error",
            request=request,
            reason="operator_password_missing",
            status_code=503,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VOXERA_PANEL_OPERATOR_PASSWORD must be set",
        )
    return user, password


def _require_operator_basic_auth(request: Request, authorization: str | None) -> None:
    user, password = _operator_credentials(request)
    now_ms = _now_ms()
    ip = _client_ip(request)
    lockout_until_ms = _active_lockout_until_ms(
        queue_root=_health_queue_root(), ip=ip, now_ms=now_ms
    )
    if lockout_until_ms is not None:
        _panel_security_counter_incr("panel_429_count")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many authentication attempts",
            headers={"Retry-After": str(PANEL_AUTH_LOCKOUT_S)},
        )

    if not authorization:
        _panel_auth_state_update(
            queue_root=_health_queue_root(), ip=ip, now_ms=now_ms, auth_success=False
        )
        _panel_security_counter_incr("panel_401_count", last_error="missing authorization")
        _log_panel_security_event(
            "panel_auth_missing", request=request, reason="missing_authorization", status_code=401
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="operator authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )
    import base64

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "basic" or not token:
        _panel_auth_state_update(
            queue_root=_health_queue_root(), ip=ip, now_ms=now_ms, auth_success=False
        )
        _panel_security_counter_incr(
            "panel_auth_invalid", last_error="invalid authentication scheme"
        )
        _panel_security_counter_incr("panel_401_count")
        _log_panel_security_event(
            "panel_auth_invalid", request=request, reason="invalid_scheme", status_code=401
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid authentication scheme",
            headers={"WWW-Authenticate": "Basic"},
        )
    try:
        decoded = base64.b64decode(token).decode("utf-8")
    except Exception as exc:
        _panel_auth_state_update(
            queue_root=_health_queue_root(), ip=ip, now_ms=now_ms, auth_success=False
        )
        _panel_security_counter_incr(
            "panel_auth_invalid", last_error="invalid authorization header"
        )
        _panel_security_counter_incr("panel_401_count")
        _log_panel_security_event(
            "panel_auth_invalid", request=request, reason="invalid_header", status_code=401
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid authorization header",
            headers={"WWW-Authenticate": "Basic"},
        ) from exc
    got_user, _, got_password = decoded.partition(":")
    if not (
        secrets.compare_digest(got_user, user) and secrets.compare_digest(got_password, password)
    ):
        payload = _panel_auth_state_update(
            queue_root=_health_queue_root(), ip=ip, now_ms=now_ms, auth_success=False
        )
        panel_auth_raw = payload.get("panel_auth")
        panel_auth = panel_auth_raw if isinstance(panel_auth_raw, dict) else {}
        failures_raw = panel_auth.get("failures_by_ip")
        failures = failures_raw if isinstance(failures_raw, dict) else {}
        ip_key = ip.strip() or "unknown"
        failure_row_raw = failures.get(ip_key)
        failure_row = failure_row_raw if isinstance(failure_row_raw, dict) else {}
        lockouts_raw = panel_auth.get("lockouts_by_ip")
        lockouts = lockouts_raw if isinstance(lockouts_raw, dict) else {}
        lockout_row = lockouts.get(ip_key) if isinstance(lockouts.get(ip_key), dict) else None
        if lockout_row is not None and now_ms < int(lockout_row.get("until_ts_ms", 0) or 0):
            attempt_count = int(failure_row.get("count", 0) or 0)
            _panel_security_counter_incr("panel_429_count")
            log(
                {
                    "event": "panel_auth_lockout",
                    "ts_ms": now_ms,
                    "ip": ip,
                    "attempt_count": attempt_count,
                    "window_s": PANEL_AUTH_WINDOW_S,
                    "lockout_s": PANEL_AUTH_LOCKOUT_S,
                }
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="too many authentication attempts",
                headers={"Retry-After": str(PANEL_AUTH_LOCKOUT_S)},
            )

        _panel_security_counter_incr(
            "panel_auth_invalid", last_error="invalid operator credentials"
        )
        _panel_security_counter_incr("panel_401_count")
        _log_panel_security_event(
            "panel_auth_invalid", request=request, reason="invalid_credentials", status_code=401
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid operator credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

    _panel_auth_state_update(
        queue_root=_health_queue_root(), ip=ip, now_ms=now_ms, auth_success=True
    )


async def _require_mutation_guard(request: Request) -> None:
    _require_operator_auth_from_request(request)
    if not _settings().panel_csrf_enabled:
        _panel_security_counter_incr("panel_mutation_allowed")
        _log_panel_security_event(
            "panel_mutation_allowed",
            request=request,
            reason="auth_valid_csrf_disabled",
            status_code=200,
        )
        return
    cookie_token = request.cookies.get(CSRF_COOKIE, "")
    request_token = (request.headers.get("x-csrf-token") or "").strip() or (
        await _request_value(request, CSRF_FORM_KEY, "")
    ).strip()
    if not cookie_token or not request_token:
        _panel_security_counter_incr("panel_403_count", last_error="csrf token missing")
        _panel_security_counter_incr("panel_csrf_missing")
        _log_panel_security_event(
            "panel_csrf_missing", request=request, reason="csrf_token_missing", status_code=403
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="csrf validation failed")
    if not secrets.compare_digest(cookie_token, request_token):
        _panel_security_counter_incr("panel_403_count", last_error="csrf token mismatch")
        _panel_security_counter_incr("panel_csrf_invalid")
        _log_panel_security_event(
            "panel_csrf_invalid", request=request, reason="csrf_token_mismatch", status_code=403
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="csrf validation failed")
    _panel_security_counter_incr("panel_mutation_allowed")
    _log_panel_security_event(
        "panel_mutation_allowed",
        request=request,
        reason="auth_and_csrf_valid",
        status_code=200,
    )


def _require_operator_auth_from_request(request: Request) -> None:
    _require_operator_basic_auth(request, request.headers.get("authorization"))


def _validate_mission_id(mission_id: str) -> str:
    normalized = mission_id.strip()
    if not MISSION_ID_RE.fullmatch(normalized):
        raise ValueError("mission_id_invalid")
    return normalized


def _enforce_get_mutations_enabled() -> None:
    if not _allow_get_mutations():
        raise HTTPException(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            detail="GET mutation endpoints are disabled",
        )


def _is_within_path(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _bucket_base_dir(bucket: str) -> Path:
    queue_root = _queue_root()
    if bucket == "recovery":
        return queue_root / "recovery"
    if bucket == "quarantine":
        return queue_root / "quarantine"
    raise HTTPException(status_code=404, detail="Not found")


def _dir_metrics(root: Path) -> tuple[int, int]:
    file_count = 0
    total_size = 0
    for current_root, dir_names, file_names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current_root)
        dir_names[:] = [
            name for name in sorted(dir_names) if not (current_path / name).is_symlink()
        ]
        for file_name in sorted(file_names):
            file_path = current_path / file_name
            if file_path.is_symlink() or not file_path.is_file():
                continue
            stat = file_path.stat()
            file_count += 1
            total_size += int(stat.st_size)
    return file_count, total_size


def _collect_bucket_items(bucket: str) -> list[dict[str, Any]]:
    base = _bucket_base_dir(bucket)
    if not base.exists() or not base.is_dir():
        return []

    items: list[dict[str, Any]] = []
    for child in sorted(base.iterdir(), key=lambda entry: entry.name):
        if child.is_symlink():
            continue
        stat = child.stat()
        if child.is_dir():
            file_count, size_bytes = _dir_metrics(child)
            kind = "dir"
        elif child.is_file():
            file_count, size_bytes = 1, int(stat.st_size)
            kind = "file"
        else:
            continue
        items.append(
            {
                "name": child.name,
                "kind": kind,
                "mtime_ts": int(stat.st_mtime),
                "size_bytes": size_bytes,
                "file_count": file_count,
            }
        )
    return items


def _build_recovery_zip(target: Path, zip_path: Path) -> None:
    files_added = 0
    total_size = 0

    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        if target.is_file() and not target.is_symlink():
            total_size = int(target.stat().st_size)
            if total_size > _RECOVERY_ZIP_MAX_TOTAL_BYTES:
                raise HTTPException(status_code=413, detail="Requested archive too large")
            zf.write(target, arcname=target.name)
            return

        for current_root, dir_names, file_names in os.walk(target, topdown=True, followlinks=False):
            current_path = Path(current_root)
            dir_names[:] = [
                name for name in sorted(dir_names) if not (current_path / name).is_symlink()
            ]
            for file_name in sorted(file_names):
                file_path = current_path / file_name
                if file_path.is_symlink() or not file_path.is_file():
                    continue
                stat = file_path.stat()
                files_added += 1
                total_size += int(stat.st_size)
                if files_added > _RECOVERY_ZIP_MAX_FILES:
                    raise HTTPException(
                        status_code=413, detail="Requested archive has too many files"
                    )
                if total_size > _RECOVERY_ZIP_MAX_TOTAL_BYTES:
                    raise HTTPException(status_code=413, detail="Requested archive too large")
                arcname = file_path.relative_to(target)
                zf.write(file_path, arcname=str(arcname))


def _write_queue_job(payload: dict[str, Any]) -> str:
    queue_root = _queue_root()
    inbox = queue_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    job_id = f"job-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    tmp_path = inbox / f".{job_id}.tmp.json"
    final_path = inbox / f"{job_id}.json"
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(final_path)
    return final_path.name


def _write_panel_mission_job(*, prompt: str, approval_required: bool) -> tuple[str, str]:
    queue_root = _queue_root()
    inbox = queue_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    normalized_prompt = prompt.strip()
    slug = re.sub(r"[^a-z0-9_-]+", "-", normalized_prompt.lower()).strip("-")
    slug = slug[:32] or "mission"
    ts = int(time.time())
    suffix = hashlib.sha1(normalized_prompt.encode("utf-8")).hexdigest()[:6]
    mission_id = re.sub(r"[^a-z0-9_-]+", "-", f"{slug}-{suffix}-{ts}").strip("-")

    payload = {
        "id": mission_id,
        "goal": normalized_prompt,
        "approval_required": approval_required,
    }

    base_name = f"job-panel-mission-{slug}-{ts}"
    final_path = inbox / f"{base_name}.json"
    counter = 1
    while final_path.exists():
        final_path = inbox / f"{base_name}-{counter}.json"
        counter += 1

    tmp_path = inbox / f".{final_path.stem}.tmp.json"
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(final_path)
    return final_path.name, mission_id


def _artifact_text(path: Path, *, max_chars: int = 8000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[:max_chars] + ("\n...[truncated]..." if len(text) > max_chars else "")


def _safe_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_actions(path: Path, *, limit: int = 200) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    actions: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except Exception:
            event = {"raw": line}
        if isinstance(event, dict):
            actions.append(event)
    return list(reversed(actions[-limit:]))


def _read_generated_files(artifacts_dir: Path) -> list[str]:
    generated = artifacts_dir / "outputs" / "generated_files.json"
    if not generated.exists():
        return []
    try:
        payload = json.loads(generated.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    return [str(item) for item in payload] if isinstance(payload, list) else []


def _trim_tail(value: str, *, max_chars: int = 2000) -> str:
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _repo_root_for_panel_subprocess() -> Path:
    env_root = os.getenv("VOXERA_REPO_ROOT", "").strip()
    if env_root:
        candidate = Path(env_root).expanduser().resolve()
        if candidate.exists() and candidate.is_dir():
            return candidate

    default_root = Path(__file__).resolve().parents[3]
    if default_root.exists() and default_root.is_dir():
        return default_root
    return Path.cwd()


def _run_queue_hygiene_command(queue_root: Path, args: list[str]) -> dict[str, Any]:
    run_cwd = _repo_root_for_panel_subprocess()
    commands = [
        [sys.executable, "-m", "voxera.cli", *args, "--queue-dir", str(queue_root)],
        ["voxera", *args, "--queue-dir", str(queue_root)],
    ]
    attempted: list[dict[str, Any]] = []

    for cmd in commands:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=run_cwd)
        except FileNotFoundError as exc:
            attempted.append(
                {
                    "cmd": cmd,
                    "cwd": str(run_cwd),
                    "exit_code": None,
                    "stderr_tail": _trim_tail(str(exc)),
                    "stdout_tail": "",
                }
            )
            continue

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        stdout_tail = _trim_tail(stdout)
        stderr_tail = _trim_tail(stderr)

        result: dict[str, Any] = {
            "ok": False,
            "result": {},
            "exit_code": int(proc.returncode),
            "stderr_tail": stderr_tail,
            "stdout_tail": stdout_tail,
            "cmd": cmd,
            "cwd": str(run_cwd),
            "attempted": attempted,
            "error": "",
        }

        if proc.returncode != 0:
            result["error"] = _trim_tail(stderr or stdout or "command failed")
        else:
            if not stdout.strip():
                result["error"] = "no json output"
            else:
                try:
                    parsed = json.loads(stdout)
                except json.JSONDecodeError:
                    result["error"] = "json parse failed"
                else:
                    if not isinstance(parsed, dict):
                        result["error"] = "json parse failed"
                    else:
                        result["ok"] = True
                        result["result"] = parsed

        if not result["ok"]:
            log(
                {
                    "event": "panel_hygiene_command_failed",
                    "cmd": cmd,
                    "rc": int(proc.returncode),
                    "stderr_tail": stderr_tail,
                    "stdout_tail": stdout_tail,
                    "error": result["error"],
                    "cwd": str(run_cwd),
                }
            )
        return result

    last_attempt = attempted[-1] if attempted else {}
    error_tail = _trim_tail(
        str(last_attempt.get("stderr_tail") or "voxera CLI executable not found")
    )
    failure = {
        "ok": False,
        "result": {},
        "exit_code": None,
        "stderr_tail": error_tail,
        "stdout_tail": "",
        "cmd": last_attempt.get("cmd", commands[0]),
        "cwd": str(run_cwd),
        "attempted": attempted,
        "error": error_tail,
    }
    log(
        {
            "event": "panel_hygiene_command_failed",
            "cmd": failure["cmd"],
            "rc": None,
            "stderr_tail": error_tail,
            "stdout_tail": "",
            "error": failure["error"],
            "cwd": str(run_cwd),
        }
    )
    return failure


def _write_hygiene_result(queue_root: Path, key: str, result: dict[str, Any]) -> None:
    def _apply(payload: dict[str, Any]) -> dict[str, Any]:
        payload[key] = result
        payload["updated_at_ms"] = _now_ms()
        return payload

    update_health_snapshot(queue_root, _apply)


def _job_detail_payload(queue_root: Path, job_id: str) -> dict[str, Any]:
    lookup = lookup_job(queue_root, job_id)
    if lookup is None:
        stem = Path(job_id).stem
        artifacts_dir = queue_root / "artifacts" / stem
        if not artifacts_dir.exists():
            raise HTTPException(status_code=404, detail="job not found")
        primary: dict[str, Any] = {}
        approval: dict[str, Any] = {}
        failed_sidecar: dict[str, Any] = {}
        bucket = "unknown"
        job_name = f"{stem}.json"
    else:
        primary = _safe_json(lookup.primary_path)
        approval = _safe_json(lookup.approval_path) if lookup.approval_path else {}
        failed_sidecar = (
            _safe_json(lookup.failed_sidecar_path) if lookup.failed_sidecar_path else {}
        )
        artifacts_dir = lookup.artifacts_dir
        bucket = lookup.bucket
        job_name = lookup.job_id

    state_sidecar: dict[str, Any] = {}
    stem = Path(job_name).stem
    state_candidates = [
        queue_root / bucket / f"{stem}.state.json"
        for bucket in ("pending", "inbox", "done", "failed", "canceled")
    ]
    for state_path in state_candidates:
        if not state_path.exists():
            continue
        loaded = _safe_json(state_path)
        if loaded:
            state_sidecar = loaded
            break

    artifact_files = (
        [
            child.relative_to(artifacts_dir).as_posix()
            for child in sorted(artifacts_dir.rglob("*"))
            if child.is_file()
        ]
        if artifacts_dir.exists()
        else []
    )

    snapshot = queue_snapshot(queue_root)
    relevant_events = [
        item
        for item in reversed(tail(200))
        if job_name in str(item.get("job", ""))
        or item.get("event") in {"queue_job_failed", "queue_job_done"}
    ]
    return {
        "job_id": job_name,
        "bucket": bucket,
        "job": primary,
        "approval": approval,
        "state": state_sidecar,
        "failed_sidecar": failed_sidecar,
        "lock": snapshot.get("lock_status", {}),
        "paused": snapshot.get("paused", False),
        "plan": _safe_json(artifacts_dir / "plan.json"),
        "actions": _load_actions(artifacts_dir / "actions.jsonl"),
        "stdout": _artifact_text(artifacts_dir / "stdout.txt", max_chars=64 * 1024),
        "stderr": _artifact_text(artifacts_dir / "stderr.txt", max_chars=64 * 1024),
        "generated_files": _read_generated_files(artifacts_dir),
        "artifact_files": artifact_files,
        "artifacts_dir": str(artifacts_dir),
        "audit_timeline": relevant_events[:40],
        "has_approval": bool(approval),
        "can_cancel": bucket in {"inbox", "pending", "approvals"},
        "can_retry": bucket in {"failed", "canceled"},
        "can_delete": bucket in {"done", "failed", "canceled"},
    }


def _incident_archive_dir(queue_root: Path, suffix: str) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    out = queue_root / "_archive" / f"incident-{stamp}-{suffix}"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _job_artifact_flags(queue_root: Path, job_id: str) -> dict[str, bool]:
    artifacts_dir = queue_root / "artifacts" / Path(job_id).stem
    return {
        "plan": (artifacts_dir / "plan.json").exists(),
        "actions": (artifacts_dir / "actions.jsonl").exists(),
        "stdout": (artifacts_dir / "stdout.txt").exists(),
        "stderr": (artifacts_dir / "stderr.txt").exists(),
    }


def _last_activity(artifacts_dir: Path) -> str:
    actions = artifacts_dir / "actions.jsonl"
    if not actions.exists():
        return ""
    lines = actions.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in reversed(lines):
        if line.strip():
            return line[:180]
    return ""


def _job_ref_bucket(row: dict[str, Any]) -> str:
    bucket = str(row.get("bucket") or "")
    if bucket == "approvals":
        return "pending/approvals"
    return bucket


def _job_artifact_payload(queue_root: Path, job_name: str) -> dict[str, Any]:
    stem = Path(job_name).stem
    art = queue_root / "artifacts" / stem
    plan = {}
    if (art / "plan.json").exists():
        with (art / "plan.json").open("r", encoding="utf-8") as f:
            plan = json.load(f)
    actions: list[dict[str, Any]] = []
    actions_path = art / "actions.jsonl"
    if actions_path.exists():
        for line in actions_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                actions.append(json.loads(line))
            except Exception:
                continue
    actions.reverse()
    generated_files: list[str] = []
    generated = art / "outputs" / "generated_files.json"
    if generated.exists():
        try:
            parsed = json.loads(generated.read_text(encoding="utf-8"))
            if isinstance(parsed, list):
                generated_files = [str(i) for i in parsed]
        except Exception:
            generated_files = []
    return {
        "job": job_name,
        "artifacts_dir": str(art),
        "plan": plan,
        "actions": actions,
        "stdout": _artifact_text(art / "stdout.txt"),
        "stderr": _artifact_text(art / "stderr.txt"),
        "generated_files": generated_files,
    }


def _build_activity(
    audit_events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    active: dict[str, dict[str, Any]] = {}
    recent: list[dict[str, Any]] = []
    for event in audit_events:
        event_name = str(event.get("event", ""))
        job = Path(str(event.get("job", ""))).name if event.get("job") else ""
        mission = str(event.get("mission") or "")
        goal = str(event.get("goal") or "")

        if event_name == "queue_job_started" and job:
            active[job] = {
                "job": job,
                "mission": mission,
                "goal": goal,
                "state": "running",
            }
        if event_name in {"queue_job_done", "queue_job_failed"} and job:
            active.pop(job, None)

        if event_name.startswith("queue_") or event_name.startswith("mission_"):
            recent.append(
                {
                    "event": event_name,
                    "job": job,
                    "mission": mission,
                    "step": event.get("step", ""),
                }
            )

    return list(active.values())[:8], list(reversed(recent[-12:]))


def _build_mission_payload(
    mission_id: str,
    title: str,
    goal: str,
    notes: str,
    steps_json: str,
) -> MissionTemplate:
    validated_mission_id = _validate_mission_id(mission_id)
    payload: dict[str, Any] = {
        "id": validated_mission_id,
        "title": title.strip() or validated_mission_id,
        "goal": goal.strip() or "User-defined mission",
    }
    if notes.strip():
        payload["notes"] = notes.strip()

    try:
        steps_raw = json.loads(steps_json)
    except json.JSONDecodeError as exc:
        raise ValueError("steps_json_invalid") from exc
    if not isinstance(steps_raw, list):
        raise ValueError("steps_json_not_list")
    payload["steps"] = steps_raw

    missions_dir = _missions_dir()
    missions_dir.mkdir(parents=True, exist_ok=True)
    candidate = (missions_dir / f"{payload['id']}.json").resolve()
    missions_root = missions_dir.resolve()
    if not str(candidate).startswith(f"{missions_root}{os.sep}"):
        raise ValueError("mission_id_invalid")
    candidate.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        validated = _parse_mission_file(candidate, payload["id"])
    except Exception as exc:
        candidate.unlink(missing_ok=True)
        raise ValueError("mission_schema_invalid") from exc
    return validated


def _render_assistant_page(
    request: Request,
    *,
    question: str = "",
    error: str = "",
    context: dict[str, Any] | None = None,
    request_result: dict[str, Any] | None = None,
    thread_id: str = "",
    thread_turns: list[dict[str, Any]] | None = None,
) -> HTMLResponse:
    tmpl = templates.get_template("assistant.html")
    csrf_token = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(24)
    result = request_result or {}
    status = str(result.get("status") or "")
    html = tmpl.render(
        question=question,
        error=error,
        context=context or {},
        request_result=result,
        thread_id=thread_id,
        thread_turns=thread_turns or [],
        should_poll=status in {"queued", "thinking", "thinking through Voxera"},
        csrf_token=csrf_token,
        example_prompts=[
            "What is happening right now?",
            "From inside Voxera, how does the system look?",
            "Why would a job require approval?",
            "What do you suggest I check next?",
        ],
    )
    response = HTMLResponse(content=html)
    response.set_cookie(CSRF_COOKIE, csrf_token, httponly=False, samesite="strict")
    return response


@app.get("/assistant", response_class=HTMLResponse)
def assistant_page(
    request: Request,
    request_id: str = "",
    question: str = "",
    thread_id: str = "",
):
    _require_operator_auth_from_request(request)
    queue_root = _queue_root()
    context = build_operator_assistant_context(queue_root)
    request_result = read_assistant_result(queue_root, request_id) if request_id else {}
    active_thread_id = thread_id or str(request_result.get("thread_id") or "")
    thread_turns = (
        read_assistant_thread_turns(queue_root, active_thread_id) if active_thread_id else []
    )

    degraded_reason = _assistant_stalled_degraded_reason(
        context,
        request_result,
        now_ms=int(time.time() * 1000),
    )
    if degraded_reason and request_id:
        resolved_question = question.strip() or "What is happening right now?"
        degraded_data = _generate_degraded_assistant_answer(
            resolved_question,
            context,
            thread_turns=thread_turns,
            degraded_reason=degraded_reason,
        )
        degraded_answer = str(degraded_data.get("answer") or "")
        now_ms = int(time.time() * 1000)
        if active_thread_id and not any(
            str(turn.get("request_id") or "") == request_id
            and str(turn.get("role") or "").lower() == "assistant"
            for turn in thread_turns
            if isinstance(turn, dict)
        ):
            append_thread_turn(
                queue_root,
                thread_id=active_thread_id,
                role="assistant",
                text=degraded_answer,
                request_id=request_id,
                ts_ms=now_ms,
            )
            thread_turns = read_assistant_thread_turns(queue_root, active_thread_id)

        _persist_degraded_assistant_result(
            queue_root,
            request_id=request_id,
            thread_id=active_thread_id,
            question=resolved_question,
            degraded_answer=degraded_data,
            degraded_reason=degraded_reason,
            context=context,
            ts_ms=now_ms,
        )
        request_result = read_assistant_result(queue_root, request_id)
        request_result["status"] = "answered"

    return _render_assistant_page(
        request,
        question=question,
        context=context,
        request_result=request_result,
        thread_id=active_thread_id,
        thread_turns=thread_turns,
    )


@app.post("/assistant/ask", response_class=HTMLResponse)
async def assistant_ask(request: Request):
    await _require_mutation_guard(request)
    question = (await _request_value(request, "question", "")).strip()
    thread_id = (await _request_value(request, "thread_id", "")).strip()
    if not question:
        context = build_operator_assistant_context(_queue_root())
        return _render_assistant_page(
            request,
            question=question,
            error="Question is required.",
            context=context,
            request_result={},
            thread_id=thread_id,
            thread_turns=read_assistant_thread_turns(_queue_root(), thread_id),
        )

    try:
        request_id, thread_id = enqueue_assistant_question(
            _queue_root(), question, thread_id=thread_id
        )
    except OSError:
        queue_root = _queue_root()
        normalized_thread = normalize_thread_id(thread_id) if thread_id else new_thread_id()
        context = build_operator_assistant_context(queue_root)
        degraded_data = await _generate_degraded_assistant_answer_async(
            question,
            context,
            thread_turns=read_assistant_thread_turns(queue_root, normalized_thread),
            degraded_reason="queue_unavailable",
        )
        degraded_answer = str(degraded_data.get("answer") or "")
        ts_ms = int(time.time() * 1000)
        append_thread_turn(
            queue_root,
            thread_id=normalized_thread,
            role="user",
            text=question,
            request_id=f"degraded-{ts_ms}",
            ts_ms=ts_ms,
        )
        append_thread_turn(
            queue_root,
            thread_id=normalized_thread,
            role="assistant",
            text=degraded_answer,
            request_id=f"degraded-{ts_ms}",
            ts_ms=ts_ms,
        )
        return _render_assistant_page(
            request,
            question=question,
            context=context,
            request_result={
                "request_id": f"degraded-{ts_ms}.json",
                "status": "answered",
                "lifecycle_state": "degraded",
                "answer": degraded_answer,
                "advisory_mode": "degraded_brain_only",
                "degraded_reason": "queue_unavailable",
                "fallback_used": bool(degraded_data.get("fallback_used")),
                "fallback_reason": degraded_data.get("fallback_reason"),
                "provider": degraded_data.get("provider"),
                "model": degraded_data.get("model"),
                "thread_id": normalized_thread,
            },
            thread_id=normalized_thread,
            thread_turns=read_assistant_thread_turns(queue_root, normalized_thread),
        )

    query = urlencode({"request_id": request_id, "thread_id": thread_id, "question": question})
    return RedirectResponse(url=f"/assistant?{query}", status_code=303)


def _create_mission_template_from_values(
    mission_id: str, title: str, goal: str, notes: str, steps_json: str
) -> RedirectResponse:
    normalized_id = mission_id.strip()
    if not normalized_id:
        return RedirectResponse(url="/?error=mission_id_required", status_code=303)

    try:
        _build_mission_payload(normalized_id, title, goal, notes, steps_json)
    except ValueError as exc:
        code = str(exc)
        return RedirectResponse(url=f"/?error={code}", status_code=303)

    return RedirectResponse(url=f"/?mission_created={normalized_id}", status_code=303)


@app.get("/missions/templates/create")
def create_mission_template_get(
    request: Request,
    mission_id: str = "",
    title: str = "",
    goal: str = "",
    notes: str = "",
    steps_json: str = "[]",
):
    _enforce_get_mutations_enabled()
    _require_operator_auth_from_request(request)
    return _create_mission_template_from_values(mission_id, title, goal, notes, steps_json)


@app.post("/missions/templates/create")
async def create_mission_template(request: Request):
    await _require_mutation_guard(request)
    mission_id = await _request_value(request, "mission_id", "")
    title = await _request_value(request, "title", "")
    goal = await _request_value(request, "goal", "")
    notes = await _request_value(request, "notes", "")
    steps_json = await _request_value(request, "steps_json", "[]")
    return _create_mission_template_from_values(mission_id, title, goal, notes, steps_json)


def _create_panel_mission_from_values(prompt: str, approval_required: bool) -> RedirectResponse:
    normalized_prompt = prompt.strip()
    if not normalized_prompt:
        return RedirectResponse(url="/?error=panel_prompt_required", status_code=303)
    created, mission_id = _write_panel_mission_job(
        prompt=normalized_prompt,
        approval_required=approval_required,
    )
    return RedirectResponse(
        url=f"/?created={created}&mission_created={mission_id}",
        status_code=303,
    )


@app.get("/missions/create")
def create_mission_get(
    request: Request,
    prompt: str = "",
    approval_required: str = "1",
):
    _enforce_get_mutations_enabled()
    _require_operator_auth_from_request(request)
    return _create_panel_mission_from_values(prompt, approval_required != "0")


@app.post("/missions/create")
async def create_mission(request: Request):
    await _require_mutation_guard(request)
    prompt = (await _request_value(request, "prompt", "")).strip() or (
        await _request_value(request, "goal", "")
    ).strip()
    approval_raw = await _request_value(request, "approval_required", "1")
    return _create_panel_mission_from_values(prompt, approval_raw not in {"0", "false", "off"})


@app.get("/jobs/{job_id}/bundle")
def job_bundle(job_id: str, request: Request):
    _require_operator_auth_from_request(request)
    queue_root = _queue_root()
    stem = Path(job_id).stem
    archive_dir = _incident_archive_dir(queue_root, stem or "job")
    started = time.perf_counter()
    log(
        {
            "event": "bundle_build_started",
            "bundle": "job",
            "job_ref": job_id,
            "archive_dir": str(archive_dir),
        }
    )
    try:
        out = build_job_bundle(queue_root, job_id, archive_dir=archive_dir)
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        log(
            {
                "event": "bundle_build_failed",
                "bundle": "job",
                "job_ref": job_id,
                "duration_ms": duration_ms,
                "error": type(exc).__name__,
            }
        )
        if isinstance(exc, BundleError):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise
    duration_ms = int((time.perf_counter() - started) * 1000)
    size_bytes = out.stat().st_size
    log(
        {
            "event": "bundle_build_ok",
            "bundle": "job",
            "job_ref": job_id,
            "duration_ms": duration_ms,
            "bytes": size_bytes,
            "path": str(out),
        }
    )
    return FileResponse(
        path=out,
        media_type="application/zip",
        filename=out.name,
    )


@app.get("/bundle/system")
def system_bundle(request: Request):
    _require_operator_auth_from_request(request)
    queue_root = _queue_root()
    archive_dir = _incident_archive_dir(queue_root, "system")
    started = time.perf_counter()
    log({"event": "bundle_build_started", "bundle": "system", "archive_dir": str(archive_dir)})
    out = build_system_bundle(queue_root, archive_dir=archive_dir)
    duration_ms = int((time.perf_counter() - started) * 1000)
    size_bytes = out.stat().st_size
    log(
        {
            "event": "bundle_build_ok",
            "bundle": "system",
            "duration_ms": duration_ms,
            "bytes": size_bytes,
            "path": str(out),
        }
    )
    return FileResponse(
        path=out,
        media_type="application/zip",
        filename=out.name,
    )


@app.get("/recovery", response_class=HTMLResponse)
def recovery_page(request: Request):
    recovery_sessions = _collect_bucket_items("recovery")
    quarantine_sessions = _collect_bucket_items("quarantine")
    tmpl = templates.get_template("recovery.html")
    html = tmpl.render(
        recovery_sessions=recovery_sessions,
        quarantine_sessions=quarantine_sessions,
    )
    return HTMLResponse(content=html)


@app.get("/recovery/download/{bucket}/{name}")
def recovery_download(bucket: str, name: str, request: Request):
    _require_operator_auth_from_request(request)
    if "/" in name or "\\" in name or not name or Path(name).name != name:
        raise HTTPException(status_code=404, detail="Not found")

    base = _bucket_base_dir(bucket).resolve()
    target = (base / name).resolve()
    if not _is_within_path(target, base) or not target.exists() or target.is_symlink():
        raise HTTPException(status_code=404, detail="Not found")
    if not target.is_file() and not target.is_dir():
        raise HTTPException(status_code=404, detail="Not found")

    fd, temp_name = tempfile.mkstemp(prefix="voxera-recovery-", suffix=".zip")
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        _build_recovery_zip(target, temp_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    def _zip_file_iterator(path: Path):
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(64 * 1024)
                if not chunk:
                    break
                yield chunk

    filename = f"{bucket}-{name}.zip"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(
        _zip_file_iterator(temp_path),
        media_type="application/zip",
        headers=headers,
        background=BackgroundTask(lambda: temp_path.unlink(missing_ok=True)),
    )


@app.get("/hygiene", response_class=HTMLResponse)
def hygiene_page(request: Request, flash: str = ""):
    _require_operator_auth_from_request(request)
    health = read_health_snapshot(_health_queue_root())
    tmpl = templates.get_template("hygiene.html")
    csrf_token = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(24)
    html = tmpl.render(
        last_prune_result=health.get("last_prune_result"),
        last_reconcile_result=health.get("last_reconcile_result"),
        csrf_token=csrf_token,
        flash=FLASH_MESSAGES.get(flash, ""),
        hygiene_prune_url=str(request.url_for("hygiene_prune_dry_run")),
        hygiene_reconcile_url=str(request.url_for("hygiene_reconcile")),
        hygiene_health_reset_url=str(request.url_for("hygiene_health_reset")),
    )
    response = HTMLResponse(content=html)
    response.set_cookie(CSRF_COOKIE, csrf_token, httponly=False, samesite="strict")
    return response


@app.post("/hygiene/prune-dry-run")
async def hygiene_prune_dry_run(request: Request):
    await _require_mutation_guard(request)
    queue_root = _queue_root()
    run = _run_queue_hygiene_command(queue_root, ["queue", "prune", "--json"])
    parsed = run["result"]
    per_bucket = parsed.get("per_bucket") if isinstance(parsed.get("per_bucket"), dict) else {}
    removed_jobs = int(
        sum(int((per_bucket.get(b) or {}).get("pruned", 0) or 0) for b in per_bucket)
    )
    result = {
        "ts_ms": _now_ms(),
        "mode": "dry-run",
        "ok": bool(run["ok"]),
        "removed_jobs": 0,
        "would_remove_jobs": removed_jobs,
        "removed_sidecars": 0,
        "reclaimed_bytes": parsed.get("reclaimed_bytes"),
        "by_bucket": per_bucket,
        "exit_code": run.get("exit_code"),
        "cmd": run.get("cmd"),
        "cwd": run.get("cwd"),
        "stdout_tail": run.get("stdout_tail", ""),
    }
    if run["stderr_tail"]:
        result["stderr_tail"] = run["stderr_tail"]
    if not run["ok"]:
        result["error"] = run["error"]
    _write_hygiene_result(queue_root, "last_prune_result", result)
    return JSONResponse({"ok": bool(run["ok"]), "result": result}, status_code=200)


@app.post("/hygiene/reconcile")
async def hygiene_reconcile(request: Request):
    await _require_mutation_guard(request)
    queue_root = _queue_root()
    run = _run_queue_hygiene_command(queue_root, ["queue", "reconcile", "--json"])
    parsed = run["result"]
    issue_counts = (
        parsed.get("issue_counts") if isinstance(parsed.get("issue_counts"), dict) else {}
    )
    result = {
        "ts_ms": _now_ms(),
        "ok": bool(run["ok"]),
        "issue_counts": issue_counts,
        "exit_code": run.get("exit_code"),
        "cmd": run.get("cmd"),
        "cwd": run.get("cwd"),
        "stdout_tail": run.get("stdout_tail", ""),
    }
    if run["stderr_tail"]:
        result["stderr_tail"] = run["stderr_tail"]
    if not run["ok"]:
        result["error"] = run["error"]
    _write_hygiene_result(queue_root, "last_reconcile_result", result)
    return JSONResponse({"ok": bool(run["ok"]), "result": result}, status_code=200)


def _hygiene_redirect(request: Request, flash: str) -> RedirectResponse:
    url = str(request.url_for("hygiene_page"))
    sep = "&" if "?" in url else "?"
    return RedirectResponse(url=f"{url}{sep}flash={flash}", status_code=303)


@app.post("/hygiene/health-reset")
async def hygiene_health_reset(request: Request):
    await _require_mutation_guard(request)
    scope = (await _request_value(request, "scope", "current_and_recent")).strip()
    counter_group_raw = (await _request_value(request, "counter_group", "")).strip()
    counter_group = counter_group_raw or None
    try:
        summary = reset_health_snapshot(
            _health_queue_root(),
            scope=scope,
            counter_group=counter_group,
            actor_surface="panel",
        )
    except HealthResetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    event_name = (
        "health_reset_historical_counters"
        if counter_group
        else EVENT_BY_SCOPE.get(scope, "health_reset")
    )
    log(
        {
            "event": event_name,
            "scope": scope,
            "counter_group": counter_group,
            "actor_surface": "panel",
            "fields_changed": summary["changed_fields"],
            "timestamp_ms": summary["timestamp_ms"],
        }
    )
    flash = "health_reset_historical_counters" if counter_group else event_name
    return _hygiene_redirect(request, flash)


def _safe_jobs_n(raw: str) -> int:
    try:
        return max(1, min(int(raw), 200))
    except ValueError:
        return 80


async def _jobs_redirect_local(request: Request, flash: str) -> RedirectResponse:
    params: dict[str, str | int] = {"flash": flash}
    bucket = (await _request_value(request, "bucket", "")).strip()
    if bucket:
        params["bucket"] = bucket
    query = (await _request_value(request, "q", "")).strip()
    if query:
        params["q"] = query
    n_raw = (await _request_value(request, "n", "80")).strip()
    params["n"] = _safe_jobs_n(n_raw)

    url = str(request.url_for("jobs_page"))
    return RedirectResponse(url=f"{url}?{urlencode(params)}", status_code=303)


@app.post("/queue/jobs/{ref}/delete")
async def delete_queue_job(ref: str, request: Request):
    await _require_mutation_guard(request)
    confirm = await _request_value(request, "confirm", "")
    daemon = MissionQueueDaemon(queue_root=_queue_root())
    try:
        daemon.delete_terminal_job(ref, confirm=confirm)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return await _jobs_redirect_local(request, "deleted")


register_home_routes(
    app,
    templates=templates,
    csrf_cookie=CSRF_COOKIE,
    approvals=APPROVALS,
    error_messages=ERROR_MESSAGES,
    allow_get_mutations=_allow_get_mutations,
    queue_root=_queue_root,
    build_activity=_build_activity,
    daemon_health_view=_daemon_health_view,
    performance_stats_view=_performance_stats_view,
    panel_security_snapshot=_panel_security_snapshot,
    auth_setup_banner=_auth_setup_banner,
    enforce_get_mutations_enabled=_enforce_get_mutations_enabled,
    require_operator_auth_from_request=_require_operator_auth_from_request,
    require_mutation_guard=_require_mutation_guard,
    write_queue_job=_write_queue_job,
)

register_job_routes(
    app,
    templates=templates,
    csrf_cookie=CSRF_COOKIE,
    flash_messages=FLASH_MESSAGES,
    queue_root=_queue_root,
    require_mutation_guard=_require_mutation_guard,
    panel_security_counter_incr=_panel_security_counter_incr,
    job_ref_bucket=_job_ref_bucket,
    job_artifact_flags=_job_artifact_flags,
    last_activity=_last_activity,
    job_detail_payload=_job_detail_payload,
    auth_setup_banner=_auth_setup_banner,
)


@app.post("/queue/pause")
async def pause_queue(request: Request):
    await _require_mutation_guard(request)
    daemon = MissionQueueDaemon(queue_root=_queue_root())
    daemon.pause()
    return RedirectResponse(url="/", status_code=303)


@app.post("/queue/resume")
async def resume_queue(request: Request):
    await _require_mutation_guard(request)
    daemon = MissionQueueDaemon(queue_root=_queue_root())
    daemon.resume()
    return RedirectResponse(url="/", status_code=303)
