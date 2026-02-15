from __future__ import annotations

from typing import Any, Dict

from ..audit import log
from ..models import AppConfig, PlanSimulation, PlanStep, RunResult, SkillManifest
from ..policy import decide
from .arg_normalizer import canonicalize_args
from .execution import generate_job_id, select_runner
from .registry import SkillRegistry


class SkillRunner:
    def __init__(self, registry: SkillRegistry, config: AppConfig | None = None):
        self.registry = registry
        self.config = config or AppConfig()

    def simulate(self, manifest: SkillManifest, args: Dict[str, Any], policy) -> PlanSimulation:
        args = canonicalize_args(manifest.id, args)
        decision = decide(manifest, policy, args=args)
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
            "Blocked by policy"
            if blocked
            else "Approval required before execution"
            if requires_approval
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

    def run(
        self,
        manifest: SkillManifest,
        args: Dict[str, Any],
        policy,
        require_approval_cb=None,
        audit_context: Dict[str, Any] | None = None,
    ) -> RunResult:
        args = canonicalize_args(manifest.id, args)
        decision = decide(manifest, policy, args=args)
        requires = decision.decision in ("ask", "deny") or manifest.risk in ("high",)

        if decision.decision == "deny":
            log({"event": "skill_denied", "skill": manifest.id, "reason": decision.reason})
            return RunResult(ok=False, error=f"Denied by policy: {decision.reason}")

        if requires and require_approval_cb:
            try:
                approved = require_approval_cb(manifest, decision, audit_context=audit_context, args=args)
            except TypeError:
                approved = require_approval_cb(manifest, decision)

            if isinstance(approved, dict) and approved.get("status") == "pending":
                log({"event": "skill_pending_approval", "skill": manifest.id, "reason": decision.reason})
                return RunResult(
                    ok=False,
                    error="Approval required.",
                    data={
                        **approved,
                        "status": "pending_approval",
                        "skill": manifest.id,
                        "reason": decision.reason,
                        "capability": (manifest.capabilities[0] if manifest.capabilities else None),
                    },
                )

            if not approved:
                if audit_context and audit_context.get("mission"):
                    log(
                        {
                            "event": "mission_denied",
                            "mission": audit_context.get("mission"),
                            "step": audit_context.get("step"),
                            "skill": manifest.id,
                            "reason": decision.reason,
                        }
                    )
                log({"event": "skill_rejected", "skill": manifest.id, "reason": decision.reason})
                return RunResult(ok=False, error="User rejected approval.")
            if audit_context and audit_context.get("mission"):
                log(
                    {
                        "event": "mission_approved",
                        "mission": audit_context.get("mission"),
                        "step": audit_context.get("step"),
                        "skill": manifest.id,
                        "reason": decision.reason,
                    }
                )

        fn = self.registry.load_entrypoint(manifest)
        runner = select_runner(manifest)
        job_id = generate_job_id()
        log(
            {
                "event": "skill_start",
                "skill": manifest.id,
                "args": args,
                "reason": decision.reason,
                "runner": runner.runner_name,
                "job_id": job_id,
            }
        )
        try:
            rr = runner.run(manifest=manifest, args=args, fn=fn, cfg=self.config, job_id=job_id)
            log(
                {
                    "event": "skill_done",
                    "skill": manifest.id,
                    "ok": rr.ok,
                    "error": rr.error,
                    "runner": runner.runner_name,
                    "job_id": job_id,
                    "artifacts_dir": rr.data.get("artifacts_dir"),
                }
            )
            return rr
        except Exception as e:
            log({"event": "skill_error", "skill": manifest.id, "error": repr(e), "runner": runner.runner_name, "job_id": job_id})
            return RunResult(ok=False, error=repr(e))
