from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..paths import queue_root
from ..vera.handoff import (
    drafting_guidance,
    is_explicit_handoff_request,
    maybe_draft_job_payload,
    normalize_preview_payload,
    preview_message,
    submit_preview,
)
from ..vera.prompt import VERA_SYSTEM_PROMPT, vera_queue_boundary_summary
from ..vera.service import (
    append_session_turn,
    clear_session_turns,
    generate_vera_reply,
    new_session_id,
    read_session_preview,
    read_session_turns,
    session_debug_info,
    write_session_preview,
)

app = FastAPI(title="Vera v0", version="0")

HERE = Path(__file__).resolve().parent
templates = Environment(
    loader=FileSystemLoader(str(HERE / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")


def _render_page(
    *,
    session_id: str,
    turns: list[dict[str, str]],
    status: str,
    error: str = "",
) -> HTMLResponse:
    root = queue_root()
    tmpl = templates.get_template("index.html")
    html = tmpl.render(
        session_id=session_id,
        turns=turns,
        mode_status=status,
        queue_boundary=vera_queue_boundary_summary(),
        error=error,
        debug_info=session_debug_info(root, session_id, mode_status=status),
        system_prompt=VERA_SYSTEM_PROMPT,
        pending_preview=read_session_preview(root, session_id),
        drafting_examples=drafting_guidance().examples,
    )
    response = HTMLResponse(content=html)
    response.set_cookie("vera_session_id", session_id, httponly=False, samesite="lax")
    return response


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    session_id = (request.cookies.get("vera_session_id") or "").strip() or new_session_id()
    return _render_page(
        session_id=session_id,
        turns=read_session_turns(queue_root(), session_id),
        status="conversation",
    )


@app.post("/chat", response_class=HTMLResponse)
async def chat(request: Request):
    raw = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw, keep_blank_values=True)
    message = str((parsed.get("message") or [""])[0])
    session_id = str((parsed.get("session_id") or [""])[0])

    active_session = session_id.strip() or (request.cookies.get("vera_session_id") or "").strip()
    active_session = active_session or new_session_id()

    if not message.strip():
        return _render_page(
            session_id=active_session,
            turns=read_session_turns(queue_root(), active_session),
            status="conversation",
            error="Message is required.",
        )

    root = queue_root()
    append_session_turn(root, active_session, role="user", text=message)
    turns = read_session_turns(root, active_session)

    pending_preview = read_session_preview(root, active_session)
    if is_explicit_handoff_request(message):
        if pending_preview is None:
            assistant_text = (
                "I don't have a prepared VoxeraOS job to submit yet. "
                "Ask me for an action and I'll draft a structured preview first."
            )
            status = "handoff_missing_preview"
        else:
            try:
                ack = submit_preview(queue_root=root, payload=pending_preview)
                assistant_text = ack["ack"]
                status = "handoff_submitted"
                write_session_preview(root, active_session, None)
            except Exception as exc:
                assistant_text = (
                    "I could not submit that job to VoxeraOS, so nothing was queued. "
                    f"Submission failed with: {exc}"
                )
                status = "handoff_submit_failed"
        append_session_turn(root, active_session, role="assistant", text=assistant_text)
        return _render_page(
            session_id=active_session,
            turns=read_session_turns(root, active_session),
            status=status,
        )

    drafted = maybe_draft_job_payload(message)
    if drafted is not None:
        payload = normalize_preview_payload(drafted)
        write_session_preview(root, active_session, payload)
        assistant_text = preview_message(payload)
        append_session_turn(root, active_session, role="assistant", text=assistant_text)
        return _render_page(
            session_id=active_session,
            turns=read_session_turns(root, active_session),
            status="prepared_preview",
        )

    reply = await generate_vera_reply(turns=turns, user_message=message)
    append_session_turn(root, active_session, role="assistant", text=reply["answer"])

    return _render_page(
        session_id=active_session,
        turns=read_session_turns(root, active_session),
        status=reply["status"],
    )


@app.post("/handoff", response_class=HTMLResponse)
async def handoff(request: Request):
    raw = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw, keep_blank_values=True)
    session_id = str((parsed.get("session_id") or [""])[0]).strip()
    active_session = session_id or (request.cookies.get("vera_session_id") or "").strip()
    active_session = active_session or new_session_id()
    root = queue_root()
    preview = read_session_preview(root, active_session)

    append_session_turn(root, active_session, role="user", text="[explicit handoff requested]")
    if preview is None:
        assistant_text = (
            "No prepared VoxeraOS job was found for this session, so nothing was submitted."
        )
        status = "handoff_missing_preview"
    else:
        try:
            ack = submit_preview(queue_root=root, payload=preview)
            assistant_text = ack["ack"]
            status = "handoff_submitted"
            write_session_preview(root, active_session, None)
        except Exception as exc:
            assistant_text = (
                "I could not submit that job to VoxeraOS, so nothing was queued. "
                f"Submission failed with: {exc}"
            )
            status = "handoff_submit_failed"
    append_session_turn(root, active_session, role="assistant", text=assistant_text)

    return _render_page(
        session_id=active_session,
        turns=read_session_turns(root, active_session),
        status=status,
    )


@app.post("/clear", response_class=HTMLResponse)
async def clear_chat(request: Request):
    raw = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw, keep_blank_values=True)
    session_id = str((parsed.get("session_id") or [""])[0]).strip()
    active_session = session_id or (request.cookies.get("vera_session_id") or "").strip()
    active_session = active_session or new_session_id()

    clear_session_turns(queue_root(), active_session)
    return _render_page(
        session_id=active_session,
        turns=[],
        status="conversation",
    )
