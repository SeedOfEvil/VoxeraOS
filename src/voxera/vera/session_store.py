"""Vera session persistence/state helpers extracted from service.py.

This module owns stable session payload IO and field-level access/update helpers while
`service.py` keeps compatibility aliases for existing call sites.
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
            normalized.append({"role": role, "text": text})
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
    queue_root: Path, session_id: str, *, role: str, text: str
) -> list[dict[str, str]]:
    turns = read_session_turns(queue_root, session_id)
    turns.append({"role": role, "text": text.strip()})
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
    _write_session_payload(queue_root, session_id, payload)
    return turns


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
