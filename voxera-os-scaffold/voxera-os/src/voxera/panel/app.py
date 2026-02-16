from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..audit import tail
from ..core.queue_daemon import MissionQueueDaemon

app = FastAPI(title="Voxera Panel", version="0.1.0")

HERE = Path(__file__).resolve().parent
templates = Environment(
    loader=FileSystemLoader(str(HERE / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
)

APPROVALS: list[dict[str, Any]] = []


@app.get("/", response_class=HTMLResponse)
def home():
    queue_root = Path.home() / "VoxeraOS" / "notes" / "queue"
    daemon = MissionQueueDaemon(queue_root=queue_root)
    queue = daemon.status_snapshot(approvals_limit=12, failed_limit=8)
    mission_log = Path.home() / "VoxeraOS" / "notes" / "mission-log.md"
    mission_log_tail = []
    if mission_log.exists():
        mission_log_tail = mission_log.read_text(encoding="utf-8").splitlines()[-20:]

    tmpl = templates.get_template("home.html")
    return tmpl.render(
        approvals=APPROVALS,
        audit=tail(50),
        queue=queue,
        queue_root=str(queue_root),
        mission_log_path=str(mission_log),
        mission_log_tail=mission_log_tail,
    )


@app.post("/approvals/add")
async def add_approval(request: Request):
    form_data = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    title = (form_data.get("title") or [""])[0]
    detail = (form_data.get("detail") or [""])[0]
    if not title or not detail:
        return RedirectResponse(url="/", status_code=303)
    APPROVALS.append({"title": title, "detail": detail})
    return RedirectResponse(url="/", status_code=303)


@app.post("/approvals/clear")
def clear_approvals():
    APPROVALS.clear()
    return RedirectResponse(url="/", status_code=303)
