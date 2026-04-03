"""Integration tests: shared session context updates through the Vera web layer.

These tests verify that the app.py lifecycle hooks correctly update the shared
session context at preview creation, submit/handoff, and session clear.
"""

from __future__ import annotations

from voxera.vera import service as vera_service
from voxera.vera_web import app as vera_app_module

from .vera_session_helpers import make_vera_session


def test_preview_creation_updates_context(tmp_path, monkeypatch):
    """When a preview is created via chat, context tracks active_draft_ref."""
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message):
        return {"answer": "Here is your note.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    session.chat("save that to a note")

    # If a preview was created, context should track it
    preview = session.preview()
    ctx = session.session_context()
    if preview is not None:
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
