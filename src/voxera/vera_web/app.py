from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import load_config as load_runtime_config
from ..paths import queue_root as default_queue_root
from ..vera.evidence_review import (
    draft_followup_preview,
    is_followup_preview_request,
    is_review_request,
    maybe_extract_job_id,
    review_job_outcome,
    review_message,
)
from ..vera.handoff import (
    drafting_guidance,
    is_active_preview_submit_request,
    is_explicit_handoff_request,
    maybe_draft_job_payload,
    normalize_preview_payload,
    submit_preview,
)
from ..vera.prompt import VERA_SYSTEM_PROMPT, vera_queue_boundary_summary
from ..vera.service import (
    append_session_turn,
    clear_session_turns,
    generate_preview_builder_update,
    generate_vera_reply,
    new_session_id,
    read_session_handoff_state,
    read_session_preview,
    read_session_turns,
    session_debug_info,
    write_session_handoff_state,
    write_session_preview,
)

app = FastAPI(title="Vera v0", version="0")

HERE = Path(__file__).resolve().parent
templates = Environment(
    loader=FileSystemLoader(str(HERE / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")


def _active_queue_root() -> Path:
    try:
        return load_runtime_config().queue_root
    except Exception:
        return default_queue_root()


def _submit_handoff(
    *,
    root: Path,
    session_id: str,
    preview: dict[str, object] | None,
) -> tuple[str, str]:
    if preview is None:
        write_session_handoff_state(
            root,
            session_id,
            attempted=False,
            queue_path=str(root),
            status="missing_preview",
            error="No prepared preview found",
        )
        return (
            "I don’t have a prepared preview in this session yet, so I did not submit anything to VoxeraOS.",
            "handoff_missing_preview",
        )

    try:
        ack = submit_preview(queue_root=root, payload=preview)
        job_id = str(ack.get("job_id") or "").strip()
        if not job_id:
            raise RuntimeError("queue accepted payload but returned no job id")
        write_session_handoff_state(
            root,
            session_id,
            attempted=True,
            queue_path=str(ack.get("queue_path") or root),
            status="submitted",
            job_id=job_id,
        )
        write_session_preview(root, session_id, None)
        return str(ack["ack"]), "handoff_submitted"
    except Exception as exc:
        write_session_handoff_state(
            root,
            session_id,
            attempted=True,
            queue_path=str(root),
            status="submit_failed",
            error=str(exc),
        )
        return (
            "I could not submit that job to VoxeraOS, so nothing was queued. "
            f"Submission failed with: {exc}",
            "handoff_submit_failed",
        )


def _is_active_preview_submit_intent(message: str, *, preview_available: bool) -> bool:
    if not preview_available:
        return False
    return is_explicit_handoff_request(message) or is_active_preview_submit_request(message)


def _looks_like_submission_claim(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return False
    suspicious_phrases = (
        "submitted to voxeraos",
        "submitted the job",
        "request is now in the queue",
        "handed off",
        "queued",
    )
    return any(phrase in lowered for phrase in suspicious_phrases)


def _guardrail_submission_claim(*, root: Path, session_id: str, text: str) -> str:
    handoff = read_session_handoff_state(root, session_id) or {}
    confirmed = str(handoff.get("status") or "") == "submitted" and bool(handoff.get("job_id"))
    if _looks_like_submission_claim(text) and not confirmed:
        return (
            "I have not submitted anything to VoxeraOS yet. "
            "No confirmed queue handoff is recorded for this session. "
            "If you want to proceed, ask me to prepare a job preview first, then explicitly hand it off."
        )
    return text


def _looks_like_voxera_preview_dump(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return False
    if "proposed voxeraos job" in lowered or "submit-ready voxeraos preview" in lowered:
        return True
    return "```json" in lowered and any(
        marker in lowered
        for marker in (
            '"goal"',
            '"write_file"',
            '"enqueue_child"',
            "voxeraos",
        )
    )


def _looks_like_preview_update_claim(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return False
    return any(
        phrase in lowered
        for phrase in (
            "updated the draft in the preview",
            "updated the preview",
            "latest version is ready in the preview",
            "proposal in the preview",
            "refined the proposal in the preview",
        )
    )


def _is_explicit_json_content_request(message: str) -> bool:
    lowered = message.strip().lower()
    if not lowered:
        return False
    if "voxera" in lowered and "json" in lowered:
        return False
    return bool(
        re.search(
            r"\b(json\s+(config|payload|body|schema|file|example)|return\s+json|show\s+me\s+json|generate\s+json|as\s+json)\b",
            lowered,
        )
    )


def _conversational_preview_update_message(*, updated: bool) -> str:
    if updated:
        return (
            "I updated the draft in the preview. "
            "The preview pane is the authoritative version I would hand off."
        )
    return (
        "I kept the current preview unchanged because I couldn’t produce a safe valid refinement yet. "
        "If you want, I can refine it with more specific goal/target details."
    )


def _render_page(
    *,
    session_id: str,
    turns: list[dict[str, str]],
    status: str,
    error: str = "",
) -> HTMLResponse:
    root = _active_queue_root()
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
    root = _active_queue_root()
    return _render_page(
        session_id=session_id,
        turns=read_session_turns(root, session_id),
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
        root = _active_queue_root()
        return _render_page(
            session_id=active_session,
            turns=read_session_turns(root, active_session),
            status="conversation",
            error="Message is required.",
        )

    root = _active_queue_root()
    append_session_turn(root, active_session, role="user", text=message)
    turns = read_session_turns(root, active_session)

    pending_preview = read_session_preview(root, active_session)
    requested_job_id = maybe_extract_job_id(message)
    if is_review_request(message) or requested_job_id is not None:
        target_job_id = requested_job_id
        if not target_job_id:
            handoff = read_session_handoff_state(root, active_session) or {}
            target_job_id = str(handoff.get("job_id") or "") or None
        evidence = review_job_outcome(queue_root=root, requested_job_id=target_job_id)
        if evidence is None:
            assistant_text = (
                "I could not resolve a VoxeraOS job to review from canonical evidence. "
                "Share a job id (for example `job-123.json`) or submit a job first in this session."
            )
            status = "review_missing_job"
        else:
            assistant_text = review_message(evidence)
            status = "reviewed_job_outcome"
        append_session_turn(root, active_session, role="assistant", text=assistant_text)
        return _render_page(
            session_id=active_session,
            turns=read_session_turns(root, active_session),
            status=status,
        )

    if is_followup_preview_request(message):
        handoff = read_session_handoff_state(root, active_session) or {}
        evidence = review_job_outcome(
            queue_root=root,
            requested_job_id=str(handoff.get("job_id") or "") or None,
        )
        if evidence is None:
            assistant_text = (
                "I can draft a follow-up preview once we have a resolvable VoxeraOS job outcome. "
                "Please give me a job id or ask me to review your most recent submitted job first."
            )
            append_session_turn(root, active_session, role="assistant", text=assistant_text)
            return _render_page(
                session_id=active_session,
                turns=read_session_turns(root, active_session),
                status="followup_missing_evidence",
            )

        payload = draft_followup_preview(evidence)
        write_session_preview(root, active_session, payload)
        write_session_handoff_state(
            root,
            active_session,
            attempted=False,
            queue_path=str(root),
            status="preview_ready",
            error=None,
            job_id=None,
        )
        assistant_text = (
            f"I drafted a follow-up proposal in the preview based on evidence from `{evidence.job_id}`. "
            "This is preview-only; I did not submit anything to VoxeraOS."
        )
        append_session_turn(root, active_session, role="assistant", text=assistant_text)
        return _render_page(
            session_id=active_session,
            turns=read_session_turns(root, active_session),
            status="followup_preview_ready",
        )

    if _is_active_preview_submit_intent(message, preview_available=pending_preview is not None):
        assistant_text, status = _submit_handoff(
            root=root,
            session_id=active_session,
            preview=pending_preview,
        )
        append_session_turn(root, active_session, role="assistant", text=assistant_text)
        return _render_page(
            session_id=active_session,
            turns=read_session_turns(root, active_session),
            status=status,
        )

    drafted = maybe_draft_job_payload(message, active_preview=pending_preview)
    if drafted is not None:
        payload = normalize_preview_payload(drafted)
        write_session_preview(root, active_session, payload)
        write_session_handoff_state(
            root,
            active_session,
            attempted=False,
            queue_path=str(root),
            status="preview_ready",
            error=None,
            job_id=None,
        )
        assistant_text = "I drafted a VoxeraOS proposal in the preview. Nothing has been submitted or executed yet. Say ‘send it’ when you want me to hand it off."
        append_session_turn(root, active_session, role="assistant", text=assistant_text)
        return _render_page(
            session_id=active_session,
            turns=read_session_turns(root, active_session),
            status="prepared_preview",
        )

    builder_preview = await generate_preview_builder_update(
        turns=turns,
        user_message=message,
        active_preview=pending_preview,
    )
    builder_payload: dict[str, object] | None = None
    if builder_preview is not None:
        try:
            builder_payload = normalize_preview_payload(builder_preview)
        except Exception:
            builder_payload = None
        if builder_payload is not None:
            write_session_preview(root, active_session, builder_payload)
            write_session_handoff_state(
                root,
                active_session,
                attempted=False,
                queue_path=str(root),
                status="preview_ready",
                error=None,
                job_id=None,
            )

    reply = await generate_vera_reply(turns=turns, user_message=message)
    guarded_answer = _guardrail_submission_claim(
        root=root,
        session_id=active_session,
        text=reply["answer"],
    )
    in_voxera_preview_flow = pending_preview is not None or builder_preview is not None
    is_json_content_request = _is_explicit_json_content_request(message)
    should_hide_voxera_preview_dump = in_voxera_preview_flow and not is_json_content_request

    assistant_text = guarded_answer
    if should_hide_voxera_preview_dump and _looks_like_voxera_preview_dump(guarded_answer):
        assistant_text = _conversational_preview_update_message(updated=builder_payload is not None)
    elif (
        _looks_like_preview_update_claim(guarded_answer)
        and builder_payload is None
        and pending_preview is not None
    ):
        assistant_text = _conversational_preview_update_message(updated=False)

    status = reply["status"]

    append_session_turn(root, active_session, role="assistant", text=assistant_text)

    return _render_page(
        session_id=active_session,
        turns=read_session_turns(root, active_session),
        status=status,
    )


@app.post("/handoff", response_class=HTMLResponse)
async def handoff(request: Request):
    raw = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw, keep_blank_values=True)
    session_id = str((parsed.get("session_id") or [""])[0]).strip()
    active_session = session_id or (request.cookies.get("vera_session_id") or "").strip()
    active_session = active_session or new_session_id()
    root = _active_queue_root()
    preview = read_session_preview(root, active_session)

    append_session_turn(root, active_session, role="user", text="[explicit handoff requested]")
    assistant_text, status = _submit_handoff(
        root=root,
        session_id=active_session,
        preview=preview,
    )
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

    clear_session_turns(_active_queue_root(), active_session)
    return _render_page(
        session_id=active_session,
        turns=[],
        status="conversation",
    )
