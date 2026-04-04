from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from ..vera.prompt import vera_queue_boundary_summary
from ..vera.service import (
    generate_vera_reply,
)
from ..vera.session_store import (
    append_session_turn,
    new_session_id,
    read_session_turns,
)


def register_vera_routes(
    app: FastAPI,
    *,
    templates: Any,
    csrf_cookie: str,
    queue_root: Callable[[], Path],
    require_operator_auth_from_request: Callable[[Request], None],
    require_mutation_guard: Callable[[Request], Awaitable[None]],
    request_value: Callable[[Request, str, str], Awaitable[str]],
) -> None:
    def _render(
        request: Request,
        *,
        session_id: str,
        error: str = "",
        mode_status: str = "conversation",
        turns: list[dict[str, str]] | None = None,
    ) -> HTMLResponse:
        tmpl = templates.get_template("vera.html")
        csrf_token = request.cookies.get(csrf_cookie) or secrets.token_urlsafe(24)
        html = tmpl.render(
            session_id=session_id,
            error=error,
            mode_status=mode_status,
            turns=turns or [],
            queue_boundary=vera_queue_boundary_summary(),
            csrf_token=csrf_token,
        )
        response = HTMLResponse(content=html)
        response.set_cookie(csrf_cookie, csrf_token, httponly=False, samesite="strict")
        response.set_cookie("vera_session_id", session_id, httponly=False, samesite="lax")
        return response

    @app.get("/vera", response_class=HTMLResponse)
    def vera_page(request: Request):
        require_operator_auth_from_request(request)
        session_id = (request.cookies.get("vera_session_id") or "").strip() or new_session_id()
        turns = read_session_turns(queue_root(), session_id)
        return _render(request, session_id=session_id, turns=turns)

    @app.post("/vera/chat", response_class=HTMLResponse)
    async def vera_chat(request: Request):
        await require_mutation_guard(request)
        session_id = (await request_value(request, "session_id", "")).strip() or (
            request.cookies.get("vera_session_id") or ""
        ).strip()
        session_id = session_id or new_session_id()
        message = (await request_value(request, "message", "")).strip()
        if not message:
            return _render(
                request,
                session_id=session_id,
                turns=read_session_turns(queue_root(), session_id),
                error="Message is required.",
            )

        current_queue_root = queue_root()
        append_session_turn(current_queue_root, session_id, role="user", text=message)
        context_turns = read_session_turns(current_queue_root, session_id)
        reply = await generate_vera_reply(turns=context_turns, user_message=message)
        append_session_turn(current_queue_root, session_id, role="assistant", text=reply["answer"])
        return _render(
            request,
            session_id=session_id,
            mode_status=reply["status"],
            turns=read_session_turns(current_queue_root, session_id),
        )
