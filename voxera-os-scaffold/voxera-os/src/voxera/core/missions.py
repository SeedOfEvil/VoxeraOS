from __future__ import annotations

"""Mission templates.

Observe -> Suggest -> Simulate -> Approve -> Apply -> Verify -> Remember
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

@dataclass
class Mission:
    title: str
    goal: str
    risk: Literal["low", "medium", "high"] = "low"
    steps: List[Dict[str, Any]] = field(default_factory=list)
    notes: Optional[str] = None
