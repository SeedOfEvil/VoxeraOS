from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from voxera.vera import session_store as vera_session_store
from voxera.vera.weather import WeatherLocation, WeatherSnapshot
from voxera.vera_web import app as vera_app_module


def set_vera_queue_root(monkeypatch: Any, queue: Path) -> None:
    monkeypatch.setattr(vera_app_module, "_active_queue_root", lambda: queue)


class VeraSessionHarness:
    """Shared narrow-session helper for Vera characterization tests."""

    def __init__(self, client: TestClient, queue: Path, session_id: str) -> None:
        self.client = client
        self.queue = queue
        self.session_id = session_id

    def chat(self, message: str):
        return self.client.post("/chat", data={"session_id": self.session_id, "message": message})

    def preview(self) -> dict[str, Any] | None:
        return vera_session_store.read_session_preview(self.queue, self.session_id)

    def turns(self) -> list[dict[str, str]]:
        return vera_session_store.read_session_turns(self.queue, self.session_id)

    def weather_context(self) -> dict[str, Any] | None:
        return vera_session_store.read_session_weather_context(self.queue, self.session_id)

    def derived_output(self) -> dict[str, Any] | None:
        return vera_session_store.read_session_derived_investigation_output(
            self.queue, self.session_id
        )

    def write_investigation(self, payload: dict[str, Any] | None) -> None:
        vera_session_store.write_session_investigation(self.queue, self.session_id, payload)

    def session_context(self) -> dict[str, Any]:
        return vera_session_store.read_session_context(self.queue, self.session_id)


def make_vera_session(monkeypatch: Any, tmp_path: Path) -> VeraSessionHarness:
    queue = tmp_path / "queue"
    set_vera_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    home = client.get("/")
    assert home.status_code == 200
    session_id = client.cookies.get("vera_session_id") or ""
    assert session_id
    return VeraSessionHarness(client=client, queue=queue, session_id=session_id)


def sample_weather_snapshot(*, query: str = "Calgary AB") -> WeatherSnapshot:
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


def sample_investigation_payload() -> dict[str, object]:
    return {
        "query": "best practices for incident response",
        "retrieved_at_ms": 123,
        "results": [
            {
                "result_id": 1,
                "title": "Guide A",
                "url": "https://example.com/a",
                "source": "example.com",
                "snippet": "Triage and containment workflow.",
                "why_it_matched": "Fast-response guidance.",
                "rank": 1,
            },
            {
                "result_id": 2,
                "title": "Guide B",
                "url": "https://example.com/b",
                "source": "example.com",
                "snippet": "Human review and escalation guidance.",
                "why_it_matched": "Human oversight guidance.",
                "rank": 2,
            },
            {
                "result_id": 3,
                "title": "Guide C",
                "url": "https://example.com/c",
                "source": "example.com",
                "snippet": "Evidence collection and communication plan.",
                "why_it_matched": "Evidence-first guidance.",
                "rank": 3,
            },
            {
                "result_id": 4,
                "title": "Guide D",
                "url": "https://example.com/d",
                "source": "example.com",
                "snippet": "Post-incident review and follow-up actions.",
                "why_it_matched": "Retrospective guidance.",
                "rank": 4,
            },
        ],
    }
