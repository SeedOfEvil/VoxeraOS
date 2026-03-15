from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..brain.fallback import classify_fallback_reason
from ..brain.gemini import GeminiBrain
from ..brain.json_recovery import recover_json_object
from ..brain.openai_compat import OpenAICompatBrain
from ..config import load_app_config
from ..core.queue_inspect import lookup_job
from ..core.queue_result_consumers import resolve_structured_execution
from .brave_search import BraveSearchClient, WebSearchResult
from .handoff import drafting_guidance, maybe_draft_job_payload, normalize_preview_payload
from .prompt import VERA_PREVIEW_BUILDER_PROMPT, VERA_SYSTEM_PROMPT

MAX_SESSION_TURNS = 8
_SESSION_ID_LENGTH = 24
_MAX_LINKED_JOB_TRACK = 64
_MAX_LINKED_COMPLETIONS = 64
_MAX_LINKED_NOTIFICATIONS = 128


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
    normalized = _build_structured_investigation_results(query=query, results=results)
    findings = normalized.get("results") if isinstance(normalized, dict) else None
    if not isinstance(findings, list) or not findings:
        return (
            f"I ran a read-only web investigation for '{query}' but didn't find usable results. "
            "Try refining the query or asking for a narrower topic."
        )

    bullets = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        result_id = int(finding.get("result_id") or 0)
        title = str(finding.get("title") or "Untitled").strip()
        source = str(finding.get("source") or "unknown").strip()
        url = str(finding.get("url") or "").strip()
        snippet = str(finding.get("snippet") or "No snippet available.").strip()
        relevance = str(finding.get("why_it_matched") or "").strip()
        bullets.append(
            "\n".join(
                [
                    f"Result {result_id}: {title}",
                    f"- Source: {url}",
                    f"- Source domain: {source}",
                    f"- Snippet: {snippet}",
                    f"- Why it matched: {relevance}",
                ]
            )
        )

    joined = "\n".join(bullets)
    return (
        "Here are the top findings I found via read-only Brave web investigation:\n\n"
        f"{joined}\n\n"
        "You can reference them by number (for example: 'save result 2 to a note')."
    )


def _source_from_url(url: str) -> str:
    host = urlparse(url).netloc.strip().lower()
    return host[4:] if host.startswith("www.") else (host or "unknown")


def _build_structured_investigation_results(
    *, query: str, results: list[WebSearchResult]
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for idx, result in enumerate(results[:5], start=1):
        snippet = str(result.description or "").strip() or "No snippet available."
        age = str(result.age or "").strip()
        relevance = f"Matched read-only web query '{query}'."
        if age:
            relevance = f"Matched read-only web query '{query}' ({age})."
        findings.append(
            {
                "result_id": idx,
                "rank": idx,
                "title": result.title,
                "url": result.url,
                "source": _source_from_url(result.url),
                "snippet": snippet,
                "why_it_matched": relevance,
            }
        )
    return {
        "query": query,
        "retrieved_at_ms": int(time.time() * 1000),
        "results": findings,
    }


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


def _read_json_dict(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


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
        "linked_queue_jobs",
    ):
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


def read_session_investigation(queue_root: Path, session_id: str) -> dict[str, Any] | None:
    payload = _read_session_payload(queue_root, session_id)
    investigation = payload.get("last_investigation")
    return investigation if isinstance(investigation, dict) else None


def write_session_investigation(
    queue_root: Path, session_id: str, investigation: dict[str, Any] | None
) -> None:
    payload = _read_session_payload(queue_root, session_id)
    if not payload:
        payload = {"session_id": session_id, "updated_at_ms": int(time.time() * 1000), "turns": []}
    payload["updated_at_ms"] = int(time.time() * 1000)
    if investigation is None:
        payload.pop("last_investigation", None)
    else:
        payload["last_investigation"] = investigation
    _write_session_payload(queue_root, session_id, payload)


def read_session_derived_investigation_output(
    queue_root: Path, session_id: str
) -> dict[str, Any] | None:
    payload = _read_session_payload(queue_root, session_id)
    derived = payload.get("last_derived_investigation_output")
    return derived if isinstance(derived, dict) else None


def write_session_derived_investigation_output(
    queue_root: Path, session_id: str, derived_output: dict[str, Any] | None
) -> None:
    payload = _read_session_payload(queue_root, session_id)
    if not payload:
        payload = {"session_id": session_id, "updated_at_ms": int(time.time() * 1000), "turns": []}
    payload["updated_at_ms"] = int(time.time() * 1000)
    if derived_output is None:
        payload.pop("last_derived_investigation_output", None)
    else:
        payload["last_derived_investigation_output"] = derived_output
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
    investigation = read_session_investigation(queue_root, session_id)
    derived_output = read_session_derived_investigation_output(queue_root, session_id)
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
        "investigation_available": isinstance(investigation, dict),
        "investigation_result_count": len(investigation.get("results", []))
        if isinstance(investigation, dict)
        else 0,
        "derived_investigation_output_available": isinstance(derived_output, dict),
        "handoff_attempted": handoff.get("attempted") is True,
        "handoff_status": str(handoff.get("status") or "none"),
        "handoff_queue_path": str(handoff.get("queue_path") or str(queue_root)),
        "handoff_job_id": str(handoff.get("job_id") or "") or None,
        "handoff_error": str(handoff.get("error") or "") or None,
        "linked_jobs": len(_read_linked_job_registry(queue_root, session_id).get("tracked", [])),
        "linked_completions": len(
            _read_linked_job_registry(queue_root, session_id).get("completions", [])
        ),
    }


def _read_linked_job_registry(queue_root: Path, session_id: str) -> dict[str, Any]:
    payload = _read_session_payload(queue_root, session_id)
    raw = payload.get("linked_queue_jobs")
    return (
        raw
        if isinstance(raw, dict)
        else {"tracked": [], "completions": [], "notification_outbox": []}
    )


def _write_linked_job_registry(queue_root: Path, session_id: str, registry: dict[str, Any]) -> None:
    payload = _read_session_payload(queue_root, session_id)
    if not payload:
        payload = {"session_id": session_id, "updated_at_ms": int(time.time() * 1000), "turns": []}
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


def _completion_delivery_eligible(completion: dict[str, Any]) -> bool:
    supported_policies = {"read_only_success", "mutating_success", "approval_blocked", "failed"}
    policy = str(completion.get("surfacing_policy") or "").strip().lower()
    if policy not in supported_policies:
        return False

    terminal_outcome = str(completion.get("terminal_outcome") or "").strip().lower()
    if policy == "read_only_success":
        return terminal_outcome == "succeeded"
    if policy == "approval_blocked":
        return terminal_outcome in {"blocked", "failed"}
    if policy == "failed":
        return terminal_outcome == "failed"
    if policy == "mutating_success":
        return terminal_outcome == "succeeded" and _is_true_terminal_completion(completion)
    return False


def _build_completion_notification(
    *, session_id: str, completion: dict[str, Any]
) -> dict[str, Any]:
    created_at_ms = int(completion.get("completion_detected_at_ms") or int(time.time() * 1000))
    lineage = completion.get("lineage")
    root_job_id = None
    if isinstance(lineage, dict):
        root_job_id = str(lineage.get("root_job_id") or "").strip() or None
    return {
        "notification_id": f"{session_id}:{completion.get('job_ref')}:{created_at_ms}",
        "job_id": str(completion.get("job_ref") or ""),
        "session_id": session_id,
        "root_job_id": root_job_id,
        "outcome_class": str(completion.get("normalized_outcome_class") or "").strip() or None,
        "is_user_terminal": _completion_delivery_eligible(completion),
        "message": _format_completion_autosurface_message(completion),
        "created_at_ms": created_at_ms,
        "delivery_status": "pending",
        "surfaced_in_chat": completion.get("surfaced_in_chat") is True,
        "surfaced_at_ms": completion.get("surfaced_at_ms"),
        "fallback_pending": completion.get("surfaced_in_chat") is not True,
    }


def _upsert_completion_notification(
    *, session_id: str, registry: dict[str, Any], completion: dict[str, Any]
) -> dict[str, Any]:
    outbox_raw = registry.get("notification_outbox")
    outbox = outbox_raw if isinstance(outbox_raw, list) else []
    job_ref = str(completion.get("job_ref") or "")
    existing: dict[str, Any] | None = None
    for item in outbox:
        if isinstance(item, dict) and str(item.get("job_id") or "") == job_ref:
            existing = item
            break
    if existing is None:
        existing = _build_completion_notification(session_id=session_id, completion=completion)
        outbox.append(existing)
    else:
        existing["is_user_terminal"] = _completion_delivery_eligible(completion)
        existing["message"] = _format_completion_autosurface_message(completion)
        existing["surfaced_in_chat"] = completion.get("surfaced_in_chat") is True
        existing["surfaced_at_ms"] = completion.get("surfaced_at_ms")
        if completion.get("surfaced_in_chat") is True:
            existing["fallback_pending"] = False
            if str(existing.get("delivery_status") or "").strip().lower() == "pending":
                existing["delivery_status"] = "delivered"
        else:
            existing["fallback_pending"] = True
    registry["notification_outbox"] = outbox[-_MAX_LINKED_NOTIFICATIONS:]
    return existing


def _attempt_live_delivery(
    *, queue_root: Path, session_id: str, completion: dict[str, Any], notification: dict[str, Any]
) -> bool:
    if completion.get("surfaced_in_chat") is True:
        notification["delivery_status"] = "delivered"
        notification["fallback_pending"] = False
        return True
    if not _completion_delivery_eligible(completion):
        notification["delivery_status"] = "pending"
        notification["fallback_pending"] = True
        return False
    if not _session_path(queue_root, session_id).exists():
        notification["delivery_status"] = "unavailable"
        notification["fallback_pending"] = True
        return False

    message = _format_completion_autosurface_message(completion)
    try:
        append_session_turn(queue_root, session_id, role="assistant", text=message)
    except Exception:
        notification["delivery_status"] = "unavailable"
        notification["fallback_pending"] = True
        return False
    now_ms = int(time.time() * 1000)
    completion["surfaced_in_chat"] = True
    completion["surfaced_at_ms"] = now_ms
    notification["delivery_status"] = "delivered"
    notification["fallback_pending"] = False
    notification["surfaced_in_chat"] = True
    notification["surfaced_at_ms"] = now_ms
    return True


def _is_terminal_queue_state(*, lifecycle_state: str, terminal_outcome: str, bucket: str) -> bool:
    lifecycle = lifecycle_state.strip().lower()
    outcome = terminal_outcome.strip().lower()
    return (
        bucket in {"done", "failed", "canceled"}
        or lifecycle in {"done", "failed", "canceled"}
        or outcome in {"succeeded", "failed", "blocked", "canceled"}
    )


def _classify_surfacing_policy(payload: dict[str, Any]) -> str:
    terminal_outcome = str(payload.get("terminal_outcome") or "").strip().lower()
    approval_status = str(payload.get("approval_status") or "").strip().lower()
    outcome_class = str(payload.get("normalized_outcome_class") or "").strip().lower()
    request_kind = str(payload.get("request_kind") or "").strip().lower()
    side_effect_class = str(payload.get("side_effect_class") or "").strip().lower()
    read_only_requested = payload.get("read_only_requested") is True
    latest_summary = str(payload.get("latest_summary") or "")
    highlights = payload.get("result_highlights")
    result_highlight_count = len(highlights) if isinstance(highlights, list) else 0

    if approval_status == "pending" or outcome_class == "approval_blocked":
        return "approval_blocked"
    if terminal_outcome == "canceled":
        return "canceled"
    if terminal_outcome in {"failed", "blocked"}:
        if outcome_class in {"policy_denied", "capability_boundary_mismatch", "path_blocked_scope"}:
            return "manual_only"
        return "failed"
    if len(latest_summary) > 480 or result_highlight_count >= 6:
        return "noisy_large_result"
    if terminal_outcome == "succeeded":
        if read_only_requested:
            return "read_only_success"
        if side_effect_class in {"write", "execute", "mutating"}:
            return "mutating_success"
        if request_kind in {"write_file", "file_organize", "open_url", "open_app", "run_command"}:
            return "mutating_success"
        return "read_only_success"
    return "manual_only"


def _normalize_result_highlights(structured: dict[str, Any]) -> list[str]:
    highlights: list[str] = []
    artifact_families = structured.get("artifact_families")
    if isinstance(artifact_families, list) and artifact_families:
        compact = ", ".join(
            str(item).strip() for item in artifact_families[:4] if str(item).strip()
        )
        if compact:
            highlights.append(f"artifact_families={compact}")
    child_summary = structured.get("child_summary")
    if isinstance(child_summary, dict) and child_summary:
        summary_parts = [
            f"{key}:{int(value)}"
            for key, value in child_summary.items()
            if isinstance(value, int) and key in {"done", "failed", "pending", "canceled"}
        ]
        if summary_parts:
            highlights.append("child_summary=" + ", ".join(summary_parts))
    return highlights[:6]


def _build_completion_payload(
    *,
    session_id: str,
    job_ref: str,
    bucket: str,
    structured: dict[str, Any],
    state_payload: dict[str, Any],
    job_payload: dict[str, Any],
) -> dict[str, Any]:
    raw_job_intent = job_payload.get("job_intent")
    job_intent: dict[str, Any] = raw_job_intent if isinstance(raw_job_intent, dict) else {}
    request_kind = str(
        job_intent.get("request_kind")
        or job_payload.get("request_kind")
        or job_payload.get("kind")
        or "unknown"
    )
    mission_id = str(job_intent.get("mission_id") or job_payload.get("mission_id") or "").strip()
    lifecycle_state = str(
        structured.get("lifecycle_state") or state_payload.get("lifecycle_state") or ""
    ).strip()
    terminal_outcome = str(
        structured.get("terminal_outcome") or state_payload.get("terminal_outcome") or ""
    ).strip()
    approval_status = str(
        structured.get("approval_status") or state_payload.get("approval_status") or "none"
    ).strip()
    execution_capabilities_raw = structured.get("execution_capabilities")
    execution_capabilities: dict[str, Any] = (
        execution_capabilities_raw if isinstance(execution_capabilities_raw, dict) else {}
    )
    lineage_raw = structured.get("lineage")
    lineage: dict[str, Any] | None = lineage_raw if isinstance(lineage_raw, dict) else None
    child_refs_raw = structured.get("child_refs")
    child_refs: list[Any] = child_refs_raw if isinstance(child_refs_raw, list) else []
    child_summary_raw = structured.get("child_summary")
    child_summary: dict[str, Any] | None = (
        child_summary_raw if isinstance(child_summary_raw, dict) else None
    )

    payload = {
        "job_ref": job_ref,
        "linked_session_id": session_id,
        "linked_thread_id": session_id,
        "lifecycle_state": lifecycle_state,
        "terminal_outcome": terminal_outcome,
        "approval_status": approval_status,
        "normalized_outcome_class": str(structured.get("normalized_outcome_class") or "").strip(),
        "request_kind": request_kind,
        "read_only_requested": job_payload.get("read_only") is True
        or job_intent.get("read_only") is True,
        "mission_id": mission_id or None,
        "latest_summary": str(structured.get("latest_summary") or "").strip(),
        "failure_summary": str(structured.get("error") or "").strip(),
        "operator_note": str(structured.get("operator_note") or "").strip(),
        "next_action_hint": str(structured.get("next_action_hint") or "").strip(),
        "result_highlights": _normalize_result_highlights(structured),
        "side_effect_class": str(execution_capabilities.get("side_effect_class") or "").strip(),
        "lineage": lineage,
        "child_refs_count": len(child_refs),
        "child_summary": child_summary,
        "stop_reason": str(structured.get("stop_reason") or "").strip(),
        "queue_bucket": bucket,
        "completion_detected_at_ms": int(time.time() * 1000),
        "surfaced_in_chat": False,
        "surfaced_at_ms": None,
    }
    payload["surfacing_policy"] = _classify_surfacing_policy(payload)
    return payload


def ingest_linked_job_completions(
    queue_root: Path,
    session_id: str,
    *,
    only_job_ref: str | None = None,
) -> list[dict[str, Any]]:
    registry = _read_linked_job_registry(queue_root, session_id)
    tracked_raw = registry.get("tracked")
    tracked = tracked_raw if isinstance(tracked_raw, list) else []
    completions_raw = registry.get("completions")
    completions = completions_raw if isinstance(completions_raw, list) else []
    completion_job_refs = {
        str(item.get("job_ref") or "")
        for item in completions
        if isinstance(item, dict) and str(item.get("job_ref") or "")
    }

    created: list[dict[str, Any]] = []
    changed = False
    only_ref = Path(only_job_ref).name.strip() if only_job_ref else None
    for item in tracked:
        if not isinstance(item, dict):
            continue
        job_ref = str(item.get("job_ref") or "").strip()
        if only_ref and job_ref != only_ref:
            continue
        if not job_ref or job_ref in completion_job_refs:
            continue
        found = lookup_job(queue_root, job_ref)
        if found is None:
            continue
        state_payload = _read_json_dict(
            found.primary_path.with_name(f"{found.primary_path.stem}.state.json")
        )
        approval_payload = _read_json_dict(found.approval_path) if found.approval_path else {}
        failed_payload = (
            _read_json_dict(found.failed_sidecar_path) if found.failed_sidecar_path else {}
        )
        structured = resolve_structured_execution(
            artifacts_dir=found.artifacts_dir,
            state_sidecar=state_payload,
            approval=approval_payload,
            failed_sidecar=failed_payload,
        )
        lifecycle_state = str(
            structured.get("lifecycle_state") or state_payload.get("lifecycle_state") or ""
        )
        terminal_outcome = str(
            structured.get("terminal_outcome") or state_payload.get("terminal_outcome") or ""
        )
        if not _is_terminal_queue_state(
            lifecycle_state=lifecycle_state,
            terminal_outcome=terminal_outcome,
            bucket=found.bucket,
        ):
            continue
        job_payload = _read_json_dict(found.primary_path)
        completion = _build_completion_payload(
            session_id=session_id,
            job_ref=job_ref,
            bucket=found.bucket,
            structured=structured,
            state_payload=state_payload,
            job_payload=job_payload,
        )
        completions.append(completion)
        completion_job_refs.add(job_ref)
        item["completion_ingested"] = True
        item["completion_detected_at_ms"] = int(completion.get("completion_detected_at_ms") or 0)
        created.append(completion)
        changed = True

    if changed:
        registry["tracked"] = tracked[-_MAX_LINKED_JOB_TRACK:]
        registry["completions"] = completions[-_MAX_LINKED_COMPLETIONS:]
        _write_linked_job_registry(queue_root, session_id, registry)

    return created


def read_linked_job_completions(queue_root: Path, session_id: str) -> list[dict[str, Any]]:
    registry = _read_linked_job_registry(queue_root, session_id)
    completions = registry.get("completions")
    if not isinstance(completions, list):
        return []
    return [item for item in completions if isinstance(item, dict)]


def _format_completion_autosurface_message(completion: dict[str, Any]) -> str:
    request_kind = str(completion.get("request_kind") or "request").strip().replace("_", " ")
    latest_summary = str(completion.get("latest_summary") or "").strip()
    failure_summary = str(completion.get("failure_summary") or "").strip()
    operator_note = str(completion.get("operator_note") or "").strip()
    next_action_hint = str(completion.get("next_action_hint") or "").strip()
    policy = str(completion.get("surfacing_policy") or "").strip().lower()

    if policy == "approval_blocked":
        message = "Your linked request is paused pending approval in VoxeraOS."
        if latest_summary:
            return f"{message} Latest summary: {latest_summary}".strip()
        if next_action_hint:
            return f"{message} Next action: {next_action_hint}".strip()
        return f"{message} Approve or reject it in VoxeraOS to continue.".strip()

    if policy == "failed":
        summary = failure_summary or latest_summary
        message = f"Your linked {request_kind} job failed."
        if summary:
            message = f"{message} Failure summary: {summary}"
        hint = next_action_hint or operator_note
        if hint and len(hint) <= 220:
            message = f"{message} Next action: {hint}"
        return message.strip()

    highlights = completion.get("result_highlights")
    highlight_text = ""
    if isinstance(highlights, list):
        compact = "; ".join(str(item).strip() for item in highlights[:2] if str(item).strip())
        if compact:
            highlight_text = compact

    message = f"Your linked {request_kind} job completed successfully."
    if latest_summary:
        message = f"{message} {latest_summary}"
    elif highlight_text:
        message = f"{message} Canonical evidence highlights: {highlight_text}."
    else:
        message = f"{message} I have the canonical result available for follow-up."
    return message.strip()


def _is_true_terminal_completion(completion: dict[str, Any]) -> bool:
    child_summary = completion.get("child_summary")
    child_refs_count = int(completion.get("child_refs_count") or 0)
    stop_reason = str(completion.get("stop_reason") or "").strip().lower()

    if isinstance(child_summary, dict):
        pending_like = sum(
            int(child_summary.get(key) or 0) for key in ("pending", "awaiting_approval", "unknown")
        )
        if pending_like > 0:
            return False
        total = int(child_summary.get("total") or 0)
        if child_refs_count > 0 and total <= 0:
            return False
    elif child_refs_count > 0:
        return False

    return not any(token in stop_reason for token in ("enqueue_child", "delegat", "handoff_child"))


def maybe_auto_surface_linked_completion(queue_root: Path, session_id: str) -> str | None:
    registry = _read_linked_job_registry(queue_root, session_id)
    completions_raw = registry.get("completions")
    completions = completions_raw if isinstance(completions_raw, list) else []

    outbox_raw = registry.get("notification_outbox")
    outbox = outbox_raw if isinstance(outbox_raw, list) else []
    for completion in completions:
        if not isinstance(completion, dict):
            continue
        notification = _upsert_completion_notification(
            session_id=session_id,
            registry=registry,
            completion=completion,
        )
        if completion.get("surfaced_in_chat") is True:
            continue
        if not _completion_delivery_eligible(completion):
            continue

        completion["surfaced_in_chat"] = True
        completion["surfaced_at_ms"] = int(time.time() * 1000)
        notification["delivery_status"] = "fallback_delivered"
        notification["fallback_pending"] = False
        notification["surfaced_in_chat"] = True
        notification["surfaced_at_ms"] = completion["surfaced_at_ms"]
        registry["completions"] = completions[-_MAX_LINKED_COMPLETIONS:]
        registry["notification_outbox"] = outbox[-_MAX_LINKED_NOTIFICATIONS:]
        _write_linked_job_registry(queue_root, session_id, registry)
        return _format_completion_autosurface_message(completion)

    return None


def maybe_deliver_linked_completion_live(
    queue_root: Path, session_id: str, *, job_ref: str
) -> bool:
    ingest_linked_job_completions(queue_root, session_id, only_job_ref=job_ref)
    registry = _read_linked_job_registry(queue_root, session_id)
    completions_raw = registry.get("completions")
    completions = completions_raw if isinstance(completions_raw, list) else []
    changed = False
    delivered = False
    for completion in completions:
        if not isinstance(completion, dict):
            continue
        if str(completion.get("job_ref") or "") != Path(job_ref).name:
            continue
        notification = _upsert_completion_notification(
            session_id=session_id,
            registry=registry,
            completion=completion,
        )
        if _attempt_live_delivery(
            queue_root=queue_root,
            session_id=session_id,
            completion=completion,
            notification=notification,
        ):
            delivered = True
            changed = True
        else:
            changed = True
    if changed:
        registry["completions"] = completions[-_MAX_LINKED_COMPLETIONS:]
        outbox_raw = registry.get("notification_outbox")
        outbox = outbox_raw if isinstance(outbox_raw, list) else []
        registry["notification_outbox"] = outbox[-_MAX_LINKED_NOTIFICATIONS:]
        _write_linked_job_registry(queue_root, session_id, registry)
    return delivered


def maybe_deliver_linked_completion_live_for_job(queue_root: Path, *, job_ref: str) -> int:
    normalized = Path(job_ref).name.strip()
    if not normalized:
        return 0
    sessions_dir = queue_root / "artifacts" / "vera_sessions"
    if not sessions_dir.exists():
        return 0
    delivered_count = 0
    for session_file in sorted(sessions_dir.glob("*.json")):
        session_id = session_file.stem.strip()
        if not session_id:
            continue
        registry = _read_linked_job_registry(queue_root, session_id)
        tracked = registry.get("tracked")
        tracked_list = tracked if isinstance(tracked, list) else []
        if not any(
            isinstance(item, dict) and str(item.get("job_ref") or "") == normalized
            for item in tracked_list
        ):
            continue
        if maybe_deliver_linked_completion_live(queue_root, session_id, job_ref=normalized):
            delivered_count += 1
    return delivered_count


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
    investigation_context: dict[str, Any] | None = None,
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
        investigation_context=investigation_context,
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


async def generate_vera_reply(*, turns: list[dict[str, str]], user_message: str) -> dict[str, Any]:
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

        structured_results = _build_structured_investigation_results(
            query=normalized_query,
            results=results,
        )
        return {
            "answer": _format_web_investigation_answer(normalized_query, results),
            "status": "ok:web_investigation",
            "investigation": structured_results,
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
