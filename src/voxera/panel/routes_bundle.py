from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from ..audit import log
from ..incident_bundle import BundleError
from ..ops_bundle import build_job_bundle, build_system_bundle


def _incident_archive_dir(queue_root: Path, suffix: str) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    out = queue_root / "_archive" / f"incident-{stamp}-{suffix}"
    out.mkdir(parents=True, exist_ok=True)
    return out


def register_bundle_routes(
    app: FastAPI,
    *,
    queue_root: Callable[[], Path],
    require_operator_auth_from_request: Callable[[Request], None],
) -> None:
    @app.get("/jobs/{job_id}/bundle")
    def job_bundle(job_id: str, request: Request):
        require_operator_auth_from_request(request)
        current_queue_root = queue_root()
        stem = Path(job_id).stem
        archive_dir = _incident_archive_dir(current_queue_root, stem or "job")
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
            out = build_job_bundle(current_queue_root, job_id, archive_dir=archive_dir)
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
        require_operator_auth_from_request(request)
        current_queue_root = queue_root()
        archive_dir = _incident_archive_dir(current_queue_root, "system")
        started = time.perf_counter()
        log({"event": "bundle_build_started", "bundle": "system", "archive_dir": str(archive_dir)})
        out = build_system_bundle(current_queue_root, archive_dir=archive_dir)
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
