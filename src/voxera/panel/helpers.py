from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

from fastapi import Request


def coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            return None
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


async def request_value(request: Request, key: str, default: str = "") -> str:
    query_value = request.query_params.get(key)
    if query_value is not None:
        return query_value

    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/x-www-form-urlencoded"):
        body = (await request.body()).decode("utf-8", errors="ignore")
        values = parse_qs(body, keep_blank_values=True)
        if key in values and values[key]:
            return values[key][0]

    if "application/json" in content_type:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            value = payload.get(key, default)
            if value is None:
                return ""
            return str(value)
    return default
