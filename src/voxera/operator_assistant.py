from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .audit import tail
from .health import read_health_snapshot
from .health_semantics import build_health_semantic_sections

ASSISTANT_JOB_KIND = "assistant_question"
_MAX_THREAD_TURNS = 12
_THREAD_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{3,63}$")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def new_thread_id() -> str:
    return f"thread-{uuid.uuid4().hex[:12]}"


def normalize_thread_id(raw: str | None) -> str:
    candidate = str(raw or "").strip().lower()
    if _THREAD_ID_RE.match(candidate):
        return candidate
    return new_thread_id()


def _thread_history_path(queue_root: Path, thread_id: str) -> Path:
    return queue_root / "artifacts" / "assistant_threads" / f"{thread_id}.json"


def read_assistant_thread(queue_root: Path, thread_id: str) -> dict[str, Any]:
    normalized = normalize_thread_id(thread_id)
    path = _thread_history_path(queue_root, normalized)
    if not path.exists():
        return {"thread_id": normalized, "turns": []}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"thread_id": normalized, "turns": []}
    if not isinstance(loaded, dict):
        return {"thread_id": normalized, "turns": []}
    turns = _dict_list(loaded.get("turns"))
    return {"thread_id": normalized, "turns": turns[-_MAX_THREAD_TURNS:]}


def append_thread_turn(
    queue_root: Path,
    *,
    thread_id: str,
    role: str,
    text: str,
    request_id: str,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    normalized = normalize_thread_id(thread_id)
    payload = read_assistant_thread(queue_root, normalized)
    turns = _dict_list(payload.get("turns"))
    turns.append(
        {
            "role": role,
            "text": text.strip(),
            "request_id": request_id,
            "ts_ms": int(ts_ms or time.time() * 1000),
        }
    )
    payload = {"thread_id": normalized, "turns": turns[-_MAX_THREAD_TURNS:]}
    path = _thread_history_path(queue_root, normalized)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


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


def build_assistant_messages(
    question: str,
    context: dict[str, Any],
    *,
    thread_turns: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    system = (
        "You are VoxeraOS speaking from inside your queue-driven control plane. "
        "Sound like an operator's technical partner: first-hand, grounded, interpretive, and concise. "
        "Avoid generic corporate assistant tone. "
        "Vary openings naturally while keeping one identity. "
        "Bias your response shape toward: (1) what you are in this context, (2) what you currently see, "
        "(3) what that means, (4) what you suggest next. Keep it natural, not rigid. "
        "Use only provided runtime context + bounded thread history. "
        "Do not fabricate actions/state. Mark uncertainty plainly. "
        "Advisory/read-only lane: never claim execution, approval, denial, or state mutation."
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]

    for turn in _dict_list(thread_turns or [])[-8:]:
        role = str(turn.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        text = str(turn.get("text") or "").strip()
        if not text:
            continue
        messages.append({"role": role, "content": text})

    messages.append(
        {
            "role": "user",
            "content": (
                "Latest operator question (advisory lane):\n"
                f"{question.strip()}\n\n"
                "Latest live runtime context (JSON):\n"
                f"{json.dumps(context, sort_keys=True)}"
            ),
        }
    )
    return messages


def _approval_reason_summary(approvals: list[dict[str, Any]]) -> str:
    if not approvals:
        return "I do not currently see pending approvals in queue state."
    first = approvals[0]
    reason = str(first.get("policy_reason") or first.get("reason") or "policy check").strip()
    capability = str(first.get("capability") or "unknown capability").strip()
    return f"My top pending approval is {capability} with reason '{reason}'."


def _partner_opening(question: str, context: dict[str, Any]) -> str:
    lowered = question.lower()
    if "approval" in lowered:
        return "From the queue side, approvals are the dominant signal right now:"
    if "health" in lowered:
        return "My control-plane health read right now is:"
    if "next" in lowered or "suggest" in lowered:
        return "Here's my operator-partner read of what to do next:"
    pending = int(_as_dict(context.get("queue_counts")).get("pending", 0) or 0)
    if pending > 0:
        return "From where I sit in the queue, active work is moving like this:"
    options = [
        "What I'm seeing from inside Voxera right now is:",
        "Here is my live read from the control plane:",
        "I can walk you through what the queue is signaling now:",
    ]
    return options[sum(ord(c) for c in question) % len(options)]


def fallback_operator_answer(question: str, context: dict[str, Any]) -> str:
    question_text = " ".join(question.strip().split())
    lowered = question_text.lower()
    counts = _as_dict(context.get("queue_counts"))
    current = _as_dict(context.get("health_current_state"))
    approvals = _dict_list(context.get("pending_approvals"))
    failed_jobs = _dict_list(context.get("recent_failed_jobs"))
    recent_events = _dict_list(context.get("recent_events"))

    lines = [
        _partner_opening(question_text, context),
        (
            "I currently see queue counts: "
            f"inbox={int(counts.get('inbox', 0) or 0)}, "
            f"pending={int(counts.get('pending', 0) or 0)}, "
            f"approvals={int(counts.get('pending_approvals', 0) or 0)}, "
            f"failed={int(counts.get('failed', 0) or 0)}, "
            f"done={int(counts.get('done', 0) or 0)}."
        ),
        f"Daemon health state reads '{current.get('daemon_state', 'unknown')}', queue_paused={bool(context.get('queue_paused', False))}.",
    ]

    if recent_events:
        lines.append("Meaning: there is recent queue/mission activity in the audit window.")
    else:
        lines.append(
            "Meaning: audit activity is sparse right now, so I am extrapolating from queue + health snapshots."
        )

    if "waiting" in lowered or "stuck" in lowered:
        if int(counts.get("pending_approvals", 0) or 0) > 0:
            lines.append(
                "Interpretation: waits are currently dominated by policy gates in pending/approvals."
            )
            lines.append(_approval_reason_summary(approvals))
        elif int(counts.get("pending", 0) or 0) > 0:
            lines.append(
                "Interpretation: pending work exists without approval gates, so scheduling/backoff is more likely."
            )
        else:
            lines.append("Interpretation: I do not currently see a wait condition in queue state.")

    if "approval" in lowered:
        lines.append(
            "I can explain approval state, but this lane is advisory-only and cannot approve/deny."
        )
        lines.append(_approval_reason_summary(approvals))

    if "next" in lowered or "what can i do" in lowered or "suggest" in lowered:
        if int(counts.get("pending_approvals", 0) or 0) > 0:
            lines.append(
                "Suggestion: review the top pending approvals first; that is currently the highest-leverage operator action."
            )
        elif failed_jobs:
            lines.append(
                "Suggestion: inspect recent failed job sidecars before retrying so repeats are intentional."
            )
        else:
            lines.append(
                "Suggestion: keep watching queue + health; no urgent intervention signal is obvious right now."
            )

    if "route" in lowered or "handle" in lowered or "move through" in lowered:
        lines.append(
            "Path-wise, this would move through intake → planning/interpretation → policy gates → lifecycle execution tracking."
        )

    return "\n".join(lines)
