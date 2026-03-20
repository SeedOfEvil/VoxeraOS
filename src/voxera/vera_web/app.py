from __future__ import annotations

import inspect
import re
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import load_config as load_runtime_config
from ..core.code_draft_intent import (
    classify_code_draft_intent,
    extract_code_from_reply,
    has_code_file_extension,
    is_code_draft_request,
)
from ..core.file_intent import detect_blocked_file_intent
from ..core.writing_draft_intent import (
    classify_writing_draft_intent,
    extract_text_draft_from_reply,
    is_text_draft_preview,
    is_writing_draft_request,
    is_writing_refinement_request,
)
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
    derive_investigation_comparison,
    derive_investigation_expansion,
    derive_investigation_summary,
    diagnostics_request_refusal,
    diagnostics_service_or_logs_intent,
    draft_investigation_derived_save_preview,
    draft_investigation_save_preview,
    drafting_guidance,
    is_active_preview_submit_request,
    is_explicit_handoff_request,
    is_investigation_compare_request,
    is_investigation_derived_followup_save_request,
    is_investigation_derived_save_request,
    is_investigation_expand_request,
    is_investigation_save_request,
    is_investigation_summary_request,
    is_recent_assistant_content_save_request,
    maybe_draft_job_payload,
    normalize_preview_payload,
    select_investigation_results,
    submit_preview,
)
from ..vera.prompt import VERA_SYSTEM_PROMPT, vera_queue_boundary_summary
from ..vera.service import (
    _CODE_DRAFT_HINT,
    _WRITING_DRAFT_HINT,
    _is_informational_web_query,
    append_session_turn,
    clear_session_turns,
    generate_preview_builder_update,
    generate_vera_reply,
    ingest_linked_job_completions,
    maybe_auto_surface_linked_completion,
    new_session_id,
    read_session_derived_investigation_output,
    read_session_enrichment,
    read_session_handoff_state,
    read_session_investigation,
    read_session_preview,
    read_session_turns,
    read_session_updated_at_ms,
    register_session_linked_job,
    run_web_enrichment,
    session_debug_info,
    write_session_derived_investigation_output,
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
    if active_preview is not None and not is_text_draft_preview(active_preview):
        return True
    if is_recent_assistant_content_save_request(message):
        return True
    if _is_natural_confirmation_phrase(message):
        return True
    if is_explicit_handoff_request(message) or is_active_preview_submit_request(message):
        return True
    return maybe_draft_job_payload(message, active_preview=None) is not None


def _prefer_derived_followup_save(
    *,
    message: str,
    session_derived_output: dict[str, object] | None,
    turns: list[dict[str, str]],
) -> bool:
    if not isinstance(session_derived_output, dict):
        return False
    if not is_investigation_derived_followup_save_request(message):
        return False

    expected_answer = str(session_derived_output.get("answer") or "").strip()
    if not expected_answer:
        return False

    for turn in reversed(turns):
        role = str(turn.get("role") or "").strip().lower()
        if role != "assistant":
            continue
        latest_assistant = str(turn.get("text") or "").strip()
        return latest_assistant == expected_answer
    return False


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


def _strip_internal_control_blocks(text: str) -> str:
    """Remove internal Voxera control markup from user-visible assistant text."""
    if not text:
        return ""

    cleaned = re.sub(
        r"```[^\n]*\n\s*<voxera_control\b.*?</voxera_control>\s*```",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    cleaned = re.sub(
        r"<voxera_control\b[^>]*>.*?</voxera_control>",
        "",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _text_outside_code_blocks(text: str) -> str:
    """Return text with fenced code blocks removed."""
    return re.sub(r"```[^\n]*\n.*?```", "", text, flags=re.DOTALL).strip()


def _looks_like_preview_pane_claim(text: str) -> bool:
    """Detect claims that a preview/draft currently exists or is available.

    Only inspects text outside of fenced code blocks to avoid false
    positives from code content that mentions "preview" as a variable name
    or string literal.
    """
    outside = _text_outside_code_blocks(text)
    lowered = outside.lower()
    if not lowered:
        return False
    # Direct claims of preview/draft availability in a pane/panel
    claim_phrases = (
        "preview pane",
        "preview panel",
        "in the preview",
        "in your preview",
        "review it in",
        "check the preview",
        "available in preview",
        "visible in preview",
        "find it in the preview",
        "see it in the preview",
    )
    if any(p in lowered for p in claim_phrases):
        return True
    return _looks_like_preview_update_claim(text)


def _is_governed_writing_preview(preview: dict[str, object] | None) -> bool:
    if not is_text_draft_preview(preview):
        return False
    goal = str((preview or {}).get("goal") or "").strip().lower()
    return goal.startswith("draft a ")


def _guardrail_false_preview_claim(*, text: str, preview_exists: bool) -> str:
    """Replace false preview-existence claims with truthful language.

    When the LLM claims a preview/draft was created or is available but no
    authoritative preview state exists, replace the claim.  Fenced code
    blocks are preserved so users can still see generated code.
    """
    if preview_exists:
        return text
    if not _looks_like_preview_pane_claim(text):
        return text

    # Preserve any fenced code blocks
    code_blocks = re.findall(r"```[^\n]*\n.*?```", text, flags=re.DOTALL)
    if code_blocks:
        preserved = "\n\n".join(code_blocks)
        return (
            preserved
            + "\n\n"
            + "Note: I was not able to create a governed preview for this code. "
            + "The code above is shown for reference only — "
            + "no preview is active in this session."
        )
    return (
        "I was not able to prepare a governed preview for this request. "
        "If you share clearer details, I can try again."
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


def _conversational_preview_update_message(
    *,
    updated: bool,
    has_active_preview: bool,
    user_message: str,
) -> str:
    if updated:
        return "Understood. Nothing has been submitted or executed yet. I can send it whenever you’re ready."
    if has_active_preview:
        return "Understood. I still have the current request ready whenever you want to send it."
    if is_recent_assistant_content_save_request(user_message):
        return (
            "I couldn't resolve a suitable recent assistant-authored summary/answer in this active session, "
            "so I didn't prepare a write preview. Please point to a specific recent response or ask me to "
            "generate one first."
        )
    return "I couldn’t safely prepare a request yet. If you share clearer target details, I can continue."


async def _generate_vera_reply_with_optional_draft_hints(
    *,
    turns: list[dict[str, str]],
    user_message: str,
    code_draft: bool,
    writing_draft: bool,
) -> dict[str, object]:
    signature = inspect.signature(generate_vera_reply)
    parameters = signature.parameters
    if "code_draft" in parameters or "writing_draft" in parameters:
        return await generate_vera_reply(
            turns=turns,
            user_message=user_message,
            code_draft=code_draft,
            writing_draft=writing_draft,
        )

    hinted_message = user_message
    if code_draft:
        hinted_message = user_message + _CODE_DRAFT_HINT
    elif writing_draft:
        hinted_message = user_message + _WRITING_DRAFT_HINT
    return await generate_vera_reply(turns=turns, user_message=hinted_message)


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
    diagnostics_service_turn = diagnostics_service_or_logs_intent(message)

    diagnostics_refusal = diagnostics_request_refusal(message)
    if diagnostics_refusal is not None:
        append_session_turn(root, active_session, role="assistant", text=diagnostics_refusal)
        return _render_page(
            session_id=active_session,
            turns=read_session_turns(root, active_session),
            status="blocked_diagnostics",
        )

    if (
        is_review_request(message) and not diagnostics_service_turn
    ) or requested_job_id is not None:
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
    session_derived_output = read_session_derived_investigation_output(root, active_session)

    should_attempt_derived_save = is_investigation_derived_save_request(message) or (
        _prefer_derived_followup_save(
            message=message,
            session_derived_output=session_derived_output,
            turns=turns,
        )
    )
    if should_attempt_derived_save:
        derived_preview = draft_investigation_derived_save_preview(
            message,
            derived_output=session_derived_output,
        )
        if derived_preview is None:
            assistant_text = (
                "I couldn't find a current investigation comparison, summary, or expanded result to save in this session. "
                "Ask me to compare, summarize, or expand a finding first, then ask to save that output."
            )
            append_session_turn(root, active_session, role="assistant", text=assistant_text)
            return _render_page(
                session_id=active_session,
                turns=read_session_turns(root, active_session),
                status="investigation_derived_missing",
            )
        write_session_preview(root, active_session, derived_preview)
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
            "I prepared a governed save-to-note preview from the latest investigation-derived text artifact. "
            "Nothing has been submitted yet."
        )
        append_session_turn(root, active_session, role="assistant", text=assistant_text)
        return _render_page(
            session_id=active_session,
            turns=read_session_turns(root, active_session),
            status="prepared_preview",
        )

    if is_investigation_compare_request(message):
        comparison = derive_investigation_comparison(
            message,
            investigation_context=session_investigation,
        )
        if comparison is None:
            assistant_text = (
                "I couldn't resolve those result references for comparison in this session. "
                "Run a fresh read-only investigation first, then compare valid result numbers "
                "(for example: 'compare results 1 and 3' or 'compare all findings')."
            )
            append_session_turn(root, active_session, role="assistant", text=assistant_text)
            return _render_page(
                session_id=active_session,
                turns=read_session_turns(root, active_session),
                status="investigation_reference_invalid",
            )
        write_session_derived_investigation_output(root, active_session, comparison)
        append_session_turn(
            root, active_session, role="assistant", text=str(comparison.get("answer") or "")
        )
        return _render_page(
            session_id=active_session,
            turns=read_session_turns(root, active_session),
            status="ok:investigation_comparison",
        )

    if is_investigation_summary_request(message):
        summary = derive_investigation_summary(
            message,
            investigation_context=session_investigation,
        )
        if summary is None:
            assistant_text = (
                "I couldn't resolve those result references for summary in this session. "
                "Run a fresh read-only investigation first, then summarize valid result numbers "
                "(for example: 'summarize result 2' or 'summarize all findings')."
            )
            append_session_turn(root, active_session, role="assistant", text=assistant_text)
            return _render_page(
                session_id=active_session,
                turns=read_session_turns(root, active_session),
                status="investigation_reference_invalid",
            )
        write_session_derived_investigation_output(root, active_session, summary)
        append_session_turn(
            root, active_session, role="assistant", text=str(summary.get("answer") or "")
        )
        return _render_page(
            session_id=active_session,
            turns=read_session_turns(root, active_session),
            status="ok:investigation_summary",
        )

    if is_investigation_expand_request(message):
        expansion_error_text = (
            "I couldn't resolve that investigation result for expansion in this session. "
            "Run a fresh read-only investigation first, then expand one valid result number "
            "(for example: 'expand result 1 please')."
        )
        selected_results, selected_ids = select_investigation_results(
            message,
            investigation_context=session_investigation,
        )
        if (
            selected_results is None
            or selected_ids is None
            or len(selected_ids) != 1
            or not isinstance(session_investigation, dict)
        ):
            append_session_turn(root, active_session, role="assistant", text=expansion_error_text)
            return _render_page(
                session_id=active_session,
                turns=read_session_turns(root, active_session),
                status="investigation_reference_invalid",
            )

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
    informational_web_turn = (
        is_info_query
        and pending_preview is None
        and not diagnostics_service_or_logs_intent(message)
        and not is_recent_assistant_content_save_request(message)
    )
    # Pre-compute code-draft intent so the LLM call can be given the code-generation
    # hint before the reply is generated.  This flag is reused below where
    # is_code_draft_turn would have been computed from the same expression.
    is_code_draft_turn = (
        is_code_draft_request(message)
        and not informational_web_turn
        and not is_writing_draft_request(message)
    )
    is_writing_draft_turn = (
        not is_code_draft_turn and not informational_web_turn and is_writing_draft_request(message)
    )

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

    reply = await _generate_vera_reply_with_optional_draft_hints(
        turns=turns,
        user_message=message,
        code_draft=is_code_draft_turn,
        writing_draft=is_writing_draft_turn,
    )
    reply_answer = str(reply.get("answer") or "")
    reply_status = str(reply.get("status") or "")
    investigation_payload = reply.get("investigation") if isinstance(reply, dict) else None
    if isinstance(investigation_payload, dict):
        write_session_investigation(root, active_session, investigation_payload)
        write_session_derived_investigation_output(root, active_session, None)
    elif is_investigation_expand_request(message):
        expansion = derive_investigation_expansion(
            message,
            investigation_context=session_investigation,
            expanded_text=reply_answer,
        )
        if expansion is not None:
            write_session_derived_investigation_output(root, active_session, expansion)

    # Code draft post-processing: after the LLM reply we know the actual code
    # content.  Extract it from any fenced block and inject it into the preview
    # so the preview is authoritative and submit-ready — not just a placeholder.
    # is_code_draft_turn was pre-computed above (before the LLM call) so the
    # code-draft hint could be passed to generate_vera_reply.
    reply_code_content = extract_code_from_reply(reply_answer)
    sanitized_answer = _strip_internal_control_blocks(reply_answer)
    reply_text_draft = extract_text_draft_from_reply(sanitized_answer)

    # Code draft refinement: when an active preview has a code-type file
    # extension and the LLM reply contains a fenced code block, treat this
    # turn as a code draft update so the reply is shown (not suppressed) and
    # the preview content is refreshed with the updated code.
    if (
        not is_code_draft_turn
        and not informational_web_turn
        and not is_enrichment_turn
        and isinstance(pending_preview, dict)
        and reply_code_content is not None
    ):
        existing_wf = pending_preview.get("write_file")
        if isinstance(existing_wf, dict) and has_code_file_extension(
            str(existing_wf.get("path") or "")
        ):
            is_code_draft_turn = True

    if is_code_draft_turn and reply_code_content is not None:
        # Use existing builder payload if the hidden compiler built one;
        # otherwise create a fresh code draft preview now.
        target_draft: dict[str, object] | None = builder_payload
        builder_has_explicit_content = False
        explicit_literal_content_refinement = bool(
            re.search(
                r"\b("
                r"add\s+content\s+to|"
                r"use\s+this\s+as\s+(?:the\s+)?content|"
                r"(?:content|text)\s*:|"
                r"with\s+(?:the\s+)?(?:content|text)\b|"
                r"as\s+content\s+add|"
                r"put\s+.+?\s+(?:inside|in|into)\s+(?:it|the\s+file)\b"
                r")",
                message,
                re.IGNORECASE,
            )
        )
        if isinstance(builder_payload, dict):
            builder_wf = builder_payload.get("write_file")
            builder_has_explicit_content = isinstance(builder_wf, dict) and bool(
                str(builder_wf.get("content") or "").strip()
            )
        if target_draft is None:
            raw_draft = classify_code_draft_intent(message)
            if raw_draft is not None:
                try:
                    target_draft = normalize_preview_payload(raw_draft)
                except Exception:
                    target_draft = None
        # Fall back to the existing pending preview for refinement turns
        # where the classifier doesn't match but a code preview already exists.
        if target_draft is None and isinstance(pending_preview, dict):
            target_draft = dict(pending_preview)
        if builder_has_explicit_content and explicit_literal_content_refinement:
            # Respect explicit structured content/refinement updates that the
            # deterministic preview builder already resolved from the user
            # message. Only suppress fenced-code extraction when the current
            # turn itself already produced authoritative structured content.
            reply_code_content = None
        if isinstance(target_draft, dict) and reply_code_content is not None:
            wf = target_draft.get("write_file")
            if isinstance(wf, dict):
                updated_draft: dict[str, object] = {
                    **target_draft,
                    "write_file": {**wf, "content": reply_code_content},
                }
                builder_payload = updated_draft
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

    is_existing_writing_preview = _is_governed_writing_preview(pending_preview)
    if (
        not is_writing_draft_turn
        and not informational_web_turn
        and not is_enrichment_turn
        and is_existing_writing_preview
        and is_writing_refinement_request(message)
        and reply_text_draft is not None
    ):
        is_writing_draft_turn = True

    if (
        is_writing_draft_turn
        and reply_text_draft is not None
        and not reply_status.startswith("degraded")
    ):
        prose_target_draft: dict[str, object] | None = builder_payload
        if prose_target_draft is None:
            raw_draft = classify_writing_draft_intent(message)
            if raw_draft is not None:
                try:
                    prose_target_draft = normalize_preview_payload(raw_draft)
                except Exception:
                    prose_target_draft = None
        if (
            prose_target_draft is None
            and isinstance(pending_preview, dict)
            and _is_governed_writing_preview(pending_preview)
        ):
            prose_target_draft = dict(pending_preview)
        if isinstance(prose_target_draft, dict):
            wf = prose_target_draft.get("write_file")
            if isinstance(wf, dict):
                updated_prose_draft: dict[str, object] = {
                    **prose_target_draft,
                    "write_file": {**wf, "content": reply_text_draft},
                }
                builder_payload = updated_prose_draft
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

    guarded_answer = _guardrail_submission_claim(
        root=root,
        session_id=active_session,
        text=sanitized_answer,
    )
    # Gate preview-existence claims on actual preview state.
    # An empty-content write_file preview is a placeholder, not authoritative
    # code — treat it as "no real preview" for claim-checking purposes.
    effective_preview = read_session_preview(root, active_session)
    _preview_has_content = False
    if isinstance(effective_preview, dict):
        _epwf = effective_preview.get("write_file")
        if isinstance(_epwf, dict):
            preview_path = str(_epwf.get("path") or "").strip()
            preview_content = str(_epwf.get("content") or "").strip()
            _preview_has_content = bool(preview_content) or (
                bool(preview_path) and not has_code_file_extension(preview_path)
            )
        else:
            _preview_has_content = "write_file" not in effective_preview
    _answer_before_preview_guardrail = guarded_answer
    guarded_answer = _guardrail_false_preview_claim(
        text=guarded_answer,
        preview_exists=effective_preview is not None and _preview_has_content,
    )
    # All-or-nothing cleanup: when _guardrail_false_preview_claim stripped a false
    # preview-existence claim, it means the LLM claimed a preview was ready but no
    # authoritative code content was actually committed.  Clear any empty write_file
    # placeholder so the session is in a clean state — no orphaned shell, no
    # accidental empty submission.  Only clears empty-content previews; any
    # preview that already had content was not touched by the guardrail above.
    if guarded_answer != _answer_before_preview_guardrail and isinstance(effective_preview, dict):
        _stale_wf = effective_preview.get("write_file")
        if isinstance(_stale_wf, dict) and not str(_stale_wf.get("content") or "").strip():
            write_session_preview(root, active_session, None)
            builder_payload = None
    in_voxera_preview_flow = pending_preview is not None or builder_preview is not None
    is_json_content_request = _is_explicit_json_content_request(message)
    is_voxera_control_turn = _is_voxera_control_turn(message, active_preview=pending_preview)
    should_hide_voxera_preview_dump = (
        in_voxera_preview_flow or is_voxera_control_turn
    ) and not is_json_content_request

    assistant_text = guarded_answer
    # Code draft replies must NOT be suppressed — they contain the actual code
    # that the user needs to see in a proper fenced block.  All other preview
    # control-turn suppression logic still applies.
    should_use_conversational_control_reply = (
        not is_enrichment_turn
        and not is_code_draft_turn
        and not is_writing_draft_turn
        and (
            (is_voxera_control_turn and not is_json_content_request)
            or (should_hide_voxera_preview_dump and _looks_like_voxera_preview_dump(guarded_answer))
            or (_looks_like_preview_update_claim(guarded_answer) and not is_json_content_request)
        )
    )
    if should_use_conversational_control_reply or not assistant_text.strip():
        assistant_text = _conversational_preview_update_message(
            updated=builder_payload is not None,
            has_active_preview=pending_preview is not None,
            user_message=message,
        )

    status = "prepared_preview" if builder_payload is not None else reply_status

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
    updated_at_ms = read_session_updated_at_ms(root, active_session)
    raw_since_updated = request.query_params.get("since_updated_at_ms")
    has_since_updated = raw_since_updated is not None
    try:
        since_updated_at_ms = int(str(raw_since_updated or "0"))
    except ValueError:
        since_updated_at_ms = 0
    since_updated_at_ms = max(0, since_updated_at_ms)

    changed = turn_count > since_count
    if has_since_updated:
        changed = changed or updated_at_ms > since_updated_at_ms

    payload: dict[str, object] = {
        "session_id": active_session,
        "turn_count": turn_count,
        "updated_at_ms": updated_at_ms,
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
