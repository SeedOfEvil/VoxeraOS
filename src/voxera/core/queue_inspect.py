from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .queue_daemon import MissionQueueDaemon

JOB_BUCKETS = ("inbox", "pending", "approvals", "done", "failed")


@dataclass(frozen=True)
class JobLookup:
    job_id: str
    bucket: str
    primary_path: Path
    approval_path: Path | None
    failed_sidecar_path: Path | None
    artifacts_dir: Path


def _normalize_job_id(job_id: str) -> str:
    base = Path(job_id).name
    return base if base.endswith(".json") else f"{Path(base).stem}.json"


def lookup_job(queue_root: Path, job_id: str) -> JobLookup | None:
    normalized = _normalize_job_id(job_id)
    stem = Path(normalized).stem
    bucket_dirs = {
        "inbox": queue_root / "inbox",
        "pending": queue_root / "pending",
        "approvals": queue_root / "pending",
        "done": queue_root / "done",
        "failed": queue_root / "failed",
    }

    order = ["inbox", "pending", "done", "failed"]
    for bucket in order:
        primary = bucket_dirs[bucket] / normalized
        if not primary.exists():
            continue
        approval = queue_root / "pending" / "approvals" / f"{stem}.approval.json"
        sidecar = queue_root / "failed" / f"{stem}.error.json"
        return JobLookup(
            job_id=normalized,
            bucket=bucket,
            primary_path=primary,
            approval_path=approval if approval.exists() else None,
            failed_sidecar_path=sidecar if sidecar.exists() else None,
            artifacts_dir=queue_root / "artifacts" / stem,
        )

    approval = queue_root / "pending" / "approvals" / f"{stem}.approval.json"
    if approval.exists():
        pending_primary = queue_root / "pending" / normalized
        inbox_primary = queue_root / "inbox" / normalized
        primary = pending_primary if pending_primary.exists() else inbox_primary
        if primary.exists():
            return JobLookup(
                job_id=normalized,
                bucket="approvals",
                primary_path=primary,
                approval_path=approval,
                failed_sidecar_path=None,
                artifacts_dir=queue_root / "artifacts" / stem,
            )
    return None


def list_jobs(
    queue_root: Path,
    *,
    bucket: str = "pending",
    q: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    normalized_bucket = bucket if bucket in JOB_BUCKETS else "pending"
    capped = max(1, min(limit, 200))
    needle = q.lower().strip()

    daemon = MissionQueueDaemon(queue_root=queue_root)
    approvals_by_job = {item.get("job"): item for item in daemon.approvals_list()}

    dir_for_bucket = {
        "inbox": queue_root / "inbox",
        "pending": queue_root / "pending",
        "approvals": queue_root / "pending",
        "done": queue_root / "done",
        "failed": queue_root / "failed",
    }[normalized_bucket]

    rows: list[dict[str, Any]] = []
    for path in sorted(
        dir_for_bucket.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        name = path.name
        if normalized_bucket == "pending" and (
            name.endswith(".pending.json") or name.endswith(".approval.json")
        ):
            continue
        if (
            normalized_bucket == "approvals"
            and not (queue_root / "pending" / "approvals" / f"{path.stem}.approval.json").exists()
        ):
            continue
        if normalized_bucket == "failed" and name.endswith(".error.json"):
            continue

        title = ""
        goal = ""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                title = str(payload.get("title") or payload.get("mission_id") or "")
                goal = str(payload.get("goal") or payload.get("plan_goal") or "")
        except Exception:
            pass

        approval = (
            approvals_by_job.get(name, {}) if isinstance(approvals_by_job.get(name), dict) else {}
        )
        failed_sidecar = queue_root / "failed" / f"{path.stem}.error.json"
        status = []
        if approval:
            status.append("approval pending")
        if failed_sidecar.exists():
            status.append("failed metadata")
        if not status:
            status.append("ok")

        if needle and needle not in f"{name} {title} {goal}".lower():
            continue

        rows.append(
            {
                "job_id": name,
                "bucket": normalized_bucket,
                "title": title or "(untitled)",
                "goal": goal,
                "updated_ts": int(path.stat().st_mtime),
                "updated_iso": path.stat().st_mtime,
                "status_summary": ", ".join(status),
            }
        )
        if len(rows) >= capped:
            break
    return rows


def queue_snapshot(queue_root: Path) -> dict[str, Any]:
    daemon = MissionQueueDaemon(queue_root=queue_root)
    return daemon.status_snapshot(approvals_limit=12, failed_limit=8)
