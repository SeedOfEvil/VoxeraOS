from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


@dataclass(frozen=True)
class WeatherLocation:
    query: str
    name: str
    admin1: str | None
    country: str | None
    latitude: float
    longitude: float
    timezone: str

    @property
    def label(self) -> str:
        parts = [self.name]
        if self.admin1:
            parts.append(self.admin1)
        elif self.country:
            parts.append(self.country)
        return ", ".join(parts)

    @property
    def full_label(self) -> str:
        parts = [self.name]
        if self.admin1:
            parts.append(self.admin1)
        if self.country:
            parts.append(self.country)
        return ", ".join(parts)


@dataclass(frozen=True)
class WeatherSnapshot:
    location: WeatherLocation
    retrieved_at_ms: int
    current_temperature_c: float
    current_feels_like_c: float | None
    current_condition: str
    current_wind_kph: float | None
    today_high_c: float | None
    today_low_c: float | None
    hourly: list[dict[str, Any]]
    daily: list[dict[str, Any]]

    def to_session_payload(self) -> dict[str, Any]:
        return {
            "location_query": self.location.query,
            "resolved_location": self.location.label,
            "resolved_location_full": self.location.full_label,
            "latitude": self.location.latitude,
            "longitude": self.location.longitude,
            "timezone": self.location.timezone,
            "retrieved_at_ms": self.retrieved_at_ms,
            "current": {
                "temperature_c": self.current_temperature_c,
                "feels_like_c": self.current_feels_like_c,
                "condition": self.current_condition,
                "wind_kph": self.current_wind_kph,
            },
            "today": {
                "high_c": self.today_high_c,
                "low_c": self.today_low_c,
            },
            "hourly": self.hourly,
            "daily": self.daily,
            "followup_active": True,
            "source": "Open-Meteo",
        }


def _weather_code_to_text(code: int | None) -> str:
    mapping = {
        0: "clear skies",
        1: "mostly clear skies",
        2: "partly cloudy skies",
        3: "cloudy skies",
        45: "fog",
        48: "depositing rime fog",
        51: "light drizzle",
        53: "drizzle",
        55: "dense drizzle",
        56: "light freezing drizzle",
        57: "dense freezing drizzle",
        61: "light rain",
        63: "rain",
        65: "heavy rain",
        66: "light freezing rain",
        67: "heavy freezing rain",
        71: "light snow",
        73: "snow",
        75: "heavy snow",
        77: "snow grains",
        80: "light rain showers",
        81: "rain showers",
        82: "heavy rain showers",
        85: "light snow showers",
        86: "heavy snow showers",
        95: "thunderstorms",
        96: "thunderstorms with light hail",
        99: "thunderstorms with hail",
    }
    return mapping.get(code or -1, "mixed conditions")


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _round_temp(value: float | None) -> str | None:
    if value is None:
        return None
    rounded = int(round(value))
    return f"{rounded}°C"


def _parse_iso_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class OpenMeteoWeatherClient:
    def __init__(self, *, timeout_s: float = 15.0) -> None:
        self.timeout_s = timeout_s

    async def resolve_location(self, query: str) -> WeatherLocation | None:
        normalized_query = " ".join(query.strip().split())
        if not normalized_query:
            return None
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                response = await client.get(
                    GEOCODING_URL,
                    params={
                        "name": normalized_query,
                        "count": "5",
                        "language": "en",
                        "format": "json",
                    },
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Weather geocoding request failed (HTTP {exc.response.status_code})"
            ) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError("Weather geocoding request failed due to network error") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Weather geocoding returned invalid JSON") from exc

        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list) or not results:
            return None

        first = results[0]
        if not isinstance(first, dict):
            return None

        name = str(first.get("name") or "").strip()
        latitude = _coerce_float(first.get("latitude"))
        longitude = _coerce_float(first.get("longitude"))
        timezone = str(first.get("timezone") or "").strip() or "auto"
        if not name or latitude is None or longitude is None:
            return None

        admin1 = str(first.get("admin1") or "").strip() or None
        country = str(first.get("country") or "").strip() or None
        return WeatherLocation(
            query=normalized_query,
            name=name,
            admin1=admin1,
            country=country,
            latitude=latitude,
            longitude=longitude,
            timezone=timezone,
        )

    async def fetch_snapshot(self, location: WeatherLocation) -> WeatherSnapshot:
        params = {
            "latitude": str(location.latitude),
            "longitude": str(location.longitude),
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
            "hourly": "temperature_2m,weather_code",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min",
            "forecast_days": "16",
            "timezone": location.timezone or "auto",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                response = await client.get(FORECAST_URL, params=params)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Weather forecast request failed (HTTP {exc.response.status_code})"
            ) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError("Weather forecast request failed due to network error") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Weather forecast returned invalid JSON") from exc

        current = payload.get("current") if isinstance(payload, dict) else None
        daily = payload.get("daily") if isinstance(payload, dict) else None
        hourly = payload.get("hourly") if isinstance(payload, dict) else None
        if (
            not isinstance(current, dict)
            or not isinstance(daily, dict)
            or not isinstance(hourly, dict)
        ):
            raise RuntimeError("Weather forecast response was missing required sections")

        current_temperature_c = _coerce_float(current.get("temperature_2m"))
        if current_temperature_c is None:
            raise RuntimeError("Weather forecast response was missing current temperature")

        current_feels_like_c = _coerce_float(current.get("apparent_temperature"))
        current_wind_kph = _coerce_float(current.get("wind_speed_10m"))
        current_condition = _weather_code_to_text(
            int(_coerce_float(current.get("weather_code")) or -1)
        )

        daily_entries = _build_daily_entries(daily)
        hourly_entries = _build_hourly_entries(hourly)
        today = daily_entries[0] if daily_entries else {}
        return WeatherSnapshot(
            location=location,
            retrieved_at_ms=int(time.time() * 1000),
            current_temperature_c=current_temperature_c,
            current_feels_like_c=current_feels_like_c,
            current_condition=current_condition,
            current_wind_kph=current_wind_kph,
            today_high_c=_coerce_float(today.get("high_c")),
            today_low_c=_coerce_float(today.get("low_c")),
            hourly=hourly_entries,
            daily=daily_entries,
        )


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def _build_hourly_entries(hourly: dict[str, Any]) -> list[dict[str, Any]]:
    times = hourly.get("time")
    temps = hourly.get("temperature_2m")
    codes = hourly.get("weather_code")
    if not isinstance(times, list) or not isinstance(temps, list) or not isinstance(codes, list):
        return []

    entries: list[dict[str, Any]] = []
    for raw_time, raw_temp, raw_code in zip(times, temps, codes, strict=False):
        if not isinstance(raw_time, str):
            continue
        parsed_time = _parse_iso_time(raw_time)
        if parsed_time is None:
            continue
        entries.append(
            {
                "time": raw_time,
                "display_time": parsed_time.strftime("%a %I %p").replace(" 0", " "),
                "temperature_c": _coerce_float(raw_temp),
                "condition": _weather_code_to_text(int(_coerce_float(raw_code) or -1)),
            }
        )
    return entries


def _build_daily_entries(daily: dict[str, Any]) -> list[dict[str, Any]]:
    times = daily.get("time")
    highs = daily.get("temperature_2m_max")
    lows = daily.get("temperature_2m_min")
    codes = daily.get("weather_code")
    if (
        not isinstance(times, list)
        or not isinstance(highs, list)
        or not isinstance(lows, list)
        or not isinstance(codes, list)
    ):
        return []

    entries: list[dict[str, Any]] = []
    for raw_time, raw_high, raw_low, raw_code in zip(times, highs, lows, codes, strict=False):
        if not isinstance(raw_time, str):
            continue
        parsed_time = _parse_iso_time(raw_time)
        if parsed_time is None:
            continue
        entries.append(
            {
                "date": raw_time,
                "weekday": parsed_time.strftime("%a"),
                "high_c": _coerce_float(raw_high),
                "low_c": _coerce_float(raw_low),
                "condition": _weather_code_to_text(int(_coerce_float(raw_code) or -1)),
            }
        )
    return entries


def format_current_weather_answer(snapshot: Any) -> str:
    location = snapshot.location.label
    current_temp = _round_temp(snapshot.current_temperature_c) or "unknown"
    lines = [
        f"It’s currently {current_temp} in {location} with {snapshot.current_condition}.",
    ]
    high = _round_temp(snapshot.today_high_c)
    low = _round_temp(snapshot.today_low_c)
    if high and low:
        lines.append(f"Today’s high is {high} and the low is {low}.")

    feels_like = _round_temp(snapshot.current_feels_like_c)
    if feels_like and feels_like != current_temp:
        lines.append(f"It feels like {feels_like}.")

    if snapshot.current_wind_kph is not None:
        lines.append(f"Wind is around {int(round(snapshot.current_wind_kph))} km/h.")

    lines.append("Want the hourly, 7-day, or weekend outlook?")
    return "\n".join(lines)


def format_hourly_weather_answer(snapshot: Any, *, hours: int = 6) -> str:
    upcoming = [entry for entry in snapshot.hourly if entry.get("temperature_c") is not None][
        :hours
    ]
    if not upcoming:
        return (
            f"I couldn’t build the hourly outlook for {snapshot.location.label} from the live weather data. "
            "I can still try the 7-day outlook instead."
        )

    lines = [f"Here’s the next {len(upcoming)} hours for {snapshot.location.label}:"]
    for entry in upcoming:
        temp = _round_temp(_coerce_float(entry.get("temperature_c"))) or "unknown"
        lines.append(f"- {entry.get('display_time')}: {temp}, {entry.get('condition')}.")
    lines.append("I can also show the 7-day or weekend outlook.")
    return "\n".join(lines)


def format_daily_weather_answer(snapshot: Any, *, days: int) -> str:
    outlook = snapshot.daily[:days]
    if not outlook:
        return f"I couldn’t build a longer-range outlook for {snapshot.location.label} from the live weather data."

    lines = [f"Here’s the next {len(outlook)} days for {snapshot.location.label}:"]
    for entry in outlook:
        high = _round_temp(_coerce_float(entry.get("high_c"))) or "unknown"
        low = _round_temp(_coerce_float(entry.get("low_c"))) or "unknown"
        lines.append(
            f"- {entry.get('weekday')} ({entry.get('date')}): {entry.get('condition')}, high {high}, low {low}."
        )
    lines.append("If you want, I can also show the hourly or weekend outlook.")
    return "\n".join(lines)


def format_weekend_weather_answer(snapshot: Any) -> str:
    weekend = [entry for entry in snapshot.daily if str(entry.get("weekday")) in {"Sat", "Sun"}][:2]
    if not weekend:
        return (
            f"I couldn’t isolate the weekend outlook for {snapshot.location.label} from the live weather data. "
            "I can show the 7-day outlook instead."
        )

    lines = [f"Here’s the weekend outlook for {snapshot.location.label}:"]
    for entry in weekend:
        high = _round_temp(_coerce_float(entry.get("high_c"))) or "unknown"
        low = _round_temp(_coerce_float(entry.get("low_c"))) or "unknown"
        lines.append(
            f"- {entry.get('weekday')} ({entry.get('date')}): {entry.get('condition')}, high {high}, low {low}."
        )
    lines.append("If you want more, I can also show the hourly or 7-day outlook.")
    return "\n".join(lines)
