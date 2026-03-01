from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path
from typing import Any

from .core.queue_inspect import JobLookup, lookup_job, queue_snapshot
from .health import read_health_snapshot
from .version import get_version

DEFAULT_TOTAL_CAP = 4 * 1024 * 1024
DEFAULT_FILE_CAP = 256 * 1024


class BundleError(RuntimeError):
    pass


def _zip_writestr(zf: zipfile.ZipFile, arcname: str, data: bytes) -> None:
    info = zipfile.ZipInfo(filename=arcname)
    info.date_time = (1980, 1, 1, 0, 0, 0)
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, data)


def _safe_read(path: Path, *, cap_bytes: int) -> tuple[bytes, bool, int]:
    raw = path.read_bytes()
    size = len(raw)
    truncated = size > cap_bytes
    return (raw[:cap_bytes], truncated, size)


def _to_pretty_json(payload: Any) -> bytes:
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")


def _add_file_with_caps(
    zf: zipfile.ZipFile,
    manifest_files: list[dict[str, Any]],
    *,
    source: Path,
    arcname: str,
    total_written: int,
    total_cap: int,
    file_cap: int,
) -> int:
    if not source.exists() or not source.is_file():
        return total_written
    data, truncated, original_size = _safe_read(source, cap_bytes=file_cap)
    if total_written + len(data) > total_cap:
        budget = max(0, total_cap - total_written)
        data = data[:budget]
        truncated = True

    _zip_writestr(zf, arcname, data)
    total_written += len(data)
    manifest_files.append(
        {
            "path": arcname,
            "source": str(source),
            "bytes_written": len(data),
            "bytes_original": original_size,
            "truncated": truncated,
        }
    )
    return total_written


def _job_lookup_or_raise(queue_root: Path, job_id: str) -> JobLookup:
    lookup = lookup_job(queue_root, job_id)
    if lookup is None:
        raise BundleError(f"job not found: {job_id}")
    return lookup


def build_job_bundle(
    queue_root: Path,
    job_id: str,
    *,
    total_cap: int = DEFAULT_TOTAL_CAP,
    file_cap: int = DEFAULT_FILE_CAP,
) -> bytes:
    lookup = _job_lookup_or_raise(queue_root, job_id)
    bio = io.BytesIO()
    manifest_files: list[dict[str, Any]] = []

    with zipfile.ZipFile(bio, mode="w") as zf:
        written = 0
        written = _add_file_with_caps(
            zf,
            manifest_files,
            source=lookup.primary_path,
            arcname="job.json",
            total_written=written,
            total_cap=total_cap,
            file_cap=file_cap,
        )
        if lookup.approval_path:
            written = _add_file_with_caps(
                zf,
                manifest_files,
                source=lookup.approval_path,
                arcname="approval.json",
                total_written=written,
                total_cap=total_cap,
                file_cap=file_cap,
            )
        if lookup.failed_sidecar_path:
            written = _add_file_with_caps(
                zf,
                manifest_files,
                source=lookup.failed_sidecar_path,
                arcname="failed.error.json",
                total_written=written,
                total_cap=total_cap,
                file_cap=file_cap,
            )

        if lookup.artifacts_dir.exists():
            for child in sorted(lookup.artifacts_dir.rglob("*")):
                if not child.is_file():
                    continue
                rel = child.relative_to(lookup.artifacts_dir)
                written = _add_file_with_caps(
                    zf,
                    manifest_files,
                    source=child,
                    arcname=f"artifacts/{rel.as_posix()}",
                    total_written=written,
                    total_cap=total_cap,
                    file_cap=file_cap,
                )
                if written >= total_cap:
                    break

        health = _to_pretty_json(read_health_snapshot(queue_root))
        _zip_writestr(zf, "health.json", health[:file_cap])
        manifest_files.append(
            {
                "path": "health.json",
                "source": str((queue_root / "health.json").resolve()),
                "bytes_written": min(len(health), file_cap),
                "bytes_original": len(health),
                "truncated": len(health) > file_cap,
            }
        )

        manifest = {
            "type": "job_incident_bundle",
            "app_version": get_version(),
            "created_at_ms": int(time.time() * 1000),
            "job_id": lookup.job_id,
            "bucket": lookup.bucket,
            "included_paths": {
                "job": str(lookup.primary_path),
                "approval": str(lookup.approval_path) if lookup.approval_path else None,
                "failed_sidecar": str(lookup.failed_sidecar_path)
                if lookup.failed_sidecar_path
                else None,
                "artifacts_dir": str(lookup.artifacts_dir),
            },
            "caps": {"total_bytes": total_cap, "file_bytes": file_cap},
            "files": manifest_files,
        }
        _zip_writestr(zf, "manifest.json", _to_pretty_json(manifest))

    return bio.getvalue()


def build_system_bundle(
    queue_root: Path,
    *,
    total_cap: int = DEFAULT_TOTAL_CAP,
    file_cap: int = DEFAULT_FILE_CAP,
) -> bytes:
    bio = io.BytesIO()
    manifest_files: list[dict[str, Any]] = []
    with zipfile.ZipFile(bio, mode="w") as zf:
        health_payload = _to_pretty_json(read_health_snapshot(queue_root))
        _zip_writestr(zf, "health.json", health_payload[:file_cap])
        manifest_files.append(
            {
                "path": "health.json",
                "bytes_written": min(len(health_payload), file_cap),
                "bytes_original": len(health_payload),
                "truncated": len(health_payload) > file_cap,
            }
        )

        queue_payload = _to_pretty_json(queue_snapshot(queue_root))
        _zip_writestr(zf, "queue_snapshot.json", queue_payload[:file_cap])
        manifest_files.append(
            {
                "path": "queue_snapshot.json",
                "bytes_written": min(len(queue_payload), file_cap),
                "bytes_original": len(queue_payload),
                "truncated": len(queue_payload) > file_cap,
            }
        )

        journal_note = b"""No privileged journal export included. Run (if available):
journalctl --user -u voxera-daemon.service -n 200 --no-pager
or check ~/.local/state/voxera for daemon logs.
"""
        _zip_writestr(zf, "journal_pointer.txt", journal_note)

        config_note = b"""Config pointers only (contents intentionally omitted):
- ~/.config/voxera/config.json     (runtime ops config: panel/queue settings)
- ~/.config/voxera/config.yml      (app config: brain/mode/privacy; written by voxera setup)
- ~/.config/voxera/secrets.env
- ~/VoxeraOS/notes/queue/health.json
"""
        _zip_writestr(zf, "config_pointers.txt", config_note)

        manifest = {
            "type": "system_incident_bundle",
            "app_version": get_version(),
            "created_at_ms": int(time.time() * 1000),
            "caps": {"total_bytes": total_cap, "file_bytes": file_cap},
            "files": manifest_files,
        }
        _zip_writestr(zf, "manifest.json", _to_pretty_json(manifest))
    return bio.getvalue()
