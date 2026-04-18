"""Voice Workbench spoken lifecycle commands seam.

Operators working through the Voice Workbench can — after Vera has
drafted a preview or the canonical queue has raised a pending approval
— say short bounded phrases like ``submit it``, ``send it``, ``run it``,
``save it``, ``approve it``, or ``deny it`` to move the existing
canonical artefact along its lifecycle.  This module is the narrow
bridge that turns such a transcript into a real lifecycle action on
**canonical** state only — never fabricating what the operator is
referring to.

Trust model, re-stated
----------------------
* Voice Workbench still never **drafts** previews here and never
  **invents** a target.  Every action in this module dispatches against
  canonical preview truth (``read_session_preview`` +
  :func:`voxera.vera.preview_submission.submit_active_preview_for_session`)
  or canonical approval truth (:class:`voxera.core.queue_daemon.MissionQueueDaemon`
  ``approvals_list`` / ``resolve_approval``).
* Fail-closed: if the canonical state to act on does not exist or is
  ambiguous (no preview, no pending approval, multiple pending
  approvals on this session with no distinguishing ref) the dispatcher
  returns a truthful negative result.  It never submits, approves, or
  denies something the operator did not actually authorize.
* Bounded phrase matching: the classifier only fires on exact short
  phrases (``submit it`` / ``send it`` / ``run it`` / ``save it`` for
  submit; ``approve it`` for approve; ``deny it`` / ``reject it`` for
  deny), with optional trailing punctuation.  Anything richer — a full
  sentence, a question, a new drafting request — stays in the regular
  Vera conversational lane where the canonical preview drafter and
  classifier can handle it.
* Same-session scoping: approve/deny actions only dispatch against
  approvals whose ``job`` matches a job the current voice session is
  linked to (via
  :func:`voxera.vera.session_store.register_session_linked_job`).  Any
  cross-session ambiguity fails closed — the operator is pointed back
  at canonical Vera.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..vera import session_store
from ..vera.preview_submission import (
    submit_active_preview_for_session,
)

# Canonical classification kinds.  Kept as bare strings so template
# conditionals and tests can compare stable literals without importing
# this module.
LIFECYCLE_ACTION_NONE = "none"
LIFECYCLE_ACTION_SUBMIT = "submit"
LIFECYCLE_ACTION_APPROVE = "approve"
LIFECYCLE_ACTION_DENY = "deny"

# Canonical dispatch status values.  Narrow and operator-readable.
LIFECYCLE_STATUS_SUBMITTED = "submitted"
LIFECYCLE_STATUS_NO_PREVIEW = "no_preview"
LIFECYCLE_STATUS_SUBMIT_FAILED = "submit_failed"
LIFECYCLE_STATUS_AMBIGUOUS_PREVIEW = "ambiguous_preview"
LIFECYCLE_STATUS_APPROVED = "approved"
LIFECYCLE_STATUS_DENIED = "denied"
LIFECYCLE_STATUS_NO_PENDING_APPROVAL = "no_pending_approval"
LIFECYCLE_STATUS_AMBIGUOUS_PENDING_APPROVAL = "ambiguous_pending_approval"
LIFECYCLE_STATUS_APPROVAL_FAILED = "approval_failed"
LIFECYCLE_STATUS_ERROR = "error"

# Reason codes surface the branch of the classifier that fired.  They
# are NOT operator-facing strings — the UI renders its own copy.
_REASON_EMPTY = "empty_or_missing_transcript"
_REASON_SUBMIT_PHRASE = "submit_phrase_matched"
_REASON_APPROVE_PHRASE = "approve_phrase_matched"
_REASON_DENY_PHRASE = "deny_phrase_matched"
_REASON_NO_MATCH = "no_lifecycle_phrase_matched"

# Bounded exact-phrase patterns (with optional trailing punctuation).
# We deliberately do NOT accept free-form sentences here; richer
# phrasings stay in the normal Vera drafting / confirmation lanes so
# the two surfaces cannot disagree on what "it" means.
_SUBMIT_RE = re.compile(r"^(?:submit|send|run|save)\s+(?:it|this|that)[.!?]*$", re.IGNORECASE)
_APPROVE_RE = re.compile(r"^approve\s+(?:it|this|that)[.!?]*$", re.IGNORECASE)
_DENY_RE = re.compile(r"^(?:deny|reject)\s+(?:it|this|that)[.!?]*$", re.IGNORECASE)


@dataclass(frozen=True)
class VoiceWorkbenchLifecycleClassification:
    """Typed result of classifying a transcript for lifecycle intent.

    ``kind`` is one of the ``LIFECYCLE_ACTION_*`` constants.  ``reason``
    identifies which branch fired; ``matched_phrase`` is the normalized
    transcript that matched a bounded pattern (or ``None`` for the
    no-match / empty cases).  The orchestrator uses ``kind`` as the
    dispatch key and treats ``LIFECYCLE_ACTION_NONE`` as "let the
    regular Vera conversational lane handle this".
    """

    kind: str
    reason: str
    matched_phrase: str | None = None


@dataclass(frozen=True)
class VoiceWorkbenchLifecycleResult:
    """Typed result of dispatching a lifecycle command.

    ``ok=True`` means the canonical lifecycle helper reported success
    (preview submitted, approval approved, approval denied).  ``ack``
    holds the short operator-facing sentence the UI renders (and the
    TTS lane may speak); ``job_id`` / ``approval_ref`` surface the
    concrete canonical id the action acted on when one exists.  In
    every failure case ``ok=False`` and ``status`` + ``error`` describe
    why no claim should be made.
    """

    ok: bool
    action: str
    status: str
    ack: str | None = None
    job_id: str | None = None
    approval_ref: str | None = None
    error: str | None = None


def classify_lifecycle_phrase(
    transcript_text: str | None,
) -> VoiceWorkbenchLifecycleClassification:
    """Classify a normalized transcript as a bounded lifecycle command.

    Returns ``LIFECYCLE_ACTION_NONE`` on empty input or when no bounded
    pattern matches.  The matcher strips surrounding whitespace and
    case-folds but does not attempt fuzzy / substring matching — the
    phrase must be the *entire* utterance so it cannot collide with
    longer natural-language turns the regular Vera lane handles.
    """
    if not transcript_text:
        return VoiceWorkbenchLifecycleClassification(
            kind=LIFECYCLE_ACTION_NONE,
            reason=_REASON_EMPTY,
        )
    normalized = transcript_text.strip()
    if not normalized:
        return VoiceWorkbenchLifecycleClassification(
            kind=LIFECYCLE_ACTION_NONE,
            reason=_REASON_EMPTY,
        )
    if _SUBMIT_RE.fullmatch(normalized):
        return VoiceWorkbenchLifecycleClassification(
            kind=LIFECYCLE_ACTION_SUBMIT,
            reason=_REASON_SUBMIT_PHRASE,
            matched_phrase=normalized,
        )
    if _APPROVE_RE.fullmatch(normalized):
        return VoiceWorkbenchLifecycleClassification(
            kind=LIFECYCLE_ACTION_APPROVE,
            reason=_REASON_APPROVE_PHRASE,
            matched_phrase=normalized,
        )
    if _DENY_RE.fullmatch(normalized):
        return VoiceWorkbenchLifecycleClassification(
            kind=LIFECYCLE_ACTION_DENY,
            reason=_REASON_DENY_PHRASE,
            matched_phrase=normalized,
        )
    return VoiceWorkbenchLifecycleClassification(
        kind=LIFECYCLE_ACTION_NONE,
        reason=_REASON_NO_MATCH,
    )


def _session_linked_job_refs(queue_root: Path, session_id: str) -> set[str]:
    """Return the canonical ``inbox-<uuid>.json`` refs linked to this session.

    Reads the session's linked-job registry through the canonical
    session-store accessor.  On any read failure returns an empty set —
    callers treat that as "no linked jobs on this session" and fail
    closed, which is the safe default for approve/deny dispatch.
    """
    try:
        # ``_read_linked_job_registry`` is the internal canonical accessor
        # used throughout ``session_debug_info``; we reuse it rather than
        # re-parse the session payload.
        registry = session_store._read_linked_job_registry(queue_root, session_id)
    except Exception:
        return set()
    tracked = registry.get("tracked") if isinstance(registry, dict) else None
    if not isinstance(tracked, list):
        return set()
    refs: set[str] = set()
    for item in tracked:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("job_ref") or "").strip()
        if raw:
            refs.add(Path(raw).name)
    return refs


def _dispatch_submit(
    *,
    session_id: str,
    queue_root: Path,
    submit_hook: Callable[..., tuple[str, str]],
) -> VoiceWorkbenchLifecycleResult:
    """Dispatch a spoken submit phrase against canonical preview truth.

    Delegates entirely to
    :func:`submit_active_preview_for_session` — the same seam canonical
    Vera uses from chat.  Passing ``preview=None`` means "submit whatever
    the canonical session says is active", which is exactly the
    behavior the operator implied by saying "submit it".  A missing
    canonical preview, an ambiguous preview state, or a queue write
    failure all surface as typed fail-closed results.
    """
    try:
        ack, status = submit_hook(
            queue_root=queue_root,
            session_id=session_id,
            preview=None,
            register_linked_job=session_store.register_session_linked_job,
        )
    except Exception as exc:
        return VoiceWorkbenchLifecycleResult(
            ok=False,
            action=LIFECYCLE_ACTION_SUBMIT,
            status=LIFECYCLE_STATUS_ERROR,
            error=f"{type(exc).__name__}: {exc}",
        )
    if status == "handoff_submitted":
        # Read back the persisted handoff state to recover the canonical
        # job id without parsing the ack text.
        handoff = session_store.read_session_handoff_state(queue_root, session_id) or {}
        job_id = str(handoff.get("job_id") or "").strip() or None
        return VoiceWorkbenchLifecycleResult(
            ok=True,
            action=LIFECYCLE_ACTION_SUBMIT,
            status=LIFECYCLE_STATUS_SUBMITTED,
            ack=ack,
            job_id=job_id,
        )
    if status == "handoff_missing_preview":
        return VoiceWorkbenchLifecycleResult(
            ok=False,
            action=LIFECYCLE_ACTION_SUBMIT,
            status=LIFECYCLE_STATUS_NO_PREVIEW,
            ack=ack,
        )
    if status == "handoff_ambiguous_preview_state":
        return VoiceWorkbenchLifecycleResult(
            ok=False,
            action=LIFECYCLE_ACTION_SUBMIT,
            status=LIFECYCLE_STATUS_AMBIGUOUS_PREVIEW,
            ack=ack,
        )
    # ``handoff_submit_failed`` and any unknown status ride this lane.
    return VoiceWorkbenchLifecycleResult(
        ok=False,
        action=LIFECYCLE_ACTION_SUBMIT,
        status=LIFECYCLE_STATUS_SUBMIT_FAILED,
        ack=ack,
    )


def _select_session_scoped_approval(
    *,
    approvals: list[dict[str, Any]],
    linked_refs: set[str],
) -> tuple[dict[str, Any] | None, str]:
    """Pick the single pending approval scoped to this session.

    Returns ``(approval, status)`` where ``status`` is one of:

    * ``LIFECYCLE_STATUS_NO_PENDING_APPROVAL`` — no approvals intersect
      the session's linked-job set.
    * ``LIFECYCLE_STATUS_AMBIGUOUS_PENDING_APPROVAL`` — more than one
      approval intersects, so "it" is ambiguous and we fail closed.
    * empty string — one unambiguous match, returned as ``approval``.

    Matching uses the canonical ``job`` field (``inbox-<uuid>.json``)
    which is the same shape ``register_session_linked_job`` normalizes
    the ref to.
    """
    if not linked_refs:
        return None, LIFECYCLE_STATUS_NO_PENDING_APPROVAL
    matches: list[dict[str, Any]] = []
    for item in approvals:
        if not isinstance(item, dict):
            continue
        job = str(item.get("job") or "").strip()
        if not job:
            continue
        if Path(job).name in linked_refs:
            matches.append(item)
    if not matches:
        return None, LIFECYCLE_STATUS_NO_PENDING_APPROVAL
    if len(matches) > 1:
        return None, LIFECYCLE_STATUS_AMBIGUOUS_PENDING_APPROVAL
    return matches[0], ""


def _dispatch_approval(
    *,
    action: str,
    session_id: str,
    queue_root: Path,
    approvals_list_hook: Callable[[], list[dict[str, Any]]],
    canonicalize_ref_hook: Callable[[str], str],
    resolve_approval_hook: Callable[[str, bool], bool],
) -> VoiceWorkbenchLifecycleResult:
    """Dispatch a spoken approve/deny phrase against canonical approval truth.

    Enforces same-session scoping: the approval being resolved must
    belong to a job the current Vera session already linked (via the
    session linked-job registry).  That keeps approve/deny from acting
    on pending approvals the operator never drove from this session,
    and keeps the dispatcher from guessing what "it" means when two
    approvals are pending at once.
    """
    approve = action == LIFECYCLE_ACTION_APPROVE
    try:
        approvals = approvals_list_hook()
    except Exception as exc:
        return VoiceWorkbenchLifecycleResult(
            ok=False,
            action=action,
            status=LIFECYCLE_STATUS_ERROR,
            error=f"approvals_list_failed: {type(exc).__name__}: {exc}",
        )
    linked_refs = _session_linked_job_refs(queue_root, session_id)
    approval, scope_status = _select_session_scoped_approval(
        approvals=approvals,
        linked_refs=linked_refs,
    )
    if approval is None:
        if scope_status == LIFECYCLE_STATUS_AMBIGUOUS_PENDING_APPROVAL:
            ack = (
                "I did not "
                + ("approve" if approve else "deny")
                + " anything because this session has more than one pending approval. "
                "Please specify which one in canonical Vera."
            )
        else:
            ack = (
                "I did not "
                + ("approve" if approve else "deny")
                + " anything because this session has no pending approval to act on."
            )
        return VoiceWorkbenchLifecycleResult(
            ok=False,
            action=action,
            status=scope_status,
            ack=ack,
        )

    approval_job = str(approval.get("job") or "").strip()
    try:
        canonical_ref = canonicalize_ref_hook(approval_job)
    except Exception as exc:
        return VoiceWorkbenchLifecycleResult(
            ok=False,
            action=action,
            status=LIFECYCLE_STATUS_APPROVAL_FAILED,
            approval_ref=approval_job or None,
            error=f"canonicalize_ref_failed: {type(exc).__name__}: {exc}",
        )
    try:
        resolved = resolve_approval_hook(canonical_ref, approve)
    except Exception as exc:
        return VoiceWorkbenchLifecycleResult(
            ok=False,
            action=action,
            status=LIFECYCLE_STATUS_APPROVAL_FAILED,
            approval_ref=canonical_ref,
            error=f"resolve_approval_failed: {type(exc).__name__}: {exc}",
        )
    if not resolved:
        return VoiceWorkbenchLifecycleResult(
            ok=False,
            action=action,
            status=LIFECYCLE_STATUS_APPROVAL_FAILED,
            approval_ref=canonical_ref,
            error="resolve_approval returned False",
        )

    verb_past = "approved" if approve else "denied"
    ack = (
        f"I {verb_past} the pending approval for {canonical_ref}. "
        "Canonical Vera is authoritative for the post-resolution state."
    )
    return VoiceWorkbenchLifecycleResult(
        ok=True,
        action=action,
        status=LIFECYCLE_STATUS_APPROVED if approve else LIFECYCLE_STATUS_DENIED,
        ack=ack,
        approval_ref=canonical_ref,
    )


def dispatch_spoken_lifecycle_command(
    *,
    classification: VoiceWorkbenchLifecycleClassification,
    session_id: str,
    queue_root: Path,
    submit_hook: Callable[..., tuple[str, str]] | None = None,
    approvals_list_hook: Callable[[], list[dict[str, Any]]] | None = None,
    canonicalize_ref_hook: Callable[[str], str] | None = None,
    resolve_approval_hook: Callable[[str, bool], bool] | None = None,
) -> VoiceWorkbenchLifecycleResult:
    """Dispatch a classified lifecycle phrase against canonical state.

    The helper accepts injectable hooks so tests (and the route) can
    wire in narrow fakes / real canonical helpers without this module
    importing the queue daemon directly (the daemon brings heavier
    dependencies and we want this seam to stay import-light).  When a
    hook is not supplied, the real canonical helper is used.

    ``LIFECYCLE_ACTION_NONE`` classifications return a bounded no-op
    result; this keeps the callsite uniform (no special branch for
    "not a lifecycle phrase") while making the fact that nothing was
    attempted explicit.
    """
    if classification.kind == LIFECYCLE_ACTION_NONE:
        return VoiceWorkbenchLifecycleResult(
            ok=False,
            action=LIFECYCLE_ACTION_NONE,
            status=LIFECYCLE_STATUS_ERROR,
            error="no lifecycle phrase",
        )
    if classification.kind == LIFECYCLE_ACTION_SUBMIT:
        submit = submit_hook or submit_active_preview_for_session
        return _dispatch_submit(
            session_id=session_id,
            queue_root=queue_root,
            submit_hook=submit,
        )
    if classification.kind in (LIFECYCLE_ACTION_APPROVE, LIFECYCLE_ACTION_DENY):
        if (
            approvals_list_hook is None
            or canonicalize_ref_hook is None
            or resolve_approval_hook is None
        ):
            from ..core.queue_daemon import MissionQueueDaemon

            daemon = MissionQueueDaemon(queue_root=queue_root)
            approvals_list_hook = approvals_list_hook or daemon.approvals_list
            canonicalize_ref_hook = canonicalize_ref_hook or daemon.canonicalize_approval_ref

            def _default_resolve(ref: str, approve: bool) -> bool:
                return bool(daemon.resolve_approval(ref, approve=approve))

            resolve_approval_hook = resolve_approval_hook or _default_resolve
        return _dispatch_approval(
            action=classification.kind,
            session_id=session_id,
            queue_root=queue_root,
            approvals_list_hook=approvals_list_hook,
            canonicalize_ref_hook=canonicalize_ref_hook,
            resolve_approval_hook=resolve_approval_hook,
        )
    # Unreachable under the current classifier, but fail-closed if a
    # future kind is added without updating the dispatch table.
    return VoiceWorkbenchLifecycleResult(
        ok=False,
        action=classification.kind,
        status=LIFECYCLE_STATUS_ERROR,
        error=f"unknown lifecycle action: {classification.kind}",
    )
