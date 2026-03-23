from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..core.file_intent import is_safe_notes_path
from .saveable_artifacts import (
    looks_like_ambiguous_reference_only,
    message_requests_referenced_content,
    select_recent_saveable_assistant_artifact,
)


def extract_quoted_content(text: str) -> str | None:
    quoted = re.search(r'"([^"]+)"', text)
    if quoted:
        return quoted.group(1)
    single = re.search(r"'([^']+)'", text)
    if single:
        return single.group(1)
    return None


def normalize_extracted_content_block(candidate: str) -> str | None:
    value = candidate.replace("\r\n", "\n")
    value = value.lstrip(" \t")
    if value.startswith(":"):
        value = value[1:]
    value = value.lstrip(" \t")
    if value.startswith("\n"):
        value = value[1:]
    value = value.rstrip()
    if not value:
        return None
    if re.fullmatch(r"(that|this|it|same|same thing)", value, re.IGNORECASE):
        return None
    if re.search(r"\bfile\s+called\b", value, re.IGNORECASE):
        return None
    if message_requests_referenced_content(value) or looks_like_ambiguous_reference_only(value):
        return None
    return value


def extract_content_after_markers(text: str, markers: tuple[str, ...]) -> str | None:
    for marker in markers:
        match = re.search(marker, text, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        candidate = normalize_extracted_content_block(match.group(1))
        if candidate:
            return candidate
    return None


def extract_named_target(message: str) -> str | None:
    named = re.search(
        r"\b(?:called|named|call\s+(?:it|that|(?:the|this)\s+(?:note|file|draft|document|doc)))\s+([^\s]+)",
        message,
        re.IGNORECASE,
    )
    if named:
        return named.group(1).strip("\"'.,!? ")
    path_like = re.search(
        r"\b(?:as|to)\s+([~\/a-zA-Z0-9_.-]+\.[a-zA-Z0-9]{1,8})\b",
        message,
        re.IGNORECASE,
    )
    if path_like:
        return path_like.group(1).strip("\"'.,!? ")
    explicit_path = re.search(
        r"\b(?:use\s+path|change\s+(?:the\s+)?path|set\s+(?:the\s+)?path)\s*:?\s+(?:to\s+)?([~\/][^\s]+)",
        message,
        re.IGNORECASE,
    )
    if explicit_path:
        return explicit_path.group(1).strip("\"'.,!? ")
    tail = re.search(
        r"\b(?:rename|make\s+that|change\s+(?:the\s+)?(?:name|filename|file\s+name))\s+(?:it\s+)?(?:to\s+)?([^\s]+)",
        message,
        re.IGNORECASE,
    )
    if tail:
        return tail.group(1).strip("\"'.,!? ")
    return None


def filename_from_preview(preview: dict[str, Any]) -> str | None:
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


def normalize_refinement_content_candidate(candidate: str) -> str | None:
    value = candidate.strip(" \"'`:\n\t")
    if not value:
        return None
    if re.fullmatch(r"(that|this|it|same|same thing)", value, re.IGNORECASE):
        return None
    if re.search(r"\bfile\s+called\b", value, re.IGNORECASE):
        return None
    if message_requests_referenced_content(value) or looks_like_ambiguous_reference_only(value):
        return None
    return value


def extract_content_refinement(
    text: str, lowered: str, *, filename_hint: str | None = None
) -> str | None:
    content = extract_quoted_content(text)
    if content:
        return content

    block_content = extract_content_after_markers(
        text,
        (
            r"\b(?:change|update|replace)\s+(?:it|this|that)\s+(?:so\s+)?(?:the\s+)?(?:content|text)\s+becomes\s*:\s*(.+)$",
            r"\b(?:change|update|replace)\s+(?:so\s+)?(?:the\s+)?(?:content|text)\s+becomes\s*:\s*(.+)$",
            r"\b(?:change|update|replace|make)\s+(?:the\s+)?(?:content|text)\s*(?:to|with)?\s*:\s*(.+)$",
            r"\b(?:content|text)\s*:\s*(.+)$",
            r"\buse\s+this\s+as\s+(?:the\s+)?content\s*:\s*(.+)$",
        ),
    )
    if block_content:
        return block_content

    patterns = [
        r"\bput\s+(.+?)\s+(?:inside|in|into)\s+(?:the\s+)?file\b",
        r"\buse\s+(?:this\s+)?(?:content|text|joke)\s*:?\s*(.+)$",
        r"\badd\s+content\s+to\s+[^\s]+\s+(?:saying|with)\s+(.+)$",
        r"\badd\s+content\s+to\s+[^\s]+\s+(.+)$",
        r"\bmake\s+(?:the\s+)?file\s+contain\s+(.+)$",
        r"\bmake\s+(?:the\s+)?content\s+(?:to|into)?\s*(.+)$",
        r"\bchange\s+(?:the\s+)?(?:content|text)\s+to\s+(.+)$",
        r"\breplace\s+(?:the\s+)?(?:content|text)\s+with\s+(.+)$",
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
        candidate = normalize_refinement_content_candidate(match.group(1))
        if candidate:
            return candidate

    return None


def extract_semantic_content_request(lowered: str) -> str | None:
    if re.search(r"\b(summary|summari[sz]e)\b", lowered) and re.search(
        r"\b(news|headlines?|top\s+stories?)\b", lowered
    ):
        if re.search(r"\b(short|brief)\b", lowered):
            return "Short summary of today's top news headlines."
        if re.search(r"\b(list|bullet|bulleted)\b", lowered):
            return "Top stories:\n- Headline 1\n- Headline 2\n- Headline 3"
        return "Summary of today's top news headlines."
    if re.search(r"\b(make\s+it\s+more\s+formal|more\s+formal|formal\s+tone)\b", lowered):
        return "Formal rewrite requested for the existing file content."
    if re.search(r"\b(dad\s+joke|dad\s+style|corny)\b", lowered):
        return "I'm reading a book on anti-gravity. It's impossible to put down."
    return None


def refined_content_from_active_preview(
    *,
    text: str,
    lowered: str,
    existing_content: str,
) -> str | None:
    explicit = extract_content_refinement(text, lowered)
    if explicit:
        return explicit

    semantic = extract_semantic_content_request(lowered)
    if semantic:
        return semantic

    if re.search(r"\b(programmer|coding|developer)\b", lowered):
        return "Why do programmers prefer dark mode? Because light attracts bugs."
    if re.search(r"\b(pet|dog|cat|puppy|kitten)\b", lowered):
        return "Why did the cat sit on the computer? To keep an eye on the mouse."
    if re.search(
        r"\b(different\s+joke|change\s+the\s+joke|replace\s+the\s+content|make\s+it\s+funnier)\b",
        lowered,
    ):
        return "I told my computer I needed a break, so it said: 'No problem, I’ll go to sleep.'"
    if re.search(r"\b(make\s+it\s+shorter|shorter)\b", lowered):
        compressed = existing_content.strip()
        if compressed:
            words = compressed.split()
            return " ".join(words[: min(len(words), 8)])
        return "Quick joke: cache me outside."
    if re.search(
        r"\b(update\s+the\s+content|update\s+content|change\s+it|change\s+content|replace\s+that|replace\s+it|use\s+a\s+different\s+joke)\b",
        lowered,
    ):
        if existing_content.strip():
            return existing_content.strip() + " (updated)"
        return "Updated content."
    return None


def writing_kind_from_preview_goal(goal: str) -> str | None:
    match = re.match(r"\s*draft\s+a\s+([a-z]+)\s+as\s+", goal, re.IGNORECASE)
    if not match:
        return None
    kind = (match.group(1) or "").strip().lower()
    if kind in {"essay", "article", "writeup", "explanation"}:
        return kind
    return None


def looks_like_preview_rename_or_save_as_request(message: str) -> bool:
    normalized = message.strip().lower()
    if not normalized:
        return False
    return bool(
        re.search(
            r"\b("
            r"save\s+(?:it|this|that)?\s*as|"
            r"rename|"
            r"call\s+(?:it|that)|"
            r"change\s+(?:the\s+)?(?:name|filename|file\s+name)"
            r")\b",
            normalized,
        )
    )


def interpret_active_preview_draft_revision(
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
    current_goal = str(active_preview.get("goal") or "")

    filename = filename_from_preview(active_preview) or "note.txt"
    explicit_targeted_content_refinement = bool(
        re.search(r"\badd\s+content\s+to\b", lowered)
    ) and bool(re.search(rf"\b{re.escape(filename)}\b", text, re.IGNORECASE))
    if explicit_targeted_content_refinement:
        write_file = active_preview.get("write_file")
        mode = "overwrite"
        existing_content = ""
        if isinstance(write_file, dict):
            path = str(write_file.get("path") or f"~/VoxeraOS/notes/{filename}")
            mode = str(write_file.get("mode") or "overwrite")
            existing_content = str(write_file.get("content") or "")
        else:
            path = f"~/VoxeraOS/notes/{filename}"

        refined_content = extract_content_refinement(text, lowered, filename_hint=filename)
        if not refined_content:
            refined_content = refined_content_from_active_preview(
                text=text,
                lowered=lowered,
                existing_content=existing_content,
            )
        if not refined_content and enrichment_context is not None:
            enrich_summary = str(enrichment_context.get("summary") or "").strip()
            if enrich_summary and re.search(
                r"\b(that|it|this|the\s+result|the\s+results|the\s+summary|those)\b",
                lowered,
            ):
                refined_content = enrich_summary
        if not refined_content:
            referenced_artifact = select_recent_saveable_assistant_artifact(
                message=text,
                assistant_artifacts=assistant_artifacts,
            )
            refined_content = (
                str(referenced_artifact.get("content") or "").strip()
                if isinstance(referenced_artifact, dict)
                else None
            )
        if refined_content:
            return {
                "goal": f"write a file called {filename} with provided content",
                "write_file": {"path": path, "content": refined_content, "mode": mode},
            }

    if re.search(
        r"\b("
        r"rename|"
        r"save\s+(?:it|this|that)?\s*as|"
        r"make\s+that|"
        r"call\s+(?:it|that|(?:the|this)\s+(?:note|file|draft|document|doc))|"
        r"change\s+(?:the\s+)?(?:name|filename|file\s+name|path)|"
        r"use\s+path|"
        r"set\s+(?:the\s+)?path"
        r")\b",
        lowered,
    ) or (
        re.search(r"\b(save|write|put)\b", lowered)
        and re.search(r"\b(note|file|markdown)\b", lowered)
        and re.search(r"\b(?:called|named)\s+[^\s]+\b", lowered)
        and message_requests_referenced_content(text)
    ):
        new_name = extract_named_target(text)
        if new_name:
            write_file = active_preview.get("write_file")
            if isinstance(write_file, dict):
                if new_name.startswith("~/") or new_name.startswith("/home/"):
                    rewritten_path = new_name
                else:
                    base_path = str(write_file.get("path") or "")
                    if "/" in base_path:
                        rewritten_path = str(Path(base_path).with_name(new_name))
                    else:
                        rewritten_path = f"~/VoxeraOS/notes/{new_name}"
                if not is_safe_notes_path(rewritten_path):
                    return None
                display_name = Path(rewritten_path).name
                writing_kind = writing_kind_from_preview_goal(current_goal)
                goal = f"write a file called {display_name} with provided content"
                if writing_kind is not None:
                    goal = f"draft a {writing_kind} as {display_name}"
                return {
                    "goal": goal,
                    "write_file": {
                        "path": rewritten_path,
                        "content": str(write_file.get("content") or ""),
                        "mode": str(write_file.get("mode") or "overwrite"),
                    },
                }
            if "write a note called" in current_goal:
                if ".." in new_name or new_name.startswith("/"):
                    return None
                return {"goal": f"write a note called {new_name}"}

    if re.search(r"\bappend\b", lowered) and re.search(
        r"\b(?:same\s+file|it|this|instead|switch)\b", lowered
    ):
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

    content_refinement_intent = re.search(
        r"\b(add|put|use|make|change|update|replace)\b", lowered
    ) and re.search(r"\b(file|content|text|joke|script|it|that)\b", lowered)
    if content_refinement_intent:
        write_file = active_preview.get("write_file")
        mode = "overwrite"
        existing_content = ""
        if isinstance(write_file, dict):
            path = str(write_file.get("path") or f"~/VoxeraOS/notes/{filename}")
            mode = str(write_file.get("mode") or "overwrite")
            existing_content = str(write_file.get("content") or "")
        else:
            path = f"~/VoxeraOS/notes/{filename}"

        refined_content = extract_content_refinement(text, lowered, filename_hint=filename)
        if not refined_content:
            refined_content = refined_content_from_active_preview(
                text=text,
                lowered=lowered,
                existing_content=existing_content,
            )
        if not refined_content and enrichment_context is not None:
            enrich_summary = str(enrichment_context.get("summary") or "").strip()
            if enrich_summary and re.search(
                r"\b(that|it|this|the\s+result|the\s+results|the\s+summary|those)\b",
                lowered,
            ):
                refined_content = enrich_summary
        if not refined_content:
            referenced_artifact = select_recent_saveable_assistant_artifact(
                message=text,
                assistant_artifacts=assistant_artifacts,
            )
            refined_content = (
                str(referenced_artifact.get("content") or "").strip()
                if isinstance(referenced_artifact, dict)
                else None
            )
        if refined_content:
            return {
                "goal": f"write a file called {filename} with provided content",
                "write_file": {"path": path, "content": refined_content, "mode": mode},
            }

    return None
