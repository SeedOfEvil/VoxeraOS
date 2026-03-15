from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import load_config as load_runtime_config
from ..core.file_intent import detect_blocked_file_intent
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
    draft_investigation_save_preview,
    drafting_guidance,
    is_active_preview_submit_request,
    is_explicit_handoff_request,
    is_investigation_save_request,
    maybe_draft_job_payload,
    normalize_preview_payload,
    submit_preview,
)
from ..vera.prompt import VERA_SYSTEM_PROMPT, vera_queue_boundary_summary
from ..vera.service import (
    _is_informational_web_query,
    append_session_turn,
    clear_session_turns,
    generate_preview_builder_update,
    generate_vera_reply,
    ingest_linked_job_completions,
    maybe_auto_surface_linked_completion,
    new_session_id,
    read_session_enrichment,
    read_session_handoff_state,
    read_session_investigation,
    read_session_preview,
    read_session_turns,
    register_session_linked_job,
    run_web_enrichment,
    session_debug_info,
    write_session_enrichment,
    write_session_handoff_state,
    write_session_investigation,
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
        register_session_linked_job(root, session_id, job_ref=f"inbox-{job_id}.json")
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
        "sent it to voxeraos",
        "sent it to the queue",
        "i sent it",
        "i queued it",
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


def _is_natural_confirmation_phrase(message: str) -> bool:
    normalized = message.strip().lower()
    if not normalized:
        return False
    return bool(
        re.fullmatch(
            r"(?:yes(?:\s+please)?|yes\s+go\s+ahead|go\s+ahead|do\s+it|send\s+it|submit\s+it|hand\s+it\s+off)[.!?]*",
            normalized,
        )
    )


def _is_voxera_control_turn(message: str, *, active_preview: dict[str, object] | None) -> bool:
    if active_preview is not None:
        return True
    if _is_natural_confirmation_phrase(message):
        return True
    if is_explicit_handoff_request(message) or is_active_preview_submit_request(message):
        return True
    return maybe_draft_job_payload(message, active_preview=None) is not None


def _looks_like_voxera_preview_dump(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return False
    if (
        "proposed voxeraos job" in lowered
        or "proposal for voxeraos" in lowered
        or "submit-ready voxeraos preview" in lowered
    ):
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
            "prepared a proposal",
            "prepared a preview",
            "prepared a draft",
            "prepared the following job",
            "drafted a proposal",
            "drafted a preview",
            "i drafted",
            "i've drafted",
            "i have drafted",
            "i've prepared",
            "i have prepared",
            "created a preview",
            "created a draft",
            "set up a preview",
            "set up a draft",
            "here is the prepared proposal",
            "here is the json",
            "here's a draft",
            "here is a draft",
            "updated the draft in the preview",
            "updated the preview",
            "preview is ready",
            "draft is ready",
            "preview ready",
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


def _conversational_preview_update_message(*, updated: bool, has_active_preview: bool) -> str:
    if updated:
        return "Understood. Nothing has been submitted or executed yet. I can send it whenever you’re ready."
    if has_active_preview:
        return "Understood. I still have the current request ready whenever you want to send it."
    return "I couldn’t safely prepare a request yet. If you share clearer target details, I can continue."


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
    ingest_linked_job_completions(root, active_session)
    auto_completion_note = maybe_auto_surface_linked_completion(root, active_session)

    append_session_turn(root, active_session, role="user", text=message)
    if auto_completion_note is not None:
        append_session_turn(root, active_session, role="assistant", text=auto_completion_note)
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
            f"I prepared a follow-up request based on evidence from `{evidence.job_id}`. "
            "This is preview-only; I did not submit anything to VoxeraOS."
        )
        append_session_turn(root, active_session, role="assistant", text=assistant_text)
        return _render_page(
            session_id=active_session,
            turns=read_session_turns(root, active_session),
            status="followup_preview_ready",
        )

    session_investigation = read_session_investigation(root, active_session)
    if is_investigation_save_request(message):
        investigation_preview = draft_investigation_save_preview(
            message,
            investigation_context=session_investigation,
        )
        if investigation_preview is None:
            assistant_text = (
                "I couldn't resolve those investigation result references in this session. "
                "Run a fresh read-only investigation first, then refer to valid result numbers "
                "(for example: 'save result 2 to a note' or 'save all findings')."
            )
            append_session_turn(root, active_session, role="assistant", text=assistant_text)
            return _render_page(
                session_id=active_session,
                turns=read_session_turns(root, active_session),
                status="investigation_reference_invalid",
            )

        write_session_preview(root, active_session, investigation_preview)
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
            "I prepared a governed save-to-note preview from your selected investigation findings. "
            "Nothing has been submitted yet."
        )
        append_session_turn(root, active_session, role="assistant", text=assistant_text)
        return _render_page(
            session_id=active_session,
            turns=read_session_turns(root, active_session),
            status="prepared_preview",
        )

    if (
        _is_natural_confirmation_phrase(message)
        or is_explicit_handoff_request(message)
        or is_active_preview_submit_request(message)
    ) and pending_preview is None:
        assistant_text, status = _submit_handoff(
            root=root,
            session_id=active_session,
            preview=None,
        )
        append_session_turn(root, active_session, role="assistant", text=assistant_text)
        return _render_page(
            session_id=active_session,
            turns=read_session_turns(root, active_session),
            status=status,
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

    # Blocked bounded file intent: fail closed with a clear refusal before
    # the message reaches the LLM, which might produce a misleading pseudo
    # action blob.
    blocked_refusal = detect_blocked_file_intent(message)
    if blocked_refusal is not None:
        append_session_turn(root, active_session, role="assistant", text=blocked_refusal)
        return _render_page(
            session_id=active_session,
            turns=read_session_turns(root, active_session),
            status="blocked_path",
        )

    is_info_query = _is_informational_web_query(message)
    informational_web_turn = is_info_query and pending_preview is None

    # When an active preview exists and the user makes an informational query,
    # run read-only enrichment and store it for follow-up pronoun resolution.
    is_enrichment_turn = is_info_query and pending_preview is not None
    session_enrichment = read_session_enrichment(root, active_session)
    if is_enrichment_turn:
        fresh_enrichment = await run_web_enrichment(user_message=message)
        if fresh_enrichment is not None:
            write_session_enrichment(root, active_session, fresh_enrichment)
            session_enrichment = fresh_enrichment

    enrichment_context = session_enrichment if pending_preview is not None else None

    builder_preview: dict[str, object] | None = None
    if not informational_web_turn:
        builder_preview = await generate_preview_builder_update(
            turns=turns,
            user_message=message,
            active_preview=pending_preview,
            enrichment_context=enrichment_context,
            investigation_context=session_investigation,
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
    investigation_payload = reply.get("investigation") if isinstance(reply, dict) else None
    if isinstance(investigation_payload, dict):
        write_session_investigation(root, active_session, investigation_payload)
    guarded_answer = _guardrail_submission_claim(
        root=root,
        session_id=active_session,
        text=reply["answer"],
    )
    in_voxera_preview_flow = pending_preview is not None or builder_preview is not None
    is_json_content_request = _is_explicit_json_content_request(message)
    is_voxera_control_turn = _is_voxera_control_turn(message, active_preview=pending_preview)
    should_hide_voxera_preview_dump = (
        in_voxera_preview_flow or is_voxera_control_turn
    ) and not is_json_content_request

    assistant_text = guarded_answer
    should_use_conversational_control_reply = not is_enrichment_turn and (
        (is_voxera_control_turn and not is_json_content_request)
        or (should_hide_voxera_preview_dump and _looks_like_voxera_preview_dump(guarded_answer))
        or (_looks_like_preview_update_claim(guarded_answer) and not is_json_content_request)
    )
    if should_use_conversational_control_reply:
        assistant_text = _conversational_preview_update_message(
            updated=builder_payload is not None,
            has_active_preview=pending_preview is not None,
        )

    status = "prepared_preview" if builder_payload is not None else reply["status"]

    append_session_turn(root, active_session, role="assistant", text=assistant_text)

    return _render_page(
        session_id=active_session,
        turns=read_session_turns(root, active_session),
        status=status,
    )


@app.get("/chat/updates")
def chat_updates(request: Request):
    session_id = str(request.query_params.get("session_id") or "").strip()
    active_session = session_id or (request.cookies.get("vera_session_id") or "").strip()
    active_session = active_session or new_session_id()

    try:
        since_count = int(str(request.query_params.get("since_count") or "0"))
    except ValueError:
        since_count = 0
    since_count = max(0, since_count)

    root = _active_queue_root()
    turns = read_session_turns(root, active_session)
    turn_count = len(turns)
    changed = turn_count > since_count

    payload: dict[str, object] = {
        "session_id": active_session,
        "turn_count": turn_count,
        "changed": changed,
    }
    if changed:
        payload["turns"] = turns
    return JSONResponse(payload)


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
