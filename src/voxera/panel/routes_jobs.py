from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..audit import log
from ..core.queue_daemon import MissionQueueDaemon
from ..core.queue_inspect import JOB_BUCKETS, list_jobs, lookup_job


def register_job_routes(
    app: FastAPI,
    *,
    templates: Any,
    csrf_cookie: str,
    flash_messages: dict[str, str],
    queue_root: Callable[[], Path],
    require_mutation_guard: Callable[[Request], Awaitable[None]],
    panel_security_counter_incr: Callable[..., None],
    job_ref_bucket: Callable[[dict[str, Any]], str],
    job_artifact_flags: Callable[[Path, str], dict[str, bool]],
    last_activity: Callable[[Path], str],
    job_detail_payload: Callable[[Path, str], dict[str, Any]],
    job_progress_payload: Callable[[Path, str], dict[str, Any]],
    auth_setup_banner: Callable[[], dict[str, str] | None],
) -> None:
    def _safe_jobs_n(raw: str) -> int:
        try:
            return max(1, min(int(raw), 200))
        except ValueError:
            return 80

    async def _jobs_redirect(request: Request, flash: str) -> RedirectResponse:
        from .helpers import request_value

        params: dict[str, str | int] = {"flash": flash}
        bucket = (await request_value(request, "bucket", "")).strip()
        if bucket:
            params["bucket"] = bucket
        query = (await request_value(request, "q", "")).strip()
        if query:
            params["q"] = query
        n_raw = (await request_value(request, "n", "80")).strip()
        params["n"] = _safe_jobs_n(n_raw)

        from urllib.parse import urlencode

        return RedirectResponse(url=f"/jobs?{urlencode(params)}", status_code=303)

    @app.post("/queue/approvals/{ref}/approve")
    async def approve_queue_job(ref: str, request: Request):
        import anyio

        await require_mutation_guard(request)
        daemon = MissionQueueDaemon(queue_root=queue_root())
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
        import anyio

        await require_mutation_guard(request)
        daemon = MissionQueueDaemon(queue_root=queue_root())
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
        import anyio

        await require_mutation_guard(request)
        daemon = MissionQueueDaemon(queue_root=queue_root())
        try:
            await anyio.to_thread.run_sync(
                lambda: daemon.resolve_approval(
                    daemon.canonicalize_approval_ref(ref), approve=False
                )
            )
        except FileNotFoundError:
            return await _jobs_redirect(request, "approval_not_found")
        except ValueError:
            return await _jobs_redirect(request, "approval_invalid")
        return await _jobs_redirect(request, "denied")

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs_page(request: Request, bucket: str = "all", q: str = "", n: int = 80, flash: str = ""):
        root = queue_root()
        rows = list_jobs(root, bucket=bucket, q=q, limit=n)
        rows_enriched: list[dict[str, Any]] = []
        for row in rows:
            job_id = str(row.get("job_id") or "")
            artifacts_dir = root / "artifacts" / Path(job_id).stem
            enriched = dict(row)
            enriched["bucket_ref"] = job_ref_bucket(row)
            enriched["artifacts"] = job_artifact_flags(root, job_id)
            enriched["last_activity"] = last_activity(artifacts_dir)
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
        csrf_token = request.cookies.get(csrf_cookie) or secrets.token_urlsafe(24)
        html = tmpl.render(
            rows=rows_enriched,
            bucket=bucket if bucket in {*JOB_BUCKETS, "all"} else "pending",
            q=q,
            n=max(1, min(n, 200)),
            buckets=["all", *JOB_BUCKETS],
            flash=flash_messages.get(flash, ""),
            csrf_token=csrf_token,
            auth_setup_banner=auth_setup_banner(),
        )
        response = HTMLResponse(content=html)
        response.set_cookie(csrf_cookie, csrf_token, httponly=False, samesite="strict")
        return response

    @app.get("/jobs/{job_id}/progress", response_class=JSONResponse)
    def jobs_detail_progress(job_id: str):
        return JSONResponse(job_progress_payload(queue_root(), job_id))

    @app.get("/queue/jobs/{job}/progress", response_class=JSONResponse)
    def queue_job_detail_progress(job: str):
        return jobs_detail_progress(job)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def jobs_detail(job_id: str, request: Request):
        payload = job_detail_payload(queue_root(), job_id)
        tmpl = templates.get_template("job_detail.html")
        csrf_token = request.cookies.get(csrf_cookie) or secrets.token_urlsafe(24)
        html = tmpl.render(payload=payload, csrf_token=csrf_token)
        response = HTMLResponse(content=html)
        response.set_cookie(csrf_cookie, csrf_token, httponly=False, samesite="strict")
        return response

    @app.get("/queue/jobs/{job}/detail", response_class=HTMLResponse)
    def queue_job_detail(job: str, request: Request):
        return jobs_detail(job, request)

    @app.post("/queue/jobs/{ref}/cancel")
    async def cancel_queue_job(ref: str, request: Request):
        await require_mutation_guard(request)
        root = queue_root()
        lookup = lookup_job(root, ref)
        if lookup and lookup.bucket in {"done", "failed", "canceled"}:
            panel_security_counter_incr(
                "panel_4xx_count", last_error="cancel_terminal_job_rejected"
            )
            return await _jobs_redirect(request, "cannot_cancel_terminal")

        daemon = MissionQueueDaemon(queue_root=root)
        try:
            daemon.cancel_job(ref)
        except FileNotFoundError:
            panel_security_counter_incr("panel_4xx_count", last_error="cancel_job_not_found")
            return await _jobs_redirect(request, "cancel_not_found")
        return await _jobs_redirect(request, "canceled")

    @app.post("/queue/jobs/{ref}/retry")
    async def retry_queue_job(ref: str, request: Request):
        await require_mutation_guard(request)
        daemon = MissionQueueDaemon(queue_root=queue_root())
        daemon.retry_job(ref)
        return await _jobs_redirect(request, "retried")
