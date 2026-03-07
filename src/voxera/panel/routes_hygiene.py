from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..health import read_health_snapshot
from ..health_reset import EVENT_BY_SCOPE, HealthResetError, reset_health_snapshot
from .helpers import request_value


def _hygiene_result_view(result: Any, *, kind: str) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"state": "empty", "summary": "No runs yet.", "details": {}}

    ok = bool(result.get("ok"))
    if kind == "prune":
        would_remove = int(result.get("would_remove_jobs") or 0)
        state = "good" if ok and would_remove == 0 else ("problem" if not ok else "warn")
        summary = f"ok={ok} · would_remove_jobs={would_remove} · at={int(result.get('ts_ms') or 0)}"
    else:
        issue_counts_raw = result.get("issue_counts")
        issue_counts: dict[str, Any] = (
            issue_counts_raw if isinstance(issue_counts_raw, dict) else {}
        )
        total_issues = 0
        for value in issue_counts.values():
            try:
                total_issues += int(value or 0)
            except (TypeError, ValueError):
                continue
        state = "good" if ok and total_issues == 0 else ("problem" if not ok else "warn")
        summary = f"ok={ok} · total_issues={total_issues} · at={int(result.get('ts_ms') or 0)}"

    return {
        "state": state,
        "summary": summary,
        "details": result,
    }


def register_hygiene_routes(
    app: FastAPI,
    *,
    templates: Any,
    csrf_cookie: str,
    flash_messages: dict[str, str],
    queue_root: Callable[[], Path],
    health_queue_root: Callable[[], Path | None],
    require_operator_auth_from_request: Callable[[Request], None],
    require_mutation_guard: Callable[[Request], Awaitable[None]],
    run_queue_hygiene_command: Callable[[Path, list[str]], dict[str, Any]],
    write_hygiene_result: Callable[[Path, str, dict[str, Any]], None],
    now_ms: Callable[[], int],
    audit_log: Callable[[dict[str, Any]], None],
) -> None:
    def _hygiene_redirect(request: Request, flash: str) -> RedirectResponse:
        url = str(request.url_for("hygiene_page"))
        sep = "&" if "?" in url else "?"
        return RedirectResponse(url=f"{url}{sep}flash={flash}", status_code=303)

    @app.get("/hygiene", response_class=HTMLResponse)
    def hygiene_page(request: Request, flash: str = ""):
        require_operator_auth_from_request(request)
        health = read_health_snapshot(health_queue_root())
        tmpl = templates.get_template("hygiene.html")
        csrf_token = request.cookies.get(csrf_cookie) or secrets.token_urlsafe(24)
        html = tmpl.render(
            last_prune_result=health.get("last_prune_result"),
            last_reconcile_result=health.get("last_reconcile_result"),
            prune_view=_hygiene_result_view(health.get("last_prune_result"), kind="prune"),
            reconcile_view=_hygiene_result_view(
                health.get("last_reconcile_result"), kind="reconcile"
            ),
            csrf_token=csrf_token,
            flash=flash_messages.get(flash, ""),
            hygiene_prune_url=str(request.url_for("hygiene_prune_dry_run")),
            hygiene_reconcile_url=str(request.url_for("hygiene_reconcile")),
            hygiene_health_reset_url=str(request.url_for("hygiene_health_reset")),
        )
        response = HTMLResponse(content=html)
        response.set_cookie(csrf_cookie, csrf_token, httponly=False, samesite="strict")
        return response

    @app.post("/hygiene/prune-dry-run")
    async def hygiene_prune_dry_run(request: Request):
        await require_mutation_guard(request)
        root = queue_root()
        run = run_queue_hygiene_command(root, ["queue", "prune", "--json"])
        parsed = run["result"]
        per_bucket = parsed.get("per_bucket") if isinstance(parsed.get("per_bucket"), dict) else {}
        removed_jobs = int(
            sum(int((per_bucket.get(b) or {}).get("pruned", 0) or 0) for b in per_bucket)
        )
        result = {
            "ts_ms": now_ms(),
            "mode": "dry-run",
            "ok": bool(run["ok"]),
            "removed_jobs": 0,
            "would_remove_jobs": removed_jobs,
            "removed_sidecars": 0,
            "reclaimed_bytes": parsed.get("reclaimed_bytes"),
            "by_bucket": per_bucket,
            "exit_code": run.get("exit_code"),
            "cmd": run.get("cmd"),
            "cwd": run.get("cwd"),
            "stdout_tail": run.get("stdout_tail", ""),
        }
        if run["stderr_tail"]:
            result["stderr_tail"] = run["stderr_tail"]
        if not run["ok"]:
            result["error"] = run["error"]
        write_hygiene_result(root, "last_prune_result", result)
        return JSONResponse({"ok": bool(run["ok"]), "result": result}, status_code=200)

    @app.post("/hygiene/reconcile")
    async def hygiene_reconcile(request: Request):
        await require_mutation_guard(request)
        root = queue_root()
        run = run_queue_hygiene_command(root, ["queue", "reconcile", "--json"])
        parsed = run["result"]
        issue_counts = (
            parsed.get("issue_counts") if isinstance(parsed.get("issue_counts"), dict) else {}
        )
        result = {
            "ts_ms": now_ms(),
            "ok": bool(run["ok"]),
            "issue_counts": issue_counts,
            "exit_code": run.get("exit_code"),
            "cmd": run.get("cmd"),
            "cwd": run.get("cwd"),
            "stdout_tail": run.get("stdout_tail", ""),
        }
        if run["stderr_tail"]:
            result["stderr_tail"] = run["stderr_tail"]
        if not run["ok"]:
            result["error"] = run["error"]
        write_hygiene_result(root, "last_reconcile_result", result)
        return JSONResponse({"ok": bool(run["ok"]), "result": result}, status_code=200)

    @app.post("/hygiene/health-reset")
    async def hygiene_health_reset(request: Request):
        await require_mutation_guard(request)
        scope = (await request_value(request, "scope", "current_and_recent")).strip()
        counter_group_raw = (await request_value(request, "counter_group", "")).strip()
        counter_group = counter_group_raw or None
        try:
            summary = reset_health_snapshot(
                health_queue_root(),
                scope=scope,
                counter_group=counter_group,
                actor_surface="panel",
            )
        except HealthResetError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        event_name = (
            "health_reset_historical_counters"
            if counter_group
            else EVENT_BY_SCOPE.get(scope, "health_reset")
        )
        audit_log(
            {
                "event": event_name,
                "scope": scope,
                "counter_group": counter_group,
                "actor_surface": "panel",
                "fields_changed": summary["changed_fields"],
                "timestamp_ms": summary["timestamp_ms"],
            }
        )
        flash = "health_reset_historical_counters" if counter_group else event_name
        return _hygiene_redirect(request, flash)
