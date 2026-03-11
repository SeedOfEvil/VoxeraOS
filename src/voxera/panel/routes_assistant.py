from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..brain.fallback import AUTH, MALFORMED, NETWORK, RATE_LIMIT, TIMEOUT, classify_fallback_reason
from ..brain.gemini import GeminiBrain
from ..brain.openai_compat import OpenAICompatBrain
from ..config import load_app_config
from ..operator_assistant import (
    append_thread_turn,
    build_assistant_messages,
    build_operator_assistant_context,
    fallback_operator_answer,
    new_thread_id,
    normalize_thread_id,
)
from .assistant import (
    enqueue_assistant_question,
    read_assistant_result,
    read_assistant_thread_turns,
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


def _assistant_stalled_degraded_reason(
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


def _create_panel_assistant_brain(provider: Any) -> OpenAICompatBrain | GeminiBrain:
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


async def _generate_degraded_assistant_answer_async(
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
            brain = _create_panel_assistant_brain(primary_provider)
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
            brain = _create_panel_assistant_brain(fallback_provider)
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


def _generate_degraded_assistant_answer(
    question: str,
    context: dict[str, Any],
    *,
    thread_turns: list[dict[str, Any]],
    degraded_reason: str,
) -> dict[str, Any]:
    return asyncio.run(
        _generate_degraded_assistant_answer_async(
            question,
            context,
            thread_turns=thread_turns,
            degraded_reason=degraded_reason,
        )
    )


def _persist_degraded_assistant_result(
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


def _coerce_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def register_assistant_routes(
    app: FastAPI,
    *,
    templates: Any,
    csrf_cookie: str,
    queue_root: Callable[[], Path],
    require_operator_auth_from_request: Callable[[Request], None],
    require_mutation_guard: Callable[[Request], Awaitable[None]],
    request_value: Callable[[Request, str, str], Awaitable[str]],
    enqueue_assistant_question_fn: Callable[..., tuple[str, str]] = enqueue_assistant_question,
    assistant_stalled_degraded_reason_fn: Callable[
        ..., str | None
    ] = _assistant_stalled_degraded_reason,
    generate_degraded_assistant_answer_fn: Callable[
        ..., dict[str, Any]
    ] = _generate_degraded_assistant_answer,
    generate_degraded_assistant_answer_async_fn: Callable[
        ..., Awaitable[dict[str, Any]]
    ] = _generate_degraded_assistant_answer_async,
    persist_degraded_assistant_result_fn: Callable[
        ..., dict[str, Any]
    ] = _persist_degraded_assistant_result,
) -> None:
    def _render_assistant_page(
        request: Request,
        *,
        question: str = "",
        error: str = "",
        context: dict[str, Any] | None = None,
        request_result: dict[str, Any] | None = None,
        thread_id: str = "",
        thread_turns: list[dict[str, Any]] | None = None,
    ) -> HTMLResponse:
        tmpl = templates.get_template("assistant.html")
        csrf_token = request.cookies.get(csrf_cookie) or secrets.token_urlsafe(24)
        result = request_result or {}
        status = str(result.get("status") or "")
        html = tmpl.render(
            question=question,
            error=error,
            context=context or {},
            request_result=result,
            thread_id=thread_id,
            thread_turns=thread_turns or [],
            should_poll=status in {"queued", "thinking", "thinking through Voxera"},
            csrf_token=csrf_token,
            example_prompts=[
                "What is happening right now?",
                "From inside Voxera, how does the system look?",
                "Why would a job require approval?",
                "What do you suggest I check next?",
            ],
        )
        response = HTMLResponse(content=html)
        response.set_cookie(csrf_cookie, csrf_token, httponly=False, samesite="strict")
        return response

    @app.get("/assistant", response_class=HTMLResponse)
    def assistant_page(
        request: Request,
        request_id: str = "",
        question: str = "",
        thread_id: str = "",
    ):
        require_operator_auth_from_request(request)
        current_queue_root = queue_root()
        context = build_operator_assistant_context(current_queue_root)
        request_result = read_assistant_result(current_queue_root, request_id) if request_id else {}
        active_thread_id = thread_id or str(request_result.get("thread_id") or "")
        thread_turns = (
            read_assistant_thread_turns(current_queue_root, active_thread_id)
            if active_thread_id
            else []
        )

        degraded_reason = assistant_stalled_degraded_reason_fn(
            context,
            request_result,
            now_ms=int(time.time() * 1000),
        )
        if degraded_reason and request_id:
            resolved_question = question.strip() or "What is happening right now?"
            degraded_data = generate_degraded_assistant_answer_fn(
                resolved_question,
                context,
                thread_turns=thread_turns,
                degraded_reason=degraded_reason,
            )
            degraded_answer = str(degraded_data.get("answer") or "")
            now_ms = int(time.time() * 1000)
            if active_thread_id and not any(
                str(turn.get("request_id") or "") == request_id
                and str(turn.get("role") or "").lower() == "assistant"
                for turn in thread_turns
                if isinstance(turn, dict)
            ):
                append_thread_turn(
                    current_queue_root,
                    thread_id=active_thread_id,
                    role="assistant",
                    text=degraded_answer,
                    request_id=request_id,
                    ts_ms=now_ms,
                )
                thread_turns = read_assistant_thread_turns(current_queue_root, active_thread_id)

            persist_degraded_assistant_result_fn(
                current_queue_root,
                request_id=request_id,
                thread_id=active_thread_id,
                question=resolved_question,
                degraded_answer=degraded_data,
                degraded_reason=degraded_reason,
                context=context,
                ts_ms=now_ms,
            )
            request_result = read_assistant_result(current_queue_root, request_id)
            request_result["status"] = "answered"

        return _render_assistant_page(
            request,
            question=question,
            context=context,
            request_result=request_result,
            thread_id=active_thread_id,
            thread_turns=thread_turns,
        )

    @app.get("/assistant/progress/{request_id}", response_class=JSONResponse)
    def assistant_progress(request: Request, request_id: str):
        require_operator_auth_from_request(request)
        current_queue_root = queue_root()
        result = read_assistant_result(current_queue_root, request_id)
        return JSONResponse(
            {
                "ok": True,
                "request_id": result.get("request_id") or f"{Path(request_id).stem}.json",
                "status": result.get("status") or "unknown",
                "lifecycle_state": result.get("lifecycle_state") or "unknown",
                "bucket": result.get("bucket") or "unknown",
                "execution_lane": result.get("execution_lane") or "",
                "fast_lane": result.get("fast_lane")
                if isinstance(result.get("fast_lane"), dict)
                else None,
                "intent_route": result.get("intent_route")
                if isinstance(result.get("intent_route"), dict)
                else None,
                "approval_status": result.get("approval_status") or "none",
                "current_step_index": int(result.get("current_step_index") or 0),
                "total_steps": int(result.get("total_steps") or 0),
                "last_attempted_step": int(result.get("last_attempted_step") or 0),
                "last_completed_step": int(result.get("last_completed_step") or 0),
                "latest_summary": str(result.get("latest_summary") or ""),
                "terminal_outcome": str(result.get("terminal_outcome") or ""),
                "stop_reason": result.get("stop_reason"),
                "error": str(result.get("error") or ""),
                "updated_at_ms": result.get("updated_at_ms"),
                "has_answer": bool(str(result.get("answer") or "").strip()),
            }
        )

    @app.post("/assistant/ask", response_class=HTMLResponse)
    async def assistant_ask(request: Request):
        await require_mutation_guard(request)
        question = (await request_value(request, "question", "")).strip()
        thread_id = (await request_value(request, "thread_id", "")).strip()
        if not question:
            return _render_assistant_page(
                request,
                question=question,
                error="Question is required.",
                context=build_operator_assistant_context(queue_root()),
                request_result={},
                thread_id=thread_id,
                thread_turns=read_assistant_thread_turns(queue_root(), thread_id),
            )

        try:
            request_id, thread_id = enqueue_assistant_question_fn(
                queue_root(), question, thread_id=thread_id
            )
        except OSError:
            current_queue_root = queue_root()
            normalized_thread = normalize_thread_id(thread_id) if thread_id else new_thread_id()
            context = build_operator_assistant_context(current_queue_root)
            degraded_data = await generate_degraded_assistant_answer_async_fn(
                question,
                context,
                thread_turns=read_assistant_thread_turns(current_queue_root, normalized_thread),
                degraded_reason="queue_unavailable",
            )
            degraded_answer = str(degraded_data.get("answer") or "")
            ts_ms = int(time.time() * 1000)
            append_thread_turn(
                current_queue_root,
                thread_id=normalized_thread,
                role="user",
                text=question,
                request_id=f"degraded-{ts_ms}",
                ts_ms=ts_ms,
            )
            append_thread_turn(
                current_queue_root,
                thread_id=normalized_thread,
                role="assistant",
                text=degraded_answer,
                request_id=f"degraded-{ts_ms}",
                ts_ms=ts_ms,
            )
            return _render_assistant_page(
                request,
                question=question,
                context=context,
                request_result={
                    "request_id": f"degraded-{ts_ms}.json",
                    "status": "answered",
                    "lifecycle_state": "degraded",
                    "answer": degraded_answer,
                    "advisory_mode": "degraded_brain_only",
                    "degraded_reason": "queue_unavailable",
                    "fallback_used": bool(degraded_data.get("fallback_used")),
                    "fallback_reason": degraded_data.get("fallback_reason"),
                    "provider": degraded_data.get("provider"),
                    "model": degraded_data.get("model"),
                    "thread_id": normalized_thread,
                },
                thread_id=normalized_thread,
                thread_turns=read_assistant_thread_turns(current_queue_root, normalized_thread),
            )

        query = urlencode({"request_id": request_id, "thread_id": thread_id, "question": question})
        return RedirectResponse(url=f"/assistant?{query}", status_code=303)
