"""Panel routes for the operator-facing voice status surface.

Read-only diagnostic routes that show STT/TTS configuration and
availability state.  No interactive audio controls, no playback,
no mutation endpoints.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..voice.flags import load_voice_foundation_flags
from ..voice.voice_status_summary import build_voice_status_summary


def register_voice_routes(
    app: FastAPI,
    *,
    templates: Any,
    require_operator_auth_from_request: Callable[[Request], None],
) -> None:
    @app.get("/voice/status", response_class=HTMLResponse)
    def voice_status_page(request: Request) -> HTMLResponse:
        require_operator_auth_from_request(request)
        try:
            flags = load_voice_foundation_flags()
            summary = build_voice_status_summary(flags)
            error = None
        except Exception as exc:
            summary = None
            error = f"Failed to load voice status: {type(exc).__name__}: {exc}"

        tmpl = templates.get_template("voice.html")
        html = tmpl.render(summary=summary, error=error)
        return HTMLResponse(content=html)

    @app.get("/voice/status.json")
    def voice_status_json(request: Request) -> JSONResponse:
        require_operator_auth_from_request(request)
        try:
            flags = load_voice_foundation_flags()
            summary = build_voice_status_summary(flags)
            return JSONResponse({"ok": True, "voice": summary})
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                status_code=500,
            )
