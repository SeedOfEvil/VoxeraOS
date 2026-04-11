"""Automation lane — extracted from ``voxera.vera_web.app``.

This module owns the lane-specific decision logic for the four
automation-touching branches that previously lived inline in
``chat()`` in ``app.py``:

1. **Automation preview submit** — save the active automation preview
   as a durable automation definition (does NOT enqueue a job).
2. **Automation preview drafting / revision** — draft a new automation
   preview from an authoring intent, or revise the active automation
   preview.
3. **Automation lifecycle management** — show / enable / disable /
   delete / run-now / history for saved automation definitions.
4. **Automation shell materialization** — post-clarification completion
   and direct-automation-request Python-script shell synthesis, which
   feed the existing code-draft flow.

Ownership boundaries (important — must be preserved)
----------------------------------------------------
* ``app.py`` remains the top-level orchestrator. It still owns lane
  order and chooses when to call into each lane entry point.
* Every preview-state mutation in this module goes through the
  approved helpers in :mod:`voxera.vera.preview_ownership`
  (``reset_active_preview`` / ``record_submit_success``) — there are
  no direct ``write_session_preview`` writes here.
* The save-vs-execute truth boundary for automation previews lives in
  :mod:`voxera.vera.automation_preview` and
  :mod:`voxera.vera.automation_lifecycle`; this module only calls those
  stable entry points.
* Lane functions return a :class:`AutomationLaneResult` with
  ``matched=True`` when they claim the turn, so the caller can perform
  the final ``append_session_turn`` / ``append_routing_debug_entry`` /
  ``_render_page`` orchestration uniformly. Turn append, routing debug,
  and render remain in ``app.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...core.code_draft_intent import is_code_draft_request
from ...vera.automation_lifecycle import (
    dispatch_lifecycle_action,
    is_automation_lifecycle_intent,
)
from ...vera.automation_preview import (
    AutomationClarification,
    AutomationPreview,
    draft_automation_preview,
    is_automation_authoring_intent,
    is_automation_preview,
    revise_automation_preview,
    submit_automation_preview,
)
from ...vera.context_lifecycle import (
    context_on_automation_lifecycle_action,
    context_on_automation_saved,
)
from ...vera.preview_ownership import (
    record_submit_success,
    reset_active_preview,
)
from ...vera.preview_submission import (
    normalize_preview_payload,
    should_submit_active_preview,
)
from ...vera.session_store import (
    write_session_last_automation_preview,
)

__all__ = [
    "AutomationLaneResult",
    "try_submit_automation_preview_lane",
    "try_automation_draft_or_revision_lane",
    "try_automation_lifecycle_lane",
    "try_materialize_automation_shell",
    # Regex/detectors and helpers (re-exported through app.py for tests).
    "_AUTOMATION_INTENT_RE",
    "_AUTOMATION_CLARIFICATION_QUESTION_RE",
    "_AUTOMATION_DETAIL_SIGNAL_RE",
    "_DIRECT_AUTOMATION_VERB_RE",
    "_DIRECT_AUTOMATION_PATH_TOKEN_RE",
    "_DIRECT_AUTOMATION_ACTION_RE",
    "_DIRECT_AUTOMATION_SUBJECT_RE",
    "_PREVIEWABLE_AUTOMATION_INTENT_RE",
    "_PREVIEWABLE_AUTOMATION_SUBJECT_RE",
    "_PREVIEWABLE_AUTOMATION_ACTION_HINT_RE",
    "_PREVIEWABLE_AUTOMATION_CLARIFICATION_REPLY",
    "_detect_automation_clarification_completion",
    "_looks_like_direct_automation_request",
    "_looks_like_previewable_automation_intent",
    "_synthesize_direct_automation_preview",
]


# ---------------------------------------------------------------------------
# Lane result
# ---------------------------------------------------------------------------


@dataclass
class AutomationLaneResult:
    """Result returned by an automation lane entry point.

    When ``matched`` is ``False`` the lane declined the turn and the
    caller should move on to the next lane. When ``matched`` is
    ``True`` the lane has already performed its internal session writes
    (preview installation via ``preview_ownership`` helpers, etc.) and
    the caller must only:

    1. Call ``append_session_turn`` with ``assistant_text``.
    2. Call ``append_routing_debug_entry`` with ``status`` and
       ``dispatch_source`` (and ``matched_early_exit`` when set).
    3. Call ``_render_page`` with ``status``.

    ``pending_preview_after`` carries the post-write active preview
    value when the lane installed or revised a preview, so the caller
    can update its local pending-preview reference if it continues past
    the lane. When ``matched`` is ``True`` callers return early, so
    this field is only informational in that case; it exists for
    parity with the shell-materialization helper.
    """

    matched: bool
    assistant_text: str = ""
    status: str = ""
    dispatch_source: str = ""
    matched_early_exit: bool = False
    pending_preview_after: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Detectors for the automation shell materialization paths
# ---------------------------------------------------------------------------


_AUTOMATION_INTENT_RE = re.compile(
    r"\b(?:process|automation|workflow|automate|monitor|watch|detect)\b.*"
    r"\b(?:folder|directory|file|files|path)\b",
    re.IGNORECASE,
)

_AUTOMATION_CLARIFICATION_QUESTION_RE = re.compile(
    r"\?|\b(?:source|destination|where|which folder|what should|what action|how often|what (?:trigger|condition))\b",
    re.IGNORECASE,
)

_AUTOMATION_DETAIL_SIGNAL_RE = re.compile(
    r"(?:[~./][\w./-]+|"  # path-like token
    r"\b(?:source|destination|action|trigger|when|every|interval|timeout|stability)\s*[:=])",
    re.IGNORECASE,
)


def _detect_automation_clarification_completion(
    message: str,
    *,
    pending_preview: dict[str, object] | None,
    turns: list[dict[str, str]],
) -> dict[str, object] | None:
    """Detect a clarification answer for an automation/process-style request.

    The previous PR's code-draft recovery only fires when the original request
    matches ``is_code_draft_request`` (requires explicit language keyword or
    code filename).  Process/automation phrasing like "I want a process that
    detects a new folder..." does not match, so a Python-script clarification
    flow could not materialize a preview.

    This helper closes that gap with a narrow detector:

    1. No preview currently exists.
    2. The current message is not a new informational/writing/control turn
       (caller responsibility — these are gated upstream).
    3. The most recent assistant turn looks like a clarification question.
    4. A recent user turn contains automation/process intent (process/automate/
       monitor/watch + folder/directory/file).
    5. The current message provides specific clarification details (path tokens
       or structured ``key: value`` clarification fields).

    Returns a synthesized Python-script preview shell so the standard code-draft
    flow can inject the actual generated code.  Returns None when any condition
    is not met (fail-closed).
    """
    if pending_preview is not None:
        return None
    if is_code_draft_request(message):
        return None
    if not _AUTOMATION_DETAIL_SIGNAL_RE.search(message):
        return None
    if not turns:
        return None
    last_assistant: str | None = None
    for turn in reversed(turns):
        if str(turn.get("role") or "").strip().lower() == "assistant":
            last_assistant = str(turn.get("text") or "").strip()
            break
    if not last_assistant:
        return None
    if not _AUTOMATION_CLARIFICATION_QUESTION_RE.search(last_assistant):
        return None
    has_automation_intent = False
    for turn in reversed(turns[-8:]):
        if str(turn.get("role") or "").strip().lower() != "user":
            continue
        prior_text = str(turn.get("text") or "").strip()
        if prior_text == message.strip():
            continue
        if _AUTOMATION_INTENT_RE.search(prior_text):
            has_automation_intent = True
            break
    if not has_automation_intent:
        return None
    shell = {
        "goal": "draft a python script for the requested automation as automation.py",
        "write_file": {
            "path": "~/VoxeraOS/notes/automation.py",
            "content": "",
            "mode": "overwrite",
        },
    }
    try:
        return normalize_preview_payload(shell)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Direct automation/script preview detection (no clarification required)
# ---------------------------------------------------------------------------
#
# Handles fully specified single-turn requests like:
#   "I need a process that continuously watches ./incoming. When a folder is
#    fully copied in, add a status.txt file containing processed! and then
#    move it to ./processed."
#
# The narrower clarification-recovery helpers above only fire after a prior
# clarification exchange.  Direct requests must pass four structural gates
# so the lane stays tight:
#   (1) automation verb  — watch/monitor/detect/automate/poll (+ tense forms)
#   (2) path token       — ~/X, ./X, or /X
#   (3) action verb      — add/write/move/copy/create/append/rename/delete
#   (4) file/dir subject — folder/directory/file/path (+ plurals)

_DIRECT_AUTOMATION_VERB_RE = re.compile(
    r"\b(?:watch|watches|watching|monitor|monitors|monitoring|"
    r"detect|detects|detecting|automate|automates|automating|"
    r"poll|polls|polling)\b",
    re.IGNORECASE,
)

_DIRECT_AUTOMATION_PATH_TOKEN_RE = re.compile(r"(?:[~./][\w./-]*[\w/])")

_DIRECT_AUTOMATION_ACTION_RE = re.compile(
    r"\b(?:add(?:s|ing|ed)?|write|writes|writing|move|moves|moving|"
    r"copy|copies|copying|create(?:s|d)?|creating|append(?:s|ing|ed)?|"
    r"rename(?:s|d)?|renaming|delete(?:s|d)?|deleting)\b",
    re.IGNORECASE,
)

_DIRECT_AUTOMATION_SUBJECT_RE = re.compile(
    r"\b(?:folder|folders|directory|directories|file|files|path|paths)\b",
    re.IGNORECASE,
)


def _looks_like_direct_automation_request(message: str) -> bool:
    """Return True when the message is a fully specified automation/script request.

    All four structural signals must be present to enter this lane:
    automation verb, path token, action verb, and file/directory subject.
    This is deliberately narrow — simple file ops ("move a.txt to b.txt"),
    informational queries, writing drafts, and weather questions do not
    match, so the detector only widens the code-draft lane for clearly
    previewable automation requests.
    """
    text = message.strip()
    if not text:
        return False
    return (
        bool(_DIRECT_AUTOMATION_VERB_RE.search(text))
        and bool(_DIRECT_AUTOMATION_PATH_TOKEN_RE.search(text))
        and bool(_DIRECT_AUTOMATION_ACTION_RE.search(text))
        and bool(_DIRECT_AUTOMATION_SUBJECT_RE.search(text))
    )


# ---------------------------------------------------------------------------
# Previewable automation/process intent — broader, clarification-routing detector
# ---------------------------------------------------------------------------
#
# Handles first-turn requests that clearly describe a previewable automation/
# process/script (e.g. "I need a process that identifies a new folder copied
# into a specific folder and then copies it to another folder") but lack the
# explicit path token / narrow verb required by ``_looks_like_direct_automation
# _request``.  These should never get a blanket first-turn refusal — the right
# behavior is to ask a focused clarification.
#
# This detector is structural, not keyword-only.  All three signals must be
# present so unrelated informational, writing, weather, or simple file-intent
# requests do not match:
#   (1) automation intent verb — process/automation/automate/script/workflow/
#       monitor/watch/detect/identify/poll (+ tense forms)
#   (2) file/folder/directory subject — folder/directory/file/path (+ plurals)
#   (3) action-on-arrival/source-destination hint — copy/move/add/write/create/
#       trigger/another folder/new folder/when phrasing
#
# Used to convert the blanket "I was not able to prepare a governed preview"
# refusal into a focused clarification question — does NOT itself materialize
# a preview or weaken the trust model.

_PREVIEWABLE_AUTOMATION_INTENT_RE = re.compile(
    r"\b(?:process|automation|automate|automating|automates|"
    r"script|workflow|"
    r"monitor|monitors|monitoring|"
    r"watch|watches|watching|"
    r"detect|detects|detecting|"
    r"identify|identifies|identifying|"
    r"poll|polls|polling)\b",
    re.IGNORECASE,
)

_PREVIEWABLE_AUTOMATION_SUBJECT_RE = re.compile(
    r"\b(?:folder|folders|directory|directories|file|files|path|paths)\b",
    re.IGNORECASE,
)

_PREVIEWABLE_AUTOMATION_ACTION_HINT_RE = re.compile(
    r"\b(?:copy|copies|copied|copying|"
    r"move|moves|moved|moving|"
    r"add|adds|added|adding|"
    r"write|writes|wrote|writing|"
    r"create|creates|created|creating|"
    r"append|appends|appended|appending|"
    r"rename|renames|renamed|renaming|"
    r"delete|deletes|deleted|deleting|"
    r"trigger|triggers|triggered|"
    r"another\s+(?:folder|directory|file|path)|"
    r"new\s+(?:folder|directory|file|path)|"
    r"on\s+arrival|"
    r"when\s+(?:a|an|new|something|it|they|the))\b",
    re.IGNORECASE,
)


def _looks_like_previewable_automation_intent(message: str) -> bool:
    """Return True when the message clearly describes a previewable automation request.

    Broader than ``_looks_like_direct_automation_request``: does not require an
    explicit path token, so first-turn requests phrased in natural language
    (without ``./incoming``-style paths) are still recognized as previewable.

    All three structural signals must match — automation intent verb, file or
    directory subject, and an action-on-arrival/source-destination hint — so
    informational, weather, writing, and simple file-intent requests do not
    match.  Used only to route blanket first-turn refusals into a focused
    clarification question; never used to materialize a preview directly.
    """
    text = message.strip()
    if not text:
        return False
    if not _PREVIEWABLE_AUTOMATION_INTENT_RE.search(text):
        return False
    if not _PREVIEWABLE_AUTOMATION_SUBJECT_RE.search(text):
        return False
    return bool(_PREVIEWABLE_AUTOMATION_ACTION_HINT_RE.search(text))


_PREVIEWABLE_AUTOMATION_CLARIFICATION_REPLY = (
    "I can help with that. To prepare a governed preview I need a few details:\n\n"
    "- Which source folder should be watched (full path, e.g. `~/VoxeraOS/notes/incoming`)?\n"
    "- Which destination folder should receive the items (full path)?\n"
    "- What should happen when a new folder is detected (for example: add a "
    "marker file, then move the folder)?\n\n"
    "Once I have those details I can draft a script for you to review."
)


def _synthesize_direct_automation_preview() -> dict[str, object] | None:
    """Synthesize an empty Python-script preview shell for direct automation requests.

    Returns a normalized preview payload or None on normalization failure.
    The actual code content is injected by the standard code-draft flow after
    the LLM reply is generated.
    """
    shell = {
        "goal": "draft a python script for the requested automation as automation.py",
        "write_file": {
            "path": "~/VoxeraOS/notes/automation.py",
            "content": "",
            "mode": "overwrite",
        },
    }
    try:
        return normalize_preview_payload(shell)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Lane entry points
# ---------------------------------------------------------------------------


def try_submit_automation_preview_lane(
    *,
    message: str,
    pending_preview: dict[str, Any] | None,
    queue_root: Path,
    session_id: str,
) -> AutomationLaneResult:
    """Claim the turn when the active preview is an automation preview and
    the user asked to submit it.

    Submitting an automation preview saves a durable automation definition
    via :func:`voxera.vera.automation_preview.submit_automation_preview`;
    it does NOT enqueue a queue job. All continuity writes go through
    the approved helpers so the automation save-vs-execute truth
    boundary is preserved.
    """
    if not should_submit_active_preview(message, preview_available=pending_preview is not None):
        return AutomationLaneResult(matched=False)
    if not (isinstance(pending_preview, dict) and is_automation_preview(pending_preview)):
        return AutomationLaneResult(matched=False)

    result = submit_automation_preview(pending_preview, queue_root)
    # Automation submit does NOT emit a queue job; it saves a durable
    # definition. Clear the preview slot through the approved helper and
    # then refresh the continuity refs.
    record_submit_success(queue_root, session_id)
    context_on_automation_saved(queue_root, session_id, automation_id=result.automation_id)
    # Stash the preview so post-submit continuity can describe it.
    write_session_last_automation_preview(queue_root, session_id, pending_preview)
    return AutomationLaneResult(
        matched=True,
        assistant_text=result.ack,
        status="automation_definition_saved",
        dispatch_source="submit_automation_preview",
    )


def try_automation_draft_or_revision_lane(
    *,
    message: str,
    pending_preview: dict[str, Any] | None,
    diagnostics_service_turn: bool,
    queue_root: Path,
    session_id: str,
) -> AutomationLaneResult:
    """Claim the turn when an automation preview is active (revision) or
    when the message has an authoring intent and no preview exists (draft).

    Both branches install the resulting automation preview through
    :func:`voxera.vera.preview_ownership.reset_active_preview` with
    ``mark_handoff_ready=False`` because the automation submit path
    saves a durable definition rather than emitting a queue job.
    """
    # Revision branch — active preview is an automation preview.
    if isinstance(pending_preview, dict) and is_automation_preview(pending_preview):
        revision = revise_automation_preview(message, pending_preview)
        if isinstance(revision, AutomationPreview):
            reset_active_preview(
                queue_root,
                session_id,
                revision.preview,
                draft_ref="automation_preview",
                mark_handoff_ready=False,
            )
            text = (
                "I updated the automation preview.\n\n"
                f"{revision.explanation}\n\n"
                "Say **go ahead** to save this automation definition, or "
                "tell me what to change."
            )
            return AutomationLaneResult(
                matched=True,
                assistant_text=text,
                status="automation_preview_revised",
                dispatch_source="automation_revision",
                pending_preview_after=revision.preview,
            )
        if isinstance(revision, AutomationClarification):
            return AutomationLaneResult(
                matched=True,
                assistant_text=revision.question,
                status="automation_clarification",
                dispatch_source="automation_revision_clarify",
            )
        return AutomationLaneResult(matched=False)

    # Drafting branch — no preview yet, but authoring intent is present.
    if (
        pending_preview is None
        and is_automation_authoring_intent(message)
        and not diagnostics_service_turn
    ):
        draft = draft_automation_preview(message, active_preview=None)
        if isinstance(draft, AutomationPreview):
            reset_active_preview(
                queue_root,
                session_id,
                draft.preview,
                draft_ref="automation_preview",
                mark_handoff_ready=False,
            )
            text = (
                "Here's an automation preview:\n\n"
                f"{draft.explanation}\n\n"
                "Say **go ahead** to save this automation definition, or "
                "tell me what to change."
            )
            return AutomationLaneResult(
                matched=True,
                assistant_text=text,
                status="automation_preview_drafted",
                dispatch_source="automation_drafting",
                pending_preview_after=draft.preview,
            )
        if isinstance(draft, AutomationClarification):
            return AutomationLaneResult(
                matched=True,
                assistant_text=draft.question,
                status="automation_clarification",
                dispatch_source="automation_drafting_clarify",
            )

    return AutomationLaneResult(matched=False)


def try_automation_lifecycle_lane(
    *,
    message: str,
    pending_preview: dict[str, Any] | None,
    active_preview_revision_in_flight: bool,
    session_context: dict[str, Any] | None,
    last_automation_preview: dict[str, Any] | None,
    queue_root: Path,
    session_id: str,
) -> AutomationLaneResult:
    """Claim the turn when the message is a lifecycle request for a saved
    automation definition (show / enable / disable / delete / run-now /
    history).

    This lane steps aside when:

    * The active preview is an automation preview (revision lane owns it).
    * ``active_preview_revision_in_flight`` is True — a clear revision of a
      normal active preview must not be hijacked by overlapping lifecycle
      wording ("run it now", "show me the file").
    """
    if not is_automation_lifecycle_intent(message):
        return AutomationLaneResult(matched=False)
    if isinstance(pending_preview, dict) and is_automation_preview(pending_preview):
        return AutomationLaneResult(matched=False)
    if active_preview_revision_in_flight:
        return AutomationLaneResult(matched=False)

    lifecycle = dispatch_lifecycle_action(
        message,
        queue_root=queue_root,
        session_context=session_context,
        last_automation_preview=last_automation_preview,
    )
    if not lifecycle.matched:
        return AutomationLaneResult(matched=False)

    context_on_automation_lifecycle_action(
        queue_root,
        session_id,
        automation_id=lifecycle.automation_id,
        deleted=lifecycle.definition_deleted,
    )
    if lifecycle.definition_deleted:
        # Clear stashed preview since the definition was deleted.
        write_session_last_automation_preview(queue_root, session_id, None)
    return AutomationLaneResult(
        matched=True,
        assistant_text=lifecycle.assistant_text,
        status=lifecycle.status,
        dispatch_source="automation_lifecycle",
        matched_early_exit=True,
    )


def try_materialize_automation_shell(
    *,
    message: str,
    pending_preview: dict[str, Any] | None,
    turns: list[dict[str, str]],
    is_info_query: bool,
    is_explicit_writing_transform: bool,
    conversational_answer_first_turn: bool,
    is_voxera_control_turn: bool,
    looks_like_new_unrelated_query: bool,
    queue_root: Path,
    session_id: str,
) -> dict[str, Any] | None:
    """Materialize a Python-script automation preview shell when the current
    turn is either a clarification answer or a direct automation request.

    Returns the new pending preview dict when a shell was installed (via
    :func:`voxera.vera.preview_ownership.reset_active_preview`), or None
    when neither path fired.

    This helper does NOT claim the turn on its own — the caller still
    runs the code-draft flow in the same turn so the generated code is
    injected into the shell. It therefore does not return an
    :class:`AutomationLaneResult` but only the updated preview.

    Both paths share the same precondition guards with slight
    differences:

    * Clarification-completion additionally guards on
      ``looks_like_new_unrelated_query`` being False so a fresh question
      does not get rerouted as a clarification answer.
    * Direct-automation requires
      :func:`_looks_like_direct_automation_request` to match.
    """
    # Post-clarification completion
    if (
        pending_preview is None
        and not is_code_draft_request(message)
        and not is_info_query
        and not is_explicit_writing_transform
        and not conversational_answer_first_turn
        and not is_voxera_control_turn
        and not looks_like_new_unrelated_query
    ):
        shell = _detect_automation_clarification_completion(
            message, pending_preview=pending_preview, turns=turns
        )
        if shell is not None:
            reset_active_preview(
                queue_root,
                session_id,
                shell,
                draft_ref="~/VoxeraOS/notes/automation.py",
            )
            return shell

    # Direct automation request (no clarification needed)
    if (
        pending_preview is None
        and not is_code_draft_request(message)
        and not is_info_query
        and not is_explicit_writing_transform
        and not conversational_answer_first_turn
        and not is_voxera_control_turn
        and _looks_like_direct_automation_request(message)
    ):
        shell = _synthesize_direct_automation_preview()
        if shell is not None:
            reset_active_preview(
                queue_root,
                session_id,
                shell,
                draft_ref="~/VoxeraOS/notes/automation.py",
            )
            return shell

    return None
