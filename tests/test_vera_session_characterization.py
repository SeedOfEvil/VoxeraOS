from __future__ import annotations

import json

from voxera.vera import service as vera_service
from voxera.vera_web import app as vera_app_module

from .vera_session_helpers import make_vera_session


def test_concise_answer_then_save_that_creates_preview(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message):
        _ = turns
        if user_message == "What is 2 + 2?":
            return {"answer": "2 + 2 is 4.", "status": "ok:test"}
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    assert session.chat("What is 2 + 2?").status_code == 200
    assert session.chat("save that to a note").status_code == 200

    preview = session.preview()
    assert preview is not None
    assert preview["write_file"]["content"] == "2 + 2 is 4."
    assert preview["write_file"]["path"].startswith("~/VoxeraOS/notes/note-")


def test_concise_answer_then_thanks_then_save_that_keeps_meaningful_answer(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message):
        _ = turns
        if user_message == "What is 2 + 2?":
            return {"answer": "2 + 2 is 4.", "status": "ok:test"}
        return {"answer": "You're welcome!", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    session.chat("What is 2 + 2?")
    session.chat("thanks")
    session.chat("save that to a note")

    preview = session.preview()
    assert preview is not None
    assert preview["write_file"]["content"] == "2 + 2 is 4."
    assert "welcome" not in preview["write_file"]["content"].lower()


def test_explanation_then_save_that_creates_preview(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message):
        _ = turns
        if user_message == "Explain photosynthesis simply.":
            return {
                "answer": (
                    "Photosynthesis lets plants use sunlight, water, and carbon dioxide to make sugar."
                ),
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    session.chat("Explain photosynthesis simply.")
    session.chat("save that to a note")

    preview = session.preview()
    assert preview is not None
    assert "sunlight" in preview["write_file"]["content"].lower()
    assert "carbon dioxide" in preview["write_file"]["content"].lower()


def test_active_preview_rename_path_revision_and_submit_remain_truthful(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message):
        _ = turns
        if user_message == "Explain photosynthesis simply.":
            return {
                "answer": "Photosynthesis lets plants turn sunlight into stored food energy.",
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    session.chat("Explain photosynthesis simply.")
    session.chat("save that to a note")
    session.chat("call the note math.txt")
    session.chat("save it as math.txt")
    session.chat("use path: ~/VoxeraOS/notes/math.txt")

    preview = session.preview()
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/math.txt"

    submit = session.chat("submit it")
    assert submit.status_code == 200
    inbox_files = list((session.queue / "inbox").glob("*.json"))
    assert len(inbox_files) == 1
    payload = json.loads(inbox_files[0].read_text(encoding="utf-8"))
    assert payload["write_file"]["path"] == "~/VoxeraOS/notes/math.txt"
    assert vera_service.read_session_preview(session.queue, session.session_id) is None


def test_checklist_request_returns_conversational_answer_not_preview_error(tmp_path, monkeypatch):
    """Checklist/planning requests must be answered conversationally,
    not routed through preview drafting."""
    session = make_vera_session(monkeypatch, tmp_path)

    checklist_answer = (
        "Here's your wedding prep checklist:\n\n"
        "1. Find a plus-one\n"
        "2. Get a nice suit\n"
        "3. Book travel and accommodations\n"
        "4. Request time off work"
    )

    async def _fake_reply(*, turns, user_message, **kw):
        _ = turns
        return {"answer": checklist_answer, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat(
        "create a checklist would surely help on the many things I need to do. "
        "First I need to find a +1, I also need to get a nice suit, "
        "I need to get the tickets to travel there and accommodations "
        "and I need to take time off work!"
    )
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    assert last_turn["role"] == "assistant"
    # Must contain the actual checklist, not a preview error
    assert "checklist" in last_turn["text"].lower()
    assert "governed preview" not in last_turn["text"].lower()
    assert "couldn't safely prepare" not in last_turn["text"].lower()
    # No preview should have been created
    assert session.preview() is None


def test_checklist_answer_then_save_that_creates_preview(tmp_path, monkeypatch):
    """After a checklist answer, 'save that' should create a governed preview."""
    session = make_vera_session(monkeypatch, tmp_path)

    checklist_answer = (
        "Here's your wedding prep checklist:\n\n"
        "1. Find a plus-one\n"
        "2. Get a nice suit\n"
        "3. Book travel and accommodations\n"
        "4. Request time off work"
    )

    async def _fake_reply(*, turns, user_message, **kw):
        _ = turns
        if "checklist" in user_message.lower():
            return {"answer": checklist_answer, "status": "ok:test"}
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    session.chat("create a checklist for my wedding prep")
    session.chat("save that to a note")

    preview = session.preview()
    assert preview is not None
    assert "write_file" in preview
    assert "checklist" in preview["write_file"]["content"].lower()


def test_planning_request_returns_conversational_answer(tmp_path, monkeypatch):
    """Planning/step-by-step requests must be answered conversationally."""
    session = make_vera_session(monkeypatch, tmp_path)

    plan_answer = (
        "Here's your plan for the trip:\n\n1. Book flights\n2. Reserve hotel\n3. Plan activities"
    )

    async def _fake_reply(*, turns, user_message, **kw):
        _ = turns
        return {"answer": plan_answer, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("help me plan for a vacation to Japan")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    assert last_turn["role"] == "assistant"
    assert "plan" in last_turn["text"].lower()
    assert "governed preview" not in last_turn["text"].lower()
    assert session.preview() is None


def test_checklist_with_preview_claim_language_not_blocked(tmp_path, monkeypatch):
    """Even if the LLM uses 'I've prepared' phrasing, checklist turns must not
    be blocked by the false-preview-claim guardrail."""
    session = make_vera_session(monkeypatch, tmp_path)

    answer_with_claim_phrasing = (
        "I've prepared your checklist:\n\n1. Find a plus-one\n2. Buy a suit\n3. Book travel"
    )

    async def _fake_reply(*, turns, user_message, **kw):
        _ = turns
        return {"answer": answer_with_claim_phrasing, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("make me a checklist for the wedding")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    assert "plus-one" in last_turn["text"].lower()
    assert "governed preview" not in last_turn["text"].lower()
    assert "couldn't safely prepare" not in last_turn["text"].lower()


def test_brainstorm_request_returns_conversational_answer(tmp_path, monkeypatch):
    """Brainstorming requests should be answer-first."""
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message, **kw):
        _ = turns
        return {
            "answer": "Here are some ideas:\n- idea A\n- idea B\n- idea C",
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("brainstorm what I need for the camping trip")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    assert "idea" in last_turn["text"].lower()
    assert "governed preview" not in last_turn["text"].lower()


def test_save_checklist_to_file_does_not_bypass_preview(tmp_path, monkeypatch):
    """'save a checklist to a file' has explicit save intent — should NOT be
    treated as answer-first."""
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_reply(*, turns, user_message, **kw):
        _ = turns
        return {
            "answer": "Understood. Nothing has been submitted or executed yet. I can send it whenever you're ready.",
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("save a checklist to a file called wedding.txt")
    assert res.status_code == 200

    last_turn = session.turns()[-1]
    # Must NOT produce a raw conversational checklist — the save+file intent
    # means this should go through preview drafting, not answer-first.
    assert last_turn["role"] == "assistant"
    assert "1." not in last_turn["text"]
    assert "plus-one" not in last_turn["text"].lower()


def test_checklist_request_with_active_preview_does_not_bypass(tmp_path, monkeypatch):
    """When a governed preview is already active, a checklist-style message
    should NOT trigger answer-first bypass — the preview context dominates."""
    session = make_vera_session(monkeypatch, tmp_path)

    preview = {
        "goal": "write a file called notes.txt with provided content",
        "write_file": {
            "path": "~/VoxeraOS/notes/notes.txt",
            "content": "existing content",
            "mode": "overwrite",
        },
    }
    vera_service.write_session_preview(session.queue, session.session_id, preview)

    async def _fake_reply(*, turns, user_message, **kw):
        _ = turns
        return {
            "answer": "Understood. I still have the current request ready.",
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    res = session.chat("add a checklist to the file")
    assert res.status_code == 200

    # Preview must still be intact (not cleared by answer-first bypass).
    # The builder may have updated content, but the preview itself persists.
    assert session.preview() is not None
    assert "write_file" in session.preview()


def test_unsafe_path_revision_fails_closed_and_preserves_preview(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)

    preview = {
        "goal": "write a file called math.txt with provided content",
        "write_file": {
            "path": "~/VoxeraOS/notes/math.txt",
            "content": "2 + 2 is 4.",
            "mode": "overwrite",
        },
    }
    vera_service.write_session_preview(session.queue, session.session_id, preview)

    res = session.chat("use path: ~/VoxeraOS/notes/../bad.txt")

    assert res.status_code == 200
    assert vera_service.read_session_preview(session.queue, session.session_id) == preview
    assert session.turns()[-1]["role"] == "assistant"
