from __future__ import annotations

import json
import os
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from ..core.missions import MissionTemplate, _parse_mission_file

MISSION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


def _validate_mission_id(mission_id: str) -> str:
    normalized = mission_id.strip()
    if not MISSION_ID_RE.fullmatch(normalized):
        raise ValueError("mission_id_invalid")
    return normalized


def _missions_dir() -> Path:
    return Path.home() / ".config" / "voxera" / "missions"


def _build_mission_payload(
    mission_id: str,
    title: str,
    goal: str,
    notes: str,
    steps_json: str,
) -> MissionTemplate:
    validated_mission_id = _validate_mission_id(mission_id)
    payload: dict[str, Any] = {
        "id": validated_mission_id,
        "title": title.strip() or validated_mission_id,
        "goal": goal.strip() or "User-defined mission",
    }
    if notes.strip():
        payload["notes"] = notes.strip()

    try:
        steps_raw = json.loads(steps_json)
    except json.JSONDecodeError as exc:
        raise ValueError("steps_json_invalid") from exc
    if not isinstance(steps_raw, list):
        raise ValueError("steps_json_not_list")
    payload["steps"] = steps_raw

    missions_dir = _missions_dir()
    missions_dir.mkdir(parents=True, exist_ok=True)
    candidate = (missions_dir / f"{payload['id']}.json").resolve()
    missions_root = missions_dir.resolve()
    if not str(candidate).startswith(f"{missions_root}{os.sep}"):
        raise ValueError("mission_id_invalid")
    candidate.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        validated = _parse_mission_file(candidate, payload["id"])
    except Exception as exc:
        candidate.unlink(missing_ok=True)
        raise ValueError("mission_schema_invalid") from exc
    return validated


def _create_mission_template_from_values(
    mission_id: str, title: str, goal: str, notes: str, steps_json: str
) -> RedirectResponse:
    normalized_id = mission_id.strip()
    if not normalized_id:
        return RedirectResponse(url="/?error=mission_id_required", status_code=303)

    try:
        _build_mission_payload(normalized_id, title, goal, notes, steps_json)
    except ValueError as exc:
        code = str(exc)
        return RedirectResponse(url=f"/?error={code}", status_code=303)

    return RedirectResponse(url=f"/?mission_created={normalized_id}", status_code=303)


def _create_panel_mission_from_values(
    prompt: str,
    approval_required: bool,
    *,
    write_panel_mission_job: Callable[..., tuple[str, str]],
) -> RedirectResponse:
    normalized_prompt = prompt.strip()
    if not normalized_prompt:
        return RedirectResponse(url="/?error=panel_prompt_required", status_code=303)
    created, mission_id = write_panel_mission_job(
        prompt=normalized_prompt,
        approval_required=approval_required,
    )
    return RedirectResponse(
        url=f"/?created={created}&mission_created={mission_id}",
        status_code=303,
    )


def register_mission_routes(
    app: FastAPI,
    *,
    enforce_get_mutations_enabled: Callable[[], None],
    require_operator_auth_from_request: Callable[[Request], None],
    require_mutation_guard: Callable[[Request], Awaitable[None]],
    request_value: Callable[[Request, str, str], Awaitable[str]],
    write_panel_mission_job: Callable[..., tuple[str, str]],
) -> None:
    @app.get("/missions/templates/create")
    def create_mission_template_get(
        request: Request,
        mission_id: str = "",
        title: str = "",
        goal: str = "",
        notes: str = "",
        steps_json: str = "[]",
    ):
        enforce_get_mutations_enabled()
        require_operator_auth_from_request(request)
        return _create_mission_template_from_values(mission_id, title, goal, notes, steps_json)

    @app.post("/missions/templates/create")
    async def create_mission_template(request: Request):
        await require_mutation_guard(request)
        mission_id = await request_value(request, "mission_id", "")
        title = await request_value(request, "title", "")
        goal = await request_value(request, "goal", "")
        notes = await request_value(request, "notes", "")
        steps_json = await request_value(request, "steps_json", "[]")
        return _create_mission_template_from_values(mission_id, title, goal, notes, steps_json)

    @app.get("/missions/create")
    def create_mission_get(
        request: Request,
        prompt: str = "",
        approval_required: str = "1",
    ):
        enforce_get_mutations_enabled()
        require_operator_auth_from_request(request)
        return _create_panel_mission_from_values(
            prompt,
            approval_required != "0",
            write_panel_mission_job=write_panel_mission_job,
        )

    @app.post("/missions/create")
    async def create_mission(request: Request):
        await require_mutation_guard(request)
        prompt = (await request_value(request, "prompt", "")).strip() or (
            await request_value(request, "goal", "")
        ).strip()
        approval_raw = await request_value(request, "approval_required", "1")
        return _create_panel_mission_from_values(
            prompt,
            approval_raw not in {"0", "false", "off"},
            write_panel_mission_job=write_panel_mission_job,
        )
