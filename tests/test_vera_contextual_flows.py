from __future__ import annotations

import json

from voxera.models import AppConfig, WebInvestigationConfig
from voxera.vera import service as vera_service
from voxera.vera.brave_search import WebSearchResult

from .vera_session_helpers import (
    make_vera_session,
    sample_investigation_payload,
    sample_weather_snapshot,
)


def test_weather_missing_location_then_followup_hourly_stays_in_weather_lane(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_lookup(location_query: str):
        assert location_query == "Calgary AB"
        return sample_weather_snapshot(query=location_query)

    monkeypatch.setattr(vera_service, "_lookup_live_weather", _fake_lookup)

    first = session.chat("What's the weather like?")
    second = session.chat("Calgary AB")
    third = session.chat("hourly")

    assert first.status_code == 200
    assert "Which location should I check?" in first.text
    assert session.weather_context() is not None
    assert second.status_code == 200
    assert "It’s currently 3°C in Calgary, Alberta" in second.text
    assert third.status_code == 200
    assert "Here’s the next 3 hours for Calgary, Alberta:" in third.text
    assert "Here are the top findings" not in third.text


def test_weather_followup_7_day_and_weekend_stay_in_weather_lane(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_lookup(location_query: str):
        assert location_query == "Calgary AB"
        return sample_weather_snapshot(query=location_query)

    monkeypatch.setattr(vera_service, "_lookup_live_weather", _fake_lookup)

    first = session.chat("What's the weather in Calgary AB?")
    weekly = session.chat("7 day")
    weekly_turn = session.turns()[-1]["text"]
    weekend = session.chat("weekend")
    weekend_turn = session.turns()[-1]["text"]

    assert first.status_code == 200
    assert weekly.status_code == 200
    assert "Here’s the next 7 days for Calgary, Alberta:" in weekly_turn
    assert "- Sat (2026-03-21): cloudy skies, high 6°C, low -4°C." in weekly_turn
    assert "Here are the top findings" not in weekly_turn
    assert weekend.status_code == 200
    assert "Here’s the weekend outlook for Calgary, Alberta:" in weekend_turn
    assert "- Sun (2026-03-22): partly cloudy skies, high 7°C, low -2°C." in weekend_turn
    assert "Here are the top findings" not in weekend_turn


def test_service_level_weather_followup_hook_still_controls_delegated_flow(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_lookup(location_query: str):
        assert location_query == "Calgary AB"
        return sample_weather_snapshot(query=location_query)

    monkeypatch.setattr(vera_service, "_lookup_live_weather", _fake_lookup)
    monkeypatch.setattr(
        vera_service,
        "_weather_answer_for_followup",
        lambda snapshot_payload, followup_kind: f"patched followup: {followup_kind}",
    )

    first = session.chat("What's the weather in Calgary AB?")
    followup = session.chat("hourly")

    assert first.status_code == 200
    assert followup.status_code == 200
    assert session.turns()[-1]["text"] == "patched followup: hourly"


def test_service_level_investigation_detector_hook_still_controls_weather_question(
    tmp_path, monkeypatch
):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_lookup(location_query: str):
        assert location_query == "Calgary AB"
        return sample_weather_snapshot(query=location_query)

    monkeypatch.setattr(vera_service, "_lookup_live_weather", _fake_lookup)
    monkeypatch.setattr(vera_service, "_is_weather_investigation_request", lambda _message: False)

    res = session.chat("Look up the weather in Calgary AB")

    assert res.status_code == 200
    assert "It’s currently 3°C in Calgary, Alberta" in session.turns()[-1]["text"]


def test_service_level_location_normalizer_hook_still_controls_inline_weather_location(
    tmp_path, monkeypatch
):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _fake_lookup(location_query: str):
        assert location_query == "Calgary AB"
        return sample_weather_snapshot(query=location_query)

    monkeypatch.setattr(vera_service, "_lookup_live_weather", _fake_lookup)
    monkeypatch.setattr(
        vera_service,
        "_normalize_weather_location_candidate",
        lambda candidate: "Calgary AB" if "YYC" in candidate else candidate,
    )

    res = session.chat("What's the weather in YYC?")

    assert res.status_code == 200
    assert "It’s currently 3°C in Calgary, Alberta" in session.turns()[-1]["text"]


def test_invalid_weather_location_fails_closed_without_guessing(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)

    async def _missing_lookup(location_query: str):
        assert location_query == "Atlantis"
        raise RuntimeError("Could not resolve location: Atlantis")

    monkeypatch.setattr(vera_service, "_lookup_live_weather", _missing_lookup)

    res = session.chat("What's the weather in Atlantis right now?")

    assert res.status_code == 200
    last_turn = session.turns()[-1]["text"].lower()
    assert "won’t guess" in last_turn
    assert "atlantis" in last_turn
    assert "it’s currently" not in last_turn


def test_investigation_compare_summarize_expand_save_and_submit_preserve_lane_truth(
    tmp_path, monkeypatch
):
    session = make_vera_session(monkeypatch, tmp_path)
    session.write_investigation(sample_investigation_payload())

    compare = session.chat("compare results 1 and 3")
    assert compare.status_code == 200
    compare_turn = session.turns()[-1]["text"].lower()
    assert "compared results: 1, 3" in compare_turn

    summary = session.chat("summarize all findings")
    assert summary.status_code == 200
    summary_output = session.derived_output()
    assert summary_output is not None
    assert summary_output["derivation_type"] == "summary"
    assert summary_output["selected_result_ids"] == [1, 2, 3, 4]

    vera_service.write_session_derived_investigation_output(
        session.queue,
        session.session_id,
        {
            "derivation_type": "expanded_result",
            "query": "best practices for incident response",
            "selected_result_ids": [1],
            "result_id": 1,
            "result_title": "Guide A",
            "answer": (
                "Result 1 recommends a tight incident-response loop: fast triage, source-backed "
                "containment, and explicit human review before governed action."
            ),
            "markdown": (
                "# Expanded Investigation Result 1\n\n"
                "## Expanded Writeup\n"
                "Result 1 recommends a tight incident-response loop: fast triage, source-backed "
                "containment, and explicit human review before governed action.\n"
            ),
        },
    )

    save = session.chat("save that expanded result as incident-response.md")
    assert save.status_code == 200

    preview = session.preview()
    assert preview is not None
    assert preview["goal"] == "write investigation expanded result to markdown note"
    assert preview["write_file"]["path"] == "~/VoxeraOS/notes/incident-response.md"
    assert "incident-response loop" in preview["write_file"]["content"].lower()

    submit = session.chat("submit it")
    assert submit.status_code == 200
    inbox_files = list((session.queue / "inbox").glob("*.json"))
    assert len(inbox_files) == 1
    payload = json.loads(inbox_files[0].read_text(encoding="utf-8"))
    assert payload["write_file"]["path"] == "~/VoxeraOS/notes/incident-response.md"


def test_explicit_weather_investigation_stays_in_brave_lane(tmp_path, monkeypatch):
    session = make_vera_session(monkeypatch, tmp_path)
    monkeypatch.setattr(
        vera_service,
        "load_app_config",
        lambda: AppConfig(
            web_investigation=WebInvestigationConfig(
                api_key_ref="BRAVE_API_KEY", env_api_key_var="BRAVE_API_KEY", max_results=5
            )
        ),
    )

    class _FakeBraveClient:
        def __init__(self, **kwargs):
            _ = kwargs

        async def search(self, *, query: str, count: int = 5):
            assert "weather in calgary" in query.lower()
            return [
                WebSearchResult(
                    title="Weather overview",
                    url="https://example.com/weather",
                    description="A source overview.",
                )
            ]

    async def _fail_if_weather_lookup(_location_query: str):
        raise AssertionError(
            "quick weather lookup should not run for explicit investigation request"
        )

    monkeypatch.setattr(vera_service, "BraveSearchClient", _FakeBraveClient)
    monkeypatch.setattr(vera_service, "_lookup_live_weather", _fail_if_weather_lookup)

    res = session.chat("Search the web for weather in Calgary AB")

    assert res.status_code == 200
    assert session.turns()[-1]["role"] == "assistant"
    assert (
        "Here are the top findings I found via read-only Brave web investigation"
        in session.turns()[-1]["text"]
    )
