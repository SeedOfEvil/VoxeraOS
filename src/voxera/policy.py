from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .core.capability_semantics import (
    CAPABILITY_EFFECT_CLASS,
    capability_semantic,
    manifest_capability_semantics,
)
from .models import PolicyApprovals, SkillManifest


@dataclass
class PolicyDecision:
    decision: str  # allow/ask/deny
    reason: str


def decide(
    skill: SkillManifest, policy: PolicyApprovals, *, args: dict[str, Any] | None = None
) -> PolicyDecision:
    decision = "allow"
    reasons = []
    semantics = manifest_capability_semantics(skill)

    for cap in skill.capabilities:
        semantic = capability_semantic(cap)
        field = semantic.policy_field if semantic is not None else None
        if field:
            cap_decision = getattr(policy, field)
        else:
            cap_decision = "allow" if cap in CAPABILITY_EFFECT_CLASS else "ask"
        reasons.append(f"{cap} -> {cap_decision}")
        if cap_decision == "deny":
            decision = "deny"
        elif cap_decision == "ask" and decision != "deny":
            decision = "ask"

    resource_boundaries = semantics.get("resource_boundaries")
    touches_network = isinstance(resource_boundaries, dict) and bool(
        resource_boundaries.get("network")
    )

    if touches_network:
        reasons.append("skill semantics network boundary=true")
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
