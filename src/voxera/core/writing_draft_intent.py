"""Bounded prose/document draft intent classifier for Vera.

Detects a narrow set of writing-oriented asks that should create authoritative
preview-backed ``write_file`` drafts. The actual prose body is populated from
the assistant reply by ``vera_web/app.py`` so the preview always reflects real
assistant-authored content, not a pseudo draft blob.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .code_draft_intent import has_code_file_extension

_PAGE_ESSAY_RE = re.compile(r"\b\d+\s*-\s*page\s+essay\b|\b\d+\s+page\s+essay\b", re.IGNORECASE)
_DIRECT_WRITING_RE = re.compile(
    r"\b("
    r"essay|article|writeup|write-up|rewrite\s+that\s+as|rewrite\s+this\s+as|"
    r"rewrite\s+(?:it|that|this)\s+as|rewrite\b|"
    r"make\s+it\s+more\s+formal|"
    r"expand\s+on\s+(?:that|this|these|those|the(?:se)?\s+themes?)|"
    r"turn\s+(?:that|this|it)\s+into\s+(?:paragraphs?|an?\s+article|an?\s+essay)|"
    r"short\s+article\s+for\s+a\s+technical\s+teammate|"
    r"explain\s+how\s+this\s+script\s+works\s+in\s+plain\s+english|"
    r"plain\s+english"
    r")\b",
    re.IGNORECASE,
)
_WRITING_VERB_RE = re.compile(
    r"\b(write|rewrite|draft|expand|turn|explain|formal(?:ize)?|make)\b", re.IGNORECASE
)
_SAVE_ONLY_RE = re.compile(
    r"\b(save|write|put)\b.*\b(note|file|markdown|\.md\b|\.txt\b)\b", re.IGNORECASE
)
_TRANSFORM_SIGNAL_RE = re.compile(
    r"\b(rewrite|essay|article|writeup|formal|expand|turn\s+.+\s+into|plain\s+english)\b",
    re.IGNORECASE,
)
_TEXT_FILENAME_RE = re.compile(r"\b([a-zA-Z0-9_.-]+\.(?:md|txt))\b")
_SAVE_AS_RE = re.compile(
    r"\bsave\s+(?:it|this|that)?\s*as\s+([a-zA-Z0-9_.-]+\.(?:md|txt))\b", re.IGNORECASE
)
_TOPIC_RE = re.compile(r"\b(?:about|on|for|based\s+on)\s+(.+?)(?:[.?!]|$)", re.IGNORECASE)
_SLUG_TOKEN_RE = re.compile(r"[a-z0-9]+")


def is_writing_draft_request(message: str) -> bool:
    text = message.strip()
    if not text:
        return False
    if _SAVE_ONLY_RE.search(text) and not _TRANSFORM_SIGNAL_RE.search(text):
        return False
    if _PAGE_ESSAY_RE.search(text):
        return True
    if not _WRITING_VERB_RE.search(text):
        return False
    return bool(_DIRECT_WRITING_RE.search(text))


def is_writing_refinement_request(message: str) -> bool:
    text = message.strip()
    if not text:
        return False
    return bool(
        re.search(
            r"\b("
            r"rewrite|more\s+formal|less\s+formal|more\s+casual|shorter|longer|"
            r"expand|turn\s+(?:that|this|it)\s+into|save\s+as|plain\s+english|"
            r"high\s+school\s+essay|technical\s+teammate|essay|article|writeup"
            r")\b",
            text,
            re.IGNORECASE,
        )
    )


def classify_writing_draft_intent(message: str) -> dict[str, Any] | None:
    if not is_writing_draft_request(message):
        return None

    text = message.strip()
    kind = _classify_kind(text)
    filename = _extract_filename(text, kind)
    return {
        "goal": f"draft a {kind} as {filename}",
        "write_file": {
            "path": f"~/VoxeraOS/notes/{filename}",
            "content": "",
            "mode": "overwrite",
        },
    }


def is_text_draft_preview(preview: dict[str, Any] | None) -> bool:
    if not isinstance(preview, dict):
        return False
    write_file = preview.get("write_file")
    if not isinstance(write_file, dict):
        return False
    path = str(write_file.get("path") or "").strip()
    if not path:
        return False
    return not has_code_file_extension(path)


def extract_text_draft_from_reply(text: str) -> str | None:
    content = text.replace("\r\n", "\n").strip()
    if not content:
        return None
    if len(content) < 24 and len(content.split()) < 5:
        return None
    return content


def _classify_kind(text: str) -> str:
    lowered = text.lower()
    if "essay" in lowered or _PAGE_ESSAY_RE.search(text):
        return "essay"
    if "article" in lowered:
        return "article"
    if "writeup" in lowered or "write-up" in lowered:
        return "writeup"
    return "explanation"


def _extract_filename(text: str, kind: str) -> str:
    explicit = _SAVE_AS_RE.search(text) or _TEXT_FILENAME_RE.search(text)
    if explicit:
        return Path(explicit.group(1)).name

    topic_slug = _topic_slug(text)
    ext = ".txt" if kind == "explanation" else ".md"
    if topic_slug:
        return f"{topic_slug}-{kind}{ext}"
    return f"{kind}{ext}"


def _topic_slug(text: str) -> str | None:
    match = _TOPIC_RE.search(text)
    if not match:
        return None
    topic = match.group(1).strip().lower()
    topic = re.sub(r"\b(that|this|it|the\s+summary|the\s+explanation)\b", "", topic).strip()
    tokens = _SLUG_TOKEN_RE.findall(topic)
    if not tokens:
        return None
    return "-".join(tokens[:8])
