from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from dataclasses import dataclass
from typing import List, Tuple

from ..audit import log
from ..brain.gemini import GeminiBrain
from ..brain.openai_compat import OpenAICompatBrain
from ..models import AppConfig
from ..skills.arg_normalizer import canonicalize_args
from ..skills.registry import SkillRegistry
from .missions import MissionStep, MissionTemplate


class MissionPlannerError(RuntimeError):
    pass


_MAX_STEPS = 5
_PLANNER_TIMEOUT_SECONDS = 25


@dataclass(frozen=True)
class _BrainCandidate:
    name: str
    brain: object


def _expected_args_for_skill(registry: SkillRegistry, skill_id: str) -> List[str]:
    """Return keyword-compatible argument names for the skill entrypoint."""
    manifest = registry.get(skill_id)
    fn = registry.load_entrypoint(manifest)
    sig = inspect.signature(fn)
    names: List[str] = []
    for p in sig.parameters.values():
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
            names.append(p.name)
    return names


def _normalize_step_args(raw_args: object, expected_args: List[str]) -> dict:
    if not isinstance(raw_args, dict):
        return {}

    if not expected_args:
        return raw_args

    alias_map = {"content": "text", "body": "text"}
    expanded = dict(raw_args)
    for alias, canonical in alias_map.items():
        if canonical in expected_args and canonical not in expanded and alias in expanded:
            expanded[canonical] = expanded[alias]

    normalized = {k: v for k, v in expanded.items() if k in expected_args}
    if normalized:
        return normalized

    # Compatibility fallback: if the planner picked the wrong key for a
    # single-argument skill (e.g. app_name vs name), map the single provided
    # value to the expected parameter.
    if len(expected_args) == 1 and len(raw_args) == 1:
        return {expected_args[0]: next(iter(raw_args.values()))}

    return {}


def _create_brain(provider):
    if provider.type == "openai_compat":
        return OpenAICompatBrain(
            base_url=provider.base_url or "",
            model=provider.model,
            api_key_ref=provider.api_key_ref,
            extra_headers=provider.extra_headers,
        )

    if provider.type == "gemini":
        return GeminiBrain(model=provider.model, api_key_ref=provider.api_key_ref)

    raise MissionPlannerError(f"Unsupported brain provider for mission planning: {provider.type}")


def _build_brain_candidates(cfg: AppConfig) -> List[_BrainCandidate]:
    if not cfg.privacy.cloud_allowed:
        raise MissionPlannerError("Cloud planning is disabled by privacy.cloud_allowed=false")

    if not cfg.brain:
        raise MissionPlannerError("No brain provider is configured. Run 'voxera setup' first.")

    ordered: List[Tuple[str, object]] = []
    for key in ("primary", "fast", "fallback"):
        provider = cfg.brain.get(key)
        if provider:
            ordered.append((key, provider))

    for key, provider in cfg.brain.items():
        if key not in {name for name, _ in ordered}:
            ordered.append((key, provider))

    return [_BrainCandidate(name=name, brain=_create_brain(provider)) for name, provider in ordered]


async def _plan_payload(goal: str, registry: SkillRegistry, brain) -> dict:
    skills = sorted(registry.discover().values(), key=lambda m: m.id)

    skills_block = "\n".join(
        f"- {m.id}: {m.description} (risk={m.risk}, caps={','.join(m.capabilities) or 'none'})"
        for m in skills
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are a strict mission planner for Voxera OS. "
                "Output JSON only with keys: title, goal, notes, steps. "
                "steps must be 1-5 items and each item has skill_id and args object. "
                "Use only skill IDs from the provided catalog."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Goal: {goal}\n"
                f"Skill catalog:\n{skills_block}\n"
                "Return only JSON."
            ),
        },
    ]

    try:
        resp = await asyncio.wait_for(brain.generate(messages), timeout=_PLANNER_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        raise MissionPlannerError(f"Planner timed out after {_PLANNER_TIMEOUT_SECONDS}s") from exc
    raw = resp.text.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MissionPlannerError(f"Planner returned non-JSON output: {raw[:200]}") from exc


async def plan_mission(
    goal: str,
    cfg: AppConfig,
    registry: SkillRegistry,
    *,
    source: str = "cli",
    job_ref: str | None = None,
) -> MissionTemplate:
    candidates = _build_brain_candidates(cfg)
    payload = None
    planner_name = None
    retries = 0
    last_error = None
    plan_id = str(uuid.uuid4())

    log(
        {
            "event": "plan_start",
            "plan_id": plan_id,
            "source": source,
            "goal": goal,
            "job": job_ref,
        }
    )

    for retries, candidate in enumerate(candidates):
        log(
            {
                "event": "planner_selected",
                "plan_id": plan_id,
                "provider": candidate.name,
                "attempt_index": retries,
            }
        )
        try:
            payload = await _plan_payload(goal=goal, registry=registry, brain=candidate.brain)
            planner_name = candidate.name
            break
        except Exception as exc:
            last_error = str(exc)
            log(
                {
                    "event": "planner_fallback",
                    "plan_id": plan_id,
                    "provider": candidate.name,
                    "attempt_index": retries,
                    "error_type": type(exc).__name__,
                    "error": last_error,
                }
            )

    if payload is None or planner_name is None:
        log({"event": "plan_failed", "plan_id": plan_id, "error": last_error or "unknown error"})
        raise MissionPlannerError(f"Planner failed after fallbacks: {last_error or 'unknown error'}")

    if not isinstance(payload, dict):
        log({"event": "plan_failed", "plan_id": plan_id, "error": "invalid JSON payload type"})
        raise MissionPlannerError("Planner returned invalid JSON payload type.")

    allowed_keys = {"title", "goal", "notes", "steps"}
    unknown_keys = set(payload) - allowed_keys
    if unknown_keys:
        keys = ", ".join(sorted(unknown_keys))
        raise MissionPlannerError(f"Planner returned unsupported keys: {keys}")

    steps_json = payload.get("steps")
    if not isinstance(steps_json, list) or not steps_json:
        raise MissionPlannerError("Planner returned no mission steps.")
    if len(steps_json) > _MAX_STEPS:
        raise MissionPlannerError(f"Planner returned too many steps ({len(steps_json)} > {_MAX_STEPS}).")

    skills = sorted(registry.discover().values(), key=lambda m: m.id)
    known_ids = {m.id for m in skills}
    steps: List[MissionStep] = []
    for item in steps_json:
        if not isinstance(item, dict):
            raise MissionPlannerError("Planner returned an invalid step object.")

        skill_id = item.get("skill_id")
        if not isinstance(skill_id, str):
            raise MissionPlannerError("Planner returned a step without a valid skill_id.")
        if skill_id not in known_ids:
            raise MissionPlannerError(f"Planner referenced unknown skill: {skill_id}")
        args = _normalize_step_args(item.get("args"), _expected_args_for_skill(registry, skill_id))
        args = canonicalize_args(skill_id, args)
        steps.append(MissionStep(skill_id=skill_id, args=args))

    title = payload.get("title") or "Cloud Planned Mission"
    notes = payload.get("notes") or f"Generated by configured cloud brain ({planner_name})."

    log({"event": "plan_built", "plan_id": plan_id, "steps": len(steps)})
    return MissionTemplate(id="cloud_planned", title=title, goal=goal, steps=steps, notes=notes)
