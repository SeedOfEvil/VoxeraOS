from __future__ import annotations

import json
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
    followup_preview_message,
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
    preview_message,
    submit_preview,
)
from ..vera.prompt import VERA_SYSTEM_PROMPT, vera_queue_boundary_summary
from ..vera.service import (
    append_session_turn,
    clear_session_turns,
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


def _extract_json_objects(text: str) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    for match in re.findall(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, flags=re.IGNORECASE):
        try:
            parsed = json.loads(match)
        except Exception:
            continue
        if isinstance(parsed, dict):
            candidates.append(parsed)

    decoder = json.JSONDecoder()
    scan_index = 0
    while True:
        start = text.find("{", scan_index)
        if start < 0:
            break
        try:
            parsed, end = decoder.raw_decode(text[start:])
        except Exception:
            scan_index = start + 1
            continue
        if isinstance(parsed, dict):
            candidates.append(parsed)
        scan_index = start + max(end, 1)

    return candidates


def _is_lossless_preview_candidate(
    candidate: dict[str, object], normalized: dict[str, object]
) -> bool:
    candidate_keys = set(candidate.keys())
    normalized_keys = set(normalized.keys())
    if candidate_keys - normalized_keys:
        return False

    candidate_write_file = candidate.get("write_file")
    normalized_write_file = normalized.get("write_file")
    if isinstance(candidate_write_file, dict):
        if not isinstance(normalized_write_file, dict):
            return False
        if set(candidate_write_file.keys()) - set(normalized_write_file.keys()):
            return False

    return True


def _coerce_assistant_preview_update(text: str) -> tuple[dict[str, object] | None, bool]:
    saw_preview_like = False
    latest_normalized: dict[str, object] | None = None
    for candidate in _extract_json_objects(text):
        if not any(
            key in candidate for key in ("goal", "title", "write_file", "enqueue_child", "content")
        ):
            continue
        saw_preview_like = True
        try:
            normalized = normalize_preview_payload(candidate)
        except Exception:
            continue
        if not _is_lossless_preview_candidate(candidate, normalized):
            continue
        latest_normalized = normalized
    return latest_normalized, saw_preview_like


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

        payload = draft_followup_preview(evidence, user_message=message)
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
        assistant_text = followup_preview_message(evidence, payload, user_message=message)
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
        assistant_text = preview_message(payload)
        append_session_turn(root, active_session, role="assistant", text=assistant_text)
        return _render_page(
            session_id=active_session,
            turns=read_session_turns(root, active_session),
            status="prepared_preview",
        )

    reply = await generate_vera_reply(turns=turns, user_message=message)
    guarded_answer = _guardrail_submission_claim(
        root=root,
        session_id=active_session,
        text=reply["answer"],
    )
    model_preview, saw_preview_like = _coerce_assistant_preview_update(guarded_answer)
    if model_preview is not None:
        write_session_preview(root, active_session, model_preview)
        write_session_handoff_state(
            root,
            active_session,
            attempted=False,
            queue_path=str(root),
            status="preview_ready",
            error=None,
            job_id=None,
        )
        assistant_text = preview_message(model_preview)
        status = "prepared_preview"
    elif saw_preview_like:
        assistant_text = (
            "I couldn’t safely turn that into a submit-ready VoxeraOS preview yet. "
            "Do you want me to create a file preview with explicit filename and exact content?"
        )
        status = "clarify_preview_shape"
    else:
        assistant_text = guarded_answer
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
