from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from ..config import load_app_config
from .brave_search import BraveSearchClient, WebSearchResult


def is_informational_web_query(message: str) -> bool:
    lowered = message.lower().strip()
    if not lowered:
        return False
    if _is_operational_open_request(lowered):
        return False
    if _is_explicit_internal_search_request(lowered):
        return True
    if _is_operational_side_effect_request(lowered):
        return False

    explicit_search_terms = (
        "what's on",
        "what is on",
        "look up",
        "look into",
        "search for",
        "search ",
        "search the web",
        "search online",
        "find the latest",
        "find current",
        "find sources",
        "investigate",
        "investigation",
        "web investigation",
        "stock information",
        "latest",
        "latest news",
        "latest stories",
        "latest updates",
        "latest official",
        "latest documentation",
        "latest docs",
        "current documentation",
        "official documentation",
        "official docs",
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
        "research",
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

    contains_explicit_search_signal = any(term in lowered for term in explicit_search_terms)
    looks_like_question = lowered.endswith("?") or any(
        lowered.startswith(starter) for starter in question_starters
    )

    current_info_query = bool(
        re.search(r"\b(latest|current|recent|today|right\s+now|as\s+of\s+today)\b", lowered)
    )
    explicit_docs_query = bool(re.search(r"\b(official\s+)?(docs|documentation)\b", lowered))

    if explicit_docs_query and current_info_query:
        return True

    return contains_explicit_search_signal or (
        looks_like_question and any(hint in lowered for hint in web_hints)
    )


def normalize_web_query(user_message: str) -> str:
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


def build_structured_investigation_results(
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


def format_web_investigation_answer(query: str, results: list[WebSearchResult]) -> str:
    normalized = build_structured_investigation_results(query=query, results=results)
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


async def maybe_handle_investigation_turn(
    *,
    user_message: str,
    web_cfg: Any,
    is_informational_web_query_hook: Callable[[str], bool] = is_informational_web_query,
    normalize_web_query_hook: Callable[[str], str] = normalize_web_query,
    format_web_investigation_answer_hook: Callable[
        [str, list[WebSearchResult]], str
    ] = format_web_investigation_answer,
    build_structured_investigation_results_hook: Callable[
        [str, list[WebSearchResult]], dict[str, Any]
    ]
    | None = None,
    brave_client_factory: type[BraveSearchClient] = BraveSearchClient,
) -> dict[str, Any] | None:
    if not is_informational_web_query_hook(user_message):
        return None

    if web_cfg is None:
        return {
            "answer": (
                "Read-only web investigation is not configured yet (Brave API key missing). "
                "I can still help reason from what you provide, but I cannot fetch live web results yet."
            ),
            "status": "web_investigation_unconfigured",
        }

    normalized_query = normalize_web_query_hook(user_message)
    client = brave_client_factory(
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

    builder = build_structured_investigation_results_hook or (
        lambda query, results: build_structured_investigation_results(query=query, results=results)
    )
    structured_results = builder(normalized_query, results)
    return {
        "answer": format_web_investigation_answer_hook(normalized_query, results),
        "status": "ok:web_investigation",
        "investigation": structured_results,
    }


async def run_web_enrichment(
    *,
    user_message: str,
    load_app_config_hook: Callable[[], Any] = load_app_config,
    is_informational_web_query_hook: Callable[[str], bool] = is_informational_web_query,
    normalize_web_query_hook: Callable[[str], str] = normalize_web_query,
    brave_client_factory: type[BraveSearchClient] = BraveSearchClient,
) -> dict[str, Any] | None:
    """Perform a read-only web search and return structured enrichment suitable for preview authoring.

    Returns a dict with ``query``, ``summary`` (plain-text, file-content ready), and
    ``retrieved_at_ms``. Returns None if web investigation is not configured, the
    message is not an informational query, or the search produces no usable results.
    No side effects; never submits to the queue.
    """
    cfg = load_app_config_hook()
    web_cfg = cfg.web_investigation
    if web_cfg is None:
        return None
    if not is_informational_web_query_hook(user_message):
        return None

    normalized_query = normalize_web_query_hook(user_message)
    client = brave_client_factory(
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
    for i, result in enumerate(results[:5], start=1):
        snippet = result.description or ""
        if snippet:
            lines.append(f"{i}. {result.title}\n   {snippet}")
        else:
            lines.append(f"{i}. {result.title}")

    return {
        "query": normalized_query,
        "summary": "\n".join(lines),
        "retrieved_at_ms": int(time.time() * 1000),
    }


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


def _source_from_url(url: str) -> str:
    host = urlparse(url).netloc.strip().lower()
    return host[4:] if host.startswith("www.") else (host or "unknown")
