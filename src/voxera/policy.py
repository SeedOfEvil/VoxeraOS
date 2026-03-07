from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import PolicyApprovals, SkillManifest

CAP_TO_POLICY_FIELD = {
    "network.change": "network_changes",
    "install.packages": "installs",
    "file.delete": "file_delete",
    "apps.open": "open_apps",
    "system.settings": "system_settings",
}

# Canonical capability catalog used by runtime enforcement.
#
# effect_class is intentionally coarse (read | write | execute) so operator surfaces can
# explain impact succinctly while still routing concrete capabilities through policy fields.
CAPABILITY_EFFECT_CLASS = {
    "apps.open": "execute",
    "network.change": "write",
    "install.packages": "write",
    "file.delete": "write",
    "system.settings": "write",
    "state.read": "read",
    "files.read": "read",
    "files.write": "write",
    "clipboard.read": "read",
    "clipboard.write": "write",
    "window.read": "read",
    "sandbox.exec": "execute",
}


@dataclass
class PolicyDecision:
    decision: str  # allow/ask/deny
    reason: str


def decide(
    skill: SkillManifest, policy: PolicyApprovals, *, args: dict[str, Any] | None = None
) -> PolicyDecision:
    decision = "allow"
    reasons = []
    for cap in skill.capabilities:
        field = CAP_TO_POLICY_FIELD.get(cap)
        if field:
            cap_decision = getattr(policy, field)
        else:
            cap_decision = "allow" if cap in CAPABILITY_EFFECT_CLASS else "ask"
        reasons.append(f"{cap} -> {cap_decision}")
        if cap_decision == "deny":
            decision = "deny"
        elif cap_decision == "ask" and decision != "deny":
            decision = "ask"

    if skill.needs_network:
        reasons.append("skill metadata needs_network=true")
        if decision == "allow":
            decision = "ask"

    if skill.fs_scope == "broader":
        reasons.append("skill metadata fs_scope=broader")
        if decision == "allow":
            decision = "ask"

    if skill.risk == "high":
        reasons.append("skill metadata risk=high")
        if decision == "allow":
            decision = "ask"

    if skill.exec_mode == "sandbox":
        requested_network = bool((args or {}).get("network", False))
        reasons.append(f"runs in SANDBOX (network={'on' if requested_network else 'off'})")
        if requested_network and decision == "allow":
            decision = "ask"
            reasons.append("sandbox network requested => approval required")

    return PolicyDecision(
        decision=decision, reason="; ".join(reasons) if reasons else "no capabilities"
    )
