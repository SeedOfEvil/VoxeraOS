from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from ..secrets import get_secret

BRAVE_WEB_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


@dataclass(frozen=True)
class WebSearchResult:
    title: str
    url: str
    description: str
    age: str | None = None


class BraveSearchClient:
    def __init__(
        self,
        *,
        api_key_ref: str | None,
        env_api_key_var: str = "BRAVE_API_KEY",
        timeout_s: float = 15.0,
    ) -> None:
        self.api_key_ref = (api_key_ref or "").strip() or None
        self.env_api_key_var = (env_api_key_var or "").strip() or "BRAVE_API_KEY"
        self.timeout_s = timeout_s

    def _resolve_api_key(self) -> str:
        if self.api_key_ref:
            key_or_ref = get_secret(self.api_key_ref) or self.api_key_ref
            if key_or_ref.startswith(("keyring:", "file:")):
                ref_name = key_or_ref.split(":", 1)[1]
                resolved = get_secret(ref_name)
                if resolved:
                    return resolved
            if key_or_ref.strip():
                return key_or_ref

        import os

        env_value = (os.getenv(self.env_api_key_var) or "").strip()
        if env_value:
            return env_value
        raise RuntimeError("Brave web investigation is not configured (missing API key)")

    def _headers(self) -> dict[str, str]:
        key = self._resolve_api_key()
        return {
            "Accept": "application/json",
            "X-Subscription-Token": key,
        }

    async def search(self, *, query: str, count: int = 5) -> list[WebSearchResult]:
        if not query.strip():
            return []
        clamped_count = max(1, min(int(count), 10))
        params = {"q": query.strip(), "count": str(clamped_count)}

        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                response = await client.get(
                    BRAVE_WEB_SEARCH_URL,
                    params=params,
                    headers=self._headers(),
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in {401, 403}:
                raise RuntimeError("Brave web investigation credentials were rejected") from exc
            raise RuntimeError(f"Brave web investigation request failed (HTTP {code})") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(
                "Brave web investigation request failed due to network error"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Brave web investigation returned invalid JSON") from exc

        return _parse_brave_web_results(payload)


def _parse_brave_web_results(payload: dict[str, Any]) -> list[WebSearchResult]:
    if not isinstance(payload, dict):
        return []
    web = payload.get("web")
    if not isinstance(web, dict):
        return []
    entries = web.get("results")
    if not isinstance(entries, list):
        return []

    out: list[WebSearchResult] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        description = str(item.get("description") or "").strip()
        age_raw = str(item.get("age") or "").strip()
        if not title or not url:
            continue
        out.append(
            WebSearchResult(
                title=title,
                url=url,
                description=description,
                age=age_raw or None,
            )
        )
    return out
