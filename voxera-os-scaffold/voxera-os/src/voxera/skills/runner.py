from __future__ import annotations

from typing import Any, Dict
from ..models import PlanSimulation, PlanStep, RunResult, SkillManifest
from ..policy import decide
from ..audit import log
from .registry import SkillRegistry

class SkillRunner:
    def __init__(self, registry: SkillRegistry):
        self.registry = registry

    def simulate(self, manifest: SkillManifest, args: Dict[str, Any], policy) -> PlanSimulation:
        decision = decide(manifest, policy)
        requires_approval = decision.decision == "ask" or manifest.risk == "high"
        blocked = decision.decision == "deny"

        step = PlanStep(
            action="Run skill",
            skill_id=manifest.id,
            args=args,
            requires_approval=requires_approval,
            risk=manifest.risk,
            policy_decision=decision.decision,
            reason=decision.reason,
        )

        summary = (
            "Blocked by policy" if blocked
            else "Approval required before execution" if requires_approval
            else "Safe to execute"
        )

        return PlanSimulation(
            title=f"Dry-run: {manifest.name}",
            goal=manifest.description,
            steps=[step],
            approvals_required=1 if requires_approval else 0,
            blocked=blocked,
            summary=summary,
        )

    def run(self, manifest: SkillManifest, args: Dict[str, Any], policy, require_approval_cb=None) -> RunResult:
        decision = decide(manifest, policy)
        requires = decision.decision in ("ask", "deny") or manifest.risk in ("high",)

        if decision.decision == "deny":
            log({"event": "skill_denied", "skill": manifest.id, "reason": decision.reason})
            return RunResult(ok=False, error=f"Denied by policy: {decision.reason}")

        if requires and require_approval_cb:
            approved = require_approval_cb(manifest, decision)
            if not approved:
                log({"event": "skill_rejected", "skill": manifest.id, "reason": decision.reason})
                return RunResult(ok=False, error="User rejected approval.")

        fn = self.registry.load_entrypoint(manifest)
        log({"event": "skill_start", "skill": manifest.id, "args": args, "reason": decision.reason})
        try:
            out = fn(**args)
            rr = out if isinstance(out, RunResult) else RunResult(ok=True, output=str(out))
            log({"event": "skill_done", "skill": manifest.id, "ok": rr.ok, "error": rr.error})
            return rr
        except Exception as e:
            log({"event": "skill_error", "skill": manifest.id, "error": repr(e)})
            return RunResult(ok=False, error=repr(e))
