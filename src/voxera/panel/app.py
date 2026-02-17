from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI
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

APPROVALS: List[Dict[str, Any]] = []


def _queue_root() -> Path:
    return Path.home() / "VoxeraOS" / "notes" / "queue"


@app.get("/", response_class=HTMLResponse)
def home():
    queue_root = _queue_root()
    daemon = MissionQueueDaemon(queue_root=queue_root)
    queue = daemon.status_snapshot(approvals_limit=12, failed_limit=8)
    queue["pending_approvals"] = daemon.approvals_list()[:12]

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
