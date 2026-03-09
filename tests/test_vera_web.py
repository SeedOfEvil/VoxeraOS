from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from voxera.models import AppConfig
from voxera.vera import prompt as vera_prompt
from voxera.vera import service as vera_service
from voxera.vera.handoff import drafting_guidance, normalize_preview_payload
from voxera.vera_web import app as vera_app_module


def _set_queue_root(monkeypatch, queue):
    monkeypatch.setattr(vera_app_module, "_active_queue_root", lambda: queue)


def _write_job_artifacts(
    queue,
    job_id,
    *,
    bucket="done",
    execution_result=None,
    state=None,
    failed_sidecar=None,
    approval=None,
):
    stem = Path(job_id).stem
    job_payload = {"goal": "test goal"}
    bucket_dir = queue / bucket
    bucket_dir.mkdir(parents=True, exist_ok=True)
    (bucket_dir / job_id).write_text(json.dumps(job_payload), encoding="utf-8")
    art = queue / "artifacts" / stem
    art.mkdir(parents=True, exist_ok=True)
    if execution_result is not None:
        (art / "execution_result.json").write_text(json.dumps(execution_result), encoding="utf-8")
    if state is not None:
        (bucket_dir / f"{stem}.state.json").write_text(json.dumps(state), encoding="utf-8")
    if failed_sidecar is not None:
        failed_dir = queue / "failed"
        failed_dir.mkdir(parents=True, exist_ok=True)
        (failed_dir / f"{stem}.error.json").write_text(json.dumps(failed_sidecar), encoding="utf-8")
    if approval is not None:
        approvals = queue / "pending" / "approvals"
        approvals.mkdir(parents=True, exist_ok=True)
        (approvals / f"{stem}.approval.json").write_text(json.dumps(approval), encoding="utf-8")


def test_vera_web_page_renders_single_pane(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    client = TestClient(vera_app_module.app)
    res = client.get("/")

    assert res.status_code == 200
    assert "Reasoning partner" in res.text
    assert "composer" in res.text
    assert "VoxeraOS queue handoff" in res.text
    assert "DEV diagnostics" in res.text


def test_vera_web_chat_returns_assistant_response(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

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
    _set_queue_root(monkeypatch, queue)

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
    _set_queue_root(monkeypatch, queue)

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
    monkeypatch.setattr(vera_service, "load_app_config", lambda: AppConfig())
    result = asyncio.run(vera_service.generate_vera_reply(turns=[], user_message="hello"))
    assert result["status"].startswith("degraded")


def test_vera_chat_does_not_enqueue_jobs(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

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
    _set_queue_root(monkeypatch, queue)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post("/chat", data={"session_id": sid, "message": "open https://example.com"})

    assert "job preview" in res.text
    assert "Nothing has been submitted or executed yet" in res.text
    assert list((queue / "inbox").glob("*.json")) == [] if (queue / "inbox").exists() else True


def test_explicit_submit_phrase_without_preview_is_honest_non_submission(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post("/chat", data={"session_id": sid, "message": "submit now please"})

    assert "did not submit anything" in res.text.lower() or "prepared preview" in res.text.lower()
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))


def test_prepare_preview_sets_preview_available_true_for_natural_open_phrase(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post("/chat", data={"session_id": sid, "message": "Can you open example.com?"})

    assert "job preview" in res.text
    assert "preview_available</b>: True" in res.text
    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["goal"] == "open https://example.com"


def test_submit_now_uses_persisted_structured_preview_state(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    seen_payload: dict[str, object] = {}
    original_submit = vera_app_module.submit_preview

    def _capture_submit(*, queue_root, payload):
        seen_payload.update(payload)
        return original_submit(queue_root=queue_root, payload=payload)

    monkeypatch.setattr(vera_app_module, "submit_preview", _capture_submit)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "Can you open example.com?"})
    res = client.post("/chat", data={"session_id": sid, "message": "submit now"})

    assert "I submitted the job to VoxeraOS" in res.text
    assert seen_payload == {"goal": "open https://example.com"}
    jobs = list((queue / "inbox").glob("inbox-*.json"))
    assert len(jobs) == 1


def test_explicit_handoff_creates_real_queue_job_and_ack(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

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


def test_submit_success_wording_requires_real_job_creation(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    def _fake_submit(*, queue_root, payload):
        _ = (queue_root, payload)
        return {"ack": "I submitted the job to VoxeraOS.", "job_id": ""}

    monkeypatch.setattr(vera_app_module, "submit_preview", _fake_submit)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "open https://example.com"})
    res = client.post("/chat", data={"session_id": sid, "message": "submit it"})

    assert "could not submit" in res.text
    assert "submitted the job" not in res.text.lower()
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))


def test_handoff_failure_reports_honestly(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

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


def test_chat_model_cannot_bypass_handoff_with_fake_submission_language(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        _ = (turns, user_message)
        return {"answer": "I submitted the job to VoxeraOS and it is queued.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post("/chat", data={"session_id": sid, "message": "hello"})

    assert "I have not submitted anything to VoxeraOS yet" in res.text
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))


def test_context_intact_across_preview_to_submit_flow(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

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


def test_preview_survives_into_handoff_submit_action(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "write a note called hello.txt"})

    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None

    res = client.post("/handoff", data={"session_id": sid})
    assert res.status_code == 200
    assert "I submitted the job to VoxeraOS" in res.text
    assert vera_service.read_session_preview(queue, sid) is None


def test_dev_diagnostics_expose_safe_handoff_state(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    monkeypatch.setattr(
        vera_app_module, "load_runtime_config", lambda: SimpleNamespace(queue_root=queue)
    )

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post("/chat", data={"session_id": sid, "message": "open https://example.com"})

    assert "preview_available" in res.text
    assert "handoff_status" in res.text
    assert str(queue) in res.text


def test_active_queue_root_uses_runtime_config(tmp_path, monkeypatch):
    queue = tmp_path / "configured-queue"
    monkeypatch.setattr(
        vera_app_module, "load_runtime_config", lambda: SimpleNamespace(queue_root=queue)
    )

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "open https://example.com"})
    client.post("/chat", data={"session_id": sid, "message": "submit it"})

    jobs = list((queue / "inbox").glob("inbox-*.json"))
    assert len(jobs) == 1


def test_rolling_turn_cap_does_not_drop_pending_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        return {"answer": f"ok {len(turns)} {user_message}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post("/chat", data={"session_id": sid, "message": "open https://example.com"})
    for i in range(10):
        client.post("/chat", data={"session_id": sid, "message": f"chat-{i}"})

    assert vera_service.read_session_preview(queue, sid) is not None
    res = client.post("/chat", data={"session_id": sid, "message": "submit it now"})

    assert "I submitted the job to VoxeraOS" in res.text
    assert len(list((queue / "inbox").glob("inbox-*.json"))) == 1


def test_structured_job_drafting_helper_examples_are_valid():
    guidance = drafting_guidance()
    assert guidance.base_shape == {"goal": "..."}
    for example in guidance.examples:
        normalized = normalize_preview_payload(example)
        assert normalized["goal"]


@pytest.mark.parametrize(
    ("message", "expected_goal"),
    [
        ("open example.com", "open https://example.com"),
        ("go to example.com", "open https://example.com"),
        ("visit example.com", "open https://example.com"),
        ("take me to example.com", "open https://example.com"),
        ("bring up example.com", "open https://example.com"),
        ("can you open example.com", "open https://example.com"),
        ("can you go to example.com", "open https://example.com"),
    ],
)
def test_web_navigation_phrases_prepare_preview(tmp_path, monkeypatch, message, expected_goal):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": message})

    preview = vera_service.read_session_preview(queue, sid)
    assert preview == {"goal": expected_goal}


@pytest.mark.parametrize(
    "message",
    [
        "what is example.com",
        "tell me about example.com",
    ],
)
def test_informational_domain_phrases_do_not_auto_prepare_preview(tmp_path, monkeypatch, message):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        _ = (turns, user_message)
        return {"answer": "info mode", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post("/chat", data={"session_id": sid, "message": message})

    assert "info mode" in res.text
    assert vera_service.read_session_preview(queue, sid) is None


@pytest.mark.parametrize(
    ("message", "expected_goal"),
    [
        ("inspect ~/VoxeraOS/notes/test.txt", "read the file ~/VoxeraOS/notes/test.txt"),
        ("show me ~/VoxeraOS/notes/test.txt", "read the file ~/VoxeraOS/notes/test.txt"),
        ("open the file ~/VoxeraOS/notes/test.txt", "read the file ~/VoxeraOS/notes/test.txt"),
    ],
)
def test_file_read_variants_prepare_preview(tmp_path, monkeypatch, message, expected_goal):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": message})

    preview = vera_service.read_session_preview(queue, sid)
    assert preview == {"goal": expected_goal}


@pytest.mark.parametrize(
    ("message", "expected_goal"),
    [
        ("make a note called hello.txt", "write a note called hello.txt"),
        ("create a file called hello.txt", "write a note called hello.txt"),
        ("jot this down", "write a note"),
    ],
)
def test_note_write_variants_prepare_preview(tmp_path, monkeypatch, message, expected_goal):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": message})

    preview = vera_service.read_session_preview(queue, sid)
    assert preview == {"goal": expected_goal}


def test_named_note_preview_and_submitted_payload_stay_consistent(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    preview_res = client.post(
        "/chat", data={"session_id": sid, "message": "write a note called jokester.txt"}
    )

    assert '"goal": "write a note called jokester.txt"' in preview_res.text
    preview = vera_service.read_session_preview(queue, sid)
    assert preview == {"goal": "write a note called jokester.txt"}

    client.post("/chat", data={"session_id": sid, "message": "submit it"})
    jobs = list((queue / "inbox").glob("inbox-*.json"))
    assert len(jobs) == 1
    payload = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert payload["goal"] == "write a note called jokester.txt"


def test_preview_replacement_uses_latest_payload_for_submit(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "open example.com"})
    client.post("/chat", data={"session_id": sid, "message": "actually open openai.com instead"})
    client.post("/chat", data={"session_id": sid, "message": "send it to VoxeraOS"})

    jobs = list((queue / "inbox").glob("inbox-*.json"))
    assert len(jobs) == 1
    payload = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert payload["goal"] == "open https://openai.com"


def test_preview_persists_across_followup_turn_before_submit(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        _ = (turns, user_message)
        return {"answer": "sounds good", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "open example.com"})
    client.post("/chat", data={"session_id": sid, "message": "yep that looks right"})
    res = client.post("/chat", data={"session_id": sid, "message": "queue it"})

    assert "I submitted the job to VoxeraOS" in res.text
    jobs = list((queue / "inbox").glob("inbox-*.json"))
    assert len(jobs) == 1


def test_review_latest_submitted_job_succeeded(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    job_id = "job-111.json"
    _write_job_artifacts(
        queue,
        job_id,
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
            "step_results": [{"step_index": 1, "status": "succeeded", "summary": "Opened page"}],
        },
    )
    vera_service.write_session_handoff_state(
        queue,
        "sid",
        attempted=True,
        queue_path=str(queue),
        status="submitted",
        job_id=job_id,
    )

    client = TestClient(vera_app_module.app)
    client.cookies.set("vera_session_id", "sid")
    res = client.post("/chat", data={"session_id": "sid", "message": "what happened to that job?"})

    assert "I reviewed canonical VoxeraOS evidence" in res.text
    assert "`succeeded`" in res.text
    assert "Opened page" in res.text


def test_review_specific_job_id_failed(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _write_job_artifacts(
        queue,
        "job-fail-1.json",
        bucket="failed",
        execution_result={
            "lifecycle_state": "failed",
            "terminal_outcome": "failed",
            "approval_status": "none",
            "error": "invalid request shape",
        },
        failed_sidecar={"error": "invalid request shape"},
    )

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post("/chat", data={"session_id": sid, "message": "why did job-fail-1 fail?"})

    assert "job-fail-1.json" in res.text
    assert "`failed`" in res.text
    assert "invalid request shape" in res.text


def test_review_awaiting_approval_job(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _write_job_artifacts(
        queue,
        "job-await-1.json",
        bucket="pending",
        execution_result={
            "lifecycle_state": "awaiting_approval",
            "terminal_outcome": "awaiting_approval",
            "approval_status": "pending",
            "step_results": [
                {"step_index": 1, "status": "blocked", "summary": "Need operator approval"}
            ],
        },
        approval={"job": "job-await-1.json", "status": "pending"},
    )

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post(
        "/chat", data={"session_id": sid, "message": "what's the status of job-await-1?"}
    )

    assert "`awaiting_approval`" in res.text
    assert "Approve or reject the pending approval" in res.text


def test_review_missing_job_is_honest(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post("/chat", data={"session_id": sid, "message": "what happened to job-404?"})

    assert "could not resolve a VoxeraOS job" in res.text


def test_followup_preview_drafted_from_evidence_not_submitted(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    job_id = "job-222.json"
    _write_job_artifacts(
        queue,
        job_id,
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
            "step_results": [
                {"step_index": 1, "status": "succeeded", "summary": "Read file complete"}
            ],
        },
    )
    vera_service.write_session_handoff_state(
        queue,
        "sid-follow",
        attempted=True,
        queue_path=str(queue),
        status="submitted",
        job_id=job_id,
    )

    client = TestClient(vera_app_module.app)
    client.cookies.set("vera_session_id", "sid-follow")
    res = client.post(
        "/chat", data={"session_id": "sid-follow", "message": "prepare the next step"}
    )

    assert "drafted a follow-up preview" in res.text
    assert "did not submit anything" in res.text.lower()
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))
    assert vera_service.read_session_preview(queue, "sid-follow") is not None
