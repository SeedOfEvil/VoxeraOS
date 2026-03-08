"""Deterministic simple-intent routing for common operator requests.

Classifies a goal string into a small, deliberately narrow intent set.
If an intent is recognised, the classifier returns the set of skill IDs that
are valid as the *first* step of a plan.  If the plan produced by the planner
does not start with a skill in that set we fail closed before any side effects
occur.

Intent set (exhaustive for this version):
    assistant_question  – advisory / status questions (read-only)
    open_resource       – open an app or URL
    write_file          – create or write a file
    read_file           – read / display a file
    run_command         – run a command in a sandbox or terminal
    unknown_or_ambiguous – everything else; no constraint is applied

Keep this module small, boring, and free of NLP dependencies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

SimpleIntentKind = Literal[
    "assistant_question",
    "open_resource",
    "write_file",
    "read_file",
    "run_command",
    "unknown_or_ambiguous",
]

# ---------------------------------------------------------------------------
# Skill-family allowlists per intent
# ---------------------------------------------------------------------------

_ADVISORY_SKILLS: frozenset[str] = frozenset({"assistant.advisory", "system.status"})
# General open_resource: GUI app launcher or URL opener.
_OPEN_SKILLS: frozenset[str] = frozenset({"system.open_app", "system.open_url"})
# "open terminal" specifically: planner may use either system.open_app (launches
# gnome-terminal) or system.terminal_run_once (deterministic terminal demo skill).
_TERMINAL_OPEN_SKILLS: frozenset[str] = frozenset({"system.terminal_run_once", "system.open_app"})
_WRITE_SKILLS: frozenset[str] = frozenset({"files.write_text"})
_READ_SKILLS: frozenset[str] = frozenset({"files.read_text"})
_RUN_SKILLS: frozenset[str] = frozenset({"sandbox.exec", "system.terminal_run_once"})
_NO_CONSTRAINT: frozenset[str] = frozenset()

# Reference table: canonical allowed-skill sets per intent kind.
# Note: open_resource entries use the union of all possible first-step skills
# across the sub-routes (terminal vs. general open); the classifier returns a
# refined set per goal (e.g. _TERMINAL_OPEN_SKILLS for "open terminal").
INTENT_ALLOWED_SKILLS: dict[str, frozenset[str]] = {
    "assistant_question": _ADVISORY_SKILLS,
    "open_resource": _OPEN_SKILLS | _TERMINAL_OPEN_SKILLS,
    "write_file": _WRITE_SKILLS,
    "read_file": _READ_SKILLS,
    "run_command": _RUN_SKILLS,
    "unknown_or_ambiguous": _NO_CONSTRAINT,
}

# ---------------------------------------------------------------------------
# Regex patterns (conservative – only match when we are highly confident)
# ---------------------------------------------------------------------------

# write_file: starts with write/create/append/save verb
# "create file" / "create a file" / "create a new file" / "create an empty file" are all
# write-file intents regardless of articles or adjectives before "file".
_RE_WRITE_VERB = re.compile(
    r"^\s*(?:write|append\s+to|save\s+.+\s+to|create\s+(?:(?:a|an|new|empty)\s+)*file)\b",
    re.IGNORECASE,
)

# read_file: starts with an explicit read verb + a path.
# Matches the following forms (path must start with ~ or /):
#   "read ~/path"                     — bare read + path
#   "read the ~/path"                 — read + article + path
#   "read the file ~/path"            — read + article + "file" + path
#   "open and read ~/path"            — compound verb + path
#   "open and read the file ~/path"   — compound verb + article + "file" + path
#   "cat ~/path"                      — shell shorthand
#   "display ~/path"                  — display verb
#   "view ~/path"                     — view verb
#   "show contents of ~/path"         — show-contents form
#
# Deliberately does NOT match:
#   "read this and copy it"    — no path prefix
#   "read the situation"       — no path prefix
#   "open ~/notes/foo.txt"     — path-like open falls through to unknown (open_resource
#                                 already excludes path-like targets via _RE_PATH_LIKE)
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

# Companion extractor: same prefix pattern but captures the path itself.
# Used to populate SimpleIntentResult.extracted_target and to power the
# deterministic read route in mission_planner.
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

# write_file target extraction: matches "called <filename>" at end of goal.
# Used to populate SimpleIntentResult.extracted_target for write goals like
# "write a file called whatupboy.txt" → extracted_target = "~/VoxeraOS/notes/whatupboy.txt".
_RE_WRITE_CALLED = re.compile(
    r"\bcalled?\s+(?P<name>\S+)\s*$",
    re.IGNORECASE,
)

# open_resource: conservative patterns — only match when obviously an open/launch/start ask.
# Require that the target is a single simple identifier (no spaces, no articles).
# "open terminal", "open Firefox", "launch gnome-terminal" → match
# "Open status and report", "open an app and report status" → do NOT match
# "open the terminal" → do NOT match (starts with article "the")
_RE_OPEN_SIMPLE_TARGET = re.compile(
    # verb + optional "the"/"a"/"an" exclusion + single-word target + end-of-string
    # Negative lookahead for articles prevents "open an app", "open the ..."
    r"^\s*(?:open|launch|start)\s+(?!a\b|an\b|the\b)([a-z0-9._-]+)\s*$",
    re.IGNORECASE,
)
# "open terminal" specifically — kept for clarity
_RE_OPEN_TERMINAL = re.compile(r"^\s*open\s+terminal\s*$", re.IGNORECASE)
# "open <url>" specifically
_RE_OPEN_URL = re.compile(r"^\s*(?:open|launch)\s+https?://", re.IGNORECASE)
# A goal that looks like a file path (used to exclude e.g. "open ~/notes/foo.txt")
_RE_PATH_LIKE = re.compile(r"[~/\\]|\.\w{1,6}($|\s)", re.IGNORECASE)

# run_command: starts with run/execute/exec
_RE_RUN_VERB = re.compile(
    r"^\s*(?:run\s+command\b|execute\b|exec\b|run\s+(?:the\s+)?(?:command\s+)?`)",
    re.IGNORECASE,
)

# assistant_question: starts with question word or status ask
_RE_QUESTION_VERB = re.compile(
    r"^\s*(?:what\s+is\b|what\s+are\b|what\'s\b|how\s+do\b|how\s+can\b|"
    r"tell\s+me\b|show\s+me\b|describe\b|explain\b|"
    r"(?:get|fetch|check|show)\s+(?:current\s+)?system\s+status\b|"
    r"(?:what\s+is\s+(?:my\s+)?(?:current\s+)?system\s+status)\b)",
    re.IGNORECASE,
)
_RE_STATUS_PHRASE = re.compile(
    r"\b(?:system\s+status|current\s+status|health\s+status)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SimpleIntentResult:
    """Classifier output.

    Attributes:
        intent_kind:          The recognised intent class.
        deterministic:        True when we are confident in the classification
                              and should apply the skill-family constraint.
        allowed_skill_ids:    Set of skill IDs that are valid for this intent.
                              Empty for unknown_or_ambiguous.
        routing_reason:       Short machine-readable reason code for observability.
        fail_closed:          Whether a mismatch with allowed_skill_ids should
                              produce a hard failure (True) or a warning only (False).
                              Always False for unknown_or_ambiguous.
        extracted_target:     For read_file / write_file intents, the deterministically
                              extracted file path (or candidate path for write).
                              None for all other intents or when extraction fails.
                              Used by the mission planner to construct a governed
                              first-step plan without calling the cloud brain.
    """

    intent_kind: SimpleIntentKind
    deterministic: bool
    allowed_skill_ids: frozenset[str]
    routing_reason: str
    fail_closed: bool
    extracted_target: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "intent_kind": self.intent_kind,
            "deterministic": self.deterministic,
            "allowed_skill_ids": sorted(self.allowed_skill_ids),
            "routing_reason": self.routing_reason,
            "fail_closed": self.fail_closed,
        }
        if self.extracted_target is not None:
            d["extracted_target"] = self.extracted_target
        return d


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

_UNKNOWN = SimpleIntentResult(
    intent_kind="unknown_or_ambiguous",
    deterministic=False,
    allowed_skill_ids=_NO_CONSTRAINT,
    routing_reason="ambiguous_simple_intent",
    fail_closed=False,
)


def classify_simple_operator_intent(
    *,
    goal: str | None,
    action_hints: list[str] | None = None,
) -> SimpleIntentResult:
    """Classify a goal string into a simple intent kind.

    Args:
        goal:         The raw goal / request text from the operator.
        action_hints: Optional list of action hints from the job payload.
                      Currently used to check for status-class hints.

    Returns:
        A :class:`SimpleIntentResult`.  For ``unknown_or_ambiguous`` the
        result is non-deterministic (``deterministic=False``) and no constraint
        is applied (``fail_closed=False``).
    """
    if not goal:
        return _UNKNOWN

    text = goal.strip()
    if not text:
        return _UNKNOWN

    # --- assistant_question / advisory / status -------------------------
    if _RE_QUESTION_VERB.match(text):
        return SimpleIntentResult(
            intent_kind="assistant_question",
            deterministic=True,
            allowed_skill_ids=_ADVISORY_SKILLS,
            routing_reason="goal_starts_with_question_verb",
            fail_closed=True,
        )
    if _RE_STATUS_PHRASE.search(text) and len(text.split()) <= 12:
        # Short phrase mentioning "system status" – treat as advisory/status ask.
        return SimpleIntentResult(
            intent_kind="assistant_question",
            deterministic=True,
            allowed_skill_ids=_ADVISORY_SKILLS,
            routing_reason="goal_contains_status_phrase",
            fail_closed=True,
        )

    # --- read_file -------------------------------------------------------
    if _RE_READ_VERB.match(text):
        # Extract the file path for direct routing by the mission planner.
        path_match = _RE_READ_PATH.match(text)
        extracted_path: str | None = None
        if path_match:
            raw_path = path_match.group("path").rstrip(".,;:!?\"'").strip()
            if raw_path:
                extracted_path = raw_path
        return SimpleIntentResult(
            intent_kind="read_file",
            deterministic=True,
            allowed_skill_ids=_READ_SKILLS,
            routing_reason="goal_starts_with_read_verb_and_path",
            fail_closed=True,
            extracted_target=extracted_path,
        )

    # --- write_file ------------------------------------------------------
    if _RE_WRITE_VERB.match(text):
        # Try to extract a candidate filename from "called <name>" suffix.
        extracted_write: str | None = None
        name_match = _RE_WRITE_CALLED.search(text)
        if name_match:
            raw_name = name_match.group("name").strip(".,;:!?\"'")
            # Accept simple filenames only — no path separators.
            if raw_name and "/" not in raw_name and "\\" not in raw_name:
                extracted_write = f"~/VoxeraOS/notes/{raw_name}"
        return SimpleIntentResult(
            intent_kind="write_file",
            deterministic=True,
            allowed_skill_ids=_WRITE_SKILLS,
            routing_reason="goal_starts_with_write_verb",
            fail_closed=True,
            extracted_target=extracted_write,
        )

    # --- run_command -----------------------------------------------------
    if _RE_RUN_VERB.match(text):
        return SimpleIntentResult(
            intent_kind="run_command",
            deterministic=True,
            allowed_skill_ids=_RUN_SKILLS,
            routing_reason="goal_starts_with_run_verb",
            fail_closed=True,
        )

    # --- open_resource ---------------------------------------------------
    # Only classify as open_resource when we are highly confident.
    # We require one of:
    #   (a) Exactly "open terminal" (case-insensitive, whole goal)
    #   (b) "open/launch <url>" — verb + https:// URL
    #   (c) "open/launch/start <single-word-app>" — verb + exactly one simple
    #       alphanumeric identifier (no articles, no multi-word phrases, no paths)
    #
    # Exclusions: "Open status and report", "open an app and ...", "open the ..."
    # fall through to unknown_or_ambiguous below.
    if _RE_OPEN_TERMINAL.match(text):
        # "open terminal" specifically: the planner may use system.open_app
        # (e.g. gnome-terminal) or system.terminal_run_once (deterministic
        # demo skill).  Both are legitimate first steps for this goal.
        return SimpleIntentResult(
            intent_kind="open_resource",
            deterministic=True,
            allowed_skill_ids=_TERMINAL_OPEN_SKILLS,
            routing_reason="goal_is_open_terminal",
            fail_closed=True,
        )
    if _RE_OPEN_URL.match(text):
        # URL goals have dots/slashes by design — skip path-like exclusion.
        return SimpleIntentResult(
            intent_kind="open_resource",
            deterministic=True,
            allowed_skill_ids=_OPEN_SKILLS,
            routing_reason="goal_is_open_url",
            fail_closed=True,
        )
    if _RE_OPEN_SIMPLE_TARGET.match(text) and not _RE_PATH_LIKE.search(text):
        return SimpleIntentResult(
            intent_kind="open_resource",
            deterministic=True,
            allowed_skill_ids=_OPEN_SKILLS,
            routing_reason="goal_is_open_or_launch_app",
            fail_closed=True,
        )

    return _UNKNOWN


# ---------------------------------------------------------------------------
# Mismatch detection
# ---------------------------------------------------------------------------


def check_skill_family_mismatch(
    intent: SimpleIntentResult,
    first_step_skill_id: str,
) -> tuple[bool, str]:
    """Check whether *first_step_skill_id* is allowed for *intent*.

    Returns:
        (mismatch, reason_code)

        mismatch=True means the plan should be rejected fail-closed.
        reason_code is a short machine-readable string for artifacts.
    """
    if not intent.deterministic or not intent.fail_closed:
        return (False, "intent_not_deterministic")
    if intent.intent_kind == "unknown_or_ambiguous":
        return (False, "intent_not_deterministic")
    if not intent.allowed_skill_ids:
        return (False, "no_allowed_skill_constraint")
    if first_step_skill_id in intent.allowed_skill_ids:
        return (False, "skill_family_matches")
    return (True, "simple_intent_skill_family_mismatch")
