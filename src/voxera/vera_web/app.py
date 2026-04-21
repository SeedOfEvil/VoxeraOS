from __future__ import annotations

import asyncio
import contextlib
import os
import re
import secrets
import tempfile
import time
from collections.abc import Awaitable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import load_config as load_runtime_config
from ..core.code_draft_intent import (
    classify_code_draft_intent,
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
from ..vera.automation_preview import (
    is_automation_preview,
    submit_automation_preview,
)
from ..vera.context_lifecycle import (
    context_on_automation_saved,
    context_on_completion_ingested,
    context_on_handoff_submitted,
    context_on_preview_cleared,
    context_on_session_cleared,
)
from ..vera.draft_revision import (
    looks_like_preview_rename_or_save_as_request,
)
from ..vera.evidence_review import (
    maybe_extract_job_id,
)
from ..vera.first_run_tour import (
    clear_walkthrough,
    is_fresh_vera_session,
    is_walkthrough_active,
)
from ..vera.investigation_derivations import (
    derive_investigation_expansion,
    is_investigation_derived_followup_save_request,
    is_investigation_derived_save_request,
    is_investigation_expand_request,
)
from ..vera.investigation_flow import (
    is_informational_web_query,
    run_web_enrichment,
)
from ..vera.linked_completions import (
    ingest_linked_job_completions,
    maybe_auto_surface_linked_completion,
)
from ..vera.preview_drafting import (
    diagnostics_service_or_logs_intent,
    drafting_guidance,
    is_recent_assistant_content_save_request,
    maybe_draft_job_payload,
)
from ..vera.preview_ownership import (
    clear_active_preview,
    record_submit_success,
    reset_active_preview,
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
    generate_preview_builder_update,
    generate_vera_reply,
)
from ..vera.session_store import (
    append_routing_debug_entry,
    append_session_turn,
    clear_session_routing_debug,
    clear_session_turns,
    new_session_id,
    read_session_context,
    read_session_conversational_planning_active,
    read_session_derived_investigation_output,
    read_session_enrichment,
    read_session_handoff_state,
    read_session_investigation,
    read_session_last_automation_preview,
    read_session_last_user_input_origin,
    read_session_preview,
    read_session_saveable_assistant_artifacts,
    read_session_turns,
    read_session_updated_at_ms,
    read_session_weather_context,
    register_session_linked_job,
    session_debug_snapshot,
    write_session_conversational_planning_active,
    write_session_derived_investigation_output,
    write_session_enrichment,
    write_session_investigation,
    write_session_last_automation_preview,
    write_session_weather_context,
)
from ..vera.weather_flow import (
    weather_context_has_pending_lookup,
)
from ..voice.flags import VoiceFoundationFlags, load_voice_foundation_flags
from ..voice.input import (
    VoiceInputDisabledError,
    ingest_voice_transcript,
    transcribe_audio_file_async,
)
from ..voice.models import InputOrigin, normalize_input_origin
from ..voice.output import synthesize_text_async, voice_output_status
from ..voice.stt_protocol import STT_STATUS_SUCCEEDED, stt_response_as_dict
from ..voice.tts_protocol import TTS_STATUS_SUCCEEDED, tts_response_as_dict
from .chat_early_exit_dispatch import dispatch_early_exit_intent
from .conversational_checklist import (
    enforce_conversational_checklist_output,
    sanitize_false_preview_claims_from_answer,
    should_use_conversational_artifact_mode,
)
from .draft_content_binding import (
    extract_reply_drafts,
    resolve_draft_content_binding,
)
from .execution_mode import (
    ExecutionMode,
    _is_explicit_json_content_request,
    _is_relative_writing_refinement_request,
)
from .execution_mode import (
    _classify_execution_mode as _em_classify_execution_mode,
)
from .execution_mode import (
    _is_refinable_prose_preview as _em_is_refinable_prose_preview,
)
from .execution_mode import (
    _is_voxera_control_turn as _em_is_voxera_control_turn,
)
from .execution_mode import (
    _looks_like_active_preview_content_generation_turn as _em_looks_like_active_preview_content_generation_turn,
)
from .lanes.automation_lane import (
    _AUTOMATION_CLARIFICATION_QUESTION_RE,  # noqa: F401 — re-export for tests
    _AUTOMATION_DETAIL_SIGNAL_RE,  # noqa: F401 — re-export for tests
    _AUTOMATION_INTENT_RE,  # noqa: F401 — re-export for tests
    _DIRECT_AUTOMATION_ACTION_RE,  # noqa: F401 — re-export for tests
    _DIRECT_AUTOMATION_PATH_TOKEN_RE,  # noqa: F401 — re-export for tests
    _DIRECT_AUTOMATION_SUBJECT_RE,  # noqa: F401 — re-export for tests
    _DIRECT_AUTOMATION_VERB_RE,  # noqa: F401 — re-export for tests
    _PREVIEWABLE_AUTOMATION_ACTION_HINT_RE,  # noqa: F401 — re-export for tests
    _PREVIEWABLE_AUTOMATION_CLARIFICATION_REPLY,
    _PREVIEWABLE_AUTOMATION_INTENT_RE,  # noqa: F401 — re-export for tests
    _PREVIEWABLE_AUTOMATION_SUBJECT_RE,  # noqa: F401 — re-export for tests
    _detect_automation_clarification_completion,  # noqa: F401 — re-export for tests
    _looks_like_direct_automation_request,  # noqa: F401 — re-export for tests
    _looks_like_previewable_automation_intent,
    _synthesize_direct_automation_preview,  # noqa: F401 — re-export for tests
    try_automation_draft_or_revision_lane,
    try_automation_lifecycle_lane,
    try_materialize_automation_shell,
    try_submit_automation_preview_lane,
)
from .lanes.review_lane import (
    apply_early_exit_state_writes,
    compute_active_preview_revision_in_flight,
)
from .markdown_render import render_assistant_markdown
from .preview_content_binding import (
    is_targeted_code_preview_refinement,
    looks_like_builder_refinement_placeholder,
    preview_body_looks_like_control_narration,
)
from .preview_routing import (
    canonical_preview_lane_order,
)
from .response_shaping import (
    BLANKET_PREVIEW_REFUSAL_TEXT,
    assemble_assistant_reply,
    derive_preview_has_content,
    guardrail_false_preview_claim,
    should_clear_stale_preview,
    strip_internal_compiler_leakage,
)

app = FastAPI(title="Vera v0", version="0")

HERE = Path(__file__).resolve().parent
templates = Environment(
    loader=FileSystemLoader(str(HERE / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
)
templates.filters["render_markdown"] = render_assistant_markdown
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
    assistant_text, status = submit_active_preview_for_session(
        queue_root=root,
        session_id=session_id,
        preview=preview,
        register_linked_job=lambda queue_root, sid, job_ref: register_session_linked_job(
            queue_root, sid, job_ref=job_ref
        ),
        submit_preview_hook=submit_preview,
    )
    if status == "handoff_submitted":
        # Clear walkthrough state if it was active — the user has completed
        # the guided tour by submitting through the governed queue.
        if is_walkthrough_active(root, session_id):
            clear_walkthrough(root, session_id)
        handoff = read_session_handoff_state(root, session_id) or {}
        job_id = str(handoff.get("job_id") or "").strip() or None
        _submit_file_ref: str | None = None
        if isinstance(preview, dict):
            _submit_wf = preview.get("write_file")
            _submit_file_ref = (
                str(_submit_wf.get("path") or "").strip() if isinstance(_submit_wf, dict) else None
            )
        if job_id:
            context_on_handoff_submitted(
                root, session_id, job_id=job_id, saved_file_ref=_submit_file_ref or None
            )
        else:
            # Handoff succeeded but no job_id recorded — clear preview refs only.
            # last_submitted_job_ref retains whatever stale value it had; this is
            # acceptable as fail-closed behavior since there is no real job to track.
            context_on_preview_cleared(root, session_id)
    return assistant_text, status


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


# -- Dependency-binding wrappers for execution_mode.py --
# execution_mode.py is intentionally kept pure (stdlib-only imports) so its
# predicates remain isolated and easily testable.  These wrappers bind concrete
# Vera module dependencies into those pure functions at the app boundary.


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


def _is_empty_code_preview_shell(preview: dict[str, object] | None) -> bool:
    """Return True when preview is an empty-content code/script file shell.

    This detects the state created when Vera classifies a code-draft request
    but the LLM asked for clarification instead of generating code — leaving
    the preview with an authoritative path but no content.
    """
    if not isinstance(preview, dict):
        return False
    wf = preview.get("write_file")
    if not isinstance(wf, dict):
        return False
    path = str(wf.get("path") or "").strip()
    content = str(wf.get("content") or "").strip()
    return (
        bool(path)
        and has_code_file_extension(path)
        and not path.lower().endswith(".md")
        and not content
    )


_NEW_QUERY_RE = re.compile(
    r"^(?:what|who|where|when|why|how|is|are|can|could|does|do|will|should|tell)\b",
    re.IGNORECASE,
)


def _looks_like_new_unrelated_query(message: str) -> bool:
    """Return True when the message looks like a new question/request, not a clarification answer.

    Clarification answers are typically short factual fragments like
    "~/VoxeraOS/notes as source" or "use 5 seconds timeout".  New queries
    start with question words or imperative verbs that signal a fresh topic.
    """
    text = message.strip()
    if not text:
        return False
    return bool(_NEW_QUERY_RE.match(text))


_CODE_DRAFT_RECOVERY_RE = re.compile(
    r"\b(?:prepare|create|make|build|write|generate|draft)\b.*"
    r"\b(?:script|code|program)\b",
    re.IGNORECASE,
)


def _recover_code_draft_from_history(
    message: str,
    *,
    pending_preview: dict[str, object] | None,
    turns: list[dict[str, str]],
) -> dict[str, object] | None:
    """Recover a code draft preview shell from conversation history.

    Fires when no preview exists, the message references a prior code draft
    (e.g. "please prepare that script"), and conversation history contains
    an actual code draft request.  Returns a re-created preview shell so
    the code draft flow can re-engage; returns None otherwise.
    """
    if pending_preview is not None:
        return None
    if is_code_draft_request(message):
        return None
    if not _CODE_DRAFT_RECOVERY_RE.search(message):
        return None
    for turn in reversed(turns[-8:]):
        if str(turn.get("role") or "").strip().lower() != "user":
            continue
        prior_text = str(turn.get("text") or "").strip()
        if prior_text == message.strip():
            continue
        if is_code_draft_request(prior_text):
            draft = classify_code_draft_intent(prior_text)
            if draft is not None:
                try:
                    return normalize_preview_payload(draft)
                except Exception:
                    pass
    return None


def _is_refinable_prose_preview(preview: dict[str, object] | None) -> bool:
    return _em_is_refinable_prose_preview(preview, is_text_draft_preview=is_text_draft_preview)


def _looks_like_active_preview_content_generation_turn(message: str) -> bool:
    return _em_looks_like_active_preview_content_generation_turn(
        message,
        looks_like_preview_rename_or_save_as_request=looks_like_preview_rename_or_save_as_request,
        message_requests_referenced_content=message_requests_referenced_content,
    )


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
        should_use_conversational_artifact_mode=should_use_conversational_artifact_mode,
        is_recent_assistant_content_save_request=is_recent_assistant_content_save_request(message),
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
    # Compute the tour hint for the empty-state landing page only.
    # Skip the session-context read when turns exist (guidance hidden).
    _show_tour_hint = False
    if not turns:
        _ctx = read_session_context(root, session_id)
        _show_tour_hint = is_fresh_vera_session(turns, _ctx)
    _guidance = _main_screen_guidance(show_tour_hint=_show_tour_hint)
    tmpl = templates.get_template("index.html")
    html = tmpl.render(
        session_id=session_id,
        turns=turns,
        mode_status=status,
        queue_boundary=vera_queue_boundary_summary(),
        error=error,
        debug_info=session_debug_snapshot(root, session_id, mode_status=status),
        voice_runtime=voice_runtime,
        last_user_input_origin=read_session_last_user_input_origin(root, session_id),
        system_prompt=VERA_SYSTEM_PROMPT,
        pending_preview=read_session_preview(root, session_id),
        drafting_examples=drafting_guidance().examples,
        main_screen_guidance=_guidance,
    )
    response = HTMLResponse(content=html)
    response.set_cookie("vera_session_id", session_id, httponly=False, samesite="lax")
    return response


def _main_screen_guidance(*, show_tour_hint: bool = False) -> dict[str, object]:
    guidance: dict[str, object] = {
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
    if show_tour_hint:
        guidance["tour_hint"] = (
            "I can walk you through how VoxeraOS works step by step. "
            "You'll edit a preview, rename a note, and then submit it "
            "through the queue so you can inspect the evidence trail. "
            'Say "start VoxeraOS tour" to begin.'
        )
    return guidance


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    # ``?session_id=<id>`` lets another surface (e.g. the panel Voice
    # Workbench) hand the operator a direct link to the same canonical
    # Vera session that voice turns were persisted under.  The value is
    # clamped through ``Path(...).name`` so it can only resolve to a
    # session file under the canonical sessions directory.  Empty or
    # whitespace-only values fall back to the cookie / new session
    # behavior as before.
    query_session_id = Path((request.query_params.get("session_id") or "").strip() or ".").name
    cookie_session_id = (request.cookies.get("vera_session_id") or "").strip()
    session_id = (
        query_session_id
        if query_session_id and query_session_id != "."
        else (cookie_session_id or new_session_id())
    )
    root = _active_queue_root()
    return _render_page(
        session_id=session_id,
        turns=read_session_turns(root, session_id),
        status="conversation",
    )


@dataclass(frozen=True)
class ChatTurnResult:
    """Canonical outcome of one Vera chat turn.

    Shared by both the typed ``/chat`` endpoint and the dictation
    ``/chat/voice`` endpoint so the 7-lane preview routing, LLM
    orchestration, post-LLM draft binding, conversational artifact
    enforcement, and guardrails produce identical results regardless
    of whether the input was typed or dictated.  Surface-specific
    rendering (HTML for typed, JSON for dictation) is a thin wrapper
    around this result.

    ``preview`` reflects the active preview as persisted in the session
    store after the turn finishes; ``assistant_text`` is the final
    assistant reply appended this turn (or ``""`` when only an error
    was surfaced without running the lanes).

    ``stage_timings`` is a bounded, truthful mapping of per-sub-stage
    wall-clock milliseconds for the Vera-internal stages that actually
    ran this turn (preview builder LLM call, main reply LLM call, web
    enrichment LLM call).  Stages that did not run on this turn stay
    absent from the dict.  Surfaces like ``/chat/voice`` merge this
    into their own top-level ``stage_timings`` so operators can see
    where the ``vera_ms`` umbrella actually went.  Never used to gate
    behaviour.
    """

    session_id: str
    turns: list[dict[str, str]]
    status: str
    error: str = ""
    assistant_text: str = ""
    preview: dict[str, object] | None = None
    stage_timings: dict[str, int] = field(default_factory=dict)


# ── Sub-stage timing helpers ───────────────────────────────────────
# Bounded wrappers that compute wall-clock milliseconds for individual
# Vera-internal sub-stages (the two LLM calls, optional enrichment).
# They are intentionally plain helpers, not framework: the goal is
# truthful operator-visible timings for the stages operators care
# about, not a full tracing system.  ``time.monotonic()`` is used so
# the measurement is immune to wall-clock adjustments during a turn.
T = TypeVar("T")


def _now_ms() -> float:
    """Monotonic wall-clock milliseconds for sub-stage measurement.

    Returns a float because we subtract two samples and round once
    at the end; using ``int(time.monotonic() * 1000)`` directly would
    truncate sub-millisecond portions on each read and bias timings.
    """
    return time.monotonic() * 1000.0


async def _timed_stage(coro: Awaitable[T]) -> tuple[T, int]:
    """Await ``coro`` and return ``(result, elapsed_ms)`` as a pair.

    Used to measure individual LLM stages inside ``run_vera_chat_turn``.
    The elapsed value is a non-negative ``int`` of wall-clock ms rounded
    once at the end, which is what operator diagnostics surface.
    """
    started = _now_ms()
    result = await coro
    elapsed_ms = max(0, int(_now_ms() - started))
    return result, elapsed_ms


async def run_vera_chat_turn(
    *,
    message: str,
    input_origin: InputOrigin,
    session_id: str,
    voice_flags: VoiceFoundationFlags,
) -> ChatTurnResult:
    """Vera chat turn — canonical preview-routing lane precedence.

    Both the typed ``/chat`` endpoint and the dictation ``/chat/voice``
    endpoint call this helper with the same canonical semantics.  The
    only surface-level difference is the input boundary:

    * Typed: ``input_origin=InputOrigin.TYPED`` from the form.
    * Any ``VOICE_TRANSCRIPT`` input (today only dictation, but the
      helper treats the origin generically): after the caller has
      already run STT and is ready to hand a transcript in, this
      function re-runs :func:`ingest_voice_transcript` for normalization
      + fail-closed voice-input-disabled enforcement.

    The caller is responsible for resolving the active ``session_id``
    (form field, cookie fallback, or :func:`new_session_id`) BEFORE
    calling this helper — the helper trusts the value it is handed
    and does not touch cookies or request state.

    Everything downstream — the 7-lane routing, LLM orchestration,
    post-LLM draft binding, conversational artifact mode, guardrails,
    session writes — is identical.  Dictation parity with typed Vera
    is therefore enforced by construction.

    Lanes run in strict order below; the same order is recorded in
    :func:`voxera.vera_web.preview_routing.canonical_preview_lane_order`
    so the two surfaces stay aligned. Earlier lanes either claim the
    turn or fall through; no lane may silently mutate the active
    preview owned by another lane.

    1. ``EXPLICIT_SUBMIT`` — explicit submit / handoff on the active
       preview (including the automation-preview-save branch).
    2. ``ACTIVE_PREVIEW_REVISION`` — revision of an active automation
       preview, and the ``is_active_preview_revision_turn`` gate that
       protects normal active previews from lifecycle/review hijacks.
    3. ``AUTOMATION_LIFECYCLE`` — manage saved automation definitions.
       Steps aside when a normal active preview is clearly under
       revision.
    4. ``FOLLOWUP_FROM_EVIDENCE`` — evidence-driven follow-up previews
       (handled inside ``dispatch_early_exit_intent``).
    5. ``PREVIEW_CREATION`` — code/writing/automation shell synthesis,
       deterministic builder path, rename/save-as fallback.
    6. ``READ_ONLY_EARLY_EXIT`` — time, weather, diagnostics, blocked
       file intent, near-miss submit, investigation utilities.
    7. ``CONVERSATIONAL`` — LLM orchestration + post-LLM draft binding.

    All preview state mutations go through
    ``voxera.vera.preview_ownership`` so the set of writers is narrow
    and auditable.
    """
    # Sanity gate: keep the chat dispatcher aligned with the lane enum.
    # This assertion is cheap and fires immediately if someone reorders
    # lanes without updating the enum.
    assert len(canonical_preview_lane_order()) == 7  # noqa: S101

    active_session = session_id

    if input_origin is InputOrigin.VOICE_TRANSCRIPT:
        try:
            ingested = ingest_voice_transcript(
                transcript_text=message,
                voice_input_enabled=voice_flags.voice_input_enabled,
            )
        except VoiceInputDisabledError as exc:
            root = _active_queue_root()
            return ChatTurnResult(
                session_id=active_session,
                turns=read_session_turns(root, active_session),
                status="voice_input_disabled",
                error=str(exc),
            )
        except ValueError as exc:
            root = _active_queue_root()
            return ChatTurnResult(
                session_id=active_session,
                turns=read_session_turns(root, active_session),
                status="voice_input_invalid",
                error=str(exc),
            )
        message = ingested.transcript_text
        input_origin = InputOrigin(ingested.input_origin)

    if not message.strip():
        root = _active_queue_root()
        return ChatTurnResult(
            session_id=active_session,
            turns=read_session_turns(root, active_session),
            status="conversation",
            error="Message is required.",
        )

    root = _active_queue_root()
    pre_turn_preview = read_session_preview(root, active_session)
    suppress_auto_completion_note = should_submit_active_preview(
        message,
        preview_available=pre_turn_preview is not None,
    )
    _ingested_completions = ingest_linked_job_completions(root, active_session)
    # Track the freshest ingested completion in session context so
    # reference resolution ("that result", "the last job") stays current.
    for _ic in _ingested_completions:
        _ic_ref = str(_ic.get("job_ref") or "").strip()
        if _ic_ref:
            context_on_completion_ingested(root, active_session, job_id=_ic_ref)
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
    #
    # Active-preview revision protection: when a clear revision/follow-up
    # mutation of a normal active preview is in flight, we prevent the
    # early-exit dispatch's preview-writing branches (follow-up from
    # evidence, investigation derived-save, investigation save) from
    # silently overwriting the active preview slot. Non-mutating branches
    # (time, diagnostics refusal, job review report, near-miss submit
    # rejection, stale-draft reference) still run — they never touch the
    # preview.  The narrow revision gate plus the review/evidence belt-
    # and-suspenders live in ``lanes.review_lane`` so the review lane
    # owns its own protection logic; this file only calls the helper so
    # the value remains reusable by the downstream automation-lifecycle
    # gate.
    _active_preview_revision_in_flight = compute_active_preview_revision_in_flight(
        message, pending_preview=pending_preview
    )
    _session_ctx = read_session_context(root, active_session)
    _early = dispatch_early_exit_intent(
        message=message,
        diagnostics_service_turn=diagnostics_service_turn,
        requested_job_id=requested_job_id,
        should_attempt_derived_save=should_attempt_derived_save,
        session_investigation=session_investigation,
        session_derived_output=session_derived_output,
        queue_root=root,
        session_id=active_session,
        session_context=_session_ctx,
        active_preview_revision_in_flight=_active_preview_revision_in_flight,
    )
    if _early.matched:
        # Review-lane orchestration: apply preview / context / derived-
        # output writes for the early-exit result. All preview mutations
        # flow through ``preview_ownership`` helpers so ownership stays
        # centralized and the review/evidence truth boundary is
        # preserved.
        apply_early_exit_state_writes(_early, queue_root=root, session_id=active_session)
        append_session_turn(root, active_session, role="assistant", text=_early.assistant_text)
        _early_turns = read_session_turns(root, active_session)
        append_routing_debug_entry(
            root,
            active_session,
            route_status=_early.status,
            dispatch_source="early_exit_dispatch",
            matched_early_exit=True,
            turn_index=len(_early_turns),
        )
        return ChatTurnResult(
            session_id=active_session,
            turns=_early_turns,
            status=_early.status,
            assistant_text=_early.assistant_text,
            preview=read_session_preview(root, active_session),
        )

    if (
        is_natural_preview_submission_confirmation(message)
        and pending_preview is None
        and weather_context_has_pending_lookup(session_weather_context)
    ):
        reply = await generate_vera_reply(
            turns=turns,
            user_message=message,
            code_draft=False,
            writing_draft=False,
            weather_context=session_weather_context,
        )
        reply_answer = strip_internal_compiler_leakage(str(reply.get("answer") or ""))
        weather_payload = reply.get("weather_context") if isinstance(reply, dict) else None
        if isinstance(weather_payload, dict):
            write_session_weather_context(root, active_session, weather_payload)
        append_session_turn(root, active_session, role="assistant", text=reply_answer)
        _weather_status = str(reply.get("status") or "ok:weather_current")
        _weather_turns = read_session_turns(root, active_session)
        append_routing_debug_entry(
            root,
            active_session,
            route_status=_weather_status,
            dispatch_source="weather_pending_lookup",
            turn_index=len(_weather_turns),
        )
        return ChatTurnResult(
            session_id=active_session,
            turns=_weather_turns,
            status=_weather_status,
            assistant_text=reply_answer,
            preview=read_session_preview(root, active_session),
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
        _submit_turns = read_session_turns(root, active_session)
        append_routing_debug_entry(
            root,
            active_session,
            route_status=status,
            dispatch_source="submit_no_preview",
            turn_index=len(_submit_turns),
        )
        return ChatTurnResult(
            session_id=active_session,
            turns=_submit_turns,
            status=status,
            assistant_text=assistant_text,
            preview=read_session_preview(root, active_session),
        )

    # ── Automation preview submit ──────────────────────────────────────────
    # When the active preview is an automation definition preview, submit
    # saves a durable automation definition instead of emitting a queue job.
    # Lane-specific logic lives in ``lanes.automation_lane``; this file
    # retains the dispatch + render orchestration.
    _auto_submit_result = try_submit_automation_preview_lane(
        message=message,
        pending_preview=pending_preview,
        queue_root=root,
        session_id=active_session,
    )
    if _auto_submit_result.matched:
        append_session_turn(
            root, active_session, role="assistant", text=_auto_submit_result.assistant_text
        )
        _auto_turns = read_session_turns(root, active_session)
        append_routing_debug_entry(
            root,
            active_session,
            route_status=_auto_submit_result.status,
            dispatch_source=_auto_submit_result.dispatch_source,
            turn_index=len(_auto_turns),
        )
        return ChatTurnResult(
            session_id=active_session,
            turns=_auto_turns,
            status=_auto_submit_result.status,
            assistant_text=_auto_submit_result.assistant_text,
            preview=read_session_preview(root, active_session),
        )

    if should_submit_active_preview(message, preview_available=pending_preview is not None):
        assistant_text, status = _submit_handoff(
            root=root,
            session_id=active_session,
            preview=pending_preview,
        )
        append_session_turn(root, active_session, role="assistant", text=assistant_text)
        _submit_turns = read_session_turns(root, active_session)
        append_routing_debug_entry(
            root,
            active_session,
            route_status=status,
            dispatch_source="submit_active_preview",
            turn_index=len(_submit_turns),
        )
        return ChatTurnResult(
            session_id=active_session,
            turns=_submit_turns,
            status=status,
            assistant_text=assistant_text,
            preview=read_session_preview(root, active_session),
        )

    # Blocked bounded file intent: fail closed with a clear refusal before
    # the message reaches the LLM, which might produce a misleading pseudo
    # action blob.
    blocked_refusal = detect_blocked_file_intent(message)
    if blocked_refusal is not None:
        append_session_turn(root, active_session, role="assistant", text=blocked_refusal)
        _blocked_turns = read_session_turns(root, active_session)
        append_routing_debug_entry(
            root,
            active_session,
            route_status="blocked_path",
            dispatch_source="blocked_file_intent",
            turn_index=len(_blocked_turns),
        )
        return ChatTurnResult(
            session_id=active_session,
            turns=_blocked_turns,
            status="blocked_path",
            assistant_text=blocked_refusal,
            preview=read_session_preview(root, active_session),
        )

    # ── Automation preview drafting / revision ─────────────────────────────
    # Detect automation-authoring intent ("every hour, run diagnostics") and
    # draft a governed automation preview deterministically.  If there is
    # already an active automation preview, handle revision turns.
    # Post-submit continuity: answer "what did you save?" / "did it run?"
    # using the stashed automation preview.
    _auto_draft_result = try_automation_draft_or_revision_lane(
        message=message,
        pending_preview=pending_preview,
        diagnostics_service_turn=diagnostics_service_turn,
        queue_root=root,
        session_id=active_session,
    )
    if _auto_draft_result.matched:
        if _auto_draft_result.pending_preview_after is not None:
            pending_preview = _auto_draft_result.pending_preview_after
        append_session_turn(
            root, active_session, role="assistant", text=_auto_draft_result.assistant_text
        )
        _auto_turns = read_session_turns(root, active_session)
        append_routing_debug_entry(
            root,
            active_session,
            route_status=_auto_draft_result.status,
            dispatch_source=_auto_draft_result.dispatch_source,
            turn_index=len(_auto_turns),
        )
        return ChatTurnResult(
            session_id=active_session,
            turns=_auto_turns,
            status=_auto_draft_result.status,
            assistant_text=_auto_draft_result.assistant_text,
            preview=read_session_preview(root, active_session),
        )

    # ── Automation lifecycle management ───────────────────────────────────
    # Detect conversational lifecycle requests (show, enable, disable,
    # delete, run-now, history) for saved automation definitions. Uses the
    # canonical automation store and history — not only session memory.
    # Must come after automation preview revision (so active-preview
    # revision turns are not hijacked) but before the LLM path.
    #
    # Lane precedence fix: this lane also steps aside when an active
    # **normal** preview exists and the current message is a clear
    # revision/follow-up of that preview. Without this guard, a phrase
    # that overlaps lifecycle wording ("run it now", "show me the
    # file") could steal a turn that was clearly mutating an active
    # non-automation preview. See ``preview_routing.is_active_preview_
    # revision_turn`` for the conservative gate.
    _last_auto_preview = read_session_last_automation_preview(root, active_session)
    _lifecycle_result = try_automation_lifecycle_lane(
        message=message,
        pending_preview=pending_preview,
        active_preview_revision_in_flight=_active_preview_revision_in_flight,
        session_context=_session_ctx,
        last_automation_preview=_last_auto_preview,
        queue_root=root,
        session_id=active_session,
    )
    if _lifecycle_result.matched:
        append_session_turn(
            root, active_session, role="assistant", text=_lifecycle_result.assistant_text
        )
        _lc_turns = read_session_turns(root, active_session)
        append_routing_debug_entry(
            root,
            active_session,
            route_status=_lifecycle_result.status,
            dispatch_source=_lifecycle_result.dispatch_source,
            matched_early_exit=_lifecycle_result.matched_early_exit,
            turn_index=len(_lc_turns),
        )
        return ChatTurnResult(
            session_id=active_session,
            turns=_lc_turns,
            status=_lifecycle_result.status,
            assistant_text=_lifecycle_result.assistant_text,
            preview=read_session_preview(root, active_session),
        )

    is_info_query = is_informational_web_query(message)
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
    # ── Post-clarification code draft continuation ────────────────────────
    # When the LLM asked for clarification on a prior code-draft request
    # (leaving an empty-content code preview shell), treat this answer as
    # a code draft turn so the LLM receives the code generation hint and
    # the reply code can be injected into the existing shell.
    # Guard: do not fire when the message has a clear different intent
    # (informational query, writing-draft, conversational-artifact, or a
    # new question unrelated to the clarification exchange).
    _post_clarification_code_draft = (
        _is_empty_code_preview_shell(pending_preview)
        and not is_code_draft_request(message)
        and not is_info_query
        and not is_explicit_writing_transform
        and not conversational_answer_first_turn
        and not _is_voxera_control_turn(message, active_preview=pending_preview)
        and not _looks_like_new_unrelated_query(message)
    )
    # ── Code draft recovery from conversation history ─────────────────────
    # When no preview exists but a recent user turn was a code draft request
    # and the current message references it (e.g. "please prepare that
    # script"), re-create the preview shell from the historical intent.
    _recovered_code_draft = _recover_code_draft_from_history(
        message,
        pending_preview=pending_preview,
        turns=turns,
    )
    if _recovered_code_draft is not None:
        pending_preview = _recovered_code_draft
        reset_active_preview(root, active_session, _recovered_code_draft)
        _post_clarification_code_draft = True
    # ── Automation shell materialization ─────────────────────────────────
    # Two sub-lanes live behind a single helper in ``lanes.automation_lane``:
    #
    # 1. Post-clarification completion — the user answers a clarification
    #    question for an automation/process-style request (which does not
    #    match ``is_code_draft_request``). Synthesize a Python-script
    #    preview shell so the standard code-draft flow can inject the
    #    generated code.
    # 2. Direct automation request — a fully specified single-turn request
    #    with all four structural signals (automation verb + path token +
    #    action verb + file/dir subject) synthesizes the shell directly.
    #
    # Both paths reset the active preview through the approved
    # ``preview_ownership`` helper and, when either fires, the code-draft
    # flow continues on the same turn so the generated code is injected
    # into the shell.
    _materialized_shell = try_materialize_automation_shell(
        message=message,
        pending_preview=pending_preview,
        turns=turns,
        is_info_query=is_info_query,
        is_explicit_writing_transform=is_explicit_writing_transform,
        conversational_answer_first_turn=conversational_answer_first_turn,
        is_voxera_control_turn=_is_voxera_control_turn(message, active_preview=None),
        looks_like_new_unrelated_query=_looks_like_new_unrelated_query(message),
        queue_root=root,
        session_id=active_session,
    )
    if _materialized_shell is not None:
        pending_preview = _materialized_shell
        _post_clarification_code_draft = True
    is_code_draft_turn = (
        (
            is_code_draft_request(message)
            or explicit_targeted_content_refinement
            or _post_clarification_code_draft
        )
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

    # Sub-stage timings captured during the LLM orchestration lane.
    # These are returned on the ``ChatTurnResult`` so operator-visible
    # surfaces (dictation payload, debug snapshot) can explain where
    # the ``vera_ms`` umbrella actually went.  Only keys for stages
    # that actually ran this turn are inserted — absence is truthful.
    stage_timings: dict[str, int] = {}

    # When an active preview exists and the user makes an informational query,
    # run read-only enrichment and store it for follow-up pronoun resolution.
    is_enrichment_turn = is_info_query and pending_preview is not None
    session_enrichment = read_session_enrichment(root, active_session)
    if is_enrichment_turn:
        fresh_enrichment, enrichment_ms = await _timed_stage(
            run_web_enrichment(user_message=message)
        )
        stage_timings["vera_enrichment_ms"] = enrichment_ms
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

    # ── Bounded LLM-call parallelism ──────────────────────────────────────
    # The preview builder and the main Vera reply are functionally
    # independent: the reply does not read the builder's output, and
    # the builder does not read the reply's.  Running them concurrently
    # when both are needed cuts the dominant ``vera_ms`` phase roughly
    # in half for the common "conversational + preview refresh" turn
    # shape — which is exactly the slow case that drove time-to-first-
    # speech on voice turns.  Text remains authoritative: the reply
    # still waits on its own completion, and preview writes still go
    # through ``reset_active_preview`` after the gather resolves, so
    # no semantics shift.
    should_run_builder = not informational_web_turn and not conversational_answer_first_turn
    builder_preview: dict[str, object] | None = None
    builder_ms: int | None = None
    if should_run_builder:
        (builder_preview, builder_ms), (reply, reply_ms) = await asyncio.gather(
            _timed_stage(
                generate_preview_builder_update(
                    turns=turns,
                    user_message=message,
                    active_preview=pending_preview,
                    enrichment_context=enrichment_context,
                    investigation_context=session_investigation,
                    recent_assistant_artifacts=recent_assistant_artifacts,
                )
            ),
            _timed_stage(
                generate_vera_reply(
                    turns=turns,
                    user_message=message,
                    code_draft=is_code_draft_turn,
                    writing_draft=is_writing_draft_turn,
                    weather_context=session_weather_context,
                )
            ),
        )
        stage_timings["vera_preview_builder_ms"] = builder_ms
        stage_timings["vera_reply_ms"] = reply_ms
    else:
        reply, reply_ms = await _timed_stage(
            generate_vera_reply(
                turns=turns,
                user_message=message,
                code_draft=is_code_draft_turn,
                writing_draft=is_writing_draft_turn,
                weather_context=session_weather_context,
            )
        )
        stage_timings["vera_reply_ms"] = reply_ms
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
                session_context=_session_ctx,
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
            reset_active_preview(root, active_session, builder_payload)

    # ── Rename-mutation fallback ──────────────────────────────────────────
    # When the user clearly asked for a rename/save-as but builder_payload
    # is still None (either the builder returned None outright, or its
    # result was rejected/no-op'd), re-run the deterministic path which
    # handles rename/save-as mutations reliably.
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
            session_context=_session_ctx,
        )
        if isinstance(_rename_fallback, dict):
            try:
                _rename_payload = normalize_preview_payload(_rename_fallback)
            except Exception:
                _rename_payload = None
            if _rename_payload is not None and _rename_payload != pending_preview:
                builder_payload = _rename_payload
                reset_active_preview(root, active_session, builder_payload)

    # ``reply`` was already produced above via ``_timed_stage(...)`` —
    # in the builder+reply parallel branch it awaited concurrently with
    # the preview builder through ``asyncio.gather``; in the builder-
    # skipped branch it awaited standalone.  Either way, ``reply`` and
    # ``stage_timings["vera_reply_ms"]`` are already populated.
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
    _drafts = extract_reply_drafts(
        reply_answer,
        message,
        active_preview_is_refinable_prose=active_preview_is_refinable_prose,
    )
    sanitized_answer = _drafts.sanitized_answer

    _binding = resolve_draft_content_binding(
        message=message,
        reply_code_content=_drafts.reply_code_content,
        reply_text_draft=_drafts.reply_text_draft,
        sanitized_answer=sanitized_answer,
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
    if _binding.preview_needs_write and isinstance(builder_payload, dict):
        reset_active_preview(root, active_session, builder_payload)

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
        guarded_answer = sanitize_false_preview_claims_from_answer(sanitized_answer)
        # Final enforcement: deterministic safety net catches any edge cases
        # the sanitizer missed and re-renders as a plain checklist if needed.
        guarded_answer = enforce_conversational_checklist_output(
            guarded_answer, raw_answer=sanitized_answer, user_message=message
        )
    else:
        # Defense-in-depth: strip any internal compiler/JSON payloads that
        # survived into the sanitized answer before downstream guardrails run.
        # This prevents intent/reasoning/decisions/write_file dumps from
        # leaking into visible chat in GOVERNED_PREVIEW mode.
        sanitized_answer = strip_internal_compiler_leakage(sanitized_answer)
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
        guarded_answer = guardrail_false_preview_claim(
            guarded_answer,
            preview_exists=effective_preview is not None and _preview_has_content,
        )
        # First-turn previewable automation polish: when the guardrail collapsed
        # the LLM reply into the blanket "I was not able to prepare a governed
        # preview" refusal AND the user message clearly describes a previewable
        # automation/process request, swap the refusal for a focused
        # clarification question.  This narrows over-conservative first-turn
        # refusals for requests Vera can clearly handle without weakening the
        # trust model — no preview is materialized here, only the visible reply
        # text is replaced.  Fail-closed for genuinely unsupported requests
        # because the detector requires three structural signals.
        if (
            guarded_answer.strip() == BLANKET_PREVIEW_REFUSAL_TEXT
            and effective_preview is None
            and _looks_like_previewable_automation_intent(message)
        ):
            guarded_answer = _PREVIEWABLE_AUTOMATION_CLARIFICATION_REPLY
        # All-or-nothing cleanup: when guardrail_false_preview_claim stripped a
        # false preview-existence claim, clear any empty write_file placeholder so
        # the session is clean — no orphaned shell, no accidental empty submission.
        if should_clear_stale_preview(
            guarded_answer, _answer_before_preview_guardrail, effective_preview
        ):
            clear_active_preview(root, active_session, reason="stale_preview_guardrail_cleanup")
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
    _final_turns = read_session_turns(root, active_session)
    append_routing_debug_entry(
        root,
        active_session,
        route_status=status,
        dispatch_source="llm_orchestration",
        turn_index=len(_final_turns),
    )

    return ChatTurnResult(
        session_id=active_session,
        turns=_final_turns,
        status=status,
        assistant_text=assistant_text,
        preview=read_session_preview(root, active_session),
        stage_timings=stage_timings,
    )


@app.post("/chat", response_class=HTMLResponse)
async def chat(request: Request) -> HTMLResponse:
    """Typed Vera chat turn — thin Request→HTML wrapper over the canonical helper.

    All semantic behavior lives in :func:`run_vera_chat_turn`; this
    endpoint only parses the typed form submission, resolves the active
    session, and renders the result as HTML.  Dictation (``/chat/voice``)
    shares the same helper so typed and dictated turns produce identical
    Vera behavior for equivalent input.
    """
    raw = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw, keep_blank_values=True)
    message = str((parsed.get("message") or [""])[0])
    input_origin_raw = str((parsed.get("input_origin") or ["typed"])[0])
    session_id = str((parsed.get("session_id") or [""])[0])
    voice_flags = load_voice_foundation_flags()
    input_origin = normalize_input_origin(input_origin_raw)

    active_session = session_id.strip() or (request.cookies.get("vera_session_id") or "").strip()
    active_session = active_session or new_session_id()

    result = await run_vera_chat_turn(
        message=message,
        input_origin=input_origin,
        session_id=active_session,
        voice_flags=voice_flags,
    )
    return _render_page(
        session_id=result.session_id,
        turns=result.turns,
        status=result.status,
        error=result.error,
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

    # ── Automation preview: save definition instead of queue submit ────
    # The handoff endpoint must respect the active preview type.
    # Automation previews save a durable definition; they do NOT emit a
    # queue job.  This matches the routing in /chat for
    # should_submit_active_preview + is_automation_preview.
    if isinstance(preview, dict) and is_automation_preview(preview):
        _auto_result = submit_automation_preview(preview, root)
        record_submit_success(root, active_session)
        context_on_automation_saved(root, active_session, automation_id=_auto_result.automation_id)
        write_session_last_automation_preview(root, active_session, preview)
        append_session_turn(root, active_session, role="assistant", text=_auto_result.ack)
        return _render_page(
            session_id=active_session,
            turns=read_session_turns(root, active_session),
            status="automation_definition_saved",
            voice_flags=load_voice_foundation_flags(),
        )

    # ── Normal preview: queue-submit path (existing behavior) ─────────
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

    _clear_root = _active_queue_root()
    clear_session_turns(_clear_root, active_session)
    context_on_session_cleared(_clear_root, active_session)
    clear_session_routing_debug(_clear_root, active_session)
    return _render_page(
        session_id=active_session,
        turns=[],
        status="conversation",
        voice_flags=load_voice_foundation_flags(),
    )


# ── Vera dictation (canonical /chat/voice) ─────────────────────────
# Browser-initiated, operator-only microphone capture that funnels
# through the exact same STT -> (lifecycle | Vera + preview) ->
# optional TTS pipeline as the panel Voice Workbench. The browser
# posts a raw audio blob; the server transcribes, runs the shared
# pipeline, and returns a small JSON payload the dictation enhancer
# uses to refresh the thread inline (no full-page reload).
#
# Trust model:
# - Turns land on the canonical Vera session (``voice_transcript``
#   input origin) — identical to what typed chat writes.
# - Preview/approve/submit still flow through canonical preview and
#   queue seams. This route never bypasses them.
# - Audio bodies are capped; non-audio / empty / oversized bodies
#   fail closed without creating a temp file.
# - TTS artifacts are served via a short-lived tokenized endpoint
#   (``/vera/voice/audio/<token>``) so the browser can play the audio
#   without learning the real temp-file path.

_VERA_DICTATION_MAX_BYTES = 25 * 1024 * 1024
_VERA_DICTATION_TEMP_PREFIX = "voxera_vera_mic_"
_VERA_DICTATION_SUFFIX_BY_CONTENT_TYPE: dict[str, str] = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".mp4",
    "audio/aac": ".aac",
    "audio/flac": ".flac",
}

# Token -> (absolute_audio_path, expires_at_ms). A short-lived registry
# that lets the browser fetch TTS output without the path ever being
# exposed. Tokens are single-use-friendly but not strictly single-use:
# the browser may re-fetch for <audio> buffering. Expiry keeps the
# registry bounded; leaked tokens expire within ``_TTS_TOKEN_TTL_MS``.
# The registry is also capped at ``_MAX_TTS_REGISTRY_ENTRIES`` so a
# flood of voice-reply syntheses cannot grow the registry (or keep
# backing audio files on disk) without bound.
_TTS_TOKEN_TTL_MS = 10 * 60 * 1000
_MAX_TTS_REGISTRY_ENTRIES = 32
_TTS_TOKEN_ALPHABET_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_TTS_AUDIO_REGISTRY: dict[str, tuple[str, int]] = {}


def _audio_suffix_for(content_type: str | None) -> str:
    if not content_type:
        return ".webm"
    base = content_type.split(";", 1)[0].strip().lower()
    return _VERA_DICTATION_SUFFIX_BY_CONTENT_TYPE.get(base, ".webm")


def _is_audio_content_type(content_type: str | None) -> bool:
    if not content_type:
        return False
    base = content_type.split(";", 1)[0].strip().lower()
    return base.startswith("audio/")


def _remove_tts_audio_file(path: str) -> None:
    """Best-effort unlink of a TTS artifact referenced by the registry.

    The registry only owns the reference once ``_register_tts_audio`` is
    called; pruning removes both the registry entry AND the on-disk
    artifact so successful-but-unfetched TTS output cannot accumulate
    across many dictation turns.  Unlink failures are swallowed because
    the artifact lives under a temp dir the operating system eventually
    recycles.
    """
    if not path:
        return
    with contextlib.suppress(OSError):
        os.unlink(path)


def _prune_expired_tts_tokens(now_ms: int) -> None:
    expired = [tok for tok, (_path, expiry) in _TTS_AUDIO_REGISTRY.items() if expiry <= now_ms]
    for tok in expired:
        entry = _TTS_AUDIO_REGISTRY.pop(tok, None)
        if entry is not None:
            _remove_tts_audio_file(entry[0])


def _register_tts_audio(audio_path: str) -> str:
    now_ms = int(time.time() * 1000)
    _prune_expired_tts_tokens(now_ms)
    # Cap the registry: if we're still at or over the hard limit after
    # pruning expired entries, evict the oldest (lowest expiry) ones so
    # a chatty session cannot grow the registry without bound. The on-
    # disk artifact for the evicted entry is unlinked as well so the
    # registry's size cap doubles as a disk-cleanup cap.
    while len(_TTS_AUDIO_REGISTRY) >= _MAX_TTS_REGISTRY_ENTRIES:
        oldest_token = min(
            _TTS_AUDIO_REGISTRY,
            key=lambda tok: _TTS_AUDIO_REGISTRY[tok][1],
        )
        entry = _TTS_AUDIO_REGISTRY.pop(oldest_token, None)
        if entry is not None:
            _remove_tts_audio_file(entry[0])
    token = secrets.token_urlsafe(24)
    _TTS_AUDIO_REGISTRY[token] = (audio_path, now_ms + _TTS_TOKEN_TTL_MS)
    return token


def _resolve_tts_token(token: str) -> str | None:
    entry = _TTS_AUDIO_REGISTRY.get(token)
    if entry is None:
        return None
    path, expiry = entry
    if expiry <= int(time.time() * 1000):
        _TTS_AUDIO_REGISTRY.pop(token, None)
        _remove_tts_audio_file(path)
        return None
    return path


def _display_status_for_stt(stt_status: str, *, ok: bool) -> str:
    if ok:
        return stt_status
    if stt_status == STT_STATUS_SUCCEEDED:
        return "no_transcript"
    return stt_status or "failed"


def _display_status_for_tts(tts_status: str, *, ok: bool) -> str:
    if ok:
        return tts_status
    if tts_status == TTS_STATUS_SUCCEEDED:
        return "no_audio_artifact"
    return tts_status or "failed"


@app.post("/chat/voice")
async def chat_voice(request: Request) -> JSONResponse:
    """Dictation endpoint — bounded browser-mic capture for canonical Vera.

    Operator-initiated capture only (no always-on listening; the
    browser posts a discrete utterance).  The audio is transcribed by
    the canonical STT backend, then the transcript is routed through
    :func:`run_vera_chat_turn` — the exact same canonical helper that
    typed ``/chat`` uses.  This is how dictation stays at parity with
    typed Vera: both surfaces share one message-processing path, and
    dictation differs only at the audio/STT input boundary.

    Returns a small JSON payload so the dictation enhancer can update
    the thread inline without a full-page reload.  TTS synthesis (when
    ``speak_response=1``) runs over the final assistant text produced
    by the canonical path — whether that text came from an early-exit
    lane, an automation lifecycle action, a preview draft, or the LLM.

    JSON response shape (stable contract for the enhancer):

    * ``ok`` — ``stt_ok AND chat_ran AND chat_error_empty``.  A clean
      refusal lane (e.g. ``blocked_path``) reports ``ok=True`` because
      the canonical path ran to completion and produced a truthful
      reply; ``ok`` being ``False`` means the turn genuinely could not
      complete (STT failure, voice-input-disabled, empty transcript).
    * ``session_id``, ``status``, ``error``, ``turns``, ``turn_count``,
      ``assistant_text`` — canonical turn result (truthful even when
      ``ok`` is ``False``).
    * ``preview`` + ``has_preview_truth`` — canonical preview state
      read fresh from the session store on both branches (chat-ran and
      STT-failed).  The enhancer refreshes the preview pane only when
      ``has_preview_truth=True`` so it never clobbers a still-active
      preview on a pre-chat failure.
    * ``stt``, ``tts``, ``tts_url``, ``speak_response_requested`` —
      per-subsystem status dicts; ``tts_url`` is ``None`` when TTS is
      not requested or synthesis failed (text stays authoritative).
    * ``stage_timings`` — bounded, truthful dict of per-stage wall-
      clock milliseconds (``upload_ms``, ``temp_write_ms``, ``stt_ms``,
      ``vera_ms``, ``tts_ms``, ``total_ms``).  ``None`` for a stage
      that did not run this turn (e.g. ``tts_ms`` without
      ``speak_response``).  Used by operator diagnostics to see where
      time is going; never used to gate behaviour.

    Earlier iterations carried ``classification``, ``lifecycle``,
    ``vera``, ``preview_attempt``, and ``show_action_guidance`` from
    the bespoke workbench pipeline.  Those are intentionally removed:
    the canonical chat helper is now the single source of truth, so
    the enhancer reads ``status`` + ``assistant_text`` + ``preview``
    directly instead of reconstructing state from sub-results.
    """
    # Stage timings are collected throughout this handler and surfaced
    # under ``payload["stage_timings"]`` so operators can see exactly
    # where time is going in a dictation round trip.  Every entry is a
    # wall-clock millisecond count; ``None`` means the stage did not
    # run this turn (e.g. ``tts_ms`` without ``speak_response``).
    #
    # The ``vera_*`` keys break the ``vera_ms`` umbrella down into the
    # two dominant LLM calls (preview builder + main reply) and the
    # optional web-enrichment LLM call, so operators can tell which
    # sub-stage actually owns a slow turn.  Sub-stage keys stay
    # ``None`` when the corresponding branch did not run (e.g. the
    # preview builder is skipped for informational/conversational
    # turns) — absence stays truthful, never fabricated.
    request_started_at_ms = int(time.time() * 1000)
    stage_timings: dict[str, int | None] = {
        "upload_ms": None,
        "temp_write_ms": None,
        "stt_ms": None,
        "vera_ms": None,
        "vera_preview_builder_ms": None,
        "vera_reply_ms": None,
        "vera_enrichment_ms": None,
        "tts_ms": None,
        "total_ms": None,
    }

    content_type_header = request.headers.get("content-type")
    if not _is_audio_content_type(content_type_header):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Dictation upload requires an audio/* Content-Type.",
        )

    # Early Content-Length gate — refuses giant bodies BEFORE they are
    # materialized by ``await request.body()``.  The gate is best-
    # effort: clients can omit or lie about Content-Length, so the
    # post-body length check below remains the authoritative cap.
    content_length_raw = request.headers.get("content-length")
    if content_length_raw:
        try:
            advertised_length = int(content_length_raw)
        except ValueError:
            advertised_length = -1
        if advertised_length > _VERA_DICTATION_MAX_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"Dictation upload exceeds {_VERA_DICTATION_MAX_BYTES} byte cap.",
            )

    qp = request.query_params
    session_id_raw = str(qp.get("session_id") or "").strip()
    language = str(qp.get("language") or "").strip() or None
    speak_response_raw = str(qp.get("speak_response") or "").strip().lower()
    speak_response = speak_response_raw in {"1", "true", "on", "yes"}

    active_session = (
        session_id_raw or (request.cookies.get("vera_session_id") or "").strip() or new_session_id()
    )

    try:
        voice_flags = load_voice_foundation_flags()
    except Exception as exc:
        return JSONResponse(
            {
                "ok": False,
                "status": "voice_flags_failed",
                "error": f"{type(exc).__name__}: {exc}",
                "session_id": active_session,
            },
            status_code=500,
        )

    # Voice-input gate — fail closed BEFORE writing a single byte to
    # disk when the runtime has voice input disabled.  The dictation
    # enhancer already hides the mic button in this case; a direct
    # client that bypasses the UI must get the same fail-closed
    # result without the audio ever landing on temp storage.
    if not voice_flags.voice_input_enabled:
        return JSONResponse(
            {
                "ok": False,
                "status": "voice_input_disabled",
                "error": "Voice input is disabled by runtime flags.",
                "session_id": active_session,
            },
            status_code=status.HTTP_403_FORBIDDEN,
        )

    upload_started_at_ms = int(time.time() * 1000)
    raw_body = await request.body()
    stage_timings["upload_ms"] = max(0, int(time.time() * 1000) - upload_started_at_ms)
    if not raw_body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty dictation upload body.",
        )
    if len(raw_body) > _VERA_DICTATION_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Dictation upload exceeds {_VERA_DICTATION_MAX_BYTES} byte cap.",
        )

    suffix = _audio_suffix_for(content_type_header)
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=_VERA_DICTATION_TEMP_PREFIX, suffix=suffix)

    # ── Step 1: STT ────────────────────────────────────────────────
    stt_ok = False
    transcript_text: str | None = None
    stt_dict: dict[str, object]
    try:
        try:
            temp_write_started_at_ms = int(time.time() * 1000)
            with os.fdopen(tmp_fd, "wb") as handle:
                handle.write(raw_body)
            stage_timings["temp_write_ms"] = max(
                0, int(time.time() * 1000) - temp_write_started_at_ms
            )
        except Exception:
            with contextlib.suppress(OSError):
                os.close(tmp_fd)
            raise
        try:
            start_ms = int(time.time() * 1000)
            stt_response = await transcribe_audio_file_async(
                audio_path=tmp_path,
                flags=voice_flags,
                language=language,
                session_id=active_session,
            )
            elapsed_ms = int(time.time() * 1000) - start_ms
            stage_timings["stt_ms"] = max(0, elapsed_ms)
            stt_ok = bool(stt_response.status == STT_STATUS_SUCCEEDED and stt_response.transcript)
            transcript_text = stt_response.transcript if stt_ok else None
            stt_dict = {
                "success": stt_ok,
                "status": stt_response.status,
                "display_status": _display_status_for_stt(stt_response.status, ok=stt_ok),
                "transcript": transcript_text,
                "language": stt_response.language if stt_ok else None,
                "backend": stt_response.backend,
                "error": stt_response.error if not stt_ok else None,
                "error_class": stt_response.error_class if not stt_ok else None,
                "audio_duration_ms": stt_response.audio_duration_ms,
                "inference_ms": stt_response.inference_ms,
                "elapsed_ms": elapsed_ms,
                "request_id": stt_response.request_id,
                "response_dict": stt_response_as_dict(stt_response),
            }
        except Exception as exc:
            stt_dict = {
                "success": False,
                "status": "failed",
                "display_status": "failed",
                "error": f"Unexpected error: {type(exc).__name__}: {exc}",
            }
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)

    # ── Step 2: canonical Vera chat turn (same helper as typed /chat) ──
    chat_result: ChatTurnResult | None = None
    if stt_ok and transcript_text:
        vera_started_at_ms = int(time.time() * 1000)
        chat_result = await run_vera_chat_turn(
            message=transcript_text,
            input_origin=InputOrigin.VOICE_TRANSCRIPT,
            session_id=active_session,
            voice_flags=voice_flags,
        )
        stage_timings["vera_ms"] = max(0, int(time.time() * 1000) - vera_started_at_ms)
        # Merge the Vera-internal sub-stage timings produced inside
        # ``run_vera_chat_turn`` (preview builder LLM call, main reply
        # LLM call, optional web enrichment LLM call) into the outer
        # dictation ``stage_timings`` so operator diagnostics can see
        # where the ``vera_ms`` umbrella actually went.  Each key is
        # inserted only when the corresponding sub-stage actually ran;
        # absence stays truthful.
        for sub_key, sub_ms in chat_result.stage_timings.items():
            if isinstance(sub_ms, int):
                stage_timings[sub_key] = sub_ms

    # ── Step 3: optional TTS over the assistant text the canonical path produced ──
    # Text is already authoritative at this point: ``chat_result.assistant_text``
    # and the per-turn session writes happened inside ``run_vera_chat_turn``.
    # TTS is strictly additive — if it fails or is slow, the JSON payload
    # still carries the assistant text, the fresh turns list, and the
    # canonical preview truth, so the enhancer can render the text reply
    # without waiting on audio.  Text stays authoritative even when TTS
    # fails.
    tts_dict: dict[str, object] | None = None
    tts_url: str | None = None
    tts_source_text = (
        chat_result.assistant_text.strip()
        if chat_result is not None and chat_result.assistant_text
        else ""
    )
    if speak_response and tts_source_text:
        try:
            start_ms = int(time.time() * 1000)
            tts_response = await synthesize_text_async(
                text=tts_source_text,
                flags=voice_flags,
                session_id=active_session,
            )
            elapsed_ms = int(time.time() * 1000) - start_ms
            stage_timings["tts_ms"] = max(0, elapsed_ms)
            tts_ok = bool(tts_response.status == TTS_STATUS_SUCCEEDED and tts_response.audio_path)
            tts_dict = {
                "success": tts_ok,
                "status": tts_response.status,
                "display_status": _display_status_for_tts(tts_response.status, ok=tts_ok),
                "audio_path": tts_response.audio_path if tts_ok else None,
                "backend": tts_response.backend,
                "error": tts_response.error if not tts_ok else None,
                "error_class": tts_response.error_class if not tts_ok else None,
                "audio_duration_ms": tts_response.audio_duration_ms,
                "inference_ms": tts_response.inference_ms,
                "elapsed_ms": elapsed_ms,
                "request_id": tts_response.request_id,
                "response_dict": tts_response_as_dict(tts_response),
            }
            if tts_ok and tts_response.audio_path:
                token = _register_tts_audio(tts_response.audio_path)
                tts_url = f"/vera/voice/audio/{token}"
        except ValueError as exc:
            # ``synthesize_text_async`` (via ``tts_protocol``) raises
            # ``ValueError`` for input-shape violations — empty text
            # after stripping, invalid ``output_format``, etc.  Surface
            # the validation message verbatim since it is operator-
            # readable; broader runtime failures fall through to the
            # generic ``Exception`` branch below with a class-tagged
            # message.  The STT block above has no equivalent split
            # because the audio bytes are already validated on the
            # route (content-type, size, non-empty body) before the
            # async call runs.
            tts_dict = {
                "success": False,
                "status": "failed",
                "display_status": "failed",
                "error": str(exc),
            }
        except Exception as exc:
            tts_dict = {
                "success": False,
                "status": "failed",
                "display_status": "failed",
                "error": f"Unexpected error: {type(exc).__name__}: {exc}",
            }

    # Resolve final payload fields from the canonical result (or fall
    # back to a turn-less session when STT failed before it could run).
    #
    # ``result_preview`` is always the CURRENT canonical preview on disk
    # for the active session — never a fabrication, never inferred from
    # the reply text.  When the chat helper ran, ``chat_result.preview``
    # already holds ``read_session_preview(...)`` captured right after
    # the lane writes completed.  When STT failed before chat ran, we
    # re-read the session so the dictation UI reflects whatever preview
    # state the session still truly holds (clobbering it to None would
    # misrepresent canonical truth).
    if chat_result is not None:
        result_session_id = chat_result.session_id
        result_turns = chat_result.turns
        result_status = chat_result.status
        result_error = chat_result.error
        result_assistant_text = chat_result.assistant_text
        result_preview = chat_result.preview
    else:
        root = _active_queue_root()
        result_session_id = active_session
        result_turns = read_session_turns(root, active_session)
        result_status = "stt_failed"
        result_error = str(stt_dict.get("error") or "")
        result_assistant_text = ""
        result_preview = read_session_preview(root, active_session)

    # ``ok`` stays truthful: STT must have succeeded AND the canonical
    # Vera helper must have produced a non-error result.  TTS failures
    # never flip ``ok`` off because text remains authoritative per the
    # trust model.
    ok = bool(stt_ok and chat_result is not None and not chat_result.error)

    # ``has_preview_truth`` is a small, explicit handshake with the
    # dictation enhancer's preview-pane hook: it is ``True`` whenever
    # ``preview`` in this payload faithfully reflects the current
    # canonical session preview (read fresh above in both branches),
    # which means the browser can safely replace the visible pane with
    # what the server just reported.  The flag exists so the UI never
    # has to infer "is this preview value authoritative?" from other
    # fields — truth is stated directly at the boundary.
    stage_timings["total_ms"] = max(0, int(time.time() * 1000) - request_started_at_ms)

    payload: dict[str, object] = {
        "ok": ok,
        "session_id": result_session_id,
        "status": result_status,
        "error": result_error,
        "turns": result_turns,
        "turn_count": len(result_turns),
        "assistant_text": result_assistant_text,
        "preview": result_preview,
        "has_preview_truth": True,
        "stt": stt_dict,
        "tts": tts_dict,
        "tts_url": tts_url,
        "speak_response_requested": speak_response,
        "stage_timings": stage_timings,
    }
    response = JSONResponse(payload)
    response.set_cookie("vera_session_id", result_session_id, httponly=False, samesite="lax")
    return response


@app.get("/vera/voice/audio/{token}")
async def vera_voice_audio(token: str) -> FileResponse:
    """Serve a previously-registered TTS audio artifact.

    The dictation pipeline registers successful TTS outputs under a
    short-lived random token; the browser fetches the audio through
    this route so the real temp-file path stays server-side.  An
    unknown or expired token 404s; anything else is served as
    ``audio/wav``.

    Kept ``async`` so registry reads and the POST-side writes both
    run on the event loop, avoiding get→check→pop→unlink interleaves
    between threadpool and coroutine under concurrent access.
    """
    clean = (token or "").strip()
    if not clean or not _TTS_TOKEN_ALPHABET_RE.match(clean):
        raise HTTPException(status_code=404, detail="Audio token not found.")
    path = _resolve_tts_token(clean)
    if path is None:
        raise HTTPException(status_code=404, detail="Audio token not found.")
    if not os.path.isfile(path):
        # The backing artifact vanished (disk cleanup, operator pruned,
        # process restart between registration and fetch). Drop the stale
        # registry entry so it cannot be re-resolved, then fail closed.
        _TTS_AUDIO_REGISTRY.pop(clean, None)
        raise HTTPException(status_code=404, detail="Audio artifact missing.")
    return FileResponse(path, media_type="audio/wav")


@app.get("/vera/debug/session.json")
def vera_debug_session_json(request: Request):
    """Operator-facing JSON debug endpoint for session context and routing state.

    Returns a bounded snapshot of session debug info, shared context ref values,
    and recent routing decisions.  This is a read-only observability surface —
    it does not alter any truth boundaries or session state.

    Access control: this endpoint has the same access posture as all other
    Vera web routes (no additional auth).  The Vera web service binds to
    127.0.0.1 by default, so access is limited to the local operator.
    If the service is ever exposed beyond localhost, add auth here.
    """
    session_id = str(request.query_params.get("session_id") or "").strip()
    active_session = session_id or (request.cookies.get("vera_session_id") or "").strip()
    if not active_session:
        return JSONResponse(
            content={
                "error": "no_session_id",
                "detail": "Provide session_id query param or cookie.",
            },
            status_code=400,
        )
    root = _active_queue_root()
    snapshot = session_debug_snapshot(root, active_session, mode_status="debug_inspection")
    return JSONResponse(content=snapshot)
