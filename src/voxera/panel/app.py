from __future__ import annotations

import json
import os
import re
import secrets
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..audit import log, tail
from ..core.missions import MissionTemplate, _parse_mission_file, list_missions
from ..core.queue_daemon import MissionQueueDaemon
from ..health import increment_health_counter, read_health_snapshot
from ..version import get_version

app = FastAPI(title="Voxera Panel", version=get_version())

HERE = Path(__file__).resolve().parent
templates = Environment(
    loader=FileSystemLoader(str(HERE / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

APPROVALS: list[dict[str, Any]] = []
MISSION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")

ERROR_MESSAGES = {
    "goal_required": "Goal is required when queue type is goal.",
    "mission_id_required": "Mission ID is required.",
    "queue_kind_invalid": "Queue type must be either goal or mission.",
    "mission_id_invalid": "Mission ID must use lowercase letters, numbers, '_' or '-'.",
    "steps_json_invalid": "Steps JSON must be valid JSON.",
    "steps_json_not_list": "Steps JSON must decode to a JSON list.",
    "mission_schema_invalid": "Mission template failed schema validation.",
    "get_mutation_disabled": "GET mutation endpoints are disabled; submit the form normally.",
}

CSRF_COOKIE = "voxera_panel_csrf"
CSRF_FORM_KEY = "csrf_token"


def _queue_root() -> Path:
    return Path.home() / "VoxeraOS" / "notes" / "queue"


def _missions_dir() -> Path:
    return Path.home() / ".config" / "voxera" / "missions"


def _allow_get_mutations() -> bool:
    return os.getenv("VOXERA_PANEL_ENABLE_GET_MUTATIONS", "0") == "1"


def _request_meta(request: Request) -> dict[str, Any]:
    return {
        "path": request.url.path,
        "method": request.method,
        "remote": (request.client.host if request.client else "unknown"),
    }


def _log_panel_security_event(
    event: str,
    *,
    request: Request,
    reason: str,
    status_code: int,
) -> None:
    meta = _request_meta(request)
    log(
        {
            "event": event,
            "ts_ms": int(time.time() * 1000),
            "path": meta["path"],
            "method": meta["method"],
            "remote": meta["remote"],
            "reason": reason,
            "status_code": status_code,
        }
    )


def _panel_security_counter_incr(key: str, *, last_error: str | None = None) -> None:
    increment_health_counter(_queue_root(), key, last_error=last_error)


def _panel_security_snapshot() -> dict[str, Any]:
    payload = read_health_snapshot(_queue_root())
    counters = payload.get("counters")
    return counters if isinstance(counters, dict) else {}


def _operator_credentials(request: Request) -> tuple[str, str]:
    user = os.getenv("VOXERA_PANEL_OPERATOR_USER", "operator")
    password = os.getenv("VOXERA_PANEL_OPERATOR_PASSWORD")
    if not password:
        _panel_security_counter_incr("panel_401_count", last_error="operator password missing")
        _log_panel_security_event(
            "panel_operator_config_error",
            request=request,
            reason="operator_password_missing",
            status_code=503,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VOXERA_PANEL_OPERATOR_PASSWORD must be set",
        )
    return user, password


def _require_operator_basic_auth(request: Request, authorization: str | None) -> None:
    user, password = _operator_credentials(request)
    if not authorization:
        _panel_security_counter_incr("panel_401_count", last_error="missing authorization")
        _log_panel_security_event(
            "panel_auth_missing", request=request, reason="missing_authorization", status_code=401
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="operator authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )
    import base64

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "basic" or not token:
        _panel_security_counter_incr(
            "panel_auth_invalid", last_error="invalid authentication scheme"
        )
        _panel_security_counter_incr("panel_401_count")
        _log_panel_security_event(
            "panel_auth_invalid", request=request, reason="invalid_scheme", status_code=401
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid authentication scheme",
            headers={"WWW-Authenticate": "Basic"},
        )
    try:
        decoded = base64.b64decode(token).decode("utf-8")
    except Exception as exc:
        _panel_security_counter_incr(
            "panel_auth_invalid", last_error="invalid authorization header"
        )
        _panel_security_counter_incr("panel_401_count")
        _log_panel_security_event(
            "panel_auth_invalid", request=request, reason="invalid_header", status_code=401
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid authorization header",
            headers={"WWW-Authenticate": "Basic"},
        ) from exc
    got_user, _, got_password = decoded.partition(":")
    if not (
        secrets.compare_digest(got_user, user) and secrets.compare_digest(got_password, password)
    ):
        _panel_security_counter_incr(
            "panel_auth_invalid", last_error="invalid operator credentials"
        )
        _panel_security_counter_incr("panel_401_count")
        _log_panel_security_event(
            "panel_auth_invalid", request=request, reason="invalid_credentials", status_code=401
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid operator credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


async def _require_mutation_guard(request: Request) -> None:
    _require_operator_auth_from_request(request)
    cookie_token = request.cookies.get(CSRF_COOKIE, "")
    request_token = (request.headers.get("x-csrf-token") or "").strip() or (
        await _request_value(request, CSRF_FORM_KEY, "")
    ).strip()
    if not cookie_token or not request_token:
        _panel_security_counter_incr("panel_403_count", last_error="csrf token missing")
        _panel_security_counter_incr("panel_csrf_missing")
        _log_panel_security_event(
            "panel_csrf_missing", request=request, reason="csrf_token_missing", status_code=403
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="csrf validation failed")
    if not secrets.compare_digest(cookie_token, request_token):
        _panel_security_counter_incr("panel_403_count", last_error="csrf token mismatch")
        _panel_security_counter_incr("panel_csrf_invalid")
        _log_panel_security_event(
            "panel_csrf_invalid", request=request, reason="csrf_token_mismatch", status_code=403
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="csrf validation failed")
    _panel_security_counter_incr("panel_mutation_allowed")
    _log_panel_security_event(
        "panel_mutation_allowed",
        request=request,
        reason="auth_and_csrf_valid",
        status_code=200,
    )


def _require_operator_auth_from_request(request: Request) -> None:
    _require_operator_basic_auth(request, request.headers.get("authorization"))


def _validate_mission_id(mission_id: str) -> str:
    normalized = mission_id.strip()
    if not MISSION_ID_RE.fullmatch(normalized):
        raise ValueError("mission_id_invalid")
    return normalized


def _enforce_get_mutations_enabled() -> None:
    if not _allow_get_mutations():
        raise HTTPException(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            detail="GET mutation endpoints are disabled",
        )


async def _request_value(request: Request, key: str, default: str = "") -> str:
    query_value = request.query_params.get(key)
    if query_value is not None:
        return query_value

    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/x-www-form-urlencoded"):
        body = (await request.body()).decode("utf-8", errors="ignore")
        values = parse_qs(body, keep_blank_values=True)
        if key in values and values[key]:
            return values[key][0]
    return default


def _write_queue_job(payload: dict[str, Any]) -> str:
    queue_root = _queue_root()
    inbox = queue_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    job_id = f"job-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    tmp_path = inbox / f".{job_id}.tmp.json"
    final_path = inbox / f"{job_id}.json"
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(final_path)
    return final_path.name


def _artifact_text(path: Path, *, max_chars: int = 8000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[:max_chars] + ("\n...[truncated]..." if len(text) > max_chars else "")


def _job_artifact_payload(queue_root: Path, job_name: str) -> dict[str, Any]:
    stem = Path(job_name).stem
    art = queue_root / "artifacts" / stem
    plan = {}
    if (art / "plan.json").exists():
        with (art / "plan.json").open("r", encoding="utf-8") as f:
            plan = json.load(f)
    actions: list[dict[str, Any]] = []
    actions_path = art / "actions.jsonl"
    if actions_path.exists():
        for line in actions_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                actions.append(json.loads(line))
            except Exception:
                continue
    actions.reverse()
    generated_files: list[str] = []
    generated = art / "outputs" / "generated_files.json"
    if generated.exists():
        try:
            parsed = json.loads(generated.read_text(encoding="utf-8"))
            if isinstance(parsed, list):
                generated_files = [str(i) for i in parsed]
        except Exception:
            generated_files = []
    return {
        "job": job_name,
        "artifacts_dir": str(art),
        "plan": plan,
        "actions": actions,
        "stdout": _artifact_text(art / "stdout.txt"),
        "stderr": _artifact_text(art / "stderr.txt"),
        "generated_files": generated_files,
    }


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


@app.get("/", response_class=HTMLResponse)
def home(request: Request, created: str = "", error: str = "", mission_created: str = ""):
    queue_root = _queue_root()
    daemon = MissionQueueDaemon(queue_root=queue_root)
    queue = daemon.status_snapshot(approvals_limit=12, failed_limit=8)
    queue["pending_approvals"] = daemon.approvals_list()[:12]
    queue["done_jobs"] = [
        p.name
        for p in sorted(
            (queue_root / "done").glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True
        )[:12]
    ]

    mission_log = Path.home() / "VoxeraOS" / "notes" / "mission-log.md"
    mission_log_tail = []
    if mission_log.exists():
        mission_log_tail = mission_log.read_text(encoding="utf-8").splitlines()[-20:]

    audit_events = tail(120)
    active_jobs, recent_activity = _build_activity(audit_events)

    missions = list_missions()
    tmpl = templates.get_template("home.html")
    csrf_token = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(24)
    html = tmpl.render(
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
        error_message=ERROR_MESSAGES.get(error, "Unexpected panel error." if error else ""),
        get_mutations_enabled=_allow_get_mutations(),
        active_jobs=active_jobs,
        recent_activity=recent_activity,
        csrf_token=csrf_token,
        panel_security_counters=_panel_security_snapshot(),
    )
    response = HTMLResponse(content=html)
    response.set_cookie(CSRF_COOKIE, csrf_token, httponly=False, samesite="strict")
    return response


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

    created = _write_queue_job(payload)
    return RedirectResponse(url=f"/?created={created}", status_code=303)


@app.get("/queue/create")
def create_queue_job_get(
    request: Request, kind: str = "goal", mission_id: str = "", goal: str = ""
):
    _enforce_get_mutations_enabled()
    _require_operator_auth_from_request(request)
    return _create_queue_job_from_values(kind, mission_id, goal)


@app.post("/queue/create")
async def create_queue_job(request: Request):
    await _require_mutation_guard(request)
    kind = await _request_value(request, "kind", "goal")
    mission_id = await _request_value(request, "mission_id", "")
    goal = await _request_value(request, "goal", "")
    return _create_queue_job_from_values(kind, mission_id, goal)


def _create_mission_from_values(
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


@app.get("/missions/create")
def create_mission_get(
    request: Request,
    mission_id: str = "",
    title: str = "",
    goal: str = "",
    notes: str = "",
    steps_json: str = "[]",
):
    _enforce_get_mutations_enabled()
    _require_operator_auth_from_request(request)
    return _create_mission_from_values(mission_id, title, goal, notes, steps_json)


@app.post("/missions/create")
async def create_mission(request: Request):
    await _require_mutation_guard(request)
    mission_id = await _request_value(request, "mission_id", "")
    title = await _request_value(request, "title", "")
    goal = await _request_value(request, "goal", "")
    notes = await _request_value(request, "notes", "")
    steps_json = await _request_value(request, "steps_json", "[]")
    return _create_mission_from_values(mission_id, title, goal, notes, steps_json)


@app.post("/queue/approvals/{ref}/approve")
async def approve_queue_job(ref: str, request: Request):
    await _require_mutation_guard(request)
    daemon = MissionQueueDaemon(queue_root=_queue_root())
    daemon.resolve_approval(ref, approve=True)
    return RedirectResponse(url="/", status_code=303)


@app.post("/queue/approvals/{ref}/approve-always")
async def approve_always_queue_job(ref: str, request: Request):
    await _require_mutation_guard(request)
    daemon = MissionQueueDaemon(queue_root=_queue_root())
    daemon.resolve_approval(ref, approve=True, approve_always=True)
    return RedirectResponse(url="/", status_code=303)


@app.post("/queue/approvals/{ref}/deny")
async def deny_queue_job(ref: str, request: Request):
    await _require_mutation_guard(request)
    daemon = MissionQueueDaemon(queue_root=_queue_root())
    daemon.resolve_approval(ref, approve=False)
    return RedirectResponse(url="/", status_code=303)


@app.get("/queue/jobs/{job}/detail", response_class=HTMLResponse)
def queue_job_detail(job: str):
    queue_root = _queue_root()
    payload = _job_artifact_payload(queue_root, job)
    tmpl = templates.get_template("job_detail.html")
    return tmpl.render(payload=payload)


@app.post("/queue/jobs/{ref}/cancel")
async def cancel_queue_job(ref: str, request: Request):
    await _require_mutation_guard(request)
    daemon = MissionQueueDaemon(queue_root=_queue_root())
    daemon.cancel_job(ref)
    return RedirectResponse(url="/", status_code=303)


@app.post("/queue/jobs/{ref}/retry")
async def retry_queue_job(ref: str, request: Request):
    await _require_mutation_guard(request)
    daemon = MissionQueueDaemon(queue_root=_queue_root())
    daemon.retry_job(ref)
    return RedirectResponse(url="/", status_code=303)


@app.post("/queue/pause")
async def pause_queue(request: Request):
    await _require_mutation_guard(request)
    daemon = MissionQueueDaemon(queue_root=_queue_root())
    daemon.pause()
    return RedirectResponse(url="/", status_code=303)


@app.post("/queue/resume")
async def resume_queue(request: Request):
    await _require_mutation_guard(request)
    daemon = MissionQueueDaemon(queue_root=_queue_root())
    daemon.resume()
    return RedirectResponse(url="/", status_code=303)
