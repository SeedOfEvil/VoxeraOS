from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

@dataclass
class ToolSpec:
    name: str
    description: str
    schema: Dict[str, Any]

@dataclass
class BrainResponse:
    text: str
    tool_calls: List[Dict[str, Any]]

class Brain(Protocol):
    async def generate(self, messages: List[Dict[str, str]], tools: Optional[List[ToolSpec]] = None) -> BrainResponse:
        ...
    async def capability_test(self) -> Dict[str, Any]:
        ...
