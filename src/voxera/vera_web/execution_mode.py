from __future__ import annotations

import enum
import re
from collections.abc import Callable


class ExecutionMode(enum.Enum):
    """Execution mode for a Vera chat turn.

    Decided early from the user message and enforced globally — once a turn is
    classified as CONVERSATIONAL_ARTIFACT it **must never** leak preview, draft,
    submit, or queue language.
    """

    CONVERSATIONAL_ARTIFACT = "conversational_artifact"
    GOVERNED_PREVIEW = "governed_preview"


def _is_voxera_control_turn(
    message: str,
    *,
    active_preview: dict[str, object] | None,
    is_text_draft_preview: Callable[[dict[str, object] | None], bool],
    is_recent_assistant_content_save_request: Callable[[str], bool],
    is_natural_preview_submission_confirmation: Callable[[str], bool],
    is_preview_submission_request: Callable[[str], bool],
    maybe_draft_job_payload: Callable[..., dict[str, object] | None],
) -> bool:
    if active_preview is not None and not is_text_draft_preview(active_preview):
        return True
    if is_recent_assistant_content_save_request(message):
        return True
    if is_natural_preview_submission_confirmation(message):
        return True
    if is_preview_submission_request(message):
        return True
    return maybe_draft_job_payload(message, active_preview=None) is not None


def _is_governed_writing_preview(
    preview: dict[str, object] | None,
    *,
    is_text_draft_preview: Callable[[dict[str, object] | None], bool],
) -> bool:
    if not isinstance(preview, dict):
        return False
    if is_text_draft_preview(preview):
        goal = str(preview.get("goal") or "").strip().lower()
        return bool(
            re.match(r"draft a (essay|article|writeup|explanation) as ", goal, re.IGNORECASE)
        )

    write_file = preview.get("write_file")
    if not isinstance(write_file, dict):
        return False
    path = str(write_file.get("path") or "").strip().lower()
    if not path or not path.endswith((".md", ".txt")):
        return False
    goal = str(preview.get("goal") or "").strip().lower()
    return bool(re.match(r"draft a (essay|article|writeup|explanation) as ", goal, re.IGNORECASE))


def _is_refinable_prose_preview(
    preview: dict[str, object] | None,
    *,
    is_text_draft_preview: Callable[[dict[str, object] | None], bool],
) -> bool:
    if _is_governed_writing_preview(preview, is_text_draft_preview=is_text_draft_preview):
        return True
    if not isinstance(preview, dict):
        return False
    write_file = preview.get("write_file")
    if not isinstance(write_file, dict):
        return False
    path = str(write_file.get("path") or "").strip().lower()
    return bool(path) and path.endswith((".md", ".txt"))


def _is_relative_writing_refinement_request(message: str) -> bool:
    text = message.strip()
    if not text:
        return False
    return bool(
        re.search(
            r"\b(more\s+formal|less\s+formal|more\s+casual|shorter|longer)\b",
            text,
            re.IGNORECASE,
        )
    )


def _looks_like_active_preview_content_generation_turn(
    message: str,
    *,
    looks_like_preview_rename_or_save_as_request: Callable[[str], bool],
    message_requests_referenced_content: Callable[[str], bool],
) -> bool:
    text = message.strip()
    if not text:
        return False
    lowered = text.lower()
    generation_signal = re.search(
        r"\b(tell|give|write|draft|create|generate|compose|share)\b", lowered
    )
    content_shape_signal = re.search(
        r"\b(joke|poem|story|paragraph|content|text|message|bio|summary|explanation|fact|note|writeup|write-up)\b",
        lowered,
    )
    has_naming_mutation_phrase = looks_like_preview_rename_or_save_as_request(text)
    if has_naming_mutation_phrase and not (generation_signal and content_shape_signal):
        return False
    references_prior_content = message_requests_referenced_content(text)
    if references_prior_content and not (generation_signal and content_shape_signal):
        return False
    return bool(generation_signal and content_shape_signal)


def _message_has_explicit_content_literal(message: str) -> bool:
    text = message.strip()
    if not text:
        return False
    return bool(
        re.search(r"\"[^\"]+\"|'[^']+'", text)
        or re.search(
            r"\b("
            r"containing\s+exactly\s*:|"
            r"with\s+(?:the\s+)?(?:content|text)\b|"
            r"(?:content|text)\s*:|"
            r"as\s+content\s+add\b|"
            r"add\s+content\s+to\b|"
            r"put\s+.+?\s+(?:inside|in|into)\s+(?:it|the\s+file)\b"
            r")",
            text,
            re.IGNORECASE,
        )
    )


def _looks_like_ambiguous_active_preview_content_replacement_request(message: str) -> bool:
    text = message.strip()
    if not text:
        return False
    lowered = text.lower()
    if not re.search(r"\b(add|use|replace|change|update|make)\b", lowered):
        return False
    if not re.search(r"\b(content|text|file|note)\b", lowered):
        return False
    if not re.search(r"\b(that|this|it|previous|last)\b", lowered):
        return False
    if re.search(r"\"[^\"]+\"|'[^']+'", text):
        return False
    return not re.search(
        r"\b(joke|summary|answer|response|explanation|paragraph|story|poem)\b", lowered
    )


def _extract_save_as_text_target(message: str) -> str | None:
    match = re.search(
        r"\bsave\s+(?:it|this|that)?\s*as\s+([a-zA-Z0-9_.-]+\.(?:md|txt))\b",
        message,
        re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1).strip()


def _classify_execution_mode(
    message: str,
    *,
    prior_planning_active: bool,
    pending_preview: dict[str, object] | None,
    should_use_conversational_artifact_mode: Callable[..., bool],
    is_recent_assistant_content_save_request: bool,
) -> ExecutionMode:
    """Classify the execution mode for this turn.

    This is decided EARLY and enforced GLOBALLY.  Once a turn is classified as
    CONVERSATIONAL_ARTIFACT, every downstream path must respect it:
    - preview builder is skipped
    - heavy guardrails are bypassed
    - control-reply suppression is bypassed
    - hard sanitization strips any surviving preview language
    """
    is_answer_first = should_use_conversational_artifact_mode(
        message,
        prior_planning_active=prior_planning_active,
        pending_preview=pending_preview,
        is_recent_assistant_content_save_request=is_recent_assistant_content_save_request,
    )
    return (
        ExecutionMode.CONVERSATIONAL_ARTIFACT if is_answer_first else ExecutionMode.GOVERNED_PREVIEW
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
