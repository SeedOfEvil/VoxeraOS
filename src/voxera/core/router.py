from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class Route:
    lane: Literal["local", "cloud"]
    reason: str

def route_request(text: str, privacy_cloud_allowed: bool) -> Route:
    risky_words = ["install", "update", "network", "vpn", "firewall", "delete", "remove", "sudo"]
    complex_words = ["generate", "scaffold", "refactor", "debug", "workflow", "agent", "plan"]
    t = text.lower()
    if any(w in t for w in risky_words):
        return Route("local", "risky action -> prefer local planning (ask approvals)")
    if any(w in t for w in complex_words) and privacy_cloud_allowed:
        return Route("cloud", "complex request and cloud allowed")
    return Route("local", "default local lane")
