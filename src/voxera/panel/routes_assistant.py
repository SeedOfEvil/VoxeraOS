from __future__ import annotations

import secrets
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..operator_assistant import (
    append_thread_turn,
    build_operator_assistant_context,
    new_thread_id,
    normalize_thread_id,
)
from .assistant import (
    enqueue_assistant_question,
    read_assistant_result,
    read_assistant_thread_turns,
)
from .degraded_assistant_bridge import (
    assistant_stalled_degraded_reason as _assistant_stalled_degraded_reason,
)
from .degraded_assistant_bridge import (
    generate_degraded_assistant_answer as _generate_degraded_assistant_answer,
)
from .degraded_assistant_bridge import (
    generate_degraded_assistant_answer_async as _generate_degraded_assistant_answer_async,
)
from .degraded_assistant_bridge import (
    persist_degraded_assistant_result as _persist_degraded_assistant_result,
)


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
