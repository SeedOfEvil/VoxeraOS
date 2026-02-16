"""Gemini adapter scaffold.

Wire this to Google Gemini API when ready.
"""

from __future__ import annotations

from typing import Any

from .base import BrainResponse, ToolSpec


class GeminiBrain:
    def __init__(self, model: str, api_key_ref: str | None = None):
        self.model = model
        self.api_key_ref = api_key_ref

    async def generate(
        self, messages: list[dict[str, str]], tools: list[ToolSpec] | None = None
    ) -> BrainResponse:
        raise NotImplementedError(
            "Gemini adapter is scaffolded. Implement generate() in src/voxera/brain/gemini.py."
        )

    async def capability_test(self) -> dict[str, Any]:
        return {
            "provider": "gemini",
            "model": self.model,
            "note": "Adapter scaffold only. Implement generate() to enable live tests.",
        }
