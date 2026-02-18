from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]

@dataclass
class BrainResponse:
    text: str
    tool_calls: list[dict[str, Any]]

class Brain(Protocol):
    async def generate(self, messages: list[dict[str, str]], tools: list[ToolSpec] | None = None) -> BrainResponse:
        ...
    async def capability_test(self) -> dict[str, Any]:
        ...
