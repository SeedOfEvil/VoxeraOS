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
