from __future__ import annotations

import asyncio

import pytest

from voxera.models import AppConfig, WebInvestigationConfig
from voxera.vera import service as vera_service
from voxera.vera.brave_search import BraveSearchClient, WebSearchResult, _parse_brave_web_results


class _DummyResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            req = httpx.Request("GET", "https://api.search.brave.com/res/v1/web/search")
            resp = httpx.Response(self.status_code, request=req)
            raise httpx.HTTPStatusError("boom", request=req, response=resp)

    def json(self) -> dict:
        return self._payload


class _DummyAsyncClient:
    def __init__(self, *, timeout: float):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, *, params, headers):
        self.last_call = {"url": url, "params": params, "headers": headers}
        return _DummyResponse(
            {
                "web": {
                    "results": [
                        {
                            "title": "Brave docs",
                            "url": "https://api.search.brave.com/docs",
                            "description": "API docs",
                        }
                    ]
                }
            }
        )


def test_brave_client_sends_subscription_header(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _CaptureClient(_DummyAsyncClient):
        async def get(self, url, *, params, headers):
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            return await super().get(url, params=params, headers=headers)

    monkeypatch.setenv("BRAVE_API_KEY", "test-key")
    monkeypatch.setattr("voxera.vera.brave_search.httpx.AsyncClient", _CaptureClient)

    client = BraveSearchClient(api_key_ref=None)
    results = asyncio.run(client.search(query="brave search", count=4))

    assert len(results) == 1
    assert captured["url"] == "https://api.search.brave.com/res/v1/web/search"
    assert captured["params"] == {"q": "brave search", "count": "4"}
    headers = captured["headers"]
    assert headers["X-Subscription-Token"] == "test-key"


def test_brave_client_missing_key_fails_honestly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    client = BraveSearchClient(api_key_ref=None)

    with pytest.raises(RuntimeError, match="not configured"):
        asyncio.run(client.search(query="latest horizon 8 release notes"))


def test_brave_response_parser_shapes_results() -> None:
    parsed = _parse_brave_web_results(
        {
            "web": {
                "results": [
                    {
                        "title": "Result A",
                        "url": "https://example.com/a",
                        "description": "Summary A",
                        "age": "2 days ago",
                    },
                    {"title": "", "url": "https://example.com/skip", "description": ""},
                ]
            }
        }
    )

    assert parsed == [
        WebSearchResult(
            title="Result A",
            url="https://example.com/a",
            description="Summary A",
            age="2 days ago",
        )
    ]


def test_vera_informational_query_uses_brave_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = AppConfig(
        web_investigation=WebInvestigationConfig(
            api_key_ref="BRAVE_API_KEY", env_api_key_var="BRAVE_API_KEY"
        )
    )
    monkeypatch.setattr(vera_service, "load_app_config", lambda: cfg)

    calls: list[str] = []

    async def _fake_search(self, *, query: str, count: int = 5):
        calls.append(query)
        return [
            WebSearchResult(
                title="CNN home",
                url="https://cnn.com",
                description="Top stories now",
                age=None,
            )
        ]

    monkeypatch.setattr(BraveSearchClient, "search", _fake_search)

    result = asyncio.run(
        vera_service.generate_vera_reply(turns=[], user_message="What's on cnn right now?")
    )

    assert calls == ["What's on cnn right now?"]
    assert result["status"] == "ok:web_investigation"
    assert "read-only mode" in result["answer"]
    assert "Source: https://cnn.com" in result["answer"]


def test_vera_operational_open_request_skips_brave_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = AppConfig(
        web_investigation=WebInvestigationConfig(
            api_key_ref="BRAVE_API_KEY", env_api_key_var="BRAVE_API_KEY"
        )
    )
    monkeypatch.setattr(vera_service, "load_app_config", lambda: cfg)

    class _FakeBrain:
        async def generate(self, messages, tools):
            class _Resp:
                text = "ok"

            return _Resp()

    monkeypatch.setattr(vera_service, "_create_brain", lambda provider: _FakeBrain())
    cfg.brain = {"primary": type("P", (), {"type": "gemini", "model": "x", "api_key_ref": "k"})()}

    called = {"search": False}

    async def _fake_search(self, *, query: str, count: int = 5):
        called["search"] = True
        return []

    monkeypatch.setattr(BraveSearchClient, "search", _fake_search)

    result = asyncio.run(vera_service.generate_vera_reply(turns=[], user_message="Open cnn.com"))

    assert called["search"] is False
    assert result["status"] == "ok:primary"


def test_vera_web_lane_without_key_is_honest(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = AppConfig(
        web_investigation=WebInvestigationConfig(api_key_ref=None, env_api_key_var="BRAVE_API_KEY")
    )
    monkeypatch.setattr(vera_service, "load_app_config", lambda: cfg)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    result = asyncio.run(
        vera_service.generate_vera_reply(
            turns=[], user_message="Search for the latest Horizon 8 release notes"
        )
    )

    assert result["status"] == "web_investigation_unconfigured"
    assert "not configured" in result["answer"]


@pytest.mark.parametrize(
    "message,expected",
    [
        ("can you find stock information about the big 7?", True),
        ("what are the latest prices for the magnificent seven?", True),
        ("compare Apple and Nvidia stock performance", True),
        ("what's happening with Tesla stock?", True),
        ("show me recent market news about Microsoft", True),
        ("whats the latest world wide news?", True),
        ("what are the latest global headlines?", True),
        ("look up cnn for me", True),
        ("explain what VMware Horizon does", True),
        ("find information about VMware Horizon 8", True),
        ("look into the latest Brave Search API docs", True),
        ("research Nvidia earnings", True),
        ("open cnn.com", False),
        ("open cnn for me", False),
        ("take me to cnn", False),
        ("launch facebook.com", False),
        ("write a file called changelog.txt with release notes", False),
    ],
)
def test_informational_query_classifier(message: str, expected: bool) -> None:
    assert vera_service._is_informational_web_query(message) is expected


def test_vera_finance_query_routes_to_brave(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = AppConfig(
        web_investigation=WebInvestigationConfig(
            api_key_ref="BRAVE_API_KEY", env_api_key_var="BRAVE_API_KEY"
        )
    )
    monkeypatch.setattr(vera_service, "load_app_config", lambda: cfg)

    seen: list[str] = []

    async def _fake_search(self, *, query: str, count: int = 5):
        seen.append(query)
        return [
            WebSearchResult(
                title="Magnificent Seven overview",
                url="https://example.com/mag7",
                description="Market/stock overview",
            )
        ]

    monkeypatch.setattr(BraveSearchClient, "search", _fake_search)

    result = asyncio.run(
        vera_service.generate_vera_reply(
            turns=[],
            user_message="can you find stock information about the big 7?",
        )
    )

    assert seen == ["can you find stock information about the big 7?"]
    assert result["status"] == "ok:web_investigation"
