"""Reusable time-context helpers for conversational time awareness.

This module provides deterministic, bounded helpers that let Vera and other
AI instruction surfaces reason about:

- current local datetime and timezone
- elapsed time since a past event
- time remaining until a future event
- relative-day classification (today, yesterday, etc.)

All helpers operate on system-local time and UTC. No geolocation or
IP-based location lookup is performed. Timezone information comes
from the system clock only.

Truthfulness rules:
- Known exact timestamps produce exact phrasing.
- Inferred or projected timestamps are clearly framed as approximations.
- The helpers never fabricate timestamps or execution history.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Current time context
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimeContext:
    """Structured snapshot of current system time."""

    local_iso: str  # e.g. "2025-06-15 14:32:07"
    utc_iso: str  # e.g. "2025-06-15 18:32:07"
    timezone_name: str  # e.g. "America/New_York" or "UTC"
    utc_offset: str  # e.g. "UTC-04:00"
    epoch_ms: int  # current epoch milliseconds
    day_of_week: str  # e.g. "Sunday"
    date_human: str  # e.g. "Sunday, June 15, 2025"


def current_time_context(*, now: datetime | None = None) -> TimeContext:
    """Return a structured snapshot of the current system time.

    The ``now`` parameter is for testing determinism only. In production
    it defaults to the live system clock with local timezone.
    """
    if now is None:
        now = datetime.now(tz=timezone.utc).astimezone()

    utc_now = now.astimezone(timezone.utc)
    tz_name = _timezone_name(now)
    offset = _format_utc_offset(now)

    return TimeContext(
        local_iso=now.strftime("%Y-%m-%d %H:%M:%S"),
        utc_iso=utc_now.strftime("%Y-%m-%d %H:%M:%S"),
        timezone_name=tz_name,
        utc_offset=offset,
        epoch_ms=int(now.timestamp() * 1000),
        day_of_week=now.strftime("%A"),
        date_human=now.strftime("%A, %B %-d, %Y"),
    )


def current_time_summary(*, now: datetime | None = None) -> str:
    """Return a concise human-readable current-time string for conversation.

    Example: "It's Sunday, June 15, 2025 at 2:32 PM (America/New_York, UTC-04:00)."
    """
    # Capture once so the context and the time string cannot straddle a
    # second/minute boundary.
    if now is None:
        now = datetime.now(tz=timezone.utc).astimezone()
    ctx = current_time_context(now=now)
    time_str = now.strftime("%-I:%M %p")
    return f"It's {ctx.date_human} at {time_str} ({ctx.timezone_name}, {ctx.utc_offset})."


# ---------------------------------------------------------------------------
# Elapsed time formatting
# ---------------------------------------------------------------------------


def format_elapsed(ms: int) -> str:
    """Format a millisecond duration as natural elapsed-time phrasing.

    Examples:
    - 3000 -> "3 seconds ago"
    - 150000 -> "about 2 minutes ago"
    - 7200000 -> "about 2 hours ago"
    - 90000000 -> "about 1 day ago"
    """
    if ms < 0:
        return "in the future"
    if ms < 1_000:
        return "just now"
    if ms < 60_000:
        secs = ms // 1_000
        return f"{secs} second{'s' if secs != 1 else ''} ago"
    if ms < 3_600_000:
        mins = ms // 60_000
        return f"about {mins} minute{'s' if mins != 1 else ''} ago"
    if ms < 86_400_000:
        hours = ms // 3_600_000
        return f"about {hours} hour{'s' if hours != 1 else ''} ago"
    days = ms // 86_400_000
    return f"about {days} day{'s' if days != 1 else ''} ago"


def format_elapsed_since_ms(past_epoch_ms: int, *, now_ms: int | None = None) -> str:
    """Format elapsed time since a past epoch-ms timestamp.

    If ``now_ms`` is not provided, uses the current system clock.
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    delta = now_ms - past_epoch_ms
    return format_elapsed(delta)


# ---------------------------------------------------------------------------
# Time-until formatting
# ---------------------------------------------------------------------------


def format_time_until(ms: int) -> str:
    """Format a millisecond duration as natural time-until phrasing.

    Examples:
    - 3000 -> "in about 3 seconds"
    - 150000 -> "in about 2 minutes"
    - 7200000 -> "in about 2 hours"
    - 90000000 -> "in about 1 day"
    """
    if ms < 0:
        return "already past"
    if ms < 1_000:
        return "any moment now"
    if ms < 60_000:
        secs = ms // 1_000
        return f"in about {secs} second{'s' if secs != 1 else ''}"
    if ms < 3_600_000:
        mins = ms // 60_000
        return f"in about {mins} minute{'s' if mins != 1 else ''}"
    if ms < 86_400_000:
        hours = ms // 3_600_000
        return f"in about {hours} hour{'s' if hours != 1 else ''}"
    days = ms // 86_400_000
    return f"in about {days} day{'s' if days != 1 else ''}"


def format_time_until_ms(future_epoch_ms: int, *, now_ms: int | None = None) -> str:
    """Format time remaining until a future epoch-ms timestamp.

    If ``now_ms`` is not provided, uses the current system clock.
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    delta = future_epoch_ms - now_ms
    return format_time_until(delta)


# ---------------------------------------------------------------------------
# Relative-day classification
# ---------------------------------------------------------------------------


def classify_relative_day(epoch_ms: int, *, now: datetime | None = None) -> str:
    """Classify an epoch-ms timestamp relative to the current local day.

    Returns one of: "today", "yesterday", "tomorrow", or a date string
    like "June 15, 2025".
    """
    if now is None:
        now = datetime.now(tz=timezone.utc).astimezone()
    target = datetime.fromtimestamp(epoch_ms / 1000, tz=now.tzinfo)
    today = now.date()
    target_date = target.date()
    diff = (target_date - today).days
    if diff == 0:
        return "today"
    if diff == -1:
        return "yesterday"
    if diff == 1:
        return "tomorrow"
    return target.strftime("%B %d, %Y")


# ---------------------------------------------------------------------------
# Timestamp description for automation lifecycle
# ---------------------------------------------------------------------------


def describe_timestamp_ms(
    epoch_ms: int,
    *,
    label: str = "",
    now_ms: int | None = None,
    now: datetime | None = None,
) -> str:
    """Describe an epoch-ms timestamp with both absolute and relative terms.

    Example: "Last run: today at 2:15 PM (about 47 minutes ago)"

    If ``label`` is provided, it is used as a prefix (e.g. "Last run").
    """
    if now is None:
        now = datetime.now(tz=timezone.utc).astimezone()
    if now_ms is None:
        now_ms = int(now.timestamp() * 1000)

    target = datetime.fromtimestamp(epoch_ms / 1000, tz=now.tzinfo)
    day = classify_relative_day(epoch_ms, now=now)
    time_str = target.strftime("%-I:%M %p")

    delta_ms = now_ms - epoch_ms
    relative = format_elapsed(delta_ms) if delta_ms >= 0 else format_time_until(-delta_ms)
    absolute = f"{day} at {time_str}"

    prefix = f"{label}: " if label else ""
    return f"{prefix}{absolute} ({relative})"


def describe_next_run_ms(
    next_run_at_ms: int,
    *,
    now_ms: int | None = None,
    now: datetime | None = None,
) -> str:
    """Describe when the next run is expected.

    Example: "Next run: tomorrow at 8:00 AM (in about 14 hours)"
    """
    return describe_timestamp_ms(next_run_at_ms, label="Next run", now_ms=now_ms, now=now)


def describe_last_run_ms(
    last_run_at_ms: int,
    *,
    now_ms: int | None = None,
    now: datetime | None = None,
) -> str:
    """Describe when the last run happened.

    Example: "Last run: today at 2:15 PM (about 47 minutes ago)"
    """
    return describe_timestamp_ms(last_run_at_ms, label="Last run", now_ms=now_ms, now=now)


# ---------------------------------------------------------------------------
# Time-question intent detection
# ---------------------------------------------------------------------------

# Patterns are end-anchored so the phrase must be the complete question, not
# a substring of a larger request like "what date did you save that?".
# Time-question detection runs FIRST in early-exit dispatch, so it must not
# hijack lifecycle/drafting questions.
#
# ``_END`` matches optional trailing punctuation and whitespace then end of string.
_END = r"\s*[.?!]*\s*$"

_TIME_QUESTION_PATTERNS = (
    # "what time is it" / "what time is it now" / "what time is it right now" /
    # "what time is it here" / "what time is it on this box|machine|system"
    r"\bwhat\s+time\s+is\s+it"
    r"(?:\s+(?:right\s+)?now|\s+here|\s+on\s+this\s+(?:box|machine|system))?" + _END,
    # "what's the time" / "what is the time" / "what's the current/local time"
    r"\bwhat(?:\s+is|'s|s)\s+the\s+(?:current\s+|local\s+)?time" + _END,
    # "tell me the time" / "tell me the current/local time"
    r"\btell\s+me\s+the\s+(?:current\s+|local\s+)?time" + _END,
    # bare "current time" / "local time"
    r"^\s*(?:current|local)\s+time" + _END,
    # "what day/date is it" / "what day/date is it today" / "what day/date is today"
    r"\bwhat\s+(?:day|date)\s+is\s+(?:it(?:\s+today)?|today)" + _END,
    # "what's the date" / "what is the date" / "what's today's date"
    r"\bwhat(?:\s+is|'s|s)\s+(?:the|today'?s?)\s+date" + _END,
    # "tell me the date" / "tell me today's date"
    r"\btell\s+me\s+(?:the|today'?s?)\s+date" + _END,
    # "today's date" (bare)
    r"^\s*today'?s?\s+date" + _END,
    # "what day of the week is it"
    r"\bwhat\s+day\s+of\s+the\s+week\s+is\s+it" + _END,
    # "what timezone" / "what tz" / "what's the timezone"
    r"\bwhat(?:\s+is|'s|s)?\s+(?:the\s+)?time\s*zone" + _END,
    r"\bwhat\s+tz" + _END,
)


def is_time_question(message: str) -> bool:
    """Return True if the message is a simple time/date/timezone question."""
    text = message.strip()
    if not text:
        return False
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in _TIME_QUESTION_PATTERNS)


def answer_time_question(message: str) -> str | None:
    """Answer a simple time/date/timezone question, or return None.

    Returns a professional, friendly answer grounded in system-local time.
    """
    if not is_time_question(message):
        return None
    return current_time_summary()


# ---------------------------------------------------------------------------
# Conversation context block for prompt injection
# ---------------------------------------------------------------------------


def time_context_block(*, now: datetime | None = None) -> str:
    """Return a structured text block suitable for inclusion in AI prompts.

    This gives the AI access to current time information for answering
    time-sensitive questions without fabricating timestamps.
    """
    ctx = current_time_context(now=now)
    return (
        f"Current time: {ctx.local_iso} {ctx.timezone_name} ({ctx.utc_offset})\n"
        f"Current UTC: {ctx.utc_iso}\n"
        f"Day: {ctx.day_of_week}\n"
        f"Date: {ctx.date_human}\n"
        f"Epoch (ms): {ctx.epoch_ms}"
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _timezone_name(dt: datetime) -> str:
    """Extract a human-readable timezone name from a datetime."""
    tz = dt.tzinfo
    if tz is None:
        return "UTC"
    if isinstance(tz, ZoneInfo):
        return str(tz)
    name = dt.strftime("%Z")
    return name if name else "UTC"


def _format_utc_offset(dt: datetime) -> str:
    """Format the UTC offset as a string like 'UTC+05:30' or 'UTC-04:00'."""
    offset = dt.utcoffset()
    if offset is None:
        return "UTC+00:00"
    total_seconds = int(offset.total_seconds())
    sign = "+" if total_seconds >= 0 else "-"
    total_seconds = abs(total_seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


__all__ = [
    "TimeContext",
    "answer_time_question",
    "classify_relative_day",
    "current_time_context",
    "current_time_summary",
    "describe_last_run_ms",
    "describe_next_run_ms",
    "describe_timestamp_ms",
    "format_elapsed",
    "format_elapsed_since_ms",
    "format_time_until",
    "format_time_until_ms",
    "is_time_question",
    "time_context_block",
]
