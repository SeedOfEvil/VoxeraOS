from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.file_intent import classify_bounded_file_intent
from ..core.writing_draft_intent import classify_writing_draft_intent
from .draft_revision import (
    extract_content_after_markers as _extract_content_after_markers,
)
from .draft_revision import extract_quoted_content as _extract_quoted_content
from .draft_revision import interpret_active_preview_draft_revision
from .draft_revision import (
    normalize_refinement_content_candidate as _normalize_refinement_content_candidate,
)
from .investigation_derivations import draft_investigation_save_preview
from .saveable_artifacts import (
    collect_recent_saveable_assistant_artifacts,
    looks_like_ambiguous_reference_only,
    looks_like_plural_reference_request,
    message_requests_referenced_content,
    select_recent_saveable_assistant_artifact,
)

_SAFE_SERVICE_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,120}\.service$", re.IGNORECASE)
_BROAD_DIAGNOSTICS_PATTERNS = (
    r"\binspect\s+system\s+health\b",
    r"\brun\s+diagnostics\b",
    r"\bshow\s+host\s+diagnostics\b",
    r"\bcollect\s+system\s+diagnostics\b",
)
_TARGETED_DIAGNOSTICS_PATTERNS = (
    r"\b(check|show)\s+disk\s+usage\b",
    r"\b(show|check)\s+memory\s+usage\b",
    r"\b(show|check)\s+system\s+load\b",
)
_SERVICE_STATUS_PATTERNS = (
    r"\b(?:check|show|get|inspect)(?:\s+me)?\s+(?:the\s+)?status\s+(?:of|for)\s+([A-Za-z0-9_.@\-/]+)",
    r"\bstatus\s+(?:of|for)\s+([A-Za-z0-9_.@\-/]+)",
)
_SERVICE_LOG_PATTERNS = (
    r"\b(?:show|fetch|get|summari[sz]e)(?:\s+me)?\s+(?:the\s+)?(?:recent\s+)?logs\s+(?:for|of)\s+([A-Za-z0-9_.@\-/]+)",
    r"\brecent\s+logs\s+(?:for|of)\s+([A-Za-z0-9_.@\-/]+)",
)

_DOMAIN_RE = re.compile(r"\b([a-z0-9-]+(?:\.[a-z0-9-]+)+)(/[^\s]*)?\b", re.IGNORECASE)
_URL_RE = re.compile(r"\bhttps?://[^\s)]+", re.IGNORECASE)
_WEB_ACTION_RE = re.compile(
    r"\b(open|go\s+to|visit|take\s+me\s+to|bring\s+up|load|launch|navigate\s+to)\b",
    re.IGNORECASE,
)
_INFO_ONLY_RE = re.compile(
    r"\b(what\s+is|tell\s+me\s+about|summari[sz]e|explain|what\s+does\s+this\s+link\s+mean)\b",
    re.IGNORECASE,
)
_FILE_PATH_RE = re.compile(r"(?:~|/)[^\s]+")
_BARE_WEB_TARGET_RE = re.compile(
    r"\b(?:open|go\s+to|visit|take\s+me\s+to|bring\s+up|load|launch|navigate\s+to)\s+([a-z0-9-]{2,})(?:\b|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DraftingGuidance:
    base_shape: dict[str, str]
    examples: list[dict[str, Any]]


def _normalize_open_goal(message: str) -> str | None:
    text = message.strip()
    if not _WEB_ACTION_RE.search(text):
        return None
    if _INFO_ONLY_RE.search(text):
        return None
    if re.search(r"\bfile\b", text, re.IGNORECASE):
        return None
    explicit = _URL_RE.search(text)
    if explicit:
        return f"open {explicit.group(0)}"
    bare = _DOMAIN_RE.search(text)
    if bare:
        host = bare.group(1)
        suffix = bare.group(2) or ""
        return f"open https://{host}{suffix}"
    bare_target = _BARE_WEB_TARGET_RE.search(text)
    if bare_target:
        target = bare_target.group(1).strip().lower()
        if target not in {"a", "an", "the", "this", "that", "it", "me", "for"}:
            return f"open https://{target}.com"
    return None


def _normalize_file_read_goal(message: str) -> str | None:
    text = message.strip()
    if not re.search(
        r"\b(read|open|inspect|show\s+me|pull\s+up|look\s+at|examine)\b", text, re.IGNORECASE
    ):
        return None
    path_match = _FILE_PATH_RE.search(text)
    if path_match:
        return f"read the file {path_match.group(0)}"
    if re.search(r"\b(this\s+file|the\s+file)\b", text, re.IGNORECASE):
        return "read this file"
    return None


def diagnostics_service_or_logs_intent(message: str) -> bool:
    return _extract_safe_service(message, _SERVICE_STATUS_PATTERNS) not in {
        None,
        "",
    } or _extract_safe_service(message, _SERVICE_LOG_PATTERNS) not in {None, ""}


def diagnostics_request_refusal(message: str) -> str | None:
    lowered = message.strip().lower()
    if not lowered:
        return None

    candidate: str | None = None
    for pattern in (*_SERVICE_STATUS_PATTERNS, *_SERVICE_LOG_PATTERNS):
        match = re.search(pattern, lowered, re.IGNORECASE)
        if match:
            candidate = (match.group(1) or "").strip(" .,!?;:'\"`")
            break

    if candidate is None:
        return None

    if _SAFE_SERVICE_RE.fullmatch(candidate):
        return None

    looks_like_service_target = ".service" in candidate
    looks_path_like_or_unsafe = "/" in candidate or "\\" in candidate or ".." in candidate
    if not (looks_like_service_target or looks_path_like_or_unsafe):
        return None

    return (
        "I refused that diagnostics request because the service target is unsafe or invalid. "
        "Use an explicit bounded unit name like voxera-daemon.service."
    )


def _extract_safe_service(message: str, patterns: tuple[str, ...]) -> str | None:
    text = message.strip().lower()
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        raw = (match.group(1) or "").strip(" .,!?;:'\"`")
        if _SAFE_SERVICE_RE.fullmatch(raw):
            return raw
        return ""
    return None


def _normalize_diagnostics_preview(message: str) -> dict[str, Any] | None:
    text = message.strip().lower()
    if not text:
        return None

    if any(re.search(p, text, re.IGNORECASE) for p in _BROAD_DIAGNOSTICS_PATTERNS):
        return {
            "goal": "run bounded host diagnostics via the diagnostics mission",
            "mission_id": "system_diagnostics",
        }

    if any(re.search(p, text, re.IGNORECASE) for p in _TARGETED_DIAGNOSTICS_PATTERNS):
        return {
            "goal": "run bounded host diagnostics for requested system metrics",
            "mission_id": "system_diagnostics",
        }

    status_service = _extract_safe_service(message, _SERVICE_STATUS_PATTERNS)
    if status_service == "":
        return None
    if isinstance(status_service, str):
        return {
            "goal": f"check status of {status_service} using bounded diagnostics",
            "steps": [
                {
                    "skill_id": "system.service_status",
                    "args": {"service": status_service},
                }
            ],
        }

    log_service = _extract_safe_service(message, _SERVICE_LOG_PATTERNS)
    if log_service == "":
        return None
    if isinstance(log_service, str):
        return {
            "goal": f"inspect recent logs for {log_service} using bounded diagnostics",
            "steps": [
                {
                    "skill_id": "system.recent_service_logs",
                    "args": {"service": log_service, "lines": 50, "since_minutes": 15},
                }
            ],
        }

    return None


def is_recent_assistant_content_save_request(message: str) -> bool:
    lowered = message.strip().lower()
    if not lowered:
        return False
    save_signal = bool(re.search(r"\b(save|write|put|create|make)\b", lowered))
    target_signal = bool(
        re.search(r"\b(file|note|notes|markdown|artifact|\.md\b|\.txt\b)\b", lowered)
    ) or bool(re.search(r"\bsave\s+(?:that|this|it)\b", lowered))
    reference_signal = message_requests_referenced_content(
        lowered
    ) or looks_like_plural_reference_request(lowered)
    return save_signal and target_signal and reference_signal


def _infer_content_from_message(text: str) -> str | None:
    lowered = text.lower()
    if re.search(r"\b(joke|funny|humorous)\b", lowered):
        return "Why did the developer go broke? Because they used up all their cache."
    reminder = re.search(r"\b(?:about|to)\s+(.+)$", text, re.IGNORECASE)
    if reminder and re.search(r"\b(remind|reminder|note\s+for\s+later)\b", lowered):
        subject = reminder.group(1).strip(" .'\"`?!")
        if subject:
            return f"Reminder: {subject}"
    if re.search(r"\bremind\s+me\b", lowered):
        return "Reminder"
    return None


def _generated_note_path() -> str:
    return f"~/VoxeraOS/notes/note-{int(time.time())}.txt"


def _normalize_structured_file_write_payload(
    message: str,
    *,
    assistant_artifacts: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    text = message.strip().rstrip("?.!")
    lowered = text.lower()
    append_mode = bool(re.search(r"\b(append|add\s+to)\b", lowered))
    if not re.search(r"\b(write|create|save|put|make|append|add|build)\b", lowered):
        return None
    if not (
        re.search(r"\b(file|note|\w+\.[a-z0-9]{1,8})\b", lowered)
        or message_requests_referenced_content(text)
    ):
        return None

    if append_mode:
        append_target = re.search(r"\bto\s+([^\s]+\.[a-zA-Z0-9]{1,8})\b", text, re.IGNORECASE)
        target = append_target.group(1).strip("\"'`:,.") if append_target else None
        if not target:
            return None
        content = _extract_quoted_content(text)
        if content is None:
            tail = re.search(r"\bappend\s+(.+?)\s+to\s+[^\s]+", text, re.IGNORECASE)
            content = tail.group(1).strip(" \"'`:") if tail else None
        if content is None:
            return None
        normalized_path = (
            target
            if target.startswith("~") or target.startswith("/")
            else f"~/VoxeraOS/notes/{target}"
        )
        return {
            "goal": f"append to a file called {target} with provided content",
            "write_file": {"path": normalized_path, "content": content, "mode": "append"},
        }

    direct = re.search(
        r"\b(?:write|create|make|append|build)\s+(?:a\s+)?(?:file\s+)?([a-zA-Z0-9_.-]+\.[a-zA-Z0-9]{1,8})\b",
        text,
        re.IGNORECASE,
    )
    target = direct.group(1).strip("\"'") if direct else None
    if not target:
        save_as_or_to = re.search(
            r"\bsave\s+(?:that|this|it|(?:the\s+)?previous\s+content|previous\s+content)?\s*(?:as|to)\s+([~\/a-zA-Z0-9_.-]+\.[a-zA-Z0-9]{1,8})\b",
            text,
            re.IGNORECASE,
        )
        target = save_as_or_to.group(1).strip("\"'") if save_as_or_to else None
    if not target:
        named = re.search(r"\b(?:called|call\w*|named)\s+([^\s]+)", text, re.IGNORECASE)
        target = named.group(1).strip("\"'") if named else None
    generated_target_path = _generated_note_path()
    generated_target_name = Path(generated_target_path).name

    content = _extract_quoted_content(text)
    if content is None:
        content = _extract_content_after_markers(
            text,
            (
                r"\bwith\s+(?:exactly\s+)?this\s+(?:content|text)\s*:\s*(.+)$",
                r"\bwith\s+(?:the\s+)?(?:content|text)\s*:\s*(.+)$",
                r"\b(?:content|text)\s*:\s*(.+)$",
            ),
        )
    if content is None:
        patterns = (
            r"\b(?:with\s+(?:the\s+)?)?(?:content|text)\s+(.+)$",
            r"\bas\s+content\s+add\s+(.+)$",
            r"\badd\s+content\s+to\s+[^\s]+\s+(?:saying|with)?\s*(.+)$",
            r"\bput\s+(.+?)\s+(?:inside|in|into)\s+(?:it|the\s+file)\b",
            r"\bmake\s+[^\s]+\s+and\s+add\s+(.+)$",
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue
            candidate = _normalize_refinement_content_candidate(match.group(1))
            if candidate:
                content = candidate
                break
    reference_requested = message_requests_referenced_content(text)
    ambiguous_reference = looks_like_ambiguous_reference_only(text)
    plural_reference = looks_like_plural_reference_request(text)
    clear_generation_request = bool(
        re.search(r"\b(tell|give|write|draft|create|generate|compose|share)\b", lowered)
        and re.search(
            r"\b(joke|funny|humorous|poem|story|paragraph|content|text|message|bio|summary|explanation|remind|reminder|note\s+for\s+later)\b",
            lowered,
        )
    )
    if not target and not (reference_requested or ambiguous_reference or plural_reference):
        return None
    if content is None:
        referenced_artifact = select_recent_saveable_assistant_artifact(
            message=text,
            assistant_artifacts=assistant_artifacts,
        )
        content = (
            str(referenced_artifact.get("content") or "").strip()
            if isinstance(referenced_artifact, dict)
            else None
        )
    if content is None and (reference_requested or ambiguous_reference or plural_reference):
        if clear_generation_request:
            # For same-turn generate+save intents, create a governed preview shell
            # and let post-reply binding inject the authoritative authored body.
            content = ""
        else:
            return None
    if content is None:
        content = _infer_content_from_message(text) or ""
    if not target:
        target = generated_target_name

    normalized_path = target
    if not target.startswith("~") and not target.startswith("/"):
        normalized_path = (
            generated_target_path
            if target == generated_target_name
            else f"~/VoxeraOS/notes/{target}"
        )

    mode = "overwrite"
    goal_prefix = "write a file"
    return {
        "goal": f"{goal_prefix} called {target} with provided content",
        "write_file": {"path": normalized_path, "content": content, "mode": mode},
    }


def _normalize_structured_note_payload(message: str) -> dict[str, Any] | None:
    text = message.strip().rstrip("?.!")
    lowered = text.lower()
    if not re.search(r"\b(note|remind|reminder)\b", lowered):
        return None

    if not re.search(r"\b(write|create|make|build|save|jot|remind)\b", lowered):
        return None

    topic = None
    about = re.search(r"\babout\s+(.+)$", text, re.IGNORECASE)
    if about:
        topic = about.group(1).strip(" .'\"`?!")

    if topic:
        return {
            "goal": f"write a note about {topic}",
            "write_file": {
                "path": _generated_note_path(),
                "content": f"Reminder: {topic}",
                "mode": "overwrite",
            },
        }

    if re.search(
        r"\b(note\s+for\s+later|make\s+me\s+(?:a\s+)?note|write\s+me\s+(?:a\s+)?note)\b", lowered
    ):
        return {
            "goal": "write a note",
            "write_file": {"path": _generated_note_path(), "content": "", "mode": "overwrite"},
        }

    return None


def _normalize_file_write_goal(message: str) -> str | None:
    text = message.strip().rstrip("?.!")
    lowered = text.lower()
    file_match = re.search(
        r"\b(?:write|make|create)\s+(?:a\s+)?(?:note|file)\s+called\s+([^\s]+)",
        lowered,
    )
    if file_match:
        return f"write a note called {file_match.group(1)}"
    note_to_match = re.search(r"\bmake\s+a\s+note\s+to\s+(.+)$", lowered)
    if note_to_match:
        return f"write a note to {note_to_match.group(1).strip()}"
    if re.search(r"\b(?:make|create|write)\s+me\s+(?:a\s+)?note\b", lowered) or re.search(
        r"\bnote\s+for\s+later\b", lowered
    ):
        return "write a note"
    if re.search(
        r"\b(write\s+this\s+down|jot\s+this\s+down|save\s+this\s+as\s+a\s+note)\b", lowered
    ):
        return "write a note"
    return None


def _draft_revision_from_active_preview(
    message: str,
    active_preview: dict[str, Any] | None,
    *,
    enrichment_context: dict[str, Any] | None = None,
    assistant_artifacts: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(active_preview, dict):
        return None
    text = message.strip().rstrip("?.!")
    lowered = text.lower()

    url_match = _URL_RE.search(text) or _DOMAIN_RE.search(text)
    current_goal = str(active_preview.get("goal") or "")
    if (
        url_match
        and current_goal.startswith("open ")
        and re.search(r"\b(actually|instead|change|switch)\b", lowered)
    ):
        normalized_open = _normalize_open_goal(f"open {url_match.group(0)}")
        if normalized_open:
            return {"goal": normalized_open}

    return interpret_active_preview_draft_revision(
        message,
        active_preview,
        enrichment_context=enrichment_context,
        assistant_artifacts=assistant_artifacts,
    )


def drafting_guidance() -> DraftingGuidance:
    return DraftingGuidance(
        base_shape={"goal": "..."},
        examples=[
            {"goal": "open https://example.com"},
            {"goal": "read the file ~/VoxeraOS/notes/stv-child-target.txt"},
            {"goal": "write a note called hello.txt"},
            {
                "goal": "write a file called hello.txt with provided content",
                "write_file": {"path": "~/VoxeraOS/notes/hello.txt", "content": "hello world"},
            },
            {
                "goal": "read the file ~/VoxeraOS/notes/stv-child-target.txt",
                "enqueue_child": {
                    "goal": "open https://example.com",
                    "title": "Child Open URL",
                },
            },
            {
                "goal": "check if a.txt exists in notes",
                "steps": [{"skill_id": "files.exists", "args": {"path": "~/VoxeraOS/notes/a.txt"}}],
            },
            {
                "goal": "read /skillpack-wave2/a.txt from notes",
                "steps": [
                    {
                        "skill_id": "files.read_text",
                        "args": {"path": "~/VoxeraOS/notes/skillpack-wave2/a.txt"},
                    }
                ],
            },
            {
                "goal": "create folder archive in notes",
                "steps": [
                    {
                        "skill_id": "files.mkdir",
                        "args": {"path": "~/VoxeraOS/notes/archive", "parents": True},
                    }
                ],
            },
            {
                "goal": "copy report.txt into receipts",
                "file_organize": {
                    "source_path": "~/VoxeraOS/notes/report.txt",
                    "destination_dir": "~/VoxeraOS/notes/receipts",
                    "mode": "copy",
                    "overwrite": False,
                    "delete_original": False,
                },
            },
        ],
    )


def _looks_like_contextual_refinement(message: str) -> bool:
    lowered = message.strip().lower()
    if not lowered:
        return False
    return bool(
        re.search(
            r"\b(actually|instead|change|switch|rename|append|make\s+it|put\s+.*\s+in\s+it|use\s+this|for\s+later)\b",
            lowered,
        )
    )


def _draft_from_candidate_message(
    candidate: str,
    *,
    active_preview: dict[str, Any] | None,
    enrichment_context: dict[str, Any] | None = None,
    assistant_artifacts: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    revision = _draft_revision_from_active_preview(
        candidate,
        active_preview,
        enrichment_context=enrichment_context,
        assistant_artifacts=assistant_artifacts,
    )
    if revision is not None:
        return revision

    diagnostics_preview = _normalize_diagnostics_preview(candidate)
    if diagnostics_preview is not None:
        return diagnostics_preview

    normalized_open = _normalize_open_goal(candidate)
    if normalized_open:
        return {"goal": normalized_open}

    bounded_file = classify_bounded_file_intent(candidate)
    if bounded_file is not None:
        return bounded_file

    normalized_read = _normalize_file_read_goal(candidate)
    if normalized_read:
        return {"goal": normalized_read}

    structured_write = _normalize_structured_file_write_payload(
        candidate, assistant_artifacts=assistant_artifacts
    )
    if structured_write:
        return structured_write

    writing_draft = classify_writing_draft_intent(candidate)
    if writing_draft is not None:
        return writing_draft

    structured_note = _normalize_structured_note_payload(candidate)
    if structured_note:
        return structured_note

    normalized_write = _normalize_file_write_goal(candidate)
    if normalized_write:
        return {"goal": normalized_write}

    return None


def maybe_draft_job_payload(
    message: str,
    *,
    active_preview: dict[str, Any] | None = None,
    recent_user_messages: list[str] | None = None,
    enrichment_context: dict[str, Any] | None = None,
    investigation_context: dict[str, Any] | None = None,
    recent_assistant_messages: list[str] | None = None,
    recent_assistant_artifacts: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    normalized = message.strip()
    if not normalized:
        return None

    assistant_artifacts = (
        recent_assistant_artifacts
        if recent_assistant_artifacts is not None
        else collect_recent_saveable_assistant_artifacts(recent_assistant_messages)
    )

    investigation_draft = draft_investigation_save_preview(
        normalized,
        investigation_context=investigation_context,
    )
    if investigation_draft is not None:
        return investigation_draft

    primary = _draft_from_candidate_message(
        normalized,
        active_preview=active_preview,
        enrichment_context=enrichment_context,
        assistant_artifacts=assistant_artifacts,
    )
    if primary is not None:
        return primary

    if not recent_user_messages or not _looks_like_contextual_refinement(normalized):
        return None

    for prior in reversed(recent_user_messages[-4:]):
        prior_text = prior.strip()
        if not prior_text or prior_text == normalized:
            continue
        contextual_candidate = f"{prior_text}\n{normalized}"
        contextual = _draft_from_candidate_message(
            contextual_candidate,
            active_preview=active_preview,
            enrichment_context=enrichment_context,
            assistant_artifacts=assistant_artifacts,
        )
        if contextual is not None:
            return contextual

    return None
