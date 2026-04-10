"""Panel routes for automation inspection and control.

This module adds operator-facing routes for browsing saved automation
definitions, inspecting their detail and run history, toggling enabled
state, and optionally forcing an immediate run through the existing
canonical runner / inbox path.

Architectural rule (do not break):

> Automation is deferred queue submission, not alternate execution.

The panel must never bypass the queue or execute payloads directly.
``run-now`` goes through ``process_automation_definition(force=True)``
which submits via the inbox — the queue remains the execution boundary.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..audit import log
from ..automation.history import list_history_records
from ..automation.runner import process_automation_definition
from ..automation.store import (
    AutomationNotFoundError,
    AutomationStoreError,
    list_automation_definitions,
    load_automation_definition,
    save_automation_definition,
)

AUTOMATION_FLASH_MESSAGES: dict[str, str] = {
    "enabled": "Automation enabled.",
    "disabled": "Automation disabled.",
    "already_enabled": "Automation is already enabled.",
    "already_disabled": "Automation is already disabled.",
    "run_submitted": "Run submitted to queue.",
    "run_skipped": "Run skipped by runner.",
    "run_error": "Run encountered an error.",
    "not_found": "Automation not found.",
    "store_error": "Failed to load or save automation.",
}


def _format_ts_ms(ts_ms: int | None) -> str:
    if ts_ms is None or ts_ms <= 0:
        return "\u2014"
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _safe_json_pretty(value: Any) -> str:
    try:
        return json.dumps(value, indent=2, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def register_automation_routes(
    app: FastAPI,
    *,
    templates: Any,
    csrf_cookie: str,
    queue_root: Callable[[], Path],
    require_mutation_guard: Callable[[Request], Awaitable[None]],
    panel_security_counter_incr: Callable[..., None],
    auth_setup_banner: Callable[[], dict[str, str] | None],
    format_ts_ms: Callable[[int | None], str] | None = None,
) -> None:
    ts_fmt = format_ts_ms or _format_ts_ms

    # ------------------------------------------------------------------
    # GET /automations — list page
    # ------------------------------------------------------------------
    @app.get("/automations", response_class=HTMLResponse)
    def automations_list(request: Request, flash: str = ""):
        root = queue_root()
        try:
            definitions = list_automation_definitions(root)
        except AutomationStoreError:
            definitions = []

        rows: list[dict[str, Any]] = []
        for defn in definitions:
            rows.append(
                {
                    "id": defn.id,
                    "title": defn.title,
                    "enabled": defn.enabled,
                    "trigger_kind": defn.trigger_kind,
                    "next_run_at_ms": defn.next_run_at_ms,
                    "next_run_at": ts_fmt(defn.next_run_at_ms),
                    "last_run_at_ms": defn.last_run_at_ms,
                    "last_run_at": ts_fmt(defn.last_run_at_ms),
                    "last_job_ref": defn.last_job_ref or "\u2014",
                }
            )

        log(
            {
                "event": "panel_automations_render",
                "count": len(rows),
            }
        )

        tmpl = templates.get_template("automations.html")
        csrf_token = request.cookies.get(csrf_cookie) or secrets.token_urlsafe(24)
        html = tmpl.render(
            rows=rows,
            flash=AUTOMATION_FLASH_MESSAGES.get(flash, ""),
            csrf_token=csrf_token,
            auth_setup_banner=auth_setup_banner(),
        )
        response = HTMLResponse(content=html)
        response.set_cookie(csrf_cookie, csrf_token, httponly=False, samesite="strict")
        return response

    # ------------------------------------------------------------------
    # GET /automations/{automation_id} — detail page
    # ------------------------------------------------------------------
    @app.get("/automations/{automation_id}", response_class=HTMLResponse)
    def automation_detail(automation_id: str, request: Request, flash: str = ""):
        root = queue_root()
        try:
            defn = load_automation_definition(automation_id, root)
        except AutomationNotFoundError:
            return RedirectResponse(url="/automations?flash=not_found", status_code=303)
        except AutomationStoreError:
            return RedirectResponse(url="/automations?flash=store_error", status_code=303)

        history: list[dict[str, Any]] = []
        try:
            raw_records = list_history_records(root, automation_id)
            for rec in raw_records:
                history.append(
                    {
                        "run_id": rec.get("run_id", "\u2014"),
                        "triggered_at": ts_fmt(rec.get("triggered_at_ms")),
                        "outcome": rec.get("outcome", "\u2014"),
                        "queue_job_ref": rec.get("queue_job_ref") or "\u2014",
                        "message": rec.get("message", "\u2014"),
                    }
                )
        except (ValueError, OSError):
            pass

        detail: dict[str, Any] = {
            "id": defn.id,
            "title": defn.title,
            "description": defn.description,
            "enabled": defn.enabled,
            "trigger_kind": defn.trigger_kind,
            "trigger_config_json": _safe_json_pretty(defn.trigger_config),
            "payload_template_json": _safe_json_pretty(defn.payload_template),
            "created_at": ts_fmt(defn.created_at_ms),
            "updated_at": ts_fmt(defn.updated_at_ms),
            "next_run_at": ts_fmt(defn.next_run_at_ms),
            "last_run_at": ts_fmt(defn.last_run_at_ms),
            "last_job_ref": defn.last_job_ref or "\u2014",
            "policy_posture": defn.policy_posture,
            "created_from": defn.created_from,
        }

        log(
            {
                "event": "panel_automation_detail_render",
                "automation_id": automation_id,
                "history_count": len(history),
            }
        )

        tmpl = templates.get_template("automation_detail.html")
        csrf_token = request.cookies.get(csrf_cookie) or secrets.token_urlsafe(24)
        html = tmpl.render(
            detail=detail,
            history=history,
            flash=AUTOMATION_FLASH_MESSAGES.get(flash, ""),
            csrf_token=csrf_token,
            auth_setup_banner=auth_setup_banner(),
        )
        response = HTMLResponse(content=html)
        response.set_cookie(csrf_cookie, csrf_token, httponly=False, samesite="strict")
        return response

    # ------------------------------------------------------------------
    # POST /automations/{automation_id}/enable
    # ------------------------------------------------------------------
    @app.post("/automations/{automation_id}/enable")
    async def automation_enable(automation_id: str, request: Request):
        await require_mutation_guard(request)
        root = queue_root()
        try:
            defn = load_automation_definition(automation_id, root)
        except AutomationNotFoundError:
            panel_security_counter_incr("panel_4xx_count", last_error="automation_not_found")
            return RedirectResponse(url="/automations?flash=not_found", status_code=303)
        except AutomationStoreError:
            return RedirectResponse(url="/automations?flash=store_error", status_code=303)

        if defn.enabled:
            return RedirectResponse(
                url=f"/automations/{automation_id}?flash=already_enabled",
                status_code=303,
            )

        updated = defn.model_copy(update={"enabled": True})
        try:
            save_automation_definition(updated, root)
        except (AutomationStoreError, OSError):
            return RedirectResponse(
                url=f"/automations/{automation_id}?flash=store_error",
                status_code=303,
            )

        log(
            {
                "event": "panel_automation_enabled",
                "automation_id": automation_id,
            }
        )
        return RedirectResponse(url=f"/automations/{automation_id}?flash=enabled", status_code=303)

    # ------------------------------------------------------------------
    # POST /automations/{automation_id}/disable
    # ------------------------------------------------------------------
    @app.post("/automations/{automation_id}/disable")
    async def automation_disable(automation_id: str, request: Request):
        await require_mutation_guard(request)
        root = queue_root()
        try:
            defn = load_automation_definition(automation_id, root)
        except AutomationNotFoundError:
            panel_security_counter_incr("panel_4xx_count", last_error="automation_not_found")
            return RedirectResponse(url="/automations?flash=not_found", status_code=303)
        except AutomationStoreError:
            return RedirectResponse(url="/automations?flash=store_error", status_code=303)

        if not defn.enabled:
            return RedirectResponse(
                url=f"/automations/{automation_id}?flash=already_disabled",
                status_code=303,
            )

        updated = defn.model_copy(update={"enabled": False})
        try:
            save_automation_definition(updated, root)
        except (AutomationStoreError, OSError):
            return RedirectResponse(
                url=f"/automations/{automation_id}?flash=store_error",
                status_code=303,
            )

        log(
            {
                "event": "panel_automation_disabled",
                "automation_id": automation_id,
            }
        )
        return RedirectResponse(url=f"/automations/{automation_id}?flash=disabled", status_code=303)

    # ------------------------------------------------------------------
    # POST /automations/{automation_id}/run-now
    # ------------------------------------------------------------------
    @app.post("/automations/{automation_id}/run-now")
    async def automation_run_now(automation_id: str, request: Request):
        """Force an immediate run through the canonical runner / inbox path.

        This is queue-submitting only — the panel never bypasses the queue
        or executes payloads directly.
        """
        await require_mutation_guard(request)
        root = queue_root()
        try:
            defn = load_automation_definition(automation_id, root)
        except AutomationNotFoundError:
            panel_security_counter_incr("panel_4xx_count", last_error="automation_not_found")
            return RedirectResponse(url="/automations?flash=not_found", status_code=303)
        except AutomationStoreError:
            return RedirectResponse(url="/automations?flash=store_error", status_code=303)

        result = process_automation_definition(defn, root, force=True)

        log(
            {
                "event": "panel_automation_run_now",
                "automation_id": automation_id,
                "outcome": result.outcome,
                "queue_job_ref": result.queue_job_ref,
            }
        )

        flash_key = {
            "submitted": "run_submitted",
            "skipped": "run_skipped",
            "error": "run_error",
        }.get(result.outcome, "run_error")

        return RedirectResponse(
            url=f"/automations/{automation_id}?flash={flash_key}",
            status_code=303,
        )
