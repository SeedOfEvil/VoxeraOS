from __future__ import annotations

import hashlib
import json
import re
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
from .handoff import drafting_guidance, maybe_draft_job_payload, normalize_preview_payload
from .prompt import VERA_PREVIEW_BUILDER_PROMPT, VERA_SYSTEM_PROMPT

MAX_SESSION_TURNS = 8
_SESSION_ID_LENGTH = 24


PREVIEW_BUILDER_MODEL = "gemini-3-flash-preview"
PREVIEW_BUILDER_FALLBACK_MODEL = "gemini-3.1-flash-lite-preview"


class HiddenCompilerDecision:
    def __init__(
        self,
        *,
        action: str,
        intent_type: str,
        updated_preview: dict[str, Any] | None = None,
        patch: dict[str, Any] | None = None,
    ) -> None:
        self.action = action
        self.intent_type = intent_type
        self.updated_preview = updated_preview
        self.patch = patch

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> HiddenCompilerDecision:
        allowed = {"action", "intent_type", "updated_preview", "patch"}
        if not set(payload).issubset(allowed):
            raise ValueError("hidden compiler decision contains unsupported keys")

        action = str(payload.get("action") or "").strip()
        intent_type = str(payload.get("intent_type") or "").strip()
        updated_preview = payload.get("updated_preview")
        patch = payload.get("patch")

        if action not in {"replace_preview", "patch_preview", "no_change"}:
            raise ValueError("action must be replace_preview, patch_preview, or no_change")
        if intent_type not in {"new_intent", "refinement", "unclear"}:
            raise ValueError("intent_type must be new_intent, refinement, or unclear")

        if action == "replace_preview":
            if not isinstance(updated_preview, dict):
                raise ValueError("replace_preview requires updated_preview object")
            if patch is not None:
                raise ValueError("replace_preview cannot include patch")
        elif action == "patch_preview":
            if not isinstance(patch, dict):
                raise ValueError("patch_preview requires patch object")
            if updated_preview is not None:
                raise ValueError("patch_preview cannot include updated_preview")
        else:
            if updated_preview is not None or patch is not None:
                raise ValueError("no_change cannot include updated_preview or patch")

        return cls(
            action=action,
            intent_type=intent_type,
            updated_preview=updated_preview if isinstance(updated_preview, dict) else None,
            patch=patch if isinstance(patch, dict) else None,
        )


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
        detail = result.description or "No snippet available."
        age = f" ({result.age})" if result.age else ""
        bullets.append(f"{idx}. {result.title}{age}\n   Source: {result.url}\n   Snippet: {detail}")

    joined = "\n".join(bullets)
    return (
        "Here are the top findings I found via read-only Brave web investigation:\n\n"
        f"{joined}\n\n"
        "If you want, I can compare these sources or summarize a specific angle."
    )


def _normalize_web_query(user_message: str) -> str:
    query = re.sub(r"\s+", " ", user_message.strip())
    lowered = query.lower()

    overrides = {
        "what's the news": "latest world news",
        "whats the news": "latest world news",
        "what is the news": "latest world news",
        "what's happening today": "current world news today",
        "whats happening today": "current world news today",
        "what is happening today": "current world news today",
        "what's going on today": "current world news today",
        "whats going on today": "current world news today",
        "stock info about the big 7": "magnificent seven stocks",
        "stock information about the big 7": "magnificent seven stocks",
        "find stock info about the big 7": "magnificent seven stocks",
        "find stock information about the big 7": "magnificent seven stocks",
    }

    for raw, normalized in overrides.items():
        if raw in lowered:
            return normalized

    prefix_patterns = (
        r"^(hey|hi|hello|morning|evening)\s+vera[\s,]*",
        r"^vera[\s,]+please\s+",
        r"^vera[\s,:-]+",
    )
    for pattern in prefix_patterns:
        query = re.sub(pattern, "", query, flags=re.IGNORECASE)

    filler_patterns = (
        r"\b(can you find|can you look up|can you|could you|would you|please|for me|i want to know)\b",
        r"\b(find out|look up|look into|search for)\b",
    )
    for pattern in filler_patterns:
        query = re.sub(pattern, " ", query, flags=re.IGNORECASE)

    query = re.sub(r"\s+", " ", query).strip(" ,?.!")

    if "latest" in query.lower() and "release notes" in query.lower():
        query = re.sub(r"^the\s+", "", query, flags=re.IGNORECASE)

    return query or user_message.strip()


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
    for preserved_key in ("pending_job_preview", "handoff", "last_enrichment"):
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


def read_session_enrichment(queue_root: Path, session_id: str) -> dict[str, Any] | None:
    """Return the most recent read-only enrichment stored for this session, or None."""
    payload = _read_session_payload(queue_root, session_id)
    enrichment = payload.get("last_enrichment")
    return enrichment if isinstance(enrichment, dict) else None


def write_session_enrichment(
    queue_root: Path, session_id: str, enrichment: dict[str, Any] | None
) -> None:
    """Persist read-only enrichment state into the session for preview-authoring use."""
    payload = _read_session_payload(queue_root, session_id)
    if not payload:
        payload = {"session_id": session_id, "updated_at_ms": int(time.time() * 1000), "turns": []}
    payload["updated_at_ms"] = int(time.time() * 1000)
    if enrichment is None:
        payload.pop("last_enrichment", None)
    else:
        payload["last_enrichment"] = enrichment
    _write_session_payload(queue_root, session_id, payload)


async def run_web_enrichment(*, user_message: str) -> dict[str, Any] | None:
    """Perform a read-only web search and return structured enrichment suitable for preview authoring.

    Returns a dict with ``query``, ``summary`` (plain-text, file-content ready), and
    ``retrieved_at_ms``.  Returns None if web investigation is not configured, the
    message is not an informational query, or the search produces no usable results.
    No side effects; never submits to the queue.
    """
    cfg = load_app_config()
    web_cfg = cfg.web_investigation
    if web_cfg is None:
        return None
    if not _is_informational_web_query(user_message):
        return None

    normalized_query = _normalize_web_query(user_message)
    client = BraveSearchClient(
        api_key_ref=web_cfg.api_key_ref,
        env_api_key_var=web_cfg.env_api_key_var,
    )
    try:
        results = await client.search(query=normalized_query, count=web_cfg.max_results)
    except RuntimeError:
        return None

    if not results:
        return None

    lines: list[str] = []
    for i, r in enumerate(results[:5], start=1):
        snippet = r.description or ""
        if snippet:
            lines.append(f"{i}. {r.title}\n   {snippet}")
        else:
            lines.append(f"{i}. {r.title}")

    return {
        "query": normalized_query,
        "summary": "\n".join(lines),
        "retrieved_at_ms": int(time.time() * 1000),
    }


def session_debug_info(
    queue_root: Path, session_id: str, *, mode_status: str
) -> dict[str, str | int | bool | None]:
    path = _session_path(queue_root, session_id)
    turns = read_session_turns(queue_root, session_id)
    preview = read_session_preview(queue_root, session_id)
    enrichment = read_session_enrichment(queue_root, session_id)
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
        "enrichment_available": isinstance(enrichment, dict),
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


def _recent_assistant_authored_content(turns: list[dict[str, str]]) -> list[str]:
    non_authored_markers = (
        "i submitted the job to voxeraos",
        "job id:",
        "the request is now in the queue",
        "execution has not completed yet",
        "check status and evidence",
        "approval status",
        "expected artifacts",
        "queue state",
    )
    authored: list[str] = []
    for turn in turns[-MAX_SESSION_TURNS:]:
        if str(turn.get("role") or "").strip().lower() != "assistant":
            continue
        text = str(turn.get("text") or "").strip()
        lowered = text.lower()
        if not text:
            continue
        if any(marker in lowered for marker in non_authored_markers):
            continue
        authored.append(text)
    return authored[-4:]


def _build_preview_builder_messages(
    *,
    turns: list[dict[str, str]],
    user_message: str,
    active_preview: dict[str, Any] | None,
    enrichment_context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    guidance = drafting_guidance()
    context_payload: dict[str, Any] = {
        "active_preview": active_preview,
        "latest_user_message": user_message.strip(),
        "recent_turns": turns[-MAX_SESSION_TURNS:],
        "recent_assistant_authored_content": _recent_assistant_authored_content(turns),
        "decision_contract": {
            "action": ["replace_preview", "patch_preview", "no_change"],
            "intent_type": ["new_intent", "refinement", "unclear"],
            "updated_preview": "object | null",
            "patch": "object | null",
        },
        "preview_schema": {
            "goal": "required string",
            "title": "optional string",
            "write_file": {
                "path": "required string",
                "content": "required string",
                "mode": "overwrite | append",
            },
            "enqueue_child": {
                "goal": "required string",
                "title": "optional string",
            },
            "file_organize": {
                "source_path": "required string (~/VoxeraOS/notes/ scope)",
                "destination_dir": "required string (~/VoxeraOS/notes/ scope)",
                "mode": "copy | move",
                "overwrite": "boolean (default false)",
                "delete_original": "boolean (default false)",
            },
            "steps": "optional array of {skill_id, args} for direct bounded file skill routing",
        },
        "guidance_base_shape": guidance.base_shape,
        "guidance_examples": guidance.examples,
    }
    if enrichment_context is not None:
        context_payload["enrichment_context"] = enrichment_context
    return [
        {"role": "system", "content": VERA_PREVIEW_BUILDER_PROMPT},
        {
            "role": "user",
            "content": json.dumps(context_payload, ensure_ascii=False),
        },
    ]


def _extract_hidden_compiler_decision(text: str) -> HiddenCompilerDecision | None:
    parsed, _ = recover_json_object(text)
    if not isinstance(parsed, dict):
        return None
    try:
        return HiddenCompilerDecision.from_payload(parsed)
    except ValueError:
        return None


def _apply_preview_patch(
    *,
    active_preview: dict[str, Any] | None,
    patch: dict[str, Any],
) -> dict[str, Any] | None:
    if active_preview is None:
        return None
    merged: dict[str, Any] = dict(active_preview)
    for key, value in patch.items():
        if key == "write_file" and isinstance(value, dict):
            current = merged.get("write_file")
            if isinstance(current, dict):
                merged["write_file"] = {**current, **value}
                continue
        if key == "enqueue_child" and isinstance(value, dict):
            current = merged.get("enqueue_child")
            if isinstance(current, dict):
                merged["enqueue_child"] = {**current, **value}
                continue
        merged[key] = value
    return merged


async def generate_preview_builder_update(
    *,
    turns: list[dict[str, str]],
    user_message: str,
    active_preview: dict[str, Any] | None,
    enrichment_context: dict[str, Any] | None = None,
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
    recent_assistant_messages = [
        str(turn.get("text") or "")
        for turn in turns[-MAX_SESSION_TURNS:]
        if str(turn.get("role") or "").strip().lower() == "assistant"
    ]

    deterministic_preview = maybe_draft_job_payload(
        user_message,
        active_preview=active_preview,
        recent_user_messages=recent_user_messages,
        enrichment_context=enrichment_context,
        recent_assistant_messages=recent_assistant_messages,
    )

    if not attempts:
        if deterministic_preview is None:
            return None
        try:
            return normalize_preview_payload(deterministic_preview)
        except Exception:
            return None

    messages = _build_preview_builder_messages(
        turns=turns,
        user_message=user_message,
        active_preview=active_preview,
        enrichment_context=enrichment_context,
    )

    for brain in attempts:
        try:
            response = await brain.generate(messages, tools=[])
        except Exception:
            continue
        decision = _extract_hidden_compiler_decision(str(response.text or ""))
        if decision is None:
            continue
        candidate: dict[str, Any] | None = None
        if decision.action == "no_change":
            if deterministic_preview is not None:
                try:
                    return normalize_preview_payload(deterministic_preview)
                except Exception:
                    return active_preview
            return active_preview
        if decision.action == "replace_preview":
            candidate = decision.updated_preview
        elif decision.action == "patch_preview" and decision.patch is not None:
            candidate = _apply_preview_patch(active_preview=active_preview, patch=decision.patch)

        if candidate is None:
            if deterministic_preview is None:
                return active_preview
            try:
                return normalize_preview_payload(deterministic_preview)
            except Exception:
                return active_preview

        try:
            return normalize_preview_payload(candidate)
        except Exception:
            if deterministic_preview is None:
                return active_preview
            try:
                return normalize_preview_payload(deterministic_preview)
            except Exception:
                return active_preview

    if deterministic_preview is None:
        return active_preview
    try:
        return normalize_preview_payload(deterministic_preview)
    except Exception:
        return active_preview


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
        normalized_query = _normalize_web_query(user_message)
        client = BraveSearchClient(
            api_key_ref=web_cfg.api_key_ref,
            env_api_key_var=web_cfg.env_api_key_var,
        )
        try:
            results = await client.search(query=normalized_query, count=web_cfg.max_results)
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
            "answer": _format_web_investigation_answer(normalized_query, results),
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
