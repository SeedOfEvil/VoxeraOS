from __future__ import annotations

import asyncio
import inspect
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

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


def _expected_args_for_skill(registry: SkillRegistry, skill_id: str) -> list[str]:
    """Return keyword-compatible argument names for the skill entrypoint."""
    manifest = registry.get(skill_id)
    fn = registry.load_entrypoint(manifest)
    sig = inspect.signature(fn)
    names: list[str] = []
    for p in sig.parameters.values():
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
            names.append(p.name)
    return names


def _normalize_step_args(raw_args: object, expected_args: list[str]) -> dict:
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


def _strip_matching_quotes(value: str) -> str:
    text = value.strip()
    pairs = (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"))
    for start, end in pairs:
        if text.startswith(start) and text.endswith(end) and len(text) >= 2:
            return text[1:-1].strip()
    return text


def _looks_like_append(goal: str) -> bool:
    lowered = goal.lower()
    return bool(re.search(r"\bappend(?:ing|ed)?\b", lowered))


def _is_safe_notes_path(raw_path: str) -> bool:
    try:
        resolved = Path(raw_path).expanduser().resolve(strict=False)
        allowed = (Path.home() / "VoxeraOS" / "notes").resolve(strict=False)
    except Exception:
        return False
    return resolved == allowed or allowed in resolved.parents


def _goal_implies_allowed_notes_directory(goal: str) -> bool:
    lowered = goal.lower()
    hints = (
        "allowed notes directory",
        "under notes directory",
        "under the notes directory",
        "in notes directory",
        "in the notes directory",
        "under ~/voxeraos/notes",
    )
    return any(hint in lowered for hint in hints)


def _goal_mentions_explicit_path(goal: str) -> bool:
    text = goal.lower()
    return any(token in text for token in ("~/", "/", "\\", ".txt", ".md", "path:"))


def _extract_allowed_notes_write_args(goal: str) -> dict[str, str] | None:
    if not _goal_implies_allowed_notes_directory(goal) or _goal_mentions_explicit_path(goal):
        return None

    quoted_text_match = re.search(
        r"write the text\s+(['\"])(.+?)\1\s+to a notes file",
        goal.strip(),
        flags=re.IGNORECASE | re.DOTALL,
    )
    if quoted_text_match:
        text = quoted_text_match.group(2).strip() or "ok"
    else:
        m = re.search(
            r"(?:saying|that\s+says?)\s*:?\s*(.+)$", goal.strip(), flags=re.IGNORECASE | re.DOTALL
        )
        text = _strip_matching_quotes((m.group(1) if m else "ok").strip()).strip() or "ok"
    return {
        "path": "ok.txt",
        "text": text,
        "mode": "append" if _looks_like_append(goal) else "overwrite",
    }


def _parse_planner_json(raw_text: str) -> dict:
    stripped = raw_text.strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise MissionPlannerError(f"Planner returned non-JSON output: {stripped[:200]}") from exc

    if not isinstance(parsed, dict):
        raise MissionPlannerError("Planner must return a JSON object.")
    return parsed


def _goal_requests_file_write(goal: str) -> bool:
    if _extract_simple_write_args(goal) is not None:
        return True
    lowered = goal.lower()
    write_verbs = ("write", "create", "append", "save", "update")
    file_nouns = ("note", "file", ".txt", ".md", "markdown", "document")
    return any(v in lowered for v in write_verbs) and any(n in lowered for n in file_nouns)


_SANDBOX_DISALLOWED_TOOLING = ("xdotool", "wmctrl", "xprop", "gdbus", "curl", "wget")


def _goal_explicitly_requests_shell_commands(goal: str) -> bool:
    lowered = goal.lower()
    explicit_patterns = (
        r"\b(run|execute|use|call)\b.{0,32}\b(command|shell|bash|terminal|sandbox\.exec)\b",
        r"\b(command|shell|bash|terminal|sandbox\.exec)\b.{0,32}\b(run|execute|use|call)\b",
        r"\b(run|execute)\s+this\b",
    )
    if any(re.search(pattern, lowered, flags=re.DOTALL) for pattern in explicit_patterns):
        return True
    return any(tool in lowered for tool in _SANDBOX_DISALLOWED_TOOLING)


def _sandbox_step_uses_disallowed_tooling(step: MissionStep) -> bool:
    if step.skill_id != "sandbox.exec":
        return False
    command = step.args.get("command")
    if not isinstance(command, list) or not command:
        return False
    joined = " ".join(str(part).lower() for part in command)
    return any(tool in joined for tool in _SANDBOX_DISALLOWED_TOOLING)


def _rewrite_non_explicit_sandbox_steps(goal: str, steps: list[MissionStep]) -> list[MissionStep]:
    if _goal_explicitly_requests_shell_commands(goal):
        return steps

    rewritten: list[MissionStep] = []
    for step in steps:
        if not _sandbox_step_uses_disallowed_tooling(step):
            rewritten.append(step)
            continue

        command = step.args.get("command")
        joined = " ".join(str(part) for part in command) if isinstance(command, list) else ""
        message = "Please manually confirm the expected result for this verification step."
        m = re.search(r"grep\s+['\"]([^'\"]+)['\"]", joined)
        if m:
            message = (
                f"Please confirm the page/window title contains '{m.group(1)}' (manual check)."
            )
        rewritten.append(MissionStep(skill_id="clipboard.copy", args={"text": message}))

    return rewritten


def _rewrite_non_explicit_file_writes(goal: str, steps: list[MissionStep]) -> list[MissionStep]:
    if _goal_requests_file_write(goal):
        return steps

    rewritten: list[MissionStep] = []
    for step in steps:
        if step.skill_id != "files.write_text":
            rewritten.append(step)
            continue

        text = step.args.get("text")
        if isinstance(text, str) and text.strip():
            message = text.strip()
        else:
            path = step.args.get("path")
            if isinstance(path, str) and path.strip():
                message = (
                    f"Planner requested writing to {path}; switched to clipboard.copy for safety."
                )
            else:
                message = "Planner requested a file write; switched to clipboard.copy for safety."

        rewritten.append(MissionStep(skill_id="clipboard.copy", args={"text": message}))

    return rewritten


def _extract_simple_write_args(goal: str) -> dict[str, str] | None:
    text = goal.strip()
    if not text:
        return None

    patterns = [
        re.compile(
            r"""
            ^\s*(?:please\s+)?write\s+(?:a\s+)?(?:note|file)\s+(?:to|at)\s+
            (?P<path>.+?)\s+
            (?:saying|that\s+says?)\s*:?\s*
            (?P<text>.+?)\s*$
            """,
            flags=re.IGNORECASE | re.DOTALL | re.VERBOSE,
        ),
        re.compile(
            r"""
            ^\s*(?:please\s+)?write\s+
            (?P<text>.+?)\s+
            to\s+(?P<path>.+?)\s*$
            """,
            flags=re.IGNORECASE | re.DOTALL | re.VERBOSE,
        ),
        re.compile(
            r"""
            ^\s*(?:please\s+)?create\s+(?:a\s+)?(?:note|file)\s+(?:at|to)\s+
            (?P<path>.+?)\s+
            with\s+(?P<text>.+?)\s*$
            """,
            flags=re.IGNORECASE | re.DOTALL | re.VERBOSE,
        ),
    ]

    for pattern in patterns:
        m = pattern.match(text)
        if not m:
            continue

        raw_path = _strip_matching_quotes(m.group("path")).strip().rstrip(",;")
        if not raw_path or not _is_safe_notes_path(raw_path):
            continue

        body_text = _strip_matching_quotes(m.group("text")).strip()
        if not body_text:
            continue

        mode = "append" if _looks_like_append(text) else "overwrite"
        return {"path": raw_path, "text": body_text, "mode": mode}

    return None


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


def _build_brain_candidates(cfg: AppConfig) -> list[_BrainCandidate]:
    if not cfg.privacy.cloud_allowed:
        raise MissionPlannerError("Cloud planning is disabled by privacy.cloud_allowed=false")

    if not cfg.brain:
        raise MissionPlannerError("No brain provider is configured. Run 'voxera setup' first.")

    ordered: list[tuple[str, object]] = []
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
                "Use only skill IDs from the provided catalog. "
                "Do not use files.write_text unless the user explicitly asks to write/update a file. "
                "If writing under the allowed notes directory without a specific filename, use a relative path like ok.txt. "
                "Never use placeholder paths like /path/to/notes.txt. "
                'For sandbox.exec always use argv list form like {"command": ["bash", "-lc", "echo HELLO"]}; never use a command string. '
                "Do not wrap output in markdown/code fences and do not include commentary. Return one strict JSON object only."
            ),
        },
        {
            "role": "user",
            "content": (f"Goal: {goal}\nSkill catalog:\n{skills_block}\nReturn only JSON."),
        },
    ]

    try:
        resp = await asyncio.wait_for(brain.generate(messages), timeout=_PLANNER_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        raise MissionPlannerError(f"Planner timed out after {_PLANNER_TIMEOUT_SECONDS}s") from exc
    raw = resp.text

    return _parse_planner_json(raw)


def _normalize_write_step_for_allowed_notes_goal(goal: str, step: MissionStep) -> MissionStep:
    if step.skill_id != "files.write_text":
        return step

    if not _goal_implies_allowed_notes_directory(goal):
        return step

    args = dict(step.args)
    raw_path = args.get("path")
    path = raw_path.strip() if isinstance(raw_path, str) else ""
    placeholder_paths = {"/path/to/notes.txt", "/path/to/note.txt", "notes.txt", "note.txt"}

    if not path:
        args["path"] = "ok.txt"
        return MissionStep(skill_id=step.skill_id, args=args)

    if path in placeholder_paths or path.startswith("/path/to/"):
        args["path"] = "ok.txt"
        return MissionStep(skill_id=step.skill_id, args=args)

    return step


def _normalize_sandbox_exec_step(step: MissionStep) -> MissionStep:
    if step.skill_id != "sandbox.exec":
        return step

    args = dict(step.args)
    command = args.get("command")
    if isinstance(command, str):
        command_text = command.strip()
        if not command_text:
            raise MissionPlannerError("sandbox.exec command must be a non-empty list of strings.")
        args["command"] = ["bash", "-lc", command_text]
        return MissionStep(skill_id=step.skill_id, args=args)

    if not isinstance(command, list) or not command:
        raise MissionPlannerError("sandbox.exec command must be a non-empty list of strings.")

    normalized_command: list[str] = []
    for part in command:
        if not isinstance(part, str):
            raise MissionPlannerError("sandbox.exec command must be a non-empty list of strings.")
        normalized_part = part.strip()
        if not normalized_part:
            raise MissionPlannerError("sandbox.exec command must be a non-empty list of strings.")
        normalized_command.append(normalized_part)

    args["command"] = normalized_command
    return MissionStep(skill_id=step.skill_id, args=args)


async def plan_mission(
    goal: str,
    cfg: AppConfig,
    registry: SkillRegistry,
    *,
    source: str = "cli",
    job_ref: str | None = None,
) -> MissionTemplate:
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

    simple_write_args = _extract_simple_write_args(goal)
    if simple_write_args is None:
        simple_write_args = _extract_allowed_notes_write_args(goal)

    if simple_write_args is not None:
        log(
            {
                "event": "planner_selected",
                "plan_id": plan_id,
                "provider": "deterministic_simple_write",
                "attempt_index": 0,
            }
        )
        steps = [MissionStep(skill_id="files.write_text", args=simple_write_args)]
        log({"event": "plan_built", "plan_id": plan_id, "steps": len(steps)})
        return MissionTemplate(
            id="cloud_planned",
            title="Deterministic Note Write",
            goal=goal,
            steps=steps,
            notes="Deterministic simple-write planning path.",
        )

    candidates = _build_brain_candidates(cfg)
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
        raise MissionPlannerError(
            f"Planner failed after fallbacks: {last_error or 'unknown error'}"
        )

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
        raise MissionPlannerError(
            f"Planner returned too many steps ({len(steps_json)} > {_MAX_STEPS})."
        )

    skills = sorted(registry.discover().values(), key=lambda m: m.id)
    known_ids = {m.id for m in skills}
    steps: list[MissionStep] = []
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

    steps = _rewrite_non_explicit_file_writes(goal, steps)
    steps = [_normalize_write_step_for_allowed_notes_goal(goal, step) for step in steps]
    steps = [_normalize_sandbox_exec_step(step) for step in steps]
    steps = _rewrite_non_explicit_sandbox_steps(goal, steps)

    log({"event": "plan_built", "plan_id": plan_id, "steps": len(steps)})
    return MissionTemplate(id="cloud_planned", title=title, goal=goal, steps=steps, notes=notes)
