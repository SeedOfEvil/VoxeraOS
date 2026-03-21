from __future__ import annotations

import html
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from .brave_search import BraveSearchClient, WebSearchResult

_TRUSTED_WEATHER_DOMAINS = (
    "weather.gov",
    "weather.gc.ca",
    "metoffice.gov.uk",
    "weather.com",
    "accuweather.com",
    "wunderground.com",
    "timeanddate.com",
    "foreca.com",
    "yr.no",
    "bbc.com",
)

_CONDITION_PATTERN = re.compile(
    r"\b("
    r"clear(?: skies)?|mostly clear(?: skies)?|partly cloudy(?: skies)?|cloudy(?: skies)?|overcast|"
    r"sunny|mostly sunny|hazy|fog|mist|smoke|drizzle|light drizzle|freezing drizzle|"
    r"rain|light rain|heavy rain|rain showers?|showers?|snow|light snow|heavy snow|"
    r"snow showers?|flurries|sleet|hail|thunderstorms?|stormy|windy"
    r")\b",
    re.IGNORECASE,
)

_TEMPERATURE_PATTERN = re.compile(r"(-?\d{1,3}(?:\.\d+)?)\s*°\s*([CF])", re.IGNORECASE)
_DAY_ROW_PATTERN = re.compile(
    r"\b(?P<weekday>Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b"
    r"(?:\s*\((?P<date>\d{4}-\d{2}-\d{2})\))?"
    r"[:,-]?\s*"
    r"(?P<condition>[^.|\n;]{0,80}?)?"
    r"(?:[,;]|\s)+"
    r"(?:high|max)\s*(?P<high>-?\d{1,3}(?:\.\d+)?)\s*°\s*(?P<high_unit>[CF])"
    r"(?:[^.|\n;]{0,40}?)"
    r"(?:low|min)\s*(?P<low>-?\d{1,3}(?:\.\d+)?)\s*°\s*(?P<low_unit>[CF])",
    re.IGNORECASE,
)
_HOURLY_ROW_PATTERN = re.compile(
    r"\b(?P<label>(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\d{1,2}\s*(?:AM|PM)|"
    r"\d{1,2}\s*(?:AM|PM)|now)\b"
    r"[:,-]?\s*"
    r"(?P<temp>-?\d{1,3}(?:\.\d+)?)\s*°\s*(?P<unit>[CF])"
    r"(?:[,;]|\s)+"
    r"(?P<condition>[^.|\n;]{1,60})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WeatherLocation:
    query: str
    name: str
    admin1: str | None = None
    country: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    timezone: str = "UTC"

    @property
    def label(self) -> str:
        parts = [self.name]
        if self.admin1:
            parts.append(self.admin1)
        elif self.country:
            parts.append(self.country)
        return ", ".join(part for part in parts if part)

    @property
    def full_label(self) -> str:
        parts = [self.name]
        if self.admin1:
            parts.append(self.admin1)
        if self.country:
            parts.append(self.country)
        return ", ".join(part for part in parts if part)


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
    source: str = "Brave Search API"
    source_url: str | None = None
    source_domain: str | None = None

    def to_session_payload(self) -> dict[str, Any]:
        return {
            "location_query": self.location.query,
            "resolved_location": self.location.label,
            "resolved_location_full": self.location.full_label,
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
            "source": self.source,
            "source_url": self.source_url,
            "source_domain": self.source_domain,
        }


@dataclass(frozen=True)
class _WeatherExtraction:
    location_label: str
    current_temperature_c: float | None
    current_condition: str | None
    current_feels_like_c: float | None
    current_wind_kph: float | None
    today_high_c: float | None
    today_low_c: float | None
    hourly: list[dict[str, Any]]
    daily: list[dict[str, Any]]

    def supports_current(self) -> bool:
        return self.current_temperature_c is not None and bool(self.current_condition)

    def supports_followup(self, followup_kind: str | None) -> bool:
        if followup_kind == "hourly":
            return len(self.hourly) > 0
        if followup_kind in {"7_day", "14_day", "15_day", "weekend"}:
            return len(self.daily) > 0
        return self.supports_current()


def _normalize_location_name(query: str) -> str:
    cleaned = " ".join(query.strip().split())
    if not cleaned:
        return "that location"
    parts = re.split(r"\s*,\s*|\s{2,}", cleaned)
    if len(parts) == 1 and " " in cleaned:
        pieces = cleaned.split()
        if len(pieces) >= 2 and pieces[-1].isupper() and len(pieces[-1]) <= 3:
            return f"{' '.join(pieces[:-1])}, {pieces[-1]}"
    return ", ".join(part for part in parts if part)


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


def _convert_to_celsius(value: float, unit: str) -> float:
    if unit.upper() == "F":
        return (value - 32.0) * 5.0 / 9.0
    return value


def _round_temp(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{int(round(value))}°C"


def _domain_for_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _domain_priority(url: str) -> int:
    domain = _domain_for_url(url)
    for index, trusted in enumerate(_TRUSTED_WEATHER_DOMAINS):
        if domain == trusted or domain.endswith(f".{trusted}"):
            return len(_TRUSTED_WEATHER_DOMAINS) - index
    return 0


def _clean_html_to_text(raw_html: str) -> str:
    without_scripts = re.sub(
        r"<(script|style)[^>]*>.*?</\1>",
        " ",
        raw_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<[^>]+>", " ", without_scripts)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_json_blocks(raw_html: str) -> list[str]:
    blocks = re.findall(
        r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        raw_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return [html.unescape(block.strip()) for block in blocks if block.strip()]


def _extract_condition(text: str) -> str | None:
    match = _CONDITION_PATTERN.search(text)
    if not match:
        return None
    return match.group(1).strip().lower()


def _extract_current_temperature(text: str) -> tuple[float | None, str | None]:
    for match in _TEMPERATURE_PATTERN.finditer(text):
        window_start = max(0, match.start() - 24)
        window = text[window_start : match.start()].lower()
        if any(token in window for token in ("high", "low", "feels like", "wind chill")):
            continue
        value = _coerce_float(match.group(1))
        if value is None:
            continue
        unit = match.group(2)
        return _convert_to_celsius(value, unit), unit.upper()
    return None, None


def _extract_labeled_temperature(text: str, labels: tuple[str, ...]) -> float | None:
    lowered = text.lower()
    for label in labels:
        idx = lowered.find(label)
        if idx == -1:
            continue
        snippet = text[idx : idx + 80]
        match = _TEMPERATURE_PATTERN.search(snippet)
        if not match:
            continue
        value = _coerce_float(match.group(1))
        if value is None:
            continue
        return _convert_to_celsius(value, match.group(2))
    return None


def _extract_wind_kph(text: str) -> float | None:
    match = re.search(
        r"\bwind(?:\s+speed)?\s*(?:is|around|at|:)?\s*(\d{1,3}(?:\.\d+)?)\s*(km/h|kph|mph)\b",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    value = _coerce_float(match.group(1))
    if value is None:
        return None
    unit = match.group(2).lower()
    return value * 1.60934 if unit == "mph" else value


def _extract_hourly_entries(text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _HOURLY_ROW_PATTERN.finditer(text):
        label = " ".join(match.group("label").split())
        key = label.lower()
        if key in seen:
            continue
        temp = _coerce_float(match.group("temp"))
        if temp is None:
            continue
        condition = match.group("condition").strip(" .,:;").lower()
        entries.append(
            {
                "time": label,
                "display_time": label,
                "temperature_c": _convert_to_celsius(temp, match.group("unit")),
                "condition": condition,
            }
        )
        seen.add(key)
    return entries[:12]


def _extract_daily_entries(text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _DAY_ROW_PATTERN.finditer(text):
        weekday = match.group("weekday").title()
        if weekday in seen:
            continue
        high = _coerce_float(match.group("high"))
        low = _coerce_float(match.group("low"))
        if high is None or low is None:
            continue
        condition = (match.group("condition") or "").strip(" .,:;").lower()
        entries.append(
            {
                "date": (match.group("date") or "").strip(),
                "weekday": weekday,
                "high_c": _convert_to_celsius(high, match.group("high_unit")),
                "low_c": _convert_to_celsius(low, match.group("low_unit")),
                "condition": condition or "mixed conditions",
            }
        )
        seen.add(weekday)
    return entries[:15]


def _extract_from_json_ld(block: str, location_query: str) -> _WeatherExtraction | None:
    try:
        payload = json.loads(block)
    except json.JSONDecodeError:
        return None
    nodes = payload if isinstance(payload, list) else [payload]
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("@type") or "").lower()
        if "weatherforecast" not in node_type:
            continue
        location = _normalize_location_name(
            str(
                node.get("name")
                or node.get("areaServed")
                or node.get("contentLocation")
                or location_query
            )
        )
        temp = _coerce_float(node.get("temperature"))
        high = _coerce_float(node.get("highTemperature"))
        low = _coerce_float(node.get("lowTemperature"))
        condition = _extract_condition(json.dumps(node))
        if temp is None or not condition:
            continue
        return _WeatherExtraction(
            location_label=location,
            current_temperature_c=temp,
            current_condition=condition,
            current_feels_like_c=None,
            current_wind_kph=None,
            today_high_c=high,
            today_low_c=low,
            hourly=[],
            daily=[],
        )
    return None


def _extract_weather_from_text(
    *,
    location_query: str,
    result: WebSearchResult,
    fetched_text: str | None,
) -> _WeatherExtraction | None:
    combined_parts = [result.title, result.description]
    if fetched_text:
        combined_parts.append(fetched_text)
    combined = " ".join(part for part in combined_parts if part).strip()
    if not combined:
        return None

    location_label = _normalize_location_name(location_query)
    current_temperature_c, _ = _extract_current_temperature(combined)
    current_condition = _extract_condition(combined)
    feels_like_c = _extract_labeled_temperature(combined, ("feels like", "realfeel", "apparent"))
    today_high_c = _extract_labeled_temperature(combined, ("today high", "high", "max"))
    today_low_c = _extract_labeled_temperature(combined, ("today low", "low", "min"))
    wind_kph = _extract_wind_kph(combined)
    hourly = _extract_hourly_entries(combined)
    daily = _extract_daily_entries(combined)

    if not daily and today_high_c is not None and today_low_c is not None:
        daily = [
            {
                "date": "",
                "weekday": datetime.now(timezone.utc).strftime("%a"),
                "high_c": today_high_c,
                "low_c": today_low_c,
                "condition": current_condition or "mixed conditions",
            }
        ]

    extraction = _WeatherExtraction(
        location_label=location_label,
        current_temperature_c=current_temperature_c,
        current_condition=current_condition,
        current_feels_like_c=feels_like_c,
        current_wind_kph=wind_kph,
        today_high_c=today_high_c,
        today_low_c=today_low_c,
        hourly=hourly,
        daily=daily,
    )
    return (
        extraction
        if any(
            (
                extraction.current_temperature_c is not None,
                extraction.current_condition,
                extraction.hourly,
                extraction.daily,
            )
        )
        else None
    )


class BraveWeatherClient:
    def __init__(
        self,
        *,
        brave_client: BraveSearchClient,
        fetch_timeout_s: float = 10.0,
        max_candidate_results: int = 5,
        max_page_fetches: int = 3,
    ) -> None:
        self.brave_client = brave_client
        self.fetch_timeout_s = fetch_timeout_s
        self.max_candidate_results = max_candidate_results
        self.max_page_fetches = max_page_fetches

    async def lookup(
        self,
        *,
        location_query: str,
        followup_kind: str | None = None,
    ) -> WeatherSnapshot:
        normalized_location = " ".join(location_query.strip().split())
        if not normalized_location:
            raise RuntimeError("I couldn’t resolve a weather location from that message.")

        search_query = _weather_search_query(normalized_location, followup_kind=followup_kind)
        results = await self.brave_client.search(
            query=search_query,
            count=min(10, max(3, self.max_candidate_results)),
        )
        if not results:
            raise RuntimeError("Brave Search did not return weather results for that location.")

        ranked = sorted(
            results,
            key=lambda result: (
                _domain_priority(result.url),
                1 if result.description.strip() else 0,
                1 if result.title.strip() else 0,
            ),
            reverse=True,
        )

        best_partial: tuple[WebSearchResult, _WeatherExtraction] | None = None
        fetch_budget = self.max_page_fetches
        for result in ranked[: self.max_candidate_results]:
            fetched_text = None
            fetched_html = None
            if fetch_budget > 0:
                fetched_html, fetched_text = await self._fetch_weather_page_content(result.url)
                fetch_budget -= 1
            extraction = _extract_weather_from_text(
                location_query=normalized_location,
                result=result,
                fetched_text=fetched_text,
            )
            if extraction is None and fetched_html:
                for block in _extract_json_blocks(fetched_html):
                    extraction = _extract_from_json_ld(block, normalized_location)
                    if extraction is not None:
                        break
            if extraction is None:
                continue
            if extraction.supports_followup(followup_kind):
                return self._build_snapshot(
                    location_query=normalized_location,
                    result=result,
                    extraction=extraction,
                )
            if best_partial is None and extraction.supports_current():
                best_partial = (result, extraction)

        if best_partial is not None and followup_kind in {
            "hourly",
            "7_day",
            "14_day",
            "15_day",
            "weekend",
        }:
            result, extraction = best_partial
            return self._build_snapshot(
                location_query=normalized_location,
                result=result,
                extraction=extraction,
            )

        requested_shape = {
            "hourly": "an hourly outlook",
            "7_day": "a 7-day outlook",
            "14_day": "a 14-day outlook",
            "15_day": "a 15-day outlook",
            "weekend": "a weekend outlook",
        }.get(followup_kind or "", "current conditions")
        raise RuntimeError(
            f"I found Brave weather results for {normalized_location}, but I couldn't extract "
            f"{requested_shape} reliably enough to answer without guessing."
        )

    async def _fetch_weather_page_content(self, url: str) -> tuple[str | None, str | None]:
        if not url.startswith(("http://", "https://")):
            return None, None
        try:
            async with httpx.AsyncClient(
                timeout=self.fetch_timeout_s,
                headers={
                    "User-Agent": "VoxeraOS/1.0 (+https://github.com/openai/codex)",
                    "Accept": "text/html,application/xhtml+xml",
                },
                follow_redirects=True,
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
        except httpx.HTTPError:
            return None, None
        content_type = (response.headers.get("content-type") or "").lower()
        if "html" not in content_type and "text" not in content_type:
            return None, None
        raw_html = response.text[:150000]
        return raw_html, _clean_html_to_text(raw_html)

    def _build_snapshot(
        self,
        *,
        location_query: str,
        result: WebSearchResult,
        extraction: _WeatherExtraction,
    ) -> WeatherSnapshot:
        location = WeatherLocation(
            query=location_query,
            name=extraction.location_label,
        )
        return WeatherSnapshot(
            location=location,
            retrieved_at_ms=int(time.time() * 1000),
            current_temperature_c=extraction.current_temperature_c or 0.0,
            current_feels_like_c=extraction.current_feels_like_c,
            current_condition=extraction.current_condition or "mixed conditions",
            current_wind_kph=extraction.current_wind_kph,
            today_high_c=extraction.today_high_c,
            today_low_c=extraction.today_low_c,
            hourly=extraction.hourly,
            daily=extraction.daily,
            source_url=result.url,
            source_domain=_domain_for_url(result.url),
        )


def _weather_search_query(location_query: str, followup_kind: str | None) -> str:
    if followup_kind == "hourly":
        return f"hourly weather forecast in {location_query}"
    if followup_kind == "weekend":
        return f"weekend weather forecast in {location_query}"
    if followup_kind == "14_day":
        return f"14 day weather forecast in {location_query}"
    if followup_kind == "15_day":
        return f"15 day weather forecast in {location_query}"
    if followup_kind == "7_day":
        return f"7 day weather forecast in {location_query}"
    return f"current weather in {location_query}"


def format_current_weather_answer(snapshot: Any) -> str:
    location = snapshot.location.label
    current_temp = _round_temp(snapshot.current_temperature_c) or "unknown"
    lines = [f"It’s currently {current_temp} in {location} with {snapshot.current_condition}."]
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
            f"I couldn’t build the hourly outlook for {snapshot.location.label} from the Brave-backed "
            "weather lookup. I can still try the 7-day outlook instead."
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
        return (
            f"I couldn’t build a longer-range outlook for {snapshot.location.label} from the "
            "Brave-backed weather lookup."
        )
    lines = [f"Here’s the next {len(outlook)} days for {snapshot.location.label}:"]
    for entry in outlook:
        high = _round_temp(_coerce_float(entry.get("high_c"))) or "unknown"
        low = _round_temp(_coerce_float(entry.get("low_c"))) or "unknown"
        date_text = str(entry.get("date") or "").strip()
        date_suffix = f" ({date_text})" if date_text else ""
        lines.append(
            f"- {entry.get('weekday')}{date_suffix}: {entry.get('condition')}, high {high}, low {low}."
        )
    lines.append("If you want, I can also show the hourly or weekend outlook.")
    return "\n".join(lines)


def format_weekend_weather_answer(snapshot: Any) -> str:
    weekend = [entry for entry in snapshot.daily if str(entry.get("weekday")) in {"Sat", "Sun"}][:2]
    if not weekend:
        return (
            f"I couldn’t isolate the weekend outlook for {snapshot.location.label} from the "
            "Brave-backed weather lookup. I can show the 7-day outlook instead."
        )
    lines = [f"Here’s the weekend outlook for {snapshot.location.label}:"]
    for entry in weekend:
        high = _round_temp(_coerce_float(entry.get("high_c"))) or "unknown"
        low = _round_temp(_coerce_float(entry.get("low_c"))) or "unknown"
        date_text = str(entry.get("date") or "").strip()
        date_suffix = f" ({date_text})" if date_text else ""
        lines.append(
            f"- {entry.get('weekday')}{date_suffix}: {entry.get('condition')}, high {high}, low {low}."
        )
    lines.append("If you want more, I can also show the hourly or 7-day outlook.")
    return "\n".join(lines)
