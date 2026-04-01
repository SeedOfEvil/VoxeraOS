from __future__ import annotations

import inspect
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import load_config as load_runtime_config
from ..core.code_draft_intent import (
    has_code_file_extension,
    is_code_draft_request,
)
from ..core.file_intent import detect_blocked_file_intent
from ..core.writing_draft_intent import (
    is_text_draft_preview,
    is_writing_draft_request,
    is_writing_refinement_request,
)
from ..paths import queue_root as default_queue_root
from ..vera.draft_revision import (
    looks_like_preview_rename_or_save_as_request,
)
from ..vera.evidence_review import (
    maybe_extract_job_id,
)
from ..vera.handoff import (
    derive_investigation_expansion,
    diagnostics_service_or_logs_intent,
    drafting_guidance,
    is_investigation_derived_followup_save_request,
    is_investigation_derived_save_request,
    is_investigation_expand_request,
    is_recent_assistant_content_save_request,
    maybe_draft_job_payload,
)
from ..vera.preview_submission import (
    is_natural_preview_submission_confirmation,
    is_preview_submission_request,
    normalize_preview_payload,
    should_submit_active_preview,
    submit_active_preview_for_session,
    submit_preview,
)
from ..vera.prompt import VERA_SYSTEM_PROMPT, vera_queue_boundary_summary
from ..vera.saveable_artifacts import (
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
from .chat_early_exit_dispatch import dispatch_early_exit_intent
from .conversational_checklist import (
    conversational_preview_update_message as _cc_conversational_preview_update_message,
)
from .conversational_checklist import (
    enforce_conversational_checklist_output as _cc_enforce_conversational_checklist_output,
)
from .conversational_checklist import (
    is_conversational_answer_first_request as _cc_is_conversational_answer_first_request,
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
from .draft_content_binding import (
    extract_reply_drafts,
    resolve_draft_content_binding,
    strip_internal_control_blocks,
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
from .preview_content_binding import (
    is_targeted_code_preview_refinement,
    looks_like_builder_refinement_placeholder,
    preview_body_looks_like_control_narration,
)
from .response_shaping import (
    assemble_assistant_reply,
    derive_preview_has_content,
    guardrail_false_preview_claim,
    should_clear_stale_preview,
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
        maybe_draft_job_payload=lambda content, active_preview=None: maybe_draft_job_payload(
            content,
            active_preview=active_preview,
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
    return strip_internal_control_blocks(text)


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


def _extract_save_as_text_target(message: str) -> str | None:
    return _em_extract_save_as_text_target(message)


def _guardrail_false_preview_claim(*, text: str, preview_exists: bool) -> str:
    return guardrail_false_preview_claim(text, preview_exists=preview_exists)


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

    session_investigation = read_session_investigation(root, active_session)
    session_derived_output = read_session_derived_investigation_output(root, active_session)
    session_weather_context = read_session_weather_context(root, active_session)
    is_explicit_writing_transform = is_writing_draft_request(message)

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

    # ── Early-exit intent handler dispatch ─────────────────────────────────
    # Evaluates the coherent cluster of special-intent / short-circuit
    # conditions before the LLM path.  Derivation and result structuring live
    # in chat_early_exit_dispatch.py; session writes, turn append, and
    # _render_page remain here.
    # Intentionally NOT covered here: weather-context LLM lookup (async I/O,
    # below), submit/handoff truth paths, and blocked-file check (after
    # submit checks to preserve ordering).
    _early = dispatch_early_exit_intent(
        message=message,
        pending_preview=pending_preview,
        diagnostics_service_turn=diagnostics_service_turn,
        requested_job_id=requested_job_id,
        is_explicit_writing_transform=is_explicit_writing_transform,
        should_attempt_derived_save=should_attempt_derived_save,
        session_investigation=session_investigation,
        session_derived_output=session_derived_output,
        queue_root=root,
        session_id=active_session,
    )
    if _early.matched:
        if _early.write_preview:
            write_session_preview(root, active_session, _early.preview_payload)
        if _early.write_handoff_ready:
            write_session_handoff_state(
                root,
                active_session,
                attempted=False,
                queue_path=str(root),
                status="preview_ready",
                error=None,
                job_id=None,
            )
        if _early.write_derived_output:
            write_session_derived_investigation_output(root, active_session, _early.derived_output)
        append_session_turn(root, active_session, role="assistant", text=_early.assistant_text)
        return _render_page(
            session_id=active_session,
            turns=read_session_turns(root, active_session),
            status=_early.status,
            voice_flags=voice_flags,
        )

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
    explicit_targeted_content_refinement = is_targeted_code_preview_refinement(
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
        if preview_body_looks_like_control_narration(builder_payload):
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
                if not preview_body_looks_like_control_narration(fallback_payload):
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
                and looks_like_builder_refinement_placeholder(builder_content)
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

    # ── Post-LLM draft content extraction and binding ──
    # Extract code/text drafts from the reply, then resolve binding into
    # preview payloads.  The derivation is in draft_content_binding.py;
    # final session writes stay here in app.py.
    _drafts = extract_reply_drafts(reply_answer, message)
    sanitized_answer = _drafts.sanitized_answer

    _binding = resolve_draft_content_binding(
        message=message,
        reply_code_content=_drafts.reply_code_content,
        reply_text_draft=_drafts.reply_text_draft,
        reply_status=reply_status,
        builder_payload=builder_payload,
        pending_preview=pending_preview,
        is_code_draft_turn=is_code_draft_turn,
        is_writing_draft_turn=is_writing_draft_turn,
        is_explicit_writing_transform=is_explicit_writing_transform,
        informational_web_turn=informational_web_turn,
        is_enrichment_turn=is_enrichment_turn,
        explicit_targeted_content_refinement=explicit_targeted_content_refinement,
        active_preview_is_refinable_prose=active_preview_is_refinable_prose,
        conversational_answer_first_turn=conversational_answer_first_turn,
        active_session=active_session,
    )
    builder_payload = _binding.builder_payload
    is_code_draft_turn = _binding.is_code_draft_turn
    is_writing_draft_turn = _binding.is_writing_draft_turn
    generation_content_refresh_failed_closed = _binding.generation_content_refresh_failed_closed
    if _binding.preview_needs_write:
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

    # Gate preview-existence claims on actual preview state.
    # An empty-content write_file preview is a placeholder, not authoritative
    # code — treat it as "no real preview" for claim-checking purposes.
    effective_preview = read_session_preview(root, active_session)
    _preview_has_content = derive_preview_has_content(effective_preview)

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
        # Writing draft turns author document content — that content may mention
        # queuing or submission in an explanatory context (e.g. a note about the
        # VoxeraOS queue boundary).  Skip the submission-claim guardrail so the
        # authored text is not replaced with "I have not submitted anything".
        if is_writing_draft_turn:
            guarded_answer = sanitized_answer
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
        # All-or-nothing cleanup: when guardrail_false_preview_claim stripped a
        # false preview-existence claim, clear any empty write_file placeholder so
        # the session is clean — no orphaned shell, no accidental empty submission.
        if should_clear_stale_preview(
            guarded_answer, _answer_before_preview_guardrail, effective_preview
        ):
            write_session_preview(root, active_session, None)
            builder_payload = None

    in_voxera_preview_flow = pending_preview is not None or builder_preview is not None
    is_json_content_request = _is_explicit_json_content_request(message)
    is_voxera_control_turn = _is_voxera_control_turn(message, active_preview=pending_preview)

    _reply = assemble_assistant_reply(
        guarded_answer=guarded_answer,
        message=message,
        pending_preview=pending_preview,
        builder_payload=builder_payload,
        in_voxera_preview_flow=in_voxera_preview_flow,
        is_code_draft_turn=is_code_draft_turn,
        is_writing_draft_turn=is_writing_draft_turn,
        is_enrichment_turn=is_enrichment_turn,
        conversational_answer_first_turn=conversational_answer_first_turn,
        is_json_content_request=is_json_content_request,
        is_voxera_control_turn=is_voxera_control_turn,
        explicit_targeted_content_refinement=explicit_targeted_content_refinement,
        preview_update_rejected=preview_update_rejected,
        generation_content_refresh_failed_closed=generation_content_refresh_failed_closed,
        reply_status=reply_status,
    )
    assistant_text = _reply.assistant_text
    status = _reply.status

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
