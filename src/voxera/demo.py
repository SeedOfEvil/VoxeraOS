from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import load_config

Status = Literal["PASS", "FAIL", "SKIPPED"]


@dataclass(frozen=True)
class ChecklistItem:
    name: str
    status: Status
    detail: str


def _ensure_queue_dirs(queue_dir: Path) -> None:
    for rel in (
        "inbox",
        "pending",
        "pending/approvals",
        "done",
        "failed",
        "canceled",
        "artifacts",
    ):
        (queue_dir / rel).mkdir(parents=True, exist_ok=True)


def _write_demo_job(queue_dir: Path, *, job_id: str, goal: str, approval_required: bool) -> Path:
    target = queue_dir / "inbox" / f"demo-{job_id}.json"
    payload = {
        "id": job_id,
        "goal": goal,
        "approval_required": approval_required,
        "tags": ["demo"],
    }
    if target.exists():
        raise FileExistsError(f"demo job already exists: {target.name}")
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


def _provider_envs() -> tuple[str, ...]:
    return (
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
    )


def run_demo(*, queue_dir: Path | None = None, online: bool = False, yes: bool = False) -> dict:
    cfg = load_config()
    resolved_queue = (queue_dir or cfg.queue_root).expanduser().resolve()
    _ensure_queue_dirs(resolved_queue)

    checks: list[ChecklistItem] = [
        ChecklistItem(name="queue directories", status="PASS", detail=f"ready at {resolved_queue}"),
    ]
    created_jobs: list[str] = []
    demo_token = str(int(time.time() * 1000))

    try:
        basic = _write_demo_job(
            resolved_queue,
            job_id=f"basic-{demo_token}",
            goal="Demo: run queue status and validate deterministic lifecycle",
            approval_required=False,
        )
        approval = _write_demo_job(
            resolved_queue,
            job_id=f"approval-{demo_token}",
            goal="Demo: approval gate job (expect pending approval before execution)",
            approval_required=True,
        )
        created_jobs.extend([basic.name, approval.name])
        checks.append(
            ChecklistItem(
                name="demo jobs",
                status="PASS",
                detail=f"created {basic.name}, {approval.name}",
            )
        )
    except Exception as exc:
        checks.append(ChecklistItem(name="demo jobs", status="FAIL", detail=str(exc)))

    if online:
        available = [ref for ref in _provider_envs() if os.environ.get(ref)]
        if available:
            checks.append(
                ChecklistItem(
                    name="provider readiness (online)",
                    status="PASS",
                    detail=f"credentials found for: {', '.join(sorted(available))}",
                )
            )
        else:
            checks.append(
                ChecklistItem(
                    name="provider readiness (online)",
                    status="SKIPPED",
                    detail="no provider credentials found; online checks skipped",
                )
            )
    else:
        checks.append(
            ChecklistItem(
                name="provider readiness (online)",
                status="SKIPPED",
                detail="offline mode (use --online to opt in)",
            )
        )

    cleanup_removed = 0
    if yes:
        demo_roots = [
            resolved_queue / bucket for bucket in ("inbox", "pending", "done", "failed", "canceled")
        ]
        for root in demo_roots:
            if not root.exists():
                continue
            for file in root.glob("demo-*.json"):
                if file.is_file():
                    file.unlink(missing_ok=True)
                    cleanup_removed += 1
        artifacts = resolved_queue / "artifacts"
        if artifacts.exists():
            for folder in artifacts.glob("demo-*"):
                if folder.is_dir():
                    for child in folder.glob("**/*"):
                        if child.is_file():
                            child.unlink(missing_ok=True)
                    for child_dir in sorted(
                        [p for p in folder.glob("**/*") if p.is_dir()], reverse=True
                    ):
                        child_dir.rmdir()
                    folder.rmdir()
                    cleanup_removed += 1

    statuses = [item.status for item in checks]
    overall = "fail" if "FAIL" in statuses else ("partial" if "SKIPPED" in statuses else "ok")
    return {
        "status": overall,
        "queue_dir": str(resolved_queue),
        "online": online,
        "checks": [item.__dict__ for item in checks],
        "created_jobs": created_jobs,
        "cleanup": {"performed": yes, "removed": cleanup_removed},
    }
