"""Conversational lifecycle management for saved automation definitions.

This module lets Vera inspect and manage already-saved automation definitions
through natural conversation. It supports:

- **show** — describe a saved automation from the canonical store
- **enable** / **disable** — mutate the enabled state
- **delete** — remove the definition (history preserved)
- **run_now** — force immediate evaluation through the existing runner
- **history** / **status** — surface run history from the canonical store

Architecture rules:
- Vera manages saved definitions through the existing durable store.
- Any "run it now" action uses the existing automation runner -> queue path.
- The queue remains the execution boundary.
- Vera does not execute payloads directly.
- Ambiguous references fail closed with a clarification request.

Reference resolution priority:
1. Session-stashed last automation preview / saved automation context
2. Exact id match
3. Strong title match (single unambiguous result)
4. Fail closed with clarification if ambiguous or missing
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path
from typing import Any

from ..automation.history import list_history_records
from ..automation.models import AutomationDefinition
from ..automation.runner import process_automation_definition
from ..automation.store import (
    AutomationNotFoundError,
    AutomationStoreError,
    delete_automation_definition,
    list_automation_definitions,
    load_automation_definition,
    save_automation_definition,
)

# ---------------------------------------------------------------------------
# Lifecycle intent classification
# ---------------------------------------------------------------------------


@unique
class LifecycleIntent(Enum):
    """Bounded set of lifecycle actions Vera can perform on saved automations."""

    SHOW = "show"
    ENABLE = "enable"
    DISABLE = "disable"
    DELETE = "delete"
    RUN_NOW = "run_now"
    HISTORY = "history"


# Patterns are evaluated in priority order; first match wins.
# Each tuple: (compiled_regex, intent).

_LIFECYCLE_PATTERNS: list[tuple[re.Pattern[str], LifecycleIntent]] = [
    # --- history / status ---
    (
        re.compile(
            r"\b(?:show\s+(?:me\s+)?(?:the\s+)?(?:run\s+)?history"
            r"|what\s+happened\s+(?:last\s+time|with\s+(?:that|the)\s+automation)"
            r"|(?:did|has)\s+it\s+run"
            r"|(?:did|has)\s+(?:that|the)\s+automation\s+run"
            r"|when\s+did\s+it\s+(?:last\s+)?run"
            r"|show\s+(?:the\s+)?(?:last\s+)?(?:run|execution)"
            r"|run\s+history"
            r"|last\s+run"
            r"|execution\s+history"
            r")\b",
            re.IGNORECASE,
        ),
        LifecycleIntent.HISTORY,
    ),
    # --- run now ---
    (
        re.compile(
            r"\b(?:run\s+it\s+now"
            r"|trigger\s+(?:that|it|the)\s+(?:automation\s+)?now"
            r"|run\s+(?:that|the)\s+automation\s+now"
            r"|force\s+run"
            r"|run\s+now"
            r"|execute\s+it\s+now"
            r"|fire\s+it\s+now"
            r")\b",
            re.IGNORECASE,
        ),
        LifecycleIntent.RUN_NOW,
    ),
    # --- delete ---
    (
        re.compile(
            r"\b(?:delete\s+(?:that|the|this|it)"
            r"|remove\s+(?:that|the|this|it)"
            r"|delete\s+(?:the\s+)?\w+\s+automation"
            r"|remove\s+(?:the\s+)?\w+\s+automation"
            r"|delete\s+automation"
            r"|remove\s+automation"
            r")\b",
            re.IGNORECASE,
        ),
        LifecycleIntent.DELETE,
    ),
    # --- disable ---
    (
        re.compile(
            r"\b(?:disable\s+(?:that|the|this|it)"
            r"|turn\s+off\s+(?:that|the|this|it)"
            r"|disable\s+(?:the\s+)?\w+\s+automation"
            r"|turn\s+off\s+(?:the\s+)?\w+\s+automation"
            r"|disable\s+automation"
            r"|pause\s+(?:that|the|this|it)"
            r"|stop\s+(?:that|the|this|it)"
            r")\b",
            re.IGNORECASE,
        ),
        LifecycleIntent.DISABLE,
    ),
    # --- enable ---
    (
        re.compile(
            r"\b(?:enable\s+(?:that|the|this|it)"
            r"|turn\s+on\s+(?:that|the|this|it)"
            r"|enable\s+(?:the\s+)?\w+\s+automation"
            r"|turn\s+on\s+(?:the\s+)?\w+\s+automation"
            r"|enable\s+automation"
            r"|enable\s+it\s+again"
            r"|re-?enable\s+(?:that|the|this|it)"
            r"|activate\s+(?:that|the|this|it)"
            r")\b",
            re.IGNORECASE,
        ),
        LifecycleIntent.ENABLE,
    ),
    # --- show ---
    (
        re.compile(
            r"\b(?:show\s+(?:me\s+)?(?:that|the|this)\s+(?:saved\s+)?(?:automation|definition)"
            r"|show\s+(?:me\s+)?(?:that|the|this)\s+[\w\s-]+\s+automation"
            r"|what\s+did\s+you\s+save"
            r"|what\s+will\s+it\s+do"
            r"|when\s+will\s+it\s+run"
            r"|describe\s+(?:that|the|this|it)\s*(?:automation)?"
            r"|show\s+(?:me\s+)?(?:that|the)\s+(?:saved\s+)?definition"
            r"|what\s+(?:is|was)\s+(?:that|the)\s+automation"
            r"|what\s+does\s+(?:that|the)\s+automation\s+do"
            r"|tell\s+me\s+about\s+(?:that|the)\s+automation"
            r")\b",
            re.IGNORECASE,
        ),
        LifecycleIntent.SHOW,
    ),
]


def classify_lifecycle_intent(message: str) -> LifecycleIntent | None:
    """Classify whether a message is a lifecycle management request.

    Returns the intent kind or None if the message is not a lifecycle request.
    """
    text = message.strip()
    if not text:
        return None
    for pattern, intent in _LIFECYCLE_PATTERNS:
        if pattern.search(text):
            return intent
    return None


def is_automation_lifecycle_intent(message: str) -> bool:
    """Return True when the message looks like a saved-automation lifecycle request."""
    return classify_lifecycle_intent(message) is not None


# ---------------------------------------------------------------------------
# Reference resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedAutomation:
    """A successfully resolved automation reference."""

    definition: AutomationDefinition
    source: str  # how the reference was resolved


@dataclass(frozen=True)
class AmbiguousAutomation:
    """Multiple automations matched — fail closed with clarification."""

    candidates: list[str]  # list of (id, title) descriptions
    clarification: str


@dataclass(frozen=True)
class AutomationNotResolved:
    """No automation matched the reference."""

    reason: str


def _extract_title_hint(message: str) -> str | None:
    """Try to extract a title hint from the message.

    Looks for patterns like "the reminder automation" or "the X automation".
    """
    # "the <words> automation"
    m = re.search(
        r"\bthe\s+([\w\s-]+?)\s+automation\b",
        message,
        re.IGNORECASE,
    )
    if m:
        hint = m.group(1).strip()
        # Filter out purely pronominal hints
        if hint.lower() not in {"that", "this", "it", "my", "saved"}:
            return hint
    return None


def _extract_explicit_id(message: str) -> str | None:
    """Try to extract an explicit automation id from the message.

    Looks for patterns like "automation <id>" or "id: <id>".
    """
    m = re.search(
        r"\b(?:automation\s+id[:\s]+|id[:\s]+)([\w-]+)\b",
        message,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    return None


def _title_match_score(query: str, title: str) -> float:
    """Return a rough similarity score between a query hint and a title.

    Higher is better. Returns 0.0 for no match.
    """
    q = query.lower().strip()
    t = title.lower().strip()
    if not q or not t:
        return 0.0
    # Exact substring match
    if q in t:
        return 1.0
    # Word overlap
    q_words = set(q.split())
    t_words = set(t.split())
    overlap = q_words & t_words
    if not overlap:
        return 0.0
    return len(overlap) / max(len(q_words), 1)


def resolve_automation_reference(
    message: str,
    *,
    queue_root: Path,
    session_context: dict[str, Any] | None = None,
    last_automation_preview: dict[str, Any] | None = None,
) -> ResolvedAutomation | AmbiguousAutomation | AutomationNotResolved:
    """Resolve a natural-language automation reference to a concrete definition.

    Resolution priority:
    1. Session-stashed last automation context (active_topic: automation:<id>)
    2. Explicit id in the message
    3. Strong title match from the store
    4. Fail closed

    This function never guesses. Ambiguous references fail closed.
    """
    ctx = session_context if isinstance(session_context, dict) else {}

    # --- 1. Try explicit id from message ---
    explicit_id = _extract_explicit_id(message)
    if explicit_id:
        try:
            defn = load_automation_definition(explicit_id, queue_root)
            return ResolvedAutomation(definition=defn, source="explicit_id")
        except (AutomationNotFoundError, AutomationStoreError):
            pass  # Fall through to other resolution paths

    # --- 2. Try session context (active_topic: "automation:<id>") ---
    active_topic = str(ctx.get("active_topic") or "").strip()
    if active_topic.startswith("automation:"):
        stashed_id = active_topic.removeprefix("automation:").strip()
        if stashed_id:
            try:
                defn = load_automation_definition(stashed_id, queue_root)
                return ResolvedAutomation(definition=defn, source="session_context")
            except (AutomationNotFoundError, AutomationStoreError):
                pass  # Definition was deleted or corrupted; fall through

    # --- 3. Try session-stashed last automation preview ---
    if isinstance(last_automation_preview, dict):
        # The preview dict doesn't have an id, but the session context
        # should have recorded it via context_on_automation_saved.
        # If we got here, the session context path didn't resolve.
        # We can try to find the definition by title from the preview.
        preview_title = str(last_automation_preview.get("title") or "").strip()
        if preview_title:
            try:
                definitions = list_automation_definitions(queue_root)
            except AutomationStoreError:
                definitions = []
            for defn in definitions:
                if defn.title == preview_title and defn.created_from == "vera":
                    return ResolvedAutomation(definition=defn, source="session_preview_title")

    # --- 4. Try title hint from message ---
    title_hint = _extract_title_hint(message)
    if title_hint:
        try:
            definitions = list_automation_definitions(queue_root)
        except AutomationStoreError:
            definitions = []
        scored = [(defn, _title_match_score(title_hint, defn.title)) for defn in definitions]
        strong = [(d, s) for d, s in scored if s >= 0.5]
        if len(strong) == 1:
            return ResolvedAutomation(definition=strong[0][0], source="title_match")
        if len(strong) > 1:
            candidates = [f'"{d.title}" (id: {d.id})' for d, _ in strong]
            return AmbiguousAutomation(
                candidates=candidates,
                clarification=(
                    "I found multiple automations that match. "
                    "Which one did you mean?\n" + "\n".join(f"- {c}" for c in candidates)
                ),
            )

    # --- 5. If "that automation" / "it" with no other signal, try single-definition fallback ---
    # When there's exactly one automation and the user said "that/the automation" or "it",
    # resolve to it. Otherwise fail closed.
    pronominal = bool(
        re.search(
            r"\b(?:that|the|this|it)\b.*\b(?:automation|definition)\b"
            r"|\b(?:automation|definition)\b.*\b(?:that|the|this|it)\b"
            r"|\b(?:disable|enable|delete|show|run|describe)\s+(?:it|that|this)\b",
            message,
            re.IGNORECASE,
        )
    )
    if pronominal:
        try:
            definitions = list_automation_definitions(queue_root)
        except AutomationStoreError:
            definitions = []
        if len(definitions) == 1:
            return ResolvedAutomation(definition=definitions[0], source="single_definition")
        if len(definitions) > 1:
            candidates = [f'"{d.title}" (id: {d.id})' for d in definitions[:5]]
            return AmbiguousAutomation(
                candidates=candidates,
                clarification=(
                    "There are multiple saved automations. "
                    "Which one did you mean?\n" + "\n".join(f"- {c}" for c in candidates)
                ),
            )

    return AutomationNotResolved(
        reason="I could not determine which automation you are referring to."
    )


# ---------------------------------------------------------------------------
# Action handlers — each returns an operator-friendly response string
# ---------------------------------------------------------------------------


def _human_trigger_description(kind: str, config: dict[str, Any]) -> str:
    """Return a human-readable trigger description."""
    # Reuse the same helper from automation_preview
    from .automation_preview import _human_trigger_description as _htd

    return _htd(kind, config)


def handle_show(
    definition: AutomationDefinition,
    queue_root: Path,
) -> str:
    """Describe a saved automation definition truthfully."""
    trigger_desc = _human_trigger_description(definition.trigger_kind, definition.trigger_config)
    lines = [
        f"**{definition.title}**",
        f"ID: `{definition.id}`",
        f"Enabled: {definition.enabled}",
        f"Trigger: {trigger_desc}",
    ]

    # Payload summary
    pt = definition.payload_template
    goal = str(pt.get("goal") or "").strip()
    mission = str(pt.get("mission_id") or "").strip()
    if goal:
        lines.append(f"Action: {goal}")
    elif mission:
        lines.append(f"Action: run mission {mission}")

    wf = pt.get("write_file")
    if isinstance(wf, dict):
        wf_path = str(wf.get("path") or "").strip()
        wf_content = str(wf.get("content") or "").strip()
        if wf_path:
            lines.append(f"Writes to: `{wf_path}`")
        if wf_content:
            lines.append(f"Content: {wf_content[:100]}")

    # Timing info
    if definition.next_run_at_ms is not None:
        lines.append(f"Next run at (ms): {definition.next_run_at_ms}")
    if definition.last_run_at_ms is not None:
        lines.append(f"Last run at (ms): {definition.last_run_at_ms}")
    if definition.last_job_ref:
        lines.append(f"Last job ref: `{definition.last_job_ref}`")

    # Run history count
    history = list_history_records(queue_root, definition.id)
    if history:
        lines.append(f"Run history entries: {len(history)}")
        latest = history[0]
        lines.append(
            f"Latest run: {latest.get('outcome', 'unknown')} "
            f"at {latest.get('triggered_at_ms', '?')}"
        )
    else:
        lines.append("Run history: no runs yet")

    lines.append("")
    lines.append(
        "This is a saved automation definition. "
        "The automation runner evaluates it and submits queue jobs "
        "when the trigger condition is met."
    )

    return "\n".join(lines)


def handle_enable(
    definition: AutomationDefinition,
    queue_root: Path,
) -> str:
    """Enable a saved automation definition."""
    if definition.enabled:
        return f'Automation "{definition.title}" (id: `{definition.id}`) is already enabled.'

    updated = definition.model_copy(update={"enabled": True})
    try:
        save_automation_definition(updated, queue_root)
    except (AutomationStoreError, OSError) as exc:
        return f'Failed to enable automation "{definition.title}": {exc}'

    return (
        f'Automation "{definition.title}" (id: `{definition.id}`) '
        "has been enabled. The automation runner will evaluate it on "
        "its next cycle."
    )


def handle_disable(
    definition: AutomationDefinition,
    queue_root: Path,
) -> str:
    """Disable a saved automation definition."""
    if not definition.enabled:
        return f'Automation "{definition.title}" (id: `{definition.id}`) is already disabled.'

    updated = definition.model_copy(update={"enabled": False})
    try:
        save_automation_definition(updated, queue_root)
    except (AutomationStoreError, OSError) as exc:
        return f'Failed to disable automation "{definition.title}": {exc}'

    return (
        f'Automation "{definition.title}" (id: `{definition.id}`) '
        "has been disabled. It will not run until re-enabled."
    )


def handle_delete(
    definition: AutomationDefinition,
    queue_root: Path,
) -> str:
    """Delete a saved automation definition. History is preserved."""
    try:
        delete_automation_definition(definition.id, queue_root)
    except (AutomationNotFoundError, AutomationStoreError) as exc:
        return f'Failed to delete automation "{definition.title}": {exc}'

    return (
        f'Automation "{definition.title}" (id: `{definition.id}`) '
        "has been deleted. The definition has been removed, but any "
        "existing run history records are preserved as audit trail."
    )


def handle_run_now(
    definition: AutomationDefinition,
    queue_root: Path,
) -> str:
    """Force an immediate run through the existing automation runner.

    This uses the runner's force path — it still goes through the
    canonical runner -> inbox -> queue path. Vera does not execute
    payloads directly.
    """
    result = process_automation_definition(definition, queue_root, force=True)

    if result.outcome == "submitted":
        return (
            f'Forced immediate evaluation of "{definition.title}" '
            f"(id: `{definition.id}`).\n"
            f"The automation runner submitted a queue job: "
            f"`{result.queue_job_ref or 'unknown'}`.\n\n"
            "This means a queue job has been created. It has NOT been "
            "executed yet. VoxeraOS will handle planning, policy/approval, "
            "execution, and evidence through the normal queue path."
        )

    if result.outcome == "skipped":
        return f'Could not run "{definition.title}" now: {result.message}'

    # outcome == "error"
    return f'Error running "{definition.title}": {result.message}'


def handle_history(
    definition: AutomationDefinition,
    queue_root: Path,
) -> str:
    """Surface run history for a saved automation definition."""
    records = list_history_records(queue_root, definition.id)

    if not records:
        return (
            f'Automation "{definition.title}" (id: `{definition.id}`) '
            "is saved but has not run yet. There are no history records.\n\n"
            "The automation runner will submit a queue job when the "
            "trigger condition is met."
        )

    lines = [
        f'Run history for "{definition.title}" (id: `{definition.id}`):',
        "",
    ]
    # Show up to 5 most recent records (already sorted newest-first)
    for record in records[:5]:
        run_id = str(record.get("run_id", "?"))
        outcome = str(record.get("outcome", "?"))
        triggered_at = record.get("triggered_at_ms", "?")
        job_ref = record.get("queue_job_ref") or "-"
        msg = str(record.get("message", ""))
        lines.append(f"- **{outcome}** (run: `{run_id}`)")
        lines.append(f"  Triggered at (ms): {triggered_at}")
        if job_ref != "-":
            lines.append(f"  Queue job: `{job_ref}`")
        if msg:
            lines.append(f"  {msg}")
        lines.append("")

    if len(records) > 5:
        lines.append(f"({len(records)} total history records; showing 5 most recent)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Unified dispatch — called from the web chat handler
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LifecycleResult:
    """Result of a lifecycle action dispatch."""

    matched: bool
    assistant_text: str
    status: str
    automation_id: str | None = None
    intent: LifecycleIntent | None = None
    definition_deleted: bool = False


def dispatch_lifecycle_action(
    message: str,
    *,
    queue_root: Path,
    session_context: dict[str, Any] | None = None,
    last_automation_preview: dict[str, Any] | None = None,
) -> LifecycleResult:
    """Detect and execute a lifecycle management action.

    Returns a LifecycleResult. If ``matched`` is False, the caller should
    proceed to the next routing stage. If True, the ``assistant_text`` and
    ``status`` are ready for the response.
    """
    intent = classify_lifecycle_intent(message)
    if intent is None:
        return LifecycleResult(
            matched=False,
            assistant_text="",
            status="no_lifecycle_intent",
        )

    # Resolve which automation the user is referring to
    resolution = resolve_automation_reference(
        message,
        queue_root=queue_root,
        session_context=session_context,
        last_automation_preview=last_automation_preview,
    )

    if isinstance(resolution, AmbiguousAutomation):
        return LifecycleResult(
            matched=True,
            assistant_text=resolution.clarification,
            status="automation_lifecycle_ambiguous",
            intent=intent,
        )

    if isinstance(resolution, AutomationNotResolved):
        return LifecycleResult(
            matched=True,
            assistant_text=resolution.reason,
            status="automation_lifecycle_not_found",
            intent=intent,
        )

    # We have a resolved definition — dispatch the action
    defn = resolution.definition

    if intent is LifecycleIntent.SHOW:
        text = handle_show(defn, queue_root)
        status = "automation_lifecycle_show"
    elif intent is LifecycleIntent.ENABLE:
        text = handle_enable(defn, queue_root)
        status = "automation_lifecycle_enable"
    elif intent is LifecycleIntent.DISABLE:
        text = handle_disable(defn, queue_root)
        status = "automation_lifecycle_disable"
    elif intent is LifecycleIntent.DELETE:
        text = handle_delete(defn, queue_root)
        status = "automation_lifecycle_delete"
    elif intent is LifecycleIntent.RUN_NOW:
        text = handle_run_now(defn, queue_root)
        status = "automation_lifecycle_run_now"
    elif intent is LifecycleIntent.HISTORY:
        text = handle_history(defn, queue_root)
        status = "automation_lifecycle_history"
    else:
        text = "I could not determine what action to take."
        status = "automation_lifecycle_unknown"

    return LifecycleResult(
        matched=True,
        assistant_text=text,
        status=status,
        automation_id=defn.id,
        intent=intent,
        definition_deleted=(intent is LifecycleIntent.DELETE),
    )


__all__ = [
    "AmbiguousAutomation",
    "AutomationNotResolved",
    "LifecycleIntent",
    "LifecycleResult",
    "ResolvedAutomation",
    "classify_lifecycle_intent",
    "dispatch_lifecycle_action",
    "handle_delete",
    "handle_disable",
    "handle_enable",
    "handle_history",
    "handle_run_now",
    "handle_show",
    "is_automation_lifecycle_intent",
    "resolve_automation_reference",
]
