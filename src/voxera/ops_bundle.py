from __future__ import annotations

import json
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any

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


def build_system_bundle(queue_root: Path) -> Path:
    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.ensure_dirs()
    out_dir = _archive_dir(queue_root)
    out = out_dir / "bundle-system.zip"

    status = daemon.status_snapshot(approvals_limit=8, failed_limit=8)
    status_text = json.dumps(status, indent=2, sort_keys=True)

    with zipfile.ZipFile(out, mode="w") as zf:
        _zip_write_bytes(
            zf,
            "manifest.json",
            json.dumps(_manifest(queue_root), indent=2, sort_keys=True).encode("utf-8"),
        )
        _zip_write_text(zf, "snapshots/queue_status.txt", status_text + "\n")

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

    return out


def build_job_bundle(queue_root: Path, job_ref: str) -> Path:
    daemon = MissionQueueDaemon(queue_root=queue_root)
    daemon.ensure_dirs()
    out_dir = _archive_dir(queue_root)

    lookup = lookup_job(queue_root, job_ref)
    job_stem = Path(job_ref).stem
    out = out_dir / f"bundle-job-{job_stem}.zip"

    searched = [
        str(queue_root / "done" / f"{job_stem}.json"),
        str(queue_root / "failed" / f"{job_stem}.json"),
        str(queue_root / "pending" / f"{job_stem}.json"),
        str(queue_root / "inbox" / f"{job_stem}.json"),
    ]

    with zipfile.ZipFile(out, mode="w") as zf:
        _zip_write_bytes(
            zf,
            "manifest.json",
            json.dumps(_manifest(queue_root), indent=2, sort_keys=True).encode("utf-8"),
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

    return out
