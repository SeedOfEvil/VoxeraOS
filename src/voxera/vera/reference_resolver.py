"""Bounded session-scoped reference resolution for Vera.

This module provides a conservative, fail-closed reference resolution layer
that maps natural in-session references (e.g. "that draft", "that result",
"the follow-up") to concrete referents using shared session context.

Architectural rules
-------------------
1. Session context is a **continuity aid only**, not a truth surface.
2. Preview truth > Queue truth > Artifact truth > Session context.
3. If a remembered reference conflicts with canonical truth, canonical truth wins.
4. If continuity is ambiguous, fail closed (do not guess).
5. No cross-session memory is introduced.
6. No speculative "AI knows what you meant" behavior.

Reference classes supported
---------------------------
- DRAFT: "that draft", "the draft", "the current draft", "the last draft"
- FILE: "that file", "the file", "the note", "the saved file"
- JOB_RESULT: "that result", "the result", "that job", "the last job"
- CONTINUATION: "the follow-up", "that follow-up", "the last one", "that one"
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, unique
from typing import Any

# ---------------------------------------------------------------------------
# Reference class taxonomy
# ---------------------------------------------------------------------------


@unique
class ReferenceClass(Enum):
    """Bounded set of in-session reference classes."""

    DRAFT = "draft"
    FILE = "file"
    JOB_RESULT = "job_result"
    CONTINUATION = "continuation"


# ---------------------------------------------------------------------------
# Resolution result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedReference:
    """A successfully resolved session-scoped reference."""

    reference_class: ReferenceClass
    value: str
    source: str  # which context field provided the resolution


@dataclass(frozen=True)
class UnresolvedReference:
    """A reference that could not be resolved — fail-closed result."""

    reference_class: ReferenceClass | None
    reason: str


# ---------------------------------------------------------------------------
# Phrase → reference class mapping
# ---------------------------------------------------------------------------

# Each tuple: (pattern, reference_class).
# Patterns are evaluated in order; first match wins.
# All matching is case-insensitive against the lowered message.

_DRAFT_PHRASES: tuple[str, ...] = (
    "that draft",
    "the draft",
    "the current draft",
    "the last draft",
    "the active draft",
    "my draft",
    "use the draft",
    "use the last draft",
    "use that draft",
    "save that draft",
    "save the draft",
    "save my draft",
)

_FILE_PHRASES: tuple[str, ...] = (
    "that file",
    "the file",
    "the note",
    "the saved file",
    "that note",
    "the saved note",
    "rename that file",
    "rename the file",
    "rename the note",
    "rename that note",
)

_JOB_RESULT_PHRASES: tuple[str, ...] = (
    "that result",
    "the result",
    "that job",
    "the job",
    "the last job",
    "that outcome",
    "the outcome",
    "the last result",
)

_CONTINUATION_PHRASES: tuple[str, ...] = (
    "that follow-up",
    "the follow-up",
    "that followup",
    "the followup",
    "the last one",
    "that one",
    "save that follow-up",
    "save the follow-up",
    "save that followup",
    "save the followup",
)


def classify_reference(message: str) -> ReferenceClass | None:
    """Classify which reference class a user message targets, if any.

    Returns ``None`` when no recognizable session-scoped reference phrase is
    present — the caller should proceed without reference resolution.
    """
    lowered = message.strip().lower()
    if not lowered:
        return None

    # Check in priority order: more specific before more general.
    for phrase in _CONTINUATION_PHRASES:
        if phrase in lowered:
            return ReferenceClass.CONTINUATION
    for phrase in _DRAFT_PHRASES:
        if phrase in lowered:
            return ReferenceClass.DRAFT
    for phrase in _FILE_PHRASES:
        if phrase in lowered:
            return ReferenceClass.FILE
    for phrase in _JOB_RESULT_PHRASES:
        if phrase in lowered:
            return ReferenceClass.JOB_RESULT

    return None


# ---------------------------------------------------------------------------
# Resolution logic
# ---------------------------------------------------------------------------


def _is_nonempty_ref(val: Any) -> bool:
    """Return True when *val* is a non-empty string reference."""
    return isinstance(val, str) and bool(val.strip())


def resolve_session_reference(
    message: str,
    session_context: dict[str, Any],
) -> ResolvedReference | UnresolvedReference:
    """Resolve a session-scoped reference from the user message.

    Uses the shared session context to find the intended referent.
    Returns :class:`ResolvedReference` when exactly one referent is
    resolvable, or :class:`UnresolvedReference` when resolution fails.

    This function is **fail-closed by design**: ambiguous or missing
    references always return ``UnresolvedReference``.
    """
    ref_class = classify_reference(message)
    if ref_class is None:
        return UnresolvedReference(
            reference_class=None,
            reason="no_recognizable_reference",
        )

    ctx = session_context if isinstance(session_context, dict) else {}

    if ref_class == ReferenceClass.DRAFT:
        return _resolve_draft(ctx)
    if ref_class == ReferenceClass.FILE:
        return _resolve_file(ctx)
    if ref_class == ReferenceClass.JOB_RESULT:
        return _resolve_job_result(ctx)
    if ref_class == ReferenceClass.CONTINUATION:
        return _resolve_continuation(ctx)

    return UnresolvedReference(reference_class=ref_class, reason="unknown_class")


def _resolve_draft(ctx: dict[str, Any]) -> ResolvedReference | UnresolvedReference:
    """Resolve a draft reference from session context."""
    # Prefer active_draft_ref, fall back to active_preview_ref.
    draft_ref = ctx.get("active_draft_ref")
    if _is_nonempty_ref(draft_ref):
        return ResolvedReference(
            reference_class=ReferenceClass.DRAFT,
            value=str(draft_ref).strip(),
            source="active_draft_ref",
        )
    preview_ref = ctx.get("active_preview_ref")
    if _is_nonempty_ref(preview_ref):
        return ResolvedReference(
            reference_class=ReferenceClass.DRAFT,
            value=str(preview_ref).strip(),
            source="active_preview_ref",
        )
    return UnresolvedReference(
        reference_class=ReferenceClass.DRAFT,
        reason="no_active_draft_or_preview",
    )


def _resolve_file(ctx: dict[str, Any]) -> ResolvedReference | UnresolvedReference:
    """Resolve a file reference from session context."""
    # Prefer last_saved_file_ref, fall back to active_draft_ref if it looks
    # like a file path (contains a dot or slash).
    file_ref = ctx.get("last_saved_file_ref")
    if _is_nonempty_ref(file_ref):
        return ResolvedReference(
            reference_class=ReferenceClass.FILE,
            value=str(file_ref).strip(),
            source="last_saved_file_ref",
        )
    draft_ref = ctx.get("active_draft_ref")
    if _is_nonempty_ref(draft_ref) and _looks_like_file_path(str(draft_ref)):
        return ResolvedReference(
            reference_class=ReferenceClass.FILE,
            value=str(draft_ref).strip(),
            source="active_draft_ref",
        )
    return UnresolvedReference(
        reference_class=ReferenceClass.FILE,
        reason="no_saved_file_reference",
    )


def _resolve_job_result(ctx: dict[str, Any]) -> ResolvedReference | UnresolvedReference:
    """Resolve a job/result reference from session context.

    Priority: last_completed_job_ref > last_reviewed_job_ref > last_submitted_job_ref.
    If multiple refs exist but point to different jobs, we still return the
    highest-priority one — canonical queue truth is validated downstream.
    """
    for field in (
        "last_completed_job_ref",
        "last_reviewed_job_ref",
        "last_submitted_job_ref",
    ):
        val = ctx.get(field)
        if _is_nonempty_ref(val):
            return ResolvedReference(
                reference_class=ReferenceClass.JOB_RESULT,
                value=str(val).strip(),
                source=field,
            )
    return UnresolvedReference(
        reference_class=ReferenceClass.JOB_RESULT,
        reason="no_job_or_result_reference",
    )


def _resolve_continuation(ctx: dict[str, Any]) -> ResolvedReference | UnresolvedReference:
    """Resolve a continuation/follow-up reference from session context.

    A continuation reference resolves to the most recent actionable referent:
    1. If there's an active preview (follow-up was drafted), use that.
    2. If there's a completed job, use that (follow-up grounded in evidence).
    3. If there's a submitted job, use that.
    """
    preview_ref = ctx.get("active_preview_ref")
    if _is_nonempty_ref(preview_ref):
        return ResolvedReference(
            reference_class=ReferenceClass.CONTINUATION,
            value=str(preview_ref).strip(),
            source="active_preview_ref",
        )
    for field in (
        "last_completed_job_ref",
        "last_reviewed_job_ref",
        "last_submitted_job_ref",
    ):
        val = ctx.get(field)
        if _is_nonempty_ref(val):
            return ResolvedReference(
                reference_class=ReferenceClass.CONTINUATION,
                value=str(val).strip(),
                source=field,
            )
    return UnresolvedReference(
        reference_class=ReferenceClass.CONTINUATION,
        reason="no_continuation_reference",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FILE_PATH_RE = re.compile(r"[./\\]")


def _looks_like_file_path(value: str) -> bool:
    """Return True when *value* looks like a file path rather than a bare ref."""
    return bool(_FILE_PATH_RE.search(value))


# ---------------------------------------------------------------------------
# Job-ID fallback resolution for early-exit dispatch
# ---------------------------------------------------------------------------


def resolve_job_id_from_context(
    session_context: dict[str, Any],
) -> str | None:
    """Return the best job-ID referent from session context, or None.

    Used as a fallback when the primary job-ID resolution path (handoff state,
    explicit message extraction) yields nothing.  Priority:

    1. ``last_completed_job_ref`` — most likely what "that result" means.
    2. ``last_reviewed_job_ref`` — recently inspected job.
    3. ``last_submitted_job_ref`` — recently submitted but not yet resolved.
    """
    ctx = session_context if isinstance(session_context, dict) else {}
    for field in (
        "last_completed_job_ref",
        "last_reviewed_job_ref",
        "last_submitted_job_ref",
    ):
        val = ctx.get(field)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None
