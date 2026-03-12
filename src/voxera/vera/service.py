from __future__ import annotations

import hashlib
import json
import secrets
import time
from pathlib import Path
from typing import Any

from ..brain.fallback import classify_fallback_reason
from ..brain.gemini import GeminiBrain
from ..brain.json_recovery import recover_json_object
from ..brain.openai_compat import OpenAICompatBrain
from ..config import load_app_config
from .brave_search import BraveSearchClient, WebSearchResult
from .handoff import maybe_draft_job_payload
from .prompt import VERA_PREVIEW_BUILDER_PROMPT, VERA_SYSTEM_PROMPT

MAX_SESSION_TURNS = 8
_SESSION_ID_LENGTH = 24


PREVIEW_BUILDER_MODEL = "gemini-3-flash-preview"
PREVIEW_BUILDER_FALLBACK_MODEL = "gemini-3.1-flash-lite-preview"


def _is_operational_open_request(message: str) -> bool:
    lowered = message.lower().strip()
    if not lowered:
        return False
    action_terms = (
        "open ",
        "launch ",
        "take me to",
        "bring up",
        "navigate to",
        "go to ",
        "visit ",
    )
    return any(term in lowered for term in action_terms)


def _is_operational_side_effect_request(message: str) -> bool:
    lowered = message.lower().strip()
    if not lowered:
        return False
    action_terms = (
        "write",
        "create",
        "make",
        "run",
        "execute",
        "delete",
        "remove",
        "install",
        "uninstall",
        "rename",
        "move",
        "copy",
        "save",
    )
    targets = (
        "file",
        "directory",
        "folder",
        "script",
        "app",
        "application",
        "command",
    )
    return any(term in lowered for term in action_terms) and any(t in lowered for t in targets)


def _is_explicit_internal_search_request(message: str) -> bool:
    lowered = message.lower().strip()
    if not lowered:
        return False
    patterns = (
        "use your internal internet web search",
        "use your internal web search",
        "use your web search",
        "use your internal search",
        "search the web for me",
        "look this up for me",
        "search this online",
        "look this up online",
        "search online for me",
    )
    return any(pattern in lowered for pattern in patterns)


def _is_informational_web_query(message: str) -> bool:
    lowered = message.lower().strip()
    if not lowered:
        return False
    if _is_operational_open_request(lowered):
        return False
    if _is_explicit_internal_search_request(lowered):
        return True
    if _is_operational_side_effect_request(lowered):
        return False

    informational_terms = (
        "what's on",
        "what is on",
        "look up",
        "look into",
        "search for",
        "search ",
        "find out",
        "find information",
        "give me information",
        "stock information",
        "information about",
        "tell me about",
        "latest",
        "latest news",
        "latest stories",
        "latest updates",
        "recent",
        "news",
        "world news",
        "global news",
        "world wide news",
        "current events",
        "headlines",
        "breaking news",
        "market news",
        "market updates",
        "release notes",
        "summarize",
        "summary",
        "compare",
        "research",
        "explain",
        "what is",
        "what does",
        "what changed",
        "what happened",
        "what's happening",
        "what's new",
        "what's the latest",
        "what are the latest",
        "what's going on",
        "what is going on",
        "docs",
        "documentation",
        "documentation for",
        "earnings",
        "analyst",
        "stock",
        "stocks",
        "price",
        "prices",
        "market",
        "company performance",
        "magnificent seven",
        "big 7",
    )
    question_starters = (
        "what",
        "whats",
        "why",
        "how",
        "when",
        "who",
        "can you find",
        "could you find",
        "tell me",
        "give me",
    )
    web_hints = ("http://", "https://", ".com", ".io", "website", "web")

    contains_info_signal = any(term in lowered for term in informational_terms)
    looks_like_question = lowered.endswith("?") or any(
        lowered.startswith(starter) for starter in question_starters
    )

    return contains_info_signal or (
        looks_like_question and any(hint in lowered for hint in web_hints)
    )


def _format_web_investigation_answer(query: str, results: list[WebSearchResult]) -> str:
    if not results:
        return (
            f"I ran a read-only web investigation for '{query}' but didn't find usable results. "
            "Try refining the query or asking for a narrower topic."
        )

    bullets = []
    for idx, result in enumerate(results[:5], start=1):
        detail = result.description or "No summary provided."
        age = f" ({result.age})" if result.age else ""
        bullets.append(f"{idx}. {result.title}{age}\n   {detail}\n   Source: {result.url}")

    joined = "\n".join(bullets)
    return (
        f"I used Brave Search in read-only mode to investigate: '{query}'. "
        "Here are the top findings:\n"
        f"{joined}\n\n"
        "If you want, I can compare these sources or summarize a specific angle."
    )


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


def _build_preview_builder_messages(
    *,
    turns: list[dict[str, str]],
    user_message: str,
    active_preview: dict[str, Any] | None,
) -> list[dict[str, str]]:
    context_payload = {
        "active_preview": active_preview,
        "latest_user_message": user_message.strip(),
        "recent_turns": turns[-MAX_SESSION_TURNS:],
    }
    return [
        {"role": "system", "content": VERA_PREVIEW_BUILDER_PROMPT},
        {
            "role": "user",
            "content": json.dumps(context_payload, ensure_ascii=False),
        },
    ]


def _extract_preview_builder_payload(text: str) -> dict[str, Any] | None:
    parsed, _ = recover_json_object(text)
    if not isinstance(parsed, dict):
        return None
    preview = parsed.get("preview")
    if preview is None:
        return None
    if isinstance(preview, dict):
        return preview
    return None


async def generate_preview_builder_update(
    *,
    turns: list[dict[str, str]],
    user_message: str,
    active_preview: dict[str, Any] | None,
) -> dict[str, Any] | None:
    cfg = load_app_config()
    api_key_ref = None
    if cfg.brain:
        for key in ("primary", "fallback", "fast", "reasoning"):
            provider = cfg.brain.get(key)
            if provider is not None and provider.api_key_ref:
                api_key_ref = provider.api_key_ref
                break

    attempts: list[GeminiBrain] = []
    if api_key_ref:
        attempts.append(GeminiBrain(model=PREVIEW_BUILDER_MODEL, api_key_ref=api_key_ref))
        if PREVIEW_BUILDER_FALLBACK_MODEL != PREVIEW_BUILDER_MODEL:
            attempts.append(
                GeminiBrain(model=PREVIEW_BUILDER_FALLBACK_MODEL, api_key_ref=api_key_ref)
            )

    recent_user_messages = [
        str(turn.get("text") or "")
        for turn in turns[-MAX_SESSION_TURNS:]
        if str(turn.get("role") or "").strip().lower() == "user"
    ]

    # Deterministic Voxera-aware compiler pass first for supported draftable families,
    # including contextual refinements that depend on recent user turns.
    deterministic_preview = maybe_draft_job_payload(
        user_message,
        active_preview=active_preview,
        recent_user_messages=recent_user_messages,
    )
    if deterministic_preview is not None:
        return deterministic_preview

    if not attempts:
        return None

    messages = _build_preview_builder_messages(
        turns=turns,
        user_message=user_message,
        active_preview=active_preview,
    )

    for brain in attempts:
        try:
            response = await brain.generate(messages, tools=[])
        except Exception:
            continue
        payload = _extract_preview_builder_payload(str(response.text or ""))
        if payload is not None:
            return payload
    return None


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

    web_cfg = cfg.web_investigation
    informational_web = _is_informational_web_query(user_message)
    if informational_web and web_cfg is None:
        return {
            "answer": (
                "Read-only web investigation is not configured yet (Brave API key missing). "
                "I can still help reason from what you provide, but I cannot fetch live web results yet."
            ),
            "status": "web_investigation_unconfigured",
        }

    if informational_web and web_cfg is not None:
        client = BraveSearchClient(
            api_key_ref=web_cfg.api_key_ref,
            env_api_key_var=web_cfg.env_api_key_var,
        )
        try:
            results = await client.search(query=user_message, count=web_cfg.max_results)
        except RuntimeError as exc:
            msg = str(exc)
            if "not configured" in msg:
                return {
                    "answer": (
                        "Brave web investigation is not configured yet (missing API key). "
                        "I can still help reason from what you provide, but I cannot fetch live web results yet."
                    ),
                    "status": "web_investigation_unconfigured",
                }
            return {
                "answer": f"I couldn't complete read-only web investigation: {msg}",
                "status": "web_investigation_error",
            }

        return {
            "answer": _format_web_investigation_answer(user_message, results),
            "status": "ok:web_investigation",
        }

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
