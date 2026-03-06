from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..audit import tail
from ..core.queue_inspect import list_jobs, queue_snapshot
from ..health import read_health_snapshot
from ..health_semantics import build_health_semantic_sections


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def build_operator_assistant_context(queue_root: Path) -> dict[str, Any]:
    queue = queue_snapshot(queue_root)
    health = read_health_snapshot(queue_root)
    grouped = build_health_semantic_sections(
        health,
        queue_context={
            "queue_root": queue.get("queue_root"),
            "health_path": queue.get("health_path"),
            "intake_glob": queue.get("intake_glob"),
            "paused": bool(queue.get("paused", False)),
        },
        lock_status=queue.get("lock_status") if isinstance(queue.get("lock_status"), dict) else {},
        daemon_lock_counters=queue.get("daemon_lock_counters")
        if isinstance(queue.get("daemon_lock_counters"), dict)
        else {},
    )

    counts = _as_dict(queue.get("counts"))
    approvals = _dict_list(queue.get("pending_approvals"))
    failed_jobs = list_jobs(queue_root, bucket="failed", limit=5)
    pending_jobs = list_jobs(queue_root, bucket="pending", limit=5)

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
        "queue_counts": {
            "inbox": int(counts.get("inbox", 0) or 0),
            "pending": int(counts.get("pending", 0) or 0),
            "pending_approvals": int(counts.get("pending_approvals", 0) or 0),
            "done": int(counts.get("done", 0) or 0),
            "failed": int(counts.get("failed", 0) or 0),
            "canceled": int(counts.get("canceled", 0) or 0),
        },
        "queue_paused": bool(queue.get("paused", False)),
        "pending_approvals": approvals[:5],
        "recent_failed_jobs": _dict_list(failed_jobs),
        "recent_pending_jobs": _dict_list(pending_jobs),
        "recent_events": recent_events,
        "health_current_state": _as_dict(grouped.get("current_state")),
        "health_recent_history": _as_dict(grouped.get("recent_history")),
        "health_historical_counters": _as_dict(grouped.get("historical_counters")),
    }


def _approval_reason_summary(approvals: list[dict[str, Any]]) -> str:
    if not approvals:
        return "From inside Voxera, I do not currently see pending approvals."
    first = approvals[0]
    reason = str(first.get("policy_reason") or first.get("reason") or "policy check").strip()
    capability = str(first.get("capability") or "unknown capability").strip()
    return f"From inside Voxera, my top pending approval is {capability} with reason '{reason}'."


def answer_operator_question(question: str, context: dict[str, Any]) -> str:
    question_text = " ".join(question.strip().split())
    lowered = question_text.lower()
    counts = _as_dict(context.get("queue_counts"))
    current = _as_dict(context.get("health_current_state"))
    recent = _as_dict(context.get("health_recent_history"))
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

    if "health" in lowered or "state" in lowered:
        lines.append(
            "My current health interpretation comes from Current State and Recent History fields (including fallback/shutdown markers when present)."
        )
        if recent.get("last_error"):
            lines.append(
                f"My last recorded error in snapshot history is: {recent.get('last_error')}."
            )
        else:
            lines.append("I do not currently have a recorded last_error value in recent history.")

    if "fallback" in lowered:
        fallback = _as_dict(recent.get("last_brain_fallback"))
        if fallback:
            lines.append(
                "My last fallback path was: "
                f"reason={fallback.get('reason', '-')}, "
                f"from={fallback.get('from', '-')}, "
                f"to={fallback.get('to', '-')}"
            )
        else:
            lines.append("I do not currently have fallback details in the health snapshot.")

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
        else:
            lines.append(
                "An insider suggestion from my side would be: continue monitoring queue and health surfaces; I do not currently see urgent intervention signals."
            )

    if "route" in lowered or "handle" in lowered or "move through" in lowered:
        lines.append(
            "I would route requests through queue intake, mission/planner interpretation, policy + approval checks, then execution through queue lifecycle stages."
        )

    if len(lines) <= 3:
        lines.append(
            "I can explain queue state, health semantics, approvals, and likely next operator actions, but only from data present in current runtime context."
        )

    lines.append("I did not execute jobs or mutate queue state while answering.")
    return "\n".join(lines)
