"""Interactive first-run walkthrough for Vera.

Teaches the Voxera preview-refinement loop step by step:
draft → refine in chat → preview updates → governed submit.

The walkthrough creates a real ``write_file`` preview and walks the user
through concrete refinement instructions.  Each step updates the live
preview via ``reset_active_preview`` so the user sees real preview
mutations.  The final submit uses the normal governed queue path — this
module never submits on its own.

Session state is tracked in a small ``walkthrough_state`` dict persisted
via ``session_store.write_session_walkthrough``.

This module is intentionally NOT a generic tutorial engine.  It owns
exactly one bounded walkthrough.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .preview_ownership import reset_active_preview
from .session_store import (
    read_session_walkthrough,
    write_session_walkthrough,
)

# ---------------------------------------------------------------------------
# Tour request detection
# ---------------------------------------------------------------------------

_TOUR_REQUEST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bstart\s+(?:the\s+)?voxera(?:os)?\s+tour\b", re.IGNORECASE),
    re.compile(r"\brun\s+(?:the\s+)?voxera(?:os)?\s+tour\b", re.IGNORECASE),
    re.compile(r"\bvoxera(?:os)?\s+tour\b", re.IGNORECASE),
    re.compile(r"\bfirst[- ]?run\s+tour\b", re.IGNORECASE),
)


def is_first_run_tour_request(message: str) -> bool:
    """Return True when the message is a clear request to start the tour."""
    text = message.strip()
    if not text:
        return False
    return any(p.search(text) for p in _TOUR_REQUEST_PATTERNS)


# ---------------------------------------------------------------------------
# Fresh session detection
# ---------------------------------------------------------------------------


def is_fresh_vera_session(
    turns: list[dict[str, str]],
    session_context: dict[str, Any],
) -> bool:
    """Return True when the session is genuinely fresh.

    A session is fresh when there are no prior user turns and no prior
    job submissions, completions, or reviews recorded.
    """
    user_turns = [t for t in turns if t.get("role") == "user"]
    if len(user_turns) > 1:
        return False
    return (
        session_context.get("last_submitted_job_ref") is None
        and session_context.get("last_completed_job_ref") is None
        and session_context.get("last_reviewed_job_ref") is None
    )


# ---------------------------------------------------------------------------
# Walkthrough step definitions
# ---------------------------------------------------------------------------

_INITIAL_NOTE_PATH = "~/VoxeraOS/notes/voxera-welcome.md"
_RENAMED_NOTE_PATH = "~/VoxeraOS/notes/voxera-quick-start.md"

_INITIAL_CONTENT = (
    "# Welcome to VoxeraOS\n\n"
    "VoxeraOS is an AI-assisted operating environment.\n\n"
    "- Vera explains and guides.\n"
    "- Files are concrete outputs you can inspect.\n"
    "- The queue is the trust boundary — changes go through it.\n"
    "- Artifacts are the evidence trail for every action.\n"
)

_STEP_2_CONTENT = (
    "# VoxeraOS Quick Start\n\n"
    "VoxeraOS helps you work safely with AI.\n"
    "Vera drafts, you refine, and the queue governs execution.\n"
)

_STEP_3_CONTENT = (
    "# VoxeraOS Quick Start\n\n"
    "VoxeraOS helps you work safely with AI.\n"
    "Vera drafts, you refine, and the queue governs execution.\n\n"
    "This note was created during the VoxeraOS guided tour.\n"
)


def _make_preview(path: str, content: str) -> dict[str, Any]:
    """Build a canonical write_file preview payload."""
    return {
        "goal": "VoxeraOS first-run walkthrough note",
        "write_file": {
            "path": path,
            "content": content,
            "mode": "overwrite",
        },
    }


# Each step: (assistant_text_factory, preview_factory, next_step_index | None)
# A next_step_index of None means "let the user submit normally".


def _step_0_start() -> tuple[str, dict[str, Any]]:
    """Step 0: introduce Voxera and create the initial preview."""
    text = (
        "Welcome to the VoxeraOS guided tour.\n\n"
        "VoxeraOS works like this: Vera drafts a **preview** of what will happen, "
        "you refine it in chat, and when you're ready you submit it through the "
        "**governed queue** — which produces an evidence trail you can inspect.\n\n"
        "I've prepared a preview for a welcome note. "
        "You can see it in the preview panel.\n\n"
        '**Next step:** Type: **"Change the content to something shorter and more casual."**'
    )
    preview = _make_preview(_INITIAL_NOTE_PATH, _INITIAL_CONTENT)
    return text, preview


def _step_1_refine() -> tuple[str, dict[str, Any]]:
    """Step 1: apply a content refinement."""
    text = (
        "Done — I've updated the preview with shorter, more casual content.\n\n"
        "This is how preview refinement works in Vera: you ask for a change, "
        "and the preview updates without anything being submitted yet.\n\n"
        '**Next step:** Type: **"Rename it to voxera-quick-start.md."**'
    )
    preview = _make_preview(_INITIAL_NOTE_PATH, _STEP_2_CONTENT)
    return text, preview


def _step_2_rename() -> tuple[str, dict[str, Any]]:
    """Step 2: rename the file."""
    text = (
        "Renamed — the preview now targets `voxera-quick-start.md`.\n\n"
        "You can rename, edit content, or change the goal at any point "
        "before submitting. The preview is yours to shape.\n\n"
        "**Next step:** Type: "
        '**"Add a final line saying this note was created during the VoxeraOS tour."**'
    )
    preview = _make_preview(_RENAMED_NOTE_PATH, _STEP_2_CONTENT)
    return text, preview


def _step_3_final_edit() -> tuple[str, dict[str, Any]]:
    """Step 3: add a final line, then prompt for submission."""
    text = (
        "Added — the preview now includes your tour line.\n\n"
        "The preview is complete. When you submit, this note will go through "
        "the governed queue as a real job. You'll be able to see the execution "
        "result, step artifacts, and evidence trail in the panel or CLI.\n\n"
        '**Final step:** Type **"submit it"** to send it through the queue.'
    )
    preview = _make_preview(_RENAMED_NOTE_PATH, _STEP_3_CONTENT)
    return text, preview


_WALKTHROUGH_STEPS = [_step_0_start, _step_1_refine, _step_2_rename, _step_3_final_edit]
WALKTHROUGH_TOTAL_STEPS = len(_WALKTHROUGH_STEPS)


# ---------------------------------------------------------------------------
# Walkthrough dispatch
# ---------------------------------------------------------------------------


def start_walkthrough(queue_root: Path, session_id: str) -> tuple[str, str]:
    """Start the interactive walkthrough.  Returns (assistant_text, status).

    Creates the initial preview and stores walkthrough state at step 0.
    """
    text, preview = _step_0_start()
    reset_active_preview(queue_root, session_id, preview)
    write_session_walkthrough(queue_root, session_id, {"active": True, "step": 0})
    return text, "walkthrough_step_0"


def advance_walkthrough(queue_root: Path, session_id: str) -> tuple[str, str] | None:
    """Advance the walkthrough by one step.

    Returns ``(assistant_text, status)`` if the walkthrough is active and
    there is a next step.  Returns ``None`` when:
    - no walkthrough is active
    - the walkthrough has reached the final step (user should submit)
    """
    state = read_session_walkthrough(queue_root, session_id)
    if state is None:
        return None
    current = state.get("step", 0)
    next_step = current + 1
    if next_step >= WALKTHROUGH_TOTAL_STEPS:
        # Final step already shown — let the user submit normally.
        return None
    step_fn = _WALKTHROUGH_STEPS[next_step]
    text, preview = step_fn()
    reset_active_preview(queue_root, session_id, preview)
    write_session_walkthrough(queue_root, session_id, {"active": True, "step": next_step})
    return text, f"walkthrough_step_{next_step}"


def clear_walkthrough(queue_root: Path, session_id: str) -> None:
    """Clear walkthrough state (e.g. after submit or session reset)."""
    write_session_walkthrough(queue_root, session_id, None)


def is_walkthrough_active(queue_root: Path, session_id: str) -> bool:
    """Return True when an interactive walkthrough is in progress."""
    return read_session_walkthrough(queue_root, session_id) is not None
