from __future__ import annotations

import json
import os
from typing import Any

import httpx

from ..secrets import get_secret
from .base import BrainResponse, ToolSpec
from .json_recovery import recover_json_object

DEFAULT_APP_URL = "https://voxeraos.ca"
DEFAULT_APP_TITLE = "VoxeraOS"


class OpenAICompatBrain:
    """Works with any OpenAI-compatible endpoint (local or cloud)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key_ref: str | None = None,
        timeout: float = 60.0,
        extra_headers: dict[str, str] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_ref = api_key_ref
        self.timeout = timeout
        self.extra_headers = extra_headers or {}

    def _resolve_api_key(self) -> str:
        if not self.api_key_ref:
            raise RuntimeError("OpenAI-compatible API key reference is required")
        key_or_ref = get_secret(self.api_key_ref) or self.api_key_ref
        if key_or_ref.startswith(("keyring:", "file:")):
            ref_name = key_or_ref.split(":", 1)[1]
            resolved = get_secret(ref_name)
            if resolved is None:
                raise RuntimeError("OpenAI-compatible API key secret is missing")
            key_or_ref = resolved
        if not key_or_ref.strip():
            raise RuntimeError("OpenAI-compatible API key is missing or empty")
        return key_or_ref

    def _headers(self) -> dict[str, str]:
        hdr = {"Content-Type": "application/json"}
        for k, v in self.extra_headers.items():
            if v and k not in {"Authorization", "Content-Type"}:
                hdr[k] = v

        if self.api_key_ref:
            hdr["Authorization"] = f"Bearer {self._resolve_api_key()}"

        if self._is_openrouter_request():
            app_url = os.getenv("VOXERA_APP_URL") or DEFAULT_APP_URL
            app_title = os.getenv("VOXERA_APP_TITLE") or DEFAULT_APP_TITLE

            if "HTTP-Referer" not in hdr:
                hdr["HTTP-Referer"] = app_url

            openrouter_title = hdr.get("X-OpenRouter-Title")
            x_title = hdr.get("X-Title")
            if openrouter_title is None and x_title is None:
                hdr["X-OpenRouter-Title"] = app_title
                hdr["X-Title"] = app_title
            elif openrouter_title is None and x_title is not None:
                hdr["X-OpenRouter-Title"] = x_title
            elif openrouter_title is not None and x_title is None:
                hdr["X-Title"] = openrouter_title

        return hdr

    def _is_openrouter_request(self) -> bool:
        return "openrouter.ai" in self.base_url.rstrip("/").lower()

    async def generate(
        self, messages: list[dict[str, str]], tools: list[ToolSpec] | None = None
    ) -> BrainResponse:
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.schema,
                    },
                }
                for t in tools
            ]
            payload["tool_choice"] = "auto"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(
                f"{self.base_url}/chat/completions", headers=self._headers(), json=payload
            )
            r.raise_for_status()
            data = r.json()

        choice = data["choices"][0]["message"]
        text = choice.get("content") or ""
        tool_calls = choice.get("tool_calls") or []
        return BrainResponse(text=text, tool_calls=tool_calls)

    async def generate_stream(
        self, messages: list[dict[str, str]], tools: list[ToolSpec] | None = None
    ):
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.schema,
                    },
                }
                for t in tools
            ]
            payload["tool_choice"] = "auto"

        async with (
            httpx.AsyncClient(timeout=self.timeout) as client,
            client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            ) as response,
        ):
            response.raise_for_status()
            async for raw_line in response.aiter_lines():
                line = (raw_line or "").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    payload_obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = payload_obj.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue
                delta = choices[0].get("delta")
                if not isinstance(delta, dict):
                    continue
                chunk = str(delta.get("content") or "")
                if chunk:
                    yield chunk

    async def capability_test(self) -> dict[str, Any]:
        import time

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
        parsed = None
        try:
            resp = await self.generate(messages)
            raw = (resp.text or "")[:500]
            parsed, recovery_note = recover_json_object(resp.text or "")
            if parsed is None:
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
                if not json_ok:
                    note = "invalid_json: schema_mismatch"
                elif recovery_note:
                    note = recovery_note
                else:
                    note = "live call succeeded"
        except httpx.TimeoutException:
            note = "timeout"
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            note = "rate_limit" if status == 429 else f"http_error:{status}"
            raw = raw or " ".join(exc.response.text.split())[:500]
        except httpx.HTTPError:
            note = "provider_error:HTTPError"
        except Exception as exc:
            note = f"provider_error:{type(exc).__name__}"

        return {
            "provider": "openai_compat",
            "model": self.model,
            "base_url": self.base_url,
            "latency_s": round(time.time() - start, 3),
            "json_ok": json_ok,
            "note": note,
            "raw": raw,
            "parsed": parsed,
        }
