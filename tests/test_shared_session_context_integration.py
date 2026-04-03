"""Integration tests: shared session context updates through the Vera web layer.

These tests verify that the app.py lifecycle hooks correctly update the shared
session context at preview creation, submit/handoff, session clear, stale
preview cleanup, and follow-up/review lifecycle events.
"""

from __future__ import annotations

from voxera.vera import service as vera_service
from voxera.vera.context_lifecycle import (
    context_on_completion_ingested,
    context_on_handoff_submitted,
    context_on_preview_created,
    context_on_review_performed,
)
from voxera.vera_web import app as vera_app_module

from .vera_session_helpers import make_vera_session


def test_preview_creation_updates_context(tmp_path, monkeypatch):
    """When a preview is created via chat, context tracks active_draft_ref."""
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message):
        if user_message == "What is 2 + 2?":
            return {"answer": "2 + 2 is 4.", "status": "ok:test"}
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    # First turn creates a meaningful assistant artifact
    session.chat("What is 2 + 2?")
    # Second turn triggers save-from-artifact → creates preview
    session.chat("save that to a note")

    preview = session.preview()
    assert preview is not None, "save-that should have created a preview"
    ctx = session.session_context()
    assert ctx["active_preview_ref"] == "preview"
    assert ctx["active_draft_ref"] is not None


def test_session_clear_resets_context(tmp_path, monkeypatch):
    """POST /clear should reset the shared context to empty."""
    session = make_vera_session(monkeypatch, tmp_path)

    # Set some context
    vera_service.update_session_context(
        session.queue,
        session.session_id,
        active_topic="should be cleared",
        last_submitted_job_ref="inbox-abc.json",
    )
    ctx_before = session.session_context()
    assert ctx_before["active_topic"] == "should be cleared"

    # Clear the session
    resp = session.client.post("/clear", data={"session_id": session.session_id})
    assert resp.status_code == 200

    ctx_after = session.session_context()
    assert ctx_after["active_topic"] is None
    assert ctx_after["last_submitted_job_ref"] is None


def test_context_preserved_across_normal_turns(tmp_path, monkeypatch):
    """Context survives across multiple chat turns."""
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message):
        return {"answer": "Sure!", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    vera_service.update_session_context(
        session.queue,
        session.session_id,
        active_topic="persistent topic",
    )

    session.chat("hello")
    session.chat("how are you?")

    ctx = session.session_context()
    assert ctx["active_topic"] == "persistent topic"


def test_context_empty_for_fresh_session(tmp_path, monkeypatch):
    """A fresh session has empty context."""
    session = make_vera_session(monkeypatch, tmp_path)
    ctx = session.session_context()
    assert ctx["active_draft_ref"] is None
    assert ctx["active_preview_ref"] is None
    assert ctx["last_submitted_job_ref"] is None
    assert ctx["ambiguity_flags"] == []


# ---------------------------------------------------------------------------
# Lifecycle freshness: full workflow through session store
# ---------------------------------------------------------------------------


def test_lifecycle_rename_then_handoff(tmp_path, monkeypatch):
    """Draft → rename/save-as → handoff preserves the renamed path."""
    session = make_vera_session(monkeypatch, tmp_path)
    q, sid = session.queue, session.session_id

    context_on_preview_created(q, sid, draft_ref="notes/draft-v1.md")
    ctx = session.session_context()
    assert ctx["active_draft_ref"] == "notes/draft-v1.md"

    # Rename / save-as
    context_on_preview_created(q, sid, draft_ref="notes/renamed.md")
    ctx = session.session_context()
    assert ctx["active_draft_ref"] == "notes/renamed.md"

    # Submit
    context_on_handoff_submitted(
        q, sid, job_id="inbox-abc.json", saved_file_ref="~/VoxeraOS/notes/renamed.md"
    )
    ctx = session.session_context()
    assert ctx["active_draft_ref"] is None
    assert ctx["last_submitted_job_ref"] == "inbox-abc.json"
    assert ctx["last_saved_file_ref"] == "~/VoxeraOS/notes/renamed.md"


def test_lifecycle_handoff_completion_review(tmp_path, monkeypatch):
    """Handoff → completion → review updates context at each step."""
    session = make_vera_session(monkeypatch, tmp_path)
    q, sid = session.queue, session.session_id

    context_on_handoff_submitted(q, sid, job_id="inbox-xyz.json")
    ctx = session.session_context()
    assert ctx["last_submitted_job_ref"] == "inbox-xyz.json"

    context_on_completion_ingested(q, sid, job_id="inbox-xyz.json")
    ctx = session.session_context()
    assert ctx["last_completed_job_ref"] == "inbox-xyz.json"

    context_on_review_performed(q, sid, job_id="inbox-xyz.json")
    ctx = session.session_context()
    assert ctx["last_reviewed_job_ref"] == "inbox-xyz.json"


def test_lifecycle_review_updates_context_for_later_resolution(tmp_path, monkeypatch):
    """After review, reference resolution for 'the result' should find the reviewed job."""
    from voxera.vera.reference_resolver import ReferenceClass, resolve_session_reference

    session = make_vera_session(monkeypatch, tmp_path)
    q, sid = session.queue, session.session_id

    context_on_handoff_submitted(q, sid, job_id="inbox-reviewed.json")
    context_on_completion_ingested(q, sid, job_id="inbox-reviewed.json")
    context_on_review_performed(q, sid, job_id="inbox-reviewed.json")

    ctx = session.session_context()
    result = resolve_session_reference("summarize that result", ctx)
    assert hasattr(result, "value")
    assert result.reference_class == ReferenceClass.JOB_RESULT
    assert result.value == "inbox-reviewed.json"


def test_fresh_session_fail_closed_resolution(tmp_path, monkeypatch):
    """A fresh session with no context should fail-closed on resolution."""
    from voxera.vera.reference_resolver import UnresolvedReference, resolve_session_reference

    session = make_vera_session(monkeypatch, tmp_path)
    ctx = session.session_context()
    result = resolve_session_reference("show me the result", ctx)
    assert isinstance(result, UnresolvedReference)


def test_session_clear_then_fail_closed(tmp_path, monkeypatch):
    """After session clear, context is empty and resolution fails closed."""
    from voxera.vera.reference_resolver import UnresolvedReference, resolve_session_reference

    session = make_vera_session(monkeypatch, tmp_path)
    q, sid = session.queue, session.session_id

    context_on_handoff_submitted(q, sid, job_id="inbox-abc.json")
    context_on_completion_ingested(q, sid, job_id="inbox-abc.json")

    resp = session.client.post("/clear", data={"session_id": sid})
    assert resp.status_code == 200

    ctx = session.session_context()
    result = resolve_session_reference("what happened to the job", ctx)
    assert isinstance(result, UnresolvedReference)


def test_failed_followup_draft_reference_fails_closed(tmp_path, monkeypatch):
    """After follow-up handoff + failed job, 'save that draft' fails closed.

    Exact repro: draft → handoff → complete → follow-up → handoff → fail
    → 'save that draft' must not silently create a preview.
    """
    from voxera.vera.context_lifecycle import context_on_followup_preview_prepared
    from voxera.vera.reference_resolver import UnresolvedReference, resolve_session_reference

    session = make_vera_session(monkeypatch, tmp_path)
    q, sid = session.queue, session.session_id

    # Draft created and refined
    context_on_preview_created(q, sid, draft_ref="notes/report.md")
    context_on_preview_created(q, sid, draft_ref="notes/final-report.md")

    # Handoff
    context_on_handoff_submitted(q, sid, job_id="inbox-orig.json")

    # Job completes
    context_on_completion_ingested(q, sid, job_id="inbox-orig.json")

    # Follow-up prepared
    context_on_followup_preview_prepared(q, sid, source_job_id="inbox-orig.json")

    # Follow-up handoff
    context_on_handoff_submitted(q, sid, job_id="inbox-followup.json")

    # Follow-up job fails (completion ingested)
    context_on_completion_ingested(q, sid, job_id="inbox-followup.json")

    # "save that draft" should fail closed — no active draft/preview
    ctx = session.session_context()
    result = resolve_session_reference("save that draft", ctx)
    assert isinstance(result, UnresolvedReference)
    assert result.reason == "no_active_draft_or_preview"


def test_surfaced_runtime_output_not_saveable_as_draft(tmp_path, monkeypatch):
    """Surfaced runtime/result content must not become a saveable artifact.

    Exact repro: after handoff + completion, Vera surfaces runtime output
    like a file stat line. 'save that draft' must not convert this into
    a preview/note file.
    """
    from voxera.vera.saveable_artifacts import build_saveable_assistant_artifact

    # These are surfaced runtime outputs, not authored content
    runtime_outputs = [
        "/home/user/VoxeraOS/notes/report.md: type=file size=632B modified=2026-03-28",
        "/home/user/notes/plan.md exists (file).",
        "/home/user/notes/missing.md does not exist.",
        "I reviewed canonical VoxeraOS evidence for `inbox-abc.json`.\n"
        "- State: `succeeded`\n- Lifecycle state: `done`",
        "Your linked file organize job completed successfully. "
        "I have the canonical result available for follow-up.",
        "Your linked goal job completed successfully. Diagnostics snapshot: CPU 45%, mem 2.1GB.",
        "There is no active draft or preview in this session.",
    ]
    for text in runtime_outputs:
        artifact = build_saveable_assistant_artifact(text)
        assert artifact is None, f"Runtime output should not become a saveable artifact: {text!r}"

    # Real authored content should still be saveable
    authored = (
        "Here is a comprehensive analysis of the quarterly results. "
        "The key findings include increased revenue and improved margins."
    )
    artifact = build_saveable_assistant_artifact(authored)
    assert artifact is not None, "Real authored content should be saveable"


def test_active_authored_preview_rename_save_as_works(tmp_path, monkeypatch):
    """An active authored preview must support rename/save-as.

    Exact repro: create authored draft → refine → 'save it as newname.md'
    Expected: destination path updates, content preserved.
    """
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message, **kw):
        if "queue truth" in user_message.lower():
            return {
                "answer": "Queue truth is the canonical execution boundary.",
                "status": "ok:test",
            }
        if "shorter" in user_message.lower():
            return {
                "answer": "Queue truth: canonical boundary.",
                "status": "ok:test",
            }
        return {"answer": "Done.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    # Step 1: Create authored draft
    session.chat("write me a short note about queue truth")
    preview1 = session.preview()
    assert preview1 is not None, "Draft should be created"
    wf1 = preview1.get("write_file", {})
    assert wf1.get("content"), "Draft should have content"

    # Step 2: Refine
    session.chat("make it shorter")

    # Step 3: Rename / save-as
    session.chat("save it as queue-truth-operator-brief.md")
    preview3 = session.preview()
    assert preview3 is not None, "Preview should still exist after rename"
    wf3 = preview3.get("write_file", {})
    assert wf3.get("path", "").endswith("queue-truth-operator-brief.md"), (
        f"Path should be renamed, got: {wf3.get('path')}"
    )
    assert wf3.get("content"), "Content should be preserved after rename"

    # Verify context tracks the renamed draft
    ctx = session.session_context()
    assert ctx["active_preview_ref"] == "preview"
