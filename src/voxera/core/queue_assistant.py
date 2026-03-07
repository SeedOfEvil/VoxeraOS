from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from ..audit import log
from ..brain.fallback import AUTH, MALFORMED, NETWORK, RATE_LIMIT, TIMEOUT, classify_fallback_reason
from ..brain.gemini import GeminiBrain
from ..brain.openai_compat import OpenAICompatBrain
from ..operator_assistant import (
    ASSISTANT_JOB_KIND,
    append_thread_turn,
    build_assistant_messages,
    build_operator_assistant_context,
    normalize_thread_id,
    read_assistant_thread,
)

_ASSISTANT_FALLBACK_REASONS = frozenset({TIMEOUT, AUTH, RATE_LIMIT, MALFORMED, NETWORK})


def assistant_response_artifact_path(daemon: Any, job_ref: str) -> Path:
    return daemon._job_artifacts_dir(job_ref) / "assistant_response.json"


def create_assistant_brain(provider: Any) -> OpenAICompatBrain | GeminiBrain:
    if provider.type == "openai_compat":
        return OpenAICompatBrain(
            model=provider.model,
            base_url=provider.base_url or "https://openrouter.ai/api/v1",
            api_key_ref=provider.api_key_ref,
            extra_headers=provider.extra_headers,
        )
    if provider.type == "gemini":
        return GeminiBrain(model=provider.model, api_key_ref=provider.api_key_ref)
    raise ValueError(f"unsupported assistant provider type: {provider.type}")


def assistant_brain_candidates(cfg: Any) -> list[tuple[str, Any]]:
    if not cfg.brain:
        return []
    ordered: list[tuple[str, Any]] = []
    for key in ("primary", "fallback"):
        provider = cfg.brain.get(key)
        if provider is not None:
            ordered.append((key, provider))
    return ordered


def assistant_answer_via_brain(
    question: str,
    context: dict[str, Any],
    *,
    thread_turns: list[dict[str, Any]],
    attempts: list[tuple[str, Any]],
    create_brain: Any,
) -> dict[str, Any]:
    messages = build_assistant_messages(question, context, thread_turns=thread_turns)
    if not attempts:
        raise RuntimeError("assistant advisory brain providers are not configured")

    primary_name, primary_provider = attempts[0]
    fallback = attempts[1] if len(attempts) > 1 else None

    try:
        primary_brain = create_brain(primary_provider)
        primary_resp = asyncio.run(primary_brain.generate(messages, tools=[]))
        primary_text = str(primary_resp.text or "").strip()
        if not primary_text:
            raise ValueError("assistant advisory provider returned empty response")
        return {
            "answer": primary_text,
            "provider": primary_name,
            "model": str(primary_provider.model),
            "fallback_used": False,
            "fallback_from": None,
            "fallback_reason": None,
            "error_class": None,
            "advisory_mode": "queue",
            "degraded_reason": None,
        }
    except Exception as exc:
        fallback_reason = classify_fallback_reason(exc)
        error_class = type(exc).__name__
        log(
            {
                "event": "assistant_advisory_primary_failed",
                "provider": primary_name,
                "model": str(primary_provider.model),
                "fallback_reason": fallback_reason,
                "error_class": error_class,
                "error": repr(exc),
            }
        )
        if fallback is None:
            raise RuntimeError(
                f"assistant advisory primary failed without fallback provider ({fallback_reason})"
            ) from exc
        if fallback_reason not in _ASSISTANT_FALLBACK_REASONS:
            raise RuntimeError(
                f"assistant advisory primary failed with non-retryable reason ({fallback_reason})"
            ) from exc

        fallback_name, fallback_provider = fallback
        try:
            fallback_brain = create_brain(fallback_provider)
            fallback_resp = asyncio.run(fallback_brain.generate(messages, tools=[]))
            fallback_text = str(fallback_resp.text or "").strip()
            if not fallback_text:
                raise ValueError("assistant advisory fallback provider returned empty response")
            return {
                "answer": fallback_text,
                "provider": fallback_name,
                "model": str(fallback_provider.model),
                "fallback_used": True,
                "fallback_from": {
                    "provider": primary_name,
                    "model": str(primary_provider.model),
                },
                "fallback_reason": fallback_reason,
                "error_class": error_class,
                "advisory_mode": "queue",
                "degraded_reason": None,
            }
        except Exception as fallback_exc:
            fallback_reason_2 = classify_fallback_reason(fallback_exc)
            raise RuntimeError(
                "assistant advisory primary and fallback providers failed "
                f"({fallback_reason}/{fallback_reason_2})"
            ) from fallback_exc


def process_assistant_job(daemon: Any, job_path: Path, payload: dict[str, Any]) -> bool:
    question = str(payload.get("question") or "").strip()
    if not question:
        raise ValueError("assistant question is required")
    thread_id = normalize_thread_id(str(payload.get("thread_id") or ""))

    daemon._update_job_state(
        str(job_path),
        lifecycle_state="advisory_running",
        payload={**payload, "thread_id": thread_id},
    )
    daemon._write_action_event(str(job_path), "assistant_job_started", thread_id=thread_id)

    context = build_operator_assistant_context(daemon.queue_root)
    thread_payload = read_assistant_thread(daemon.queue_root, thread_id)
    turns_raw = thread_payload.get("turns")
    thread_turns = (
        [item for item in turns_raw if isinstance(item, dict)]
        if isinstance(turns_raw, list)
        else []
    )

    try:
        answer_data = daemon._assistant_answer_via_brain(
            question, context, thread_turns=thread_turns
        )
        answer = str(answer_data.get("answer") or "")
    except Exception as exc:
        failure_reason = classify_fallback_reason(exc)
        now_ms = int(time.time() * 1000)
        artifact_payload = {
            "schema_version": 2,
            "kind": ASSISTANT_JOB_KIND,
            "thread_id": thread_id,
            "question": question,
            "answer": "",
            "error": f"assistant advisory failed: {exc}",
            "error_class": type(exc).__name__,
            "fallback_used": False,
            "fallback_reason": failure_reason,
            "provider": None,
            "model": None,
            "advisory_mode": "queue",
            "degraded_reason": "queue_processing_failed",
            "answered_at_ms": now_ms,
            "updated_at_ms": now_ms,
            "context": context,
        }
        assistant_response_artifact_path(daemon, str(job_path)).write_text(
            json.dumps(artifact_payload, indent=2), encoding="utf-8"
        )
        moved = daemon._move_job(job_path, daemon.failed)
        if moved is None:
            return False
        daemon.stats.failed += 1
        failure_text = str(artifact_payload["error"])
        daemon._write_failed_error_sidecar(
            moved, error=failure_text, payload={**payload, "thread_id": thread_id}
        )
        daemon._write_execution_result_artifacts(
            str(moved),
            rr_data={
                "results": [
                    {
                        "step": 1,
                        "skill": "assistant.advisory",
                        "args": {"question": question, "thread_id": thread_id},
                        "ok": False,
                        "output": "",
                        "error": failure_text,
                        "summary": failure_text,
                        "machine_payload": artifact_payload,
                        "started_at_ms": now_ms,
                        "finished_at_ms": now_ms,
                        "duration_ms": 0,
                    }
                ],
                "step_outcomes": [
                    {
                        "step": 1,
                        "skill": "assistant.advisory",
                        "outcome": "failed",
                    }
                ],
                "total_steps": 1,
                "lifecycle_state": "step_failed",
                "terminal_outcome": "failed",
            },
            ok=False,
            terminal_outcome="failed",
            error=failure_text,
        )
        daemon._update_job_state(
            str(moved),
            lifecycle_state="step_failed",
            payload={**payload, "thread_id": thread_id},
            terminal_outcome="failed",
            failure_summary=failure_text,
        )
        daemon._write_action_event(
            str(moved),
            "assistant_advisory_failed",
            thread_id=thread_id,
            fallback_reason=failure_reason,
            error_class=type(exc).__name__,
        )
        log(
            {
                "event": "assistant_advisory_failed",
                "job": str(moved),
                "thread_id": thread_id,
                "fallback_reason": failure_reason,
                "error_class": type(exc).__name__,
                "error": repr(exc),
                "ts_ms": now_ms,
            }
        )
        return False

    answered_ms = int(time.time() * 1000)
    append_thread_turn(
        daemon.queue_root,
        thread_id=thread_id,
        role="assistant",
        text=answer,
        request_id=job_path.name,
        ts_ms=answered_ms,
    )

    artifact_payload = {
        "schema_version": 2,
        "kind": ASSISTANT_JOB_KIND,
        "thread_id": thread_id,
        "question": question,
        "answer": answer,
        "updated_at_ms": answered_ms,
        "answered_at_ms": answered_ms,
        "provider": answer_data.get("provider"),
        "model": answer_data.get("model"),
        "fallback_used": bool(answer_data.get("fallback_used")),
        "fallback_from": answer_data.get("fallback_from"),
        "fallback_reason": answer_data.get("fallback_reason"),
        "error_class": answer_data.get("error_class"),
        "advisory_mode": str(answer_data.get("advisory_mode") or "queue"),
        "degraded_reason": answer_data.get("degraded_reason"),
        "context": context,
    }
    assistant_response_artifact_path(daemon, str(job_path)).write_text(
        json.dumps(artifact_payload, indent=2), encoding="utf-8"
    )

    moved = daemon._move_job(job_path, daemon.done)
    if moved is None:
        return False

    daemon.stats.processed += 1
    daemon._write_execution_result_artifacts(
        str(moved),
        rr_data={
            "results": [
                {
                    "step": 1,
                    "skill": "assistant.advisory",
                    "args": {"question": question, "thread_id": thread_id},
                    "ok": True,
                    "output": answer,
                    "error": "",
                    "summary": answer,
                    "machine_payload": artifact_payload,
                    "started_at_ms": answered_ms,
                    "finished_at_ms": answered_ms,
                    "duration_ms": 0,
                }
            ],
            "step_outcomes": [
                {
                    "step": 1,
                    "skill": "assistant.advisory",
                    "outcome": "succeeded",
                }
            ],
            "total_steps": 1,
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "current_step_index": 1,
            "last_completed_step": 1,
            "last_attempted_step": 1,
        },
        ok=True,
        terminal_outcome="succeeded",
        error=None,
    )
    daemon._update_job_state(
        str(moved),
        lifecycle_state="done",
        payload={**payload, "thread_id": thread_id},
        terminal_outcome="succeeded",
    )
    if artifact_payload["fallback_used"]:
        daemon._write_action_event(
            str(moved),
            "assistant_advisory_fallback_used",
            thread_id=thread_id,
            provider=artifact_payload.get("provider"),
            model=artifact_payload.get("model"),
            fallback_reason=artifact_payload.get("fallback_reason"),
        )
        log(
            {
                "event": "assistant_advisory_fallback_used",
                "job": str(moved),
                "thread_id": thread_id,
                "provider": artifact_payload.get("provider"),
                "model": artifact_payload.get("model"),
                "fallback_reason": artifact_payload.get("fallback_reason"),
                "ts_ms": answered_ms,
            }
        )
    daemon._write_action_event(
        str(moved),
        "assistant_advisory_answered",
        thread_id=thread_id,
        provider=artifact_payload.get("provider"),
        model=artifact_payload.get("model"),
        fallback_used=artifact_payload.get("fallback_used"),
        advisory_mode=artifact_payload.get("advisory_mode"),
    )
    daemon._write_action_event(str(moved), "assistant_job_done", thread_id=thread_id)
    log(
        {
            "event": "assistant_advisory_answered",
            "job": str(moved),
            "thread_id": thread_id,
            "provider": artifact_payload.get("provider"),
            "model": artifact_payload.get("model"),
            "fallback_used": artifact_payload.get("fallback_used"),
            "advisory_mode": artifact_payload.get("advisory_mode"),
            "ts_ms": answered_ms,
        }
    )
    log({"event": "assistant_job_done", "job": str(moved), "thread_id": thread_id})
    return True
