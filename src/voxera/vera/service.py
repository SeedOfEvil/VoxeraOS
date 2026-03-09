from __future__ import annotations

import hashlib
import json
import secrets
import time
from pathlib import Path
from typing import Any

from ..brain.fallback import classify_fallback_reason
from ..brain.gemini import GeminiBrain
from ..brain.openai_compat import OpenAICompatBrain
from ..config import load_app_config
from .prompt import VERA_SYSTEM_PROMPT

MAX_SESSION_TURNS = 8
_SESSION_ID_LENGTH = 24


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
    for preserved_key in ("pending_job_preview", "handoff"):
        preserved = previous.get(preserved_key)
        if isinstance(preserved, dict):
            payload[preserved_key] = preserved
    _write_session_payload(queue_root, session_id, payload)
    return turns


def read_session_preview(queue_root: Path, session_id: str) -> dict[str, Any] | None:
    payload = _read_session_payload(queue_root, session_id)
    preview = payload.get("pending_job_preview")
    return preview if isinstance(preview, dict) else None


def write_session_preview(
    queue_root: Path, session_id: str, preview: dict[str, Any] | None
) -> None:
    payload = _read_session_payload(queue_root, session_id)
    if not payload:
        payload = {"session_id": session_id, "updated_at_ms": int(time.time() * 1000), "turns": []}
    payload["updated_at_ms"] = int(time.time() * 1000)
    if preview is None:
        payload.pop("pending_job_preview", None)
    else:
        payload["pending_job_preview"] = preview
    _write_session_payload(queue_root, session_id, payload)


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
    payload = _read_session_payload(queue_root, session_id)
    if not payload:
        payload = {"session_id": session_id, "updated_at_ms": int(time.time() * 1000), "turns": []}
    payload["updated_at_ms"] = int(time.time() * 1000)
    payload["handoff"] = {
        "attempted": attempted,
        "queue_path": queue_path,
        "status": status,
        "job_id": job_id,
        "error": error,
        "updated_at_ms": int(time.time() * 1000),
    }
    _write_session_payload(queue_root, session_id, payload)


def read_session_handoff_state(queue_root: Path, session_id: str) -> dict[str, Any] | None:
    payload = _read_session_payload(queue_root, session_id)
    handoff = payload.get("handoff")
    return handoff if isinstance(handoff, dict) else None


def clear_session_turns(queue_root: Path, session_id: str) -> None:
    path = _session_path(queue_root, session_id)
    if path.exists():
        path.unlink()


def session_debug_info(
    queue_root: Path, session_id: str, *, mode_status: str
) -> dict[str, str | int | bool | None]:
    path = _session_path(queue_root, session_id)
    turns = read_session_turns(queue_root, session_id)
    preview = read_session_preview(queue_root, session_id)
    handoff = read_session_handoff_state(queue_root, session_id) or {}
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
        "handoff_attempted": handoff.get("attempted") is True,
        "handoff_status": str(handoff.get("status") or "none"),
        "handoff_queue_path": str(handoff.get("queue_path") or str(queue_root)),
        "handoff_job_id": str(handoff.get("job_id") or "") or None,
        "handoff_error": str(handoff.get("error") or "") or None,
    }


def build_vera_messages(*, turns: list[dict[str, str]], user_message: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": VERA_SYSTEM_PROMPT}]
    for turn in turns[-MAX_SESSION_TURNS:]:
        messages.append({"role": turn["role"], "content": turn["text"]})
    messages.append({"role": "user", "content": user_message.strip()})
    return messages


def _create_brain(provider: Any) -> OpenAICompatBrain | GeminiBrain:
    if provider.type == "openai_compat":
        return OpenAICompatBrain(
            model=provider.model,
            base_url=provider.base_url or "https://openrouter.ai/api/v1",
            api_key_ref=provider.api_key_ref,
            extra_headers=provider.extra_headers,
        )
    if provider.type == "gemini":
        return GeminiBrain(model=provider.model, api_key_ref=provider.api_key_ref)
    raise ValueError(f"unsupported provider type: {provider.type}")


async def generate_vera_reply(*, turns: list[dict[str, str]], user_message: str) -> dict[str, str]:
    cfg = load_app_config()
    attempts: list[tuple[str, Any]] = []
    for key in ("primary", "fallback"):
        provider = cfg.brain.get(key) if cfg.brain else None
        if provider is not None:
            attempts.append((key, provider))

    if not attempts:
        return {
            "answer": (
                "I’m in conversation-only mode right now because no model provider is configured. "
                "I can still help draft a VoxeraOS job request preview, but I cannot execute anything here."
            ),
            "status": "degraded_unavailable",
        }

    messages = build_vera_messages(turns=turns, user_message=user_message)
    last_reason = "UNKNOWN"
    for name, provider in attempts:
        try:
            brain = _create_brain(provider)
            response = await brain.generate(messages, tools=[])
            text = str(response.text or "").strip()
            if text:
                return {"answer": text, "status": f"ok:{name}"}
        except Exception as exc:
            last_reason = classify_fallback_reason(exc)
            continue

    return {
        "answer": (
            "I couldn’t reach the current model backend, so I’m staying in safe preview mode. "
            "I can help you shape a VoxeraOS queue job request, but nothing has been executed."
        ),
        "status": f"degraded_error:{last_reason}",
    }
