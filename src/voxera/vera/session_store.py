"""Vera session persistence/state helpers extracted from service.py.

This module owns stable session payload IO and field-level access/update helpers.
All production callers import session helpers directly from this module.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from pathlib import Path
from typing import Any

from .prompt import VERA_SYSTEM_PROMPT
from .saveable_artifacts import build_saveable_assistant_artifact

MAX_SESSION_TURNS = 8
_SESSION_ID_LENGTH = 24
_MAX_LINKED_JOB_TRACK = 64
_MAX_LINKED_COMPLETIONS = 64
_MAX_LINKED_NOTIFICATIONS = 128
_MAX_SAVEABLE_ASSISTANT_ARTIFACTS = 8


def _default_linked_job_registry() -> dict[str, list[Any]]:
    return {"tracked": [], "completions": [], "notification_outbox": []}


def new_session_id() -> str:
    return f"vera-{secrets.token_hex(_SESSION_ID_LENGTH // 2)}"


def _session_path(queue_root: Path, session_id: str) -> Path:
    return queue_root / "artifacts" / "vera_sessions" / f"{Path(session_id).name}.json"


def _read_session_payload(queue_root: Path, session_id: str) -> dict[str, Any]:
    if not session_id:
        return {}
    path = _session_path(queue_root, session_id)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_session_payload(queue_root: Path, session_id: str, payload: dict[str, Any]) -> None:
    path = _session_path(queue_root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _default_session_payload(session_id: str) -> dict[str, Any]:
    return {"session_id": session_id, "updated_at_ms": int(time.time() * 1000), "turns": []}


def _load_or_default_session_payload(queue_root: Path, session_id: str) -> dict[str, Any]:
    payload = _read_session_payload(queue_root, session_id)
    if payload:
        return payload
    return _default_session_payload(session_id)


def read_session_turns(queue_root: Path, session_id: str) -> list[dict[str, str]]:
    payload = _read_session_payload(queue_root, session_id)
    turns = payload.get("turns")
    if not isinstance(turns, list):
        return []
    normalized: list[dict[str, str]] = []
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").strip().lower()
        text = str(turn.get("text") or "").strip()
        if role in {"user", "assistant"} and text:
            normalized_turn: dict[str, str] = {"role": role, "text": text}
            input_origin = str(turn.get("input_origin") or "").strip().lower()
            if role == "user" and input_origin in {"typed", "voice_transcript"}:
                normalized_turn["input_origin"] = input_origin
            normalized.append(normalized_turn)
    return normalized[-MAX_SESSION_TURNS:]


def read_session_updated_at_ms(queue_root: Path, session_id: str) -> int:
    payload = _read_session_payload(queue_root, session_id)
    raw = payload.get("updated_at_ms")
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return max(0, raw)
    if isinstance(raw, str):
        try:
            return max(0, int(raw))
        except ValueError:
            return 0
    return 0


def append_session_turn(
    queue_root: Path,
    session_id: str,
    *,
    role: str,
    text: str,
    input_origin: str | None = None,
) -> list[dict[str, str]]:
    turns = read_session_turns(queue_root, session_id)
    normalized_role = role.strip().lower()
    normalized_origin = str(input_origin or "").strip().lower()
    turn_payload: dict[str, str] = {"role": normalized_role, "text": text.strip()}
    if normalized_role == "user" and normalized_origin in {"typed", "voice_transcript"}:
        turn_payload["input_origin"] = normalized_origin
    turns.append(turn_payload)
    turns = turns[-MAX_SESSION_TURNS:]
    previous = _read_session_payload(queue_root, session_id)
    payload: dict[str, Any] = {
        "session_id": session_id,
        "updated_at_ms": int(time.time() * 1000),
        "turns": turns,
    }
    for preserved_key in (
        "pending_job_preview",
        "handoff",
        "last_enrichment",
        "last_investigation",
        "last_derived_investigation_output",
        "weather_context",
        "recent_saveable_assistant_artifacts",
        "linked_queue_jobs",
        "conversational_planning_active",
        "last_user_input_origin",
        _SHARED_CONTEXT_FIELD,
        _ROUTING_DEBUG_FIELD,
    ):
        preserved = previous.get(preserved_key)
        if (
            isinstance(preserved, dict)
            or (
                preserved_key == "recent_saveable_assistant_artifacts"
                and isinstance(preserved, list)
            )
            or (preserved_key == "conversational_planning_active" and isinstance(preserved, bool))
        ):
            payload[preserved_key] = preserved
    if role.strip().lower() == "assistant":
        artifact = build_saveable_assistant_artifact(text)
        if artifact is not None:
            current_artifacts = read_session_saveable_assistant_artifacts(queue_root, session_id)
            deduped = [
                item
                for item in current_artifacts
                if item.get("content") != artifact["content"]
                or item.get("artifact_type") != artifact["artifact_type"]
            ]
            deduped.append(artifact)
            payload["recent_saveable_assistant_artifacts"] = deduped[
                -_MAX_SAVEABLE_ASSISTANT_ARTIFACTS:
            ]
    if normalized_role == "user" and normalized_origin in {"typed", "voice_transcript"}:
        payload["last_user_input_origin"] = {"value": normalized_origin}
    _write_session_payload(queue_root, session_id, payload)
    return turns


def read_session_last_user_input_origin(queue_root: Path, session_id: str) -> str:
    payload = _read_session_payload(queue_root, session_id)
    field = payload.get("last_user_input_origin")
    if not isinstance(field, dict):
        return "typed"
    value = str(field.get("value") or "").strip().lower()
    return value if value in {"typed", "voice_transcript"} else "typed"


def _write_session_field(
    queue_root: Path,
    session_id: str,
    *,
    field_name: str,
    value: dict[str, Any] | None,
) -> None:
    payload = _load_or_default_session_payload(queue_root, session_id)
    payload["updated_at_ms"] = int(time.time() * 1000)
    if value is None:
        payload.pop(field_name, None)
    else:
        payload[field_name] = value
    _write_session_payload(queue_root, session_id, payload)


def read_session_preview(queue_root: Path, session_id: str) -> dict[str, Any] | None:
    payload = _read_session_payload(queue_root, session_id)
    preview = payload.get("pending_job_preview")
    return preview if isinstance(preview, dict) else None


def write_session_preview(
    queue_root: Path, session_id: str, preview: dict[str, Any] | None
) -> None:
    _write_session_field(
        queue_root,
        session_id,
        field_name="pending_job_preview",
        value=preview,
    )


def write_session_handoff_state(
    queue_root: Path,
    session_id: str,
    *,
    attempted: bool,
    queue_path: str,
    status: str,
    job_id: str | None = None,
    error: str | None = None,
) -> None:
    _write_session_field(
        queue_root,
        session_id,
        field_name="handoff",
        value={
            "attempted": attempted,
            "queue_path": queue_path,
            "status": status,
            "job_id": job_id,
            "error": error,
            "updated_at_ms": int(time.time() * 1000),
        },
    )


def read_session_handoff_state(queue_root: Path, session_id: str) -> dict[str, Any] | None:
    payload = _read_session_payload(queue_root, session_id)
    handoff = payload.get("handoff")
    return handoff if isinstance(handoff, dict) else None


def clear_session_turns(queue_root: Path, session_id: str) -> None:
    path = _session_path(queue_root, session_id)
    if path.exists():
        path.unlink()


def read_session_enrichment(queue_root: Path, session_id: str) -> dict[str, Any] | None:
    """Return the most recent read-only enrichment stored for this session, or None."""
    payload = _read_session_payload(queue_root, session_id)
    enrichment = payload.get("last_enrichment")
    return enrichment if isinstance(enrichment, dict) else None


def write_session_enrichment(
    queue_root: Path, session_id: str, enrichment: dict[str, Any] | None
) -> None:
    """Persist read-only enrichment state into the session for preview-authoring use."""
    _write_session_field(queue_root, session_id, field_name="last_enrichment", value=enrichment)


def read_session_investigation(queue_root: Path, session_id: str) -> dict[str, Any] | None:
    payload = _read_session_payload(queue_root, session_id)
    investigation = payload.get("last_investigation")
    return investigation if isinstance(investigation, dict) else None


def read_session_weather_context(queue_root: Path, session_id: str) -> dict[str, Any] | None:
    payload = _read_session_payload(queue_root, session_id)
    weather_context = payload.get("weather_context")
    return weather_context if isinstance(weather_context, dict) else None


def write_session_weather_context(
    queue_root: Path, session_id: str, weather_context: dict[str, Any] | None
) -> None:
    _write_session_field(
        queue_root,
        session_id,
        field_name="weather_context",
        value=weather_context,
    )


def write_session_investigation(
    queue_root: Path, session_id: str, investigation: dict[str, Any] | None
) -> None:
    _write_session_field(
        queue_root,
        session_id,
        field_name="last_investigation",
        value=investigation,
    )


def read_session_derived_investigation_output(
    queue_root: Path, session_id: str
) -> dict[str, Any] | None:
    payload = _read_session_payload(queue_root, session_id)
    derived = payload.get("last_derived_investigation_output")
    return derived if isinstance(derived, dict) else None


def write_session_derived_investigation_output(
    queue_root: Path, session_id: str, derived_output: dict[str, Any] | None
) -> None:
    _write_session_field(
        queue_root,
        session_id,
        field_name="last_derived_investigation_output",
        value=derived_output,
    )


def read_session_saveable_assistant_artifacts(
    queue_root: Path, session_id: str
) -> list[dict[str, str]]:
    payload = _read_session_payload(queue_root, session_id)
    raw_artifacts = payload.get("recent_saveable_assistant_artifacts")
    if not isinstance(raw_artifacts, list):
        return []
    artifacts: list[dict[str, str]] = []
    for item in raw_artifacts:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        artifact_type = str(item.get("artifact_type") or "").strip()
        if not content or not artifact_type:
            continue
        artifacts.append({"content": content, "artifact_type": artifact_type})
    return artifacts[-_MAX_SAVEABLE_ASSISTANT_ARTIFACTS:]


def _write_session_saveable_assistant_artifacts(
    queue_root: Path, session_id: str, artifacts: list[dict[str, str]]
) -> None:
    payload = _load_or_default_session_payload(queue_root, session_id)
    payload["updated_at_ms"] = int(time.time() * 1000)
    if artifacts:
        payload["recent_saveable_assistant_artifacts"] = artifacts[
            -_MAX_SAVEABLE_ASSISTANT_ARTIFACTS:
        ]
    else:
        payload.pop("recent_saveable_assistant_artifacts", None)
    _write_session_payload(queue_root, session_id, payload)


def session_debug_info(
    queue_root: Path, session_id: str, *, mode_status: str
) -> dict[str, str | int | bool | None]:
    path = _session_path(queue_root, session_id)
    turns = read_session_turns(queue_root, session_id)
    preview = read_session_preview(queue_root, session_id)
    enrichment = read_session_enrichment(queue_root, session_id)
    investigation = read_session_investigation(queue_root, session_id)
    weather_context = read_session_weather_context(queue_root, session_id)
    derived_output = read_session_derived_investigation_output(queue_root, session_id)
    saveable_artifacts = read_session_saveable_assistant_artifacts(queue_root, session_id)
    handoff = read_session_handoff_state(queue_root, session_id) or {}
    registry = _read_linked_job_registry(queue_root, session_id)
    last_input_origin = read_session_last_user_input_origin(queue_root, session_id)
    return {
        "dev_mode": True,
        "mode_status": mode_status,
        "session_id": session_id,
        "session_file": str(path),
        "session_file_exists": path.exists(),
        "turn_count": len(turns),
        "max_session_turns": MAX_SESSION_TURNS,
        "system_prompt_sha256": hashlib.sha256(VERA_SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
        "preview_available": isinstance(preview, dict),
        "enrichment_available": isinstance(enrichment, dict),
        "investigation_available": isinstance(investigation, dict),
        "weather_context_available": isinstance(weather_context, dict),
        "investigation_result_count": len(investigation.get("results", []))
        if isinstance(investigation, dict)
        else 0,
        "derived_investigation_output_available": isinstance(derived_output, dict),
        "saveable_assistant_artifact_count": len(saveable_artifacts),
        "handoff_attempted": handoff.get("attempted") is True,
        "handoff_status": str(handoff.get("status") or "none"),
        "handoff_queue_path": str(handoff.get("queue_path") or str(queue_root)),
        "handoff_job_id": str(handoff.get("job_id") or "") or None,
        "handoff_error": str(handoff.get("error") or "") or None,
        "linked_jobs": len(registry.get("tracked", [])),
        "linked_completions": len(registry.get("completions", [])),
        "last_user_input_origin": last_input_origin,
        "shared_context_available": isinstance(
            _read_session_payload(queue_root, session_id).get(_SHARED_CONTEXT_FIELD), dict
        ),
    }


def _read_linked_job_registry(queue_root: Path, session_id: str) -> dict[str, Any]:
    payload = _read_session_payload(queue_root, session_id)
    raw = payload.get("linked_queue_jobs")
    return raw if isinstance(raw, dict) else _default_linked_job_registry()


def _write_linked_job_registry(queue_root: Path, session_id: str, registry: dict[str, Any]) -> None:
    payload = _load_or_default_session_payload(queue_root, session_id)
    payload["updated_at_ms"] = int(time.time() * 1000)
    payload["linked_queue_jobs"] = registry
    _write_session_payload(queue_root, session_id, payload)


def register_session_linked_job(queue_root: Path, session_id: str, *, job_ref: str) -> None:
    normalized_job_ref = Path(job_ref).name.strip()
    if not normalized_job_ref:
        return
    registry = _read_linked_job_registry(queue_root, session_id)
    tracked_raw = registry.get("tracked")
    tracked = tracked_raw if isinstance(tracked_raw, list) else []
    now_ms = int(time.time() * 1000)

    existing: dict[str, Any] | None = None
    for item in tracked:
        if not isinstance(item, dict):
            continue
        if str(item.get("job_ref") or "") == normalized_job_ref:
            existing = item
            break
    if existing is None:
        tracked.append(
            {
                "job_ref": normalized_job_ref,
                "linked_session_id": session_id,
                "linked_thread_id": session_id,
                "linked_at_ms": now_ms,
                "completion_ingested": False,
            }
        )
    else:
        existing["linked_session_id"] = session_id
        existing["linked_thread_id"] = session_id
        existing["updated_at_ms"] = now_ms

    tracked = tracked[-_MAX_LINKED_JOB_TRACK:]
    registry["tracked"] = tracked
    completions_raw = registry.get("completions")
    if not isinstance(completions_raw, list):
        registry["completions"] = []
    outbox_raw = registry.get("notification_outbox")
    if not isinstance(outbox_raw, list):
        registry["notification_outbox"] = []
    _write_linked_job_registry(queue_root, session_id, registry)


def read_linked_job_completions(queue_root: Path, session_id: str) -> list[dict[str, Any]]:
    registry = _read_linked_job_registry(queue_root, session_id)
    completions = registry.get("completions")
    if not isinstance(completions, list):
        return []
    return [item for item in completions if isinstance(item, dict)]


def read_session_conversational_planning_active(queue_root: Path, session_id: str) -> bool:
    """Return True if the previous turn was a conversational answer-first planning turn."""
    payload = _read_session_payload(queue_root, session_id)
    return payload.get("conversational_planning_active") is True


def write_session_conversational_planning_active(
    queue_root: Path, session_id: str, active: bool
) -> None:
    """Persist whether the most recent turn was conversational answer-first."""
    payload = _load_or_default_session_payload(queue_root, session_id)
    payload["updated_at_ms"] = int(time.time() * 1000)
    if active:
        payload["conversational_planning_active"] = True
    else:
        payload.pop("conversational_planning_active", None)
    _write_session_payload(queue_root, session_id, payload)


# ---------------------------------------------------------------------------
# Shared session context — bounded workflow-continuity surface
# ---------------------------------------------------------------------------
#
# The shared session context tracks "what Vera currently believes is in play"
# inside a session.  It is a **continuity aid**, NOT a trust-surface replacement.
#
# Precedence rules (enforced by consumers, documented here for clarity):
#   1. Preview truth (pending_job_preview) is authoritative for pre-submit state.
#   2. Queue truth (queue bucket + sidecar) is authoritative for submitted work.
#   3. Artifact/evidence truth is authoritative for completed outcomes.
#   4. Shared session context helps Vera remember workflow state across turns
#      but MUST NEVER override (1), (2), or (3).
#   5. If context conflicts with canonical truth, canonical truth wins.
#   6. If continuity is ambiguous, fail closed (do not guess).

_SHARED_CONTEXT_FIELD = "shared_context"
_ROUTING_DEBUG_FIELD = "routing_debug"
_MAX_AMBIGUITY_FLAGS = 8
_MAX_ROUTING_DEBUG_HISTORY = 8


def _empty_shared_context() -> dict[str, Any]:
    """Return the canonical empty shared session context."""
    return {
        "active_draft_ref": None,
        "active_preview_ref": None,
        "last_submitted_job_ref": None,
        "last_completed_job_ref": None,
        "last_reviewed_job_ref": None,
        "last_saved_file_ref": None,
        "active_topic": None,
        "ambiguity_flags": [],
        "updated_at_ms": 0,
    }


_SHARED_CONTEXT_KEYS = frozenset(_empty_shared_context().keys())


def _normalize_shared_context(raw: Any) -> dict[str, Any]:
    """Validate and normalize a raw shared context dict.

    Unknown keys are dropped.  Missing keys are filled from defaults.
    Non-string scalar values for ref fields are coerced or cleared.
    """
    if not isinstance(raw, dict):
        return _empty_shared_context()
    ctx = _empty_shared_context()
    _ref_keys = (
        "active_draft_ref",
        "active_preview_ref",
        "last_submitted_job_ref",
        "last_completed_job_ref",
        "last_reviewed_job_ref",
        "last_saved_file_ref",
        "active_topic",
    )
    for key in _ref_keys:
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            ctx[key] = val.strip()
        else:
            ctx[key] = None
    raw_flags = raw.get("ambiguity_flags")
    if isinstance(raw_flags, list):
        ctx["ambiguity_flags"] = [
            str(f).strip() for f in raw_flags[:_MAX_AMBIGUITY_FLAGS] if str(f).strip()
        ]
    else:
        ctx["ambiguity_flags"] = []
    raw_ts = raw.get("updated_at_ms")
    if isinstance(raw_ts, int) and raw_ts > 0:
        ctx["updated_at_ms"] = raw_ts
    else:
        ctx["updated_at_ms"] = 0
    return ctx


def read_session_context(queue_root: Path, session_id: str) -> dict[str, Any]:
    """Read the shared session context, returning a normalized copy."""
    payload = _read_session_payload(queue_root, session_id)
    raw = payload.get(_SHARED_CONTEXT_FIELD)
    return _normalize_shared_context(raw)


def write_session_context(queue_root: Path, session_id: str, context: dict[str, Any]) -> None:
    """Persist a full shared session context (normalized before write)."""
    normalized = _normalize_shared_context(context)
    normalized["updated_at_ms"] = int(time.time() * 1000)
    _write_session_field(queue_root, session_id, field_name=_SHARED_CONTEXT_FIELD, value=normalized)


def update_session_context(
    queue_root: Path,
    session_id: str,
    **updates: Any,
) -> dict[str, Any]:
    """Merge *updates* into the existing shared session context and persist.

    Only keys present in the canonical schema are accepted; unknown keys are
    silently ignored.  Returns the resulting normalized context.
    """
    ctx = read_session_context(queue_root, session_id)
    for key, value in updates.items():
        if key not in _SHARED_CONTEXT_KEYS:
            continue
        if key == "updated_at_ms":
            continue  # managed internally
        ctx[key] = value
    normalized = _normalize_shared_context(ctx)
    normalized["updated_at_ms"] = int(time.time() * 1000)
    _write_session_field(queue_root, session_id, field_name=_SHARED_CONTEXT_FIELD, value=normalized)
    return normalized


def clear_session_context(queue_root: Path, session_id: str) -> None:
    """Reset the shared session context to the empty default."""
    _write_session_field(queue_root, session_id, field_name=_SHARED_CONTEXT_FIELD, value=None)


# ---------------------------------------------------------------------------
# Routing debug — bounded operator-facing route/dispatch trace
# ---------------------------------------------------------------------------
#
# Records the last N routing decisions made during Vera chat turns.
# This is a **debug aid only** — it reveals which dispatch path fired and
# why, so operators can answer "why did this routing path fire?" without
# digging into logs.
#
# The routing debug surface must never alter truth boundaries, change
# dispatch behavior, or expose raw internal payloads.


def _empty_routing_debug() -> dict[str, Any]:
    """Return the canonical empty routing debug state."""
    return {"entries": [], "updated_at_ms": 0}


def read_session_routing_debug(queue_root: Path, session_id: str) -> dict[str, Any]:
    """Read the routing debug state for operator inspection."""
    payload = _read_session_payload(queue_root, session_id)
    raw = payload.get(_ROUTING_DEBUG_FIELD)
    if not isinstance(raw, dict):
        return _empty_routing_debug()
    entries = raw.get("entries")
    if not isinstance(entries, list):
        return _empty_routing_debug()
    # Normalize entries — keep only well-formed dicts with required keys.
    normalized: list[dict[str, Any]] = []
    for entry in entries[-_MAX_ROUTING_DEBUG_HISTORY:]:
        if not isinstance(entry, dict):
            continue
        route_status = str(entry.get("route_status") or "").strip()
        if not route_status:
            continue
        normalized.append(
            {
                "route_status": route_status,
                "dispatch_source": str(entry.get("dispatch_source") or "").strip() or "unknown",
                "matched_early_exit": entry.get("matched_early_exit") is True,
                "turn_index": entry.get("turn_index")
                if isinstance(entry.get("turn_index"), int)
                else None,
                "timestamp_ms": entry.get("timestamp_ms")
                if isinstance(entry.get("timestamp_ms"), int)
                else 0,
            }
        )
    return {"entries": normalized, "updated_at_ms": raw.get("updated_at_ms", 0)}


def append_routing_debug_entry(
    queue_root: Path,
    session_id: str,
    *,
    route_status: str,
    dispatch_source: str,
    matched_early_exit: bool = False,
    turn_index: int | None = None,
) -> None:
    """Append a routing debug entry for the current turn.

    Bounded to the last ``_MAX_ROUTING_DEBUG_HISTORY`` entries.
    ``turn_index`` is the total turn count after the assistant turn has been
    appended — i.e., the number of turns in the session at the time of the
    routing decision.
    """
    now_ms = int(time.time() * 1000)
    current = read_session_routing_debug(queue_root, session_id)
    entries = current.get("entries", [])
    if not isinstance(entries, list):
        entries = []
    entries.append(
        {
            "route_status": str(route_status).strip(),
            "dispatch_source": str(dispatch_source).strip() or "unknown",
            "matched_early_exit": bool(matched_early_exit),
            "turn_index": turn_index,
            "timestamp_ms": now_ms,
        }
    )
    entries = entries[-_MAX_ROUTING_DEBUG_HISTORY:]
    _write_session_field(
        queue_root,
        session_id,
        field_name=_ROUTING_DEBUG_FIELD,
        value={"entries": entries, "updated_at_ms": now_ms},
    )


def clear_session_routing_debug(queue_root: Path, session_id: str) -> None:
    """Reset the routing debug state."""
    _write_session_field(queue_root, session_id, field_name=_ROUTING_DEBUG_FIELD, value=None)


# ---------------------------------------------------------------------------
# Full operator debug snapshot — combines session debug, shared context,
# and routing debug into a single bounded surface
# ---------------------------------------------------------------------------


def session_debug_snapshot(
    queue_root: Path,
    session_id: str,
    *,
    mode_status: str,
) -> dict[str, Any]:
    """Build a comprehensive operator-facing debug snapshot.

    Combines:
    - session_debug_info (existing): session metadata, preview/handoff state
    - shared session context: active refs for continuity debugging
    - routing debug: recent dispatch decisions

    This is the single entry point for the operator debug surface.
    All values are bounded and safe for operator display.
    """
    base = session_debug_info(queue_root, session_id, mode_status=mode_status)
    ctx = read_session_context(queue_root, session_id)
    routing = read_session_routing_debug(queue_root, session_id)

    return {
        **base,
        # Shared session context — ref values for continuity debugging
        "context_active_draft_ref": ctx.get("active_draft_ref"),
        "context_active_preview_ref": ctx.get("active_preview_ref"),
        "context_last_submitted_job_ref": ctx.get("last_submitted_job_ref"),
        "context_last_completed_job_ref": ctx.get("last_completed_job_ref"),
        "context_last_reviewed_job_ref": ctx.get("last_reviewed_job_ref"),
        "context_last_saved_file_ref": ctx.get("last_saved_file_ref"),
        "context_active_topic": ctx.get("active_topic"),
        "context_ambiguity_flags": ctx.get("ambiguity_flags", []),
        "context_updated_at_ms": ctx.get("updated_at_ms", 0),
        # Routing debug — bounded recent dispatch trace
        "routing_debug_entries": routing.get("entries", []),
        "routing_debug_updated_at_ms": routing.get("updated_at_ms", 0),
        "routing_debug_entry_count": len(routing.get("entries", [])),
    }
