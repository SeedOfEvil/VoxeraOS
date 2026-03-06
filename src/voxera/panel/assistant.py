from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..core.queue_inspect import lookup_job
from ..operator_assistant import ASSISTANT_JOB_KIND


def enqueue_assistant_question(queue_root: Path, question: str) -> str:
    inbox = queue_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    ts_ms = int(time.time() * 1000)
    job_id = f"job-assistant-{ts_ms}.json"
    payload = {
        "kind": ASSISTANT_JOB_KIND,
        "question": question.strip(),
        "created_at_ms": ts_ms,
        "advisory": True,
        "read_only": True,
    }
    (inbox / job_id).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return job_id


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
            "status": "unknown",
            "lifecycle_state": "unknown",
            "answer": str(response_data.get("answer") or ""),
            "error": str(response_data.get("error") or ""),
            "updated_at_ms": response_data.get("updated_at_ms"),
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

    bucket = found.bucket
    status = "queued"
    if bucket == "pending":
        status = "thinking through Voxera"
    if bucket == "done":
        status = "answered"
    if bucket == "failed":
        status = "failed"

    return {
        "request_id": found.job_id,
        "status": status,
        "bucket": bucket,
        "lifecycle_state": str(state_payload.get("lifecycle_state") or "queued"),
        "answer": str(response_data.get("answer") or ""),
        "error": str(response_data.get("error") or state_payload.get("failure_summary") or ""),
        "updated_at_ms": response_data.get("updated_at_ms") or state_payload.get("updated_at_ms"),
    }
