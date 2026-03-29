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
from ..vera.draft_revision import (
    _detect_content_type_from_preview,
    _generate_refreshed_content,
    _is_clear_content_refresh_request,
    filename_from_preview,
    looks_like_preview_rename_or_save_as_request,
)
from ..vera.draft_revision import (
    _is_ambiguous_change_request as _is_ambiguous_change_request,
)
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
    is_investigation_compare_request,
    is_investigation_derived_followup_save_request,
    is_investigation_derived_save_request,
    is_investigation_expand_request,
    is_investigation_save_request,
    is_investigation_summary_request,
    is_recent_assistant_content_save_request,
    maybe_draft_job_payload,
    select_investigation_results,
)
from ..vera.preview_submission import (
    is_natural_preview_submission_confirmation,
    is_near_miss_submit_phrase,
    is_preview_submission_request,
    normalize_preview_payload,
    should_submit_active_preview,
    submit_active_preview_for_session,
    submit_preview,
)
from ..vera.prompt import VERA_SYSTEM_PROMPT, vera_queue_boundary_summary
from ..vera.saveable_artifacts import (
    looks_like_non_authored_assistant_message,
    message_requests_referenced_content,
)
from ..vera.service import (
    _CODE_DRAFT_HINT,
    _WRITING_DRAFT_HINT,
    _is_informational_web_query,
    _weather_context_has_pending_lookup,
    append_session_turn,
    clear_session_turns,
    generate_preview_builder_update,
    generate_vera_reply,
    ingest_linked_job_completions,
    maybe_auto_surface_linked_completion,
    new_session_id,
    read_session_conversational_planning_active,
    read_session_derived_investigation_output,
    read_session_enrichment,
    read_session_handoff_state,
    read_session_investigation,
    read_session_last_user_input_origin,
    read_session_preview,
    read_session_saveable_assistant_artifacts,
    read_session_turns,
    read_session_updated_at_ms,
    read_session_weather_context,
    register_session_linked_job,
    run_web_enrichment,
    session_debug_info,
    write_session_conversational_planning_active,
    write_session_derived_investigation_output,
    write_session_enrichment,
    write_session_handoff_state,
    write_session_investigation,
    write_session_preview,
    write_session_weather_context,
)
from ..voice.flags import VoiceFoundationFlags, load_voice_foundation_flags
from ..voice.input import VoiceInputDisabledError, ingest_voice_transcript
from ..voice.models import InputOrigin, normalize_input_origin
from ..voice.output import voice_output_status
from .conversational_checklist import (
    conversational_preview_update_message as _cc_conversational_preview_update_message,
)
from .conversational_checklist import (
    enforce_conversational_checklist_output as _cc_enforce_conversational_checklist_output,
)
from .conversational_checklist import (
    has_conversational_planning_signal as _cc_has_conversational_planning_signal,
)
from .conversational_checklist import (
    has_save_write_file_signal as _cc_has_save_write_file_signal,
)
from .conversational_checklist import (
    is_conversational_answer_first_request as _cc_is_conversational_answer_first_request,
)
from .conversational_checklist import (
    looks_like_preview_pane_claim as _cc_looks_like_preview_pane_claim,
)
from .conversational_checklist import (
    looks_like_preview_update_claim as _cc_looks_like_preview_update_claim,
)
from .conversational_checklist import (
    looks_like_voxera_preview_dump as _cc_looks_like_voxera_preview_dump,
)
from .conversational_checklist import (
    sanitize_false_preview_claims_from_answer as _cc_sanitize_false_preview_claims_from_answer,
)
from .conversational_checklist import (
    should_use_conversational_artifact_mode as _cc_should_use_conversational_artifact_mode,
)
from .execution_mode import (
    ExecutionMode,
)
from .execution_mode import (
    _classify_execution_mode as _em_classify_execution_mode,
)
from .execution_mode import (
    _extract_save_as_text_target as _em_extract_save_as_text_target,
)
from .execution_mode import (
    _is_explicit_json_content_request as _em_is_explicit_json_content_request,
)
from .execution_mode import (
    _is_governed_writing_preview as _em_is_governed_writing_preview,
)
from .execution_mode import (
    _is_refinable_prose_preview as _em_is_refinable_prose_preview,
)
from .execution_mode import (
    _is_relative_writing_refinement_request as _em_is_relative_writing_refinement_request,
)
from .execution_mode import (
    _is_voxera_control_turn as _em_is_voxera_control_turn,
)
from .execution_mode import (
    _looks_like_active_preview_content_generation_turn as _em_looks_like_active_preview_content_generation_turn,
)
from .execution_mode import (
    _looks_like_ambiguous_active_preview_content_replacement_request as _em_looks_like_ambiguous_active_preview_content_replacement_request,
)
from .execution_mode import (
    _message_has_explicit_content_literal as _em_message_has_explicit_content_literal,
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


def _is_active_preview_submit_intent(message: str, *, preview_available: bool) -> bool:
    return should_submit_active_preview(message, preview_available=preview_available)


def _submit_handoff(
    *,
    root: Path,
    session_id: str,
    preview: dict[str, object] | None,
) -> tuple[str, str]:
    return submit_active_preview_for_session(
        queue_root=root,
        session_id=session_id,
        preview=preview,
        register_linked_job=lambda queue_root, sid, job_ref: register_session_linked_job(
            queue_root, sid, job_ref=job_ref
        ),
        submit_preview_hook=submit_preview,
    )


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


def _is_voxera_control_turn(message: str, *, active_preview: dict[str, object] | None) -> bool:
    return _em_is_voxera_control_turn(
        message,
        active_preview=active_preview,
        is_text_draft_preview=is_text_draft_preview,
        is_recent_assistant_content_save_request=is_recent_assistant_content_save_request,
        is_natural_preview_submission_confirmation=is_natural_preview_submission_confirmation,
        is_preview_submission_request=is_preview_submission_request,
        maybe_draft_job_payload=lambda content, preview: maybe_draft_job_payload(
            content,
            active_preview=preview,
        ),
    )


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
    return _cc_looks_like_voxera_preview_dump(text)


def _looks_like_preview_update_claim(text: str) -> bool:
    return _cc_looks_like_preview_update_claim(text)


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


def _is_governed_writing_preview(preview: dict[str, object] | None) -> bool:
    return _em_is_governed_writing_preview(preview, is_text_draft_preview=is_text_draft_preview)


def _is_refinable_prose_preview(preview: dict[str, object] | None) -> bool:
    return _em_is_refinable_prose_preview(preview, is_text_draft_preview=is_text_draft_preview)


def _is_relative_writing_refinement_request(message: str) -> bool:
    return _em_is_relative_writing_refinement_request(message)


def _looks_like_active_preview_content_generation_turn(message: str) -> bool:
    return _em_looks_like_active_preview_content_generation_turn(
        message,
        looks_like_preview_rename_or_save_as_request=looks_like_preview_rename_or_save_as_request,
        message_requests_referenced_content=message_requests_referenced_content,
    )


def _message_has_explicit_content_literal(message: str) -> bool:
    return _em_message_has_explicit_content_literal(message)


def _looks_like_ambiguous_active_preview_content_replacement_request(message: str) -> bool:
    return _em_looks_like_ambiguous_active_preview_content_replacement_request(message)


def _looks_like_builder_refinement_placeholder(content: str) -> bool:
    lowered = content.strip().lower()
    if not lowered:
        return False
    placeholder_values = {
        "formal rewrite requested for the existing file content.",
        "summary of today's top news headlines.",
        "short summary of today's top news headlines.",
        "top stories:\n- headline 1\n- headline 2\n- headline 3",
    }
    return lowered in placeholder_values


def _preview_body_looks_like_control_narration(preview: dict[str, object] | None) -> bool:
    if not isinstance(preview, dict):
        return False
    write_file = preview.get("write_file")
    if not isinstance(write_file, dict):
        return False
    content = str(write_file.get("content") or "").strip()
    if not content:
        return False
    return looks_like_non_authored_assistant_message(content)


def _is_targeted_code_preview_refinement(
    message: str, *, active_preview: dict[str, object] | None
) -> bool:
    if not isinstance(active_preview, dict):
        return False
    filename = filename_from_preview(active_preview)
    if not filename:
        return False
    write_file = active_preview.get("write_file")
    path = str(write_file.get("path") or "").strip() if isinstance(write_file, dict) else ""
    if not path or not has_code_file_extension(path) or path.lower().endswith(".md"):
        return False
    return bool(
        re.search(r"\badd\s+content\s+to\b", message, re.IGNORECASE)
        and re.search(rf"\b{re.escape(filename)}\b", message, re.IGNORECASE)
    )


def _extract_save_as_text_target(message: str) -> str | None:
    return _em_extract_save_as_text_target(message)


def _guardrail_false_preview_claim(*, text: str, preview_exists: bool) -> str:
    """Replace false preview-existence claims with truthful language.

    When the LLM claims a preview/draft was created or is available but no
    authoritative preview state exists, replace the claim.  Fenced code
    blocks are preserved so users can still see generated code.
    """
    if preview_exists:
        return text
    if not _cc_looks_like_preview_pane_claim(text):
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


def _sanitize_false_preview_claims_from_answer(text: str) -> str:
    return _cc_sanitize_false_preview_claims_from_answer(text)


def _enforce_conversational_checklist_output(
    text: str, *, raw_answer: str, user_message: str
) -> str:
    return _cc_enforce_conversational_checklist_output(
        text, raw_answer=raw_answer, user_message=user_message
    )


def _is_conversational_answer_first_request(message: str) -> bool:
    return _cc_is_conversational_answer_first_request(message)


def _classify_execution_mode(
    message: str,
    *,
    prior_planning_active: bool,
    pending_preview: dict[str, object] | None,
) -> ExecutionMode:
    return _em_classify_execution_mode(
        message,
        prior_planning_active=prior_planning_active,
        pending_preview=pending_preview,
        should_use_conversational_artifact_mode=_cc_should_use_conversational_artifact_mode,
        is_recent_assistant_content_save_request=is_recent_assistant_content_save_request(message),
    )


def _is_explicit_json_content_request(message: str) -> bool:
    return _em_is_explicit_json_content_request(message)


def _conversational_preview_update_message(
    *,
    updated: bool,
    has_active_preview: bool,
    user_message: str,
    rejected: bool = False,
    updated_preview: dict[str, object] | None = None,
) -> str:
    return _cc_conversational_preview_update_message(
        updated=updated,
        has_active_preview=has_active_preview,
        user_message=user_message,
        is_recent_assistant_content_save_request=is_recent_assistant_content_save_request(
            user_message
        ),
        rejected=rejected,
        updated_preview=updated_preview,
    )


async def _generate_vera_reply_with_optional_draft_hints(
    *,
    turns: list[dict[str, str]],
    user_message: str,
    code_draft: bool,
    writing_draft: bool,
    weather_context: dict[str, object] | None = None,
) -> dict[str, object]:
    signature = inspect.signature(generate_vera_reply)
    parameters = signature.parameters
    if (
        "code_draft" in parameters
        or "writing_draft" in parameters
        or "weather_context" in parameters
    ):
        if "weather_context" in parameters:
            return await generate_vera_reply(
                turns=turns,
                user_message=user_message,
                code_draft=code_draft,
                writing_draft=writing_draft,
                weather_context=weather_context,
            )
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


async def _generate_preview_builder_update_with_optional_artifacts(
    *,
    turns: list[dict[str, str]],
    user_message: str,
    active_preview: dict[str, object] | None,
    enrichment_context: dict[str, object] | None = None,
    investigation_context: dict[str, object] | None = None,
    recent_assistant_artifacts: list[dict[str, str]] | None = None,
) -> dict[str, object] | None:
    signature = inspect.signature(generate_preview_builder_update)
    if "recent_assistant_artifacts" in signature.parameters:
        return await generate_preview_builder_update(
            turns=turns,
            user_message=user_message,
            active_preview=active_preview,
            enrichment_context=enrichment_context,
            investigation_context=investigation_context,
            recent_assistant_artifacts=recent_assistant_artifacts,
        )
    return await generate_preview_builder_update(
        turns=turns,
        user_message=user_message,
        active_preview=active_preview,
        enrichment_context=enrichment_context,
        investigation_context=investigation_context,
    )


def _render_page(
    *,
    session_id: str,
    turns: list[dict[str, str]],
    status: str,
    error: str = "",
    voice_flags: VoiceFoundationFlags | None = None,
) -> HTMLResponse:
    root = _active_queue_root()
    active_voice_flags = voice_flags
    if active_voice_flags is None:
        try:
            active_voice_flags = load_voice_foundation_flags()
        except Exception:
            active_voice_flags = VoiceFoundationFlags(
                enable_voice_foundation=False,
                enable_voice_input=False,
                enable_voice_output=False,
                voice_stt_backend=None,
                voice_tts_backend=None,
            )
    voice_runtime: dict[str, object] = {
        "voice_foundation_enabled": active_voice_flags.enable_voice_foundation,
        "voice_input_enabled": active_voice_flags.voice_input_enabled,
        "voice_output_enabled": active_voice_flags.voice_output_enabled,
        "voice_stt_backend": active_voice_flags.voice_stt_backend,
        "voice_tts_backend": active_voice_flags.voice_tts_backend,
    }
    try:
        for key, value in voice_output_status(active_voice_flags).items():
            voice_runtime[key] = value
    except Exception:
        voice_runtime["voice_output_attempted"] = False
        voice_runtime["voice_output_backend"] = None
        voice_runtime["voice_output_reason"] = "voice_runtime_unavailable"
    tmpl = templates.get_template("index.html")
    html = tmpl.render(
        session_id=session_id,
        turns=turns,
        mode_status=status,
        queue_boundary=vera_queue_boundary_summary(),
        error=error,
        debug_info=session_debug_info(root, session_id, mode_status=status),
        voice_runtime=voice_runtime,
        last_user_input_origin=read_session_last_user_input_origin(root, session_id),
        system_prompt=VERA_SYSTEM_PROMPT,
        pending_preview=read_session_preview(root, session_id),
        drafting_examples=drafting_guidance().examples,
        main_screen_guidance=_main_screen_guidance(),
    )
    response = HTMLResponse(content=html)
    response.set_cookie("vera_session_id", session_id, httponly=False, samesite="lax")
    return response


def _main_screen_guidance() -> dict[str, object]:
    return {
        "title": "How to use Vera",
        "summary": (
            "Ask naturally. Vera can answer questions, investigate the web, draft notes or files, "
            "and prepare governed previews for work you may want to save or submit."
        ),
        "preview_hint": (
            "When Vera prepares a preview, you can follow up with things like "
            "“save that to a note”, “save it as weather.md”, or “submit it”. "
            "Chat stays conversational; submit sends the prepared preview through VoxeraOS."
        ),
        "groups": [
            {
                "label": "Ask",
                "examples": [
                    "What is the capital of Alberta?",
                    "Explain photosynthesis simply.",
                ],
            },
            {
                "label": "Investigate",
                "examples": [
                    "Search the web for the latest Brave Search API documentation",
                    "Compare results 1 and 3",
                    "Expand result 1",
                ],
            },
            {
                "label": "Save",
                "examples": [
                    "Save that to a note",
                    "Save it as weather.md",
                    "Submit it",
                ],
            },
            {
                "label": "Write",
                "examples": [
                    "Write a 2 page essay about black holes",
                    "Rewrite that as a short formal article",
                ],
            },
            {
                "label": "Code",
                "examples": [
                    "Write me a python script that fetches a URL and prints the page title",
                    "Explain how this script works in plain English",
                ],
            },
            {
                "label": "System",
                "examples": [
                    "Inspect system health",
                    "Check status of voxera-vera.service",
                    "Show recent logs for voxera-daemon.service",
                ],
            },
        ],
    }


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
    input_origin_raw = str((parsed.get("input_origin") or ["typed"])[0])
    session_id = str((parsed.get("session_id") or [""])[0])
    voice_flags = load_voice_foundation_flags()
    input_origin = normalize_input_origin(input_origin_raw)

    if input_origin is InputOrigin.VOICE_TRANSCRIPT:
        try:
            ingested = ingest_voice_transcript(
                transcript_text=message,
                voice_input_enabled=voice_flags.voice_input_enabled,
            )
        except VoiceInputDisabledError as exc:
            root = _active_queue_root()
            return _render_page(
                session_id=session_id.strip() or new_session_id(),
                turns=read_session_turns(root, session_id.strip()),
                status="voice_input_disabled",
                error=str(exc),
                voice_flags=voice_flags,
            )
        except ValueError as exc:
            root = _active_queue_root()
            return _render_page(
                session_id=session_id.strip() or new_session_id(),
                turns=read_session_turns(root, session_id.strip()),
                status="voice_input_invalid",
                error=str(exc),
                voice_flags=voice_flags,
            )
        message = ingested.transcript_text
        input_origin = InputOrigin(ingested.input_origin)

    active_session = session_id.strip() or (request.cookies.get("vera_session_id") or "").strip()
    active_session = active_session or new_session_id()

    if not message.strip():
        root = _active_queue_root()
        return _render_page(
            session_id=active_session,
            turns=read_session_turns(root, active_session),
            status="conversation",
            error="Message is required.",
            voice_flags=voice_flags,
        )

    root = _active_queue_root()
    pre_turn_preview = read_session_preview(root, active_session)
    suppress_auto_completion_note = should_submit_active_preview(
        message,
        preview_available=pre_turn_preview is not None,
    )
    ingest_linked_job_completions(root, active_session)
    auto_completion_note = (
        None
        if suppress_auto_completion_note
        else maybe_auto_surface_linked_completion(root, active_session)
    )

    append_session_turn(
        root,
        active_session,
        role="user",
        text=message,
        input_origin=input_origin.value,
    )
    if auto_completion_note is not None:
        append_session_turn(root, active_session, role="assistant", text=auto_completion_note)
    turns = read_session_turns(root, active_session)

    pending_preview = pre_turn_preview
    requested_job_id = maybe_extract_job_id(message)
    diagnostics_service_turn = diagnostics_service_or_logs_intent(message)

    diagnostics_refusal = diagnostics_request_refusal(message)
    if diagnostics_refusal is not None:
        append_session_turn(root, active_session, role="assistant", text=diagnostics_refusal)
        return _render_page(
            session_id=active_session,
            turns=read_session_turns(root, active_session),
            status="blocked_diagnostics",
            voice_flags=voice_flags,
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
            voice_flags=voice_flags,
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
                voice_flags=voice_flags,
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
            voice_flags=voice_flags,
        )

    session_investigation = read_session_investigation(root, active_session)
    session_derived_output = read_session_derived_investigation_output(root, active_session)
    session_weather_context = read_session_weather_context(root, active_session)
    is_explicit_writing_transform = is_writing_draft_request(message)

    if (
        is_natural_preview_submission_confirmation(message)
        and pending_preview is None
        and _weather_context_has_pending_lookup(session_weather_context)
    ):
        reply = await _generate_vera_reply_with_optional_draft_hints(
            turns=turns,
            user_message=message,
            code_draft=False,
            writing_draft=False,
            weather_context=session_weather_context,
        )
        reply_answer = str(reply.get("answer") or "")
        weather_payload = reply.get("weather_context") if isinstance(reply, dict) else None
        if isinstance(weather_payload, dict):
            write_session_weather_context(root, active_session, weather_payload)
        append_session_turn(root, active_session, role="assistant", text=reply_answer)
        return _render_page(
            session_id=active_session,
            turns=read_session_turns(root, active_session),
            status=str(reply.get("status") or "ok:weather_current"),
            voice_flags=voice_flags,
        )

    should_attempt_derived_save = (
        not is_explicit_writing_transform
        and not _looks_like_active_preview_content_generation_turn(message)
        and (
            is_investigation_derived_save_request(message)
            or _prefer_derived_followup_save(
                message=message,
                session_derived_output=session_derived_output,
                turns=turns,
            )
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
                voice_flags=voice_flags,
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
            voice_flags=voice_flags,
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
                voice_flags=voice_flags,
            )
        write_session_derived_investigation_output(root, active_session, comparison)
        append_session_turn(
            root, active_session, role="assistant", text=str(comparison.get("answer") or "")
        )
        return _render_page(
            session_id=active_session,
            turns=read_session_turns(root, active_session),
            status="ok:investigation_comparison",
            voice_flags=voice_flags,
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
                voice_flags=voice_flags,
            )
        write_session_derived_investigation_output(root, active_session, summary)
        append_session_turn(
            root, active_session, role="assistant", text=str(summary.get("answer") or "")
        )
        return _render_page(
            session_id=active_session,
            turns=read_session_turns(root, active_session),
            status="ok:investigation_summary",
            voice_flags=voice_flags,
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

    # Near-miss / typo-like submit phrasing: fail closed explicitly.
    # This intercept runs BEFORE the canonical submit path so a fuzzy
    # near-submit never reaches the LLM, which might overclaim submission.
    if is_near_miss_submit_phrase(message):
        near_miss_text = (
            "I did not submit the preview. "
            "That looked like a submit command but didn't match the expected phrasing. "
            'Try "send it", "submit it", or "hand it off" to submit.'
        )
        append_session_turn(root, active_session, role="assistant", text=near_miss_text)
        return _render_page(
            session_id=active_session,
            turns=read_session_turns(root, active_session),
            status="near_miss_submit_rejected",
            voice_flags=voice_flags,
        )

    if (
        (is_preview_submission_request(message))
        and not is_recent_assistant_content_save_request(message)
        and pending_preview is None
    ):
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
    # ── Execution mode classification (decided EARLY, enforced GLOBALLY) ──
    # CONVERSATIONAL_ARTIFACT: checklist/planning/structured reasoning with no
    #     save/write/file intent → answered in chat, no preview builder, no
    #     preview language allowed.
    # GOVERNED_PREVIEW: everything else → normal preview/builder flow.
    #
    # Multi-turn continuation: if the previous turn was CONVERSATIONAL_ARTIFACT
    # (e.g. Vera asked for details), the follow-up stays conversational unless
    # explicit save intent appears or a preview is already active.
    _prior_planning_active = read_session_conversational_planning_active(root, active_session)
    execution_mode = _classify_execution_mode(
        message,
        prior_planning_active=_prior_planning_active,
        pending_preview=pending_preview,
    )
    conversational_answer_first_turn = execution_mode is ExecutionMode.CONVERSATIONAL_ARTIFACT
    # Pre-compute code-draft intent so the LLM call can be given the code-generation
    # hint before the reply is generated.  This flag is reused below where
    # is_code_draft_turn would have been computed from the same expression.
    explicit_targeted_content_refinement = _is_targeted_code_preview_refinement(
        message,
        active_preview=pending_preview,
    )
    is_code_draft_turn = (
        (is_code_draft_request(message) or explicit_targeted_content_refinement)
        and not informational_web_turn
        and not is_explicit_writing_transform
    )
    active_preview_is_refinable_prose = _is_refinable_prose_preview(pending_preview)
    active_preview_blocks_relative_prose_refinement = (
        isinstance(pending_preview, dict)
        and not active_preview_is_refinable_prose
        and _is_relative_writing_refinement_request(message)
    )
    is_writing_draft_turn = (
        not is_code_draft_turn
        and not informational_web_turn
        and is_explicit_writing_transform
        and not active_preview_blocks_relative_prose_refinement
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
    recent_assistant_artifacts = read_session_saveable_assistant_artifacts(root, active_session)

    # Persist the planning continuation flag so follow-up turns stay in the
    # answer-first lane.  Clear it when the turn is NOT answer-first.
    write_session_conversational_planning_active(
        root, active_session, conversational_answer_first_turn
    )

    builder_preview: dict[str, object] | None = None
    if not informational_web_turn and not conversational_answer_first_turn:
        builder_preview = await _generate_preview_builder_update_with_optional_artifacts(
            turns=turns,
            user_message=message,
            active_preview=pending_preview,
            enrichment_context=enrichment_context,
            investigation_context=session_investigation,
            recent_assistant_artifacts=recent_assistant_artifacts,
        )
    builder_payload: dict[str, object] | None = None
    preview_update_rejected = False
    if builder_preview is not None:
        try:
            builder_payload = normalize_preview_payload(builder_preview)
        except ValueError as exc:
            builder_payload = None
            if "must be within" in str(exc):
                preview_update_rejected = True
        except Exception:
            builder_payload = None
        # Detect no-op: when the builder returned the unchanged active
        # preview (e.g. after an LLM candidate was rejected internally),
        # treat it as "not updated" so the response does not claim success.
        if (
            builder_payload is not None
            and pending_preview is not None
            and builder_payload == pending_preview
        ):
            builder_payload = None
        # Rename-mutation fallback: when the user clearly asked for a rename
        # but the builder returned the unchanged preview (hidden compiler
        # override), re-run the deterministic path which handles rename/save-as
        # mutations reliably.  Without this, the rename is silently lost.
        if (
            builder_payload is None
            and isinstance(pending_preview, dict)
            and looks_like_preview_rename_or_save_as_request(message)
        ):
            _rename_user_messages = [
                str(turn.get("text") or "")
                for turn in turns[-8:]
                if str(turn.get("role") or "").strip().lower() == "user"
            ]
            _rename_fallback = maybe_draft_job_payload(
                message,
                active_preview=pending_preview,
                recent_user_messages=_rename_user_messages,
                enrichment_context=enrichment_context,
                investigation_context=session_investigation,
                recent_assistant_artifacts=recent_assistant_artifacts,
            )
            if isinstance(_rename_fallback, dict):
                try:
                    _rename_payload = normalize_preview_payload(_rename_fallback)
                except Exception:
                    _rename_payload = None
                if _rename_payload is not None and _rename_payload != pending_preview:
                    builder_payload = _rename_payload
        if _preview_body_looks_like_control_narration(builder_payload):
            recent_user_messages = [
                str(turn.get("text") or "")
                for turn in turns[-8:]
                if str(turn.get("role") or "").strip().lower() == "user"
            ]
            deterministic_fallback = maybe_draft_job_payload(
                message,
                active_preview=pending_preview,
                recent_user_messages=recent_user_messages,
                enrichment_context=enrichment_context,
                investigation_context=session_investigation,
                recent_assistant_artifacts=recent_assistant_artifacts,
            )
            if isinstance(deterministic_fallback, dict):
                try:
                    fallback_payload = normalize_preview_payload(deterministic_fallback)
                except Exception:
                    fallback_payload = None
                if not _preview_body_looks_like_control_narration(fallback_payload):
                    builder_payload = fallback_payload
                else:
                    builder_payload = None
            else:
                builder_payload = None
        if builder_payload is not None and isinstance(pending_preview, dict):
            pending_write_file = pending_preview.get("write_file")
            pending_path = (
                str(pending_write_file.get("path") or "").strip()
                if isinstance(pending_write_file, dict)
                else ""
            )
            builder_write_file = builder_payload.get("write_file")
            builder_content = (
                str(builder_write_file.get("content") or "").strip()
                if isinstance(builder_write_file, dict)
                else ""
            )
            if (
                pending_path
                and has_code_file_extension(pending_path)
                and not pending_path.lower().endswith(".md")
                and is_writing_refinement_request(message)
                and _looks_like_builder_refinement_placeholder(builder_content)
            ):
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
        weather_context=session_weather_context,
    )
    reply_answer = str(reply.get("answer") or "")
    reply_status = str(reply.get("status") or "")
    investigation_payload = reply.get("investigation") if isinstance(reply, dict) else None
    weather_payload = reply.get("weather_context") if isinstance(reply, dict) else None
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
    if isinstance(weather_payload, dict):
        write_session_weather_context(root, active_session, weather_payload)
    elif not reply_status.startswith("ok:weather") and session_weather_context is not None:
        write_session_weather_context(
            root, active_session, {**session_weather_context, "followup_active": False}
        )

    # Code draft post-processing: after the LLM reply we know the actual code
    # content.  Extract it from any fenced block and inject it into the preview
    # so the preview is authoritative and submit-ready — not just a placeholder.
    # is_code_draft_turn was pre-computed above (before the LLM call) so the
    # code-draft hint could be passed to generate_vera_reply.
    reply_code_content = extract_code_from_reply(reply_answer)
    sanitized_answer = _strip_internal_control_blocks(reply_answer)
    reply_text_draft_candidate = extract_text_draft_from_reply(sanitized_answer)
    reply_text_draft = (
        None
        if looks_like_non_authored_assistant_message(str(reply_text_draft_candidate or ""))
        else reply_text_draft_candidate
    )
    if reply_text_draft is None and _looks_like_active_preview_content_generation_turn(message):
        first_block = next(
            (block.strip() for block in re.split(r"\n{2,}", sanitized_answer) if block.strip()),
            "",
        )
        if (
            first_block
            and len(first_block.split()) >= 4
            and not looks_like_non_authored_assistant_message(first_block)
            and not _looks_like_preview_update_claim(first_block)
            and not re.search(r"\bprepared\s+(?:a|the)\s+preview\b", first_block, re.IGNORECASE)
        ):
            reply_text_draft = first_block
    generation_content_refresh_failed_closed = False

    # Code draft refinement: when an active preview has a code-type file
    # extension and the LLM reply contains a fenced code block, treat this
    # turn as a code draft update so the reply is shown (not suppressed) and
    # the preview content is refreshed with the updated code.
    if (
        not is_code_draft_turn
        and not informational_web_turn
        and not is_enrichment_turn
        and not explicit_targeted_content_refinement
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
        if (
            builder_has_explicit_content
            and explicit_literal_content_refinement
            and not isinstance(pending_preview, dict)
        ):
            # When no active code preview exists yet, keep an explicit
            # structured content payload from the deterministic builder rather
            # than replacing it with a speculative fenced-code extraction.
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

    is_existing_refinable_prose_preview = active_preview_is_refinable_prose
    is_existing_writing_preview = _is_governed_writing_preview(pending_preview)
    if (
        not is_writing_draft_turn
        and not informational_web_turn
        and not is_enrichment_turn
        and not explicit_targeted_content_refinement
        and is_existing_refinable_prose_preview
        and is_writing_refinement_request(message)
        and reply_text_draft is not None
    ):
        is_writing_draft_turn = True

    pending_preview_write_file = (
        pending_preview.get("write_file") if isinstance(pending_preview, dict) else None
    )
    pending_preview_path = (
        str(pending_preview_write_file.get("path") or "").strip()
        if isinstance(pending_preview_write_file, dict)
        else ""
    )
    pending_preview_content = (
        str(pending_preview_write_file.get("content") or "").strip()
        if isinstance(pending_preview_write_file, dict)
        else ""
    )
    active_preview_is_code = (
        bool(pending_preview_path)
        and has_code_file_extension(pending_preview_path)
        and not pending_preview_path.lower().endswith(".md")
    )
    builder_is_governed_writing_preview = _is_governed_writing_preview(builder_payload)
    builder_preview_write_file = (
        builder_payload.get("write_file") if isinstance(builder_payload, dict) else None
    )
    builder_preview_path = (
        str(builder_preview_write_file.get("path") or "").strip()
        if isinstance(builder_preview_write_file, dict)
        else ""
    )
    builder_preview_is_code = (
        bool(builder_preview_path)
        and has_code_file_extension(builder_preview_path)
        and not builder_preview_path.lower().endswith(".md")
    )

    should_preserve_builder_refinement_content = (
        active_preview_is_code
        and not builder_is_governed_writing_preview
        and (not builder_preview_path or builder_preview_is_code)
    )
    if (
        not should_preserve_builder_refinement_content
        and isinstance(builder_payload, dict)
        and is_writing_refinement_request(message)
    ):
        builder_wf = builder_payload.get("write_file")
        builder_content = (
            str(builder_wf.get("content") or "").strip() if isinstance(builder_wf, dict) else ""
        )
        reply_text_draft_content = str(reply_text_draft or "").strip()
        should_preserve_builder_refinement_content = (
            not is_existing_writing_preview
            and bool(builder_content)
            and builder_content != pending_preview_content
            and (not reply_text_draft_content or builder_content == reply_text_draft_content)
            and not _looks_like_builder_refinement_placeholder(builder_content)
        )

    if (
        is_writing_draft_turn
        and reply_text_draft is not None
        and not reply_status.startswith("degraded")
        and not should_preserve_builder_refinement_content
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
            and _is_refinable_prose_preview(pending_preview)
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

    # Single-turn generate+save guardrail: when the deterministic builder staged
    # a text preview shell with empty content (for example "tell me a joke and
    # save it as ..."), bind same-turn authored reply text directly so preview
    # content never stays empty for a clear generation intent.
    if (
        isinstance(builder_payload, dict)
        and _is_refinable_prose_preview(builder_payload)
        and not is_code_draft_turn
        and not is_writing_draft_turn
        and not informational_web_turn
        and not is_enrichment_turn
        and _looks_like_active_preview_content_generation_turn(message)
        and reply_text_draft is not None
        and not str(reply_status).strip().lower().startswith("degraded")
    ):
        _shell_wf = builder_payload.get("write_file")
        _shell_content = (
            str(_shell_wf.get("content") or "").strip() if isinstance(_shell_wf, dict) else ""
        )
        if isinstance(_shell_wf, dict) and not _shell_content:
            shell_bound_preview: dict[str, object] = {
                **builder_payload,
                "write_file": {**_shell_wf, "content": reply_text_draft},
            }
            builder_payload = shell_bound_preview
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

    generation_binding_intent = (
        not is_code_draft_turn
        and not is_writing_draft_turn
        and not informational_web_turn
        and not is_enrichment_turn
        and _looks_like_active_preview_content_generation_turn(message)
        and not _message_has_explicit_content_literal(message)
        and not str(reply_status).strip().lower().startswith("degraded")
    )
    if generation_binding_intent and (
        _is_refinable_prose_preview(builder_payload) or _is_refinable_prose_preview(pending_preview)
    ):
        if reply_text_draft is not None:
            target_preview = (
                builder_payload
                if _is_refinable_prose_preview(builder_payload)
                else pending_preview
                if _is_refinable_prose_preview(pending_preview)
                else None
            )
            if isinstance(target_preview, dict):
                target_wf = target_preview.get("write_file")
                if isinstance(target_wf, dict):
                    save_as_target = _extract_save_as_text_target(message)
                    rewritten_path = str(target_wf.get("path") or "").strip()
                    if save_as_target:
                        rewritten_path = f"~/VoxeraOS/notes/{save_as_target}"
                    updated_preview: dict[str, object] = {
                        **target_preview,
                        "write_file": {
                            **target_wf,
                            "path": rewritten_path or target_wf.get("path"),
                            "content": reply_text_draft,
                        },
                    }
                    builder_payload = updated_preview
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
        else:
            # Deterministic active-draft content refresh fallback: when the LLM
            # did not produce usable text but the user clearly asked for a content
            # refresh (e.g. "generate a different poem"), generate replacement
            # content deterministically from a content-type pool.
            _refresh_target = (
                builder_payload
                if _is_refinable_prose_preview(builder_payload)
                else pending_preview
                if _is_refinable_prose_preview(pending_preview)
                else None
            )
            if isinstance(_refresh_target, dict) and _is_clear_content_refresh_request(
                message.strip().lower()
            ):
                _refresh_wf = _refresh_target.get("write_file")
                if isinstance(_refresh_wf, dict):
                    _refresh_path = str(_refresh_wf.get("path") or "").strip()
                    _existing = str(_refresh_wf.get("content") or "")
                    _ctype = _detect_content_type_from_preview(
                        _refresh_target, message.strip().lower()
                    )
                    _refreshed = _generate_refreshed_content(_ctype, _existing)
                    if _refreshed and _refreshed != _existing.strip():
                        _refreshed_preview: dict[str, object] = {
                            **_refresh_target,
                            "write_file": {
                                **_refresh_wf,
                                "content": _refreshed,
                            },
                        }
                        builder_payload = _refreshed_preview
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
                    else:
                        generation_content_refresh_failed_closed = True
                else:
                    generation_content_refresh_failed_closed = True
            else:
                generation_content_refresh_failed_closed = True

    # Create-and-save fallback: when the message has both explicit save/write
    # intent AND planning/checklist keywords (a "create and save" hybrid like
    # "save a checklist to a note for my wedding prep"), but the builder failed
    # to produce a content-bearing preview, create one from the LLM reply.
    _is_create_and_save = (
        not conversational_answer_first_turn
        and not is_writing_draft_turn
        and not is_code_draft_turn
        and builder_payload is None
        and pending_preview is None
        and _cc_has_save_write_file_signal(message)
        and _cc_has_conversational_planning_signal(message)
    )
    if _is_create_and_save and reply_text_draft:
        _note_suffix = active_session[-8:] if len(active_session) >= 8 else active_session
        _create_save_payload: dict[str, object] = {
            "goal": f"save checklist/plan to a note ({message[:60]})",
            "write_file": {
                "path": f"~/VoxeraOS/notes/note-{_note_suffix}.md",
                "content": reply_text_draft,
                "mode": "overwrite",
            },
        }
        try:
            builder_payload = normalize_preview_payload(_create_save_payload)
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
        except Exception:
            builder_payload = None

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

    if conversational_answer_first_turn:
        # HARD CONVERSATIONAL MODE LOCK (ExecutionMode.CONVERSATIONAL_ARTIFACT):
        # Six-phase sanitizer guarantees zero preview/draft/submit/queue/
        # workflow/JSON leakage.  Phases 3-6 are nuclear layers that strip
        # banned tokens, workflow narration, meta-commentary, and bare JSON
        # payloads — making behavior deterministic regardless of LLM output.
        guarded_answer = _sanitize_false_preview_claims_from_answer(sanitized_answer)
        # Final enforcement: deterministic safety net catches any edge cases
        # the sanitizer missed and re-renders as a plain checklist if needed.
        guarded_answer = _enforce_conversational_checklist_output(
            guarded_answer, raw_answer=sanitized_answer, user_message=message
        )
    else:
        guarded_answer = _guardrail_submission_claim(
            root=root,
            session_id=active_session,
            text=sanitized_answer,
        )
        _answer_before_preview_guardrail = guarded_answer
        guarded_answer = _guardrail_false_preview_claim(
            text=guarded_answer,
            preview_exists=effective_preview is not None and _preview_has_content,
        )
        # All-or-nothing cleanup: when _guardrail_false_preview_claim stripped a
        # false preview-existence claim, clear any empty write_file placeholder so
        # the session is clean — no orphaned shell, no accidental empty submission.
        if guarded_answer != _answer_before_preview_guardrail and isinstance(
            effective_preview, dict
        ):
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
    naming_mutation_request = looks_like_preview_rename_or_save_as_request(message)
    if naming_mutation_request and (pending_preview is not None or builder_payload is not None):
        assistant_text = _conversational_preview_update_message(
            updated=builder_payload is not None,
            has_active_preview=pending_preview is not None,
            user_message=message,
            rejected=preview_update_rejected,
            updated_preview=builder_payload,
        )
    if (
        explicit_targeted_content_refinement
        and builder_payload is not None
        and not is_code_draft_turn
        and not is_writing_draft_turn
    ):
        assistant_text = _conversational_preview_update_message(
            updated=True,
            has_active_preview=pending_preview is not None,
            user_message=message,
            updated_preview=builder_payload,
        )
    # Code draft replies must NOT be suppressed — they contain the actual code
    # that the user needs to see in a proper fenced block.  All other preview
    # control-turn suppression logic still applies.
    should_use_conversational_control_reply = (
        not is_enrichment_turn
        and not is_code_draft_turn
        and not is_writing_draft_turn
        and not conversational_answer_first_turn
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
            rejected=preview_update_rejected,
            updated_preview=builder_payload,
        )
    if (
        builder_payload is None
        and isinstance(pending_preview, dict)
        and _looks_like_ambiguous_active_preview_content_replacement_request(message)
    ):
        assistant_text = (
            f"{assistant_text}\n\n"
            "I left the active draft content unchanged because the content replacement request was "
            "ambiguous. Please specify exact content or say what prior artifact to use."
        ).strip()
    if (
        builder_payload is None
        and isinstance(pending_preview, dict)
        and _is_ambiguous_change_request(message.strip().lower())
    ):
        assistant_text = (
            "I left the active draft content unchanged because the request was ambiguous. "
            "To refresh content, try something specific like 'generate a different poem' "
            "or 'tell me a different joke'."
        )
    if generation_content_refresh_failed_closed:
        assistant_text = (
            f"{assistant_text}\n\n"
            "I left the active draft content unchanged because I could not use this turn as "
            "authoritative generated content. Please ask for explicit content again or provide "
            "the exact text to save."
        ).strip()

    status = "prepared_preview" if builder_payload is not None else reply_status

    append_session_turn(root, active_session, role="assistant", text=assistant_text)

    return _render_page(
        session_id=active_session,
        turns=read_session_turns(root, active_session),
        status=status,
        voice_flags=voice_flags,
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
        voice_flags=load_voice_foundation_flags(),
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
        voice_flags=load_voice_foundation_flags(),
    )
