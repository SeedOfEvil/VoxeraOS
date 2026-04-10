from __future__ import annotations

import json
import re
from typing import Any

from ..brain.fallback import classify_fallback_reason
from ..brain.gemini import GeminiBrain
from ..brain.json_recovery import recover_json_object
from ..brain.openai_compat import OpenAICompatBrain
from ..config import load_app_config
from . import session_store as vera_session_store
from .brave_search import BraveSearchClient
from .investigation_flow import (
    build_structured_investigation_results,
    format_web_investigation_answer,
    is_informational_web_query,
    maybe_handle_investigation_turn,
    normalize_web_query,
)
from .linked_completions import (  # noqa: F401  — re-exported for caller compat
    ingest_linked_job_completions,
    maybe_auto_surface_linked_completion,
    maybe_deliver_linked_completion_live,
    maybe_deliver_linked_completion_live_for_job,
)
from .preview_drafting import drafting_guidance, maybe_draft_job_payload
from .preview_submission import normalize_preview_payload
from .prompt import VERA_PREVIEW_BUILDER_PROMPT, VERA_SYSTEM_PROMPT
from .saveable_artifacts import collect_recent_saveable_assistant_artifacts
from .weather import OpenMeteoWeatherClient, WeatherSnapshot
from .weather_flow import (
    extract_weather_followup_kind,
    extract_weather_location_from_message,
    is_weather_investigation_request,
    is_weather_question,
    maybe_handle_weather_turn,
    normalize_weather_location_candidate,
    weather_answer_for_followup,
    weather_context_has_pending_lookup,
    weather_context_is_waiting_for_location,
    weather_followup_is_active,
)

PREVIEW_BUILDER_MODEL = "gemini-3-flash-preview"
PREVIEW_BUILDER_FALLBACK_MODEL = "gemini-3.1-flash-lite-preview"


class HiddenCompilerDecision:
    def __init__(
        self,
        *,
        action: str,
        intent_type: str,
        updated_preview: dict[str, Any] | None = None,
        patch: dict[str, Any] | None = None,
    ) -> None:
        self.action = action
        self.intent_type = intent_type
        self.updated_preview = updated_preview
        self.patch = patch

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> HiddenCompilerDecision:
        allowed = {"action", "intent_type", "updated_preview", "patch"}
        if not set(payload).issubset(allowed):
            raise ValueError("hidden compiler decision contains unsupported keys")

        action = str(payload.get("action") or "").strip()
        intent_type = str(payload.get("intent_type") or "").strip()
        updated_preview = payload.get("updated_preview")
        patch = payload.get("patch")

        if action not in {"replace_preview", "patch_preview", "no_change"}:
            raise ValueError("action must be replace_preview, patch_preview, or no_change")
        if intent_type not in {"new_intent", "refinement", "unclear"}:
            raise ValueError("intent_type must be new_intent, refinement, or unclear")

        if action == "replace_preview":
            if not isinstance(updated_preview, dict):
                raise ValueError("replace_preview requires updated_preview object")
            if patch is not None:
                raise ValueError("replace_preview cannot include patch")
        elif action == "patch_preview":
            if not isinstance(patch, dict):
                raise ValueError("patch_preview requires patch object")
            if updated_preview is not None:
                raise ValueError("patch_preview cannot include updated_preview")
        else:
            if updated_preview is not None or patch is not None:
                raise ValueError("no_change cannot include updated_preview or patch")

        return cls(
            action=action,
            intent_type=intent_type,
            updated_preview=updated_preview if isinstance(updated_preview, dict) else None,
            patch=patch if isinstance(patch, dict) else None,
        )


def _service_weather_question(message: str) -> bool:
    try:
        return is_weather_question(
            message,
            is_weather_investigation_request_hook=is_weather_investigation_request,
        )
    except TypeError:
        return is_weather_question(message)


def _service_extract_weather_location_from_message(message: str) -> str | None:
    try:
        return extract_weather_location_from_message(
            message,
            normalize_weather_location_candidate_hook=normalize_weather_location_candidate,
        )
    except TypeError:
        return extract_weather_location_from_message(message)


async def _lookup_live_weather(
    location_query: str, *, followup_kind: str | None = None
) -> WeatherSnapshot:
    _ = followup_kind
    client = OpenMeteoWeatherClient()
    resolved = await client.resolve_location(location_query)
    if resolved is None:
        raise RuntimeError(
            "I couldn’t resolve that place into a structured weather location. Please give me a clearer location."
        )
    return await client.fetch_snapshot(resolved)


# Injected into the user message on code/script draft turns to override
# Vera's default "not the payload drafter" stance and actually produce code.
_CODE_DRAFT_HINT = (
    "\n\n[System note for this request: You are being asked to write a code or "
    "script file. Write the complete, working code directly in your response "
    "inside a properly-fenced code block (e.g. ```python\\n...\\n```). "
    "The fenced block will be automatically extracted and stored as a governed "
    "preview file for the user to review and submit. "
    "Produce complete, runnable code with necessary imports and idiomatic style. "
    "Include error handling for expected failure modes. "
    "If the user asked for a specific language or framework, follow its conventions.]"
)

_WRITING_DRAFT_HINT = (
    "\n\n[System note for this request: You are being asked to draft a prose document "
    "artifact. Write the actual essay/article/writeup/explanation body directly in your "
    "response. Avoid hidden control markup. If you include a short conversational wrapper, "
    "place the full draft body after a blank line so it can be extracted into the governed "
    "preview file. "
    "Honor the requested length and depth — if the user asked for a detailed or long piece, "
    "produce substantive content, not a skeletal outline. "
    "Use clear section structure for longer pieces. "
    "Respect the requested tone (formal, casual, technical, narrative).]"
)


def build_vera_messages(
    *,
    turns: list[dict[str, str]],
    user_message: str,
    code_draft: bool = False,
    writing_draft: bool = False,
) -> list[dict[str, str]]:
    if code_draft and writing_draft:
        raise ValueError("code_draft and writing_draft are mutually exclusive")
    messages: list[dict[str, str]] = [{"role": "system", "content": VERA_SYSTEM_PROMPT}]
    for turn in turns[-vera_session_store.MAX_SESSION_TURNS :]:
        messages.append({"role": turn["role"], "content": turn["text"]})
    content = user_message.strip()
    if code_draft:
        content = content + _CODE_DRAFT_HINT
    elif writing_draft:
        content = content + _WRITING_DRAFT_HINT
    messages.append({"role": "user", "content": content})
    return messages


def _recent_assistant_authored_content(turns: list[dict[str, str]]) -> list[str]:
    non_authored_markers = (
        "i submitted the job to voxeraos",
        "job id:",
        "the request is now in the queue",
        "execution has not completed yet",
        "check status and evidence",
        "approval status",
        "expected artifacts",
        "queue state",
    )
    authored: list[str] = []
    for turn in turns[-vera_session_store.MAX_SESSION_TURNS :]:
        if str(turn.get("role") or "").strip().lower() != "assistant":
            continue
        text = str(turn.get("text") or "").strip()
        lowered = text.lower()
        if not text:
            continue
        if any(marker in lowered for marker in non_authored_markers):
            continue
        normalized = re.sub(r"\s+", " ", lowered.replace("—", "-")).strip()
        if any(
            normalized.startswith(prefix)
            for prefix in (
                "you're welcome",
                "youre welcome",
                "you're very welcome",
                "youre very welcome",
                "no problem",
                "anytime",
                "of course",
                "sure thing",
                "glad to help",
                "happy to help",
                "my pleasure",
            )
        ) and (
            len(normalized.split()) <= 24
            or any(
                phrase in normalized
                for phrase in (
                    "if you'd like",
                    "if you would like",
                    "let me know",
                    "feel free",
                    "i can save that",
                    "i can also",
                )
            )
        ):
            continue
        authored.append(text)
    return authored[-4:]


def _build_preview_builder_messages(
    *,
    turns: list[dict[str, str]],
    user_message: str,
    active_preview: dict[str, Any] | None,
    enrichment_context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    guidance = drafting_guidance()
    context_payload: dict[str, Any] = {
        "active_preview": active_preview,
        "latest_user_message": user_message.strip(),
        "recent_turns": turns[-vera_session_store.MAX_SESSION_TURNS :],
        "recent_assistant_authored_content": _recent_assistant_authored_content(turns),
        "decision_contract": {
            "action": ["replace_preview", "patch_preview", "no_change"],
            "intent_type": ["new_intent", "refinement", "unclear"],
            "updated_preview": "object | null",
            "patch": "object | null",
        },
        "preview_schema": {
            "goal": "required string",
            "title": "optional string",
            "write_file": {
                "path": "required string",
                "content": "required string",
                "mode": "overwrite | append",
            },
            "enqueue_child": {
                "goal": "required string",
                "title": "optional string",
            },
            "file_organize": {
                "source_path": "required string (~/VoxeraOS/notes/ scope)",
                "destination_dir": "required string (~/VoxeraOS/notes/ scope)",
                "mode": "copy | move",
                "overwrite": "boolean (default false)",
                "delete_original": "boolean (default false)",
            },
            "steps": "optional array of {skill_id, args} for direct bounded file skill routing",
        },
        "guidance_base_shape": guidance.base_shape,
        "guidance_examples": guidance.examples,
    }
    if enrichment_context is not None:
        context_payload["enrichment_context"] = enrichment_context
    return [
        {"role": "system", "content": VERA_PREVIEW_BUILDER_PROMPT},
        {
            "role": "user",
            "content": json.dumps(context_payload, ensure_ascii=False),
        },
    ]


def _extract_hidden_compiler_decision(text: str) -> HiddenCompilerDecision | None:
    parsed, _ = recover_json_object(text)
    if not isinstance(parsed, dict):
        return None
    try:
        return HiddenCompilerDecision.from_payload(parsed)
    except ValueError:
        return None


def _apply_preview_patch(
    *,
    active_preview: dict[str, Any] | None,
    patch: dict[str, Any],
) -> dict[str, Any] | None:
    if active_preview is None:
        return None
    merged: dict[str, Any] = dict(active_preview)
    for key, value in patch.items():
        if key == "write_file" and isinstance(value, dict):
            current = merged.get("write_file")
            if isinstance(current, dict):
                merged["write_file"] = {**current, **value}
                continue
        if key == "enqueue_child" and isinstance(value, dict):
            current = merged.get("enqueue_child")
            if isinstance(current, dict):
                merged["enqueue_child"] = {**current, **value}
                continue
        merged[key] = value
    return merged


async def generate_preview_builder_update(
    *,
    turns: list[dict[str, str]],
    user_message: str,
    active_preview: dict[str, Any] | None,
    enrichment_context: dict[str, Any] | None = None,
    investigation_context: dict[str, Any] | None = None,
    recent_assistant_artifacts: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    cfg = load_app_config()
    api_key_ref = None
    if cfg.brain:
        for key in ("primary", "fallback", "fast", "reasoning"):
            provider = cfg.brain.get(key)
            if provider is not None and provider.api_key_ref:
                api_key_ref = provider.api_key_ref
                break

    attempts: list[GeminiBrain] = []
    if api_key_ref:
        attempts.append(GeminiBrain(model=PREVIEW_BUILDER_MODEL, api_key_ref=api_key_ref))
        if PREVIEW_BUILDER_FALLBACK_MODEL != PREVIEW_BUILDER_MODEL:
            attempts.append(
                GeminiBrain(model=PREVIEW_BUILDER_FALLBACK_MODEL, api_key_ref=api_key_ref)
            )

    recent_user_messages = [
        str(turn.get("text") or "")
        for turn in turns[-vera_session_store.MAX_SESSION_TURNS :]
        if str(turn.get("role") or "").strip().lower() == "user"
    ]
    recent_assistant_messages = [
        str(turn.get("text") or "")
        for turn in turns[-vera_session_store.MAX_SESSION_TURNS :]
        if str(turn.get("role") or "").strip().lower() == "assistant"
    ]

    deterministic_preview = maybe_draft_job_payload(
        user_message,
        active_preview=active_preview,
        recent_user_messages=recent_user_messages,
        enrichment_context=enrichment_context,
        investigation_context=investigation_context,
        recent_assistant_messages=recent_assistant_messages,
        recent_assistant_artifacts=(
            recent_assistant_artifacts
            if recent_assistant_artifacts is not None
            else collect_recent_saveable_assistant_artifacts(recent_assistant_messages)
        ),
    )

    if not attempts:
        if deterministic_preview is None:
            return None
        try:
            return normalize_preview_payload(deterministic_preview)
        except Exception:
            return None

    messages = _build_preview_builder_messages(
        turns=turns,
        user_message=user_message,
        active_preview=active_preview,
        enrichment_context=enrichment_context,
    )

    for brain in attempts:
        try:
            response = await brain.generate(messages, tools=[])
        except Exception:
            continue
        decision = _extract_hidden_compiler_decision(str(response.text or ""))
        if decision is None:
            continue
        candidate: dict[str, Any] | None = None
        if decision.action == "no_change":
            if deterministic_preview is not None:
                try:
                    return normalize_preview_payload(deterministic_preview)
                except Exception:
                    return active_preview
            return active_preview
        if decision.action == "replace_preview":
            candidate = decision.updated_preview
        elif decision.action == "patch_preview" and decision.patch is not None:
            candidate = _apply_preview_patch(active_preview=active_preview, patch=decision.patch)

        if candidate is None:
            if deterministic_preview is None:
                return active_preview
            try:
                return normalize_preview_payload(deterministic_preview)
            except Exception:
                return active_preview

        try:
            return normalize_preview_payload(candidate)
        except Exception:
            if deterministic_preview is None:
                return active_preview
            try:
                return normalize_preview_payload(deterministic_preview)
            except Exception:
                return active_preview

    if deterministic_preview is None:
        return active_preview
    try:
        return normalize_preview_payload(deterministic_preview)
    except Exception:
        return active_preview


def _create_brain(provider: Any) -> OpenAICompatBrain | GeminiBrain:
    if provider.type == "openai_compat":
        return OpenAICompatBrain(
            model=provider.model,
            base_url=provider.base_url or "https://openrouter.ai/api/v1",
            api_key_ref=provider.api_key_ref,
            extra_headers=provider.extra_headers,
        )
    if provider.type == "gemini":
        return GeminiBrain(model=provider.model, api_key_ref=provider.api_key_ref)
    raise ValueError(f"unsupported provider type: {provider.type}")


async def generate_vera_reply(
    *,
    turns: list[dict[str, str]],
    user_message: str,
    code_draft: bool = False,
    writing_draft: bool = False,
    weather_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = load_app_config()
    web_cfg = cfg.web_investigation

    weather_reply = await maybe_handle_weather_turn(
        user_message=user_message,
        weather_context=weather_context,
        code_draft=code_draft,
        writing_draft=writing_draft,
        lookup_weather=_lookup_live_weather,
        lookup_weather_followup=lambda location_query, followup_kind: _lookup_live_weather(
            location_query,
            followup_kind=followup_kind,
        ),
        is_weather_investigation_request_hook=is_weather_investigation_request,
        extract_weather_followup_kind_hook=extract_weather_followup_kind,
        is_weather_question_hook=_service_weather_question,
        extract_weather_location_from_message_hook=_service_extract_weather_location_from_message,
        weather_followup_is_active_hook=weather_followup_is_active,
        weather_context_has_pending_lookup_hook=weather_context_has_pending_lookup,
        weather_context_is_waiting_for_location_hook=weather_context_is_waiting_for_location,
        normalize_weather_location_candidate_hook=normalize_weather_location_candidate,
        weather_answer_for_followup_hook=weather_answer_for_followup,
    )
    if weather_reply is not None:
        return weather_reply

    investigation_reply = await maybe_handle_investigation_turn(
        user_message=user_message,
        web_cfg=web_cfg,
        is_informational_web_query_hook=is_informational_web_query,
        normalize_web_query_hook=normalize_web_query,
        format_web_investigation_answer_hook=format_web_investigation_answer,
        build_structured_investigation_results_hook=(
            lambda query, results: build_structured_investigation_results(
                query=query,
                results=results,
            )
        ),
        brave_client_factory=BraveSearchClient,
    )
    if investigation_reply is not None:
        return investigation_reply

    attempts: list[tuple[str, Any]] = []
    for key in ("primary", "fallback"):
        provider = cfg.brain.get(key) if cfg.brain else None
        if provider is not None:
            attempts.append((key, provider))

    if not attempts:
        return {
            "answer": (
                "I’m in conversation-only mode right now because no model provider is configured. "
                "I can still help draft a VoxeraOS job request preview, but I cannot execute anything here."
            ),
            "status": "degraded_unavailable",
        }

    messages = build_vera_messages(
        turns=turns,
        user_message=user_message,
        code_draft=code_draft,
        writing_draft=writing_draft,
    )
    last_reason = "UNKNOWN"
    for name, provider in attempts:
        try:
            brain = _create_brain(provider)
            response = await brain.generate(messages, tools=[])
            text = str(response.text or "").strip()
            if text:
                return {"answer": text, "status": f"ok:{name}"}
        except Exception as exc:
            last_reason = classify_fallback_reason(exc)
            continue

    return {
        "answer": (
            "I couldn’t reach the current model backend, so I’m staying in safe preview mode. "
            "I can help you shape a VoxeraOS queue job request, but nothing has been executed."
        ),
        "status": f"degraded_error:{last_reason}",
    }
