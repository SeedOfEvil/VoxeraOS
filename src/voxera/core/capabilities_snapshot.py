from __future__ import annotations

import time
from difflib import get_close_matches
from typing import Any

from voxera.skills.arg_normalizer import canonicalize_args
from voxera_builtin_skills.open_app import ALLOW as OPEN_APP_ALLOWLIST

from ..skills.registry import SkillRegistry
from .missions import MissionTemplate, list_missions_best_effort

_SCHEMA_VERSION = 1


def _sorted_unique(values: list[str]) -> list[str]:
    return sorted({str(item) for item in values if str(item).strip()})


def generate_capabilities_snapshot(registry: SkillRegistry | None = None) -> dict[str, Any]:
    reg = registry or SkillRegistry()
    manifests = reg.discover()

    missions = sorted(list_missions_best_effort(), key=lambda mission: mission.id)
    mission_entries = [
        {
            "id": mission.id,
            "title": mission.title,
            "goal": mission.goal,
            "step_count": len(mission.steps),
        }
        for mission in missions
    ]

    skill_entries = [
        {
            "id": manifest.id,
            "description": manifest.description,
            "capabilities": sorted(manifest.capabilities),
            "risk": manifest.risk,
        }
        for manifest in sorted(manifests.values(), key=lambda item: item.id)
    ]

    return {
        "schema_version": _SCHEMA_VERSION,
        "generated_ts_ms": int(time.time() * 1000),
        "missions": mission_entries,
        "allowed_apps": _sorted_unique(list(OPEN_APP_ALLOWLIST.keys())),
        "skills": skill_entries,
    }


def _closest_matches(value: str, options: list[str]) -> list[str]:
    normalized = value.strip().lower()
    if not normalized:
        return []

    canonical_options = sorted({option.strip().lower() for option in options if option.strip()})
    prefix_matches = [option for option in canonical_options if option.startswith(normalized)]
    contains_matches = [option for option in canonical_options if normalized in option]
    fuzzy_matches = get_close_matches(normalized, canonical_options, n=5, cutoff=0.35)

    merged: list[str] = []
    for candidate in [*prefix_matches, *contains_matches, *fuzzy_matches]:
        if candidate not in merged:
            merged.append(candidate)
        if len(merged) == 5:
            break
    return merged


def _format_validation_error(subject: str, value: str, options: list[str]) -> str:
    suggestions = _closest_matches(value, options)
    if suggestions:
        return (
            f"Invalid {subject}: '{value}'. Closest matches: "
            + ", ".join(f"'{item}'" for item in suggestions)
            + "."
        )
    return f"Invalid {subject}: '{value}'."


def validate_mission_id_against_snapshot(mission_id: str, snapshot: dict[str, Any]) -> None:
    known_ids = [
        item.get("id", "") for item in snapshot.get("missions", []) if isinstance(item, dict)
    ]
    if mission_id not in known_ids:
        raise ValueError(_format_validation_error("mission_id", mission_id, known_ids))


def validate_mission_steps_against_snapshot(
    mission: MissionTemplate, snapshot: dict[str, Any]
) -> None:
    allowed_apps = [str(item) for item in snapshot.get("allowed_apps", [])]
    for index, step in enumerate(mission.steps, start=1):
        if step.skill_id != "system.open_app":
            continue

        normalized_args = canonicalize_args(step.skill_id, dict(step.args))
        app_name = str(normalized_args.get("name", step.args.get("app", ""))).strip().lower()
        if app_name not in allowed_apps:
            raise ValueError(
                _format_validation_error(
                    f"step {index} system.open_app app",
                    app_name or "(empty)",
                    allowed_apps,
                )
            )
