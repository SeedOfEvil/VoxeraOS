from __future__ import annotations

import asyncio

from voxera.brain.openai_compat import OpenAICompatBrain


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": "ok", "tool_calls": []}}]}


class _CaptureClient:
    def __init__(self, sink: dict):
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url: str, *, headers: dict[str, str], json: dict):
        self._sink["url"] = url
        self._sink["headers"] = dict(headers)
        self._sink["json"] = dict(json)
        return _FakeResponse()


def _run_generate(brain: OpenAICompatBrain, monkeypatch, sink: dict) -> None:
    monkeypatch.setattr(
        "voxera.brain.openai_compat.httpx.AsyncClient",
        lambda **kwargs: _CaptureClient(sink),
    )
    monkeypatch.setattr(OpenAICompatBrain, "_resolve_api_key", lambda self: "test-key")
    asyncio.run(brain.generate(messages=[{"role": "user", "content": "hello"}]))


def test_openrouter_default_attribution_headers_applied(monkeypatch):
    monkeypatch.delenv("VOXERA_APP_URL", raising=False)
    monkeypatch.delenv("VOXERA_APP_TITLE", raising=False)
    sink: dict = {}
    brain = OpenAICompatBrain(
        base_url="https://openrouter.ai/api/v1",
        model="openai/gpt-4o-mini",
        api_key_ref="OPENROUTER_API_KEY",
    )

    _run_generate(brain, monkeypatch, sink)

    headers = sink["headers"]
    assert headers["HTTP-Referer"] == "https://voxeraos.ca"
    assert headers["X-OpenRouter-Title"] == "VoxeraOS"
    assert headers["X-Title"] == "VoxeraOS"


def test_openrouter_user_override_headers_respected(monkeypatch):
    sink: dict = {}
    brain = OpenAICompatBrain(
        base_url="https://openrouter.ai/api/v1",
        model="openai/gpt-4o-mini",
        api_key_ref="OPENROUTER_API_KEY",
        extra_headers={"HTTP-Referer": "https://example.com", "X-OpenRouter-Title": "MyApp"},
    )

    _run_generate(brain, monkeypatch, sink)

    headers = sink["headers"]
    assert headers["HTTP-Referer"] == "https://example.com"
    assert headers["X-OpenRouter-Title"] == "MyApp"
    assert headers["X-Title"] == "MyApp"


def test_non_openrouter_no_attribution_headers(monkeypatch):
    sink: dict = {}
    brain = OpenAICompatBrain(
        base_url="https://api.openai.com/v1", model="gpt-4o-mini", api_key_ref="OPENAI_API_KEY"
    )

    _run_generate(brain, monkeypatch, sink)

    headers = sink["headers"]
    assert "HTTP-Referer" not in headers
    assert "X-OpenRouter-Title" not in headers
    assert "X-Title" not in headers
