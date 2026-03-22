from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any, Protocol

from .weather import (
    WeatherSnapshot,
    format_current_weather_answer,
    format_daily_weather_answer,
    format_hourly_weather_answer,
    format_weekend_weather_answer,
)

_WEATHER_FOLLOWUP_MAX_AGE_MS = 30 * 60 * 1000
_PENDING_WEATHER_ACCEPTANCE_RE = re.compile(
    r"(?:yes(?:\s+please)?|yes\s+go\s+ahead|go\s+ahead|do\s+it)[.!?]*"
)

WeatherLookup = Callable[[str], Awaitable[WeatherSnapshot]]
WeatherFollowupLookup = Callable[[str, str | None], Awaitable[WeatherSnapshot]]
WeatherDetector = Callable[[str], bool]
WeatherLocationExtractor = Callable[[str], str | None]
WeatherLocationNormalizer = Callable[[str], str]
WeatherFollowupExtractor = Callable[[str], str | None]
WeatherContextPredicate = Callable[[dict[str, Any] | None], bool]


class WeatherFollowupAnswerBuilder(Protocol):
    def __call__(self, snapshot_payload: dict[str, Any], *, followup_kind: str) -> str: ...


def is_weather_investigation_request(message: str) -> bool:
    lowered = message.lower().strip()
    if not lowered:
        return False
    if not re.search(r"\b(weather|forecast|temperature|conditions?|outside)\b", lowered):
        return False
    explicit_terms = (
        "search the web",
        "search online",
        "search for",
        "look up",
        "look this up",
        "investigate",
        "browse sources",
        "browse results",
        "find sources",
        "show sources",
        "show me sources",
    )
    return any(term in lowered for term in explicit_terms)


def is_weather_question(
    message: str,
    *,
    is_weather_investigation_request_hook: WeatherDetector = is_weather_investigation_request,
) -> bool:
    lowered = message.lower().strip()
    if not lowered or is_weather_investigation_request_hook(lowered):
        return False
    patterns = (
        r"\bweather\b",
        r"\bforecast\b",
        r"\bcurrent conditions?\b",
        r"\btemperature\b",
        r"\btemp\b",
        r"\boutside\b",
        r"\bwhat'?s it like outside\b",
        r"\bhow'?s the weather\b",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


def normalize_weather_location_candidate(candidate: str) -> str:
    cleaned = re.sub(r"\s+", " ", candidate.strip())
    cleaned = cleaned.strip(" ,.?!")
    cleaned = re.sub(r"^(in|for|at)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\b(today|right now|currently|now|please)\b", " ", cleaned, flags=re.IGNORECASE
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.?!")
    return cleaned


def extract_weather_location_from_message(
    message: str,
    *,
    normalize_weather_location_candidate_hook: WeatherLocationNormalizer = normalize_weather_location_candidate,
) -> str | None:
    text = " ".join(message.strip().split())
    if not text:
        return None

    preposition_match = re.search(r"\b(?:in|for|at)\s+(.+)$", text, re.IGNORECASE)
    if preposition_match:
        candidate = normalize_weather_location_candidate_hook(preposition_match.group(1))
        if candidate:
            return candidate

    stripped = re.sub(
        r"\b(what'?s|what is|how'?s|how is|show me|give me|check|tell me|current|today'?s|todays)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    stripped = re.sub(
        r"\b(weather|forecast|conditions?|temperature|temp|outside|like outside)\b",
        " ",
        stripped,
        flags=re.IGNORECASE,
    )
    candidate = normalize_weather_location_candidate_hook(stripped)
    if candidate and not re.fullmatch(
        r"(the|like|the like|weather|forecast|today|right now)",
        candidate,
        re.IGNORECASE,
    ):
        return candidate
    return None


def extract_weather_followup_kind(message: str) -> str | None:
    lowered = message.strip().lower()
    if not lowered:
        return None
    normalized = re.sub(r"[^a-z0-9]+", " ", lowered).strip()
    if normalized in {"hourly", "hourly outlook", "show hourly", "show me hourly"}:
        return "hourly"
    if normalized in {
        "7 day",
        "7 day outlook",
        "7 day forecast",
        "weekly",
        "weekly outlook",
        "show me the weekly",
        "show me weekly",
    }:
        return "7_day"
    if normalized in {
        "14 day",
        "14 day outlook",
        "14 day forecast",
        "15 day",
        "15 day outlook",
        "15 day forecast",
    }:
        return "14_day" if normalized.startswith("14") else "15_day"
    if normalized in {"weekend", "weekend outlook", "show me the weekend", "show weekend"}:
        return "weekend"
    return None


def weather_followup_is_active(weather_context: dict[str, Any] | None) -> bool:
    if not isinstance(weather_context, dict):
        return False
    if weather_context.get("followup_active") is not True:
        return False
    retrieved_at_ms = weather_context.get("retrieved_at_ms")
    if isinstance(retrieved_at_ms, int):
        return int(time.time() * 1000) - retrieved_at_ms <= _WEATHER_FOLLOWUP_MAX_AGE_MS
    return True


def weather_context_has_pending_lookup(weather_context: dict[str, Any] | None) -> bool:
    if not isinstance(weather_context, dict):
        return False
    pending_lookup = weather_context.get("pending_lookup")
    return isinstance(pending_lookup, dict) and bool(
        str(pending_lookup.get("location_query") or "").strip()
    )


def weather_context_is_waiting_for_location(weather_context: dict[str, Any] | None) -> bool:
    return isinstance(weather_context, dict) and weather_context.get("awaiting_location") is True


def should_accept_pending_weather_offer(message: str) -> bool:
    return bool(_PENDING_WEATHER_ACCEPTANCE_RE.fullmatch(message.strip().lower()))


def weather_answer_for_followup(
    snapshot_payload: dict[str, Any],
    *,
    followup_kind: str,
) -> str:
    hourly = snapshot_payload.get("hourly")
    daily = snapshot_payload.get("daily")
    resolved_location = (
        str(snapshot_payload.get("resolved_location") or "").strip() or "that location"
    )
    proxy = SimpleNamespace(
        location=SimpleNamespace(label=resolved_location),
        hourly=hourly if isinstance(hourly, list) else [],
        daily=daily if isinstance(daily, list) else [],
    )
    if followup_kind == "hourly":
        return format_hourly_weather_answer(proxy)
    if followup_kind == "weekend":
        return format_weekend_weather_answer(proxy)
    if followup_kind == "14_day":
        return format_daily_weather_answer(proxy, days=14)
    if followup_kind == "15_day":
        return format_daily_weather_answer(proxy, days=15)
    return format_daily_weather_answer(proxy, days=7)


async def maybe_handle_weather_turn(
    *,
    user_message: str,
    weather_context: dict[str, Any] | None,
    code_draft: bool,
    writing_draft: bool,
    lookup_weather: WeatherLookup,
    lookup_weather_followup: WeatherFollowupLookup,
    is_weather_investigation_request_hook: WeatherDetector = is_weather_investigation_request,
    extract_weather_followup_kind_hook: WeatherFollowupExtractor = extract_weather_followup_kind,
    is_weather_question_hook: WeatherDetector = is_weather_question,
    extract_weather_location_from_message_hook: WeatherLocationExtractor = extract_weather_location_from_message,
    weather_followup_is_active_hook: WeatherContextPredicate = weather_followup_is_active,
    weather_context_has_pending_lookup_hook: WeatherContextPredicate = weather_context_has_pending_lookup,
    weather_context_is_waiting_for_location_hook: WeatherContextPredicate = weather_context_is_waiting_for_location,
    normalize_weather_location_candidate_hook: WeatherLocationNormalizer = normalize_weather_location_candidate,
    weather_answer_for_followup_hook: WeatherFollowupAnswerBuilder = weather_answer_for_followup,
) -> dict[str, Any] | None:
    explicit_weather_investigation = is_weather_investigation_request_hook(user_message)
    weather_followup_kind = extract_weather_followup_kind_hook(user_message)
    weather_request = is_weather_question_hook(user_message)
    weather_location = (
        extract_weather_location_from_message_hook(user_message) if weather_request else None
    )
    should_use_weather_followup = (
        weather_followup_kind is not None and weather_followup_is_active_hook(weather_context)
    )
    should_accept_pending_offer = weather_context_has_pending_lookup_hook(
        weather_context
    ) and should_accept_pending_weather_offer(user_message)
    should_treat_as_location_reply = weather_context_is_waiting_for_location_hook(
        weather_context
    ) and bool(user_message.strip())

    if not (
        (
            weather_request
            or should_use_weather_followup
            or should_accept_pending_offer
            or should_treat_as_location_reply
        )
        and not explicit_weather_investigation
        and not code_draft
        and not writing_draft
    ):
        return None

    location_query = ""
    try:
        if should_use_weather_followup and isinstance(weather_context, dict):
            has_followup_data = (
                weather_followup_kind == "hourly" and bool(weather_context.get("hourly"))
            ) or (
                weather_followup_kind in {"7_day", "14_day", "15_day", "weekend"}
                and bool(weather_context.get("daily"))
            )
            if not has_followup_data:
                location_query = str(weather_context.get("location_query") or "").strip()
                if not location_query:
                    raise RuntimeError(
                        "I lost the active weather location, so please tell me which place to check again."
                    )
                snapshot = await lookup_weather_followup(location_query, weather_followup_kind)
                refreshed_weather = snapshot.to_session_payload()
                refreshed_weather["awaiting_location"] = False
                refreshed_weather["pending_lookup"] = {"location_query": location_query}
                refreshed_weather["retrieved_at_ms"] = int(time.time() * 1000)
                return {
                    "answer": weather_answer_for_followup_hook(
                        refreshed_weather,
                        followup_kind=weather_followup_kind or "7_day",
                    ),
                    "status": f"ok:weather_{weather_followup_kind}",
                    "weather_context": refreshed_weather,
                }
            return {
                "answer": weather_answer_for_followup_hook(
                    weather_context,
                    followup_kind=weather_followup_kind or "7_day",
                ),
                "status": f"ok:weather_{weather_followup_kind}",
                "weather_context": {**weather_context, "followup_active": True},
            }

        if weather_request and weather_location:
            location_query = weather_location
        elif should_accept_pending_offer and isinstance(weather_context, dict):
            pending_lookup = weather_context.get("pending_lookup")
            if isinstance(pending_lookup, dict):
                location_query = str(pending_lookup.get("location_query") or "").strip()
        elif should_treat_as_location_reply:
            location_query = normalize_weather_location_candidate_hook(user_message)

        if not location_query:
            return {
                "answer": "Which location should I check?",
                "status": "weather_missing_location",
                "weather_context": {
                    "awaiting_location": True,
                    "followup_active": False,
                },
            }

        snapshot = await lookup_weather(location_query)
        session_weather = snapshot.to_session_payload()
        session_weather["awaiting_location"] = False
        session_weather["pending_lookup"] = {"location_query": location_query}
        session_weather["retrieved_at_ms"] = int(time.time() * 1000)
        return {
            "answer": format_current_weather_answer(snapshot),
            "status": "ok:weather_current",
            "weather_context": session_weather,
        }
    except RuntimeError as exc:
        return {
            "answer": (
                "I couldn’t complete a structured live weather lookup, so I won’t guess at current conditions. "
                f"{exc}"
            ),
            "status": "weather_lookup_failed",
            "weather_context": {
                "pending_lookup": {"location_query": location_query} if location_query else None,
                "followup_active": False,
            },
        }
