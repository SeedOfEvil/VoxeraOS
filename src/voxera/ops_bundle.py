from __future__ import annotations

import json
import subprocess
import time
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import config_fingerprint, write_config_snapshot
from .config import load_config as load_runtime_config
from .core.queue_daemon import MissionQueueDaemon
from .core.queue_inspect import lookup_job
from .version import get_version

_TEXT_TRUNCATE_BYTES = 256 * 1024


def _git_sha() -> str | None:
    try:
        out = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return None
    return out or None


def _archive_dir(queue_root: Path) -> Path:
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    out = queue_root / "_archive" / ts
    out.mkdir(parents=True, exist_ok=True)
    return out


def _resolve_archive_dir(
    queue_root: Path,
    archive_dir: Path | None,
    *,
    prefer_queue_root_archive: bool = False,
) -> Path:
    if archive_dir is not None:
        out_dir = archive_dir
    elif prefer_queue_root_archive:
        out_dir = _archive_dir(queue_root)
    else:
        settings = load_runtime_config()
        out_dir = settings.ops_bundle_dir if settings.ops_bundle_dir else _archive_dir(queue_root)
    out_dir = out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _write_zip(out: Path, write: Callable[[zipfile.ZipFile], None]) -> None:
    tmp = out.with_name(f".{out.name}.{uuid.uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(tmp, mode="w") as zf:
            write(zf)
        tmp.replace(out)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _zip_write_bytes(zf: zipfile.ZipFile, arcname: str, data: bytes) -> None:
    info = zipfile.ZipInfo(filename=arcname)
    info.date_time = (1980, 1, 1, 0, 0, 0)
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, data)


def _zip_write_text(zf: zipfile.ZipFile, arcname: str, text: str) -> None:
    _zip_write_bytes(zf, arcname, text.encode("utf-8"))


def _read_truncated(path: Path, *, cap: int = _TEXT_TRUNCATE_BYTES) -> tuple[bytes, bool, int]:
    raw = path.read_bytes()
    size = len(raw)
    if size <= cap:
        return raw, False, size
    return raw[:cap], True, size


def _manifest(queue_root: Path) -> dict[str, Any]:
    return {
        "timestamp_ms": int(time.time() * 1000),
        "voxera_version": get_version(),
        "git_sha": _git_sha(),
        "queue_root": str(queue_root.resolve()),
    }


def _ensure_config_snapshot(queue_root: Path) -> tuple[Path | None, Path | None, str | None]:
    snapshot = queue_root / "config_snapshot.json"
    fingerprint = queue_root / "config_snapshot.sha256"
    note = None
    if not snapshot.exists() or not fingerprint.exists():
        try:
            settings = load_runtime_config()
            if settings.queue_root.expanduser().resolve() != queue_root:
                settings = load_runtime_config(overrides={"queue_root": queue_root})
            snapshot = write_config_snapshot(queue_root, settings)
            fingerprint.write_text(config_fingerprint(settings) + "\n", encoding="utf-8")
        except Exception as exc:
            note = f"config snapshot unavailable: {type(exc).__name__}\n"
    return (
        snapshot if snapshot.exists() else None,
        fingerprint if fingerprint.exists() else None,
        note,
    )


def build_system_bundle(
    queue_root: Path,
    archive_dir: Path | None = None,
    *,
    prefer_queue_root_archive: bool = False,
) -> Path:
    queue_root = queue_root.expanduser().resolve()
    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.ensure_dirs()
    out_dir = _resolve_archive_dir(
        queue_root, archive_dir, prefer_queue_root_archive=prefer_queue_root_archive
    )
    out = out_dir / "bundle-system.zip"

    status = daemon.status_snapshot(approvals_limit=8, failed_limit=8)
    status_text = json.dumps(status, indent=2, sort_keys=True)
    config_snapshot, config_fingerprint_path, snapshot_note = _ensure_config_snapshot(queue_root)

    def write(zf: zipfile.ZipFile) -> None:
        _zip_write_bytes(
            zf,
            "manifest.json",
            json.dumps(_manifest(queue_root), indent=2, sort_keys=True).encode("utf-8"),
        )
        _zip_write_text(zf, "snapshots/queue_status.txt", status_text + "\n")
        if config_snapshot is not None:
            _zip_write_bytes(zf, "snapshots/config_snapshot.json", config_snapshot.read_bytes())
        else:
            _zip_write_text(
                zf,
                "notes/config_snapshot_missing.txt",
                snapshot_note or "config snapshot missing\n",
            )

        if config_fingerprint_path is not None:
            _zip_write_bytes(
                zf,
                "snapshots/config_snapshot.sha256",
                config_fingerprint_path.read_bytes(),
            )
        else:
            _zip_write_text(
                zf,
                "notes/config_snapshot_fingerprint_missing.txt",
                snapshot_note or "config snapshot fingerprint missing\n",
            )

        health = queue_root / "health.json"
        if health.exists():
            _zip_write_bytes(zf, "snapshots/queue_health.json", health.read_bytes())
        else:
            _zip_write_text(zf, "snapshots/queue_health.json", "health.json missing\n")

        lock = queue_root / ".daemon.lock"
        if lock.exists():
            _zip_write_bytes(zf, "snapshots/daemon_lock.json", lock.read_bytes())
        else:
            _zip_write_text(zf, "snapshots/daemon_lock.json", "daemon lock missing\n")

        try:
            tail = subprocess.check_output(
                [
                    "journalctl",
                    "--user",
                    "-u",
                    "voxera-daemon.service",
                    "-n",
                    "200",
                    "--no-pager",
                ],
                text=True,
                stderr=subprocess.STDOUT,
            )
            _zip_write_text(zf, "journal_voxera_daemon_tail.txt", tail)
        except Exception as exc:
            _zip_write_text(
                zf,
                "journal_voxera_daemon_tail.txt",
                f"journalctl unavailable: {type(exc).__name__}\n",
            )

        _zip_write_text(
            zf,
            "panel_log_hint.txt",
            "panel logs not collected automatically\n",
        )

    _write_zip(out, write)

    return out


def build_job_bundle(
    queue_root: Path,
    job_ref: str,
    archive_dir: Path | None = None,
    *,
    prefer_queue_root_archive: bool = False,
) -> Path:
    queue_root = queue_root.expanduser().resolve()
    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.ensure_dirs()
    out_dir = _resolve_archive_dir(
        queue_root, archive_dir, prefer_queue_root_archive=prefer_queue_root_archive
    )

    lookup = lookup_job(queue_root, job_ref)
    job_stem = Path(job_ref).stem
    out = out_dir / f"bundle-job-{job_stem}.zip"

    config_snapshot, config_fingerprint_path, snapshot_note = _ensure_config_snapshot(queue_root)

    searched = [
        str(queue_root / "done" / f"{job_stem}.json"),
        str(queue_root / "failed" / f"{job_stem}.json"),
        str(queue_root / "pending" / f"{job_stem}.json"),
        str(queue_root / "inbox" / f"{job_stem}.json"),
    ]

    def write(zf: zipfile.ZipFile) -> None:
        _zip_write_bytes(
            zf,
            "manifest.json",
            json.dumps(_manifest(queue_root), indent=2, sort_keys=True).encode("utf-8"),
        )

        if config_snapshot is not None:
            _zip_write_bytes(zf, "snapshots/config_snapshot.json", config_snapshot.read_bytes())
        else:
            _zip_write_text(
                zf,
                "notes/config_snapshot_missing.txt",
                snapshot_note or "config snapshot missing\n",
            )

        if config_fingerprint_path is not None:
            _zip_write_bytes(
                zf,
                "snapshots/config_snapshot.sha256",
                config_fingerprint_path.read_bytes(),
            )
        else:
            _zip_write_text(
                zf,
                "notes/config_snapshot_fingerprint_missing.txt",
                snapshot_note or "config snapshot fingerprint missing\n",
            )

        if lookup and lookup.primary_path.exists():
            _zip_write_bytes(
                zf, f"job/{lookup.primary_path.name}", lookup.primary_path.read_bytes()
            )
        else:
            _zip_write_text(
                zf,
                "notes/job_not_found.txt",
                "job file not found; searched:\n" + "\n".join(searched) + "\n",
            )

        approval = queue_root / "pending" / "approvals" / f"{job_stem}.approval.json"
        if approval.exists():
            _zip_write_bytes(zf, f"job/{approval.name}", approval.read_bytes())
        else:
            _zip_write_text(zf, "notes/approval_not_found.txt", "approval artifact not found\n")

        sidecar = queue_root / "failed" / f"{job_stem}.error.json"
        if sidecar.exists():
            _zip_write_bytes(zf, f"job/{sidecar.name}", sidecar.read_bytes())
        else:
            _zip_write_text(zf, "notes/failed_sidecar_not_found.txt", "failed sidecar not found\n")

        art_dir = queue_root / "artifacts" / job_stem
        names = ["plan.json", "actions.jsonl", "stdout.txt", "stderr.txt", "generated_files.json"]
        if art_dir.exists():
            for name in names:
                p = art_dir / name
                if not p.exists():
                    _zip_write_text(zf, f"notes/missing-{name}.txt", f"missing artifact: {name}\n")
                    continue
                if name in {"stdout.txt", "stderr.txt"}:
                    data, truncated, original = _read_truncated(p)
                    _zip_write_bytes(zf, f"artifacts/{name}", data)
                    if truncated:
                        _zip_write_text(
                            zf,
                            f"notes/{name}.truncated.txt",
                            f"truncated {name} from {original} to {len(data)} bytes\n",
                        )
                else:
                    _zip_write_bytes(zf, f"artifacts/{name}", p.read_bytes())
        else:
            _zip_write_text(
                zf, "notes/artifacts_not_found.txt", f"artifacts directory missing: {art_dir}\n"
            )

    _write_zip(out, write)

    return out
