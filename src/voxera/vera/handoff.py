from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.inbox import add_inbox_payload

_ALLOWED_TOP_LEVEL_KEYS = {
    "goal",
    "title",
    "parent_job_id",
    "root_job_id",
    "orchestration_depth",
    "sequence_index",
    "lineage_role",
    "enqueue_child",
    "write_file",
}

_HANDOFF_PATTERNS = (
    r"\bhand\s+it\s+off\b",
    r"\bhandoff\b",
    r"\bsubmit\s+it\b",
    r"\bsubmit\s+to\s+voxeraos\b",
    r"\bsend\s+it\s+to\s+voxeraos\b",
    r"\bqueue\s+it\b",
    r"\benqueue\s+it\b",
    r"\bpush\s+it\s+through\b",
    r"\b(do\s+it|go\s+ahead|proceed)\b.*\b(voxeraos|submit|send|queue)?\b",
    r"\b(submit|send|hand\s+off)\b.*\b(job|request|it|this|queue|voxeraos|now|please)\b",
)

_ACTIVE_PREVIEW_SUBMIT_PATTERNS = (
    r"\byes\s+please\b",
    r"\byes\s+go\s+ahead\b",
    r"\bthat\s+looks\s+good\s+now\b",
    r"\buse\s+it\b",
    r"\buse\s+this\s+preview\b",
    r"\buse\s+the\s+current\s+preview\b",
    r"\bthis\s+preview\s+is\s+correct\b",
    r"\bokay\s+now\s+use\s+it\b",
    r"\bthat\s+json\s+is\s+right\b",
    r"\bsend\s+this\s+version\b",
    r"\bsubmit\s+this\s+one\b",
    r"\bgo\s+with\s+this\b",
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


def _extract_quoted_content(text: str) -> str | None:
    quoted = re.search(r'"([^"]+)"', text)
    if quoted:
        return quoted.group(1)
    single = re.search(r"'([^']+)'", text)
    if single:
        return single.group(1)
    return None


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


def _normalize_structured_file_write_payload(message: str) -> dict[str, Any] | None:
    text = message.strip().rstrip("?.!")
    lowered = text.lower()
    append_mode = bool(re.search(r"\b(append|add\s+to)\b", lowered))
    if not re.search(r"\b(write|create|save|put|make|append|add|build)\b", lowered):
        return None
    if not re.search(r"\b(file|note|\w+\.[a-z0-9]{1,8})\b", lowered):
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
        named = re.search(r"\b(?:called|call\w*|named)\s+([^\s]+)", text, re.IGNORECASE)
        target = named.group(1).strip("\"'") if named else None
    if not target:
        return None

    content = _extract_quoted_content(text)
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
            candidate = match.group(1).strip(" \"'`:")
            if candidate:
                content = candidate
                break
    if content is None:
        content = _infer_content_from_message(text) or ""

    normalized_path = target
    if not target.startswith("~") and not target.startswith("/"):
        normalized_path = f"~/VoxeraOS/notes/{target}"

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


def _extract_named_target(message: str) -> str | None:
    named = re.search(r"\b(?:called|named|as|to)\s+([^\s]+)", message, re.IGNORECASE)
    if named:
        return named.group(1).strip("\"'.,!? ")
    tail = re.search(r"\b(?:rename|make\s+that)\s+(?:it\s+)?([^\s]+)", message, re.IGNORECASE)
    if tail:
        return tail.group(1).strip("\"'.,!? ")
    return None


def _filename_from_preview(preview: dict[str, Any]) -> str | None:
    write_file = preview.get("write_file")
    if isinstance(write_file, dict):
        path = str(write_file.get("path") or "").strip()
        if path:
            return Path(path).name
    goal = str(preview.get("goal") or "")
    match = re.search(r"\bcalled\s+([^\s]+)", goal, re.IGNORECASE)
    if match:
        return match.group(1).strip("\"'.,!? ")
    return None


def _extract_content_refinement(
    text: str, lowered: str, *, filename_hint: str | None = None
) -> str | None:
    content = _extract_quoted_content(text)
    if content:
        return content

    patterns = [
        r"\bput\s+(.+?)\s+(?:inside|in|into)\s+(?:the\s+)?file\b",
        r"\buse\s+(?:this\s+)?(?:content|text|joke)\s*:?\s*(.+)$",
        r"\badd\s+content\s+to\s+[^\s]+\s+(?:saying|with)\s+(.+)$",
        r"\badd\s+content\s+to\s+[^\s]+\s+(.+)$",
        r"\bmake\s+(?:the\s+)?file\s+contain\s+(.+)$",
        r"\badd\s+(.+?)\s+to\s+(?:the\s+)?file\b",
        r"\buse\s+this\s+as\s+(?:the\s+)?content\s*:?\s*(.+)$",
    ]
    if filename_hint:
        escaped = re.escape(filename_hint)
        patterns.insert(
            0,
            rf"\badd\s+content\s+to\s+{escaped}\s*(?:saying|with)?\s*(.+)$",
        )

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        candidate = match.group(1).strip(" \"'`:")
        if candidate:
            return candidate

    return None


def _draft_revision_from_active_preview(
    message: str, active_preview: dict[str, Any] | None
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

    if re.search(r"\b(rename|make\s+that)\b", lowered):
        new_name = _extract_named_target(text)
        if new_name:
            write_file = active_preview.get("write_file")
            if isinstance(write_file, dict):
                base_path = str(write_file.get("path") or "")
                if "/" in base_path:
                    rewritten_path = str(Path(base_path).with_name(new_name))
                else:
                    rewritten_path = f"~/VoxeraOS/notes/{new_name}"
                return {
                    "goal": f"write a file called {new_name} with provided content",
                    "write_file": {
                        "path": rewritten_path,
                        "content": str(write_file.get("content") or ""),
                        "mode": str(write_file.get("mode") or "overwrite"),
                    },
                }
            if "write a note called" in current_goal:
                return {"goal": f"write a note called {new_name}"}

    if re.search(r"\bappend\b", lowered) and re.search(r"\b(?:same\s+file|it|this)\b", lowered):
        write_file = active_preview.get("write_file")
        if isinstance(write_file, dict):
            path = str(write_file.get("path") or "").strip()
            content = str(write_file.get("content") or "")
            if path:
                filename = Path(path).name
                return {
                    "goal": f"append to a file called {filename} with provided content",
                    "write_file": {"path": path, "content": content, "mode": "append"},
                }

    if re.search(r"\b(add|put|use|make)\b", lowered) and re.search(
        r"\b(file|content|text|joke|script|it)\b", lowered
    ):
        filename = _filename_from_preview(active_preview) or "note.txt"
        write_file = active_preview.get("write_file")
        mode = "overwrite"
        if isinstance(write_file, dict):
            path = str(write_file.get("path") or f"~/VoxeraOS/notes/{filename}")
            mode = str(write_file.get("mode") or "overwrite")
        else:
            path = f"~/VoxeraOS/notes/{filename}"

        refined_content = _extract_content_refinement(text, lowered, filename_hint=filename)
        if refined_content:
            return {
                "goal": f"write a file called {filename} with provided content",
                "write_file": {"path": path, "content": refined_content, "mode": mode},
            }

    return None


@dataclass(frozen=True)
class DraftingGuidance:
    base_shape: dict[str, str]
    examples: list[dict[str, Any]]


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
        ],
    )


def is_explicit_handoff_request(message: str) -> bool:
    normalized = message.strip().lower()
    if not normalized:
        return False
    return any(re.search(pattern, normalized) for pattern in _HANDOFF_PATTERNS)


def is_active_preview_submit_request(message: str) -> bool:
    normalized = message.strip().lower()
    if not normalized:
        return False
    return any(re.search(pattern, normalized) for pattern in _ACTIVE_PREVIEW_SUBMIT_PATTERNS)


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
    candidate: str, *, active_preview: dict[str, Any] | None
) -> dict[str, Any] | None:
    revision = _draft_revision_from_active_preview(candidate, active_preview)
    if revision is not None:
        return revision

    normalized_open = _normalize_open_goal(candidate)
    if normalized_open:
        return {"goal": normalized_open}

    normalized_read = _normalize_file_read_goal(candidate)
    if normalized_read:
        return {"goal": normalized_read}

    structured_write = _normalize_structured_file_write_payload(candidate)
    if structured_write:
        return structured_write

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
) -> dict[str, Any] | None:
    normalized = message.strip()
    if not normalized:
        return None

    primary = _draft_from_candidate_message(normalized, active_preview=active_preview)
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
        )
        if contextual is not None:
            return contextual

    return None


def normalize_preview_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key in _ALLOWED_TOP_LEVEL_KEYS:
        if key in payload:
            cleaned[key] = payload[key]

    goal = str(cleaned.get("goal") or "").strip()
    if not goal:
        raise ValueError("goal is required")
    cleaned = {"goal": goal, **{k: v for k, v in cleaned.items() if k != "goal"}}

    if "title" in cleaned:
        title = str(cleaned["title"]).strip()
        if title:
            cleaned["title"] = title
        else:
            cleaned.pop("title", None)

    enqueue_child = cleaned.get("enqueue_child")
    if enqueue_child is not None:
        if not isinstance(enqueue_child, dict):
            raise ValueError("enqueue_child must be an object")
        child_goal = str(enqueue_child.get("goal") or "").strip()
        if not child_goal:
            raise ValueError("enqueue_child.goal is required")
        normalized_child: dict[str, Any] = {"goal": child_goal}
        child_title = str(enqueue_child.get("title") or "").strip()
        if child_title:
            normalized_child["title"] = child_title
        cleaned["enqueue_child"] = normalized_child

    write_file = cleaned.get("write_file")
    if write_file is not None:
        if not isinstance(write_file, dict):
            raise ValueError("write_file must be an object")
        path = str(write_file.get("path") or "").strip()
        if not path:
            raise ValueError("write_file.path is required")
        content = write_file.get("content")
        if not isinstance(content, str):
            raise ValueError("write_file.content must be a string")
        mode = str(write_file.get("mode") or "overwrite").strip().lower()
        if mode not in {"overwrite", "append"}:
            raise ValueError("write_file.mode must be overwrite or append")
        cleaned["write_file"] = {"path": path, "content": content, "mode": mode}

    return cleaned


def submit_preview(*, queue_root: Path, payload: dict[str, Any]) -> dict[str, str]:
    created = add_inbox_payload(queue_root, payload, source_lane="vera_handoff")
    if not created.exists():
        raise RuntimeError(f"queue write was not confirmed at {created}")

    job_id = created.stem.removeprefix("inbox-")
    return {
        "job_id": job_id,
        "job_path": str(created),
        "queue_path": str(queue_root),
        "ack": (
            f"I submitted the job to VoxeraOS. Job id: {job_id}. "
            "The request is now in the queue. Execution has not completed yet. "
            "VoxeraOS will handle planning, policy/approval, execution, and evidence."
        ),
    }
