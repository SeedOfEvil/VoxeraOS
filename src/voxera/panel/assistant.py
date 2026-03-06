from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..core.queue_inspect import lookup_job
from ..operator_assistant import (
    ASSISTANT_JOB_KIND,
    append_thread_turn,
    new_thread_id,
    normalize_thread_id,
    read_assistant_thread,
)


def enqueue_assistant_question(
    queue_root: Path, question: str, *, thread_id: str = ""
) -> tuple[str, str]:
    inbox = queue_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    ts_ms = int(time.time() * 1000)
    job_id = f"job-assistant-{ts_ms}.json"
    normalized_thread = normalize_thread_id(thread_id) if thread_id else new_thread_id()
    payload = {
        "kind": ASSISTANT_JOB_KIND,
        "question": question.strip(),
        "thread_id": normalized_thread,
        "created_at_ms": ts_ms,
        "advisory": True,
        "read_only": True,
    }
    (inbox / job_id).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    append_thread_turn(
        queue_root,
        thread_id=normalized_thread,
        role="user",
        text=question,
        request_id=job_id,
        ts_ms=ts_ms,
    )
    return job_id, normalized_thread


def read_assistant_result(queue_root: Path, request_id: str) -> dict[str, Any]:
    found = lookup_job(queue_root, request_id)
    normalized_id = f"{Path(request_id).stem}.json"
    response_path = queue_root / "artifacts" / Path(normalized_id).stem / "assistant_response.json"
    response_data: dict[str, Any] = {}
    if response_path.exists():
        try:
            loaded = json.loads(response_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                response_data = loaded
        except Exception:
            response_data = {}

    if found is None:
        return {
            "request_id": normalized_id,
            "thread_id": str(response_data.get("thread_id") or ""),
            "status": "unknown",
            "lifecycle_state": "unknown",
            "answer": str(response_data.get("answer") or ""),
            "error": str(response_data.get("error") or ""),
            "updated_at_ms": response_data.get("updated_at_ms"),
            "provider": response_data.get("provider"),
            "model": response_data.get("model"),
            "fallback_used": bool(response_data.get("fallback_used")),
            "fallback_reason": response_data.get("fallback_reason"),
            "advisory_mode": response_data.get("advisory_mode") or "unknown",
            "degraded_reason": response_data.get("degraded_reason"),
        }

    state_path = found.primary_path.with_name(f"{found.primary_path.stem}.state.json")
    state_payload: dict[str, Any] = {}
    if state_path.exists():
        try:
            loaded_state = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(loaded_state, dict):
                state_payload = loaded_state
        except Exception:
            state_payload = {}

    job_payload: dict[str, Any] = {}
    try:
        loaded_job = json.loads(found.primary_path.read_text(encoding="utf-8"))
        if isinstance(loaded_job, dict):
            job_payload = loaded_job
    except Exception:
        job_payload = {}

    bucket = found.bucket
    status = "queued"
    if bucket == "pending":
        status = "thinking through Voxera"
    if bucket == "done":
        status = "answered"
    if bucket == "failed":
        status = "failed"

    response_mode = str(response_data.get("advisory_mode") or "")
    if response_mode == "degraded_brain_only" and str(response_data.get("answer") or "").strip():
        status = "answered"

    return {
        "request_id": found.job_id,
        "thread_id": str(
            response_data.get("thread_id")
            or job_payload.get("thread_id")
            or state_payload.get("thread_id")
            or ""
        ),
        "status": status,
        "bucket": bucket,
        "lifecycle_state": str(state_payload.get("lifecycle_state") or "queued"),
        "answer": str(response_data.get("answer") or ""),
        "error": str(response_data.get("error") or state_payload.get("failure_summary") or ""),
        "updated_at_ms": response_data.get("updated_at_ms") or state_payload.get("updated_at_ms"),
        "provider": response_data.get("provider"),
        "model": response_data.get("model"),
        "fallback_used": bool(response_data.get("fallback_used")),
        "fallback_reason": response_data.get("fallback_reason"),
        "advisory_mode": response_data.get("advisory_mode")
        or ("queue" if bucket in {"pending", "done", "failed", "inbox"} else "unknown"),
        "degraded_reason": response_data.get("degraded_reason"),
    }


def read_assistant_thread_turns(queue_root: Path, thread_id: str) -> list[dict[str, Any]]:
    if not thread_id:
        return []
    payload = read_assistant_thread(queue_root, thread_id)
    turns_raw = payload.get("turns")
    return turns_raw if isinstance(turns_raw, list) else []
