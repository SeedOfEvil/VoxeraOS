from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from ..core.queue_daemon import MissionQueueDaemon
from .helpers import request_value


def _safe_jobs_n(raw: str) -> int:
    try:
        return max(1, min(int(raw), 200))
    except ValueError:
        return 80


async def _jobs_redirect_local(request: Request, flash: str) -> RedirectResponse:
    params: dict[str, str | int] = {"flash": flash}
    bucket = (await request_value(request, "bucket", "")).strip()
    if bucket:
        params["bucket"] = bucket
    query = (await request_value(request, "q", "")).strip()
    if query:
        params["q"] = query
    n_raw = (await request_value(request, "n", "80")).strip()
    params["n"] = _safe_jobs_n(n_raw)

    return RedirectResponse(url=f"/jobs?{urlencode(params)}", status_code=303)


def register_queue_control_routes(
    app: FastAPI,
    *,
    queue_root: Callable[[], Path],
    require_mutation_guard: Callable[[Request], Awaitable[None]],
) -> None:
    @app.post("/queue/jobs/{ref}/delete")
    async def delete_queue_job(ref: str, request: Request):
        await require_mutation_guard(request)
        confirm = await request_value(request, "confirm", "")
        daemon = MissionQueueDaemon(queue_root=queue_root())
        try:
            daemon.delete_terminal_job(ref, confirm=confirm)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return await _jobs_redirect_local(request, "deleted")

    @app.post("/queue/pause")
    async def pause_queue(request: Request):
        await require_mutation_guard(request)
        daemon = MissionQueueDaemon(queue_root=queue_root())
        daemon.pause()
        return RedirectResponse(url="/", status_code=303)

    @app.post("/queue/resume")
    async def resume_queue(request: Request):
        await require_mutation_guard(request)
        daemon = MissionQueueDaemon(queue_root=queue_root())
        daemon.resume()
        return RedirectResponse(url="/", status_code=303)
