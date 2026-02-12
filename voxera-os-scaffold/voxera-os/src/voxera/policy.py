from __future__ import annotations

from dataclasses import dataclass
from .models import PolicyApprovals, SkillManifest

CAP_TO_POLICY_FIELD = {
    "network.change": "network_changes",
    "install.packages": "installs",
    "file.delete": "file_delete",
    "apps.open": "open_apps",
    "system.settings": "system_settings",
}

@dataclass
class PolicyDecision:
    decision: str  # allow/ask/deny
    reason: str

def decide(skill: SkillManifest, policy: PolicyApprovals) -> PolicyDecision:
    decision = "allow"
    reasons = []
    for cap in skill.capabilities:
        field = CAP_TO_POLICY_FIELD.get(cap)
        if not field:
            cap_decision = "ask"
        else:
            cap_decision = getattr(policy, field)
        reasons.append(f"{cap} -> {cap_decision}")
        if cap_decision == "deny":
            decision = "deny"
        elif cap_decision == "ask" and decision != "deny":
            decision = "ask"
    return PolicyDecision(decision=decision, reason="; ".join(reasons) if reasons else "no capabilities")
