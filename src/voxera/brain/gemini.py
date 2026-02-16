from __future__ import annotations

"""Gemini adapter scaffold.

Wire this to Google Gemini API when ready.
"""

from typing import Any, Dict, List, Optional
from .base import BrainResponse, ToolSpec

class GeminiBrain:
    def __init__(self, model: str, api_key_ref: Optional[str] = None):
        self.model = model
        self.api_key_ref = api_key_ref

    async def generate(self, messages: List[Dict[str, str]], tools: Optional[List[ToolSpec]] = None) -> BrainResponse:
        raise NotImplementedError(
            "Gemini adapter is scaffolded. Implement generate() in src/voxera/brain/gemini.py."
        )

    async def capability_test(self) -> Dict[str, Any]:
        return {
            "provider": "gemini",
            "model": self.model,
            "note": "Adapter scaffold only. Implement generate() to enable live tests.",
        }
