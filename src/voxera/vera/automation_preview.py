"""Automation preview drafting, revision, and submission for Vera.

This module lets Vera draft, revise, and submit automation definitions
conversationally. An automation preview is a governed preview shape that
describes a *future* queue submission — it does not execute anything.

Architecture rules:
- Vera authors previews; submit saves a durable automation definition.
- Submit does NOT emit a queue job. Execution happens only through the
  automation runner -> queue path.
- The queue remains the execution boundary.
- ``recurring_cron`` and ``watch_path`` trigger kinds are stored but the
  preview must note that runtime support is not yet active.

Preview shape:
    {
        "preview_type": "automation_definition",
        "title": str,
        "description": str,           # optional
        "trigger_kind": str,
        "trigger_config": dict,
        "payload_template": dict,
        "enabled": True,
        "created_from": "vera",
        "explanation": str,            # operator-facing summary
    }
"""

from __future__ import annotations

import datetime
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, cast

from ..automation.models import AutomationDefinition, AutomationTriggerKind
from ..automation.store import save_automation_definition

# ---------------------------------------------------------------------------
# Intent detection — does the user want to create an automation?
# ---------------------------------------------------------------------------

# Strong timing patterns — these carry enough schedule/deferred context on their own.
_STRONG_SCHEDULE_PATTERNS = (
    # "every X minutes/hours/seconds"
    r"\bevery\s+\d+\s*(?:second|sec|minute|min|hour|hr|day)s?\b",
    # "every hour / every minute / every day"
    r"\bevery\s+(?:hour|minute|second|day)\b",
    # "in X minutes/hours/seconds"
    r"\bin\s+\d+\s*(?:second|sec|minute|min|hour|hr|day)s?\b",
    # "every day at" / "daily at"
    r"\b(?:every\s+day|daily)\s+at\b",
    # "every morning / every evening / every night"
    r"\bevery\s+(?:morning|evening|night)\b",
    # "after X minutes"
    r"\bafter\s+\d+\s*(?:second|sec|minute|min|hour|hr)s?\b",
)

# Weak keywords — only count as automation intent when accompanied by a
# concrete timing pattern, so bare "schedule uptime check" does not
# accidentally hijack a normal preview flow.
_WEAK_AUTOMATION_KEYWORDS_RE = re.compile(
    r"\b(?:schedule|automate|automation|recurring|repeat(?:ing)?)\b",
    re.IGNORECASE,
)

# "at 8 AM" is also weak without an explicit "every day" — treat it as
# strong only when _combined_ with an automation keyword or other signal.
_AT_TIME_INTENT_RE = re.compile(
    r"\bat\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",
    re.IGNORECASE,
)

_STRONG_SCHEDULE_RE = re.compile("|".join(_STRONG_SCHEDULE_PATTERNS), re.IGNORECASE)

# Payload target patterns — what should the automation do?
_PAYLOAD_ACTION_PATTERNS = (
    r"\b(?:run|execute|trigger|launch)\s+(\w+)",
    r"\b(?:write|save|create)\s+(?:a\s+)?(?:note|file|reminder)",
    r"\b(?:run)\s+(?:system\s+)?(?:diagnostics|inspect|health)",
    r"\b(?:check|inspect|monitor)\s+(?:system|disk|memory|cpu|load)",
)

_IMMEDIATE_EXECUTION_PATTERNS = (
    r"^(?:do\s+it|go\s+ahead|proceed|yes|ok|submit|run\s+it)\s*[.!?]*$",
    r"\bright\s+now\b",
    r"\bimmediately\b",
    r"\bthis\s+instant\b",
)

_IMMEDIATE_RE = re.compile("|".join(_IMMEDIATE_EXECUTION_PATTERNS), re.IGNORECASE)

# Trigger kind-specific parsing
_EVERY_INTERVAL_RE = re.compile(
    r"\bevery\s+(\d+)\s*(second|sec|minute|min|hour|hr|day)s?\b", re.IGNORECASE
)
_EVERY_UNIT_RE = re.compile(r"\bevery\s+(second|minute|hour|day)\b", re.IGNORECASE)
_IN_DELAY_RE = re.compile(r"\bin\s+(\d+)\s*(second|sec|minute|min|hour|hr|day)s?\b", re.IGNORECASE)
_AFTER_DELAY_RE = re.compile(
    r"\bafter\s+(\d+)\s*(second|sec|minute|min|hour|hr|day)s?\b", re.IGNORECASE
)
_AT_TIME_RE = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)
_EVERY_MORNING_RE = re.compile(r"\bevery\s+(morning|evening|night)\b", re.IGNORECASE)
_DAILY_AT_RE = re.compile(
    r"\b(?:every\s+day|daily)\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
    re.IGNORECASE,
)

_UNIT_TO_MS = {
    "second": 1_000,
    "sec": 1_000,
    "minute": 60_000,
    "min": 60_000,
    "hour": 3_600_000,
    "hr": 3_600_000,
    "day": 86_400_000,
}

_PERIOD_OF_DAY_HOUR = {
    "morning": 8,
    "evening": 18,
    "night": 21,
}


# ---------------------------------------------------------------------------
# Clarification result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AutomationClarification:
    """Returned when the user's request needs more information."""

    question: str


# ---------------------------------------------------------------------------
# Preview result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AutomationPreview:
    """A fully formed automation preview ready for user review."""

    preview: dict[str, Any]
    explanation: str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_automation_authoring_intent(message: str) -> bool:
    """Return True when the message looks like a request to create a
    scheduled/deferred automation rather than an immediate action.

    A strong timing pattern ("every 30 minutes", "in 20 minutes") is
    sufficient on its own. A weak keyword ("schedule", "automate") only
    counts when paired with a concrete timing pattern or a clear "at X AM"
    signal, so bare phrases like "schedule uptime check" do not hijack
    the normal preview flow.
    """
    text = message.strip()
    if not text:
        return False
    # Must NOT be a pure immediate-execution confirmation
    if _IMMEDIATE_RE.fullmatch(text):
        return False
    # Strong timing pattern alone is sufficient
    if _STRONG_SCHEDULE_RE.search(text):
        return True
    # Weak keyword + "at X AM/PM" is sufficient
    return bool(_WEAK_AUTOMATION_KEYWORDS_RE.search(text) and _AT_TIME_INTENT_RE.search(text))


def draft_automation_preview(
    message: str,
    *,
    active_preview: dict[str, Any] | None = None,
) -> AutomationPreview | AutomationClarification | None:
    """Attempt to draft an automation preview from a user message.

    Returns:
        AutomationPreview — a fully formed preview ready for review.
        AutomationClarification — a focused clarifying question.
        None — the message is not an automation-authoring request.
    """
    text = message.strip()
    if not text:
        return None

    if not is_automation_authoring_intent(text):
        return None

    # If we're revising an existing automation preview, delegate
    if active_preview is not None and _is_automation_preview(active_preview):
        return revise_automation_preview(text, active_preview)

    # Parse trigger
    trigger = _parse_trigger(text)
    if trigger is None:
        return AutomationClarification(
            question=(
                "I can see you want to schedule something, but I'm not sure "
                "about the timing. Could you clarify the schedule? For example:\n"
                '- "every 30 minutes"\n'
                '- "in 20 minutes"\n'
                '- "every day at 8 AM"'
            )
        )

    # Parse payload
    payload = _parse_payload(text)
    if payload is None:
        return AutomationClarification(
            question=(
                "I understand the schedule, but what should the automation do? "
                "For example:\n"
                '- "run system_inspect"\n'
                '- "write a reminder note that says check the lab"\n'
                '- "run diagnostics"'
            )
        )

    title = _infer_title(text, trigger, payload)
    explanation = _build_explanation(trigger, payload, title)

    preview: dict[str, Any] = {
        "preview_type": "automation_definition",
        "title": title,
        "description": "",
        "trigger_kind": trigger["kind"],
        "trigger_config": trigger["config"],
        "payload_template": payload,
        "enabled": True,
        "created_from": "vera",
        "explanation": explanation,
    }

    return AutomationPreview(preview=preview, explanation=explanation)


def revise_automation_preview(
    message: str,
    active_preview: dict[str, Any],
) -> AutomationPreview | AutomationClarification | None:
    """Revise an existing automation preview based on a follow-up message.

    Supports:
    - "Make it every hour instead."
    - "Change the note text."
    - "Use system_inspect instead."
    - "Name it Morning Diagnostics."
    - "Actually make it run in 20 minutes."
    """
    if not _is_automation_preview(active_preview):
        return None

    text = message.strip()
    lowered = text.lower()

    revised = dict(active_preview)

    # ── Title revision ────────────────────────────────────────────────────
    title_match = re.search(
        r"\b(?:name|call|title|rename)\s+(?:it|this|that)\s+(.+)",
        text,
        re.IGNORECASE,
    )
    if title_match:
        new_title = title_match.group(1).strip(" .\"'")
        if new_title:
            revised["title"] = new_title
            revised["explanation"] = _build_explanation(
                {"kind": revised["trigger_kind"], "config": revised["trigger_config"]},
                revised["payload_template"],
                new_title,
            )
            return AutomationPreview(preview=revised, explanation=revised["explanation"])

    # ── Trigger revision ──────────────────────────────────────────────────
    new_trigger = _parse_trigger(text)
    if new_trigger is not None:
        revised["trigger_kind"] = new_trigger["kind"]
        revised["trigger_config"] = new_trigger["config"]
        revised["explanation"] = _build_explanation(
            new_trigger,
            revised["payload_template"],
            str(revised.get("title") or ""),
        )
        return AutomationPreview(preview=revised, explanation=revised["explanation"])

    # ── Payload revision ──────────────────────────────────────────────────
    # "use X instead" / "run X instead" / "change to X"
    new_payload = _parse_payload(text)
    if new_payload is not None:
        revised["payload_template"] = new_payload
        revised["explanation"] = _build_explanation(
            {"kind": revised["trigger_kind"], "config": revised["trigger_config"]},
            new_payload,
            str(revised.get("title") or ""),
        )
        return AutomationPreview(preview=revised, explanation=revised["explanation"])

    # ── Content revision for write_file payloads ──────────────────────────
    content_match = re.search(
        r"\b(?:change|update|set)\s+(?:the\s+)?(?:note\s+)?(?:text|content|message)\s+(?:to\s+)?[\"'](.+?)[\"']",
        text,
        re.IGNORECASE,
    )
    if content_match is None:
        content_match = re.search(
            r"\b(?:says?|saying|with\s+(?:the\s+)?(?:text|content|message))\s+[\"']?(.+?)[\"']?\s*$",
            text,
            re.IGNORECASE,
        )
    if content_match:
        new_content = content_match.group(1).strip()
        pt = revised.get("payload_template")
        if isinstance(pt, dict) and "write_file" in pt:
            wf = dict(pt["write_file"])
            wf["content"] = new_content
            revised["payload_template"] = {**pt, "write_file": wf}
            revised["explanation"] = _build_explanation(
                {"kind": revised["trigger_kind"], "config": revised["trigger_config"]},
                revised["payload_template"],
                str(revised.get("title") or ""),
            )
            return AutomationPreview(preview=revised, explanation=revised["explanation"])

    # ── Enable/disable ────────────────────────────────────────────────────
    if re.search(r"\bdisable\b", lowered):
        revised["enabled"] = False
        return AutomationPreview(
            preview=revised,
            explanation=revised.get("explanation", ""),
        )
    if re.search(r"\benable\b", lowered):
        revised["enabled"] = True
        return AutomationPreview(
            preview=revised,
            explanation=revised.get("explanation", ""),
        )

    # ── Description revision ──────────────────────────────────────────────
    desc_match = re.search(
        r"\b(?:description|describe)\s+(?:as|to|:)\s*(.+)",
        text,
        re.IGNORECASE,
    )
    if desc_match:
        revised["description"] = desc_match.group(1).strip(" .\"'")
        return AutomationPreview(
            preview=revised,
            explanation=revised.get("explanation", ""),
        )

    return None


def _is_automation_preview(preview: dict[str, Any]) -> bool:
    """Return True when the preview dict is an automation definition preview."""
    return isinstance(preview, dict) and preview.get("preview_type") == "automation_definition"


def is_automation_preview(preview: dict[str, Any] | None) -> bool:
    """Public check — is this preview an automation definition preview?"""
    if not isinstance(preview, dict):
        return False
    return _is_automation_preview(preview)


# ---------------------------------------------------------------------------
# Submit — save a durable automation definition (NO queue job emitted)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AutomationSubmitResult:
    """Result of submitting an automation preview."""

    automation_id: str
    definition_path: str
    ack: str


def submit_automation_preview(
    preview: dict[str, Any],
    queue_root: Any,
) -> AutomationSubmitResult:
    """Save an automation preview as a durable automation definition.

    This does NOT emit a queue job. The saved definition will be picked up
    by the automation runner on its next evaluation cycle.

    Returns an AutomationSubmitResult with a truthful acknowledgment.
    """
    from pathlib import Path

    queue_root = Path(queue_root)

    automation_id = _generate_automation_id(str(preview.get("title") or ""))

    now_ms = int(time.time() * 1000)
    definition = AutomationDefinition(
        id=automation_id,
        title=str(preview.get("title") or "Untitled Automation"),
        description=str(preview.get("description") or ""),
        enabled=preview.get("enabled", True) is not False,
        trigger_kind=cast(AutomationTriggerKind, str(preview.get("trigger_kind") or "delay")),
        trigger_config=preview.get("trigger_config") or {},
        payload_template=preview.get("payload_template") or {},
        created_from="vera",
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
    )

    saved_path = save_automation_definition(definition, queue_root)

    trigger_desc = _human_trigger_description(definition.trigger_kind, definition.trigger_config)
    ack = (
        f'Saved automation definition "{definition.title}" (id: {automation_id}).\n'
        f"Trigger: {trigger_desc}.\n"
        "This automation definition has been saved. It has NOT been executed yet. "
        "The automation runner will evaluate it on its next cycle and submit "
        "a queue job when the trigger condition is met. "
        "All execution goes through the automation runner and queue."
    )

    return AutomationSubmitResult(
        automation_id=automation_id,
        definition_path=str(saved_path),
        ack=ack,
    )


def describe_saved_automation(
    preview: dict[str, Any],
    submit_result: AutomationSubmitResult | None = None,
) -> str:
    """Build a truthful description of a saved automation for post-submit
    continuity. Used to answer "what did you save?" or "show me that automation"."""
    title = str(preview.get("title") or "Untitled")
    trigger_kind = str(preview.get("trigger_kind") or "unknown")
    trigger_config = preview.get("trigger_config") or {}
    trigger_desc = _human_trigger_description(trigger_kind, trigger_config)
    payload = preview.get("payload_template") or {}

    lines = [f"Automation: {title}"]
    if submit_result:
        lines.append(f"ID: {submit_result.automation_id}")
    lines.append(f"Trigger: {trigger_desc}")
    lines.append(f"Enabled: {preview.get('enabled', True)}")

    # Describe what it does
    goal = str(payload.get("goal") or "")
    mission = str(payload.get("mission_id") or "")
    if goal:
        lines.append(f"Action: {goal}")
    elif mission:
        lines.append(f"Action: run mission {mission}")

    wf = payload.get("write_file")
    if isinstance(wf, dict):
        wf_path = str(wf.get("path") or "")
        wf_content = str(wf.get("content") or "")
        if wf_path:
            lines.append(f"Writes to: {wf_path}")
        if wf_content:
            lines.append(f"Content: {wf_content[:100]}")

    lines.append("")
    lines.append(
        "This is a saved definition. It has not executed yet. "
        "The automation runner will submit a queue job when the "
        "trigger condition is met."
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_trigger(text: str) -> dict[str, Any] | None:
    """Parse a trigger specification from natural language.

    Returns {"kind": ..., "config": ...} or None.
    """
    # "every day at 8 AM" / "daily at 14:00"
    daily_match = _DAILY_AT_RE.search(text)
    if daily_match:
        hour, minute, _ampm = _parse_time_parts(daily_match)
        return {
            "kind": "recurring_interval",
            "config": {"interval_ms": 86_400_000},
            "_display_hint": f"daily at {hour:02d}:{minute:02d}",
        }

    # "every morning / evening / night"
    period_match = _EVERY_MORNING_RE.search(text)
    if period_match:
        period = period_match.group(1).lower()
        return {
            "kind": "recurring_interval",
            "config": {"interval_ms": 86_400_000},
            "_display_hint": f"every {period}",
        }

    # "every 30 minutes" / "every 2 hours"
    interval_match = _EVERY_INTERVAL_RE.search(text)
    if interval_match:
        count = int(interval_match.group(1))
        unit = interval_match.group(2).lower()
        ms = count * _UNIT_TO_MS.get(unit, 60_000)
        return {
            "kind": "recurring_interval",
            "config": {"interval_ms": ms},
        }

    # "every hour" / "every minute" / "every day"
    unit_match = _EVERY_UNIT_RE.search(text)
    if unit_match:
        unit = unit_match.group(1).lower()
        ms = _UNIT_TO_MS.get(unit, 3_600_000)
        return {
            "kind": "recurring_interval",
            "config": {"interval_ms": ms},
        }

    # "in 20 minutes" / "in 1 hour"
    delay_match = _IN_DELAY_RE.search(text) or _AFTER_DELAY_RE.search(text)
    if delay_match:
        count = int(delay_match.group(1))
        unit = delay_match.group(2).lower()
        ms = count * _UNIT_TO_MS.get(unit, 60_000)
        return {
            "kind": "delay",
            "config": {"delay_ms": ms},
        }

    # "at 8 AM" (without "every day")
    time_match = _AT_TIME_RE.search(text)
    if time_match:
        hour, minute, _ = _parse_time_parts(time_match)
        # Compute target time today; if already past, schedule tomorrow
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += datetime.timedelta(days=1)
        target_ms = int(target.timestamp() * 1000)
        return {
            "kind": "once_at",
            "config": {"run_at_ms": target_ms},
            "_display_hint": f"once at {hour:02d}:{minute:02d} UTC",
        }

    return None


def _parse_time_parts(match: re.Match[str]) -> tuple[int, int, str | None]:
    """Extract hour, minute, am/pm from a time regex match."""
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    ampm = (match.group(3) or "").lower() or None
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    return hour, minute, ampm


def _parse_payload(text: str) -> dict[str, Any] | None:
    """Parse the action/payload from natural language.

    Returns a payload_template dict or None.
    """
    lowered = text.lower()

    # "run system_inspect" / "run diagnostics" / "execute health_check"
    run_match = re.search(
        r"\b(?:run|execute|trigger|launch)\s+(\w+(?:_\w+)*)\b",
        text,
        re.IGNORECASE,
    )
    if run_match:
        target = run_match.group(1)
        # Check if it's a diagnostics keyword
        if target.lower() in {
            "diagnostics",
            "system_diagnostics",
            "health_check",
            "system_inspect",
        }:
            return {
                "goal": f"run {target}",
                "mission_id": "system_diagnostics",
            }
        return {"goal": f"run {target}"}

    # "write a reminder/note that says ..."
    write_match = re.search(
        r"\b(?:write|save|create)\s+(?:a\s+)?(?:reminder\s+)?(?:note|file|reminder)\s+(?:that\s+)?(?:says?|saying|with\s+(?:the\s+)?(?:text|content|message))\s+(.+)",
        text,
        re.IGNORECASE,
    )
    if write_match:
        content = write_match.group(1).strip(" .\"'")
        return {
            "goal": "write a reminder note",
            "write_file": {
                "path": f"~/VoxeraOS/notes/reminder-{int(time.time())}.txt",
                "content": content,
                "mode": "overwrite",
            },
        }

    # "write a note about X"
    note_about = re.search(
        r"\b(?:write|save|create)\s+(?:a\s+)?(?:reminder\s+)?note\s+(?:about|for|regarding)\s+(.+)",
        text,
        re.IGNORECASE,
    )
    if note_about:
        topic = note_about.group(1).strip(" .\"'")
        return {
            "goal": f"write a note about {topic}",
            "write_file": {
                "path": f"~/VoxeraOS/notes/note-{int(time.time())}.txt",
                "content": f"Reminder: {topic}",
                "mode": "overwrite",
            },
        }

    # "save a note every morning" — note without specific content
    if re.search(r"\b(?:note|reminder)\b", lowered) and re.search(
        r"\b(?:save|write|create)\b", lowered
    ):
        return {
            "goal": "save a note",
            "write_file": {
                "path": f"~/VoxeraOS/notes/note-{int(time.time())}.txt",
                "content": "Scheduled note",
                "mode": "overwrite",
            },
        }

    # "check disk usage" / "inspect system health"
    if re.search(
        r"\b(?:check|inspect|monitor)\s+(?:system\s+)?(?:diagnostics|health|disk|memory|cpu|load)\b",
        lowered,
    ):
        return {
            "goal": "run bounded host diagnostics",
            "mission_id": "system_diagnostics",
        }

    # "run system diagnostics" as a fallback
    if re.search(r"\b(?:system\s+)?diagnostics\b", lowered):
        return {
            "goal": "run system diagnostics",
            "mission_id": "system_diagnostics",
        }

    return None


def _infer_title(
    text: str,
    trigger: dict[str, Any],
    payload: dict[str, Any],
) -> str:
    """Infer a reasonable title for the automation."""
    goal = str(payload.get("goal") or "")
    kind = trigger.get("kind", "")

    # Use goal as a base
    if goal:
        words = goal.strip().split()
        title_base = " ".join(w.capitalize() for w in words[:6])
    else:
        title_base = "Scheduled Task"

    display_hint = trigger.get("_display_hint")
    if display_hint:
        return f"{title_base} ({display_hint})"

    if kind == "recurring_interval":
        interval_ms = trigger.get("config", {}).get("interval_ms", 0)
        interval_desc = _ms_to_human(interval_ms)
        return f"{title_base} (every {interval_desc})"
    if kind == "delay":
        delay_ms = trigger.get("config", {}).get("delay_ms", 0)
        delay_desc = _ms_to_human(delay_ms)
        return f"{title_base} (in {delay_desc})"
    if kind == "once_at":
        return f"{title_base} (one-time)"

    return title_base


def _build_explanation(
    trigger: dict[str, Any],
    payload: dict[str, Any],
    title: str,
) -> str:
    """Build an operator-facing explanation of the automation."""
    kind = trigger.get("kind", "")
    config = trigger.get("config", {})
    goal = str(payload.get("goal") or "scheduled task")

    lines = [f'This will save an automation definition: "{title}".']

    # Trigger description
    trigger_desc = _human_trigger_description(kind, config)
    display_hint = trigger.get("_display_hint")
    if display_hint:
        lines.append(f"Schedule: {display_hint}.")
    else:
        lines.append(f"Schedule: {trigger_desc}.")

    # Action description
    lines.append(f"Action: {goal}.")

    wf = payload.get("write_file")
    if isinstance(wf, dict):
        wf_path = str(wf.get("path") or "")
        wf_content = str(wf.get("content") or "")
        if wf_path:
            lines.append(f"Writes to: {wf_path}")
        if wf_content:
            lines.append(f'Content: "{wf_content[:80]}"')

    lines.append("")
    lines.append(
        "Submitting this saves the automation definition. "
        "It does NOT execute immediately. "
        "The automation runner will submit a queue job when the "
        "trigger condition is met. All execution goes through the "
        "automation runner and queue."
    )

    return "\n".join(lines)


def _human_trigger_description(kind: str, config: dict[str, Any]) -> str:
    """Return a human-readable trigger description."""
    if kind == "delay":
        ms = config.get("delay_ms", 0)
        return f"once after {_ms_to_human(ms)}"
    if kind == "once_at":
        ms = config.get("run_at_ms", 0)
        if ms > 0:
            dt = datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc)
            return f"once at {dt.strftime('%Y-%m-%d %H:%M UTC')}"
        return "once at a scheduled time"
    if kind == "recurring_interval":
        ms = config.get("interval_ms", 0)
        return f"every {_ms_to_human(ms)}"
    if kind == "recurring_cron":
        cron = config.get("cron", "")
        return f"cron: {cron} (runtime support not yet active)"
    if kind == "watch_path":
        path = config.get("path", "")
        event = config.get("event", "created")
        return f"watch {path} for {event} events (runtime support not yet active)"
    return f"unknown trigger: {kind}"


def _ms_to_human(ms: int) -> str:
    """Convert milliseconds to a human-readable duration."""
    if ms <= 0:
        return "0 seconds"
    if ms < 60_000:
        secs = ms // 1_000
        return f"{secs} second{'s' if secs != 1 else ''}"
    if ms < 3_600_000:
        mins = ms // 60_000
        return f"{mins} minute{'s' if mins != 1 else ''}"
    if ms < 86_400_000:
        hours = ms // 3_600_000
        return f"{hours} hour{'s' if hours != 1 else ''}"
    days = ms // 86_400_000
    return f"{days} day{'s' if days != 1 else ''}"


def _generate_automation_id(title: str) -> str:
    """Generate a filesystem-safe automation ID from a title."""
    # Slugify the title
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.strip().lower())
    slug = slug.strip("-")[:60]
    if not slug:
        slug = "automation"
    # Add a short random suffix for uniqueness
    suffix = secrets.token_hex(4)
    return f"{slug}-{suffix}"
