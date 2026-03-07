from __future__ import annotations

from typing import Any, Literal, cast

from ..audit import log
from ..models import AppConfig, PlanSimulation, PlanStep, RunResult, SkillManifest
from ..policy import CAPABILITY_EFFECT_CLASS, decide
from .arg_normalizer import canonicalize_args
from .execution import generate_job_id, sanitize_audit_value, select_runner
from .registry import SkillRegistry
from .result_contract import SKILL_RESULT_KEY, build_skill_result

_PolicyDecisionLiteral = Literal["allow", "ask", "deny"]


class CapabilityEnforcementResult:
    def __init__(
        self,
        *,
        allowed: bool,
        needs_approval: bool,
        denied: bool,
        reason: str,
        reason_class: str,
        decision: _PolicyDecisionLiteral,
        capabilities: list[str],
        effect_classes: list[str],
    ) -> None:
        self.allowed = allowed
        self.needs_approval = needs_approval
        self.denied = denied
        self.reason = reason
        self.reason_class = reason_class
        self.decision = decision
        self.capabilities = capabilities
        self.effect_classes = effect_classes


def _normalize_policy_decision(value: str) -> _PolicyDecisionLiteral:
    if value not in {"allow", "ask", "deny"}:
        raise ValueError(f"Unexpected policy decision: {value}")
    return cast(_PolicyDecisionLiteral, value)


def _enforce_runtime_capabilities(
    manifest: SkillManifest,
    *,
    policy,
    args: dict[str, Any],
) -> CapabilityEnforcementResult:
    raw_caps = manifest.capabilities
    if not isinstance(raw_caps, list) or len(raw_caps) == 0:
        return CapabilityEnforcementResult(
            allowed=False,
            needs_approval=False,
            denied=True,
            reason="Skill manifest missing required capability declarations.",
            reason_class="missing_capability_metadata",
            decision="deny",
            capabilities=[],
            effect_classes=[],
        )

    normalized_caps: list[str] = []
    for cap in raw_caps:
        cap_str = str(cap).strip()
        if not cap_str:
            return CapabilityEnforcementResult(
                allowed=False,
                needs_approval=False,
                denied=True,
                reason="Skill manifest capability declarations are malformed.",
                reason_class="malformed_capability_metadata",
                decision="deny",
                capabilities=[],
                effect_classes=[],
            )
        normalized_caps.append(cap_str)

    if len(set(normalized_caps)) != len(normalized_caps):
        return CapabilityEnforcementResult(
            allowed=False,
            needs_approval=False,
            denied=True,
            reason="Skill manifest capability declarations are ambiguous (duplicates present).",
            reason_class="ambiguous_capability_metadata",
            decision="deny",
            capabilities=sorted(set(normalized_caps)),
            effect_classes=[],
        )

    unknown_caps = sorted(cap for cap in normalized_caps if cap not in CAPABILITY_EFFECT_CLASS)
    if unknown_caps:
        return CapabilityEnforcementResult(
            allowed=False,
            needs_approval=False,
            denied=True,
            reason=f"Skill manifest declares unknown capability metadata: {', '.join(unknown_caps)}.",
            reason_class="unknown_capability_metadata",
            decision="deny",
            capabilities=sorted(normalized_caps),
            effect_classes=[],
        )

    effect_classes = sorted({CAPABILITY_EFFECT_CLASS[cap] for cap in normalized_caps})
    policy_result = decide(manifest, policy, args=args)
    policy_decision = _normalize_policy_decision(policy_result.decision)

    return CapabilityEnforcementResult(
        allowed=policy_decision == "allow",
        needs_approval=policy_decision == "ask" or manifest.risk == "high",
        denied=policy_decision == "deny",
        reason=policy_result.reason,
        reason_class="policy_decision",
        decision=policy_decision,
        capabilities=sorted(normalized_caps),
        effect_classes=effect_classes,
    )


class SkillRunner:
    def __init__(self, registry: SkillRegistry, config: AppConfig | None = None):
        self.registry = registry
        self.config = config or AppConfig()

    def simulate(self, manifest: SkillManifest, args: dict[str, Any], policy) -> PlanSimulation:
        args = canonicalize_args(manifest.id, args)
        decision = decide(manifest, policy, args=args)
        requires_approval = decision.decision == "ask" or manifest.risk == "high"
        blocked = decision.decision == "deny"

        policy_decision = _normalize_policy_decision(decision.decision)

        step = PlanStep(
            action="Run skill",
            skill_id=manifest.id,
            args=args,
            requires_approval=requires_approval,
            capability=sorted(manifest.capabilities)[0] if manifest.capabilities else None,
            risk=manifest.risk,
            policy_decision=policy_decision,
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
        args: dict[str, Any],
        policy,
        require_approval_cb=None,
        audit_context: dict[str, Any] | None = None,
    ) -> RunResult:
        args = canonicalize_args(manifest.id, args)
        enforcement = _enforce_runtime_capabilities(manifest, policy=policy, args=args)
        policy_result = decide(manifest, policy, args=args)
        requires = enforcement.needs_approval

        if enforcement.denied:
            log(
                {
                    "event": "skill_denied",
                    "skill": manifest.id,
                    "reason": enforcement.reason,
                    "reason_class": enforcement.reason_class,
                    "required_capabilities": enforcement.capabilities,
                    "required_effect_classes": enforcement.effect_classes,
                }
            )
            return RunResult(
                ok=False,
                error=f"Denied by policy: {enforcement.reason}",
                data={
                    "status": "blocked",
                    "reason": enforcement.reason,
                    "blocked_reason_class": enforcement.reason_class,
                    "capabilities": enforcement.capabilities,
                    "effect_classes": enforcement.effect_classes,
                    "policy_decision": enforcement.decision,
                    SKILL_RESULT_KEY: build_skill_result(
                        summary="Step blocked before execution by runtime capability enforcement",
                        machine_payload={
                            "reason": enforcement.reason,
                            "reason_class": enforcement.reason_class,
                            "required_capabilities": enforcement.capabilities,
                            "required_effect_classes": enforcement.effect_classes,
                            "policy_decision": enforcement.decision,
                        },
                        operator_note=enforcement.reason,
                        next_action_hint=(
                            "fix_skill_manifest"
                            if enforcement.reason_class != "policy_decision"
                            else "request_policy_change_or_review"
                        ),
                        retryable=False,
                        error_class=enforcement.reason_class,
                    ),
                },
            )

        if requires and require_approval_cb:
            try:
                approved = require_approval_cb(
                    manifest,
                    policy_result,
                    audit_context=audit_context,
                    args=args,
                )
            except TypeError:
                approved = require_approval_cb(manifest, policy_result)

            if isinstance(approved, dict) and approved.get("status") == "pending":
                log(
                    {
                        "event": "skill_pending_approval",
                        "skill": manifest.id,
                        "reason": enforcement.reason,
                    }
                )
                return RunResult(
                    ok=False,
                    error="Approval required.",
                    data={
                        **approved,
                        "status": "pending_approval",
                        "skill": manifest.id,
                        "reason": enforcement.reason,
                        "capability": enforcement.capabilities[0]
                        if enforcement.capabilities
                        else None,
                        "capabilities": enforcement.capabilities,
                        "effect_classes": enforcement.effect_classes,
                        "policy_decision": enforcement.decision,
                        SKILL_RESULT_KEY: build_skill_result(
                            summary="Step blocked pending operator approval",
                            machine_payload={
                                "reason": enforcement.reason,
                                "required_capabilities": enforcement.capabilities,
                                "required_effect_classes": enforcement.effect_classes,
                                "policy_decision": enforcement.decision,
                            },
                            operator_note="Approval is required before this step may execute.",
                            next_action_hint="await_operator_approval",
                            retryable=True,
                            error_class="approval_required",
                        ),
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
                            "reason": enforcement.reason,
                        }
                    )
                log({"event": "skill_rejected", "skill": manifest.id, "reason": enforcement.reason})
                return RunResult(
                    ok=False,
                    error="User rejected approval.",
                    data={
                        "status": "blocked",
                        "reason": "User rejected approval.",
                        "blocked_reason_class": "approval_rejected",
                        "capabilities": enforcement.capabilities,
                        "effect_classes": enforcement.effect_classes,
                        SKILL_RESULT_KEY: build_skill_result(
                            summary="Step blocked because operator denied approval",
                            machine_payload={
                                "reason": "User rejected approval.",
                                "required_capabilities": enforcement.capabilities,
                                "required_effect_classes": enforcement.effect_classes,
                            },
                            operator_note="Operator denied approval for this step.",
                            next_action_hint="operator_review_required",
                            retryable=False,
                            error_class="approval_rejected",
                        ),
                    },
                )
            if audit_context and audit_context.get("mission"):
                log(
                    {
                        "event": "mission_approved",
                        "mission": audit_context.get("mission"),
                        "step": audit_context.get("step"),
                        "skill": manifest.id,
                        "reason": enforcement.reason,
                    }
                )

        fn = self.registry.load_entrypoint(manifest)
        runner = select_runner(manifest)
        job_id = generate_job_id()
        log(
            {
                "event": "skill_start",
                "skill": manifest.id,
                "args": sanitize_audit_value(args),
                "reason": enforcement.reason,
                "runner": runner.runner_name,
                "job_id": job_id,
                "required_capabilities": enforcement.capabilities,
                "required_effect_classes": enforcement.effect_classes,
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
            log(
                {
                    "event": "skill_error",
                    "skill": manifest.id,
                    "error": repr(e),
                    "runner": runner.runner_name,
                    "job_id": job_id,
                }
            )
            return RunResult(ok=False, error=repr(e))
