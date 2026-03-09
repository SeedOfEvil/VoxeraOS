from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from voxera.vera import prompt as vera_prompt
from voxera.vera import service as vera_service
from voxera.vera.handoff import drafting_guidance, normalize_preview_payload
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
    assert "DEV diagnostics" in res.text


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


def test_vera_clear_chat_and_context(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    monkeypatch.setattr(vera_app_module, "queue_root", lambda: queue)

    async def _fake_reply(*, turns, user_message):
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "keep context"})
    assert vera_service.read_session_turns(queue, sid)

    res = client.post("/clear", data={"session_id": sid})
    assert res.status_code == 200
    assert "How can I help?" in res.text
    assert vera_service.read_session_turns(queue, sid) == []


def test_vera_prompt_boundary_text_present():
    prompt = vera_prompt.VERA_SYSTEM_PROMPT
    assert "Vera, the conversational intelligence layer" in prompt
    assert "VoxeraOS is the execution trust layer" in prompt
    assert "Queue framing" in prompt
    assert "submitted/sent to VoxeraOS" in prompt


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
    client.post("/chat", data={"session_id": sid, "message": "hello there"})

    inbox = queue / "inbox"
    assert not inbox.exists() or not list(inbox.glob("*.json"))


def test_action_request_creates_preview_only_until_explicit_handoff(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    monkeypatch.setattr(vera_app_module, "queue_root", lambda: queue)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post("/chat", data={"session_id": sid, "message": "open https://example.com"})

    assert "Prepared VoxeraOS job preview" in res.text
    assert "Nothing has been submitted or executed yet" in res.text
    assert list((queue / "inbox").glob("*.json")) == [] if (queue / "inbox").exists() else True


def test_explicit_handoff_creates_real_queue_job_and_ack(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    monkeypatch.setattr(vera_app_module, "queue_root", lambda: queue)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post(
        "/chat",
        data={"session_id": sid, "message": "read the file ~/VoxeraOS/notes/stv-child-target.txt"},
    )
    res = client.post("/chat", data={"session_id": sid, "message": "hand it off"})

    assert "I submitted the job to VoxeraOS" in res.text
    assert "Execution has not completed yet" in res.text

    jobs = list((queue / "inbox").glob("inbox-*.json"))
    assert len(jobs) == 1
    payload = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert payload["goal"] == "read the file ~/VoxeraOS/notes/stv-child-target.txt"


def test_handoff_failure_reports_honestly(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    monkeypatch.setattr(vera_app_module, "queue_root", lambda: queue)

    def _boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(vera_app_module, "submit_preview", _boom)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "write a note called hello.txt"})
    res = client.post("/chat", data={"session_id": sid, "message": "submit it"})

    assert "could not submit" in res.text
    assert "nothing was queued" in res.text


def test_context_intact_across_preview_to_submit_flow(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    monkeypatch.setattr(vera_app_module, "queue_root", lambda: queue)

    async def _fake_reply(*, turns, user_message):
        return {"answer": f"ack {len(turns)}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "hello"})
    client.post("/chat", data={"session_id": sid, "message": "open https://example.com"})
    client.post("/chat", data={"session_id": sid, "message": "submit it"})

    turns = vera_service.read_session_turns(queue, sid)
    joined = "\n".join(turn["text"] for turn in turns)
    assert "hello" in joined
    assert "submitted the job" in joined


def test_structured_job_drafting_helper_examples_are_valid():
    guidance = drafting_guidance()
    assert guidance.base_shape == {"goal": "..."}
    for example in guidance.examples:
        normalized = normalize_preview_payload(example)
        assert normalized["goal"]
