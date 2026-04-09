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


def _canonical_request_anchor_text(payload: dict[str, Any]) -> str | None:
    """Return short identifying text for id generation, or None if the
    payload carries no canonical request anchor at all.

    The set of canonical anchor fields mirrors exactly what the queue
    execution layer accepts at intake (``core/queue_execution.py``):
    ``mission_id`` (or legacy ``mission``), ``goal`` (or ``plan_goal``),
    inline ``steps``, ``write_file``, or ``file_organize``. A payload
    with none of these is rejected fail-closed — it has no canonical
    request kind and the queue daemon would reject it downstream
    anyway.

    This replaces the earlier goal-only gate, which incorrectly forced
    every non-goal canonical submission (for example a mission_id-only
    or write_file-only automation payload) to fail at the inbox helper
    layer, even though the queue itself accepts them just fine.
    """
    for key in ("mission_id", "mission"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return f"mission:{value.strip()}"
    for key in ("goal", "plan_goal"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    steps = payload.get("steps")
    if isinstance(steps, list) and steps:
        return f"inline_steps:{len(steps)}"
    write_file = payload.get("write_file")
    if isinstance(write_file, dict):
        path = write_file.get("path")
        if isinstance(path, str) and path.strip():
            return f"write_file:{path.strip()}"
    file_organize = payload.get("file_organize")
    if isinstance(file_organize, dict):
        src = file_organize.get("source_path")
        if isinstance(src, str) and src.strip():
            return f"file_organize:{src.strip()}"
    return None


def add_inbox_payload(
    queue_root: Path,
    payload: dict[str, Any],
    *,
    job_id: str | None = None,
    source_lane: str = "inbox_cli",
) -> Path:
    """Submit a canonical queue payload through the inbox.

    ``payload`` must carry at least one canonical request anchor field
    (``mission_id``, ``goal``, ``steps``, ``write_file``, or
    ``file_organize``) so the queue execution layer can classify the
    request. A payload with none of those anchors is rejected with
    ``ValueError`` — this matches the canonical request-kind set the
    daemon enforces at intake (``core/queue_execution.py``), and is
    what lets the automation runner, the panel, Vera, and the CLI all
    share one canonical submission helper instead of each inventing
    their own.
    """
    queue_root = queue_root.expanduser()
    inbox_dir = queue_root / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)

    anchor_text = _canonical_request_anchor_text(payload)
    if anchor_text is None:
        raise ValueError(
            "job payload requires at least one canonical request anchor "
            "(mission_id, goal, steps, write_file, or file_organize)"
        )

    resolved_id = (job_id or generate_inbox_id(anchor_text)).strip()
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
