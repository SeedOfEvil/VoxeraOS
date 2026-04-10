from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from voxera.core.writing_draft_intent import extract_text_draft_from_reply
from voxera.models import AppConfig, WebInvestigationConfig
from voxera.vera import prompt as vera_prompt
from voxera.vera import service as vera_service
from voxera.vera import session_store as vera_session_store
from voxera.vera.brave_search import WebSearchResult
from voxera.vera.investigation_derivations import (
    is_investigation_derived_followup_save_request,
)
from voxera.vera.preview_drafting import (
    diagnostics_request_refusal,
    drafting_guidance,
    maybe_draft_job_payload,
)
from voxera.vera.preview_submission import normalize_preview_payload
from voxera.vera.weather import WeatherLocation, WeatherSnapshot
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


def _sample_weather_snapshot(*, query: str = "Calgary AB") -> WeatherSnapshot:
    location = WeatherLocation(
        query=query,
        name="Calgary",
        admin1="Alberta",
        country="Canada",
        latitude=51.0447,
        longitude=-114.0719,
        timezone="America/Edmonton",
    )
    return WeatherSnapshot(
        location=location,
        retrieved_at_ms=1234567890,
        current_temperature_c=3.2,
        current_feels_like_c=1.1,
        current_condition="cloudy skies",
        current_wind_kph=16.0,
        today_high_c=6.1,
        today_low_c=-4.2,
        hourly=[
            {
                "time": "2026-03-21T12:00",
                "display_time": "Sat 12 PM",
                "temperature_c": 3.0,
                "condition": "cloudy skies",
            },
            {
                "time": "2026-03-21T13:00",
                "display_time": "Sat 1 PM",
                "temperature_c": 4.0,
                "condition": "cloudy skies",
            },
            {
                "time": "2026-03-21T14:00",
                "display_time": "Sat 2 PM",
                "temperature_c": 5.0,
                "condition": "cloudy skies",
            },
        ],
        daily=[
            {
                "date": "2026-03-21",
                "weekday": "Sat",
                "high_c": 6.1,
                "low_c": -4.2,
                "condition": "cloudy skies",
            },
            {
                "date": "2026-03-22",
                "weekday": "Sun",
                "high_c": 7.0,
                "low_c": -2.0,
                "condition": "partly cloudy skies",
            },
            {
                "date": "2026-03-23",
                "weekday": "Mon",
                "high_c": 8.0,
                "low_c": -1.0,
                "condition": "clear skies",
            },
            {
                "date": "2026-03-24",
                "weekday": "Tue",
                "high_c": 9.0,
                "low_c": 0.0,
                "condition": "rain",
            },
            {
                "date": "2026-03-25",
                "weekday": "Wed",
                "high_c": 10.0,
                "low_c": 1.0,
                "condition": "cloudy skies",
            },
            {
                "date": "2026-03-26",
                "weekday": "Thu",
                "high_c": 11.0,
                "low_c": 2.0,
                "condition": "cloudy skies",
            },
            {
                "date": "2026-03-27",
                "weekday": "Fri",
                "high_c": 12.0,
                "low_c": 3.0,
                "condition": "clear skies",
            },
            {
                "date": "2026-03-28",
                "weekday": "Sat",
                "high_c": 13.0,
                "low_c": 4.0,
                "condition": "rain showers",
            },
        ],
    )


def _brave_enabled_config(max_results: int = 5) -> AppConfig:
    return AppConfig(
        web_investigation=WebInvestigationConfig(
            api_key_ref="test-brave",
            env_api_key_var="BRAVE_API_KEY",
            max_results=max_results,
        )
    )


def test_vera_web_page_renders_single_pane(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    res = client.get("/")

    assert res.status_code == 200
    assert "Reasoning partner" in res.text
    assert "composer" in res.text
    assert "VoxeraOS queue handoff" in res.text
    assert "How to use Vera" in res.text
    assert "Starter prompts" in res.text
    assert "Search the web for the latest Brave Search API documentation" in res.text
    assert 'data-prompt="Save that to a note"' in res.text
    assert "DEV diagnostics" in res.text
    assert "vera_thread_manual_scroll_up" in res.text
    assert "bindGuidanceChips" in res.text


def test_vera_web_page_renders_when_voice_runtime_flags_fail_to_load(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    monkeypatch.setattr(
        vera_app_module,
        "load_voice_foundation_flags",
        lambda: (_ for _ in ()).throw(ValueError("broken voice flags")),
    )

    client = TestClient(vera_app_module.app)
    res = client.get("/")

    assert res.status_code == 200
    assert "voice_foundation_enabled" in res.text
    assert "voice_runtime_unavailable" not in res.text


def test_vera_web_template_handles_missing_voice_runtime_field():
    tmpl = vera_app_module.templates.get_template("index.html")
    html = tmpl.render(
        session_id="vera-test",
        turns=[],
        mode_status="conversation",
        queue_boundary="queue boundary",
        error="",
        debug_info={
            "dev_mode": True,
            "mode_status": "conversation",
            "session_id": "vera-test",
            "turn_count": 0,
            "max_session_turns": 8,
            "session_file_exists": False,
            "session_file": "/tmp/missing.json",
            "preview_available": False,
            "last_user_input_origin": "typed",
            "handoff_status": "none",
            "handoff_job_id": None,
        },
        system_prompt="prompt",
        pending_preview=None,
        drafting_examples=[],
        main_screen_guidance={
            "title": "Title",
            "summary": "Summary",
            "preview_hint": "Hint",
            "groups": [],
        },
    )
    assert "voice_foundation_enabled" in html
    assert "voice_runtime_unavailable" in html


def test_vera_web_chat_returns_assistant_response(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {"answer": f"Echo: {user_message}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    home = client.get("/")
    assert home.status_code == 200
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post("/chat", data={"session_id": sid, "message": "hello"})

    assert res.status_code == 200
    assert "Echo: hello" in res.text


def test_vera_web_voice_transcript_fails_closed_when_disabled(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        return {"answer": f"Echo: {user_message}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    monkeypatch.delenv("VOXERA_ENABLE_VOICE_FOUNDATION", raising=False)
    monkeypatch.delenv("VOXERA_ENABLE_VOICE_INPUT", raising=False)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "hello", "input_origin": "voice_transcript"},
    )

    assert res.status_code == 200
    assert "Voice transcript input is disabled by runtime flags." in res.text
    turns = vera_session_store.read_session_turns(queue, sid)
    assert turns == []


def test_vera_web_voice_transcript_origin_is_persisted_and_visible(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    monkeypatch.setenv("VOXERA_ENABLE_VOICE_FOUNDATION", "1")
    monkeypatch.setenv("VOXERA_ENABLE_VOICE_INPUT", "1")

    async def _fake_reply(*, turns, user_message, **_kw):
        return {"answer": f"Echo: {user_message}", "status": "ok:test", "turn_count": len(turns)}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": "  schedule   uptime    check  ",
            "input_origin": "voice_transcript",
        },
    )

    assert res.status_code == 200
    assert "You (voice transcript)" in res.text
    assert "Echo: schedule uptime check" in res.text
    turns = vera_session_store.read_session_turns(queue, sid)
    assert turns[0]["input_origin"] == "voice_transcript"
    assert turns[0]["text"] == "schedule uptime check"


def test_vera_empty_state_guidance_renders_prompt_groups(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)

    res = client.get("/")

    assert res.status_code == 200
    for label in ("Ask", "Investigate", "Save", "Write", "Code", "System"):
        assert f">{label}<" in res.text
    assert "Save it as weather.md" in res.text
    assert "Write a 2 page essay about black holes" in res.text
    assert "Check status of voxera-vera.service" in res.text


def test_vera_web_context_is_preserved_and_capped(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {"answer": f"turns={len(turns)} latest={user_message}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    for i in range(6):
        res = client.post("/chat", data={"session_id": sid, "message": f"msg-{i}"})
        assert res.status_code == 200

    turns = vera_session_store.read_session_turns(queue, sid)
    assert len(turns) == vera_session_store.MAX_SESSION_TURNS
    assert turns[0]["text"] == "msg-2"


def test_vera_clear_chat_and_context(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "keep context"})
    assert vera_session_store.read_session_turns(queue, sid)

    res = client.post("/clear", data={"session_id": sid})
    assert res.status_code == 200
    assert "How to use Vera" in res.text
    assert "Preview → submit:" in res.text
    assert vera_session_store.read_session_turns(queue, sid) == []


def test_guidance_is_hidden_once_chat_has_turns(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {"answer": f"Echo: {user_message}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post("/chat", data={"session_id": sid, "message": "hello"})

    assert res.status_code == 200
    assert 'aria-labelledby="inline-group-' not in res.text
    assert "Echo: hello" in res.text


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

    async def _fake_reply(*, turns, user_message, **_kw):
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

    async def _fake_reply(*, turns, user_message, **_kw):
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
    assert vera_session_store.read_session_preview(queue, sid) is None
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))


def test_finance_informational_query_does_not_auto_prepare_voxera_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
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
    assert vera_session_store.read_session_preview(queue, sid) is None
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))


def test_news_query_skips_preview_builder_and_stays_informational(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _builder_should_not_run(**kwargs):
        raise AssertionError("preview builder should not run for informational web turns")

    async def _fake_reply(*, turns, user_message, **_kw):
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
    assert vera_session_store.read_session_preview(queue, sid) is None
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
            {
                "result_id": 4,
                "title": "Result Four",
                "url": "https://another.example.org/4",
                "source": "another.example.org",
                "snippet": "snippet four",
                "why_it_matched": "matched four",
                "rank": 4,
            },
        ],
    }


def test_structured_investigation_is_stored_and_numbered(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
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

    stored = vera_session_store.read_session_investigation(queue, sid)
    assert stored is not None
    assert [row["result_id"] for row in stored["results"]] == [1, 2, 3, 4]


def test_save_single_investigation_result_creates_governed_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_session_store.write_session_investigation(queue, sid, _sample_investigation_payload())

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "save result 2 to a note"},
    )

    assert res.status_code == 200
    preview = vera_session_store.read_session_preview(queue, sid)
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
    vera_session_store.write_session_investigation(queue, sid, _sample_investigation_payload())

    client.post(
        "/chat",
        data={"session_id": sid, "message": "save results 1 and 3 to note"},
    )

    preview = vera_session_store.read_session_preview(queue, sid)
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
    vera_session_store.write_session_investigation(queue, sid, _sample_investigation_payload())

    client.post(
        "/chat",
        data={"session_id": sid, "message": "save all findings to research.md"},
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/research.md"
    content = preview["write_file"]["content"]
    assert "## Result 1" in content
    assert "## Result 2" in content
    assert "## Result 3" in content
    assert "## Result 4" in content


def test_invalid_investigation_reference_fails_closed_with_clear_message(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_session_store.write_session_investigation(queue, sid, _sample_investigation_payload())

    client.post(
        "/chat",
        data={"session_id": sid, "message": "save result 9 to a note"},
    )

    turns = vera_session_store.read_session_turns(queue, sid)
    assert turns[-1]["role"] == "assistant"
    assert "couldn't resolve" in turns[-1]["text"].lower()
    assert vera_session_store.read_session_preview(queue, sid) is None


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
    assert vera_session_store.read_session_preview(queue, sid) is None


def test_compare_selected_investigation_results_stores_derived_output(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_session_store.write_session_investigation(queue, sid, _sample_investigation_payload())

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "compare results 1 and 3"},
    )

    assert res.status_code == 200
    turns = vera_session_store.read_session_turns(queue, sid)
    assert "compared results: 1, 3" in turns[-1]["text"].lower()
    derived = vera_session_store.read_session_derived_investigation_output(queue, sid)
    assert derived is not None
    assert derived["derivation_type"] == "comparison"
    assert derived["selected_result_ids"] == [1, 3]


def test_summarize_selected_investigation_results_stores_derived_output(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_session_store.write_session_investigation(queue, sid, _sample_investigation_payload())

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "summarize results 2 and 4"},
    )

    assert res.status_code == 200
    turns = vera_session_store.read_session_turns(queue, sid)
    assert "selected results: 2, 4" in turns[-1]["text"].lower()
    derived = vera_session_store.read_session_derived_investigation_output(queue, sid)
    assert derived is not None
    assert derived["derivation_type"] == "summary"
    assert derived["selected_result_ids"] == [2, 4]


def test_summarize_all_findings_uses_all_results(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_session_store.write_session_investigation(queue, sid, _sample_investigation_payload())

    client.post(
        "/chat",
        data={"session_id": sid, "message": "summarize all findings"},
    )

    derived = vera_session_store.read_session_derived_investigation_output(queue, sid)
    assert derived is not None
    assert derived["selected_result_ids"] == [1, 2, 3, 4]


def test_expand_result_stores_saveable_derived_output_and_save_it_works(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        if "expand result 1" in user_message.lower():
            return {
                "answer": (
                    "Result 1 points to a practical AI incident-response workflow centered on fast triage, "
                    "source-backed containment, and human review before any governed action."
                ),
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_session_store.write_session_investigation(queue, sid, _sample_investigation_payload())

    expand_res = client.post(
        "/chat",
        data={"session_id": sid, "message": "Expand result 1 please"},
    )
    assert expand_res.status_code == 200

    derived = vera_session_store.read_session_derived_investigation_output(queue, sid)
    assert derived is not None
    assert derived["derivation_type"] == "expanded_result"
    assert derived["selected_result_ids"] == [1]
    assert "practical ai incident-response workflow" in derived["answer"].lower()

    save_res = client.post(
        "/chat",
        data={"session_id": sid, "message": "save it"},
    )

    assert save_res.status_code == 200
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["goal"] == "write investigation expanded result to markdown note"
    assert "# Expanded Investigation Result 1" in preview["write_file"]["content"]
    assert "practical ai incident-response workflow" in preview["write_file"]["content"].lower()
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))


def test_expand_result_then_save_it_as_named_markdown_file_works(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        if "expand result 1" in user_message.lower():
            return {
                "answer": "Expanded writeup for result 1 with a concrete, saveable narrative.",
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_session_store.write_session_investigation(queue, sid, _sample_investigation_payload())

    client.post(
        "/chat",
        data={"session_id": sid, "message": "expand result 1 please"},
    )
    client.post(
        "/chat",
        data={"session_id": sid, "message": "save it as expanded-result-1.md"},
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/expanded-result-1.md"
    assert "# Expanded Investigation Result 1" in preview["write_file"]["content"]


def test_save_derived_summary_creates_governed_preview_only(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_session_store.write_session_investigation(queue, sid, _sample_investigation_payload())

    client.post(
        "/chat",
        data={"session_id": sid, "message": "summarize results 1 and 2"},
    )
    client.post(
        "/chat",
        data={"session_id": sid, "message": "save that summary to investigation-summary.md"},
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/investigation-summary.md"
    assert "# Investigation Summary" in preview["write_file"]["content"]
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))


def test_investigation_transform_prompts_are_not_classified_as_derived_save_requests() -> None:
    assert not is_investigation_derived_followup_save_request(
        "Now write a short article based on that summary for a technical teammate."
    )
    assert not is_investigation_derived_followup_save_request(
        "Turn that summary into a concise article."
    )
    assert not is_investigation_derived_followup_save_request(
        "Write a short essay based on that summary."
    )
    assert not is_investigation_derived_followup_save_request(
        "Rewrite that summary as a teammate-ready note."
    )
    assert not is_investigation_derived_followup_save_request("Expand that summary into a writeup.")
    assert is_investigation_derived_followup_save_request("save it")
    assert is_investigation_derived_followup_save_request("save that to a note")
    assert is_investigation_derived_followup_save_request("save it as brave-summary.md")


def test_compare_then_save_that_to_note_uses_derived_output_precedence(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_session_store.write_session_investigation(queue, sid, _sample_investigation_payload())

    client.post(
        "/chat",
        data={"session_id": sid, "message": "compare results 1 and 3"},
    )
    client.post(
        "/chat",
        data={"session_id": sid, "message": "save that to a note"},
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["goal"] == "write investigation comparison to markdown note"
    assert "# Investigation Comparison" in preview["write_file"]["content"]


def test_compare_then_save_that_without_note_target_uses_derived_output_precedence(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_session_store.write_session_investigation(queue, sid, _sample_investigation_payload())

    client.post(
        "/chat",
        data={"session_id": sid, "message": "compare results 1 and 3"},
    )
    client.post(
        "/chat",
        data={"session_id": sid, "message": "save that"},
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["goal"] == "write investigation comparison to markdown note"
    assert "# Investigation Comparison" in preview["write_file"]["content"]


def test_summarize_then_save_that_to_markdown_uses_derived_output_precedence(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_session_store.write_session_investigation(queue, sid, _sample_investigation_payload())

    client.post(
        "/chat",
        data={"session_id": sid, "message": "summarize results 2 and 4"},
    )
    client.post(
        "/chat",
        data={"session_id": sid, "message": "save that to a markdown file"},
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["goal"] == "write investigation summary to markdown note"
    assert "# Investigation Summary" in preview["write_file"]["content"]


def test_summarize_then_save_that_without_note_target_uses_derived_output_precedence(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_session_store.write_session_investigation(queue, sid, _sample_investigation_payload())

    client.post(
        "/chat",
        data={"session_id": sid, "message": "summarize results 2 and 4"},
    )
    client.post(
        "/chat",
        data={"session_id": sid, "message": "save that"},
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["goal"] == "write investigation summary to markdown note"
    assert "# Investigation Summary" in preview["write_file"]["content"]


def test_derived_output_does_not_override_newer_conversational_answer_for_save_that(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        if "dark matter" in user_message.lower():
            return {
                "answer": "Dark matter is unseen matter inferred from gravitational effects.",
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_session_store.write_session_investigation(queue, sid, _sample_investigation_payload())

    client.post(
        "/chat",
        data={"session_id": sid, "message": "compare results 1 and 3"},
    )
    client.post(
        "/chat",
        data={"session_id": sid, "message": "save that to a note"},
    )
    vera_session_store.write_session_preview(queue, sid, None)
    client.post(
        "/chat",
        data={"session_id": sid, "message": "Explain dark matter simply."},
    )
    client.post(
        "/chat",
        data={"session_id": sid, "message": "save that to a note called darkmatter.txt"},
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/darkmatter.txt"
    assert (
        preview["write_file"]["content"]
        == "Dark matter is unseen matter inferred from gravitational effects."
    )


def test_save_derived_output_without_existing_derivation_fails_closed(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={"session_id": sid, "message": "save that summary to a note"},
    )

    turns = vera_session_store.read_session_turns(queue, sid)
    assert (
        "couldn't find a current investigation comparison, summary, or expanded result"
        in turns[-1]["text"].lower()
    )
    assert vera_session_store.read_session_preview(queue, sid) is None


def test_expand_result_save_it_then_create_it_submits_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        if "expand result 1" in user_message.lower():
            return {
                "answer": "Expanded result 1 that should become a governed saveable artifact.",
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_session_store.write_session_investigation(queue, sid, _sample_investigation_payload())

    client.post(
        "/chat",
        data={"session_id": sid, "message": "expand result 1 please"},
    )
    client.post(
        "/chat",
        data={"session_id": sid, "message": "save it"},
    )
    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "create it"},
    )

    assert res.status_code == 200
    assert "I submitted the job to VoxeraOS" in res.text
    jobs = list((queue / "inbox").glob("inbox-*.json"))
    assert len(jobs) == 1


def test_invalid_reference_for_summary_fails_closed(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_session_store.write_session_investigation(queue, sid, _sample_investigation_payload())

    client.post(
        "/chat",
        data={"session_id": sid, "message": "summarize results 2 and 9"},
    )

    turns = vera_session_store.read_session_turns(queue, sid)
    assert "couldn't resolve those result references for summary" in turns[-1]["text"].lower()
    assert vera_session_store.read_session_derived_investigation_output(queue, sid) is None


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

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["mode"] == "append"
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/log.txt"


def test_put_that_into_file_after_informational_turn_does_not_claim_phantom_preview(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
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
    assert vera_session_store.read_session_preview(queue, sid) is None

    res = client.post("/chat", data={"session_id": sid, "message": "put that into the file"})

    assert "I've prepared a draft" not in res.text
    assert vera_session_store.read_session_preview(queue, sid) is None
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))


def test_informational_query_then_send_it_does_not_enqueue(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
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
    assert vera_session_store.read_session_preview(queue, sid) is None
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))


def test_missing_key_informational_query_is_honest_and_no_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _builder_should_not_run(**kwargs):
        raise AssertionError("preview builder should not run for informational web turns")

    async def _fake_reply(*, turns, user_message, **_kw):
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
    assert vera_session_store.read_session_preview(queue, sid) is None
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))


def test_explicit_internal_search_request_stays_no_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _builder_should_not_run(**kwargs):
        raise AssertionError("preview builder should not run for informational web turns")

    async def _fake_reply(*, turns, user_message, **_kw):
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
    assert vera_session_store.read_session_preview(queue, sid) is None
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))


def test_action_request_creates_preview_only_until_explicit_handoff(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post("/chat", data={"session_id": sid, "message": "open https://example.com"})

    assert "nothing has been submitted yet" in res.text.lower()
    assert "Preview panel · Active VoxeraOS draft (authoritative, not submitted)" in res.text
    assert list((queue / "inbox").glob("*.json")) == [] if (queue / "inbox").exists() else True


def test_explicit_submit_phrase_without_preview_is_honest_non_submission(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post("/chat", data={"session_id": sid, "message": "submit now please"})

    assert "not submitted" in res.text.lower() or "prepare" in res.text.lower()
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))


def test_prepare_preview_sets_preview_available_true_for_natural_open_phrase(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post("/chat", data={"session_id": sid, "message": "Can you open example.com?"})

    assert "nothing has been submitted yet" in res.text.lower()
    assert "preview_available</b>: True" in res.text
    preview = vera_session_store.read_session_preview(queue, sid)
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
    assert vera_session_store.read_session_preview(queue, sid) == {"goal": "open https://cnn.com"}


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
    preview = vera_session_store.read_session_preview(queue, sid)
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
    preview = vera_session_store.read_session_preview(queue, sid)
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
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is None


def test_filesystem_find_request_creates_governed_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "find txt files in my notes/runtime-validation folder"},
    )
    assert "Preview panel · Active VoxeraOS draft" in res.text
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["steps"][0]["skill_id"] == "files.find"
    assert preview["steps"][0]["args"]["root_path"] == "~/VoxeraOS/notes/runtime-validation"
    assert preview["steps"][0]["args"]["glob"] == "*.txt"


def test_filesystem_copy_preview_then_submit_is_truthful_and_queue_backed(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    prep = client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": "copy ~/VoxeraOS/notes/runtime-validation/src/a.txt to ~/VoxeraOS/notes/runtime-validation/dst/a-copy.txt",
        },
    )
    assert "nothing has been submitted yet" in prep.text.lower()
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["steps"][0]["skill_id"] == "files.copy"

    submit = client.post("/chat", data={"session_id": sid, "message": "submit it"})
    assert "I submitted the job to VoxeraOS" in submit.text
    inbox_jobs = list((queue / "inbox").glob("inbox-*.json"))
    assert len(inbox_jobs) == 1
    payload = json.loads(inbox_jobs[0].read_text(encoding="utf-8"))
    assert payload["steps"][0]["skill_id"] == "files.copy"
    assert payload["steps"][0]["args"]["source_path"].endswith("/src/a.txt")


def test_yes_please_without_preview_fails_closed_even_if_model_claims_submission(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = (turns, user_message)
        return {"answer": "I submitted the request to VoxeraOS.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post("/chat", data={"session_id": sid, "message": "yes please"})

    lowered = res.text.lower()
    assert "have not submitted" in lowered or "not submitted" in lowered or "prepare" in lowered
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

    assert "nothing has been submitted yet" in res.text.lower()
    preview = vera_session_store.read_session_preview(queue, sid)
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

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/skibbz.txt"
    assert preview["write_file"]["content"] == "hello"


def test_content_refinement_phrase_script_text_updates_active_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    generated_script = "\n".join(
        (
            'New-ADUser -Name "Skibbidy" -SamAccountName "Skibbidy" \\',
            '    -AccountPassword (ConvertTo-SecureString "P@ssw0rd!" -AsPlainText -Force) \\',
            "    -Enabled $true",
        )
    )

    async def _fake_reply(*, turns, user_message, code_draft=False, writing_draft=False, **kwargs):
        _ = (turns, writing_draft, kwargs)
        if "add content to script.ps1" in user_message.lower():
            assert code_draft is True
            return {
                "answer": (
                    "I updated the draft PowerShell script.\n\n"
                    f"```powershell\n{generated_script}\n```"
                ),
                "status": "ok:code_draft",
            }
        return {"answer": "I prepared the script preview shell.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "write a file called script.ps1"})

    script_text = "an Active Directory script that creates a user called Skibbidy"
    client.post(
        "/chat",
        data={"session_id": sid, "message": f"add content to script.ps1 {script_text}"},
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    turns = vera_session_store.read_session_turns(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/script.ps1"
    assert preview["write_file"]["content"] == generated_script
    assert "updated the draft powershell script" in turns[-1]["text"].lower()


def test_targeted_code_preview_refinement_uses_code_draft_hint(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    observed: dict[str, bool] = {}

    async def _fake_reply(*, turns, user_message, code_draft=False, writing_draft=False, **kwargs):
        _ = (turns, writing_draft, kwargs)
        if "add content to script.ps1" in user_message.lower():
            observed["code_draft"] = code_draft
            return {
                "answer": "I updated the draft.\n\n```powershell\nWrite-Host 'Skibbidy'\n```",
                "status": "ok:code_draft",
            }
        return {"answer": "I prepared the script preview shell.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "write a file called script.ps1"})

    script_text = "an Active Directory script that creates a user called Skibbidy"
    client.post(
        "/chat",
        data={"session_id": sid, "message": f"add content to script.ps1 {script_text}"},
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert observed["code_draft"] is True
    assert preview is not None
    assert preview["write_file"]["content"] == "Write-Host 'Skibbidy'"


def test_targeted_code_preview_refinement_uses_generated_script_reply_as_preview_truth(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    generated_script = "\n".join(
        (
            'New-ADUser -Name "Skibbidy" -SamAccountName "Skibbidy" \\',
            '    -AccountPassword (ConvertTo-SecureString "P@ssw0rd!" -AsPlainText -Force) \\',
            "    -Enabled $true",
        )
    )

    async def _fake_reply(*, turns, user_message, code_draft=False, writing_draft=False, **kwargs):
        _ = (turns, code_draft, writing_draft, kwargs)
        if "add content to script.ps1" in user_message.lower():
            return {
                "answer": (
                    "I updated the draft PowerShell script.\n\n"
                    f"```powershell\n{generated_script}\n```"
                ),
                "status": "ok:test",
            }
        return {"answer": "I prepared the script preview shell.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "write a file called script.ps1"})

    res = client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": (
                "add content to script.ps1 an Active Directory script that creates a user "
                "called Skibbidy"
            ),
        },
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    turns = vera_session_store.read_session_turns(queue, sid)
    assert res.status_code == 200
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/script.ps1"
    assert preview["write_file"]["content"] == generated_script
    assert "updated the draft powershell script" in turns[-1]["text"].lower()


def test_targeted_code_preview_refinement_submit_uses_authoritative_preview_truth(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    generated_script = "\n".join(
        (
            'New-ADGroup -Name "wowza" -GroupScope Global -GroupCategory Security',
            "Write-Host 'Created wowza'",
        )
    )

    async def _fake_reply(*, turns, user_message, code_draft=False, writing_draft=False, **kwargs):
        _ = (turns, writing_draft, kwargs)
        if "add content to script.ps1" in user_message.lower():
            assert code_draft is True
            return {
                "answer": (
                    "I updated the draft PowerShell script.\n\n"
                    f"```powershell\n{generated_script}\n```"
                ),
                "status": "ok:code_draft",
            }
        return {"answer": "I prepared the script preview shell.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post("/chat", data={"session_id": sid, "message": "write a file called script.ps1"})
    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": (
                "add content to script.ps1 an Active Directory script that creates a group "
                "security called wowza"
            ),
        },
    )
    submit = client.post("/chat", data={"session_id": sid, "message": "submit it"})

    assert submit.status_code == 200
    jobs = list((queue / "inbox").glob("inbox-*.json"))
    assert len(jobs) == 1
    payload = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert payload["write_file"]["path"] == "~/VoxeraOS/notes/script.ps1"
    assert payload["write_file"]["content"] == generated_script


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

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = (turns, user_message)
        return {"answer": "Working on it.", "status": "ok:test"}

    async def _fake_builder(
        *,
        turns,
        user_message,
        active_preview,
        enrichment_context=None,
        investigation_context=None,
        **_kw,
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
    assert vera_session_store.read_session_preview(queue, sid) == {
        "goal": "open https://openai.com"
    }


def test_submit_after_model_preview_replacement_uses_latest_payload(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = (turns, user_message)
        return {"answer": "Working on it.", "status": "ok:test"}

    async def _fake_builder(
        *,
        turns,
        user_message,
        active_preview,
        enrichment_context=None,
        investigation_context=None,
        **_kw,
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

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = (turns, user_message)
        return {"answer": "Got it, I kept this conversational.", "status": "ok:test"}

    async def _fake_builder(
        *,
        turns,
        user_message,
        active_preview,
        enrichment_context=None,
        investigation_context=None,
        **_kw,
    ):
        _ = (turns, user_message, active_preview)
        return {"goal": "", "write_file": "bad-shape"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _fake_builder)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_session_store.write_session_preview(
        queue, sid, {"goal": "write a note called scipptyaway.txt"}
    )

    res = client.post("/chat", data={"session_id": sid, "message": "add content"})

    assert "current draft is still in the preview" in res.text.lower()
    assert vera_session_store.read_session_preview(queue, sid) == {
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
    assert vera_session_store.read_session_preview(queue, sid) == {
        "goal": "open https://example.com"
    }

    cleared = client.post("/clear", data={"session_id": sid})
    assert "Submit current preview to VoxeraOS" not in cleared.text
    assert vera_session_store.read_session_preview(queue, sid) is None

    second = client.post("/chat", data={"session_id": sid, "message": "open openai.com"})
    assert '"goal": "open https://openai.com"' in second.text
    assert vera_session_store.read_session_preview(queue, sid) == {
        "goal": "open https://openai.com"
    }


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
    preview = vera_session_store.read_session_preview(queue, sid)
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

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = (turns, user_message)
        return {"answer": "I left the preview as-is.", "status": "ok:test"}

    async def _fake_builder(
        *,
        turns,
        user_message,
        active_preview,
        enrichment_context=None,
        investigation_context=None,
        **_kw,
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

    assert "nothing has been submitted yet" in res.text.lower()
    assert vera_session_store.read_session_preview(queue, sid) == {
        "goal": "write a note called skibbidy.txt"
    }


def test_builder_multiple_preview_replacements_latest_wins_in_pane(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = (turns, user_message)
        return {"answer": "plain reply", "status": "ok:test"}

    async def _fake_builder(
        *,
        turns,
        user_message,
        active_preview,
        enrichment_context=None,
        investigation_context=None,
        **_kw,
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
    assert vera_session_store.read_session_preview(queue, sid) == {
        "goal": "open https://openai.com"
    }

    c = client.post("/chat", data={"session_id": sid, "message": "update to c"})
    assert '"goal": "open https://github.com"' in c.text
    assert vera_session_store.read_session_preview(queue, sid) == {
        "goal": "open https://github.com"
    }


def test_builder_can_set_preview_without_changing_vera_voice(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = (turns, user_message)
        return {"answer": "I updated the target in the preview.", "status": "ok:test"}

    async def _fake_builder(
        *,
        turns,
        user_message,
        active_preview,
        enrichment_context=None,
        investigation_context=None,
        **_kw,
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
    assert vera_session_store.read_session_preview(queue, sid) == {
        "goal": "open https://openai.com"
    }


def test_chat_model_cannot_bypass_handoff_with_fake_submission_language(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
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

    async def _fake_reply(*, turns, user_message, **_kw):
        return {"answer": f"ack {len(turns)}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "hello"})
    client.post("/chat", data={"session_id": sid, "message": "open https://example.com"})
    client.post("/chat", data={"session_id": sid, "message": "submit it"})

    turns = vera_session_store.read_session_turns(queue, sid)
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

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None

    res = client.post("/handoff", data={"session_id": sid})
    assert res.status_code == 200
    assert "I submitted the job to VoxeraOS" in res.text
    assert vera_session_store.read_session_preview(queue, sid) is None


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

    async def _fake_reply(*, turns, user_message, **_kw):
        return {"answer": f"ok {len(turns)} {user_message}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post("/chat", data={"session_id": sid, "message": "open https://example.com"})
    for i in range(10):
        client.post("/chat", data={"session_id": sid, "message": f"chat-{i}"})

    assert vera_session_store.read_session_preview(queue, sid) is not None
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

    preview = vera_session_store.read_session_preview(queue, sid)
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

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = (turns, user_message)
        return {"answer": "info mode", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post("/chat", data={"session_id": sid, "message": message})

    assert "info mode" in res.text
    assert vera_session_store.read_session_preview(queue, sid) is None


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

    preview = vera_session_store.read_session_preview(queue, sid)
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

    preview = vera_session_store.read_session_preview(queue, sid)
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

    preview = vera_session_store.read_session_preview(queue, sid)
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

    preview = vera_session_store.read_session_preview(queue, sid)
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

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/jokester.txt"
    turns = vera_session_store.read_session_turns(queue, sid)
    assert "Updated the draft destination to `~/VoxeraOS/notes/jokester.txt`" in turns[-1]["text"]


def test_name_the_note_updates_preview_path_with_explicit_confirmation(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_session_store.write_session_preview(
        queue,
        sid,
        {
            "goal": "write a file called note-123.txt with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/note-123.txt",
                "content": "Mauna Loa background text",
                "mode": "overwrite",
            },
        },
    )
    client.post("/chat", data={"session_id": sid, "message": "name the note bigvolcano.txt"})

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/bigvolcano.txt"
    turns = vera_session_store.read_session_turns(queue, sid)
    assert "Updated the draft destination to `~/VoxeraOS/notes/bigvolcano.txt`" in turns[-1]["text"]

    client.post("/chat", data={"session_id": sid, "message": "submit it"})
    jobs = list((queue / "inbox").glob("inbox-*.json"))
    assert len(jobs) == 1
    payload = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert payload["write_file"]["path"] == "~/VoxeraOS/notes/bigvolcano.txt"


def test_ambiguous_note_naming_fails_closed_with_specific_message(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": "write a file called draft-note.txt with provided content",
        },
    )
    client.post("/chat", data={"session_id": sid, "message": "name the note"})

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/draft-note.txt"
    turns = vera_session_store.read_session_turns(queue, sid)
    assert "couldn’t safely apply that naming update" in turns[-1]["text"].lower()


def test_repeated_note_naming_keeps_latest_canonical_preview_path(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": "write a file called first-name.txt with provided content",
        },
    )
    client.post("/chat", data={"session_id": sid, "message": "name it second-name.txt"})
    client.post("/chat", data={"session_id": sid, "message": "call it final-name.txt"})

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/final-name.txt"


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

    preview = vera_session_store.read_session_preview(queue, sid)
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

    assert vera_session_store.read_session_preview(queue, sid) is not None

    client.post("/chat", data={"session_id": sid, "message": "submit it"})

    assert vera_session_store.read_session_preview(queue, sid) is None


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
    before = vera_session_store.read_session_preview(queue, sid)

    client.post("/chat", data={"session_id": sid, "message": "submit it"})
    after = vera_session_store.read_session_preview(queue, sid)

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

    async def _fake_reply(*, turns, user_message, **_kw):
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

    async def _fake_reply(*, turns, user_message, **_kw):
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
    assert vera_session_store.read_session_preview(queue, sid) == {
        "goal": "open https://openai.com"
    }


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
    vera_session_store.write_session_handoff_state(
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
    assert "<code>succeeded</code>" in res.text
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
    vera_session_store.write_session_handoff_state(
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
    assert "<code>awaiting_approval</code>" in res.text
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
    assert "<code>awaiting_approval</code>" in res.text


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
    assert "<code>awaiting_approval</code>" in res.text


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

    assert "<code>awaiting_approval</code>" in res.text
    assert "blocked on operator approval" in res.text


def test_review_missing_job_is_honest(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post("/chat", data={"session_id": sid, "message": "what happened to job-404?"})

    assert "No job could be resolved" in res.text


def test_review_missing_job_followups_stay_evidence_aware(tmp_path, monkeypatch):
    """Explicit job ID in message → review branch fires and fails closed.
    Hint-only match without job context → also fails closed honestly."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    # Explicit job ID → review fires, fails closed.
    first = client.post("/chat", data={"session_id": sid, "message": "what happened to job-404?"})
    assert "No job could be resolved" in first.text
    # Hint-only match without job context → also fails closed honestly.
    second = client.post("/chat", data={"session_id": sid, "message": "did it work?"})
    assert "No job could be resolved" in second.text


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
    vera_session_store.write_session_handoff_state(
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

    assert "follow-up preview" in res.text.lower()
    assert "nothing has been submitted yet" in res.text.lower()
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))
    assert vera_session_store.read_session_preview(queue, "sid-follow") is not None


def test_voxera_refinement_hides_visible_json_dump_and_updates_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = (turns, user_message)
        return {
            "answer": ('Proposed VoxeraOS Job:\n```json\n{"goal": "open https://openai.com"}\n```'),
            "status": "ok:test",
        }

    async def _fake_builder(
        *,
        turns,
        user_message,
        active_preview,
        enrichment_context=None,
        investigation_context=None,
        **_kw,
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
    assert "nothing has been submitted yet" in res.text.lower()
    assert vera_session_store.read_session_preview(queue, sid) == {
        "goal": "open https://openai.com"
    }


def test_ordinary_voxera_turn_hides_prepared_proposal_wording_in_chat(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = (turns, user_message)
        return {
            "answer": "I prepared a proposal for VoxeraOS. Let me know and I'll submit it.",
            "status": "ok:test",
        }

    async def _fake_builder(
        *,
        turns,
        user_message,
        active_preview,
        enrichment_context=None,
        investigation_context=None,
        **_kw,
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
    assert "nothing has been submitted yet" in res.text.lower()
    assert vera_session_store.read_session_preview(queue, sid) == {
        "goal": "open https://example.com"
    }


def test_chat_does_not_claim_preview_updated_when_builder_update_invalid(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = (turns, user_message)
        return {"answer": "Working on it.", "status": "ok:test"}

    async def _fake_builder(
        *,
        turns,
        user_message,
        active_preview,
        enrichment_context=None,
        investigation_context=None,
        **_kw,
    ):
        _ = (turns, user_message, active_preview)
        return {"goal": "open https://openai.com", "write_file": "bad-shape"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _fake_builder)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_session_store.write_session_preview(queue, sid, {"goal": "open https://example.com"})

    res = client.post("/chat", data={"session_id": sid, "message": "refine it"})

    assert "current draft is still in the preview" in res.text.lower()
    assert vera_session_store.read_session_preview(queue, sid) == {
        "goal": "open https://example.com"
    }


def test_json_config_request_creates_preview_and_shows_fenced_code(tmp_path, monkeypatch):
    """JSON config draft requests now produce a governed preview AND show fenced code.

    Previously this test asserted no preview was created.  The governed code/script
    draft lane (feat: add governed code/script draft lane) intentionally changes
    this: requests like "make me a JSON config" produce a real write_file preview
    so the user can save/submit it, while the fenced JSON block still appears in chat.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = (turns, user_message)
        return {
            "answer": '```json\n{"app":"demo","enabled":true}\n```',
            "status": "ok:test",
        }

    async def _fake_builder(
        *,
        turns,
        user_message,
        active_preview,
        enrichment_context=None,
        investigation_context=None,
        **_kw,
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

    # Fenced JSON content must appear in chat (rendered as <pre><code> by markdown)
    assert "<pre><code>" in res.text
    assert "demo" in res.text
    # A governed write_file preview must also be created (code draft lane)
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"].endswith(".json")
    assert '{"app":"demo","enabled":true}' in preview["write_file"]["content"]


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

    preview = vera_session_store.read_session_preview(queue, sid)
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
    assert "nothing has been submitted yet" in res.text.lower()
    assert vera_session_store.read_session_preview(queue, sid) == {"goal": "open https://cnn.com"}


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

    preview = vera_session_store.read_session_preview(queue, sid)
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
    assert "nothing has been submitted yet" in res.text.lower()
    preview = vera_session_store.read_session_preview(queue, sid)
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

    preview = vera_session_store.read_session_preview(queue, sid)
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

    preview = vera_session_store.read_session_preview(queue, sid)
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

    preview = vera_session_store.read_session_preview(queue, sid)
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

    preview = vera_session_store.read_session_preview(queue, sid)
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

    preview = vera_session_store.read_session_preview(queue, sid)
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
    before = vera_session_store.read_session_preview(queue, sid)
    assert before is not None

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "actually make it a programmer joke"},
    )

    after = vera_session_store.read_session_preview(queue, sid)
    assert after is not None
    assert "```json" not in res.text
    assert "nothing has been submitted yet" in res.text.lower()
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

    preview = vera_session_store.read_session_preview(queue, sid)
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
    before = vera_session_store.read_session_preview(queue, sid)
    assert before is not None

    client.post(
        "/chat",
        data={"session_id": sid, "message": "put that into the file"},
    )

    after = vera_session_store.read_session_preview(queue, sid)
    assert after == before


def test_save_previous_summary_creates_governed_write_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        if "summarize" in user_message.lower():
            return {
                "answer": "Summary:\n- Item A\n- Item B\nOverall: stable outlook.",
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={"session_id": sid, "message": "please summarize what we just discussed"},
    )
    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": "ok take that previous summary and put it in a note called sessionstart.txt",
        },
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    turns = vera_session_store.read_session_turns(queue, sid)
    assert preview is not None
    assert turns[-1]["role"] == "assistant"
    assert "nothing has been submitted" in turns[-1]["text"].lower()
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/sessionstart.txt"
    assert preview["write_file"]["mode"] == "overwrite"
    assert (
        preview["write_file"]["content"] == "Summary:\n- Item A\n- Item B\nOverall: stable outlook."
    )


def test_save_previous_answer_to_markdown_creates_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        if "explain" in user_message.lower():
            return {
                "answer": "This is the previous answer with explanation details.",
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={"session_id": sid, "message": "explain the architecture briefly"},
    )
    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": "write your previous answer to a file called sessionstart.md",
        },
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/sessionstart.md"
    assert preview["write_file"]["mode"] == "overwrite"
    assert (
        preview["write_file"]["content"] == "This is the previous answer with explanation details."
    )


def test_black_hole_explanation_then_save_previous_answer_creates_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        if "black hole" in user_message.lower():
            return {
                "answer": "A black hole is a region of spacetime where gravity is so strong that even light cannot escape.",
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={"session_id": sid, "message": "Explain what a black hole is in a few paragraphs."},
    )
    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": "write your previous answer to a file called blackhole.md",
        },
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/blackhole.md"
    assert (
        preview["write_file"]["content"]
        == "A black hole is a region of spacetime where gravity is so strong that even light cannot escape."
    )


def test_black_hole_explanation_then_essay_followup_creates_authoritative_preview(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        lowered = user_message.lower()
        if "2 page essay" in lowered:
            return {
                "answer": (
                    "I've prepared a draft below.\n\n"
                    "# Black Holes\n\n"
                    "Black holes are extreme objects formed when matter collapses into a compact region. "
                    "This essay expands on the science, the history of discovery, and the role of black holes "
                    "in modern astrophysics."
                ),
                "status": "ok:test",
            }
        return {
            "answer": "Black holes form when gravity collapses enough matter into a region that traps light.",
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "Tell me about black holes."})
    res = client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": "Write a 2 page essay about that expanding on the science and the history.",
        },
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert res.status_code == 200
    assert preview is not None
    assert preview["write_file"]["path"].endswith(".md")
    assert "prepared a draft below" not in preview["write_file"]["content"].lower()
    assert "Black Holes" in preview["write_file"]["content"]
    assert "science" in preview["write_file"]["content"]


def test_roman_empire_rewrite_then_formalize_and_save_as_updates_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        lowered = user_message.lower()
        if "more formal" in lowered:
            return {
                "answer": (
                    "I will refine the essay to a more formal academic tone and prepare it to be saved as `roman-empire-essay.md`.\n\n"
                    "# The Roman Empire\n\n"
                    "The Roman Empire was a foundational Mediterranean power whose administrative structure, "
                    "military organization, and legal traditions influenced later European states."
                ),
                "status": "ok:test",
            }
        if "rewrite that as a short high school essay" in lowered:
            return {
                "answer": (
                    "Here's the draft essay.\n\n"
                    "# The Roman Empire\n\n"
                    "The Roman Empire grew from the city of Rome into a large empire around the Mediterranean. "
                    "It is remembered for its roads, armies, laws, and lasting cultural influence."
                ),
                "status": "ok:test",
            }
        return {
            "answer": "The Roman Empire expanded across Europe, North Africa, and the Near East.",
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post(
        "/chat", data={"session_id": sid, "message": "Find me info about the Roman Empire."}
    )
    client.post(
        "/chat",
        data={"session_id": sid, "message": "Rewrite that as a short high school essay."},
    )
    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": "Make it more formal and save it as roman-empire-essay.md.",
        },
    )

    assert not list((queue / "inbox").glob("inbox-*.json"))

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/roman-empire-essay.md"
    assert "essay overview" not in preview["write_file"]["content"].lower()
    assert "foundational mediterranean power" in preview["write_file"]["content"].lower()

    submit_res = client.post("/chat", data={"session_id": sid, "message": "submit it"})

    jobs = list((queue / "inbox").glob("inbox-*.json"))
    assert submit_res.status_code == 200
    assert "I submitted the job to VoxeraOS" in submit_res.text
    assert len(jobs) == 1
    payload = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert payload["write_file"]["path"] == "~/VoxeraOS/notes/roman-empire-essay.md"
    assert "i will refine the essay" not in payload["write_file"]["content"].lower()


def test_investigation_summary_then_article_followup_creates_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        return {
            "answer": (
                "Article overview\n\n"
                "A short technical article follows.\n\n"
                "# Technical Article\n\n"
                "The investigation suggests a narrow set of likely causes, highlights the highest-signal evidence, "
                "and frames the next debugging steps for a technical teammate."
            ),
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_session_store.write_session_derived_investigation_output(
        queue,
        sid,
        {
            "derivation_type": "summary",
            "answer": "Selected results: 1, 2\nShort takeaway: evidence points to config drift.",
            "markdown": "# Investigation Summary\n\nEvidence points to config drift.\n",
        },
    )

    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": "Now write a short article based on that summary for a technical teammate.",
        },
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"].endswith(".md")
    assert "article overview" not in preview["write_file"]["content"].lower()
    assert "technical teammate" in preview["write_file"]["content"].lower()


def test_investigation_summary_then_article_save_as_named_file_keeps_article_body(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        lowered = user_message.lower()
        if "write a short article based on that summary" in lowered:
            return {
                "answer": (
                    "Article overview\n\n"
                    "Here is the teammate-ready article.\n\n"
                    "# Brave Search API Notes\n\n"
                    "The latest Brave Search API documentation emphasizes authenticated web and local "
                    "search endpoints, response metadata, and predictable integration constraints for "
                    "technical teammates evaluating adoption."
                ),
                "status": "ok:test",
            }
        return {
            "answer": "I will keep the article body and rename the preview file.",
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_session_store.write_session_derived_investigation_output(
        queue,
        sid,
        {
            "derivation_type": "summary",
            "answer": "Selected results: 1, 2\nShort takeaway: evidence points to config drift.",
            "markdown": "# Investigation Summary\n\nEvidence points to config drift.\n",
        },
    )

    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": "Now write a short article based on that summary for a technical teammate.",
        },
    )
    client.post(
        "/chat",
        data={"session_id": sid, "message": "save it as brave-api-article.md"},
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/brave-api-article.md"
    assert "# Brave Search API Notes" in preview["write_file"]["content"]
    assert "# Investigation Summary" not in preview["write_file"]["content"]


def test_extract_text_draft_from_reply_keeps_heading_but_strips_preface() -> None:
    cleaned = extract_text_draft_from_reply(
        "I will refine the essay to a more formal academic tone and prepare it to be saved as `roman-empire-essay.md`.\n\n# The Roman Empire\n\nThe Roman Empire shaped the Mediterranean world through military expansion, administration, and law."
    )

    assert cleaned is not None
    assert cleaned.startswith("# The Roman Empire")
    assert "prepare it to be saved" not in cleaned.lower()


def test_extract_text_draft_from_reply_strips_explanation_preface_but_keeps_body() -> None:
    cleaned = extract_text_draft_from_reply(
        "Certainly! Here is a plain-English explanation of how the Python script functions:\n\n"
        "The script first fetches the URL, parses the HTML response, and then prints the page title to standard output."
    )

    assert cleaned is not None
    assert cleaned.startswith("The script first fetches the URL")
    assert "certainly!" not in cleaned.lower()
    assert "plain-english explanation" not in cleaned.lower()


def test_extract_text_draft_from_reply_keeps_legitimate_explanation_opening_paragraph() -> None:
    cleaned = extract_text_draft_from_reply(
        "The script first fetches the requested URL, parses the returned HTML, and prints the page title so the operator can confirm the page responded as expected."
    )

    assert cleaned is not None
    assert cleaned.startswith("The script first fetches the requested URL")


def test_direct_essay_request_creates_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = (turns, user_message)
        return {
            "answer": (
                "I've prepared an essay draft.\n\n"
                "# Great Pyramids of Giza\n\n"
                "The Great Pyramids of Giza were monumental royal tombs built during Egypt's Old Kingdom and remain "
                "among the most studied engineering achievements of the ancient world."
            ),
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": "Write me a 3 page essay about the Great Pyramids of Giza.",
        },
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert "great-pyramids-of-giza-essay.md" in preview["write_file"]["path"]
    assert "prepared an essay draft" not in preview["write_file"]["content"].lower()
    assert "Great Pyramids of Giza" in preview["write_file"]["content"]


def test_black_hole_essay_submit_saves_clean_body_only(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        lowered = user_message.lower()
        if "2 page essay" in lowered:
            return {
                "answer": (
                    "I can certainly help you expand that into a longer essay.\n\n"
                    "# Black Holes\n\n"
                    "Black holes are extreme objects formed when matter collapses into a compact region that traps light."
                ),
                "status": "ok:test",
            }
        return {"answer": "Black holes form when stars collapse.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "Tell me about black holes."})
    client.post(
        "/chat",
        data={"session_id": sid, "message": "Write a 2 page essay about that."},
    )
    submit_res = client.post("/chat", data={"session_id": sid, "message": "submit it"})

    jobs = list((queue / "inbox").glob("inbox-*.json"))
    assert submit_res.status_code == 200
    assert len(jobs) == 1
    payload = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert payload["write_file"]["content"].startswith("# Black Holes")
    assert "i can certainly help you expand" not in payload["write_file"]["content"].lower()


def test_direct_essay_submit_saves_clean_body_only(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = (turns, user_message)
        return {
            "answer": (
                "I can certainly help with that request.\n\n"
                "# Great Pyramids of Giza\n\n"
                "The Great Pyramids of Giza were monumental royal tombs built during Egypt's Old Kingdom."
            ),
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": "Write me a 3 page essay about the Great Pyramids of Giza.",
        },
    )
    submit_res = client.post("/chat", data={"session_id": sid, "message": "submit it"})

    jobs = list((queue / "inbox").glob("inbox-*.json"))
    assert submit_res.status_code == 200
    assert len(jobs) == 1
    payload = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert payload["write_file"]["content"].startswith("# Great Pyramids of Giza")
    assert "i can certainly help with that request" not in payload["write_file"]["content"].lower()


def test_writing_draft_hides_internal_control_block_but_keeps_authoritative_preview(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    leaked = (
        "# Draft Essay\n\n"
        "Black holes are among the most extreme objects in the universe.\n\n"
        "<voxera_control>\n"
        "interpreter: hidden\n"
        "action: update_preview\n"
        "payload:\n"
        "  write_file:\n"
        "    path: ~/VoxeraOS/notes/essay.md\n"
        "</voxera_control>"
    )

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = (turns, user_message)
        return {"answer": leaked, "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "Write me a 2 page essay about black holes."},
    )

    turns = vera_session_store.read_session_turns(queue, sid)
    preview = vera_session_store.read_session_preview(queue, sid)
    assert res.status_code == 200
    assert "<voxera_control>" not in res.text
    assert "action: update_preview" not in res.text
    assert "<voxera_control>" not in turns[-1]["text"]
    assert "action: update_preview" not in turns[-1]["text"]
    assert preview is not None
    assert "<voxera_control>" not in preview["write_file"]["content"]
    assert "Black holes are among the most extreme objects" in preview["write_file"]["content"]


def test_writing_draft_submit_after_control_block_strip_uses_clean_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = (turns, user_message)
        return {
            "answer": (
                "# Draft Essay\n\n"
                "The Great Pyramid of Giza was built as a royal tomb.\n\n"
                "<voxera_control>\n"
                "action: update_preview\n"
                "work_units:\n"
                "  - write_file\n"
                "</voxera_control>"
            ),
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={"session_id": sid, "message": "Write me a short essay about the Great Pyramid."},
    )
    res = client.post("/chat", data={"session_id": sid, "message": "submit it"})

    jobs = list((queue / "inbox").glob("inbox-*.json"))
    assert res.status_code == 200
    assert "I submitted the job to VoxeraOS" in res.text
    assert len(jobs) == 1
    payload = json.loads(jobs[0].read_text(encoding="utf-8"))
    content = payload["write_file"]["content"]
    assert "<voxera_control>" not in content
    assert "action: update_preview" not in content
    assert "royal tomb" in content


def test_code_draft_rendering_still_shows_fenced_code_with_control_sanitizer_present(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = (turns, user_message)
        return {
            "answer": "```python\nprint('still visible')\n```",
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "write me a python script that prints still visible"},
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert res.status_code == 200
    assert "<pre><code>" in res.text
    assert "still visible" in res.text
    assert preview is not None
    assert "print('still visible')" in preview["write_file"]["content"]


def test_entropy_explanation_then_save_that_to_note_creates_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        if "entropy" in user_message.lower():
            return {
                "answer": "Entropy is a measure of how spread out energy is, often described as disorder.",
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post("/chat", data={"session_id": sid, "message": "Explain entropy simply."})
    client.post(
        "/chat",
        data={"session_id": sid, "message": "save that to a note called entropy.txt"},
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/entropy.txt"
    assert (
        preview["write_file"]["content"]
        == "Entropy is a measure of how spread out energy is, often described as disorder."
    )


def test_weather_answer_then_save_that_to_note_creates_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        lowered = user_message.lower()
        if "weather" in lowered:
            return {
                "answer": (
                    "The weather in Seattle today is cool and rainy, around 52°F with steady light rain."
                ),
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={"session_id": sid, "message": "What's the weather in Seattle today?"},
    )
    client.post(
        "/chat",
        data={"session_id": sid, "message": "save that to a note called weather.md"},
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/weather.md"
    assert "seattle today" in preview["write_file"]["content"].lower()
    assert "light rain" in preview["write_file"]["content"].lower()


def test_weather_question_without_location_prompts_for_location(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fail_if_weather_lookup(_location_query: str):
        raise AssertionError("live weather lookup should not run when location is missing")

    monkeypatch.setattr(vera_service, "_lookup_live_weather", _fail_if_weather_lookup)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post("/chat", data={"session_id": sid, "message": "What's the weather like?"})

    assert res.status_code == 200
    assert "Which location should I check?" in res.text
    weather_context = vera_session_store.read_session_weather_context(queue, sid)
    assert weather_context is not None
    assert weather_context["awaiting_location"] is True


def test_weather_location_reply_returns_concise_live_answer_not_result_dump(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_lookup(location_query: str):
        assert location_query == "Calgary AB"
        return _sample_weather_snapshot(query=location_query)

    monkeypatch.setattr(vera_service, "_lookup_live_weather", _fake_lookup)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    first = client.post("/chat", data={"session_id": sid, "message": "What's the weather like?"})
    second = client.post("/chat", data={"session_id": sid, "message": "Calgary AB"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert "It’s currently 3°C in Calgary, Alberta" in second.text
    assert "Today’s high is 6°C and the low is -4°C." in second.text
    assert "Want the hourly, 7-day, or weekend outlook?" in second.text
    assert "Here are the top findings" not in second.text


def test_weather_lookup_failure_refuses_to_guess_live_conditions(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _failing_lookup(_location_query: str):
        raise RuntimeError("Weather service is temporarily unavailable.")

    monkeypatch.setattr(vera_service, "_lookup_live_weather", _failing_lookup)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "What's the weather in Calgary AB right now?"},
    )

    assert res.status_code == 200
    assert "I couldn’t complete a structured live weather lookup, so I won’t guess" in res.text
    assert "Weather service is temporarily unavailable." in res.text
    assert "It’s currently" not in res.text
    assert "Today’s high is" not in res.text


def test_weather_lookup_still_works_without_brave_web_investigation_config(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    monkeypatch.setattr(vera_service, "load_app_config", lambda: AppConfig(web_investigation=None))

    async def _fake_lookup(location_query: str):
        assert location_query == "Calgary AB"
        return _sample_weather_snapshot(query=location_query)

    monkeypatch.setattr(vera_service, "_lookup_live_weather", _fake_lookup)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "What's the weather in Calgary AB right now?"},
    )

    assert res.status_code == 200
    assert "It’s currently 3°C in Calgary, Alberta" in res.text
    assert "Want the hourly, 7-day, or weekend outlook?" in res.text


def test_weather_followup_hourly_routes_naturally(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_lookup(_location_query: str):
        return _sample_weather_snapshot()

    monkeypatch.setattr(vera_service, "_lookup_live_weather", _fake_lookup)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={"session_id": sid, "message": "What's the weather in Calgary AB?"},
    )
    res = client.post("/chat", data={"session_id": sid, "message": "hourly"})

    assert res.status_code == 200
    assert "Here’s the next 3 hours for Calgary, Alberta:" in res.text
    assert "Sat 12 PM: 3°C, cloudy skies." in res.text
    assert "I can also show the 7-day or weekend outlook." in res.text


def test_explicit_weather_investigation_still_uses_generic_result_list_flow(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    monkeypatch.setattr(
        vera_service,
        "load_app_config",
        lambda: _brave_enabled_config(max_results=3),
    )

    class _FakeBraveClient:
        def __init__(self, **kwargs):
            _ = kwargs

        async def search(self, *, query: str, count: int = 5):
            assert "weather in calgary" in query.lower()
            assert count == 3
            return [
                WebSearchResult(
                    title="Weather overview",
                    url="https://example.com/weather",
                    description="A source overview.",
                )
            ]

    async def _fail_if_weather_lookup(_location_query: str):
        raise AssertionError(
            "quick weather lookup should not run for explicit investigation requests"
        )

    monkeypatch.setattr(vera_service, "BraveSearchClient", _FakeBraveClient)
    monkeypatch.setattr(vera_service, "_lookup_live_weather", _fail_if_weather_lookup)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "Search the web for weather in Calgary AB"},
    )

    assert res.status_code == 200
    assert "Here are the top findings I found via read-only Brave web investigation" in res.text


def test_pending_weather_offer_acceptance_executes_lookup(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_lookup(location_query: str):
        assert location_query == "Calgary AB"
        return _sample_weather_snapshot(query=location_query)

    monkeypatch.setattr(vera_service, "_lookup_live_weather", _fake_lookup)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    vera_session_store.write_session_weather_context(
        queue,
        sid,
        {
            "pending_lookup": {"location_query": "Calgary AB"},
            "followup_active": False,
        },
    )

    res = client.post("/chat", data={"session_id": sid, "message": "go ahead"})

    assert res.status_code == 200
    assert "It’s currently 3°C in Calgary, Alberta" in res.text


def test_concise_information_answer_then_save_it_creates_note_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        lowered = user_message.lower()
        if "capital of france" in lowered:
            return {"answer": "The capital of France is Paris.", "status": "ok:test"}
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post("/chat", data={"session_id": sid, "message": "What is the capital of France?"})
    client.post("/chat", data={"session_id": sid, "message": "save it"})

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["goal"].startswith("write a file called note-")
    assert preview["write_file"]["path"].startswith("~/VoxeraOS/notes/note-")
    assert preview["write_file"]["content"] == "The capital of France is Paris."


def test_previous_explanation_survives_trivial_thanks_turn_for_save_reference(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        lowered = user_message.lower()
        if "photosynthesis" in lowered:
            return {
                "answer": (
                    "Sure! Here is a plain-English explanation of photosynthesis: "
                    "Photosynthesis lets plants use sunlight, water, and carbon dioxide to make sugar."
                ),
                "status": "ok:test",
            }
        return {
            "answer": "You're very welcome — happy to help. If you'd like, I can save that explanation too.",
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "Explain photosynthesis simply."})
    client.post("/chat", data={"session_id": sid, "message": "thanks"})
    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": "put your previous explanation in a note called photosynthesis.txt",
        },
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/photosynthesis.txt"
    assert "sunlight" in preview["write_file"]["content"].lower()
    assert "very welcome" not in preview["write_file"]["content"].lower()


def test_previous_explanation_without_courtesy_turn_uses_latest_explanation(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        lowered = user_message.lower()
        if "photosynthesis" in lowered:
            return {
                "answer": (
                    "Photosynthesis is the process plants use to convert sunlight into stored chemical energy."
                ),
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post("/chat", data={"session_id": sid, "message": "Explain photosynthesis simply."})
    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": "put your previous explanation in a note called photosynthesis.txt",
        },
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/photosynthesis.txt"
    assert "stored chemical energy" in preview["write_file"]["content"].lower()


def test_code_explanation_then_save_explanation_creates_text_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        lowered = user_message.lower()
        if "write me a python script" in lowered:
            return {
                "answer": "```python\nprint('hello world')\n```",
                "status": "ok:test",
            }
        if "explain how this script works in plain english" in lowered:
            return {
                "answer": (
                    "Certainly! Here is a plain-English explanation of how the Python script functions:\n\n"
                    "The script runs one statement. It calls Python's print function, which sends "
                    "the text hello world to standard output."
                ),
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    client.post(
        "/chat",
        data={"session_id": sid, "message": "Write me a python script that prints hello world."},
    )
    client.post(
        "/chat",
        data={"session_id": sid, "message": "Explain how this script works in plain English."},
    )
    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": "save that explanation to a note called script-explained.txt",
        },
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/script-explained.txt"
    assert "print function" in preview["write_file"]["content"].lower()
    assert "preview pane" not in preview["write_file"]["content"].lower()

    submit_res = client.post("/chat", data={"session_id": sid, "message": "submit it"})

    jobs = list((queue / "inbox").glob("inbox-*.json"))
    assert submit_res.status_code == 200
    assert len(jobs) == 1
    payload = json.loads(jobs[0].read_text(encoding="utf-8"))
    assert payload["write_file"]["path"] == "~/VoxeraOS/notes/script-explained.txt"
    assert payload["write_file"]["content"].startswith("The script runs one statement.")
    assert "preview pane" not in payload["write_file"]["content"].lower()
    assert "i've drafted a clear" not in payload["write_file"]["content"].lower()


def test_ordinary_compare_prompt_stays_conversational_not_investigation(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        return {
            "answer": (
                "The Great Pyramid is an ancient Egyptian tomb, while the Colosseum is a Roman amphitheater. "
                "Both are monumental stone structures, but they served different cultures and purposes."
            ),
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""
    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "Compare the Great Pyramid and the Colosseum briefly."},
    )

    assert res.status_code == 200
    assert "Roman amphitheater" in res.text
    assert vera_session_store.read_session_investigation(queue, sid) is None
    assert vera_session_store.read_session_preview(queue, sid) is None


def test_two_recent_assistant_answers_save_that_prefers_latest(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        lowered = user_message.lower()
        if "gravity" in lowered:
            return {"answer": "Gravity is the attraction between masses.", "status": "ok:test"}
        if "dark matter" in lowered:
            return {
                "answer": "Dark matter is inferred from gravity effects and does not emit light.",
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post("/chat", data={"session_id": sid, "message": "Explain gravity simply."})
    client.post("/chat", data={"session_id": sid, "message": "Explain dark matter simply."})
    client.post(
        "/chat",
        data={"session_id": sid, "message": "save that to a note called ambiguous.txt"},
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/ambiguous.txt"
    assert (
        preview["write_file"]["content"]
        == "Dark matter is inferred from gravity effects and does not emit light."
    )


def test_plural_save_reference_fails_closed(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        lowered = user_message.lower()
        if "gravity" in lowered:
            return {"answer": "Gravity is the attraction between masses.", "status": "ok:test"}
        if "dark matter" in lowered:
            return {
                "answer": "Dark matter is inferred from gravity effects and does not emit light.",
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post("/chat", data={"session_id": sid, "message": "Explain gravity simply."})
    client.post("/chat", data={"session_id": sid, "message": "Explain dark matter simply."})
    client.post(
        "/chat",
        data={"session_id": sid, "message": "save both to a note called ambiguous.txt"},
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    turns = vera_session_store.read_session_turns(queue, sid)
    assert preview is None
    assert "couldn’t find a recent response to save" in turns[-1]["text"].lower()


def test_recent_assistant_reference_failure_is_clear_when_no_content(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = (turns, user_message)
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": "save your previous answer to a note called missing.txt",
        },
    )

    assert vera_session_store.read_session_preview(queue, sid) is None
    turns = vera_session_store.read_session_turns(queue, sid)
    assert turns[-1]["role"] == "assistant"
    assert "couldn’t find a recent response to save" in turns[-1]["text"].lower()
    assert res.status_code == 200


def test_active_preview_formal_refinement_updates_content(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        if "make it more formal" in user_message.lower():
            return {
                "answer": "Good afternoon.\n\nI hope you are doing well.",
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

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

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/notes.txt"
    assert preview["write_file"]["content"] == "Good afternoon.\n\nI hope you are doing well."


def test_active_preview_formal_refinement_and_save_as_updates_path_and_content(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        if "make it more formal and save it as polished.txt" in user_message.lower():
            return {
                "answer": "Good afternoon.\n\nI hope you are doing well.",
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

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
        data={
            "session_id": sid,
            "message": "make it more formal and save it as polished.txt",
        },
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/polished.txt"
    assert preview["write_file"]["content"] == "Good afternoon.\n\nI hope you are doing well."


def test_active_preview_shorter_refinement_uses_reply_text_over_builder_heuristic(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        if "make it shorter" in user_message.lower():
            return {
                "answer": "This shorter version keeps the key point while remaining complete.",
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    vera_session_store.write_session_preview(
        queue,
        sid,
        {
            "goal": "write a file called notes.txt with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/notes.txt",
                "content": "This is a longer note with extra details for shortening.",
                "mode": "overwrite",
            },
        },
    )
    client.post(
        "/chat",
        data={"session_id": sid, "message": "make it shorter"},
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"].endswith(".txt")
    assert (
        preview["write_file"]["content"]
        == "This shorter version keeps the key point while remaining complete."
    )


def test_active_note_refinement_reuses_existing_preview_when_builder_returns_none(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        if "more casual" in user_message.lower():
            return {
                "answer": "Hey there — here is a more casual version that still keeps the same idea.",
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    async def _fake_builder_update(**kwargs):
        _ = kwargs
        return None

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    monkeypatch.setattr(
        vera_app_module,
        "generate_preview_builder_update",
        _fake_builder_update,
    )

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    vera_session_store.write_session_preview(
        queue,
        sid,
        {
            "goal": "write a file called notes.txt with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/notes.txt",
                "content": "This is the original note content.",
                "mode": "overwrite",
            },
        },
    )

    client.post(
        "/chat",
        data={"session_id": sid, "message": "make it more casual"},
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/notes.txt"
    assert (
        preview["write_file"]["content"]
        == "Hey there — here is a more casual version that still keeps the same idea."
    )


def test_code_preview_refinement_prompt_does_not_overwrite_code_content(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    vera_session_store.write_session_preview(
        queue,
        sid,
        {
            "goal": "draft a python script as demo.py",
            "write_file": {
                "path": "~/VoxeraOS/notes/demo.py",
                "content": 'print("hello")',
                "mode": "overwrite",
            },
        },
    )

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = (turns, user_message)
        return {
            "answer": "Formal rewrite:\n\nThis script prints hello to standard output.",
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client.post(
        "/chat",
        data={"session_id": sid, "message": "make it more formal"},
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/demo.py"
    assert preview["write_file"]["content"] == 'print("hello")'


def test_non_document_preview_refinement_does_not_write_prose_back(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    vera_session_store.write_session_preview(
        queue,
        sid,
        {
            "goal": "write a file called report.csv with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/report.csv",
                "content": "name,value\nalpha,1",
                "mode": "overwrite",
            },
        },
    )

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        if "more formal" in user_message.lower():
            return {
                "answer": "This comma-separated report lists alpha with a value of one.",
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    async def _fake_builder_update(**kwargs):
        _ = kwargs
        return None

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    monkeypatch.setattr(
        vera_app_module,
        "generate_preview_builder_update",
        _fake_builder_update,
    )

    client.post(
        "/chat",
        data={"session_id": sid, "message": "make it more formal"},
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/report.csv"
    assert preview["write_file"]["content"] == "name,value\nalpha,1"


def test_code_preview_plain_english_save_as_updates_to_text_preview(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    vera_session_store.write_session_preview(
        queue,
        sid,
        {
            "goal": "draft a json config as config.json",
            "write_file": {
                "path": "~/VoxeraOS/notes/config.json",
                "content": '{"debug": true, "port": 8080}',
                "mode": "overwrite",
            },
        },
    )

    async def _fake_reply(*, turns, user_message, **_kw):
        _ = turns
        if "plain english" in user_message.lower():
            return {
                "answer": (
                    "This configuration turns on debug mode and tells the app to listen on port "
                    "8080 so developers can inspect behavior during local testing."
                ),
                "status": "ok:test",
            }
        return {"answer": "ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": "Explain this in plain English and save as notes.md",
        },
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/notes.md"
    assert (
        preview["write_file"]["content"]
        == "This configuration turns on debug mode and tells the app to listen on port 8080 so developers can inspect behavior during local testing."
    )


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

    preview = vera_session_store.read_session_preview(queue, sid)
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

    preview = vera_session_store.read_session_preview(queue, sid)
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

    preview = vera_session_store.read_session_preview(queue, sid)
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

    preview = vera_session_store.read_session_preview(queue, sid)
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

    latest = vera_session_store.read_session_preview(queue, sid)
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
    assert vera_session_store.read_session_preview(queue, sid) is not None

    # Informational query WITH active preview → should run enrichment and store it
    client.post("/chat", data={"session_id": sid, "message": "find the latest news"})

    enrichment = vera_session_store.read_session_enrichment(queue, sid)
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
    before = vera_session_store.read_session_preview(queue, sid)
    assert before is not None

    # Info query with active preview → stores enrichment
    client.post("/chat", data={"session_id": sid, "message": "find the latest news"})

    # Pronoun follow-up → enrichment summary resolves into file content
    client.post("/chat", data={"session_id": sid, "message": "put that into the file"})

    after = vera_session_store.read_session_preview(queue, sid)
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
    assert vera_session_store.read_session_enrichment(queue, sid) is None


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

    assert vera_session_store.read_session_preview(queue, sid) is None
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

    completions = vera_session_store.read_linked_job_completions(queue, sid)
    assert len(completions) == 1
    completion = completions[0]
    assert completion["lifecycle_state"] == "done"
    assert completion["terminal_outcome"] == "succeeded"
    assert completion["request_kind"] == "goal"
    assert completion["surfacing_policy"] == "read_only_success"


def test_linked_read_only_success_auto_surfaces_once_per_completion(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
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

    completions = vera_session_store.read_linked_job_completions(queue, sid)
    assert len(completions) == 1
    assert completions[0]["surfaced_in_chat"] is True
    assert isinstance(completions[0]["surfaced_at_ms"], int)

    second = client.post("/chat", data={"session_id": sid, "message": "hello again"})
    assert second.status_code == 200
    turns = vera_session_store.read_session_turns(queue, sid)
    surfaced_messages = [
        turn["text"]
        for turn in turns
        if turn["role"] == "assistant"
        and turn["text"].startswith("Your linked ")
        and "completed successfully." in turn["text"]
    ]
    assert len(surfaced_messages) == 1


def test_auto_surface_prefers_latest_submitted_linked_job_completion(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    sid = "vera-linked-latest-priority"
    vera_session_store.append_session_turn(queue, sid, role="user", text="seed")

    # Older linked completion is intentionally ingestible first.
    vera_session_store.register_session_linked_job(queue, sid, job_ref="inbox-old.json")
    _write_job_artifacts(
        queue,
        "inbox-old.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
            "latest_summary": "Old completion path: ~/VoxeraOS/notes/old-note.txt",
            "normalized_outcome_class": "succeeded",
        },
        state={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
        },
    )

    # Newly submitted/linked job should win completion surfacing priority.
    vera_session_store.register_session_linked_job(queue, sid, job_ref="inbox-new.json")
    vera_session_store.write_session_handoff_state(
        queue,
        sid,
        attempted=True,
        queue_path=str(queue),
        status="submitted",
        job_id="new",
    )
    _write_job_artifacts(
        queue,
        "inbox-new.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
            "latest_summary": "New completion path: ~/VoxeraOS/notes/earthcore.txt",
            "normalized_outcome_class": "succeeded",
        },
        state={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
        },
    )

    vera_service.ingest_linked_job_completions(queue, sid)
    surfaced = vera_service.maybe_auto_surface_linked_completion(queue, sid)

    assert surfaced is not None
    assert "earthcore.txt" in surfaced
    assert "old-note.txt" not in surfaced


def test_submit_turn_suppresses_stale_linked_completion_autosurface(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    vera_session_store.write_session_preview(
        queue,
        sid,
        {
            "goal": "write a file called earthcore.txt with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/earthcore.txt",
                "content": "earth core summary",
                "mode": "overwrite",
            },
        },
    )
    vera_session_store.register_session_linked_job(queue, sid, job_ref="inbox-old.json")
    _write_job_artifacts(
        queue,
        "inbox-old.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
            "latest_summary": "Old completion path: ~/VoxeraOS/notes/old-note.txt",
            "normalized_outcome_class": "succeeded",
        },
        state={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
        },
    )

    submit = client.post("/chat", data={"session_id": sid, "message": "submit it"})
    assert submit.status_code == 200
    assert "I submitted the job to VoxeraOS" in submit.text
    assert "old-note.txt" not in submit.text

    completions = vera_session_store.read_linked_job_completions(queue, sid)
    old_completion = next(
        item for item in completions if str(item.get("job_ref") or "") == "inbox-old.json"
    )
    assert old_completion["surfaced_in_chat"] is False


def test_auto_surface_waits_for_latest_submitted_job_instead_of_older_completion(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    sid = "vera-linked-latest-only"
    vera_session_store.append_session_turn(queue, sid, role="user", text="seed")

    vera_session_store.register_session_linked_job(queue, sid, job_ref="inbox-old.json")
    _write_job_artifacts(
        queue,
        "inbox-old.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
            "latest_summary": "Old completion path: ~/VoxeraOS/notes/old-note.txt",
            "normalized_outcome_class": "succeeded",
        },
        state={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
        },
    )

    vera_session_store.register_session_linked_job(queue, sid, job_ref="inbox-new.json")
    vera_session_store.write_session_handoff_state(
        queue,
        sid,
        attempted=True,
        queue_path=str(queue),
        status="submitted",
        job_id="new",
    )

    vera_service.ingest_linked_job_completions(queue, sid)
    assert vera_service.maybe_auto_surface_linked_completion(queue, sid) is None

    _write_job_artifacts(
        queue,
        "inbox-new.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
            "latest_summary": "New completion path: ~/VoxeraOS/notes/new-note.txt",
            "normalized_outcome_class": "succeeded",
        },
        state={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
        },
    )

    vera_service.ingest_linked_job_completions(queue, sid)
    surfaced = vera_service.maybe_auto_surface_linked_completion(queue, sid)
    assert surfaced is not None
    assert "new-note.txt" in surfaced
    assert "old-note.txt" not in surfaced


def test_linked_approval_blocked_auto_surfaces_once(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
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

    turns = vera_session_store.read_session_turns(queue, sid)
    approval_messages = [
        turn["text"]
        for turn in turns
        if turn["role"] == "assistant"
        and turn["text"].startswith("Your linked request is paused pending approval in VoxeraOS.")
    ]
    assert len(approval_messages) == 1
    assert "pending approval" in approval_messages[0]

    completions = vera_session_store.read_linked_job_completions(queue, sid)
    assert len(completions) == 1
    assert completions[0]["surfacing_policy"] == "approval_blocked"
    assert completions[0]["surfaced_in_chat"] is True

    second = client.post("/chat", data={"session_id": sid, "message": "hello again"})
    assert second.status_code == 200
    turns = vera_session_store.read_session_turns(queue, sid)
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

    async def _fake_reply(*, turns, user_message, **_kw):
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

    turns = vera_session_store.read_session_turns(queue, sid)
    failed_messages = [
        turn["text"]
        for turn in turns
        if turn["role"] == "assistant" and turn["text"].startswith("Your linked goal job failed.")
    ]
    assert len(failed_messages) == 1
    assert "Failure summary: Path not found: ~/VoxeraOS/notes/report.txt" in failed_messages[0]

    completions = vera_session_store.read_linked_job_completions(queue, sid)
    assert len(completions) == 1
    assert completions[0]["surfacing_policy"] == "failed"
    assert completions[0]["surfaced_in_chat"] is True

    second = client.post("/chat", data={"session_id": sid, "message": "status again?"})
    assert second.status_code == 200
    turns = vera_session_store.read_session_turns(queue, sid)
    surfaced_messages = [
        turn["text"]
        for turn in turns
        if turn["role"] == "assistant" and turn["text"].startswith("Your linked goal job failed.")
    ]
    assert len(surfaced_messages) == 1


def test_linked_mutating_success_auto_surfaces_once(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {"answer": f"Echo: {user_message}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    sid = "vera-test-mutate"
    vera_session_store.append_session_turn(queue, sid, role="user", text="seed")
    vera_session_store.register_session_linked_job(queue, sid, job_ref="inbox-a.json")

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

    turns = vera_session_store.read_session_turns(queue, sid)
    surfaced_first = [
        turn["text"]
        for turn in turns
        if turn["role"] == "assistant"
        and turn["text"].startswith("Your linked write file job completed successfully.")
    ]
    assert len(surfaced_first) == 1
    assert "Destination created at ~/VoxeraOS/notes/testdir." in surfaced_first[0]

    completions = vera_session_store.read_linked_job_completions(queue, sid)
    assert len(completions) == 1
    assert completions[0]["surfacing_policy"] == "mutating_success"
    assert completions[0]["surfaced_in_chat"] is True

    second = client.post("/chat", data={"session_id": sid, "message": "any update now?"})
    assert second.status_code == 200
    turns = vera_session_store.read_session_turns(queue, sid)
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

    async def _fake_reply(*, turns, user_message, **_kw):
        return {"answer": f"Echo: {user_message}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    sid = "vera-test-intermediate"
    vera_session_store.append_session_turn(queue, sid, role="user", text="seed")
    vera_session_store.register_session_linked_job(queue, sid, job_ref="inbox-parent.json")

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

    completions = vera_session_store.read_linked_job_completions(queue, sid)
    assert len(completions) == 1
    assert completions[0]["surfacing_policy"] == "mutating_success"
    assert completions[0]["surfaced_in_chat"] is False


def test_linked_terminal_completion_live_delivery_posts_immediately(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    sid = "vera-live-delivery"
    vera_session_store.append_session_turn(queue, sid, role="user", text="seed")
    vera_session_store.register_session_linked_job(queue, sid, job_ref="inbox-live.json")

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

    turns = vera_session_store.read_session_turns(queue, sid)
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
    vera_session_store.append_session_turn(queue, sid, role="user", text="seed")
    vera_session_store.register_session_linked_job(queue, sid, job_ref="inbox-live-1.json")
    vera_session_store.register_session_linked_job(queue, sid, job_ref="inbox-live-2.json")

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

    turns = vera_session_store.read_session_turns(queue, sid)
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

    async def _fake_reply(*, turns, user_message, **_kw):
        return {"answer": f"Echo: {user_message}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

    sid = "vera-live-no-duplicate"
    vera_session_store.append_session_turn(queue, sid, role="user", text="seed")
    vera_session_store.register_session_linked_job(queue, sid, job_ref="inbox-live-once.json")

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

    turns = vera_session_store.read_session_turns(queue, sid)
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
    vera_session_store.append_session_turn(queue, sid, role="user", text="seed")
    vera_session_store.register_session_linked_job(queue, sid, job_ref="inbox-fallback.json")

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

    original_append = vera_session_store.append_session_turn
    monkeypatch.setattr(vera_session_store, "append_session_turn", _raise_append)
    delivered = vera_service.maybe_deliver_linked_completion_live_for_job(
        queue, job_ref="inbox-fallback.json"
    )
    assert delivered == 0

    payload = json.loads((queue / "artifacts" / "vera_sessions" / f"{sid}.json").read_text())
    outbox = payload["linked_queue_jobs"].get("notification_outbox")
    assert isinstance(outbox, list) and len(outbox) == 1
    assert outbox[0]["delivery_status"] == "unavailable"
    assert outbox[0]["fallback_pending"] is True

    monkeypatch.setattr(vera_session_store, "append_session_turn", original_append)
    msg = vera_service.maybe_auto_surface_linked_completion(queue, sid)
    assert msg is not None
    assert msg.startswith("Your linked ")
    assert "completed successfully." in msg
    assert vera_service.maybe_auto_surface_linked_completion(queue, sid) is None


def test_live_delivered_linked_completion_not_reposted_by_fallback(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    sid = "vera-live-dedupe"
    vera_session_store.append_session_turn(queue, sid, role="user", text="seed")
    vera_session_store.register_session_linked_job(queue, sid, job_ref="inbox-dedupe.json")

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
    vera_session_store.append_session_turn(queue, sid, role="user", text="seed")
    vera_session_store.register_session_linked_job(queue, sid, job_ref="inbox-parent-live.json")

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

    turns = vera_session_store.read_session_turns(queue, sid)
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

    assert vera_session_store.read_linked_job_completions(queue, sid) == []


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

    vera_session_store.append_session_turn(queue, sid, role="assistant", text="live completion")

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

    for idx in range(vera_session_store.MAX_SESSION_TURNS):
        role = "user" if idx % 2 == 0 else "assistant"
        vera_session_store.append_session_turn(queue, sid, role=role, text=f"seed-{idx}")

    baseline = client.get(
        "/chat/updates",
        params={"session_id": sid, "since_count": vera_session_store.MAX_SESSION_TURNS},
    )
    assert baseline.status_code == 200
    baseline_payload = baseline.json()
    assert baseline_payload["changed"] is False
    updated_at_ms = int(baseline_payload["updated_at_ms"])

    vera_session_store.append_session_turn(
        queue, sid, role="assistant", text="live completion second wave"
    )

    refreshed = client.get(
        "/chat/updates",
        params={
            "session_id": sid,
            "since_count": vera_session_store.MAX_SESSION_TURNS,
            "since_updated_at_ms": updated_at_ms,
        },
    )
    assert refreshed.status_code == 200
    refreshed_payload = refreshed.json()
    assert refreshed_payload["changed"] is True
    assert refreshed_payload["turn_count"] == vera_session_store.MAX_SESSION_TURNS
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


def test_diagnostics_broad_health_routes_to_system_diagnostics_preview():
    preview = maybe_draft_job_payload("inspect system health")

    assert preview is not None
    assert preview["mission_id"] == "system_diagnostics"
    assert "steps" not in preview
    normalized = normalize_preview_payload(preview)
    assert normalized["mission_id"] == "system_diagnostics"


def test_diagnostics_disk_usage_routes_to_system_diagnostics_preview():
    preview = maybe_draft_job_payload("check disk usage")

    assert preview is not None
    assert preview["mission_id"] == "system_diagnostics"


def test_diagnostics_service_status_routes_to_bounded_skill_preview():
    preview = maybe_draft_job_payload("check status of voxera-vera.service")

    assert preview is not None
    assert preview["steps"][0]["skill_id"] == "system.service_status"
    assert preview["steps"][0]["args"] == {"service": "voxera-vera.service"}


def test_diagnostics_recent_logs_routes_to_bounded_skill_preview():
    preview = maybe_draft_job_payload("show recent logs for voxera-daemon.service")

    assert preview is not None
    assert preview["steps"][0]["skill_id"] == "system.recent_service_logs"
    assert preview["steps"][0]["args"] == {
        "service": "voxera-daemon.service",
        "lines": 50,
        "since_minutes": 15,
    }


def test_diagnostics_invalid_service_target_fails_closed_in_web_chat(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {"answer": f"Echo: {user_message}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "show recent logs for ../../etc/passwd"},
    )

    assert res.status_code == 200
    assert "unsafe or invalid" in res.text
    assert vera_session_store.read_session_preview(queue, sid) is None


def test_unrelated_write_preview_flow_remains_unchanged():
    preview = maybe_draft_job_payload('write a file called hello.txt with content "hello"')

    assert preview is not None
    normalized = normalize_preview_payload(preview)
    assert normalized["write_file"]["path"] == "~/VoxeraOS/notes/hello.txt"


def test_service_status_request_prefers_diagnostics_preview_over_review(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {"answer": f"Echo: {user_message}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "check status of voxera-vera.service"},
    )

    assert res.status_code == 200
    assert "No job could be resolved" not in res.text
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["steps"][0]["skill_id"] == "system.service_status"


def test_job_review_query_fails_closed_without_job_context(tmp_path, monkeypatch):
    """Review hint on a fresh session (no job context) fails closed honestly."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "check the last job status"},
    )

    assert res.status_code == 200
    # Without job context, fails closed with an honest message.
    assert "No job could be resolved" in res.text


def test_what_was_the_output_surfaces_actual_written_content(tmp_path, monkeypatch):
    """End-to-end regression: 'What was the output?' for a completed file-write
    job returns the actual written content, not just operator metadata.

    Live repro: user saves a joke to a note, job completes, 'What was the output?'
    must show the real joke text from canonical evidence."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    joke = "Why did the queue cross the road? To get to done."
    job_id = "job-joke-output.json"
    _write_job_artifacts(
        queue,
        job_id,
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "approval_status": "none",
            "step_results": [
                {
                    "step_index": 1,
                    "status": "succeeded",
                    "skill_id": "files.write_text",
                    "summary": "Wrote text to ~/VoxeraOS/notes/note-joke.txt",
                    "machine_payload": {
                        "path": "~/VoxeraOS/notes/note-joke.txt",
                        "bytes": len(joke),
                        "content": joke,
                    },
                }
            ],
        },
    )
    vera_session_store.write_session_handoff_state(
        queue,
        "sid-output",
        attempted=True,
        queue_path=str(queue),
        status="submitted",
        job_id=job_id,
    )
    client = TestClient(vera_app_module.app)
    client.cookies.set("vera_session_id", "sid-output")
    res = client.post("/chat", data={"session_id": "sid-output", "message": "What was the output?"})

    assert res.status_code == 200
    # Must contain the actual written joke text
    assert joke in res.text
    # Must NOT contain an alternate/hallucinated joke
    assert "invisible man" not in res.text
    # Must identify the file path
    assert "note-joke.txt" in res.text


def test_diagnostics_system_mission_completion_surfaces_useful_values(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {"answer": f"Echo: {user_message}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post("/chat", data={"session_id": sid, "message": "inspect system health"})
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
                "latest_summary": "Listed 332 running processes",
                "normalized_outcome_class": "succeeded",
                "step_results": [
                    {
                        "step_index": 1,
                        "skill_id": "system.host_info",
                        "status": "succeeded",
                        "machine_payload": {"hostname": "voxera-box", "uptime_seconds": 7200},
                    },
                    {
                        "step_index": 2,
                        "skill_id": "system.memory_usage",
                        "status": "succeeded",
                        "machine_payload": {
                            "used_gib": 3.2,
                            "total_gib": 15.6,
                            "used_percent": 20.5,
                        },
                    },
                    {
                        "step_index": 3,
                        "skill_id": "system.load_snapshot",
                        "status": "succeeded",
                        "machine_payload": {"load_1m": 0.4, "load_5m": 0.5, "load_15m": 0.6},
                    },
                    {
                        "step_index": 4,
                        "skill_id": "system.disk_usage",
                        "status": "succeeded",
                        "machine_payload": {"used_percent": 52.1, "free_gb": 120.0},
                    },
                    {
                        "step_index": 5,
                        "skill_id": "system.process_list",
                        "status": "succeeded",
                        "machine_payload": {"count": 332},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    response = client.post("/chat", data={"session_id": sid, "message": "thanks"})
    assert response.status_code == 200
    assert "Diagnostics snapshot:" in response.text
    assert "host=voxera-box" in response.text
    assert "memory=3.2/15.6GiB" in response.text


def test_diagnostics_service_status_completion_surfaces_state(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {"answer": f"Echo: {user_message}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post("/chat", data={"session_id": sid, "message": "check status of voxera-vera.service"})
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
                "latest_summary": "Service voxera-vera.service: active/running",
                "normalized_outcome_class": "succeeded",
                "step_results": [
                    {
                        "step_index": 1,
                        "skill_id": "system.service_status",
                        "status": "succeeded",
                        "machine_payload": {
                            "service": "voxera-vera.service",
                            "ActiveState": "active",
                            "SubState": "running",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    response = client.post("/chat", data={"session_id": sid, "message": "thanks"})
    assert response.status_code == 200
    assert "Service voxera-vera.service is active/running." in response.text


def test_diagnostics_recent_logs_completion_surfaces_line_count(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {"answer": f"Echo: {user_message}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat", data={"session_id": sid, "message": "show recent logs for voxera-daemon.service"}
    )
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
                "latest_summary": "Collected 18 recent logs for voxera-daemon.service",
                "normalized_outcome_class": "succeeded",
                "step_results": [
                    {
                        "step_index": 1,
                        "skill_id": "system.recent_service_logs",
                        "status": "succeeded",
                        "machine_payload": {
                            "service": "voxera-daemon.service",
                            "line_count": 18,
                            "since_minutes": 15,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    response = client.post("/chat", data={"session_id": sid, "message": "thanks"})
    assert response.status_code == 200
    assert "Recent logs for voxera-daemon.service: 18 lines in the last 15m." in response.text


def test_diagnostics_refusal_does_not_override_non_service_job_status_queries():
    assert diagnostics_request_refusal("what's the status of job-await-1?") is None
    assert diagnostics_request_refusal("status of my job") is None
    assert diagnostics_request_refusal("what is the status of the last job") is None
    assert (
        diagnostics_request_refusal("what's the status of inbox-1773082365485-1336541d.json?")
        is None
    )


def test_diagnostics_refusal_still_blocks_path_like_service_targets():
    refusal = diagnostics_request_refusal("show recent logs for ../../etc/passwd")
    assert isinstance(refusal, str)
    assert "unsafe or invalid" in refusal


def test_job_review_query_status_of_my_job_falls_through_without_context(tmp_path, monkeypatch):
    """'status of my job' on a fresh session → falls through to LLM."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {"answer": f"Echo: {user_message}", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "status of my job"},
    )

    assert res.status_code == 200
    assert "No job could be resolved" not in res.text


def test_job_review_query_status_of_last_job_fails_closed_without_context(tmp_path, monkeypatch):
    """'what is the status of the last job' on a fresh session → fails closed honestly."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "what is the status of the last job"},
    )

    assert res.status_code == 200
    assert "No job could be resolved" in res.text


# ---------------------------------------------------------------------------
# Code / script draft lane tests
# ---------------------------------------------------------------------------

_PYTHON_CODE = "import os\n\ndef main():\n    print('hello')\n\nmain()"
_BASH_CODE = "#!/bin/bash\necho 'hello'\n"
_YAML_CODE = "version: '3'\nservices:\n  web:\n    image: nginx\n"
_JSON_CODE = '{\n  "key": "value"\n}\n'


def test_python_script_request_creates_authoritative_preview(tmp_path, monkeypatch):
    """Python script request → real write_file preview with LLM-generated code."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {
            "answer": f"Here's a Python script:\n\n```python\n{_PYTHON_CODE}\n```",
            "status": "ok:code_draft",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post("/chat", data={"session_id": sid, "message": "write me a python script"})

    assert res.status_code == 200
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None, "Expected an authoritative preview to be created"
    assert "write_file" in preview
    assert preview["write_file"]["path"].endswith(".py")
    assert preview["write_file"]["path"].startswith("~/VoxeraOS/notes/")
    assert preview["write_file"]["content"] == _PYTHON_CODE
    assert preview["write_file"]["mode"] == "overwrite"
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))


def test_bash_script_request_creates_authoritative_preview(tmp_path, monkeypatch):
    """Bash script request → real write_file preview with LLM-generated code."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {
            "answer": f"Here's a bash script:\n\n```bash\n{_BASH_CODE}```",
            "status": "ok:code_draft",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post("/chat", data={"session_id": sid, "message": "make a bash script for backup"})

    assert res.status_code == 200
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"].endswith(".sh")
    assert "bash" in preview["write_file"]["content"] or "echo" in preview["write_file"]["content"]
    assert not (queue / "inbox").exists() or not list((queue / "inbox").glob("*.json"))


def test_yaml_config_request_creates_authoritative_preview(tmp_path, monkeypatch):
    """YAML config request → real write_file preview."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {
            "answer": f"Here's a YAML config:\n\n```yaml\n{_YAML_CODE}```",
            "status": "ok:code_draft",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat", data={"session_id": sid, "message": "create a yaml config for my service"}
    )

    assert res.status_code == 200
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"].endswith(".yaml")
    assert (
        "nginx" in preview["write_file"]["content"] or "version" in preview["write_file"]["content"]
    )


def test_json_config_request_creates_authoritative_preview(tmp_path, monkeypatch):
    """JSON config request → real write_file preview."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {
            "answer": f"Here's your JSON config:\n\n```json\n{_JSON_CODE}```",
            "status": "ok:code_draft",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post("/chat", data={"session_id": sid, "message": "draft a JSON config file"})

    assert res.status_code == 200
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"].endswith(".json")


def test_code_is_rendered_in_proper_fenced_code_block_in_reply(tmp_path, monkeypatch):
    """Code draft reply is shown to the user with fenced code blocks, not suppressed."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {
            "answer": f"Here's the script:\n\n```python\n{_PYTHON_CODE}\n```\n\nThis script is ready.",
            "status": "ok:code_draft",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post("/chat", data={"session_id": sid, "message": "write me a python script"})

    assert res.status_code == 200
    # The fenced code block must appear in the rendered response (as <pre><code>)
    assert "<pre><code>" in res.text
    assert _PYTHON_CODE[:30] in res.text


def test_code_reply_not_suppressed_by_voxera_preview_flow_logic(tmp_path, monkeypatch):
    """Code draft replies are not replaced by the generic 'Understood' message."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {
            "answer": "Here's a Python script:\n\n```python\nprint('hello')\n```\n\nEnjoy!",
            "status": "ok:code_draft",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post("/chat", data={"session_id": sid, "message": "write me a python script"})

    assert res.status_code == 200
    # The actual code reply should appear (rendered as <pre><code>), not suppressed
    assert "<pre><code>" in res.text
    # Generic suppression messages should NOT appear instead
    assert "Nothing has been submitted or executed yet. I can send it whenever" not in res.text


def test_follow_up_save_it_submits_code_draft_preview(tmp_path, monkeypatch):
    """'save it' after a code draft creates a governed handoff because preview exists."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {
            "answer": f"Here's the script:\n\n```python\n{_PYTHON_CODE}\n```",
            "status": "ok:code_draft",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    # First turn: request the script → preview is created
    client.post("/chat", data={"session_id": sid, "message": "write me a python script"})
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None, "Preview must exist after code draft request"
    assert preview["write_file"]["content"] == _PYTHON_CODE

    # Second turn: save it → should submit the preview, not fail with 'no preview'
    res = client.post("/chat", data={"session_id": sid, "message": "save it"})
    assert res.status_code == 200
    # Submission must have succeeded (job written to inbox)
    inbox_files = list((queue / "inbox").glob("*.json")) if (queue / "inbox").exists() else []
    assert inbox_files, "Expected a job to be written to the inbox after 'save it'"
    # The preview is cleared after successful submit
    assert vera_session_store.read_session_preview(queue, sid) is None


def test_follow_up_save_this_submits_code_draft_preview(tmp_path, monkeypatch):
    """'save this' after a code draft submits because preview exists."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {
            "answer": f"Here's the script:\n\n```python\n{_PYTHON_CODE}\n```",
            "status": "ok:code_draft",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post("/chat", data={"session_id": sid, "message": "create a python script"})
    assert vera_session_store.read_session_preview(queue, sid) is not None

    res = client.post("/chat", data={"session_id": sid, "message": "save this"})
    assert res.status_code == 200
    inbox_files = list((queue / "inbox").glob("*.json")) if (queue / "inbox").exists() else []
    assert inbox_files, "Expected a job to be written to the inbox after 'save this'"


def test_code_draft_preview_has_real_content_not_empty_placeholder(tmp_path, monkeypatch):
    """The preview content must be the actual LLM-generated code, not the empty placeholder."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    code = "def scrape(url):\n    import requests\n    return requests.get(url).text"

    async def _fake_reply(*, turns, user_message, **_kw):
        return {
            "answer": f"Here's a web scraper:\n\n```python\n{code}\n```",
            "status": "ok:code_draft",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={"session_id": sid, "message": "write me a python scraper script"},
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["content"] == code
    assert preview["write_file"]["content"] != ""


def test_no_pseudo_preview_json_in_user_facing_chat_output(tmp_path, monkeypatch):
    """Internal write_file JSON must NOT leak raw into the user-facing answer."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {
            "answer": f"Here's your script:\n\n```python\n{_PYTHON_CODE}\n```",
            "status": "ok:code_draft",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post("/chat", data={"session_id": sid, "message": "write me a python script"})

    assert res.status_code == 200
    turns = vera_session_store.read_session_turns(queue, sid)
    assistant_turns = [t for t in turns if t["role"] == "assistant"]
    assert assistant_turns
    last_assistant = assistant_turns[-1]["text"]
    # Raw internal action JSON must not appear as a standalone blob in the reply
    assert '"action": "update_preview"' not in last_assistant


def test_code_draft_when_llm_reply_has_no_code_fence_no_exception(tmp_path, monkeypatch):
    """When the LLM reply has no fenced code, no exception is raised."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {
            "answer": "I can help you write a Python script. What should it do?",
            "status": "ok:clarifying",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post("/chat", data={"session_id": sid, "message": "write me a python script"})

    assert res.status_code == 200
    turns = vera_session_store.read_session_turns(queue, sid)
    assert turns


def test_existing_write_file_flows_still_work(tmp_path, monkeypatch):
    """Regression: existing explicit write_file flows must not be broken."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {"answer": "I prepared a write preview for hello.txt.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": 'write a file called hello.txt with content "world"'},
    )

    assert res.status_code == 200
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/hello.txt"
    assert preview["write_file"]["content"] == "world"


def test_code_draft_explicit_filename_is_used(tmp_path, monkeypatch):
    """When the user provides an explicit filename, it is used in the preview path."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {
            "answer": f"Here's scraper.py:\n\n```python\n{_PYTHON_CODE}\n```",
            "status": "ok:code_draft",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={"session_id": sid, "message": "write me a python script called scraper.py"},
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/scraper.py"


def test_code_draft_does_not_enqueue_without_explicit_submit(tmp_path, monkeypatch):
    """Creating a code draft preview must not enqueue a job automatically."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {
            "answer": f"Here's the script:\n\n```python\n{_PYTHON_CODE}\n```",
            "status": "ok:code_draft",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post("/chat", data={"session_id": sid, "message": "write me a python script"})

    inbox = queue / "inbox"
    assert not inbox.exists() or not list(inbox.glob("*.json")), (
        "No job should be enqueued before explicit submit"
    )


def test_code_draft_refinement_updates_preview_and_shows_reply(tmp_path, monkeypatch):
    """Refining a code draft updates the preview content and shows the updated code."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    original_code = "import os\n\ndef main():\n    print('hello')\n\nmain()"
    updated_code = "import requests\n\ndef main():\n    r = requests.get('https://example.com')\n    print(r.text)\n\nmain()"

    call_count = 0

    async def _fake_reply(*, turns, user_message, **_kw):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "answer": f"Here's a Python script:\n\n```python\n{original_code}\n```",
                "status": "ok:code_draft",
            }
        return {
            "answer": f"Here's the updated script using requests:\n\n```python\n{updated_code}\n```",
            "status": "ok:code_draft",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    # Turn 1: initial code draft
    client.post("/chat", data={"session_id": sid, "message": "write me a python script"})
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["content"] == original_code

    # Turn 2: refinement — not a fresh code draft request, but the reply has code
    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "actually use the requests library instead"},
    )
    assert res.status_code == 200

    # Preview must be updated with the new code
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["content"] == updated_code

    # The updated code must appear in the response (not suppressed)
    assert "requests" in res.text
    # Must NOT see the generic suppression message
    assert "Nothing has been submitted or executed yet. I can send it whenever" not in res.text


def test_code_draft_refinement_then_save_it_submits(tmp_path, monkeypatch):
    """Full flow: draft → refine → save it → job enqueued."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    original_code = "print('hello')"
    updated_code = "print('hello world')"

    call_count = 0

    async def _fake_reply(*, turns, user_message, **_kw):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "answer": f"```python\n{original_code}\n```",
                "status": "ok:code_draft",
            }
        return {
            "answer": f"```python\n{updated_code}\n```",
            "status": "ok:code_draft",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    # Draft
    client.post("/chat", data={"session_id": sid, "message": "create a python script"})
    # Refine
    client.post("/chat", data={"session_id": sid, "message": "change it to say hello world"})
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["content"] == updated_code

    # Save
    client.post("/chat", data={"session_id": sid, "message": "save it"})
    inbox_files = list((queue / "inbox").glob("*.json")) if (queue / "inbox").exists() else []
    assert inbox_files, "Expected a job after draft → refine → save it"


def test_lets_save_it_with_apostrophe_submits(tmp_path, monkeypatch):
    """\"let's save it\" (with apostrophe) correctly submits when a preview exists."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {
            "answer": "```python\nprint('done')\n```",
            "status": "ok:code_draft",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post("/chat", data={"session_id": sid, "message": "write me a python script"})
    assert vera_session_store.read_session_preview(queue, sid) is not None

    client.post("/chat", data={"session_id": sid, "message": "let's save it"})
    inbox_files = list((queue / "inbox").glob("*.json")) if (queue / "inbox").exists() else []
    assert inbox_files, 'Expected a job after "let\'s save it"'


def test_write_that_to_a_file_submits_when_preview_exists(tmp_path, monkeypatch):
    """\"write that to a file\" submits when a preview exists."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {
            "answer": "```bash\necho hello\n```",
            "status": "ok:code_draft",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post("/chat", data={"session_id": sid, "message": "make a bash script"})
    assert vera_session_store.read_session_preview(queue, sid) is not None

    client.post("/chat", data={"session_id": sid, "message": "write that to a file"})
    inbox_files = list((queue / "inbox").glob("*.json")) if (queue / "inbox").exists() else []
    assert inbox_files, 'Expected a job after "write that to a file"'


# ---------------------------------------------------------------------------
# Code draft lane truthfulness / state-sync tests
# ---------------------------------------------------------------------------


def test_no_false_preview_claim_when_llm_has_no_fenced_code(tmp_path, monkeypatch):
    """When the LLM mentions 'preview' but produces no fenced code, no preview exists."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        # LLM claims preview exists but never produces a fenced code block
        return {
            "answer": (
                "I've drafted the script and it's available in the Preview Pane. "
                "Review and submit it when you're ready."
            ),
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "write me a python script"},
    )

    assert res.status_code == 200
    # No real preview should exist
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is None, "No preview should exist when no fenced code was produced"
    # The response must NOT contain false preview-pane claims
    turns = vera_session_store.read_session_turns(queue, sid)
    assistant_turns = [t for t in turns if t["role"] == "assistant"]
    assert assistant_turns
    last = assistant_turns[-1]["text"].lower()
    assert "preview pane" not in last, "False preview pane claim must be stripped"


def test_no_false_preview_claim_when_builder_creates_empty_preview(tmp_path, monkeypatch):
    """When the builder creates a preview with empty content, false preview claims are stripped."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_builder(
        *,
        turns,
        user_message,
        active_preview,
        enrichment_context=None,
        investigation_context=None,
        **_kw,
    ):
        # Builder creates a preview with empty write_file content
        return {
            "goal": "draft a python script as script.py",
            "write_file": {
                "path": "~/VoxeraOS/notes/script.py",
                "content": "",
                "mode": "overwrite",
            },
        }

    async def _fake_reply(*, turns, user_message, **_kw):
        # LLM reply has no fenced code block but falsely claims preview exists
        return {
            "answer": "I've prepared the script. Check the Preview Pane to review it.",
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _fake_builder)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "write me a python script"},
    )

    assert res.status_code == 200
    # False claim stripped AND empty placeholder shell cleared (all-or-nothing).
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is None, (
        "Empty-content placeholder must be cleared when a false preview claim is stripped"
    )
    turns = vera_session_store.read_session_turns(queue, sid)
    assistant_turns = [t for t in turns if t["role"] == "assistant"]
    assert assistant_turns
    last = assistant_turns[-1]["text"]
    assert "preview pane" not in last.lower(), (
        "False preview-existence claim must be stripped when content is empty"
    )


def test_submit_with_no_preview_fails_truthfully(tmp_path, monkeypatch):
    """'submit it' / 'go ahead' without a real preview fails clearly."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post("/chat", data={"session_id": sid, "message": "submit it"})

    assert res.status_code == 200
    turns = vera_session_store.read_session_turns(queue, sid)
    assistant_turns = [t for t in turns if t["role"] == "assistant"]
    assert assistant_turns
    last = assistant_turns[-1]["text"]
    # Must clearly say no preview exists
    assert "don't have a prepared preview" in last or "did not submit" in last
    # Must not claim submission succeeded
    assert "submitted" not in last.lower() or "did not submit" in last.lower()
    # No job enqueued
    inbox_files = list((queue / "inbox").glob("*.json")) if (queue / "inbox").exists() else []
    assert not inbox_files, "No job should be enqueued without a real preview"


def test_go_ahead_with_no_preview_fails_truthfully(tmp_path, monkeypatch):
    """'go ahead' without preview fails clearly."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post("/chat", data={"session_id": sid, "message": "go ahead"})

    assert res.status_code == 200
    turns = vera_session_store.read_session_turns(queue, sid)
    assistant_turns = [t for t in turns if t["role"] == "assistant"]
    assert assistant_turns
    last = assistant_turns[-1]["text"]
    assert "don't have a prepared preview" in last or "did not submit" in last
    inbox_files = list((queue / "inbox").glob("*.json")) if (queue / "inbox").exists() else []
    assert not inbox_files


def test_submit_with_real_code_preview_succeeds(tmp_path, monkeypatch):
    """'submit it' with a real code draft preview triggers actual queue handoff."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    code = "print('hello')"

    async def _fake_reply(*, turns, user_message, **_kw):
        return {
            "answer": f"Here's the script:\n\n```python\n{code}\n```",
            "status": "ok:code_draft",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    # Create the code draft
    client.post("/chat", data={"session_id": sid, "message": "write me a python script"})
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["content"] == code

    # Submit it
    client.post("/chat", data={"session_id": sid, "message": "submit it"})
    inbox_files = list((queue / "inbox").glob("*.json")) if (queue / "inbox").exists() else []
    assert inbox_files, "Expected a job in the inbox after submitting a real preview"
    # Preview should be cleared after successful submit
    assert vera_session_store.read_session_preview(queue, sid) is None


def test_code_in_chat_without_preview_does_not_claim_preview_exists(tmp_path, monkeypatch):
    """Code shown in chat alone must not claim a preview exists."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        # LLM produces code in a fence AND claims preview exists
        return {
            "answer": (
                "Here's a quick example:\n\n```python\nx = 1\n```\n\n"
                "I've prepared a preview for you."
            ),
            "status": "ok:test",
        }

    async def _fake_builder(
        *,
        turns,
        user_message,
        active_preview,
        enrichment_context=None,
        investigation_context=None,
        **_kw,
    ):
        return None

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _fake_builder)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    # This message IS a code draft request, but the builder returns None
    # and the code injection creates a preview.  In this case the code
    # draft lane DOES create a real preview (from classify_code_draft_intent),
    # so the "I've prepared a preview" claim is actually true.
    # The test validates that the preview IS real and the claim IS valid.
    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "write me a python script"},
    )
    assert res.status_code == 200
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None, "Real preview must exist when LLM produced fenced code"
    assert preview["write_file"]["content"] == "x = 1"


def test_false_preview_claim_stripped_preserves_code_blocks(tmp_path, monkeypatch):
    """When a false preview claim is stripped, fenced code blocks are preserved."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    # Simulate a non-code-draft informational turn where the LLM hallucinated
    # a preview claim.  Use a message that is NOT a code draft request.
    async def _fake_reply(*, turns, user_message, **_kw):
        return {
            "answer": (
                "Here is an example:\n\n```python\nprint('hello')\n```\n\n"
                "I've prepared a preview for you."
            ),
            "status": "ok:test",
        }

    async def _fake_builder(
        *,
        turns,
        user_message,
        active_preview,
        enrichment_context=None,
        investigation_context=None,
        **_kw,
    ):
        return None

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _fake_builder)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    # Non-code-draft message — should NOT create a preview
    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "explain how to print in python"},
    )
    assert res.status_code == 200
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is None, "No preview should exist for an informational query"
    # The code block should still be visible in the response
    turns = vera_session_store.read_session_turns(queue, sid)
    assistant_turns = [t for t in turns if t["role"] == "assistant"]
    assert assistant_turns
    last = assistant_turns[-1]["text"]
    assert "```python" in last, "Code block must be preserved"
    # But "prepared a preview" claim should be gone
    assert "prepared a preview" not in last.lower()


def test_existing_explicit_write_file_flow_not_regressed(tmp_path, monkeypatch):
    """Explicit write_file preview flows must still work."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_reply(*, turns, user_message, **_kw):
        return {"answer": "I prepared a write preview for hello.txt.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={"session_id": sid, "message": 'write a file called hello.txt with content "world"'},
    )

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/hello.txt"
    assert preview["write_file"]["content"] == "world"

    # Submit
    client.post("/chat", data={"session_id": sid, "message": "submit it"})
    inbox_files = list((queue / "inbox").glob("*.json")) if (queue / "inbox").exists() else []
    assert inbox_files, "Explicit write_file submit must still work"


# ---------------------------------------------------------------------------
# Code draft all-or-nothing correctness tests (fourth-pass patch)
# ---------------------------------------------------------------------------


def test_explicit_filename_code_draft_populates_preview_content(tmp_path, monkeypatch):
    """'Create a Python script called scraper.py ...' must populate real code content."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    expected_code = (
        "import requests\nfrom bs4 import BeautifulSoup\n\n"
        "resp = requests.get('https://example.com')\n"
        "soup = BeautifulSoup(resp.text, 'html.parser')\n"
        "print(soup.find('h1').text)"
    )

    async def _fake_reply(*, turns, user_message, **_kw):
        return {
            "answer": (
                f"Here is scraper.py:\n\n```python\n{expected_code}\n```\n\n"
                "The preview is ready in the Preview Pane."
            ),
            "status": "ok:code_draft",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": (
                "Create a Python script called scraper.py that downloads a page "
                "and prints the first h1 text."
            ),
        },
    )

    assert res.status_code == 200
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None, "Preview must exist after successful code draft"
    assert "write_file" in preview
    assert "scraper.py" in preview["write_file"]["path"]
    assert preview["write_file"]["content"] == expected_code, (
        "Preview content must contain the actual generated code, not an empty string"
    )
    assert preview["write_file"]["content"] != "", "Preview content must not be empty"


def test_failed_code_draft_clears_empty_preview_shell(tmp_path, monkeypatch):
    """When LLM makes false preview claim but produces no code, empty shell is cleared."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_builder(
        *,
        turns,
        user_message,
        active_preview,
        enrichment_context=None,
        investigation_context=None,
        **_kw,
    ):
        # Simulates the real hidden compiler creating a placeholder with the right path
        return {
            "goal": "draft a python scraper as scraper.py",
            "write_file": {
                "path": "~/VoxeraOS/notes/scraper.py",
                "content": "",
                "mode": "overwrite",
            },
        }

    async def _fake_reply(*, turns, user_message, **_kw):
        # LLM claims preview is ready but produces no fenced code block
        return {
            "answer": (
                "I've created scraper.py for you. "
                "You can review it in the Preview Pane and submit when ready."
            ),
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _fake_builder)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": (
                "Create a Python script called scraper.py that downloads a page "
                "and prints the first h1 text."
            ),
        },
    )

    assert res.status_code == 200
    # All-or-nothing: empty shell must be cleared when false claim is stripped
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is None, "Empty placeholder shell must be cleared when no code was generated"
    # Chat wording must be truthful (not "check the preview pane")
    turns = vera_session_store.read_session_turns(queue, sid)
    assistant_turns = [t for t in turns if t["role"] == "assistant"]
    assert assistant_turns
    last = assistant_turns[-1]["text"].lower()
    assert "preview pane" not in last, "False preview-pane claim must be stripped"
    assert "review it in the preview" not in last, "False review claim must be stripped"


def test_code_draft_placeholder_survives_when_llm_makes_no_preview_claim(tmp_path, monkeypatch):
    """Placeholder preview survives when the LLM does not claim a preview exists.

    This preserves the normal write-file refinement flow where users provide
    content in a follow-up message.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    async def _fake_builder(
        *,
        turns,
        user_message,
        active_preview,
        enrichment_context=None,
        investigation_context=None,
        **_kw,
    ):
        return {
            "goal": "create script.ps1",
            "write_file": {
                "path": "~/VoxeraOS/notes/script.ps1",
                "content": "",
                "mode": "overwrite",
            },
        }

    async def _fake_reply(*, turns, user_message, **_kw):
        # LLM acknowledges the request without claiming preview is visible
        return {
            "answer": "I'll create script.ps1. What content would you like inside it?",
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _fake_builder)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "write a file called script.ps1"},
    )

    assert res.status_code == 200
    # Placeholder must survive — no false claim, no clearing
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None, (
        "Placeholder preview must survive when LLM makes no false preview claim"
    )
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/script.ps1"


def test_code_draft_with_trailing_space_on_fence_line_extracts_code(tmp_path, monkeypatch):
    """Code extraction must succeed even when LLM adds trailing space after language tag."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    code = "print('scraped!')"

    async def _fake_reply(*, turns, user_message, **_kw):
        # LLM emits ```python<space> — trailing space after the language tag
        return {
            "answer": f"Here it is:\n\n```python \n{code}\n```\n\nDone.",
            "status": "ok:code_draft",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={"session_id": sid, "message": "write me a python script"},
    )

    assert res.status_code == 200
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None, (
        "Code extraction must succeed for fence lines with trailing whitespace"
    )
    assert preview["write_file"]["content"] == code, (
        "Extracted code must match even when fence line has trailing space"
    )


# ---------------------------------------------------------------------------
# Code draft lane bring-up tests (fifth-pass — LLM code-generation hint)
# These tests cover the real-world product flows that were failing end-to-end.
# ---------------------------------------------------------------------------


def test_code_draft_hint_injected_into_user_message_for_code_draft(tmp_path, monkeypatch):
    """code_draft=True must be passed to generate_vera_reply for code-draft requests.

    This verifies the LLM will receive the code-generation hint (injected by
    service.py's build_vera_messages) to output code in a fenced block,
    overriding Vera's default "not the payload drafter" stance.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    captured: dict = {}

    async def _capture_reply(*, turns, user_message, **_kw):
        captured["user_message"] = user_message
        captured["code_draft"] = _kw.get("code_draft", False)
        return {
            "answer": "```python\nprint('hi')\n```",
            "status": "ok:code_draft",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _capture_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    client.post(
        "/chat",
        data={"session_id": sid, "message": "write me a python script"},
    )

    assert "user_message" in captured, "generate_vera_reply must have been called"
    assert captured["code_draft"] is True, "code_draft=True must be passed for code-draft requests"
    assert captured["user_message"] == "write me a python script", (
        "Original user message must be preserved (hint injected by service layer)"
    )


def test_code_draft_hint_not_injected_for_non_code_draft(tmp_path, monkeypatch):
    """code_draft must be False for non-code-draft requests."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    captured: dict = {}

    async def _capture_reply(*, turns, user_message, **_kw):
        captured["user_message"] = user_message
        captured["code_draft"] = _kw.get("code_draft", False)
        return {"answer": "Here is the status.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _capture_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    # Use a plainly conversational message that cannot match any early-return path
    client.post("/chat", data={"session_id": sid, "message": "tell me about yourself"})

    assert "user_message" in captured, "generate_vera_reply must have been called"
    assert captured["code_draft"] is not True, (
        "code_draft must NOT be True for non-code-draft requests"
    )
    assert captured["user_message"] == "tell me about yourself"


def test_real_world_python_url_fetch_script_creates_preview(tmp_path, monkeypatch):
    """'I need you to code a python script that fetches a URL and prints the page title.'

    This was a real-world failing prompt.  Simulates the LLM responding
    correctly (code in fenced block) and verifies the preview is populated.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    code = (
        "import urllib.request\nfrom html.parser import HTMLParser\n\n"
        "class TitleParser(HTMLParser):\n"
        "    def __init__(self):\n        super().__init__()\n        self.title = ''\n        self._in_title = False\n"
        "    def handle_starttag(self, tag, attrs):\n        if tag == 'title': self._in_title = True\n"
        "    def handle_endtag(self, tag):\n        if tag == 'title': self._in_title = False\n"
        "    def handle_data(self, data):\n        if self._in_title: self.title += data\n\n"
        "url = input('Enter URL: ')\n"
        "with urllib.request.urlopen(url) as r:\n    html = r.read().decode()\n"
        "p = TitleParser()\np.parse(html)\nprint(p.title)"
    )

    async def _fake_reply(*, turns, user_message, **_kw):
        return {
            "answer": f"Here is the Python script:\n\n```python\n{code}\n```",
            "status": "ok:code_draft",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": (
                "I need you to code a python script that fetches a URL and prints the page title."
            ),
        },
    )

    assert res.status_code == 200
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None, "Preview must exist for real-world code-draft prompt"
    assert "write_file" in preview
    assert preview["write_file"]["content"] == code, (
        "Preview content must be real code, not empty string"
    )
    assert preview["write_file"]["content"] != ""


def test_real_world_scrape_any_website_creates_preview(tmp_path, monkeypatch):
    """'write me a python script that will scrape any website'

    This was a real-world failing prompt.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    code = "import requests\nfrom bs4 import BeautifulSoup\n\nurl = input('URL: ')\nresp = requests.get(url)\nprint(BeautifulSoup(resp.text, 'html.parser').get_text())"

    async def _fake_reply(*, turns, user_message, **_kw):
        return {
            "answer": f"```python\n{code}\n```",
            "status": "ok:code_draft",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    res = client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": "write me a python script that will scrape any website",
        },
    )

    assert res.status_code == 200
    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None, "Preview must exist after code-draft request"
    assert preview["write_file"]["content"] == code
    assert preview["write_file"]["content"] != ""


def test_real_world_bash_disk_memory_creates_preview_and_submit_works(tmp_path, monkeypatch):
    """'Write me a bash script that prints disk usage and memory usage.'

    This was a real-world failing prompt.  Also verifies submit works.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    code = "#!/bin/bash\necho '=== Disk Usage ==='\ndf -h\necho '=== Memory Usage ==='\nfree -h"

    async def _fake_reply(*, turns, user_message, **_kw):
        return {
            "answer": f"Here is the bash script:\n\n```bash\n{code}\n```\n\nReady to submit.",
            "status": "ok:code_draft",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    client.get("/")
    sid = client.cookies.get("vera_session_id") or ""

    # Create the preview
    res = client.post(
        "/chat",
        data={
            "session_id": sid,
            "message": "Write me a bash script that prints disk usage and memory usage.",
        },
    )
    assert res.status_code == 200

    preview = vera_session_store.read_session_preview(queue, sid)
    assert preview is not None, "Preview must exist"
    assert preview["write_file"]["content"] == code, "Content must be real bash code"
    assert ".sh" in preview["write_file"]["path"], "Path must have .sh extension"

    # Submit it
    client.post("/chat", data={"session_id": sid, "message": "save it"})
    inbox_files = list((queue / "inbox").glob("*.json")) if (queue / "inbox").exists() else []
    assert inbox_files, "Job must be enqueued after 'save it' with real preview"


def test_build_vera_messages_includes_code_draft_hint_when_flag_set():
    """build_vera_messages must append code-generation hint when code_draft=True."""
    messages = vera_service.build_vera_messages(
        turns=[],
        user_message="write me a python script",
        code_draft=True,
    )
    user_msg = next(m for m in messages if m["role"] == "user")
    assert "fenced code block" in user_msg["content"], (
        "Code-draft hint must instruct LLM to use a fenced code block"
    )
    assert "write me a python script" in user_msg["content"], (
        "Original user message must be preserved"
    )


def test_build_vera_messages_no_hint_when_flag_not_set():
    """build_vera_messages must NOT add the code-draft hint for normal requests."""
    messages = vera_service.build_vera_messages(
        turns=[],
        user_message="what is in the queue?",
        code_draft=False,
    )
    user_msg = next(m for m in messages if m["role"] == "user")
    assert user_msg["content"] == "what is in the queue?"


def test_build_vera_messages_includes_writing_draft_hint_when_flag_set():
    """build_vera_messages must append prose-draft hint when writing_draft=True."""
    messages = vera_service.build_vera_messages(
        turns=[],
        user_message="write a 2-page essay about black holes",
        writing_draft=True,
    )
    user_msg = next(m for m in messages if m["role"] == "user")
    assert "draft a prose document artifact" in user_msg["content"]
    assert "write a 2-page essay about black holes" in user_msg["content"]


def test_build_vera_messages_rejects_conflicting_draft_hints():
    """build_vera_messages must fail closed if both draft hint types are requested."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        vera_service.build_vera_messages(
            turns=[],
            user_message="write something",
            code_draft=True,
            writing_draft=True,
        )


def test_near_miss_submit_with_active_preview_fails_closed_and_preserves_preview(
    tmp_path, monkeypatch
):
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    client = TestClient(vera_app_module.app)
    home = client.get("/")
    assert home.status_code == 200
    sid = client.cookies.get("vera_session_id") or ""

    vera_session_store.write_session_preview(queue, sid, {"goal": "open https://example.com"})

    res = client.post("/chat", data={"session_id": sid, "message": "sned it"})

    assert res.status_code == 200
    assert "did not submit the preview" in res.text.lower()
    assert "submit command" in res.text.lower()
    assert vera_session_store.read_session_preview(queue, sid) == {
        "goal": "open https://example.com"
    }
    handoff = vera_session_store.read_session_handoff_state(queue, sid) or {}
    assert handoff.get("status") != "submitted"
    assert list((queue / "inbox").glob("inbox-*.json")) == []


def test_active_preview_submit_intent_detection_keeps_rename_fail_closed_boundary():
    from voxera.vera.preview_submission import should_submit_active_preview

    assert should_submit_active_preview("submit it", preview_available=True)
    assert should_submit_active_preview("save it", preview_available=True)
    assert not should_submit_active_preview("save it as renamed.txt", preview_available=True)


def test_ambiguous_active_preview_replacement_detection_characterization():
    from voxera.vera_web.execution_mode import (
        _looks_like_ambiguous_active_preview_content_replacement_request,
    )

    assert _looks_like_ambiguous_active_preview_content_replacement_request(
        "replace that text in the file"
    )
    assert not _looks_like_ambiguous_active_preview_content_replacement_request(
        'replace that text in the file with "hello"'
    )
