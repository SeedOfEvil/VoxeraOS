from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .audit import tail
from .health import read_health_snapshot
from .health_semantics import build_health_semantic_sections

ASSISTANT_JOB_KIND = "assistant_question"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _count_jobs(directory: Path) -> int:
    if not directory.exists():
        return 0
    return len(
        [
            p
            for p in directory.glob("*.json")
            if not p.name.endswith(
                (".state.json", ".error.json", ".approval.json", ".pending.json")
            )
        ]
    )


def _read_recent_jobs(directory: Path, *, limit: int = 5) -> list[dict[str, Any]]:
    if not directory.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.name.endswith((".state.json", ".error.json", ".approval.json", ".pending.json")):
            continue
        rows.append({"job_id": path.name, "bucket": directory.name})
        if len(rows) >= limit:
            break
    return rows


def _read_pending_approvals(queue_root: Path, *, limit: int = 5) -> list[dict[str, Any]]:
    approvals_dir = queue_root / "pending" / "approvals"
    if not approvals_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(
        approvals_dir.glob("*.approval.json"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(loaded, dict):
            rows.append(loaded)
        if len(rows) >= limit:
            break
    return rows


def build_operator_assistant_context(queue_root: Path) -> dict[str, Any]:
    health = read_health_snapshot(queue_root)
    counts = {
        "inbox": _count_jobs(queue_root / "inbox"),
        "pending": _count_jobs(queue_root / "pending"),
        "pending_approvals": len(
            list((queue_root / "pending" / "approvals").glob("*.approval.json"))
        )
        if (queue_root / "pending" / "approvals").exists()
        else 0,
        "done": _count_jobs(queue_root / "done"),
        "failed": _count_jobs(queue_root / "failed"),
        "canceled": _count_jobs(queue_root / "canceled"),
    }

    grouped = build_health_semantic_sections(
        health,
        queue_context={
            "queue_root": str(queue_root),
            "health_path": str(queue_root / "health.json"),
            "intake_glob": str(queue_root / "inbox" / "*.json"),
            "paused": (queue_root / ".paused").exists(),
        },
        lock_status=health.get("lock_status")
        if isinstance(health.get("lock_status"), dict)
        else {},
        daemon_lock_counters=health.get("daemon_lock_counters")
        if isinstance(health.get("daemon_lock_counters"), dict)
        else {},
    )

    recent_events: list[dict[str, str]] = []
    for event in reversed(tail(80)):
        if not isinstance(event, Mapping):
            continue
        event_name = str(event.get("event") or "").strip()
        if not event_name:
            continue
        if not (
            event_name.startswith("queue_")
            or event_name.startswith("mission_")
            or event_name.startswith("panel_")
            or event_name.startswith("assistant_")
        ):
            continue
        recent_events.append(
            {
                "event": event_name,
                "job": str(event.get("job") or "").strip(),
                "detail": str(event.get("reason") or event.get("error") or "").strip(),
            }
        )
        if len(recent_events) >= 8:
            break

    return {
        "queue_counts": counts,
        "queue_paused": (queue_root / ".paused").exists(),
        "pending_approvals": _read_pending_approvals(queue_root),
        "recent_failed_jobs": _read_recent_jobs(queue_root / "failed", limit=5),
        "recent_pending_jobs": _read_recent_jobs(queue_root / "pending", limit=5),
        "recent_events": recent_events,
        "health_current_state": _as_dict(grouped.get("current_state")),
        "health_recent_history": _as_dict(grouped.get("recent_history")),
        "health_historical_counters": _as_dict(grouped.get("historical_counters")),
    }


def build_assistant_messages(question: str, context: dict[str, Any]) -> list[dict[str, str]]:
    system = (
        "You are Voxera speaking from inside its queue-driven control plane. "
        "Use first-person control-plane framing (for example: 'From inside Voxera, I see...'). "
        "Ground every claim in the provided runtime context only. "
        "Do not claim actions were taken unless present in context. "
        "You are advisory/read-only: do not suggest that you executed, approved, denied, or mutated state. "
        "If context is incomplete, say so plainly. Keep responses concise and operator-friendly."
    )
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"Operator question:\n{question.strip()}\n\n"
                "Current Voxera runtime context (JSON):\n"
                f"{json.dumps(context, sort_keys=True)}"
            ),
        },
    ]


def _approval_reason_summary(approvals: list[dict[str, Any]]) -> str:
    if not approvals:
        return "From inside Voxera, I do not currently see pending approvals."
    first = approvals[0]
    reason = str(first.get("policy_reason") or first.get("reason") or "policy check").strip()
    capability = str(first.get("capability") or "unknown capability").strip()
    return f"From inside Voxera, my top pending approval is {capability} with reason '{reason}'."


def fallback_operator_answer(question: str, context: dict[str, Any]) -> str:
    question_text = " ".join(question.strip().split())
    lowered = question_text.lower()
    counts = _as_dict(context.get("queue_counts"))
    current = _as_dict(context.get("health_current_state"))
    approvals = _dict_list(context.get("pending_approvals"))
    failed_jobs = _dict_list(context.get("recent_failed_jobs"))
    recent_events = _dict_list(context.get("recent_events"))

    lines = [
        "From inside Voxera, I can only use current queue/health/job/audit context and operator-visible semantics.",
        (
            "Right now I see queue counts: "
            f"inbox={int(counts.get('inbox', 0) or 0)}, "
            f"pending={int(counts.get('pending', 0) or 0)}, "
            f"approvals={int(counts.get('pending_approvals', 0) or 0)}, "
            f"failed={int(counts.get('failed', 0) or 0)}, "
            f"done={int(counts.get('done', 0) or 0)}."
        ),
        f"My current daemon health state is '{current.get('daemon_state', 'unknown')}', and queue_paused={bool(context.get('queue_paused', False))}.",
    ]

    if any(
        token in lowered
        for token in [
            "happening",
            "right now",
            "status",
            "now",
            "feel like",
            "inside",
            "experiencing",
            "perspective",
        ]
    ):
        if recent_events:
            lines.append(
                "From my control-plane perspective, I am tracking recent queue/mission/panel events in the audit stream for current activity."
            )
        else:
            lines.append(
                "I do not currently see recent queue/mission/panel events in the audit window, so my activity view is limited."
            )

    if "waiting" in lowered or "stuck" in lowered:
        if int(counts.get("pending_approvals", 0) or 0) > 0:
            lines.append(
                "Right now I am waiting at policy approval gates in pending/approvals; execution resumes only after an operator decision."
            )
            lines.append(_approval_reason_summary(approvals))
        elif int(counts.get("pending", 0) or 0) > 0:
            lines.append(
                "I currently have pending jobs without approval gates, so delay is more likely daemon processing cadence or backoff behavior."
            )
        else:
            lines.append("I do not currently see a queue wait condition.")

    if "approval" in lowered:
        lines.append(
            "I can explain why approvals appear, but I cannot approve/deny or execute actions from this assistant surface."
        )
        lines.append(_approval_reason_summary(approvals))

    if "next" in lowered or "what can i do" in lowered or "suggest" in lowered:
        if int(counts.get("pending_approvals", 0) or 0) > 0:
            lines.append(
                "An insider suggestion from my side would be: review pending approvals in Control and intentionally approve/deny each gate."
            )
        elif failed_jobs:
            lines.append(
                "An insider suggestion from my side would be: inspect recent failed jobs and their sidecars before retrying."
            )

    if "route" in lowered or "handle" in lowered or "move through" in lowered:
        lines.append(
            "I would route requests through queue intake, mission/planner interpretation, policy + approval checks, then execution through queue lifecycle stages."
        )

    lines.append("I did not execute jobs or mutate queue state while answering.")
    return "\n".join(lines)
