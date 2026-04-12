from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..audit import tail
from ..core.missions import list_missions
from ..core.queue_daemon import MissionQueueDaemon
from ..core.queue_inspect import queue_snapshot
from ..health import read_health_snapshot
from .home_vera_activity import build_home_vera_activity


def register_home_routes(
    app: FastAPI,
    *,
    templates: Any,
    csrf_cookie: str,
    approvals: list[dict[str, Any]],
    error_messages: dict[str, str],
    allow_get_mutations: Callable[[], bool],
    queue_root: Callable[[], Path],
    build_activity: Callable[
        [list[dict[str, Any]]], tuple[list[dict[str, Any]], list[dict[str, Any]]]
    ],
    daemon_health_view: Callable[[dict[str, Any]], dict[str, Any]],
    performance_stats_view: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    panel_security_snapshot: Callable[[], dict[str, Any]],
    auth_setup_banner: Callable[[], dict[str, str] | None],
    enforce_get_mutations_enabled: Callable[[], None],
    require_operator_auth_from_request: Callable[[Request], None],
    require_mutation_guard: Callable[[Request], Awaitable[None]],
    write_queue_job: Callable[[dict[str, Any]], str],
    parse_error_message: str = "Unexpected panel error.",
) -> None:
    def _create_queue_job_from_values(kind: str, mission_id: str, goal: str) -> RedirectResponse:
        normalized_kind = kind.strip().lower()
        if normalized_kind not in {"goal", "mission"}:
            return RedirectResponse(url="/?error=queue_kind_invalid", status_code=303)

        payload: dict[str, Any] = {}
        if normalized_kind == "mission":
            mission_id = mission_id.strip()
            if not mission_id:
                return RedirectResponse(url="/?error=mission_id_required", status_code=303)
            payload["mission_id"] = mission_id
        else:
            goal = goal.strip()
            if not goal:
                return RedirectResponse(url="/?error=goal_required", status_code=303)
            payload["goal"] = goal

        created = write_queue_job(payload)
        return RedirectResponse(url=f"/?created={created}", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, created: str = "", error: str = "", mission_created: str = ""):
        root = queue_root()
        daemon = MissionQueueDaemon(queue_root=root)
        queue = queue_snapshot(root)
        queue["pending_approvals"] = daemon.approvals_list()[:12]
        queue["done_jobs"] = [
            p.name
            for p in sorted(
                (root / "done").glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True
            )[:12]
        ]

        mission_log = Path.home() / "VoxeraOS" / "notes" / "mission-log.md"
        mission_log_tail = []
        if mission_log.exists():
            mission_log_tail = mission_log.read_text(encoding="utf-8").splitlines()[-20:]

        audit_events = tail(120)
        active_jobs, recent_activity = build_activity(audit_events)

        missions = list_missions()
        health_snapshot = read_health_snapshot(root)
        daemon_health = daemon_health_view(health_snapshot)
        performance_stats = performance_stats_view(queue, health_snapshot)
        # Read-only, fail-soft lookup of the most recent Vera session
        # context. This is a supplemental continuity aid only —
        # canonical queue / daemon-health / approvals truth above
        # remains primary and must never be overridden by this block.
        vera_activity = build_home_vera_activity(root)
        tmpl = templates.get_template("home.html")
        csrf_token = request.cookies.get(csrf_cookie) or secrets.token_urlsafe(24)
        html = tmpl.render(
            approvals=approvals,
            audit=tail(50),
            queue=queue,
            queue_root=str(root),
            mission_log_path=str(mission_log),
            mission_log_tail=mission_log_tail,
            missions=missions,
            created=created,
            mission_created=mission_created,
            error=error,
            error_message=error_messages.get(error, parse_error_message if error else ""),
            get_mutations_enabled=allow_get_mutations(),
            active_jobs=active_jobs,
            recent_activity=recent_activity,
            csrf_token=csrf_token,
            panel_security_counters=panel_security_snapshot(),
            auth_setup_banner=auth_setup_banner(),
            daemon_health=daemon_health,
            performance_stats=performance_stats,
            vera_activity=vera_activity,
        )
        response = HTMLResponse(content=html)
        response.set_cookie(csrf_cookie, csrf_token, httponly=False, samesite="strict")
        return response

    @app.get("/queue/create")
    def create_queue_job_get(
        request: Request, kind: str = "goal", mission_id: str = "", goal: str = ""
    ):
        enforce_get_mutations_enabled()
        require_operator_auth_from_request(request)
        return _create_queue_job_from_values(kind, mission_id, goal)

    @app.post("/queue/create")
    async def create_queue_job(request: Request):
        from .helpers import request_value

        await require_mutation_guard(request)
        kind = await request_value(request, "kind", "goal")
        mission_id = await request_value(request, "mission_id", "")
        goal = await request_value(request, "goal", "")
        return _create_queue_job_from_values(kind, mission_id, goal)
