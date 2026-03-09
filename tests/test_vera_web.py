from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from voxera.vera import prompt as vera_prompt
from voxera.vera import service as vera_service
from voxera.vera_web import app as vera_app_module


def test_vera_web_page_renders_single_pane(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    monkeypatch.setattr(vera_app_module, "queue_root", lambda: queue)

    client = TestClient(vera_app_module.app)
    res = client.get("/")

    assert res.status_code == 200
    assert "Reasoning partner" in res.text
    assert "composer" in res.text
    assert "VoxeraOS queue handoff" in res.text


def test_vera_web_chat_returns_assistant_response(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    monkeypatch.setattr(vera_app_module, "queue_root", lambda: queue)

    async def _fake_reply(*, turns, user_message):
        return {"answer": f"Echo: {user_message}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    home = client.get("/")
    assert home.status_code == 200
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post("/chat", data={"session_id": sid, "message": "hello"})

    assert res.status_code == 200
    assert "Echo: hello" in res.text


def test_vera_web_context_is_preserved_and_capped(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    monkeypatch.setattr(vera_app_module, "queue_root", lambda: queue)

    async def _fake_reply(*, turns, user_message):
        return {"answer": f"turns={len(turns)} latest={user_message}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    for i in range(6):
        res = client.post("/chat", data={"session_id": sid, "message": f"msg-{i}"})
        assert res.status_code == 200

    turns = vera_service.read_session_turns(queue, sid)
    assert len(turns) == vera_service.MAX_SESSION_TURNS
    assert turns[0]["text"] == "msg-2"


def test_vera_prompt_boundary_text_present():
    prompt = vera_prompt.VERA_SYSTEM_PROMPT
    assert "Vera, the conversational intelligence layer" in prompt
    assert "VoxeraOS is the execution trust layer" in prompt
    assert "Queue framing" in prompt


def test_vera_backend_unavailable_degrades_cleanly(monkeypatch):
    monkeypatch.setenv("VOXERA_BRAIN_PRIMARY_TYPE", "")
    result = asyncio.run(vera_service.generate_vera_reply(turns=[], user_message="hello"))
    assert result["status"].startswith("degraded")


def test_vera_chat_does_not_enqueue_jobs(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    monkeypatch.setattr(vera_app_module, "queue_root", lambda: queue)

    async def _fake_reply(*, turns, user_message):
        return {"answer": "proposal only", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "open a browser"})

    inbox = queue / "inbox"
    assert not inbox.exists() or not list(inbox.glob("*.json"))
