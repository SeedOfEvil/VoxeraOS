"""Bounded action-oriented vs informational classifier for Voice Workbench.

The Voice Workbench is conversational only: it never drafts previews,
submits jobs, or triggers real-world side effects.  But when a spoken
request *sounds* like real governed work ("delete that file", "restart
the daemon", "submit the mission"), the generic "continue in Vera"
boundary note is not useful.  The operator is better served by a
clearer, truth-preserving guidance block that says: this needs
governed preview/handoff, continue in canonical Vera on the same
session.

This module is the narrow classification seam that decides whether a
run looks action-oriented.  It is deterministic, bounded, and
explainable:

- input: the normalized voice transcript (string or ``None``)
- output: a typed :class:`VoiceWorkbenchClassification`

The classifier intentionally leans **conservative**:

- Question-form phrasings ("what is", "how do I", "why does") are
  always informational, even when they mention mutation verbs —
  asking *how* to delete a file is not the same as asking to delete
  a file.
- Action-oriented firing requires both a direct mutation verb AND a
  plausible target noun (or an explicit imperative prefix like
  "please" / "go ahead and").  This keeps idle chat and generic
  "could you help me?" requests from triggering a governed-action
  guidance block.
- Empty / missing transcripts classify as informational so the UI
  never surfaces a stronger warning on a no-op run.

The classifier does NOT call Vera, does NOT inspect preview/queue
state, and does NOT mutate anything.  It is a pure string scanner
over the transcript text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Canonical classification kinds.  Kept as bare strings so template
# conditionals and tests can compare against stable literals without
# importing the module.
CLASSIFICATION_INFORMATIONAL = "informational"
CLASSIFICATION_ACTION_ORIENTED = "action_oriented"

# Reason codes surface the branch of the classifier that fired so the
# decision is explainable in tests and debug surfaces.  These are NOT
# operator-facing strings — the UI renders its own guidance copy.
_REASON_EMPTY = "empty_or_missing_transcript"
_REASON_QUESTION_FORM = "question_form"
_REASON_ACTION_VERB_WITH_TARGET = "action_verb_with_target"
_REASON_IMPERATIVE_WITH_ACTION_VERB = "imperative_with_action_verb"
_REASON_DEFAULT_CONVERSATIONAL = "default_conversational"

# Mutation / execution verbs that plausibly indicate a governed real
# action.  Listed as whole-word tokens; the matcher expands them with
# ``\b`` so "recreate" or "rundown" won't spuriously match "create"
# or "run".
#
# Intentionally excluded: "queue".  In VoxeraOS operator jargon "queue"
# is overwhelmingly a noun ("the queue is slow", "queue depth"), and
# including it as a verb caused "the queue is ..." to fire
# action-oriented on its own.  Operators who really mean *to queue*
# something reliably say "submit" or "schedule", both of which are in
# this list.
_ACTION_VERBS: tuple[str, ...] = (
    "delete",
    "remove",
    "rename",
    "move",
    "copy",
    "create",
    "make",
    "write",
    "install",
    "uninstall",
    "run",
    "execute",
    "trigger",
    "submit",
    "schedule",
    "restart",
    "stop",
    "start",
    "kill",
    "enable",
    "disable",
    "organize",
    "save",
    "send",
    "launch",
)

# Plausible targets for a governed real action.  A match here,
# combined with an action verb, is what flips the classifier to
# action-oriented.
_ACTION_TARGETS: tuple[str, ...] = (
    "file",
    "files",
    "folder",
    "folders",
    "directory",
    "directories",
    "script",
    "scripts",
    "note",
    "notes",
    "job",
    "jobs",
    "mission",
    "missions",
    "skill",
    "skills",
    "automation",
    "automations",
    "service",
    "services",
    "daemon",
    "queue",
    "panel",
    "command",
    "process",
    "package",
    "config",
    "configuration",
)

# Imperative-style prefixes — when these appear with an action verb,
# the classifier treats the turn as action-oriented even if no
# explicit target noun is present.  This catches short, voice-native
# phrasings like "please delete it" or "go ahead and restart".
_IMPERATIVE_PREFIXES: tuple[str, ...] = (
    "please ",
    "go ahead and ",
    "go ahead, ",
    "could you ",
    "can you ",
    "would you ",
    "i need you to ",
    "i want you to ",
    "let's ",
    "lets ",
)

# Question-form starters that always classify as informational.  A
# question about an action is not the same as an action.
#
# Tradeoff note: bare single-word starters like ``"do "`` and ``"is "``
# are inclusive by design — they cover the overwhelming majority of
# natural questions ("do you know...", "is the daemon running...").
# The rare cost is that genuinely action-shaped imperatives that *begin*
# with those exact tokens ("do restart the service") fall into the
# informational bucket.  The classifier is intentionally conservative:
# a missed action is safe (the always-present Governed Handoff row
# still points the operator at canonical Vera), but a false-positive
# warning on every question would be noisy.
_QUESTION_STARTERS: tuple[str, ...] = (
    "what ",
    "what's ",
    "whats ",
    "what is ",
    "what are ",
    "why ",
    "how ",
    "how's ",
    "hows ",
    "when ",
    "where ",
    "who ",
    "which ",
    "did ",
    "does ",
    "do ",
    "is ",
    "are ",
    "was ",
    "were ",
    "tell me ",
    "show me ",
    "explain ",
    "describe ",
)


@dataclass(frozen=True)
class VoiceWorkbenchClassification:
    """Typed classification result for a Voice Workbench transcript.

    ``kind`` is one of :data:`CLASSIFICATION_INFORMATIONAL` or
    :data:`CLASSIFICATION_ACTION_ORIENTED`.

    ``reason`` is the internal code for the classifier branch that
    fired, for debugging and tests.  It is not operator-facing copy.

    ``matched_signals`` is the small list of lowercase tokens that the
    classifier matched (e.g. ``("delete", "file")``).  Empty on
    informational outcomes.
    """

    kind: str
    reason: str
    matched_signals: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_action_oriented(self) -> bool:
        return self.kind == CLASSIFICATION_ACTION_ORIENTED


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _looks_like_question(lowered: str) -> bool:
    if lowered.endswith("?"):
        return True
    return any(lowered.startswith(starter) for starter in _QUESTION_STARTERS)


# Verb-in-idiom suppression table.  Each key is a whole-word action
# verb whose plain meaning is mutation; each value is a tuple of
# conversational idioms where that verb does NOT carry action intent
# ("make sure" = "ensure", "makes sense" = comprehension).  A verb
# hit is dropped only when *every* whole-word occurrence of that verb
# falls inside an idiom span — if the verb also appears standalone
# ("make sure to delete the file" still hits "delete"), the standalone
# usage wins.  Kept small and explicit: the table is the entire list
# of carve-outs.
_IDIOMATIC_VERB_PHRASES: dict[str, tuple[str, ...]] = {
    "make": ("make sure", "makes sense"),
}

# Pre-compiled whole-word matchers for every verb / target in the
# lexicons.  Compiled once at module load so hot-path classification
# never relies on ``re``'s internal pattern cache (which has a fixed
# eviction bound and could churn in a process with many regex
# callers).  The idiom spans used by ``_all_occurrences_inside_idiom``
# are also pre-compiled here; each lookup collapses to a direct
# ``dict.get`` + ``Pattern.finditer`` call at classification time.
_VERB_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (verb, re.compile(rf"\b{re.escape(verb)}\b")) for verb in _ACTION_VERBS
)
_TARGET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (target, re.compile(rf"\b{re.escape(target)}\b")) for target in _ACTION_TARGETS
)
_IDIOM_PATTERNS: dict[str, tuple[tuple[re.Pattern[str], re.Pattern[str]], ...]] = {
    verb: tuple(
        (re.compile(rf"\b{re.escape(verb)}\b"), re.compile(re.escape(idiom))) for idiom in idioms
    )
    for verb, idioms in _IDIOMATIC_VERB_PHRASES.items()
}


def _all_occurrences_inside_idiom(lowered: str, verb: str) -> bool:
    """Return True iff every whole-word match of ``verb`` in ``lowered``
    falls inside one of the idiom spans registered for that verb.

    Used to suppress verb hits that are only present as part of a
    conversational idiom ("make sure", "makes sense"), so the
    classifier does not flip action-oriented on phrasings like
    "let's make sure the daemon is healthy".  If the verb *also*
    appears outside an idiom (e.g. "make sure to delete the file"
    would still hit "delete"), the verb is treated as a real verb
    — idiom suppression only drops the idiom-bound occurrences.
    """
    patterns = _IDIOM_PATTERNS.get(verb)
    if not patterns:
        return False
    verb_pattern = patterns[0][0]
    verb_spans = [(m.start(), m.end()) for m in verb_pattern.finditer(lowered)]
    if not verb_spans:
        return False
    idiom_spans: list[tuple[int, int]] = []
    for _verb_pattern, idiom_pattern in patterns:
        idiom_spans.extend((m.start(), m.end()) for m in idiom_pattern.finditer(lowered))
    if not idiom_spans:
        return False
    for v_start, v_end in verb_spans:
        if not any(i_start <= v_start and v_end <= i_end for i_start, i_end in idiom_spans):
            return False
    return True


def _match_action_verbs(lowered: str) -> list[str]:
    hits: list[str] = []
    for verb, pattern in _VERB_PATTERNS:
        if not pattern.search(lowered):
            continue
        if verb in _IDIOM_PATTERNS and _all_occurrences_inside_idiom(lowered, verb):
            continue
        hits.append(verb)
    return hits


def _match_action_targets(lowered: str) -> list[str]:
    hits: list[str] = []
    for target, pattern in _TARGET_PATTERNS:
        if pattern.search(lowered):
            hits.append(target)
    return hits


def _dedupe_ordered(items: list[str]) -> tuple[str, ...]:
    """Dedupe while preserving first-seen order.  Used so a signal
    shared between the verb and target lexicons (should any ever
    overlap again) never surfaces twice in ``matched_signals``."""
    return tuple(dict.fromkeys(items))


def _has_imperative_prefix(lowered: str) -> bool:
    return any(lowered.startswith(prefix) for prefix in _IMPERATIVE_PREFIXES)


def classify_workbench_transcript(
    transcript_text: str | None,
) -> VoiceWorkbenchClassification:
    """Classify a voice transcript as informational vs action-oriented.

    Deterministic, bounded, and explainable.  Defaults to informational
    to keep conversational runs clean; only fires action-oriented when
    a clear mutation verb pattern is present in a non-question context.
    """
    if transcript_text is None:
        return VoiceWorkbenchClassification(kind=CLASSIFICATION_INFORMATIONAL, reason=_REASON_EMPTY)
    raw = transcript_text.strip()
    if not raw:
        return VoiceWorkbenchClassification(kind=CLASSIFICATION_INFORMATIONAL, reason=_REASON_EMPTY)

    lowered = _normalize(raw)

    if _looks_like_question(lowered):
        return VoiceWorkbenchClassification(
            kind=CLASSIFICATION_INFORMATIONAL, reason=_REASON_QUESTION_FORM
        )

    verb_hits = _match_action_verbs(lowered)
    if not verb_hits:
        return VoiceWorkbenchClassification(
            kind=CLASSIFICATION_INFORMATIONAL,
            reason=_REASON_DEFAULT_CONVERSATIONAL,
        )

    target_hits = _match_action_targets(lowered)
    if target_hits:
        return VoiceWorkbenchClassification(
            kind=CLASSIFICATION_ACTION_ORIENTED,
            reason=_REASON_ACTION_VERB_WITH_TARGET,
            matched_signals=_dedupe_ordered(verb_hits + target_hits),
        )

    if _has_imperative_prefix(lowered):
        return VoiceWorkbenchClassification(
            kind=CLASSIFICATION_ACTION_ORIENTED,
            reason=_REASON_IMPERATIVE_WITH_ACTION_VERB,
            matched_signals=_dedupe_ordered(verb_hits),
        )

    return VoiceWorkbenchClassification(
        kind=CLASSIFICATION_INFORMATIONAL,
        reason=_REASON_DEFAULT_CONVERSATIONAL,
    )
