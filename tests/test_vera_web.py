from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from voxera.models import AppConfig
from voxera.vera import prompt as vera_prompt
from voxera.vera import service as vera_service
from voxera.vera.handoff import (
    drafting_guidance,
    maybe_draft_job_payload,
    normalize_preview_payload,
)
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
    assert "vera_thread_manual_scroll_up" in res.text


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
    assert "# System Overview" in prompt
    assert "# Vera Role" in prompt
    assert "# Capability: Handoff and Submit Rules" in prompt


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


def test_informational_web_query_does_not_auto_prepare_voxera_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        return {"answer": "Read-only findings from Brave", "status": "ok:web_investigation"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "What's on cnn right now?"},
    )

    assert res.status_code == 200
    assert "Read-only findings from Brave" in res.text
    assert vera_service.read_session_preview(queue, sid) is None
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))


def test_finance_informational_query_does_not_auto_prepare_voxera_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        return {"answer": "Read-only market findings", "status": "ok:web_investigation"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "can you find stock information about the big 7?"},
    )

    assert res.status_code == 200
    assert "Read-only market findings" in res.text
    assert vera_service.read_session_preview(queue, sid) is None
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))


def test_news_query_skips_preview_builder_and_stays_informational(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _builder_should_not_run(**kwargs):
        raise AssertionError("preview builder should not run for informational web turns")

    async def _fake_reply(*, turns, user_message):
        return {"answer": "Global headlines summary", "status": "ok:web_investigation"}

    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _builder_should_not_run)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "Whats the latest world wide news?"},
    )

    assert res.status_code == 200
    assert "Global headlines summary" in res.text
    assert vera_service.read_session_preview(queue, sid) is None
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))


def _sample_investigation_payload() -> dict[str, object]:
    return {
        "query": "ai incident response",
        "retrieved_at_ms": 123,
        "results": [
            {
                "result_id": 1,
                "title": "Result One",
                "url": "https://example.com/1",
                "source": "example.com",
                "snippet": "snippet one",
                "why_it_matched": "matched one",
                "rank": 1,
            },
            {
                "result_id": 2,
                "title": "Result Two",
                "url": "https://example.com/2",
                "source": "example.com",
                "snippet": "snippet two",
                "why_it_matched": "matched two",
                "rank": 2,
            },
            {
                "result_id": 3,
                "title": "Result Three",
                "url": "https://example.com/3",
                "source": "example.com",
                "snippet": "snippet three",
                "why_it_matched": "matched three",
                "rank": 3,
            },
        ],
    }


def test_structured_investigation_is_stored_and_numbered(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        _ = (turns, user_message)
        return {
            "answer": "Result 1: Result One\nResult 2: Result Two",
            "status": "ok:web_investigation",
            "investigation": _sample_investigation_payload(),
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post("/chat", data={"session_id": sid, "message": "investigate this"})

    stored = vera_service.read_session_investigation(queue, sid)
    assert stored is not None
    assert [row["result_id"] for row in stored["results"]] == [1, 2, 3]


def test_save_single_investigation_result_creates_governed_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_service.write_session_investigation(queue, sid, _sample_investigation_payload())

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "save result 2 to a note"},
    )

    assert res.status_code == 200
    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert "investigation findings (2)" in preview["goal"]
    assert "## Result 2" in preview["write_file"]["content"]
    assert "## Result 1" not in preview["write_file"]["content"]
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))


def test_save_multiple_investigation_results_creates_expected_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_service.write_session_investigation(queue, sid, _sample_investigation_payload())

    client.post(
        "/chat",
        data={"session_id": sid, "message": "save results 1 and 3 to note"},
    )

    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    content = preview["write_file"]["content"]
    assert "## Result 1" in content
    assert "## Result 3" in content
    assert "## Result 2" not in content


def test_save_all_investigation_results_to_markdown_file(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_service.write_session_investigation(queue, sid, _sample_investigation_payload())

    client.post(
        "/chat",
        data={"session_id": sid, "message": "save all findings to research.md"},
    )

    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/research.md"
    content = preview["write_file"]["content"]
    assert "## Result 1" in content
    assert "## Result 2" in content
    assert "## Result 3" in content


def test_invalid_investigation_reference_fails_closed_with_clear_message(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_service.write_session_investigation(queue, sid, _sample_investigation_payload())

    client.post(
        "/chat",
        data={"session_id": sid, "message": "save result 9 to a note"},
    )

    turns = vera_service.read_session_turns(queue, sid)
    assert turns[-1]["role"] == "assistant"
    assert "couldn't resolve" in turns[-1]["text"].lower()
    assert vera_service.read_session_preview(queue, sid) is None


def test_save_investigation_without_active_result_set_fails_closed(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "save result 1 to a note"},
    )

    assert "run a fresh read-only investigation first" in res.text.lower()
    assert vera_service.read_session_preview(queue, sid) is None


def test_mode_refinement_append_instead_updates_active_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={"session_id": sid, "message": "write a file called log.txt"},
    )

    client.post(
        "/chat",
        data={"session_id": sid, "message": "append instead"},
    )

    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["mode"] == "append"
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/log.txt"


def test_put_that_into_file_after_informational_turn_does_not_claim_phantom_preview(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        if "put" in user_message.lower():
            return {
                "answer": "I've prepared a draft for the file with the news content.",
                "status": "ok:test",
            }
        return {"answer": "Top headlines summary.", "status": "ok:web_investigation"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post("/chat", data={"session_id": sid, "message": "find the latest news"})
    assert vera_service.read_session_preview(queue, sid) is None

    res = client.post("/chat", data={"session_id": sid, "message": "put that into the file"})

    assert "I've prepared a draft" not in res.text
    assert vera_service.read_session_preview(queue, sid) is None
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))


def test_informational_query_then_send_it_does_not_enqueue(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        if "send it" in user_message.lower():
            return {"answer": "I did not submit anything.", "status": "info:no_submit"}
        return {"answer": "Top headlines summary", "status": "ok:web_investigation"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    first = client.post(
        "/chat",
        data={"session_id": sid, "message": "What's the news today?"},
    )
    second = client.post(
        "/chat",
        data={"session_id": sid, "message": "send it"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert vera_service.read_session_preview(queue, sid) is None
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))


def test_missing_key_informational_query_is_honest_and_no_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _builder_should_not_run(**kwargs):
        raise AssertionError("preview builder should not run for informational web turns")

    async def _fake_reply(*, turns, user_message):
        return {
            "answer": "Brave web investigation is not configured yet (missing API key).",
            "status": "web_investigation_unconfigured",
        }

    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _builder_should_not_run)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "What's the latest world wide news?"},
    )

    assert res.status_code == 200
    assert "not configured" in res.text.lower()
    assert vera_service.read_session_preview(queue, sid) is None
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))


def test_explicit_internal_search_request_stays_no_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _builder_should_not_run(**kwargs):
        raise AssertionError("preview builder should not run for informational web turns")

    async def _fake_reply(*, turns, user_message):
        return {
            "answer": "I can check that online and summarize it.",
            "status": "ok:web_investigation",
        }

    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _builder_should_not_run)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "use your internal internet web search please"},
    )

    assert res.status_code == 200
    assert "summarize" in res.text.lower()
    assert vera_service.read_session_preview(queue, sid) is None
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))


def test_action_request_creates_preview_only_until_explicit_handoff(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post("/chat", data={"session_id": sid, "message": "open https://example.com"})

    assert "send it whenever" in res.text
    assert "Preview panel · Active VoxeraOS draft (authoritative, not submitted)" in res.text
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

    assert "send it whenever" in res.text
    assert "preview_available</b>: True" in res.text
    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["goal"] == "open https://example.com"


def test_open_up_domain_phrase_prepares_preview_and_renders_authoritative_pane(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "Open up cnn.com for me please"},
    )

    assert "Proposed VoxeraOS Job" not in res.text
    assert "```json" not in res.text
    assert "Preview panel · Active VoxeraOS draft" in res.text
    assert vera_service.read_session_preview(queue, sid) == {"goal": "open https://cnn.com"}


def test_yes_please_with_active_preview_submits_only_on_real_ack(tmp_path, monkeypatch):
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

    client.post("/chat", data={"session_id": sid, "message": "open example.com"})
    res = client.post("/chat", data={"session_id": sid, "message": "Yes please"})

    assert "I submitted the job to VoxeraOS" in res.text
    assert seen_payload == {"goal": "open https://example.com"}
    assert len(list((queue / "inbox").glob("inbox-*.json"))) == 1


def test_file_write_question_uses_authoritative_preview_pane_not_visible_json(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "can you write a file called wittyjoke.txt?"},
    )

    assert "Proposal for VoxeraOS" not in res.text
    assert "Proposed VoxeraOS Job" not in res.text
    assert "```json" not in res.text
    assert "Preview panel · Active VoxeraOS draft" in res.text
    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert "wittyjoke.txt" in preview["goal"]


def test_yes_please_submits_file_write_preview_when_present(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={"session_id": sid, "message": "write a file called wittyjoke.txt"},
    )
    res = client.post("/chat", data={"session_id": sid, "message": "yes please"})

    assert "I submitted the job to VoxeraOS" in res.text
    jobs = list((queue / "inbox").glob("inbox-*.json"))
    assert len(jobs) == 1
    payload = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert "wittyjoke.txt" in payload["goal"]


def test_note_write_and_file_read_requests_render_preview_pane_without_voxera_json(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    note_res = client.post(
        "/chat",
        data={"session_id": sid, "message": "write a note called ideas.txt"},
    )
    assert "```json" not in note_res.text
    assert "Preview panel · Active VoxeraOS draft" in note_res.text

    read_res = client.post(
        "/chat",
        data={"session_id": sid, "message": "read the file ~/VoxeraOS/notes/ideas.txt"},
    )
    assert "```json" not in read_res.text
    assert "Preview panel · Active VoxeraOS draft" in read_res.text
    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["goal"] == "read ~/VoxeraOS/notes/ideas.txt from notes"
    assert preview["steps"][0]["skill_id"] == "files.read_text"
    assert preview["steps"][0]["args"]["path"] == "~/VoxeraOS/notes/ideas.txt"


def test_blocked_queue_path_returns_clean_refusal_no_preview(tmp_path, monkeypatch):
    """Queue control-plane paths via shorthand must fail closed with a clear refusal."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "check if /queue/health.json exists"},
    )
    # Must NOT contain pseudo action JSON or preview panel
    assert "voxera_control" not in res.text
    assert '"action"' not in res.text
    assert "```json" not in res.text
    # Must contain a clear refusal explanation
    assert "blocked" in res.text.lower()
    # Must NOT create a preview
    preview = vera_service.read_session_preview(queue, sid)
    assert preview is None


def test_yes_please_without_preview_fails_closed_even_if_model_claims_submission(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        _ = (turns, user_message)
        return {"answer": "I submitted the request to VoxeraOS.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post("/chat", data={"session_id": sid, "message": "yes please"})

    assert "did not submit anything" in res.text.lower()
    assert "I submitted the request" not in res.text
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))


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
    assert payload["goal"] == "read ~/VoxeraOS/notes/stv-child-target.txt from notes"
    assert payload["steps"][0]["skill_id"] == "files.read_text"


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


def test_contentful_natural_file_creation_phrase_produces_canonical_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": (
                "ok create a skibbiddy.txt and as content add an Active directory script "
                "that creates a user called Skibbidy"
            ),
        },
    )

    assert "send it whenever" in res.text
    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/skibbiddy.txt"
    assert "Active directory script" in preview["write_file"]["content"]


def test_content_refinement_phrase_add_content_updates_active_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "write a file called skibbz.txt"})

    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": "add content to skibbz.txt saying hello",
        },
    )

    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/skibbz.txt"
    assert preview["write_file"]["content"] == "hello"


def test_content_refinement_phrase_script_text_updates_active_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "write a file called script.ps1"})

    script_text = "an Active Directory script that creates a user called Skibbidy"
    client.post(
        "/chat",
        data={"session_id": sid, "message": f"add content to script.ps1 {script_text}"},
    )

    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/script.ps1"
    assert preview["write_file"]["content"] == script_text


def test_latest_content_refinement_wins_for_handoff_payload(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "write a file called joke.txt"})
    client.post("/chat", data={"session_id": sid, "message": "put this joke inside it: old"})
    client.post("/chat", data={"session_id": sid, "message": "use this as the content: new"})
    client.post("/handoff", data={"session_id": sid})

    jobs = list((queue / "inbox").glob("inbox-*.json"))
    assert len(jobs) == 1
    payload = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert payload["write_file"]["path"] == "~/VoxeraOS/notes/joke.txt"
    assert payload["write_file"]["content"] == "new"


def test_backend_builder_updates_active_preview_without_json_dumping_in_chat(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        _ = (turns, user_message)
        return {"answer": "Working on it.", "status": "ok:test"}

    async def _fake_builder(
        *, turns, user_message, active_preview, enrichment_context=None, investigation_context=None
    ):
        _ = (turns, user_message, active_preview)
        return {"goal": "open https://openai.com"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _fake_builder)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "open example.com"})
    res = client.post("/chat", data={"session_id": sid, "message": "revise it"})

    assert '"goal": "open https://openai.com"' in res.text
    assert "```json" not in res.text
    assert vera_service.read_session_preview(queue, sid) == {"goal": "open https://openai.com"}


def test_submit_after_model_preview_replacement_uses_latest_payload(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        _ = (turns, user_message)
        return {"answer": "Working on it.", "status": "ok:test"}

    async def _fake_builder(
        *, turns, user_message, active_preview, enrichment_context=None, investigation_context=None
    ):
        _ = turns
        if "update" in user_message:
            return {"goal": "open https://openai.com"}
        return active_preview

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _fake_builder)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "open example.com"})
    client.post("/chat", data={"session_id": sid, "message": "actually update it"})
    client.post("/handoff", data={"session_id": sid})

    jobs = list((queue / "inbox").glob("inbox-*.json"))
    assert len(jobs) == 1
    payload = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert payload["goal"] == "open https://openai.com"


def test_invalid_builder_payload_is_ignored(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        _ = (turns, user_message)
        return {"answer": "Got it, I kept this conversational.", "status": "ok:test"}

    async def _fake_builder(
        *, turns, user_message, active_preview, enrichment_context=None, investigation_context=None
    ):
        _ = (turns, user_message, active_preview)
        return {"goal": "", "write_file": "bad-shape"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _fake_builder)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_service.write_session_preview(queue, sid, {"goal": "write a note called scipptyaway.txt"})

    res = client.post("/chat", data={"session_id": sid, "message": "add content"})

    assert "still have the current request ready" in res.text
    assert vera_service.read_session_preview(queue, sid) == {
        "goal": "write a note called scipptyaway.txt"
    }


def test_clear_resets_preview_and_new_preview_reinitializes_authoritative_state(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    first = client.post("/chat", data={"session_id": sid, "message": "open example.com"})
    assert '"goal": "open https://example.com"' in first.text
    assert vera_service.read_session_preview(queue, sid) == {"goal": "open https://example.com"}

    cleared = client.post("/clear", data={"session_id": sid})
    assert "Submit current preview to VoxeraOS" not in cleared.text
    assert vera_service.read_session_preview(queue, sid) is None

    second = client.post("/chat", data={"session_id": sid, "message": "open openai.com"})
    assert '"goal": "open https://openai.com"' in second.text
    assert vera_service.read_session_preview(queue, sid) == {"goal": "open https://openai.com"}


def test_structured_write_file_preview_submits_exact_payload(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": 'write a file called skibbidy.txt with the content "hello world"',
        },
    )
    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/skibbidy.txt"

    client.post("/handoff", data={"session_id": sid})

    jobs = list((queue / "inbox").glob("inbox-*.json"))
    assert len(jobs) == 1
    payload = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert payload["write_file"]["path"] == "~/VoxeraOS/notes/skibbidy.txt"
    assert payload["write_file"]["content"] == "hello world"


def test_builder_drops_extra_keys_and_keeps_supported_preview_shape(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        _ = (turns, user_message)
        return {"answer": "I left the preview as-is.", "status": "ok:test"}

    async def _fake_builder(
        *, turns, user_message, active_preview, enrichment_context=None, investigation_context=None
    ):
        _ = (turns, user_message, active_preview)
        return {"goal": "write a note called skibbidy.txt", "content": "hello"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _fake_builder)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "open example.com"})

    res = client.post("/chat", data={"session_id": sid, "message": "revise with content"})

    assert "send it whenever" in res.text
    assert vera_service.read_session_preview(queue, sid) == {
        "goal": "write a note called skibbidy.txt"
    }


def test_builder_multiple_preview_replacements_latest_wins_in_pane(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        _ = (turns, user_message)
        return {"answer": "plain reply", "status": "ok:test"}

    async def _fake_builder(
        *, turns, user_message, active_preview, enrichment_context=None, investigation_context=None
    ):
        _ = (turns, active_preview)
        if "to b" in user_message:
            return {"goal": "open https://openai.com"}
        if "to c" in user_message:
            return {"goal": "open https://github.com"}
        return None

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _fake_builder)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    a = client.post("/chat", data={"session_id": sid, "message": "open example.com"})
    assert '"goal": "open https://example.com"' in a.text

    b = client.post("/chat", data={"session_id": sid, "message": "update to b"})
    assert '"goal": "open https://openai.com"' in b.text
    assert vera_service.read_session_preview(queue, sid) == {"goal": "open https://openai.com"}

    c = client.post("/chat", data={"session_id": sid, "message": "update to c"})
    assert '"goal": "open https://github.com"' in c.text
    assert vera_service.read_session_preview(queue, sid) == {"goal": "open https://github.com"}


def test_builder_can_set_preview_without_changing_vera_voice(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        _ = (turns, user_message)
        return {"answer": "I updated the target in the preview.", "status": "ok:test"}

    async def _fake_builder(
        *, turns, user_message, active_preview, enrichment_context=None, investigation_context=None
    ):
        _ = (turns, user_message, active_preview)
        return {"goal": "open https://openai.com"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _fake_builder)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post("/chat", data={"session_id": sid, "message": "draft"})

    assert "I updated the target in the preview." in res.text
    assert '"goal": "open https://openai.com"' in res.text
    assert vera_service.read_session_preview(queue, sid) == {"goal": "open https://openai.com"}


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
    ("message", "expected_goal", "expected_path", "expect_structured"),
    [
        (
            "make a note called hello.txt",
            "write a file called hello.txt with provided content",
            "~/VoxeraOS/notes/hello.txt",
            True,
        ),
        (
            "create a file called hello.txt",
            "write a file called hello.txt with provided content",
            "~/VoxeraOS/notes/hello.txt",
            True,
        ),
        ("jot this down", "write a note", None, False),
    ],
)
def test_note_write_variants_prepare_preview(
    tmp_path,
    monkeypatch,
    message,
    expected_goal,
    expected_path,
    expect_structured,
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": message})

    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["goal"] == expected_goal
    if expect_structured:
        assert preview["write_file"]["path"] == expected_path
        assert preview["write_file"]["mode"] == "overwrite"
        assert preview["write_file"]["content"] == ""


def test_contentful_file_write_phrase_prepares_structured_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": 'write a file called funnyjoke.txt with the content "Why don’t scientists trust atoms? Because they make up everything!"',
        },
    )

    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/funnyjoke.txt"
    assert (
        preview["write_file"]["content"]
        == "Why don’t scientists trust atoms? Because they make up everything!"
    )


def test_named_note_preview_and_submitted_payload_stay_consistent(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "write a note called jokester.txt"})

    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["goal"] == "write a file called jokester.txt with provided content"
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/jokester.txt"
    assert preview["write_file"]["mode"] == "overwrite"

    client.post("/chat", data={"session_id": sid, "message": "submit it"})
    jobs = list((queue / "inbox").glob("inbox-*.json"))
    assert len(jobs) == 1
    payload = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert payload["goal"] == "write a file called jokester.txt with provided content"
    assert payload["write_file"]["path"] == "~/VoxeraOS/notes/jokester.txt"


def test_filename_refinement_replaces_active_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": 'write a file called funnyjoke.txt with the content "hello"',
        },
    )
    client.post(
        "/chat",
        data={"session_id": sid, "message": "actually rename it jokester.txt"},
    )

    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/jokester.txt"


def test_content_refinement_replaces_active_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post(
        "/chat",
        data={"session_id": sid, "message": "write a file called funnyjoke.txt"},
    )
    client.post(
        "/chat",
        data={"session_id": sid, "message": "put the joke inside the file"},
    )
    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": 'actually use this joke: "Why don’t scientists trust atoms? Because they make up everything!"',
        },
    )

    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert (
        preview["write_file"]["content"]
        == "Why don’t scientists trust atoms? Because they make up everything!"
    )


def test_submit_clears_preview_only_after_confirmed_success(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "open example.com"})

    assert vera_service.read_session_preview(queue, sid) is not None

    client.post("/chat", data={"session_id": sid, "message": "submit it"})

    assert vera_service.read_session_preview(queue, sid) is None


def test_failed_submit_keeps_active_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(vera_app_module, "submit_preview", _boom)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "open example.com"})
    before = vera_service.read_session_preview(queue, sid)

    client.post("/chat", data={"session_id": sid, "message": "submit it"})
    after = vera_service.read_session_preview(queue, sid)

    assert before is not None
    assert after == before


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


def test_preview_pane_submit_button_submits_active_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    preview_res = client.post("/chat", data={"session_id": sid, "message": "open example.com"})

    assert "Submit current preview to VoxeraOS" in preview_res.text

    submit_res = client.post("/handoff", data={"session_id": sid})
    assert "I submitted the job to VoxeraOS" in submit_res.text

    jobs = list((queue / "inbox").glob("inbox-*.json"))
    assert len(jobs) == 1
    payload = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert payload["goal"] == "open https://example.com"


def test_natural_preview_submit_phrase_uses_active_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "open example.com"})
    res = client.post("/chat", data={"session_id": sid, "message": "that looks good now use it"})

    assert "I submitted the job to VoxeraOS" in res.text
    jobs = list((queue / "inbox").glob("inbox-*.json"))
    assert len(jobs) == 1


def test_natural_preview_submit_phrase_without_preview_fails_closed(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        _ = (turns, user_message)
        return {"answer": "ordinary reply", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post("/chat", data={"session_id": sid, "message": "use this preview"})

    assert "I submitted the job to VoxeraOS" not in res.text
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))


def test_preview_replacement_updates_authoritative_pane_payload(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "open example.com"})
    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "actually open openai.com instead"},
    )

    assert '"goal": "open https://openai.com"' in res.text
    assert vera_service.read_session_preview(queue, sid) == {"goal": "open https://openai.com"}


def test_handoff_submit_clears_authoritative_preview_pane(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "open example.com"})
    res = client.post("/handoff", data={"session_id": sid})

    assert "I submitted the job to VoxeraOS" in res.text
    assert "Submit current preview to VoxeraOS" not in res.text
    assert "preview_available</b>: False" in res.text


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


def test_review_latest_submitted_job_short_handoff_id_resolves_inbox_filename(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    short_job_id = "1773082365485-1336541d"
    full_job_filename = f"inbox-{short_job_id}.json"
    _write_job_artifacts(
        queue,
        full_job_filename,
        bucket="pending",
        execution_result={
            "lifecycle_state": "awaiting_approval",
            "terminal_outcome": "awaiting_approval",
            "approval_status": "pending",
            "step_results": [
                {
                    "step_index": 1,
                    "status": "blocked",
                    "summary": "open https://example.com requires approval",
                }
            ],
        },
        approval={"job": full_job_filename, "status": "pending"},
    )
    vera_service.write_session_handoff_state(
        queue,
        "sid-short",
        attempted=True,
        queue_path=str(queue),
        status="submitted",
        job_id=short_job_id,
    )
    client = TestClient(vera_app_module.app)
    client.cookies.set("vera_session_id", "sid-short")
    res = client.post(
        "/chat", data={"session_id": "sid-short", "message": "What happened to that job?"}
    )

    assert "I reviewed canonical VoxeraOS evidence" in res.text
    assert "`awaiting_approval`" in res.text
    assert "open https://example.com" in res.text


def test_review_explicit_short_handoff_job_id_works(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    short_job_id = "1773082365485-1336541d"
    _write_job_artifacts(
        queue,
        f"inbox-{short_job_id}.json",
        bucket="pending",
        execution_result={
            "lifecycle_state": "awaiting_approval",
            "terminal_outcome": "awaiting_approval",
            "approval_status": "pending",
            "step_results": [
                {"step_index": 1, "status": "blocked", "summary": "Need operator approval"}
            ],
        },
        approval={"job": f"inbox-{short_job_id}.json", "status": "pending"},
    )
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post(
        "/chat", data={"session_id": sid, "message": f"did it work for {short_job_id}?"}
    )

    assert "I reviewed canonical VoxeraOS evidence" in res.text
    assert "`awaiting_approval`" in res.text


def test_review_explicit_full_queue_filename_works(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    full_job_filename = "inbox-1773082365485-1336541d.json"
    _write_job_artifacts(
        queue,
        full_job_filename,
        bucket="pending",
        execution_result={
            "lifecycle_state": "awaiting_approval",
            "terminal_outcome": "awaiting_approval",
            "approval_status": "pending",
            "step_results": [
                {"step_index": 1, "status": "blocked", "summary": "Need operator approval"}
            ],
        },
        approval={"job": full_job_filename, "status": "pending"},
    )
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post(
        "/chat",
        data={"session_id": sid, "message": f"what's the status of {full_job_filename}?"},
    )

    assert "I reviewed canonical VoxeraOS evidence" in res.text
    assert "`awaiting_approval`" in res.text


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
    assert "blocked on operator approval" in res.text


def test_review_missing_job_is_honest(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post("/chat", data={"session_id": sid, "message": "what happened to job-404?"})

    assert "could not resolve a VoxeraOS job" in res.text


def test_review_missing_job_followups_stay_evidence_aware(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _generic_reply(*, turns, user_message):
        _ = (turns, user_message)
        return {"answer": "generic model fallback", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _generic_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    first = client.post("/chat", data={"session_id": sid, "message": "what happened to job-404?"})
    second = client.post("/chat", data={"session_id": sid, "message": "did it work?"})

    assert "could not resolve a VoxeraOS job" in first.text
    assert "could not resolve a VoxeraOS job" in second.text
    assert "generic model fallback" not in second.text


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

    assert "prepared a follow-up request" in res.text
    assert "did not submit anything" in res.text.lower()
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))
    assert vera_service.read_session_preview(queue, "sid-follow") is not None


def test_voxera_refinement_hides_visible_json_dump_and_updates_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        _ = (turns, user_message)
        return {
            "answer": ('Proposed VoxeraOS Job:\n```json\n{"goal": "open https://openai.com"}\n```'),
            "status": "ok:test",
        }

    async def _fake_builder(
        *, turns, user_message, active_preview, enrichment_context=None, investigation_context=None
    ):
        _ = (turns, user_message, active_preview)
        return {"goal": "open https://openai.com"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _fake_builder)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "open example.com"})
    res = client.post("/chat", data={"session_id": sid, "message": "refine target"})

    assert "Proposed VoxeraOS Job" not in res.text
    assert "```json" not in res.text
    assert "send it whenever" in res.text
    assert vera_service.read_session_preview(queue, sid) == {"goal": "open https://openai.com"}


def test_ordinary_voxera_turn_hides_prepared_proposal_wording_in_chat(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        _ = (turns, user_message)
        return {
            "answer": "I prepared a proposal for VoxeraOS. Let me know and I'll submit it.",
            "status": "ok:test",
        }

    async def _fake_builder(
        *, turns, user_message, active_preview, enrichment_context=None, investigation_context=None
    ):
        _ = (turns, user_message, active_preview)
        return {"goal": "open https://example.com"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _fake_builder)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "open example.com"},
    )

    assert "prepared a proposal" not in res.text.lower()
    assert "let me know and i'll submit" not in res.text.lower()
    assert "send it whenever" in res.text.lower()
    assert vera_service.read_session_preview(queue, sid) == {"goal": "open https://example.com"}


def test_chat_does_not_claim_preview_updated_when_builder_update_invalid(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        _ = (turns, user_message)
        return {"answer": "Working on it.", "status": "ok:test"}

    async def _fake_builder(
        *, turns, user_message, active_preview, enrichment_context=None, investigation_context=None
    ):
        _ = (turns, user_message, active_preview)
        return {"goal": "open https://openai.com", "write_file": "bad-shape"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _fake_builder)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_service.write_session_preview(queue, sid, {"goal": "open https://example.com"})

    res = client.post("/chat", data={"session_id": sid, "message": "refine it"})

    assert "still have the current request ready" in res.text
    assert vera_service.read_session_preview(queue, sid) == {"goal": "open https://example.com"}


def test_non_voxera_user_requested_json_content_is_still_allowed(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        _ = (turns, user_message)
        return {
            "answer": '```json\n{"app":"demo","enabled":true}\n```',
            "status": "ok:test",
        }

    async def _fake_builder(
        *, turns, user_message, active_preview, enrichment_context=None, investigation_context=None
    ):
        _ = (turns, user_message, active_preview)
        return None

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _fake_builder)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "make me a JSON config for my app"},
    )

    assert "```json" in res.text
    assert "demo" in res.text
    assert vera_service.read_session_preview(queue, sid) is None


def test_append_file_intent_compiles_structured_append_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={"session_id": sid, "message": 'append "new line" to log.txt'},
    )

    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/log.txt"
    assert preview["write_file"]["content"] == "new line"
    assert preview["write_file"]["mode"] == "append"


def test_open_target_without_tld_is_naturally_inferred_as_web_intent(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "open cnn for me"},
    )

    assert "```json" not in res.text
    assert "send it whenever" in res.text.lower()
    assert vera_service.read_session_preview(queue, sid) == {"goal": "open https://cnn.com"}


def test_contextual_refinement_can_build_preview_from_recent_user_messages():
    preview = maybe_draft_job_payload(
        'put "Why do programmers prefer dark mode? Because light attracts bugs." in it instead',
        recent_user_messages=["I want a text file called skiibz.txt"],
    )

    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/skiibz.txt"
    assert "dark mode" in preview["write_file"]["content"]


def test_natural_append_mode_refinement_updates_active_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": 'write a file called changelog.txt with content "release notes"',
        },
    )
    client.post(
        "/chat",
        data={"session_id": sid, "message": "make it append to the same file"},
    )

    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/changelog.txt"
    assert preview["write_file"]["content"] == "release notes"
    assert preview["write_file"]["mode"] == "append"


def test_natural_file_drafting_with_joke_infers_structured_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "make a file called txt.txt with a joke in it"},
    )

    assert "```json" not in res.text
    assert "send it whenever" in res.text.lower()
    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/txt.txt"
    assert preview["write_file"]["mode"] == "overwrite"
    assert preview["write_file"]["content"]


def test_file_drafting_with_called_typo_still_builds_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={"session_id": sid, "message": "make a file calleddd txt.txt with a joke"},
    )

    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/txt.txt"
    assert preview["write_file"]["content"]


def test_minimal_file_drafting_defaults_to_empty_content(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={"session_id": sid, "message": "build me a file called hello.txt"},
    )

    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/hello.txt"
    assert preview["write_file"]["mode"] == "overwrite"
    assert preview["write_file"]["content"] == ""


def test_filename_refinement_call_it_updates_path(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": 'write a file called jokes.txt with content "hello"',
        },
    )
    client.post(
        "/chat",
        data={"session_id": sid, "message": "call it funnierjoke.txt instead"},
    )

    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/funnierjoke.txt"
    assert preview["write_file"]["content"] == "hello"
    assert preview["write_file"]["mode"] == "overwrite"


def test_content_style_refinement_dad_joke_updates_content(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": 'write a file called jokes.txt with content "hello"',
        },
    )
    client.post(
        "/chat",
        data={"session_id": sid, "message": "make it a dad joke"},
    )

    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/jokes.txt"
    assert "anti-gravity" in preview["write_file"]["content"]


def test_note_for_later_creates_structured_note_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={"session_id": sid, "message": "make a note for later about buying milk"},
    )

    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["goal"] == "write a note about buying milk"
    assert preview["write_file"]["path"].startswith("~/VoxeraOS/notes/note-")
    assert preview["write_file"]["path"].endswith(".txt")
    assert preview["write_file"]["mode"] == "overwrite"
    assert preview["write_file"]["content"] == "Reminder: buying milk"


def test_active_preview_natural_content_refinement_updates_write_file_content(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={"session_id": sid, "message": "make a file called jokes.txt with a funny joke"},
    )
    before = vera_service.read_session_preview(queue, sid)
    assert before is not None

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "actually make it a programmer joke"},
    )

    after = vera_service.read_session_preview(queue, sid)
    assert after is not None
    assert "```json" not in res.text
    assert "send it whenever" in res.text.lower()
    assert after["write_file"]["path"] == before["write_file"]["path"]
    assert after["write_file"]["mode"] == before["write_file"]["mode"]
    assert "programmers" in after["write_file"]["content"].lower()


def test_active_preview_news_summary_refinement_updates_content(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": 'write a file called testnews.txt with content "placeholder"',
        },
    )
    client.post(
        "/chat",
        data={"session_id": sid, "message": "make the content a summary of today's top news"},
    )

    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/testnews.txt"
    assert preview["write_file"]["mode"] == "overwrite"
    assert "summary" in preview["write_file"]["content"].lower()


def test_active_preview_put_that_into_file_is_fail_closed_without_grounded_content(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": 'write a file called testnews.txt with content "placeholder"',
        },
    )
    before = vera_service.read_session_preview(queue, sid)
    assert before is not None

    client.post(
        "/chat",
        data={"session_id": sid, "message": "put that into the file"},
    )

    after = vera_service.read_session_preview(queue, sid)
    assert after == before


def test_active_preview_formal_refinement_updates_content(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": 'write a file called notes.txt with content "hey"',
        },
    )
    client.post(
        "/chat",
        data={"session_id": sid, "message": "make it more formal"},
    )

    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert "formal" in preview["write_file"]["content"].lower()


def test_active_preview_content_becomes_multiline_replaces_body_exactly(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": """Write overwrite-test-3.txt in my notes with this content:\n\nOriginal content\nVersion 1\n""",
        },
    )
    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": """Change it so the content becomes:\n\nOriginal content\nVersion 2\nUpdated by Vera\n""",
        },
    )

    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/overwrite-test-3.txt"
    assert preview["write_file"]["mode"] == "overwrite"
    assert preview["write_file"]["content"] == "Original content\nVersion 2\nUpdated by Vera"


def test_active_preview_replace_content_with_multiline_replaces_body_exactly(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": 'write a file called overwrite-test-4.txt with content "Original content\\nVersion 1"',
        },
    )
    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": """Replace the content with:\n\nVersion 2\nUpdated by Vera\n""",
        },
    )

    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/overwrite-test-4.txt"
    assert preview["write_file"]["content"] == "Version 2\nUpdated by Vera"


def test_active_preview_explicit_multiline_replacement_drops_stale_lines(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": """Write overwrite-test-5.txt in my notes with this content:\n\nOriginal content\nVersion 1\nOld line\n""",
        },
    )
    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": """Update it so the content becomes:\n\nReplacement line A\nReplacement line B\n""",
        },
    )

    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["content"] == "Replacement line A\nReplacement line B"
    assert "Old line" not in preview["write_file"]["content"]
    assert "(updated)" not in preview["write_file"]["content"]


def test_latest_preview_wins_across_multiple_natural_content_refinements(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={"session_id": sid, "message": "make a file called jokes.txt with a funny joke"},
    )
    client.post(
        "/chat",
        data={"session_id": sid, "message": "actually make it a programmer joke"},
    )
    client.post(
        "/chat",
        data={"session_id": sid, "message": "actually make it a pet joke"},
    )

    preview = vera_service.read_session_preview(queue, sid)
    assert preview is not None
    assert (
        "cat" in preview["write_file"]["content"].lower()
        or "mouse" in preview["write_file"]["content"].lower()
    )


def test_update_content_refinement_and_submit_uses_latest_mutated_payload(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={"session_id": sid, "message": "make a file called jokes.txt with a funny joke"},
    )
    client.post(
        "/chat",
        data={"session_id": sid, "message": "and update the content"},
    )

    latest = vera_service.read_session_preview(queue, sid)
    assert latest is not None
    assert latest["write_file"]["content"].strip()

    client.post("/chat", data={"session_id": sid, "message": "send it"})
    jobs = list((queue / "inbox").glob("inbox-*.json"))
    assert len(jobs) == 1
    payload = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert payload["goal"] == latest["goal"]
    assert payload["write_file"] == latest["write_file"]


def test_builder_prompt_describes_voxera_compiler_contract():
    prompt = vera_prompt.VERA_PREVIEW_BUILDER_PROMPT
    assert "# Hidden Compiler Role" in prompt
    assert "# Capability: Preview Payload Schema" in prompt
    assert "# Capability: Hidden Compiler Payload Guidance" in prompt
    assert "open https://cnn.com" in prompt


def test_vera_prompt_keeps_control_json_off_chat_surface_by_default():
    prompt = vera_prompt.VERA_SYSTEM_PROMPT
    assert "Do not expose Voxera control JSON unless explicitly needed" in prompt


# ---------------------------------------------------------------------------
# Enrichment-to-preview bridge tests
# ---------------------------------------------------------------------------


def test_enrichment_stored_for_info_query_with_active_preview(tmp_path, monkeypatch):
    """Informational web query with an active preview stores enrichment in session."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_enrichment(*, user_message):
        return {
            "query": "latest news",
            "summary": "1. Big Tech Rally\n   Markets surged today.",
            "retrieved_at_ms": 1000,
        }

    monkeypatch.setattr(vera_app_module, "run_web_enrichment", _fake_enrichment)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    # Establish an active preview
    client.post(
        "/chat",
        data={"session_id": sid, "message": "write a file called news.txt with a placeholder"},
    )
    assert vera_service.read_session_preview(queue, sid) is not None

    # Informational query WITH active preview → should run enrichment and store it
    client.post("/chat", data={"session_id": sid, "message": "find the latest news"})

    enrichment = vera_service.read_session_enrichment(queue, sid)
    assert enrichment is not None
    assert enrichment["query"] == "latest news"
    assert "Big Tech Rally" in enrichment["summary"]


def test_enrichment_backed_put_that_into_file_updates_preview_content(tmp_path, monkeypatch):
    """'put that into the file' after an enrichment turn resolves the summary into preview content."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    fake_summary = "1. Big Tech Rally\n   Markets surged today."

    async def _fake_enrichment(*, user_message):
        return {
            "query": "latest news",
            "summary": fake_summary,
            "retrieved_at_ms": 1000,
        }

    monkeypatch.setattr(vera_app_module, "run_web_enrichment", _fake_enrichment)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={"session_id": sid, "message": "write a file called news.txt with a placeholder"},
    )
    before = vera_service.read_session_preview(queue, sid)
    assert before is not None

    # Info query with active preview → stores enrichment
    client.post("/chat", data={"session_id": sid, "message": "find the latest news"})

    # Pronoun follow-up → enrichment summary resolves into file content
    client.post("/chat", data={"session_id": sid, "message": "put that into the file"})

    after = vera_service.read_session_preview(queue, sid)
    assert after is not None
    assert "Big Tech Rally" in after["write_file"]["content"]
    assert after["write_file"]["path"] == before["write_file"]["path"]
    assert after["write_file"]["mode"] == before["write_file"]["mode"]


def test_standalone_info_query_does_not_store_enrichment(tmp_path, monkeypatch):
    """Informational query with NO active preview skips enrichment (standalone web turn)."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    call_count = {"n": 0}

    async def _fake_enrichment(*, user_message):
        call_count["n"] += 1
        return {
            "query": "latest news",
            "summary": "some headline",
            "retrieved_at_ms": 1000,
        }

    monkeypatch.setattr(vera_app_module, "run_web_enrichment", _fake_enrichment)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    # No active preview — enrichment must not be triggered or stored
    client.post("/chat", data={"session_id": sid, "message": "find the latest news"})

    assert call_count["n"] == 0
    assert vera_service.read_session_enrichment(queue, sid) is None


def test_put_that_into_file_without_enrichment_no_active_preview_fails_closed(
    tmp_path, monkeypatch
):
    """'put that into the file' with no preview and no enrichment must not create a phantom preview."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post("/chat", data={"session_id": sid, "message": "put that into the file"})

    assert vera_service.read_session_preview(queue, sid) is None
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))


def test_handoff_registers_linked_job_tracking(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "open https://example.com"})
    client.post("/chat", data={"session_id": sid, "message": "submit now"})

    payload = json.loads((queue / "artifacts" / "vera_sessions" / f"{sid}.json").read_text())
    linked = payload.get("linked_queue_jobs")
    assert isinstance(linked, dict)
    tracked = linked.get("tracked")
    assert isinstance(tracked, list)
    assert len(tracked) == 1
    assert tracked[0]["job_ref"]
    assert tracked[0]["linked_session_id"] == sid


def test_linked_job_terminal_completion_is_ingested_with_policy(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "open https://example.com"})
    client.post("/chat", data={"session_id": sid, "message": "submit now"})

    inbox_job = next((queue / "inbox").glob("inbox-*.json"))
    done_dir = queue / "done"
    done_dir.mkdir(parents=True, exist_ok=True)
    done_job = done_dir / inbox_job.name
    shutil.move(str(inbox_job), str(done_job))

    stem = done_job.stem
    (done_dir / f"{stem}.state.json").write_text(
        json.dumps(
            {"lifecycle_state": "done", "terminal_outcome": "succeeded", "approval_status": "none"}
        ),
        encoding="utf-8",
    )
    art = queue / "artifacts" / stem
    art.mkdir(parents=True, exist_ok=True)
    (art / "execution_result.json").write_text(
        json.dumps(
            {
                "lifecycle_state": "done",
                "terminal_outcome": "succeeded",
                "approval_status": "none",
                "latest_summary": "Opened example.com successfully",
                "normalized_outcome_class": "succeeded",
                "artifact_families": ["browser_trace"],
            }
        ),
        encoding="utf-8",
    )

    created = vera_service.ingest_linked_job_completions(queue, sid)
    assert len(created) == 1

    completions = vera_service.read_linked_job_completions(queue, sid)
    assert len(completions) == 1
    completion = completions[0]
    assert completion["lifecycle_state"] == "done"
    assert completion["terminal_outcome"] == "succeeded"
    assert completion["request_kind"] == "goal"
    assert completion["surfacing_policy"] == "read_only_success"


def test_linked_read_only_success_auto_surfaces_once_per_completion(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        return {"answer": f"Echo: {user_message}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "open https://example.com"})
    client.post("/chat", data={"session_id": sid, "message": "submit now"})

    inbox_job = next((queue / "inbox").glob("inbox-*.json"))
    done_dir = queue / "done"
    done_dir.mkdir(parents=True, exist_ok=True)
    done_job = done_dir / inbox_job.name
    shutil.move(str(inbox_job), str(done_job))

    stem = done_job.stem
    (done_dir / f"{stem}.state.json").write_text(
        json.dumps(
            {"lifecycle_state": "done", "terminal_outcome": "succeeded", "approval_status": "none"}
        ),
        encoding="utf-8",
    )
    art = queue / "artifacts" / stem
    art.mkdir(parents=True, exist_ok=True)
    (art / "execution_result.json").write_text(
        json.dumps(
            {
                "lifecycle_state": "done",
                "terminal_outcome": "succeeded",
                "approval_status": "none",
                "latest_summary": "Disk usage check completed from canonical evidence.",
                "normalized_outcome_class": "succeeded",
                "artifact_families": ["system_status"],
            }
        ),
        encoding="utf-8",
    )

    first = client.post("/chat", data={"session_id": sid, "message": "thanks"})
    assert first.status_code == 200
    assert "Your linked goal job completed successfully." in first.text

    completions = vera_service.read_linked_job_completions(queue, sid)
    assert len(completions) == 1
    assert completions[0]["surfaced_in_chat"] is True
    assert isinstance(completions[0]["surfaced_at_ms"], int)

    second = client.post("/chat", data={"session_id": sid, "message": "hello again"})
    assert second.status_code == 200
    turns = vera_service.read_session_turns(queue, sid)
    surfaced_messages = [
        turn["text"]
        for turn in turns
        if turn["role"] == "assistant"
        and turn["text"].startswith("Your linked ")
        and "completed successfully." in turn["text"]
    ]
    assert len(surfaced_messages) == 1


def test_linked_approval_blocked_auto_surfaces_once(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        return {"answer": f"Echo: {user_message}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "open https://example.com"})
    client.post("/chat", data={"session_id": sid, "message": "submit now"})

    inbox_job = next((queue / "inbox").glob("inbox-*.json"))
    failed_dir = queue / "failed"
    failed_dir.mkdir(parents=True, exist_ok=True)
    failed_job = failed_dir / inbox_job.name
    shutil.move(str(inbox_job), str(failed_job))

    stem = failed_job.stem
    (failed_dir / f"{stem}.state.json").write_text(
        json.dumps(
            {
                "lifecycle_state": "failed",
                "terminal_outcome": "blocked",
                "approval_status": "pending",
            }
        ),
        encoding="utf-8",
    )
    approvals = queue / "pending" / "approvals"
    approvals.mkdir(parents=True, exist_ok=True)
    (approvals / f"{stem}.approval.json").write_text(
        json.dumps({"status": "pending", "reason": "operator approval required"}),
        encoding="utf-8",
    )
    art = queue / "artifacts" / stem
    art.mkdir(parents=True, exist_ok=True)
    (art / "execution_result.json").write_text(
        json.dumps(
            {
                "lifecycle_state": "awaiting_approval",
                "terminal_outcome": "blocked",
                "approval_status": "pending",
                "latest_summary": "Step blocked pending operator approval.",
                "normalized_outcome_class": "approval_blocked",
            }
        ),
        encoding="utf-8",
    )

    first = client.post("/chat", data={"session_id": sid, "message": "any updates?"})
    assert first.status_code == 200

    turns = vera_service.read_session_turns(queue, sid)
    approval_messages = [
        turn["text"]
        for turn in turns
        if turn["role"] == "assistant"
        and turn["text"].startswith("Your linked request is paused pending approval in VoxeraOS.")
    ]
    assert len(approval_messages) == 1
    assert "pending approval" in approval_messages[0]

    completions = vera_service.read_linked_job_completions(queue, sid)
    assert len(completions) == 1
    assert completions[0]["surfacing_policy"] == "approval_blocked"
    assert completions[0]["surfaced_in_chat"] is True

    second = client.post("/chat", data={"session_id": sid, "message": "hello again"})
    assert second.status_code == 200
    turns = vera_service.read_session_turns(queue, sid)
    surfaced_messages = [
        turn["text"]
        for turn in turns
        if turn["role"] == "assistant"
        and turn["text"].startswith("Your linked request is paused pending approval in VoxeraOS.")
    ]
    assert len(surfaced_messages) == 1


def test_linked_failed_auto_surfaces_once(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        return {"answer": f"Echo: {user_message}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "open https://example.com"})
    client.post("/chat", data={"session_id": sid, "message": "submit now"})

    inbox_job = next((queue / "inbox").glob("inbox-*.json"))
    failed_dir = queue / "failed"
    failed_dir.mkdir(parents=True, exist_ok=True)
    failed_job = failed_dir / inbox_job.name
    shutil.move(str(inbox_job), str(failed_job))

    stem = failed_job.stem
    (failed_dir / f"{stem}.state.json").write_text(
        json.dumps(
            {"lifecycle_state": "failed", "terminal_outcome": "failed", "approval_status": "none"}
        ),
        encoding="utf-8",
    )
    (failed_dir / f"{stem}.error.json").write_text(
        json.dumps({"error": "Path not found: ~/VoxeraOS/notes/report.txt"}),
        encoding="utf-8",
    )
    art = queue / "artifacts" / stem
    art.mkdir(parents=True, exist_ok=True)
    (art / "execution_result.json").write_text(
        json.dumps(
            {
                "lifecycle_state": "failed",
                "terminal_outcome": "failed",
                "approval_status": "none",
                "latest_summary": "Mission failed at step 2 (files.stat)",
                "error": "Path not found: ~/VoxeraOS/notes/report.txt",
                "next_action_hint": "Check the target path and rerun.",
                "normalized_outcome_class": "runtime_execution_failed",
            }
        ),
        encoding="utf-8",
    )

    first = client.post("/chat", data={"session_id": sid, "message": "status?"})
    assert first.status_code == 200

    turns = vera_service.read_session_turns(queue, sid)
    failed_messages = [
        turn["text"]
        for turn in turns
        if turn["role"] == "assistant" and turn["text"].startswith("Your linked goal job failed.")
    ]
    assert len(failed_messages) == 1
    assert "Failure summary: Path not found: ~/VoxeraOS/notes/report.txt" in failed_messages[0]

    completions = vera_service.read_linked_job_completions(queue, sid)
    assert len(completions) == 1
    assert completions[0]["surfacing_policy"] == "failed"
    assert completions[0]["surfaced_in_chat"] is True

    second = client.post("/chat", data={"session_id": sid, "message": "status again?"})
    assert second.status_code == 200
    turns = vera_service.read_session_turns(queue, sid)
    surfaced_messages = [
        turn["text"]
        for turn in turns
        if turn["role"] == "assistant" and turn["text"].startswith("Your linked goal job failed.")
    ]
    assert len(surfaced_messages) == 1


def test_linked_mutating_success_auto_surfaces_once(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        return {"answer": f"Echo: {user_message}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    sid = "vera-test-mutate"
    vera_service.append_session_turn(queue, sid, role="user", text="seed")
    vera_service.register_session_linked_job(queue, sid, job_ref="inbox-a.json")

    _write_job_artifacts(
        queue,
        "inbox-a.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
            "latest_summary": "Destination created at ~/VoxeraOS/notes/testdir.",
            "normalized_outcome_class": "succeeded",
            "review_summary": {
                "execution_capabilities": {
                    "side_effect_class": "write",
                }
            },
        },
        state={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
        },
    )
    # mutate request kind => mutating_success policy
    done_job = queue / "done" / "inbox-a.json"
    done_payload = json.loads(done_job.read_text(encoding="utf-8"))
    done_payload["job_intent"] = {"request_kind": "write_file"}
    done_job.write_text(json.dumps(done_payload), encoding="utf-8")

    client = TestClient(vera_app_module.app)

    first = client.post("/chat", data={"session_id": sid, "message": "any update?"})
    assert first.status_code == 200

    turns = vera_service.read_session_turns(queue, sid)
    surfaced_first = [
        turn["text"]
        for turn in turns
        if turn["role"] == "assistant"
        and turn["text"].startswith("Your linked write file job completed successfully.")
    ]
    assert len(surfaced_first) == 1
    assert "Destination created at ~/VoxeraOS/notes/testdir." in surfaced_first[0]

    completions = vera_service.read_linked_job_completions(queue, sid)
    assert len(completions) == 1
    assert completions[0]["surfacing_policy"] == "mutating_success"
    assert completions[0]["surfaced_in_chat"] is True

    second = client.post("/chat", data={"session_id": sid, "message": "any update now?"})
    assert second.status_code == 200
    turns = vera_service.read_session_turns(queue, sid)
    surfaced_messages = [
        turn["text"]
        for turn in turns
        if turn["role"] == "assistant"
        and turn["text"].startswith("Your linked write file job completed successfully.")
    ]
    assert len(surfaced_messages) == 1


def test_linked_mutating_success_intermediate_orchestration_is_suppressed(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        return {"answer": f"Echo: {user_message}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    sid = "vera-test-intermediate"
    vera_service.append_session_turn(queue, sid, role="user", text="seed")
    vera_service.register_session_linked_job(queue, sid, job_ref="inbox-parent.json")

    _write_job_artifacts(
        queue,
        "inbox-parent.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
            "latest_summary": "Parent step succeeded and delegated child work.",
            "normalized_outcome_class": "succeeded",
            "stop_reason": "enqueue_child",
            "child_refs": [{"child_job_id": "inbox-child.json"}],
            "child_summary": {
                "total": 1,
                "done": 0,
                "awaiting_approval": 0,
                "pending": 1,
                "failed": 0,
                "canceled": 0,
                "unknown": 0,
            },
            "review_summary": {
                "execution_capabilities": {
                    "side_effect_class": "execute",
                }
            },
        },
        state={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
        },
    )
    parent_job = queue / "done" / "inbox-parent.json"
    parent_payload = json.loads(parent_job.read_text(encoding="utf-8"))
    parent_payload["job_intent"] = {"request_kind": "run_command"}
    parent_job.write_text(json.dumps(parent_payload), encoding="utf-8")

    client = TestClient(vera_app_module.app)
    res = client.post("/chat", data={"session_id": sid, "message": "any update?"})
    assert res.status_code == 200
    assert "Parent step succeeded and delegated child work" not in res.text

    completions = vera_service.read_linked_job_completions(queue, sid)
    assert len(completions) == 1
    assert completions[0]["surfacing_policy"] == "mutating_success"
    assert completions[0]["surfaced_in_chat"] is False


def test_linked_terminal_completion_live_delivery_posts_immediately(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    sid = "vera-live-delivery"
    vera_service.append_session_turn(queue, sid, role="user", text="seed")
    vera_service.register_session_linked_job(queue, sid, job_ref="inbox-live.json")

    _write_job_artifacts(
        queue,
        "inbox-live.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
            "latest_summary": "Read-only linked completion arrived.",
            "normalized_outcome_class": "succeeded",
        },
        state={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
        },
    )

    delivered = vera_service.maybe_deliver_linked_completion_live_for_job(
        queue, job_ref="inbox-live.json"
    )
    assert delivered == 1

    turns = vera_service.read_session_turns(queue, sid)
    messages = [
        turn["text"]
        for turn in turns
        if turn["role"] == "assistant"
        and turn["text"].startswith("Your linked ")
        and "completed successfully." in turn["text"]
    ]
    assert len(messages) == 1

    payload = json.loads((queue / "artifacts" / "vera_sessions" / f"{sid}.json").read_text())
    linked = payload["linked_queue_jobs"]
    outbox = linked.get("notification_outbox")
    assert isinstance(outbox, list) and len(outbox) == 1
    assert outbox[0]["delivery_status"] == "delivered"
    assert outbox[0]["fallback_pending"] is False


def test_linked_live_delivery_surfaces_subsequent_completions_in_same_session(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    sid = "vera-live-multi"
    vera_service.append_session_turn(queue, sid, role="user", text="seed")
    vera_service.register_session_linked_job(queue, sid, job_ref="inbox-live-1.json")
    vera_service.register_session_linked_job(queue, sid, job_ref="inbox-live-2.json")

    _write_job_artifacts(
        queue,
        "inbox-live-1.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
            "latest_summary": "First completion arrived.",
            "normalized_outcome_class": "succeeded",
        },
        state={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
        },
    )
    _write_job_artifacts(
        queue,
        "inbox-live-2.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
            "latest_summary": "Second completion arrived.",
            "normalized_outcome_class": "succeeded",
        },
        state={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
        },
    )

    assert (
        vera_service.maybe_deliver_linked_completion_live_for_job(
            queue, job_ref="inbox-live-1.json"
        )
        == 1
    )
    assert (
        vera_service.maybe_deliver_linked_completion_live_for_job(
            queue, job_ref="inbox-live-2.json"
        )
        == 1
    )

    turns = vera_service.read_session_turns(queue, sid)
    surfaced = [
        turn["text"]
        for turn in turns
        if turn["role"] == "assistant"
        and turn["text"].startswith("Your linked ")
        and "completed successfully." in turn["text"]
    ]
    assert len(surfaced) == 2
    assert any("First completion arrived." in message for message in surfaced)
    assert any("Second completion arrived." in message for message in surfaced)


def test_linked_live_delivery_not_duplicated_after_refresh_or_later_chat(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message):
        return {"answer": f"Echo: {user_message}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    sid = "vera-live-no-duplicate"
    vera_service.append_session_turn(queue, sid, role="user", text="seed")
    vera_service.register_session_linked_job(queue, sid, job_ref="inbox-live-once.json")

    _write_job_artifacts(
        queue,
        "inbox-live-once.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
            "latest_summary": "Delivered once.",
            "normalized_outcome_class": "succeeded",
        },
        state={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
        },
    )

    assert (
        vera_service.maybe_deliver_linked_completion_live_for_job(
            queue, job_ref="inbox-live-once.json"
        )
        == 1
    )

    client = TestClient(vera_app_module.app)
    refreshed = client.get("/", params={"session_id": sid})
    assert refreshed.status_code == 200

    follow_up = client.post("/chat", data={"session_id": sid, "message": "ok"})
    assert follow_up.status_code == 200

    turns = vera_service.read_session_turns(queue, sid)
    surfaced = [
        turn["text"]
        for turn in turns
        if turn["role"] == "assistant"
        and turn["text"].startswith("Your linked ")
        and "Delivered once." in turn["text"]
    ]
    assert len(surfaced) == 1


def test_linked_live_delivery_unavailable_persists_pending_for_fallback(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    sid = "vera-live-unavailable"
    vera_service.append_session_turn(queue, sid, role="user", text="seed")
    vera_service.register_session_linked_job(queue, sid, job_ref="inbox-fallback.json")

    _write_job_artifacts(
        queue,
        "inbox-fallback.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
            "latest_summary": "Fallback should surface this once.",
            "normalized_outcome_class": "succeeded",
        },
        state={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
        },
    )

    def _raise_append(*args, **kwargs):
        raise RuntimeError("transport unavailable")

    original_append = vera_service.append_session_turn
    monkeypatch.setattr(vera_service, "append_session_turn", _raise_append)
    delivered = vera_service.maybe_deliver_linked_completion_live_for_job(
        queue, job_ref="inbox-fallback.json"
    )
    assert delivered == 0

    payload = json.loads((queue / "artifacts" / "vera_sessions" / f"{sid}.json").read_text())
    outbox = payload["linked_queue_jobs"].get("notification_outbox")
    assert isinstance(outbox, list) and len(outbox) == 1
    assert outbox[0]["delivery_status"] == "unavailable"
    assert outbox[0]["fallback_pending"] is True

    monkeypatch.setattr(vera_service, "append_session_turn", original_append)
    msg = vera_service.maybe_auto_surface_linked_completion(queue, sid)
    assert msg is not None
    assert msg.startswith("Your linked ")
    assert "completed successfully." in msg
    assert vera_service.maybe_auto_surface_linked_completion(queue, sid) is None


def test_live_delivered_linked_completion_not_reposted_by_fallback(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    sid = "vera-live-dedupe"
    vera_service.append_session_turn(queue, sid, role="user", text="seed")
    vera_service.register_session_linked_job(queue, sid, job_ref="inbox-dedupe.json")

    _write_job_artifacts(
        queue,
        "inbox-dedupe.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
            "latest_summary": "One-time live delivery only.",
            "normalized_outcome_class": "succeeded",
        },
        state={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
        },
    )

    assert (
        vera_service.maybe_deliver_linked_completion_live_for_job(
            queue, job_ref="inbox-dedupe.json"
        )
        == 1
    )
    assert vera_service.maybe_auto_surface_linked_completion(queue, sid) is None


def test_uncertain_mutating_completion_is_not_live_delivered_as_final(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    sid = "vera-live-conservative"
    vera_service.append_session_turn(queue, sid, role="user", text="seed")
    vera_service.register_session_linked_job(queue, sid, job_ref="inbox-parent-live.json")

    _write_job_artifacts(
        queue,
        "inbox-parent-live.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
            "latest_summary": "Parent delegated child work.",
            "normalized_outcome_class": "succeeded",
            "stop_reason": "enqueue_child",
            "child_refs": [{"child_job_id": "inbox-child.json"}],
            "child_summary": {
                "total": 1,
                "done": 0,
                "awaiting_approval": 0,
                "pending": 1,
                "failed": 0,
                "canceled": 0,
                "unknown": 0,
            },
            "review_summary": {"execution_capabilities": {"side_effect_class": "execute"}},
        },
        state={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
        },
    )
    parent_job = queue / "done" / "inbox-parent-live.json"
    parent_payload = json.loads(parent_job.read_text(encoding="utf-8"))
    parent_payload["job_intent"] = {"request_kind": "run_command"}
    parent_job.write_text(json.dumps(parent_payload), encoding="utf-8")

    delivered = vera_service.maybe_deliver_linked_completion_live_for_job(
        queue, job_ref="inbox-parent-live.json"
    )
    assert delivered == 0

    turns = vera_service.read_session_turns(queue, sid)
    assert not any("Parent delegated child work" in turn["text"] for turn in turns)


def test_non_linked_terminal_jobs_do_not_attach_to_session(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    _write_job_artifacts(
        queue,
        "inbox-unlinked.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
            "latest_summary": "unlinked completed",
        },
        state={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
        },
    )

    client.post("/chat", data={"session_id": sid, "message": "hello"})

    assert vera_service.read_linked_job_completions(queue, sid) == []


def test_chat_updates_endpoint_reports_changes_for_active_session(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    baseline = client.get("/chat/updates", params={"session_id": sid, "since_count": 0})
    assert baseline.status_code == 200
    baseline_payload = baseline.json()
    assert baseline_payload["changed"] is False
    assert baseline_payload["turn_count"] == 0
    assert isinstance(baseline_payload.get("updated_at_ms"), int)
    assert "turns" not in baseline_payload

    vera_service.append_session_turn(queue, sid, role="assistant", text="live completion")

    updated = client.get("/chat/updates", params={"session_id": sid, "since_count": 0})
    assert updated.status_code == 200
    updated_payload = updated.json()
    assert updated_payload["changed"] is True
    assert updated_payload["turn_count"] == 1
    assert isinstance(updated_payload.get("turns"), list)
    assert updated_payload["turns"][0]["text"] == "live completion"

    up_to_date = client.get("/chat/updates", params={"session_id": sid, "since_count": 1})
    assert up_to_date.status_code == 200
    assert up_to_date.json()["changed"] is False


def test_chat_updates_detects_new_turn_when_session_window_is_full(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    for idx in range(vera_service.MAX_SESSION_TURNS):
        role = "user" if idx % 2 == 0 else "assistant"
        vera_service.append_session_turn(queue, sid, role=role, text=f"seed-{idx}")

    baseline = client.get(
        "/chat/updates", params={"session_id": sid, "since_count": vera_service.MAX_SESSION_TURNS}
    )
    assert baseline.status_code == 200
    baseline_payload = baseline.json()
    assert baseline_payload["changed"] is False
    updated_at_ms = int(baseline_payload["updated_at_ms"])

    vera_service.append_session_turn(
        queue, sid, role="assistant", text="live completion second wave"
    )

    refreshed = client.get(
        "/chat/updates",
        params={
            "session_id": sid,
            "since_count": vera_service.MAX_SESSION_TURNS,
            "since_updated_at_ms": updated_at_ms,
        },
    )
    assert refreshed.status_code == 200
    refreshed_payload = refreshed.json()
    assert refreshed_payload["changed"] is True
    assert refreshed_payload["turn_count"] == vera_service.MAX_SESSION_TURNS
    assert isinstance(refreshed_payload.get("turns"), list)
    assert refreshed_payload["turns"][-1]["text"] == "live completion second wave"


def test_index_includes_active_chat_polling_hook(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)

    res = client.get("/")
    assert res.status_code == 200
    assert 'data-turn-count="0"' in res.text
    assert "/chat/updates?" in res.text
    assert "window.setInterval(pollForTurnUpdates, 2000);" in res.text


def test_single_line_write_content_preserved_in_preview():
    preview = maybe_draft_job_payload("write single-line.txt with content: Hello world")

    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/single-line.txt"
    assert preview["write_file"]["content"] == "Hello world"
    assert preview["write_file"]["mode"] == "overwrite"


def test_multiline_write_content_preserved_in_preview():
    preview = maybe_draft_job_payload(
        """Create a file called multiline-test.txt in my notes with exactly this content:

Line A
Line B
Line C
"""
    )

    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/multiline-test.txt"
    assert preview["write_file"]["content"] == "Line A\nLine B\nLine C"


def test_multiline_content_keeps_first_line():
    preview = maybe_draft_job_payload(
        """Write first-line.txt with content:

First line
Second line
"""
    )

    assert preview is not None
    assert preview["write_file"]["content"].splitlines()[0] == "First line"


def test_overwrite_update_phrasing_preserves_multiline_content():
    initial = maybe_draft_job_payload('write overwrite-test-2.txt with content "Original content"')

    assert initial is not None
    updated = maybe_draft_job_payload(
        """Update overwrite-test-2.txt with this content:

Original content
Version 1
""",
        active_preview=initial,
    )

    assert updated is not None
    assert updated["write_file"]["content"] == "Original content\nVersion 1"


def test_preview_remains_submit_ready_when_multiline_content_present():
    preview = maybe_draft_job_payload(
        """Create submit-ready.txt with content:

Line A
Line B
"""
    )

    assert preview is not None
    normalized = normalize_preview_payload(preview)
    assert normalized["write_file"]["content"] == "Line A\nLine B"


def test_existing_quoted_write_behavior_unchanged():
    preview = maybe_draft_job_payload('write a file called hello.txt with content "hello"')

    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/hello.txt"
    assert preview["write_file"]["content"] == "hello"
    assert preview["write_file"]["mode"] == "overwrite"
