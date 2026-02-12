from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
import httpx
from .base import BrainResponse, ToolSpec
from ..secrets import get_secret

class OpenAICompatBrain:
    """Works with any OpenAI-compatible endpoint (local or cloud)."""

    def __init__(self, base_url: str, model: str, api_key_ref: Optional[str] = None, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_ref = api_key_ref
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        hdr = {"Content-Type": "application/json"}
        if self.api_key_ref:
            key = get_secret(self.api_key_ref) or self.api_key_ref
            if key and key.startswith(("keyring:", "file:")):
                key = get_secret(key.split(":", 1)[1])
            if key:
                hdr["Authorization"] = f"Bearer {key}"
        return hdr

    async def generate(self, messages: List[Dict[str, str]], tools: Optional[List[ToolSpec]] = None) -> BrainResponse:
        payload: Dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.schema}}
                for t in tools
            ]
            payload["tool_choice"] = "auto"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(f"{self.base_url}/chat/completions", headers=self._headers(), json=payload)
            r.raise_for_status()
            data = r.json()

        choice = data["choices"][0]["message"]
        text = choice.get("content") or ""
        tool_calls = choice.get("tool_calls") or []
        return BrainResponse(text=text, tool_calls=tool_calls)

    async def capability_test(self) -> Dict[str, Any]:
        import time
        start = time.time()
        messages = [
            {"role": "system", "content": "You are a strict JSON generator."},
            {"role": "user", "content": "Return ONLY JSON with keys: ok (bool), model (string), steps (array of 3 strings)."},
        ]
        resp = await self.generate(messages)
        elapsed = time.time() - start
        ok = False
        parsed = None
        try:
            parsed = json.loads(resp.text.strip())
            ok = isinstance(parsed, dict) and "ok" in parsed and "steps" in parsed
        except Exception:
            ok = False
        return {
            "provider": "openai_compat",
            "model": self.model,
            "base_url": self.base_url,
            "latency_s": round(elapsed, 3),
            "json_ok": ok,
            "raw": resp.text[:500],
            "parsed": parsed,
        }
