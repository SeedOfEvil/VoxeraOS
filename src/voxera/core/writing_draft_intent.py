"""Bounded prose/document draft intent classifier for Vera.

Detects a narrow set of writing-oriented asks that should create authoritative
preview-backed ``write_file`` drafts. The actual prose body is populated from
the assistant reply by ``vera_web/app.py`` so the preview always reflects real
assistant-authored content, not a pseudo draft blob or conversational wrapper.
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
_WRAPPER_PREFIX_RE = re.compile(
    r"^(?:"
    r"i(?:'ve| have)\s+(?:prepared|drafted|written)\b|"
    r"i\s+can(?:\s+certainly)?\s+help(?:\s+you)?\b|"
    r"i(?:'d| would)\s+be\s+happy\s+to\b|"
    r"here(?:'s| is)\s+(?:the\s+)?(?:draft|essay|article|writeup|explanation)\b|"
    r"here(?:'s| is)\s+(?:a|the)\s+(?:version|rewrite)\b|"
    r"below\s+is\s+(?:the\s+)?(?:draft|essay|article|writeup|explanation)\b|"
    r"(?:essay|article|writeup|draft)\s+(?:overview|summary)\b|"
    r"(?:draft|essay|article|writeup)\s+body\b"
    r")",
    re.IGNORECASE,
)
_BODY_LABEL_RE = re.compile(
    r"^(?:essay|article|writeup|draft|body)\s*:\s*$",
    re.IGNORECASE,
)
_HEADING_RE = re.compile(r"^(?:#{1,6}\s+.+|[A-Z][^\n]{0,100})$")


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
    normalized = text.replace("\r\n", "\n").strip()
    if not normalized:
        return None
    content = _extract_prose_body(normalized)
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


def _extract_prose_body(content: str) -> str | None:
    blocks = [block.strip() for block in re.split(r"\n{2,}", content) if block.strip()]
    if not blocks:
        return None

    trimmed = list(blocks)
    while trimmed and _looks_like_wrapper_block(trimmed[0]):
        trimmed.pop(0)

    if len(trimmed) >= 2 and _BODY_LABEL_RE.fullmatch(trimmed[0]):
        trimmed = trimmed[1:]

    if len(blocks) >= 3 and _looks_like_wrapper_block(blocks[0]) and _is_heading_like(blocks[2]):
        trimmed = blocks[1:]
        if _looks_like_wrapper_block(trimmed[0]):
            trimmed = trimmed[1:]

    if not trimmed:
        return None
    return "\n\n".join(trimmed).strip()


def _looks_like_wrapper_block(block: str) -> bool:
    lowered = block.strip().lower()
    if not lowered:
        return True
    if _BODY_LABEL_RE.fullmatch(block.strip()):
        return True
    if _WRAPPER_PREFIX_RE.match(block.strip()):
        return True
    if lowered.endswith(":") and any(
        token in lowered for token in ("overview", "summary", "draft", "body")
    ):
        return True
    return len(block.split()) <= 32 and any(
        phrase in lowered
        for phrase in (
            "i can help you",
            "i can certainly help you",
            "i'd be happy to",
            "i would be happy to",
            "prepared a draft",
            "draft below",
            "essay below",
            "article below",
            "writeup below",
            "explanation below",
            "formalized short essay appears below",
            "this draft covers",
            "this essay covers",
            "this article covers",
        )
    )


def _is_heading_like(block: str) -> bool:
    stripped = block.strip()
    if not stripped:
        return False
    if stripped.startswith("#"):
        return True
    return bool(_HEADING_RE.fullmatch(stripped)) and len(stripped.split()) <= 12
