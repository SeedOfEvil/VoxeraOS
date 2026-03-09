from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .queue_job_intent import enrich_queue_job_payload


@dataclass(frozen=True)
class InboxJob:
    filename: str
    path: Path
    state: str
    job_id: str
    goal: str
    created_at: float


def generate_inbox_id(goal: str, *, now_ms: int | None = None) -> str:
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    digest = hashlib.sha1(f"{goal}|{now_ms}".encode()).hexdigest()[:8]
    return f"{now_ms}-{digest}"


def add_inbox_job(queue_root: Path, goal: str, *, job_id: str | None = None) -> Path:
    return add_inbox_payload(queue_root, {"goal": goal}, job_id=job_id)


def add_inbox_payload(
    queue_root: Path,
    payload: dict[str, Any],
    *,
    job_id: str | None = None,
    source_lane: str = "inbox_cli",
) -> Path:
    queue_root = queue_root.expanduser()
    inbox_dir = queue_root / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)

    payload_goal = str(payload.get("goal") or "").strip()
    if not payload_goal:
        raise ValueError("job payload requires a non-empty goal")

    resolved_id = (job_id or generate_inbox_id(payload_goal)).strip()
    if not resolved_id:
        raise ValueError("job id cannot be empty")

    payload = enrich_queue_job_payload(
        {"id": resolved_id, **payload},
        source_lane=source_lane,
    )
    target = inbox_dir / f"inbox-{resolved_id}.json"
    if target.exists():
        raise FileExistsError(f"inbox job already exists: {target}")
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


def list_inbox_jobs(queue_root: Path, *, limit: int = 20) -> tuple[list[InboxJob], list[Path]]:
    queue_root = queue_root.expanduser()
    buckets = {
        "inbox": queue_root / "inbox",
        "pending": queue_root / "pending",
        "done": queue_root / "done",
        "failed": queue_root / "failed",
    }

    missing = [path for path in buckets.values() if not path.exists()]
    found: list[InboxJob] = []

    for state, directory in buckets.items():
        if not directory.exists():
            continue
        for job_path in directory.glob("inbox-*.json"):
            if not job_path.is_file():
                continue
            try:
                payload = json.loads(job_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            goal = str(payload.get("goal", ""))
            job_id = str(payload.get("id", job_path.stem.removeprefix("inbox-")))
            found.append(
                InboxJob(
                    filename=job_path.name,
                    path=job_path,
                    state=state,
                    job_id=job_id,
                    goal=goal,
                    created_at=job_path.stat().st_mtime,
                )
            )

    found.sort(key=lambda item: item.created_at, reverse=True)
    return found[:limit], missing
