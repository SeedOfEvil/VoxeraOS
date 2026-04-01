"""Characterization tests for the Vera pre-handoff draft-creation bug.

Covers the four observed failure modes:
1. "Draft a short markdown note explaining..." → fail-closed instead of real preview
2. "Create a draft explanation as explanation.txt." → fail-closed instead of real preview
3. "Write a short markdown file explaining the queue boundary." → wrong submission
   language instead of draft content
4. "Draft a short note...save it as explanation.txt." → path-only mutation without
   fresh authored content

Root causes:
- ``is_writing_draft_request`` classified all four prompts as False (missing "note"
  in _DIRECT_WRITING_RE; "create" missing from _WRITING_VERB_RE; _SAVE_ONLY_RE
  blocking "write...file" and "save it as" variants)
- ``_guardrail_submission_claim`` fired on LLM content that contained "queued"
  (explaining VoxeraOS queue boundary) even during a writing draft turn
- ``naming_mutation_request`` path in ``assemble_assistant_reply`` overrode the
  actual LLM content with "Updated the draft destination..." even when the turn
  was a writing draft turn
"""

from __future__ import annotations

import pytest

from voxera.core.writing_draft_intent import is_writing_draft_request
from voxera.vera_web import app as vera_app_module
from voxera.vera_web.response_shaping import assemble_assistant_reply

from .vera_session_helpers import make_vera_session

# ---------------------------------------------------------------------------
# Unit tests: is_writing_draft_request classification
# ---------------------------------------------------------------------------

SHOULD_BE_WRITING_DRAFT = [
    # Observed failing prompts
    "Draft a short markdown note explaining how VoxeraOS keeps execution safe.",
    "Create a draft explanation as explanation.txt.",
    "Write a short markdown file explaining the queue boundary.",
    "Draft a short note about VoxeraOS safety and save it as explanation.txt.",
    # Related variants
    "Draft a note explaining the deployment pipeline.",
    "Write a short note about the queue boundary.",
    "Draft a markdown file explaining the safety model.",
    "Create a draft note explaining the architecture.",
    "Write a markdown note explaining how this works.",
]

SHOULD_NOT_BE_WRITING_DRAFT = [
    # Save-existing-content requests (not new content creation)
    "save that to a note",
    "write that to a note",
    "put that in a file",
    # Pure informational queries
    "What is VoxeraOS?",
    "explain how python works",
    # Checklist/planning (conversational mode)
    "create a checklist for deployment",
    "make me a to-do list",
    # Regression guard: "make a note" is organizational, not a writing draft
    "make a note for later about buying milk",
]


@pytest.mark.parametrize("msg", SHOULD_BE_WRITING_DRAFT)
def test_is_writing_draft_request_true_for_clear_drafts(msg: str) -> None:
    assert is_writing_draft_request(msg), (
        f"Expected is_writing_draft_request({msg!r}) to be True (short new-content draft request)"
    )


@pytest.mark.parametrize("msg", SHOULD_NOT_BE_WRITING_DRAFT)
def test_is_writing_draft_request_false_for_non_drafts(msg: str) -> None:
    assert not is_writing_draft_request(msg), (
        f"Expected is_writing_draft_request({msg!r}) to be False (not a writing draft)"
    )


# ---------------------------------------------------------------------------
# Unit tests: assemble_assistant_reply — naming-mutation override exemption
# ---------------------------------------------------------------------------


def _base_reply_kwargs() -> dict:
    return dict(
        message="",
        pending_preview=None,
        builder_payload=None,
        in_voxera_preview_flow=False,
        is_code_draft_turn=False,
        is_writing_draft_turn=False,
        is_enrichment_turn=False,
        conversational_answer_first_turn=False,
        is_json_content_request=False,
        is_voxera_control_turn=False,
        explicit_targeted_content_refinement=False,
        preview_update_rejected=False,
        generation_content_refresh_failed_closed=False,
        reply_status="ok",
    )


def test_naming_mutation_override_skipped_for_writing_draft_turn() -> None:
    """When is_writing_draft_turn=True, naming-mutation should not override the reply."""
    shell_preview: dict[str, object] = {
        "goal": "draft a note as explanation.txt",
        "write_file": {
            "path": "~/VoxeraOS/notes/explanation.txt",
            "content": "VoxeraOS keeps execution safe by...",
            "mode": "overwrite",
        },
    }
    kwargs = _base_reply_kwargs()
    kwargs["message"] = "Draft a short note about VoxeraOS safety and save it as explanation.txt."
    kwargs["builder_payload"] = shell_preview
    kwargs["is_writing_draft_turn"] = True
    kwargs["in_voxera_preview_flow"] = True
    guarded_answer = (
        "VoxeraOS keeps execution safe by sandboxing all jobs and requiring explicit "
        "human approval before any state-changing operation is performed."
    )
    result = assemble_assistant_reply(guarded_answer, **kwargs)
    # Should NOT be the "Updated the draft destination..." message
    assert "Updated the draft destination" not in result.assistant_text, (
        "Writing draft turn should not produce 'Updated the draft destination' override"
    )
    # The actual content should pass through (or at least not be suppressed)
    assert result.assistant_text.strip(), "assistant_text should not be empty"


def test_naming_mutation_override_still_fires_for_non_writing_draft_turn() -> None:
    """Naming-mutation override still applies for non-writing-draft rename requests."""
    shell_preview: dict[str, object] = {
        "goal": "draft a note as note.md",
        "write_file": {
            "path": "~/VoxeraOS/notes/renamed.md",
            "content": "some content",
            "mode": "overwrite",
        },
    }
    kwargs = _base_reply_kwargs()
    kwargs["message"] = "save it as renamed.md"
    kwargs["builder_payload"] = shell_preview
    kwargs["is_writing_draft_turn"] = False
    kwargs["in_voxera_preview_flow"] = True
    result = assemble_assistant_reply("I updated the note path.", **kwargs)
    # For a pure rename turn (not writing draft), the naming-mutation control reply fires
    assert "Updated the draft destination" in result.assistant_text


# ---------------------------------------------------------------------------
# Integration tests: chat() end-to-end flow for all 4 observed prompts
# ---------------------------------------------------------------------------

_FAIL_CLOSED_MSG = "I was not able to prepare a governed preview for this request."
_SUBMISSION_NOT_CONFIRMED = "I have not submitted anything to VoxeraOS yet."
_DESTINATION_ONLY_MSG = "Updated the draft destination to"


def _make_writing_reply_fn(content: str):
    """Return a fake generate_vera_reply that emits real authored content."""

    async def _fake_reply(**kwargs):
        return {"answer": content, "status": "ok:test"}

    return _fake_reply


def _make_builder_fn(path: str, with_content: str = ""):
    """Return a fake generate_preview_builder_update returning a shell payload."""

    async def _fake_builder(**kwargs):
        return {
            "goal": f"draft a note as {path}",
            "write_file": {
                "path": f"~/VoxeraOS/notes/{path}",
                "content": with_content,
                "mode": "overwrite",
            },
        }

    return _fake_builder


_SAFE_NOTE_CONTENT = (
    "# VoxeraOS Execution Safety\n\n"
    "VoxeraOS keeps execution safe through sandboxing and human-in-the-loop approval. "
    "Every job must pass through the governed queue before any state change occurs."
)

_QUEUE_BOUNDARY_CONTENT = (
    "# Queue Boundary\n\n"
    "The queue boundary separates user intent from system execution. "
    "Jobs enter the queue only after explicit human approval. "
    "No file is written or process is started until a job is dequeued and dispatched."
)


@pytest.mark.asyncio
async def test_draft_short_markdown_note_creates_preview(tmp_path, monkeypatch):
    """Prompt 1: 'Draft a short markdown note explaining...' must create a real preview."""
    session = make_vera_session(monkeypatch, tmp_path)
    monkeypatch.setattr(
        vera_app_module, "generate_vera_reply", _make_writing_reply_fn(_SAFE_NOTE_CONTENT)
    )
    monkeypatch.setattr(
        vera_app_module, "generate_preview_builder_update", _make_builder_fn("safety-note.md")
    )

    resp = session.chat("Draft a short markdown note explaining how VoxeraOS keeps execution safe.")
    assert resp.status_code == 200

    preview = session.preview()
    assert preview is not None, "A real preview should have been created"
    wf = preview.get("write_file", {})
    assert wf.get("content", "").strip(), "Preview should have authored content, not empty"

    turns = session.turns()
    assistant_replies = [t["text"] for t in turns if t["role"] == "assistant"]
    assert assistant_replies, "There should be an assistant reply"
    last_reply = assistant_replies[-1]
    assert _FAIL_CLOSED_MSG not in last_reply, (
        "Should NOT produce fail-closed 'not able to prepare' message"
    )


@pytest.mark.asyncio
async def test_create_draft_explanation_as_txt_creates_preview(tmp_path, monkeypatch):
    """Prompt 2: 'Create a draft explanation as explanation.txt.' must create a real preview."""
    session = make_vera_session(monkeypatch, tmp_path)
    monkeypatch.setattr(
        vera_app_module, "generate_vera_reply", _make_writing_reply_fn(_SAFE_NOTE_CONTENT)
    )
    monkeypatch.setattr(
        vera_app_module, "generate_preview_builder_update", _make_builder_fn("explanation.txt")
    )

    resp = session.chat("Create a draft explanation as explanation.txt.")
    assert resp.status_code == 200

    preview = session.preview()
    assert preview is not None, "A real preview should have been created"
    wf = preview.get("write_file", {})
    assert wf.get("content", "").strip(), "Preview should have authored content, not empty"

    turns = session.turns()
    assistant_replies = [t["text"] for t in turns if t["role"] == "assistant"]
    assert assistant_replies
    last_reply = assistant_replies[-1]
    assert _FAIL_CLOSED_MSG not in last_reply, (
        "Should NOT produce fail-closed 'not able to prepare' message"
    )


@pytest.mark.asyncio
async def test_write_short_markdown_file_does_not_produce_submission_language(
    tmp_path, monkeypatch
):
    """Prompt 3: 'Write a short markdown file explaining the queue boundary.' must not produce submission language."""
    # The LLM content explains queuing — this triggered the _guardrail_submission_claim
    session = make_vera_session(monkeypatch, tmp_path)
    monkeypatch.setattr(
        vera_app_module, "generate_vera_reply", _make_writing_reply_fn(_QUEUE_BOUNDARY_CONTENT)
    )
    monkeypatch.setattr(
        vera_app_module, "generate_preview_builder_update", _make_builder_fn("queue-boundary.md")
    )

    resp = session.chat("Write a short markdown file explaining the queue boundary.")
    assert resp.status_code == 200

    turns = session.turns()
    assistant_replies = [t["text"] for t in turns if t["role"] == "assistant"]
    assert assistant_replies
    last_reply = assistant_replies[-1]
    assert _SUBMISSION_NOT_CONFIRMED not in last_reply, (
        "Should NOT produce 'I have not submitted anything to VoxeraOS yet' for a writing draft"
    )

    preview = session.preview()
    assert preview is not None, "A real preview should have been created"
    wf = preview.get("write_file", {})
    assert wf.get("content", "").strip(), "Preview content should not be empty"


@pytest.mark.asyncio
async def test_draft_note_with_save_as_creates_content_not_just_path(tmp_path, monkeypatch):
    """Prompt 4: 'Draft a short note...save it as explanation.txt.' must bind real content."""
    session = make_vera_session(monkeypatch, tmp_path)
    monkeypatch.setattr(
        vera_app_module, "generate_vera_reply", _make_writing_reply_fn(_SAFE_NOTE_CONTENT)
    )
    monkeypatch.setattr(
        vera_app_module, "generate_preview_builder_update", _make_builder_fn("explanation.txt")
    )

    resp = session.chat("Draft a short note about VoxeraOS safety and save it as explanation.txt.")
    assert resp.status_code == 200

    preview = session.preview()
    assert preview is not None, "A real preview should have been created"
    wf = preview.get("write_file", {})
    assert wf.get("content", "").strip(), (
        "Preview should have authored content, not empty path-only shell"
    )

    turns = session.turns()
    assistant_replies = [t["text"] for t in turns if t["role"] == "assistant"]
    assert assistant_replies
    last_reply = assistant_replies[-1]
    assert _DESTINATION_ONLY_MSG not in last_reply, (
        "Should NOT produce 'Updated the draft destination' path-only message for a writing draft"
    )
