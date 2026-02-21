"""Gemini adapter using the Gemini generateContent API."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from ..secrets import get_secret
from .base import BrainResponse, ToolSpec


class GeminiBrain:
    def __init__(
        self,
        model: str,
        api_key_ref: str | None = None,
        *,
        timeout: float = 60.0,
        api_base: str = "https://generativelanguage.googleapis.com",
    ):
        self.model = model
        self.api_key_ref = api_key_ref
        self.timeout = timeout
        self.api_base = api_base.rstrip("/")

    def _resolve_api_key(self) -> str:
        if not self.api_key_ref:
            raise RuntimeError("Gemini API key is required for planner.generate")
        key = get_secret(self.api_key_ref) or self.api_key_ref
        if key and key.startswith(("keyring:", "file:")):
            key = get_secret(key.split(":", 1)[1])
        if not key:
            raise RuntimeError("Gemini API key is missing or empty")
        return key

    def _convert_messages_to_contents(self, messages: list[dict[str, str]]) -> list[dict[str, Any]]:
        contents: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role") or "user")
            text = str(message.get("content") or "")
            gemini_role = "model" if role == "assistant" else "user"
            contents.append({"role": gemini_role, "parts": [{"text": text}]})
        return contents

    def _extract_text(self, data: dict[str, Any]) -> str:
        try:
            candidates = data["candidates"]
            first = candidates[0]
            parts = first["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                "Planner returned malformed provider output: missing candidates"
            ) from exc

        text_parts = [str(part.get("text") or "") for part in parts if isinstance(part, dict)]
        text = "".join(text_parts).strip()
        if not text:
            raise RuntimeError("Planner returned malformed provider output: empty content text")
        return text

    async def generate(
        self, messages: list[dict[str, str]], tools: list[ToolSpec] | None = None
    ) -> BrainResponse:
        del tools  # Planner currently uses JSON text output only.

        payload = {
            "contents": self._convert_messages_to_contents(messages),
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
            },
        }
        api_key = self._resolve_api_key()
        url = f"{self.api_base}/v1beta/models/{self.model}:generateContent"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, params={"key": api_key}, json=payload)
        except httpx.TimeoutException as exc:
            raise RuntimeError(f"Planner timed out contacting Gemini: {exc}") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Gemini provider error: {exc}") from exc

        if response.status_code == 429:
            raise RuntimeError("Gemini rate limit (429)")
        if response.status_code >= 500:
            raise RuntimeError(f"Gemini provider error HTTP {response.status_code}")
        if response.status_code >= 400:
            snippet = response.text[:240]
            raise RuntimeError(f"Gemini provider error HTTP {response.status_code}: {snippet}")

        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(
                "Planner returned malformed provider output: non-JSON response"
            ) from exc

        text = self._extract_text(data)
        return BrainResponse(text=text, tool_calls=[])

    async def capability_test(self) -> dict[str, Any]:
        start = time.time()
        messages = [
            {"role": "system", "content": "You are a strict JSON generator."},
            {
                "role": "user",
                "content": (
                    "Return ONLY JSON with this shape: "
                    '{"title":"string","goal":"string","steps":[{"skill_id":"system.status","args":{}}]}'
                ),
            },
        ]
        note = ""
        raw = ""
        json_ok = False
        parsed: dict[str, Any] | None = None
        try:
            resp = await self.generate(messages)
            raw = (resp.text or "")[:500]
            try:
                parsed = json.loads((resp.text or "").strip())
            except json.JSONDecodeError:
                snippet = " ".join((resp.text or "").strip().split())[:160]
                note = f"malformed_json:{snippet}"
            else:
                steps = parsed.get("steps") if isinstance(parsed, dict) else None
                first_step = steps[0] if isinstance(steps, list) and steps else None
                json_ok = (
                    isinstance(parsed, dict)
                    and isinstance(parsed.get("title"), str)
                    and isinstance(parsed.get("goal"), str)
                    and isinstance(steps, list)
                    and isinstance(first_step, dict)
                    and isinstance(first_step.get("skill_id"), str)
                    and isinstance(first_step.get("args"), dict)
                )
                note = "live call succeeded" if json_ok else "invalid_json: schema_mismatch"
        except Exception as exc:
            msg = str(exc).lower()
            if "timed out" in msg:
                note = "timeout"
            elif "429" in msg or "rate limit" in msg:
                note = "rate_limit"
            elif "http " in msg:
                import re

                match = re.search(r"http\s+(\d{3})", msg)
                note = f"http_error:{match.group(1)}" if match else "provider_error:RuntimeError"
            elif "non-json" in msg or "malformed provider output" in msg:
                snippet = " ".join(str(exc).split())[:160]
                note = f"malformed_json:{snippet}"
            else:
                note = f"provider_error:{type(exc).__name__}"
            raw = raw or " ".join(str(exc).split())[:500]

        return {
            "provider": "gemini",
            "model": self.model,
            "latency_s": round(time.time() - start, 3),
            "json_ok": json_ok,
            "note": note,
            "raw": raw,
            "parsed": parsed,
        }
