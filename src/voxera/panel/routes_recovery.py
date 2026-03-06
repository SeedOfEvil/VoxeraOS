from __future__ import annotations

import os
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from starlette.background import BackgroundTask


def register_recovery_routes(
    app: FastAPI,
    *,
    templates: Any,
    queue_root: Callable[[], Path],
    require_operator_auth_from_request: Callable[[Request], None],
    recovery_zip_max_files: int,
    recovery_zip_max_total_bytes: int,
) -> None:
    def _is_within_path(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _bucket_base_dir(bucket: str) -> Path:
        root = queue_root()
        if bucket == "recovery":
            return root / "recovery"
        if bucket == "quarantine":
            return root / "quarantine"
        raise HTTPException(status_code=404, detail="Not found")

    def _dir_metrics(root: Path) -> tuple[int, int]:
        file_count = 0
        total_size = 0
        for current_root, dir_names, file_names in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current_root)
            dir_names[:] = [
                name for name in sorted(dir_names) if not (current_path / name).is_symlink()
            ]
            for file_name in sorted(file_names):
                file_path = current_path / file_name
                if file_path.is_symlink() or not file_path.is_file():
                    continue
                stat = file_path.stat()
                file_count += 1
                total_size += int(stat.st_size)
        return file_count, total_size

    def _collect_bucket_items(bucket: str) -> list[dict[str, Any]]:
        base = _bucket_base_dir(bucket)
        if not base.exists() or not base.is_dir():
            return []

        items: list[dict[str, Any]] = []
        for child in sorted(base.iterdir(), key=lambda entry: entry.name):
            if child.is_symlink():
                continue
            stat = child.stat()
            if child.is_dir():
                file_count, size_bytes = _dir_metrics(child)
                kind = "dir"
            elif child.is_file():
                file_count, size_bytes = 1, int(stat.st_size)
                kind = "file"
            else:
                continue
            items.append(
                {
                    "name": child.name,
                    "kind": kind,
                    "mtime_ts": int(stat.st_mtime),
                    "size_bytes": size_bytes,
                    "file_count": file_count,
                }
            )
        return items

    def _build_recovery_zip(target: Path, zip_path: Path) -> None:
        files_added = 0
        total_size = 0

        with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            if target.is_file() and not target.is_symlink():
                total_size = int(target.stat().st_size)
                if total_size > recovery_zip_max_total_bytes:
                    raise HTTPException(status_code=413, detail="Requested archive too large")
                zf.write(target, arcname=target.name)
                return

            for current_root, dir_names, file_names in os.walk(
                target, topdown=True, followlinks=False
            ):
                current_path = Path(current_root)
                dir_names[:] = [
                    name for name in sorted(dir_names) if not (current_path / name).is_symlink()
                ]
                for file_name in sorted(file_names):
                    file_path = current_path / file_name
                    if file_path.is_symlink() or not file_path.is_file():
                        continue
                    stat = file_path.stat()
                    files_added += 1
                    total_size += int(stat.st_size)
                    if files_added > recovery_zip_max_files:
                        raise HTTPException(
                            status_code=413, detail="Requested archive has too many files"
                        )
                    if total_size > recovery_zip_max_total_bytes:
                        raise HTTPException(status_code=413, detail="Requested archive too large")
                    arcname = file_path.relative_to(target)
                    zf.write(file_path, arcname=str(arcname))

    @app.get("/recovery", response_class=HTMLResponse)
    def recovery_page(request: Request):
        recovery_sessions = _collect_bucket_items("recovery")
        quarantine_sessions = _collect_bucket_items("quarantine")
        tmpl = templates.get_template("recovery.html")
        html = tmpl.render(
            recovery_sessions=recovery_sessions,
            quarantine_sessions=quarantine_sessions,
        )
        return HTMLResponse(content=html)

    @app.get("/recovery/download/{bucket}/{name}")
    def recovery_download(bucket: str, name: str, request: Request):
        require_operator_auth_from_request(request)
        if "/" in name or "\\" in name or not name or Path(name).name != name:
            raise HTTPException(status_code=404, detail="Not found")

        base = _bucket_base_dir(bucket).resolve()
        target = (base / name).resolve()
        if not _is_within_path(target, base) or not target.exists() or target.is_symlink():
            raise HTTPException(status_code=404, detail="Not found")
        if not target.is_file() and not target.is_dir():
            raise HTTPException(status_code=404, detail="Not found")

        fd, temp_name = tempfile.mkstemp(prefix="voxera-recovery-", suffix=".zip")
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            _build_recovery_zip(target, temp_path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

        def _zip_file_iterator(path: Path):
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk

        filename = f"{bucket}-{name}.zip"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        return StreamingResponse(
            _zip_file_iterator(temp_path),
            media_type="application/zip",
            headers=headers,
            background=BackgroundTask(lambda: temp_path.unlink(missing_ok=True)),
        )
