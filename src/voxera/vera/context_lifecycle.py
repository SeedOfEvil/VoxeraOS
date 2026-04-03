"""Explicit lifecycle update points for shared session context.

This module provides named, bounded functions that update shared session
context at each lifecycle event in the Vera workflow.  Each function
encapsulates **exactly** which context fields should change for a given
event, keeping update logic coherent, testable, and auditable.

Architectural rules preserved
-----------------------------
- Shared session context is a **continuity aid only**.
- Preview truth > Queue truth > Artifact truth > Session context.
- Context updates here track "what is in play", they never claim to be
  authoritative truth.
- If context is stale or missing, consumers must fail closed.
- No cross-session memory is introduced.

Ownership
---------
- This module owns the *definitions* of lifecycle update semantics.
- ``app.py`` and ``chat_early_exit_dispatch.py`` own the *call sites*.
- ``session_store.update_session_context`` owns persistence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .session_store import clear_session_context, update_session_context

# ---------------------------------------------------------------------------
# Preview lifecycle
# ---------------------------------------------------------------------------


def context_on_preview_created(
    queue_root: Path,
    session_id: str,
    *,
    draft_ref: str = "preview",
) -> dict[str, Any]:
    """Update context when a new preview is created or an existing one is revised.

    Covers: preview created, preview revised/refined, rename/save-as,
    builder-generated update, follow-up preview, revised-from-evidence
    preview, save-follow-up preview.
    """
    return update_session_context(
        queue_root,
        session_id,
        active_draft_ref=draft_ref,
        active_preview_ref="preview",
    )


def context_on_preview_cleared(
    queue_root: Path,
    session_id: str,
) -> dict[str, Any]:
    """Update context when the active preview is cleared or cleaned up.

    Covers: stale preview cleanup, explicit preview discard.
    Clears preview-related refs so reference resolution does not resolve
    a phantom preview.
    """
    return update_session_context(
        queue_root,
        session_id,
        active_draft_ref=None,
        active_preview_ref=None,
    )


# ---------------------------------------------------------------------------
# Handoff / submit lifecycle
# ---------------------------------------------------------------------------


def context_on_handoff_submitted(
    queue_root: Path,
    session_id: str,
    *,
    job_id: str,
    saved_file_ref: str | None = None,
) -> dict[str, Any]:
    """Update context when a preview is successfully submitted to the queue.

    Clears active preview/draft refs (preview is no longer "in play") and
    records the submitted job ID so downstream reference resolution
    ("that job", "the result") can find it.
    """
    updates: dict[str, Any] = {
        "active_preview_ref": None,
        "active_draft_ref": None,
        "last_submitted_job_ref": job_id,
    }
    if saved_file_ref:
        updates["last_saved_file_ref"] = saved_file_ref
    return update_session_context(queue_root, session_id, **updates)


# ---------------------------------------------------------------------------
# Completion ingestion lifecycle
# ---------------------------------------------------------------------------


def context_on_completion_ingested(
    queue_root: Path,
    session_id: str,
    *,
    job_id: str,
) -> dict[str, Any]:
    """Update context when a linked job completion is ingested.

    Records the completed job so reference resolution for "that result",
    "the last job", etc. resolves to the freshest completion.
    """
    return update_session_context(
        queue_root,
        session_id,
        last_completed_job_ref=job_id,
    )


# ---------------------------------------------------------------------------
# Review lifecycle
# ---------------------------------------------------------------------------


def context_on_review_performed(
    queue_root: Path,
    session_id: str,
    *,
    job_id: str,
) -> dict[str, Any]:
    """Update context when a job review is performed.

    Records the reviewed job so downstream follow-up and reference
    resolution reflect the most recently inspected result.
    """
    return update_session_context(
        queue_root,
        session_id,
        last_reviewed_job_ref=job_id,
    )


# ---------------------------------------------------------------------------
# Follow-up / continuation preview lifecycle
# ---------------------------------------------------------------------------


def context_on_followup_preview_prepared(
    queue_root: Path,
    session_id: str,
    *,
    draft_ref: str = "preview",
    source_job_id: str | None = None,
) -> dict[str, Any]:
    """Update context when a follow-up preview is prepared from evidence.

    Covers: generic follow-up, revised-from-evidence, save-follow-up.
    Sets active preview refs and optionally records the source job as
    the last reviewed job (follow-up preparation implies a review step).
    """
    updates: dict[str, Any] = {
        "active_draft_ref": draft_ref,
        "active_preview_ref": "preview",
    }
    if source_job_id:
        updates["last_reviewed_job_ref"] = source_job_id
    return update_session_context(queue_root, session_id, **updates)


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


def context_on_session_cleared(
    queue_root: Path,
    session_id: str,
) -> None:
    """Reset context when the session is cleared.

    Delegates to ``clear_session_context`` which resets all fields to
    the empty default.
    """
    clear_session_context(queue_root, session_id)
