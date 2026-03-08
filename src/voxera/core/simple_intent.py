from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

SimpleIntentKind: TypeAlias = Literal[
    "assistant_question",
    "open_terminal",
    "open_url",
    "open_app",
    "write_file",
    "read_file",
    "run_command",
    "unknown_or_ambiguous",
]

_ADVISORY_SKILLS: frozenset[str] = frozenset({"assistant.advisory", "system.status"})
_OPEN_TERMINAL_SKILLS: frozenset[str] = frozenset({"system.open_app"})
_OPEN_URL_SKILLS: frozenset[str] = frozenset({"system.open_url"})
_OPEN_APP_SKILLS: frozenset[str] = frozenset({"system.open_app"})
_WRITE_SKILLS: frozenset[str] = frozenset({"files.write_text"})
_READ_SKILLS: frozenset[str] = frozenset({"files.read_text"})
_RUN_SKILLS: frozenset[str] = frozenset({"sandbox.exec"})
_NO_CONSTRAINT: frozenset[str] = frozenset()

INTENT_ALLOWED_SKILLS: dict[SimpleIntentKind, frozenset[str]] = {
    "assistant_question": _ADVISORY_SKILLS,
    "open_terminal": _OPEN_TERMINAL_SKILLS,
    "open_url": _OPEN_URL_SKILLS,
    "open_app": _OPEN_APP_SKILLS,
    "write_file": _WRITE_SKILLS,
    "read_file": _READ_SKILLS,
    "run_command": _RUN_SKILLS,
    "unknown_or_ambiguous": _NO_CONSTRAINT,
}

_RE_WRITE_VERB = re.compile(
    r"^\s*(?:write|append\s+to|save\s+.+\s+to|create\s+(?:(?:a|an|new|empty)\s+)*file)\b",
    re.IGNORECASE,
)
_RE_READ_VERB = re.compile(
    r"^\s*(?:"
    r"(?:open\s+and\s+)?read(?:\s+the)?\s+(?:file\s+)?"
    r"|cat\s+"
    r"|display\s+"
    r"|view\s+"
    r"|show\s+contents?\s+of\s+"
    r")[~/]",
    re.IGNORECASE,
)
_RE_READ_PATH = re.compile(
    r"^\s*(?:"
    r"(?:open\s+and\s+)?read(?:\s+the)?\s+(?:file\s+)?"
    r"|cat\s+"
    r"|display\s+"
    r"|view\s+"
    r"|show\s+contents?\s+of\s+"
    r")(?P<path>[~/]\S+)",
    re.IGNORECASE,
)
_RE_WRITE_CALLED = re.compile(r"\bcalled?\s+(?P<name>\S+)\s*$", re.IGNORECASE)
_RE_RUN_VERB = re.compile(
    r"^\s*(?:run\s+command\b|execute\b|exec\b|run\s+(?:the\s+)?(?:command\s+)?`)",
    re.IGNORECASE,
)
_RE_QUESTION_VERB = re.compile(
    r"^\s*(?:what\s+is\b|what\s+are\b|what\'s\b|how\s+do\b|how\s+can\b|"
    r"tell\s+me\b|show\s+me\b|describe\b|explain\b|"
    r"(?:get|fetch|check|show)\s+(?:current\s+)?system\s+status\b|"
    r"(?:what\s+is\s+(?:my\s+)?(?:current\s+)?system\s+status)\b)",
    re.IGNORECASE,
)
_RE_STATUS_PHRASE = re.compile(
    r"\b(?:system\s+status|current\s+status|health\s+status)\b", re.IGNORECASE
)
_RE_LEADING_POLITE = re.compile(r"^\s*(?:please\s+|kindly\s+)", re.IGNORECASE)
_RE_COMPOUND_SPLIT = re.compile(r"\s+(?:and\s+then|and|then)\s+", re.IGNORECASE)
_RE_META_PREFIX = re.compile(
    r"^\s*(?:how\s+do\s+i\b|how\s+should\b|why\b|when\s+i\s+say\b|"
    r"help\s+me\s+\b|the\s+(?:phrase|command)\b|write\s+(?:a\s+)?script\s+that\b)",
    re.IGNORECASE,
)
_RE_META_ANY = re.compile(
    r"\b(?:how\s+do\s+i|why\s+does|phrase\s+open\s+terminal|command\s+open\s+terminal\s+is\s+broken|misrouted)\b",
    re.IGNORECASE,
)
_RE_QUOTED_ACTION = re.compile(r"['\"]\s*open\s+(?:terminal|https?://|[a-z0-9._-]+)", re.IGNORECASE)
_RE_OPEN_TERMINAL_DIRECT = re.compile(
    r"^\s*(?:open|launch|start)\s+(?:the\s+|a\s+|an\s+)?terminal\b", re.IGNORECASE
)
_RE_OPEN_URL_DIRECT = re.compile(
    r"^\s*(?:open|launch)\s+(?:this\s+url\s*:\s*|the\s+site\s+)?(?P<url>https?://\S+)",
    re.IGNORECASE,
)
_RE_OPEN_APP_DIRECT = re.compile(
    r"^\s*(?:open|launch|start)\s+(?P<name>(?:gnome\s+calculator|gnome-calculator|calculator|firefox|gnome\s+terminal|terminal))\b",
    re.IGNORECASE,
)
_RE_OPEN_APP_VAGUE = re.compile(
    r"^\s*(?:open|launch|start)\s+(?:an\s+app|something|my\s+work\s+stuff)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class SimpleIntentResult:
    intent_kind: SimpleIntentKind
    deterministic: bool
    allowed_skill_ids: frozenset[str]
    routing_reason: str
    fail_closed: bool
    extracted_target: str | None = None
    compound_action: bool = False
    first_step_only: bool = False
    first_action_intent_kind: SimpleIntentKind | None = None
    trailing_remainder: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "intent_kind": self.intent_kind,
            "deterministic": self.deterministic,
            "allowed_skill_ids": sorted(self.allowed_skill_ids),
            "routing_reason": self.routing_reason,
            "fail_closed": self.fail_closed,
            "compound_action": self.compound_action,
            "first_step_only": self.first_step_only,
        }
        if self.extracted_target is not None and (
            self.intent_kind != "read_file" or _is_safe_read_extracted_target(self.extracted_target)
        ):
            d["extracted_target"] = self.extracted_target
        if self.first_action_intent_kind is not None:
            d["first_action_intent_kind"] = self.first_action_intent_kind
        if self.trailing_remainder is not None:
            d["trailing_remainder"] = self.trailing_remainder
        return d


def _make_unknown(reason: str = "ambiguous_simple_intent") -> SimpleIntentResult:
    return SimpleIntentResult(
        intent_kind="unknown_or_ambiguous",
        deterministic=False,
        allowed_skill_ids=_NO_CONSTRAINT,
        routing_reason=reason,
        fail_closed=False,
    )


def _normalize_goal(text: str) -> str:
    return _RE_LEADING_POLITE.sub("", text).strip()


def _contains_parent_traversal(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return any(segment == ".." for segment in normalized.split("/"))


def _is_safe_read_extracted_target(path: str) -> bool:
    return path.startswith("~/VoxeraOS/notes/") and not _contains_parent_traversal(path)


def _split_compound(text: str) -> tuple[str, str | None]:
    m = _RE_COMPOUND_SPLIT.search(text)
    if not m:
        return (text.strip(), None)
    return (text[: m.start()].strip(), text[m.end() :].strip() or None)


def _meta_or_explanatory(text: str) -> bool:
    lowered = text.lower()
    if _RE_META_PREFIX.match(lowered) or _RE_META_ANY.search(lowered):
        return True
    return bool(_RE_QUOTED_ACTION.search(text))


def _classify_open_clause(
    clause: str,
) -> tuple[SimpleIntentKind | None, frozenset[str], str, str | None]:
    if _RE_OPEN_TERMINAL_DIRECT.match(clause):
        return ("open_terminal", _OPEN_TERMINAL_SKILLS, "direct_open_terminal", "terminal")
    url_m = _RE_OPEN_URL_DIRECT.match(clause)
    if url_m:
        url = url_m.group("url").rstrip(".,;:!?)\"'")
        return ("open_url", _OPEN_URL_SKILLS, "direct_open_url", url)
    if _RE_OPEN_APP_VAGUE.match(clause):
        return (None, _NO_CONSTRAINT, "open_phrase_too_vague", None)
    app_m = _RE_OPEN_APP_DIRECT.match(clause)
    if app_m:
        app_name = app_m.group("name").strip()
        return ("open_app", _OPEN_APP_SKILLS, "direct_open_app", app_name)
    return (None, _NO_CONSTRAINT, "not_direct_open_clause", None)


def classify_simple_operator_intent(
    *, goal: str | None, action_hints: list[str] | None = None
) -> SimpleIntentResult:
    if not goal:
        return _make_unknown()
    text = goal.strip()
    if not text:
        return _make_unknown()

    if _meta_or_explanatory(text):
        return _make_unknown("meta_or_explanatory_request")

    norm = _normalize_goal(text)

    if _RE_QUESTION_VERB.match(norm):
        return SimpleIntentResult(
            "assistant_question", True, _ADVISORY_SKILLS, "goal_starts_with_question_verb", True
        )
    if _RE_STATUS_PHRASE.search(norm) and len(norm.split()) <= 12:
        return SimpleIntentResult(
            "assistant_question", True, _ADVISORY_SKILLS, "goal_contains_status_phrase", True
        )

    if _RE_READ_VERB.match(norm):
        path_match = _RE_READ_PATH.match(norm)
        extracted_path = None
        if path_match:
            raw_path = path_match.group("path").rstrip(".,;:!?\"'").strip()
            if raw_path and _is_safe_read_extracted_target(raw_path):
                extracted_path = raw_path
        return SimpleIntentResult(
            "read_file",
            True,
            _READ_SKILLS,
            "goal_starts_with_read_verb_and_path",
            True,
            extracted_target=extracted_path,
        )

    if _RE_WRITE_VERB.match(norm):
        extracted_write = None
        name_match = _RE_WRITE_CALLED.search(norm)
        if name_match:
            raw_name = name_match.group("name").strip(".,;:!?\"'")
            if raw_name and "/" not in raw_name and "\\" not in raw_name:
                extracted_write = f"~/VoxeraOS/notes/{raw_name}"
        return SimpleIntentResult(
            "write_file",
            True,
            _WRITE_SKILLS,
            "goal_starts_with_write_verb",
            True,
            extracted_target=extracted_write,
        )

    if _RE_RUN_VERB.match(norm):
        return SimpleIntentResult(
            "run_command", True, _RUN_SKILLS, "goal_starts_with_run_verb", True
        )

    first_clause, remainder = _split_compound(norm)
    intent_kind, allowed, reason, target = _classify_open_clause(first_clause)
    if intent_kind:
        return SimpleIntentResult(
            intent_kind=intent_kind,
            deterministic=True,
            allowed_skill_ids=allowed,
            routing_reason=reason,
            fail_closed=True,
            extracted_target=target,
            compound_action=remainder is not None,
            first_step_only=remainder is not None,
            first_action_intent_kind=intent_kind if remainder is not None else None,
            trailing_remainder=remainder,
        )

    if remainder is not None:
        return _make_unknown("compound_but_non_actionable_prefix")
    return _make_unknown()


def check_skill_family_mismatch(
    intent: SimpleIntentResult, first_step_skill_id: str
) -> tuple[bool, str]:
    if not intent.deterministic or not intent.fail_closed:
        return (False, "intent_not_deterministic")
    if intent.intent_kind == "unknown_or_ambiguous":
        return (False, "intent_not_deterministic")
    if not intent.allowed_skill_ids:
        return (False, "no_allowed_skill_constraint")
    if first_step_skill_id in intent.allowed_skill_ids:
        return (False, "skill_family_matches")
    return (True, "simple_intent_skill_family_mismatch")
