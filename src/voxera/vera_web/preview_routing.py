"""Explicit preview-routing lane precedence for Vera's chat dispatch.

The goal of this module is to make the order in which ``chat()`` in
``app.py`` considers preview-touching lanes **legible**. Before this
module was added, the dispatch order was correct but implicit: the
branches in ``chat()`` were arranged by convention and required reading
~1000 lines of orchestration to understand which lane would claim a
given turn.

This module does NOT replace the dispatch logic. ``app.py`` still runs
the same branches in the same order. Instead it provides:

* A :class:`PreviewLane` enum that names the canonical lanes.
* :func:`canonical_preview_lane_order` which returns the precedence
  order that ``app.py`` must keep in sync with its branch order.
* :func:`is_active_preview_revision_turn`, the canonical gate for
  lane 2 ("active preview revision / follow-up mutation"). Other lanes
  use this predicate to **fail closed** when a turn that clearly
  belongs to the active-preview revision lane would otherwise be
  hijacked.

Canonical lane precedence
-------------------------
1. ``EXPLICIT_SUBMIT`` — explicit submit / handoff on the active
   preview. Always wins when a preview is available and the message is
   a canonical submit phrase.
2. ``ACTIVE_PREVIEW_REVISION`` — revision or follow-up mutation of the
   active preview (``"make it longer"``, ``"change the content to X"``,
   ``"use Python instead"``, ``"save it as note.md"``). Claims the turn
   before automation lifecycle or evidence review so those lanes cannot
   hijack a clear revision turn.
3. ``AUTOMATION_LIFECYCLE`` — management of a saved automation
   definition (``"show it"``, ``"disable it"``, ``"run it now"``,
   ``"delete the automation"``). Only claims the turn when the active
   preview (if any) is *not* a normal preview that the user is
   currently revising.
4. ``FOLLOWUP_FROM_EVIDENCE`` — follow-up / save-follow-up / revised
   preview drafted from a previously completed job. Handled by the
   early-exit dispatch layer.
5. ``PREVIEW_CREATION`` — new deterministic or LLM-assisted preview
   creation (automation draft, code shell, writing draft, structured
   file write, builder-authored preview).
6. ``READ_ONLY_EARLY_EXIT`` — read-only utility lanes that return
   deterministically without touching preview state (time, weather,
   investigation compare/summary/expand, near-miss submit rejection).
7. ``CONVERSATIONAL`` — general chat / LLM reply with no preview
   implication.

Routing precedence is not magic. Each lane still makes its own claim
in ``app.py``; this module records the order and supplies shared gate
predicates so overlapping ownership is easier to audit.

Fail-closed rationale
---------------------
When a normal active preview is present and the message could be
interpreted as either "mutate the active preview" or "spawn a new
evidence-grounded follow-up", we prefer not to mutate the wrong
object. The :func:`is_active_preview_revision_turn` gate is
deliberately narrow (specific revision verbs + rename/save-as), so
``app.py`` layers an additional belt-and-suspenders check over
evidence-review follow-up phrasing (``is_save_followup_request`` /
``is_revise_from_evidence_request``) when the active preview is
normal — this treats ambiguous phrasings as revision candidates so
the early-exit follow-up branches cannot silently replace the active
preview. Legitimate evidence-grounded follow-ups still work when no
active preview is present, or when the user explicitly submits /
clears the active preview first.
"""

from __future__ import annotations

import re
from enum import Enum, unique
from typing import Any

from ..vera.draft_revision import looks_like_preview_rename_or_save_as_request

__all__ = [
    "PreviewLane",
    "canonical_preview_lane_order",
    "is_active_preview_revision_turn",
    "is_normal_preview",
]


@unique
class PreviewLane(Enum):
    """Canonical routing lanes that may claim a Vera chat turn.

    The enum order matches :func:`canonical_preview_lane_order` so that
    ``PreviewLane`` values can be compared with ``<`` to reason about
    precedence.
    """

    EXPLICIT_SUBMIT = 1
    ACTIVE_PREVIEW_REVISION = 2
    AUTOMATION_LIFECYCLE = 3
    FOLLOWUP_FROM_EVIDENCE = 4
    PREVIEW_CREATION = 5
    READ_ONLY_EARLY_EXIT = 6
    CONVERSATIONAL = 7


def canonical_preview_lane_order() -> tuple[PreviewLane, ...]:
    """Return the canonical lane precedence as a tuple.

    ``app.py`` branch order must match this tuple. When a new lane is
    added the tuple must be updated so reviewers can keep the two
    surfaces aligned.
    """
    return (
        PreviewLane.EXPLICIT_SUBMIT,
        PreviewLane.ACTIVE_PREVIEW_REVISION,
        PreviewLane.AUTOMATION_LIFECYCLE,
        PreviewLane.FOLLOWUP_FROM_EVIDENCE,
        PreviewLane.PREVIEW_CREATION,
        PreviewLane.READ_ONLY_EARLY_EXIT,
        PreviewLane.CONVERSATIONAL,
    )


# ---------------------------------------------------------------------------
# Active-preview revision gate
# ---------------------------------------------------------------------------

# A small, deliberately narrow set of revision/follow-up signals that
# clearly belong to the "mutate the active preview" lane. The list is
# intentionally conservative: each pattern is a well-known revision verb
# or transformation instruction. Patterns that are too generic
# (``"update"``, ``"change"`` without an object) are excluded because
# they are more likely to be ambiguous and should fail closed.
#
# This gate is used by :func:`is_active_preview_revision_turn` which is
# called by the dispatch layer *before* lanes 3 and 4 to prevent them
# from hijacking a clear revision turn.
_REVISION_VERB_PATTERNS: tuple[str, ...] = (
    # Length / conciseness
    r"\bmake\s+(?:it|that|this)\s+(?:more\s+)?(?:longer|shorter|concise|brief|detailed|terse)\b",
    r"\b(?:lengthen|shorten|expand|compress)\s+(?:it|that|this)\b",
    # Content / body transformations
    r"\bchange\s+(?:the\s+)?(?:content|text|body|wording)\b",
    r"\breplace\s+(?:the\s+)?(?:content|text|body)\b",
    r"\bupdate\s+(?:the\s+)?(?:content|text|body)\b",
    r"\brewrite\s+(?:it|that|this|the\s+content)\b",
    # Language / script kind
    r"\buse\s+python\s+instead\b",
    r"\bmake\s+(?:it|that)\s+python\b",
    r"\buse\s+bash\s+instead\b",
    r"\bmake\s+(?:it|that)\s+bash\b",
    r"\bmake\s+(?:it|that)\s+a\s+(?:python|bash|shell|node|js|typescript)\s+script\b",
    r"\bconvert\s+(?:it|that|this)\s+to\s+(?:python|bash|shell|node|js|typescript)\b",
    # Target path / filename / script-ness
    r"\bchange\s+(?:the\s+)?(?:target\s+path|file\s*path|path|filename|file\s*name)\b",
    r"\bmake\s+(?:it|that|this)\s+a\s+(?:follow[- ]?up\s+)?script\b",
    r"\bmake\s+(?:it|that|this)\s+into\s+a\s+(?:follow[- ]?up\s+)?script\b",
    r"\bturn\s+(?:it|that|this)\s+into\s+a\s+(?:follow[- ]?up\s+)?script\b",
    # Generic revision shortcut
    r"\brevise\s+(?:it|that|this)\b",
    # Transformation into list/checklist (authored-content follow-ups)
    r"\bturn\s+(?:it|that|this)\s+into\s+(?:a\s+)?(?:checklist|list|outline|bullet(?:s|\s+list)?)\b",
    r"\bas\s+a\s+checklist\b",
    r"\bmake\s+(?:it|that|this)\s+(?:more\s+)?(?:operator|user)[- ](?:facing|focused|friendly)\b",
)

_REVISION_VERB_RE = re.compile("|".join(_REVISION_VERB_PATTERNS), re.IGNORECASE)


def is_normal_preview(preview: dict[str, Any] | None) -> bool:
    """Return True when *preview* is a normal file/script preview.

    A "normal" preview is a non-empty preview dict that is not an
    automation definition. Normal previews include governed write_file
    previews (code, script, writing draft), mission previews,
    diagnostics previews, and file-organize previews.

    Used by the dispatch layer to distinguish "there's an active
    automation preview under revision" (automation revision lane) from
    "there's an active normal preview" (active-preview revision lane
    with the automation lifecycle gate turned on). Empty / malformed
    dicts do not count as "normal" so that revision-lane protections
    never fire on a phantom preview.
    """
    if not isinstance(preview, dict) or not preview:
        return False
    if preview.get("preview_type") == "automation_definition":
        return False
    # A real preview always carries one of these authoring surfaces.
    # Any missing-all-surfaces dict is not a real preview we should
    # protect from revision-lane collisions.
    return bool(
        preview.get("goal")
        or preview.get("write_file")
        or preview.get("steps")
        or preview.get("file_organize")
        or preview.get("mission_id")
        or preview.get("enqueue_child")
    )


def is_active_preview_revision_turn(
    message: str,
    *,
    active_preview: dict[str, Any] | None,
) -> bool:
    """Return True when *message* is a clear revision of the active preview.

    This is the canonical gate for lane 2. It returns True only when:

    * An active preview exists.
    * The preview is a normal (non-automation) preview.
    * The message matches a conservative revision/follow-up pattern OR
      a rename/save-as pattern.

    The patterns are deliberately conservative so that ambiguous
    turns do NOT steal the turn for the revision lane — they fall
    through to later lanes or the LLM path, which is the fail-closed
    behavior we want. Adding very generic patterns here would re-open
    the hijack class of bugs this module exists to fix.

    Callers in later lanes can invoke this predicate to short-circuit
    their own claim when a clear revision turn is in flight.
    """
    if not is_normal_preview(active_preview):
        return False
    normalized = (message or "").strip()
    if not normalized:
        return False
    if looks_like_preview_rename_or_save_as_request(normalized):
        return True
    return bool(_REVISION_VERB_RE.search(normalized))
