from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode

import anyio
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..audit import log, tail
from ..config import load_config as load_runtime_config
from ..core.missions import MissionTemplate, _parse_mission_file, list_missions
from ..core.queue_daemon import MissionQueueDaemon
from ..core.queue_inspect import JOB_BUCKETS, list_jobs, lookup_job, queue_snapshot
from ..health import increment_health_counter, read_health_snapshot
from ..incident_bundle import BundleError
from ..ops_bundle import build_job_bundle, build_system_bundle
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
    "panel_prompt_required": "Prompt / Goal is required.",
}

FLASH_MESSAGES = {
    "approved": "Approval granted.",
    "approved_always": "Approval granted and remembered for matching scope.",
    "denied": "Approval denied.",
    "canceled": "Job moved to canceled/.",
    "retried": "Job re-enqueued into inbox/.",
    "deleted": "Terminal job deleted.",
    "cancel_not_found": "Cannot cancel: job was not found in active queue buckets.",
    "cannot_cancel_terminal": "Cannot cancel terminal jobs. Use retry/delete for failed/canceled/done.",
    "approval_not_found": "Approval/job reference was not found.",
    "approval_invalid": "Approval request was invalid.",
}

CSRF_COOKIE = "voxera_panel_csrf"
CSRF_FORM_KEY = "csrf_token"


class _RequestUrlLike(Protocol):
    @property
    def path(self) -> str: ...


class _RequestClientLike(Protocol):
    @property
    def host(self) -> str: ...


class _PanelSecurityRequestLike(Protocol):
    @property
    def url(self) -> _RequestUrlLike: ...

    @property
    def method(self) -> str: ...

    @property
    def client(self) -> _RequestClientLike | None: ...


def _settings():
    return load_runtime_config()


def _queue_root() -> Path:
    return _settings().queue_root


def _missions_dir() -> Path:
    return Path.home() / ".config" / "voxera" / "missions"


def _allow_get_mutations() -> bool:
    return _settings().panel_enable_get_mutations


def _request_meta(request: _PanelSecurityRequestLike) -> dict[str, Any]:
    return {
        "path": request.url.path,
        "method": request.method,
        "remote": (request.client.host if request.client else "unknown"),
    }


def _log_panel_security_event(
    event: str,
    *,
    request: _PanelSecurityRequestLike,
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


def _auth_setup_banner() -> dict[str, str] | None:
    settings = _settings()
    if settings.panel_operator_password not in {None, ""}:
        return None
    config_path_hint = str(settings.config_path.expanduser())
    return {
        "title": "Setup required: panel operator password is not configured.",
        "detail": (
            "Mutation routes require Basic auth. Set VOXERA_PANEL_OPERATOR_PASSWORD in your "
            "user service environment and restart panel + daemon. If VOXERA_LOAD_DOTENV=1, "
            ".env may override file settings."
        ),
        "path_hint": f"Config file: {config_path_hint}",
        "commands": (
            "systemctl --user edit voxera-panel.service\n"
            "# add [Service] Environment=VOXERA_PANEL_OPERATOR_PASSWORD=<set-a-strong-password>\n"
            "systemctl --user daemon-reload\n"
            "systemctl --user restart voxera-panel.service voxera-daemon.service"
        ),
    }


def _operator_credentials(request: _PanelSecurityRequestLike) -> tuple[str, str]:
    settings = _settings()
    user = settings.panel_operator_user
    password = settings.panel_operator_password
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
    if not _settings().panel_csrf_enabled:
        _panel_security_counter_incr("panel_mutation_allowed")
        _log_panel_security_event(
            "panel_mutation_allowed",
            request=request,
            reason="auth_valid_csrf_disabled",
            status_code=200,
        )
        return
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


def _write_panel_mission_job(*, prompt: str, approval_required: bool) -> tuple[str, str]:
    queue_root = _queue_root()
    inbox = queue_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    normalized_prompt = prompt.strip()
    slug = re.sub(r"[^a-z0-9_-]+", "-", normalized_prompt.lower()).strip("-")
    slug = slug[:32] or "mission"
    ts = int(time.time())
    suffix = hashlib.sha1(normalized_prompt.encode("utf-8")).hexdigest()[:6]
    mission_id = re.sub(r"[^a-z0-9_-]+", "-", f"{slug}-{suffix}-{ts}").strip("-")

    payload = {
        "id": mission_id,
        "goal": normalized_prompt,
        "approval_required": approval_required,
    }

    base_name = f"job-panel-mission-{slug}-{ts}"
    final_path = inbox / f"{base_name}.json"
    counter = 1
    while final_path.exists():
        final_path = inbox / f"{base_name}-{counter}.json"
        counter += 1

    tmp_path = inbox / f".{final_path.stem}.tmp.json"
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(final_path)
    return final_path.name, mission_id


def _artifact_text(path: Path, *, max_chars: int = 8000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[:max_chars] + ("\n...[truncated]..." if len(text) > max_chars else "")


def _safe_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_actions(path: Path, *, limit: int = 200) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    actions: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except Exception:
            event = {"raw": line}
        if isinstance(event, dict):
            actions.append(event)
    return list(reversed(actions[-limit:]))


def _read_generated_files(artifacts_dir: Path) -> list[str]:
    generated = artifacts_dir / "outputs" / "generated_files.json"
    if not generated.exists():
        return []
    try:
        payload = json.loads(generated.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    return [str(item) for item in payload] if isinstance(payload, list) else []


def _job_detail_payload(queue_root: Path, job_id: str) -> dict[str, Any]:
    lookup = lookup_job(queue_root, job_id)
    if lookup is None:
        stem = Path(job_id).stem
        artifacts_dir = queue_root / "artifacts" / stem
        if not artifacts_dir.exists():
            raise HTTPException(status_code=404, detail="job not found")
        primary: dict[str, Any] = {}
        approval: dict[str, Any] = {}
        failed_sidecar: dict[str, Any] = {}
        bucket = "unknown"
        job_name = f"{stem}.json"
    else:
        primary = _safe_json(lookup.primary_path)
        approval = _safe_json(lookup.approval_path) if lookup.approval_path else {}
        failed_sidecar = (
            _safe_json(lookup.failed_sidecar_path) if lookup.failed_sidecar_path else {}
        )
        artifacts_dir = lookup.artifacts_dir
        bucket = lookup.bucket
        job_name = lookup.job_id

    artifact_files = (
        [
            child.relative_to(artifacts_dir).as_posix()
            for child in sorted(artifacts_dir.rglob("*"))
            if child.is_file()
        ]
        if artifacts_dir.exists()
        else []
    )

    snapshot = queue_snapshot(queue_root)
    relevant_events = [
        item
        for item in reversed(tail(200))
        if job_name in str(item.get("job", ""))
        or item.get("event") in {"queue_job_failed", "queue_job_done"}
    ]
    return {
        "job_id": job_name,
        "bucket": bucket,
        "job": primary,
        "approval": approval,
        "failed_sidecar": failed_sidecar,
        "lock": snapshot.get("lock_status", {}),
        "paused": snapshot.get("paused", False),
        "plan": _safe_json(artifacts_dir / "plan.json"),
        "actions": _load_actions(artifacts_dir / "actions.jsonl"),
        "stdout": _artifact_text(artifacts_dir / "stdout.txt", max_chars=64 * 1024),
        "stderr": _artifact_text(artifacts_dir / "stderr.txt", max_chars=64 * 1024),
        "generated_files": _read_generated_files(artifacts_dir),
        "artifact_files": artifact_files,
        "artifacts_dir": str(artifacts_dir),
        "audit_timeline": relevant_events[:40],
        "has_approval": bool(approval),
        "can_cancel": bucket in {"inbox", "pending", "approvals"},
        "can_retry": bucket in {"failed", "canceled"},
        "can_delete": bucket in {"done", "failed", "canceled"},
    }


def _incident_archive_dir(queue_root: Path, suffix: str) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    out = queue_root / "_archive" / f"incident-{stamp}-{suffix}"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _job_artifact_flags(queue_root: Path, job_id: str) -> dict[str, bool]:
    artifacts_dir = queue_root / "artifacts" / Path(job_id).stem
    return {
        "plan": (artifacts_dir / "plan.json").exists(),
        "actions": (artifacts_dir / "actions.jsonl").exists(),
        "stdout": (artifacts_dir / "stdout.txt").exists(),
        "stderr": (artifacts_dir / "stderr.txt").exists(),
    }


def _last_activity(artifacts_dir: Path) -> str:
    actions = artifacts_dir / "actions.jsonl"
    if not actions.exists():
        return ""
    lines = actions.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in reversed(lines):
        if line.strip():
            return line[:180]
    return ""


def _job_ref_bucket(row: dict[str, Any]) -> str:
    bucket = str(row.get("bucket") or "")
    if bucket == "approvals":
        return "pending/approvals"
    return bucket


async def _jobs_redirect(request: Request, flash: str) -> RedirectResponse:
    bucket = (await _request_value(request, "bucket", "all")).strip() or "all"
    q = (await _request_value(request, "q", "")).strip()
    n_raw = (await _request_value(request, "n", "80")).strip()
    try:
        n = max(1, min(int(n_raw), 200))
    except ValueError:
        n = 80
    query = urlencode({"bucket": bucket, "q": q, "n": n, "flash": flash})
    return RedirectResponse(url=f"/jobs?{query}", status_code=303)


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
    queue = queue_snapshot(queue_root)
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
        auth_setup_banner=_auth_setup_banner(),
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


@app.get("/missions/templates/create")
def create_mission_template_get(
    request: Request,
    mission_id: str = "",
    title: str = "",
    goal: str = "",
    notes: str = "",
    steps_json: str = "[]",
):
    _enforce_get_mutations_enabled()
    _require_operator_auth_from_request(request)
    return _create_mission_template_from_values(mission_id, title, goal, notes, steps_json)


@app.post("/missions/templates/create")
async def create_mission_template(request: Request):
    await _require_mutation_guard(request)
    mission_id = await _request_value(request, "mission_id", "")
    title = await _request_value(request, "title", "")
    goal = await _request_value(request, "goal", "")
    notes = await _request_value(request, "notes", "")
    steps_json = await _request_value(request, "steps_json", "[]")
    return _create_mission_template_from_values(mission_id, title, goal, notes, steps_json)


def _create_panel_mission_from_values(prompt: str, approval_required: bool) -> RedirectResponse:
    normalized_prompt = prompt.strip()
    if not normalized_prompt:
        return RedirectResponse(url="/?error=panel_prompt_required", status_code=303)
    created, mission_id = _write_panel_mission_job(
        prompt=normalized_prompt,
        approval_required=approval_required,
    )
    return RedirectResponse(
        url=f"/?created={created}&mission_created={mission_id}",
        status_code=303,
    )


@app.get("/missions/create")
def create_mission_get(
    request: Request,
    prompt: str = "",
    approval_required: str = "1",
):
    _enforce_get_mutations_enabled()
    _require_operator_auth_from_request(request)
    return _create_panel_mission_from_values(prompt, approval_required != "0")


@app.post("/missions/create")
async def create_mission(request: Request):
    await _require_mutation_guard(request)
    prompt = (await _request_value(request, "prompt", "")).strip() or (
        await _request_value(request, "goal", "")
    ).strip()
    approval_raw = await _request_value(request, "approval_required", "1")
    return _create_panel_mission_from_values(prompt, approval_raw not in {"0", "false", "off"})


@app.post("/queue/approvals/{ref}/approve")
async def approve_queue_job(ref: str, request: Request):
    await _require_mutation_guard(request)
    daemon = MissionQueueDaemon(queue_root=_queue_root())
    try:
        await anyio.to_thread.run_sync(
            lambda: daemon.resolve_approval(daemon.canonicalize_approval_ref(ref), approve=True)
        )
    except FileNotFoundError:
        return await _jobs_redirect(request, "approval_not_found")
    except ValueError:
        return await _jobs_redirect(request, "approval_invalid")
    return await _jobs_redirect(request, "approved")


@app.post("/queue/approvals/{ref}/approve-always")
async def approve_always_queue_job(ref: str, request: Request):
    await _require_mutation_guard(request)
    daemon = MissionQueueDaemon(queue_root=_queue_root())
    try:
        await anyio.to_thread.run_sync(
            lambda: daemon.resolve_approval(
                daemon.canonicalize_approval_ref(ref), approve=True, approve_always=True
            )
        )
    except FileNotFoundError:
        return await _jobs_redirect(request, "approval_not_found")
    except ValueError:
        return await _jobs_redirect(request, "approval_invalid")
    return await _jobs_redirect(request, "approved_always")


@app.post("/queue/approvals/{ref}/deny")
async def deny_queue_job(ref: str, request: Request):
    await _require_mutation_guard(request)
    daemon = MissionQueueDaemon(queue_root=_queue_root())
    try:
        await anyio.to_thread.run_sync(
            lambda: daemon.resolve_approval(daemon.canonicalize_approval_ref(ref), approve=False)
        )
    except FileNotFoundError:
        return await _jobs_redirect(request, "approval_not_found")
    except ValueError:
        return await _jobs_redirect(request, "approval_invalid")
    return await _jobs_redirect(request, "denied")


@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request, bucket: str = "all", q: str = "", n: int = 80, flash: str = ""):
    queue_root = _queue_root()
    rows = list_jobs(queue_root, bucket=bucket, q=q, limit=n)
    rows_enriched: list[dict[str, Any]] = []
    for row in rows:
        job_id = str(row.get("job_id") or "")
        artifacts_dir = queue_root / "artifacts" / Path(job_id).stem
        enriched = dict(row)
        enriched["bucket_ref"] = _job_ref_bucket(row)
        enriched["artifacts"] = _job_artifact_flags(queue_root, job_id)
        enriched["last_activity"] = _last_activity(artifacts_dir)
        row_bucket = str(row.get("bucket") or "")
        enriched["can_cancel"] = row_bucket in {"inbox", "pending", "approvals"}
        enriched["can_retry"] = row_bucket in {"failed", "canceled"}
        enriched["can_delete"] = row_bucket in {"done", "failed", "canceled"}
        enriched["can_bundle"] = row_bucket == "done"
        rows_enriched.append(enriched)

    log(
        {
            "event": "panel_jobs_render",
            "bucket": bucket,
            "query": q[:120],
            "limit": max(1, min(n, 200)),
            "count": len(rows_enriched),
        }
    )

    tmpl = templates.get_template("jobs.html")
    csrf_token = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(24)
    html = tmpl.render(
        rows=rows_enriched,
        bucket=bucket if bucket in {*JOB_BUCKETS, "all"} else "pending",
        q=q,
        n=max(1, min(n, 200)),
        buckets=["all", *JOB_BUCKETS],
        flash=FLASH_MESSAGES.get(flash, ""),
        csrf_token=csrf_token,
        auth_setup_banner=_auth_setup_banner(),
    )
    response = HTMLResponse(content=html)
    response.set_cookie(CSRF_COOKIE, csrf_token, httponly=False, samesite="strict")
    return response


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def jobs_detail(job_id: str, request: Request):
    payload = _job_detail_payload(_queue_root(), job_id)
    tmpl = templates.get_template("job_detail.html")
    csrf_token = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(24)
    html = tmpl.render(payload=payload, csrf_token=csrf_token)
    response = HTMLResponse(content=html)
    response.set_cookie(CSRF_COOKIE, csrf_token, httponly=False, samesite="strict")
    return response


@app.get("/queue/jobs/{job}/detail", response_class=HTMLResponse)
def queue_job_detail(job: str, request: Request):
    return jobs_detail(job, request)


@app.get("/jobs/{job_id}/bundle")
def job_bundle(job_id: str, request: Request):
    _require_operator_auth_from_request(request)
    queue_root = _queue_root()
    stem = Path(job_id).stem
    archive_dir = _incident_archive_dir(queue_root, stem or "job")
    started = time.perf_counter()
    log(
        {
            "event": "bundle_build_started",
            "bundle": "job",
            "job_ref": job_id,
            "archive_dir": str(archive_dir),
        }
    )
    try:
        out = build_job_bundle(queue_root, job_id, archive_dir=archive_dir)
    except Exception as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        log(
            {
                "event": "bundle_build_failed",
                "bundle": "job",
                "job_ref": job_id,
                "duration_ms": duration_ms,
                "error": type(exc).__name__,
            }
        )
        if isinstance(exc, BundleError):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise
    duration_ms = int((time.perf_counter() - started) * 1000)
    size_bytes = out.stat().st_size
    log(
        {
            "event": "bundle_build_ok",
            "bundle": "job",
            "job_ref": job_id,
            "duration_ms": duration_ms,
            "bytes": size_bytes,
            "path": str(out),
        }
    )
    return FileResponse(
        path=out,
        media_type="application/zip",
        filename=out.name,
    )


@app.get("/bundle/system")
def system_bundle(request: Request):
    _require_operator_auth_from_request(request)
    queue_root = _queue_root()
    archive_dir = _incident_archive_dir(queue_root, "system")
    started = time.perf_counter()
    log({"event": "bundle_build_started", "bundle": "system", "archive_dir": str(archive_dir)})
    out = build_system_bundle(queue_root, archive_dir=archive_dir)
    duration_ms = int((time.perf_counter() - started) * 1000)
    size_bytes = out.stat().st_size
    log(
        {
            "event": "bundle_build_ok",
            "bundle": "system",
            "duration_ms": duration_ms,
            "bytes": size_bytes,
            "path": str(out),
        }
    )
    return FileResponse(
        path=out,
        media_type="application/zip",
        filename=out.name,
    )


@app.post("/queue/jobs/{ref}/cancel")
async def cancel_queue_job(ref: str, request: Request):
    await _require_mutation_guard(request)
    queue_root = _queue_root()
    lookup = lookup_job(queue_root, ref)
    if lookup and lookup.bucket in {"done", "failed", "canceled"}:
        _panel_security_counter_incr("panel_4xx_count", last_error="cancel_terminal_job_rejected")
        return await _jobs_redirect(request, "cannot_cancel_terminal")

    daemon = MissionQueueDaemon(queue_root=queue_root)
    try:
        daemon.cancel_job(ref)
    except FileNotFoundError:
        _panel_security_counter_incr("panel_4xx_count", last_error="cancel_job_not_found")
        return await _jobs_redirect(request, "cancel_not_found")
    return await _jobs_redirect(request, "canceled")


@app.post("/queue/jobs/{ref}/retry")
async def retry_queue_job(ref: str, request: Request):
    await _require_mutation_guard(request)
    daemon = MissionQueueDaemon(queue_root=_queue_root())
    daemon.retry_job(ref)
    return await _jobs_redirect(request, "retried")


@app.post("/queue/jobs/{ref}/delete")
async def delete_queue_job(ref: str, request: Request):
    await _require_mutation_guard(request)
    confirm = await _request_value(request, "confirm", "")
    daemon = MissionQueueDaemon(queue_root=_queue_root())
    try:
        daemon.delete_terminal_job(ref, confirm=confirm)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return await _jobs_redirect(request, "deleted")


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
