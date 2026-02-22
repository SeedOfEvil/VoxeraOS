from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..audit import tail
from ..core.missions import MissionTemplate, _parse_mission_file, list_missions
from ..core.queue_daemon import MissionQueueDaemon
from ..version import get_version

app = FastAPI(title="Voxera Panel", version=get_version())

HERE = Path(__file__).resolve().parent
templates = Environment(
    loader=FileSystemLoader(str(HERE / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

APPROVALS: list[dict[str, Any]] = []


def _queue_root() -> Path:
    return Path.home() / "VoxeraOS" / "notes" / "queue"


def _missions_dir() -> Path:
    return Path.home() / ".config" / "voxera" / "missions"


def _write_queue_job(payload: dict[str, Any]) -> str:
    queue_root = _queue_root()
    queue_root.mkdir(parents=True, exist_ok=True)
    job_id = f"job-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    tmp_path = queue_root / f".{job_id}.tmp.json"
    final_path = queue_root / f"{job_id}.json"
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(final_path)
    return final_path.name


def _build_activity(
    audit_events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    active: dict[str, dict[str, Any]] = {}
    recent: list[dict[str, Any]] = []
    for event in audit_events:
        event_name = str(event.get("event", ""))
        job = Path(str(event.get("job", ""))).name if event.get("job") else ""
        mission = str(event.get("mission") or "")
        goal = str(event.get("goal") or "")

        if event_name == "queue_job_started" and job:
            active[job] = {
                "job": job,
                "mission": mission,
                "goal": goal,
                "state": "running",
            }
        if event_name in {"queue_job_done", "queue_job_failed"} and job:
            active.pop(job, None)

        if event_name.startswith("queue_") or event_name.startswith("mission_"):
            recent.append(
                {
                    "event": event_name,
                    "job": job,
                    "mission": mission,
                    "step": event.get("step", ""),
                }
            )

    return list(active.values())[:8], list(reversed(recent[-12:]))


def _build_mission_payload(
    mission_id: str,
    title: str,
    goal: str,
    notes: str,
    steps_json: str,
) -> MissionTemplate:
    payload: dict[str, Any] = {
        "id": mission_id.strip(),
        "title": title.strip() or mission_id.strip(),
        "goal": goal.strip() or "User-defined mission",
    }
    if notes.strip():
        payload["notes"] = notes.strip()

    steps_raw = json.loads(steps_json)
    if not isinstance(steps_raw, list):
        raise ValueError("steps_json must decode to a JSON list")
    payload["steps"] = steps_raw

    missions_dir = _missions_dir()
    missions_dir.mkdir(parents=True, exist_ok=True)
    candidate = missions_dir / f"{payload['id']}.json"
    candidate.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        validated = _parse_mission_file(candidate, payload["id"])
    except Exception:
        candidate.unlink(missing_ok=True)
        raise
    return validated


@app.get("/", response_class=HTMLResponse)
def home(created: str = "", error: str = "", mission_created: str = ""):
    queue_root = _queue_root()
    daemon = MissionQueueDaemon(queue_root=queue_root)
    queue = daemon.status_snapshot(approvals_limit=12, failed_limit=8)
    queue["pending_approvals"] = daemon.approvals_list()[:12]

    mission_log = Path.home() / "VoxeraOS" / "notes" / "mission-log.md"
    mission_log_tail = []
    if mission_log.exists():
        mission_log_tail = mission_log.read_text(encoding="utf-8").splitlines()[-20:]

    audit_events = tail(120)
    active_jobs, recent_activity = _build_activity(audit_events)

    missions = list_missions()
    tmpl = templates.get_template("home.html")
    return tmpl.render(
        approvals=APPROVALS,
        audit=tail(50),
        queue=queue,
        queue_root=str(queue_root),
        mission_log_path=str(mission_log),
        mission_log_tail=mission_log_tail,
        missions=missions,
        created=created,
        mission_created=mission_created,
        error=error,
        active_jobs=active_jobs,
        recent_activity=recent_activity,
    )


@app.get("/queue/create")
def create_queue_job(kind: str = "goal", mission_id: str = "", goal: str = ""):
    payload: dict[str, Any] = {}
    if kind == "mission":
        mission_id = mission_id.strip()
        if not mission_id:
            return RedirectResponse(url="/?error=mission_id_required", status_code=303)
        payload["mission_id"] = mission_id
    else:
        goal = goal.strip()
        if not goal:
            return RedirectResponse(url="/?error=goal_required", status_code=303)
        payload["goal"] = goal

    created = _write_queue_job(payload)
    return RedirectResponse(url=f"/?created={created}", status_code=303)


@app.get("/missions/create")
def create_mission(
    mission_id: str = "",
    title: str = "",
    goal: str = "",
    notes: str = "",
    steps_json: str = "[]",
):
    mission_id = mission_id.strip()
    if not mission_id:
        return RedirectResponse(url="/?error=mission_id_required", status_code=303)

    try:
        _build_mission_payload(mission_id, title, goal, notes, steps_json)
    except Exception as exc:
        return RedirectResponse(url=f"/?error=mission_create_failed:{exc}", status_code=303)

    return RedirectResponse(url=f"/?mission_created={mission_id}", status_code=303)


@app.post("/queue/approvals/{ref}/approve")
def approve_queue_job(ref: str):
    daemon = MissionQueueDaemon(queue_root=_queue_root())
    daemon.resolve_approval(ref, approve=True)
    return RedirectResponse(url="/", status_code=303)


@app.post("/queue/approvals/{ref}/deny")
def deny_queue_job(ref: str):
    daemon = MissionQueueDaemon(queue_root=_queue_root())
    daemon.resolve_approval(ref, approve=False)
    return RedirectResponse(url="/", status_code=303)
