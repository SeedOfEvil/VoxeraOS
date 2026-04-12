"""Degraded-assistant bridge / messaging helpers.

This module owns the narrow seam between the panel assistant route layer
and the degraded (model-only / deterministic-fallback) answer path.  It
handles stall detection, provider-tier traversal, degraded-mode disclosure,
the async/sync bridge, and result persistence for degraded advisory answers.

Public entry points:

- ``assistant_stalled_degraded_reason(context, request_result, *, now_ms)``
  — detects whether the assistant queue transport is stalled and returns a
  degraded-reason string or ``None``.
- ``create_panel_assistant_brain(provider)``
  — factory that creates an ``OpenAICompatBrain`` or ``GeminiBrain`` from
  a provider config object.
- ``generate_degraded_assistant_answer_async(question, context, *, ...)``
  — async path: tries the fast/primary/fallback provider tiers, falls
  through to the deterministic fallback.
- ``generate_degraded_assistant_answer(question, context, *, ...)``
  — sync bridge: wraps the async path with ``asyncio.run``.
- ``persist_degraded_assistant_result(queue_root, *, ...)``
  — writes the degraded advisory result to the artifact directory.

Architecture invariant: this module is explicit-args — it does NOT reach
back into ``panel.app`` via any import.  ``app.py`` pushes monkeypatched
``load_app_config`` and ``create_panel_assistant_brain`` into this module's
globals before calling the async entry point, preserving the existing test
bridge semantics.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from ..brain.fallback import AUTH, MALFORMED, NETWORK, RATE_LIMIT, TIMEOUT, classify_fallback_reason
from ..brain.gemini import GeminiBrain
from ..brain.openai_compat import OpenAICompatBrain
from ..config import load_app_config
from ..operator_assistant import (
    build_assistant_messages,
    fallback_operator_answer,
)

_ASSISTANT_STALL_TIMEOUT_MS = 120_000
_ASSISTANT_FALLBACK_REASONS = frozenset({TIMEOUT, AUTH, RATE_LIMIT, MALFORMED, NETWORK})
_ASSISTANT_UNAVAILABLE_STATES = frozenset(
    {
        "unknown",
        "stopped",
        "unavailable",
        "offline",
        "error",
        "failed",
        "unhealthy",
    }
)


def _assistant_request_ts_ms(request_id: str) -> int | None:
    name = Path(request_id).name
    match = re.match(r"^job-assistant-(\d+)\.json$", name)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _coerce_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def assistant_stalled_degraded_reason(
    context: dict[str, Any], request_result: dict[str, Any], *, now_ms: int
) -> str | None:
    if not request_result:
        return None
    if str(request_result.get("advisory_mode") or "") == "degraded_brain_only":
        return None
    if str(request_result.get("answer") or "").strip():
        return None

    status = str(request_result.get("status") or "")
    if status not in {"queued", "thinking", "thinking through Voxera"}:
        return None

    if bool(context.get("queue_paused")):
        return "daemon_paused"

    current_state = context.get("health_current_state")
    daemon_state = ""
    if isinstance(current_state, dict):
        daemon_state = str(current_state.get("daemon_state") or "").strip().lower()
    if daemon_state in _ASSISTANT_UNAVAILABLE_STATES:
        return "daemon_unavailable"

    updated_at_ms = _coerce_int(request_result.get("updated_at_ms"))
    if (
        updated_at_ms is not None
        and updated_at_ms > 0
        and now_ms - updated_at_ms >= _ASSISTANT_STALL_TIMEOUT_MS
    ):
        return "queue_processing_timeout"

    request_id = str(request_result.get("request_id") or "")
    request_ts = _assistant_request_ts_ms(request_id)
    if request_ts is not None and now_ms - request_ts >= _ASSISTANT_STALL_TIMEOUT_MS:
        return "advisory_transport_stalled"

    return None


def create_panel_assistant_brain(provider: Any) -> OpenAICompatBrain | GeminiBrain:
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


def _degraded_mode_disclosure(degraded_reason: str, context: dict[str, Any]) -> str:
    daemon_state = "unknown"
    current_state = context.get("health_current_state")
    if isinstance(current_state, dict):
        daemon_state = str(current_state.get("daemon_state") or "unknown")
    if degraded_reason == "daemon_paused":
        return (
            "The advisory queue lane is paused right now, so I'm answering in model-only recovery mode. "
            "This is still read-only and grounded in current runtime context."
        )
    if degraded_reason in {"queue_processing_timeout", "advisory_transport_stalled"}:
        return (
            "The normal advisory queue transport is stalled, so this answer is coming through degraded "
            "model-only recovery mode while staying read-only and grounded."
        )
    if degraded_reason == "daemon_unavailable":
        return (
            f"I can still read the current control-plane picture, but daemon state looks '{daemon_state}', "
            "so this response is through degraded model-only recovery mode."
        )
    return (
        "Queue-backed advisory transport is unavailable, so I'm responding in degraded model-only recovery "
        "mode (still read-only and grounded)."
    )


async def generate_degraded_assistant_answer_async(
    question: str,
    context: dict[str, Any],
    *,
    thread_turns: list[dict[str, Any]],
    degraded_reason: str,
) -> dict[str, Any]:
    cfg = load_app_config()
    disclosure = _degraded_mode_disclosure(degraded_reason, context)
    prompt = f"{question.strip()}\n\nMode context: {disclosure}"
    messages = build_assistant_messages(prompt, context, thread_turns=thread_turns)

    attempts: list[tuple[str, Any]] = []
    for key in ("fast", "primary", "fallback"):
        provider = cfg.brain.get(key) if cfg.brain else None
        if provider is not None:
            attempts.append((key, provider))

    primary_attempt: tuple[str, Any] | None = attempts[0] if attempts else None
    fallback_attempt: tuple[str, Any] | None = attempts[1] if len(attempts) > 1 else None

    if primary_attempt is not None:
        primary_name, primary_provider = primary_attempt
        try:
            brain = create_panel_assistant_brain(primary_provider)
            resp = await brain.generate(messages, tools=[])
            text = str(resp.text or "").strip()
            if text:
                return {
                    "answer": f"{disclosure}\n\n{text}",
                    "provider": primary_name,
                    "model": str(primary_provider.model),
                    "fallback_used": False,
                    "fallback_from": None,
                    "fallback_reason": None,
                    "error_class": None,
                    "deterministic_used": False,
                }
            raise ValueError("assistant degraded primary provider returned empty response")
        except Exception as exc:
            reason = classify_fallback_reason(exc)
            if fallback_attempt is None or reason not in _ASSISTANT_FALLBACK_REASONS:
                fallback_attempt = None
            else:
                primary_error = (
                    reason,
                    type(exc).__name__,
                    primary_name,
                    str(primary_provider.model),
                )
            if fallback_attempt is None:
                primary_error = (
                    reason,
                    type(exc).__name__,
                    primary_name,
                    str(primary_provider.model),
                )
    else:
        primary_error = ("UNKNOWN", "RuntimeError", "none", "none")

    if fallback_attempt is not None:
        fallback_name, fallback_provider = fallback_attempt
        try:
            brain = create_panel_assistant_brain(fallback_provider)
            resp = await brain.generate(messages, tools=[])
            text = str(resp.text or "").strip()
            if text:
                return {
                    "answer": f"{disclosure}\n\n{text}",
                    "provider": fallback_name,
                    "model": str(fallback_provider.model),
                    "fallback_used": True,
                    "fallback_from": {
                        "provider": primary_error[2],
                        "model": primary_error[3],
                    },
                    "fallback_reason": primary_error[0],
                    "error_class": primary_error[1],
                    "deterministic_used": False,
                }
            raise ValueError("assistant degraded fallback provider returned empty response")
        except Exception as exc:
            final_reason = classify_fallback_reason(exc)
            final_class = type(exc).__name__
    else:
        final_reason = primary_error[0]
        final_class = primary_error[1]

    fallback_text = fallback_operator_answer(question, context)
    return {
        "answer": f"{disclosure}\n\n{fallback_text}",
        "provider": "deterministic_fallback",
        "model": "fallback_operator_answer",
        "fallback_used": False,
        "fallback_from": None,
        "fallback_reason": final_reason,
        "error_class": final_class,
        "deterministic_used": True,
    }


def generate_degraded_assistant_answer(
    question: str,
    context: dict[str, Any],
    *,
    thread_turns: list[dict[str, Any]],
    degraded_reason: str,
) -> dict[str, Any]:
    return asyncio.run(
        generate_degraded_assistant_answer_async(
            question,
            context,
            thread_turns=thread_turns,
            degraded_reason=degraded_reason,
        )
    )


def persist_degraded_assistant_result(
    queue_root: Path,
    *,
    request_id: str,
    thread_id: str,
    question: str,
    degraded_answer: dict[str, Any],
    degraded_reason: str,
    context: dict[str, Any],
    ts_ms: int,
) -> dict[str, Any]:
    normalized = f"{Path(request_id).stem}.json"
    artifact_dir = queue_root / "artifacts" / Path(normalized).stem
    artifact_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 2,
        "kind": "assistant_question",
        "thread_id": thread_id,
        "question": question,
        "answer": str(degraded_answer.get("answer") or ""),
        "updated_at_ms": ts_ms,
        "answered_at_ms": ts_ms,
        "provider": degraded_answer.get("provider"),
        "model": degraded_answer.get("model"),
        "fallback_used": bool(degraded_answer.get("fallback_used")),
        "fallback_from": degraded_answer.get("fallback_from"),
        "fallback_reason": degraded_answer.get("fallback_reason"),
        "error_class": degraded_answer.get("error_class"),
        "advisory_mode": "degraded_brain_only",
        "degraded_reason": degraded_reason,
        "degraded_at_ms": ts_ms,
        "deterministic_fallback_used": bool(degraded_answer.get("deterministic_used")),
        "context": context,
    }
    (artifact_dir / "assistant_response.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    return payload
