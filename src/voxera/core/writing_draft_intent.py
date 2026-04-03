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
    r"plain\s+english|"
    r"explanation\b|"
    r"write\s+up\s+(?:a|an)\b|"
    r"put\s+together\s+(?:a|an)\s+(?:short\s+)?(?:writeup|write-up|summary|note|explanation)\b|"
    r"brief\s+(?:summary|writeup|write-up|explanation)\b"
    r")\b",
    re.IGNORECASE,
)
_WRITING_VERB_RE = re.compile(
    r"\b(write|rewrite|draft|create|expand|turn|explain|formal(?:ize)?|make|put)\b", re.IGNORECASE
)
_SAVE_ONLY_RE = re.compile(
    r"\b(save|write|put)\b.*\b(note|file|markdown|\.md\b|\.txt\b)\b", re.IGNORECASE
)
_TRANSFORM_SIGNAL_RE = re.compile(
    r"\b(rewrite|essay|article|writeup|formal|expand|turn\s+.+\s+into|plain\s+english)\b",
    re.IGNORECASE,
)
# Matches clear new-content creation requests with a note/file target and a topic signal.
# These override the _SAVE_ONLY_RE early exit because they are generating fresh content,
# not saving existing content.  Examples: "Write a short markdown file explaining X",
# "Draft a short note about Y and save it as Z.txt".
_SHORT_NEW_FILE_DRAFT_RE = re.compile(
    r"\b(?:draft|write|create|put\s+together)\b.{0,40}\b(?:note|file|summary)\b.{0,80}\b(?:explaining|about|on\s+\w|describing|regarding|of\s+\w)\b",
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
    r"i(?:'ve| have)\s+(?:prepared|drafted|written|put\s+together)\b|"
    r"i\s+can(?:\s+certainly)?\s+help(?:\s+you)?\b|"
    r"i(?:'d| would)\s+be\s+happy\s+to\b|"
    r"here(?:'s| is)\s+(?:the\s+)?(?:draft|essay|article|writeup|explanation|note|summary)\b|"
    r"here(?:'s| is)\s+(?:a|the)\s+(?:version|rewrite)\b|"
    r"here(?:'s| is)\s+what\s+i\s+(?:came\s+up\s+with|wrote|drafted|put\s+together)\b|"
    r"here(?:'s| is)\s+(?:a\s+)?(?:short|brief)?\s*(?:markdown\s+)?(?:note|draft|summary|explanation)\b|"
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
    # Clear new-content creation with a note/file target overrides the save-only
    # early exit below.  These are generate-then-save requests ("draft a note
    # explaining X", "write a file about Y"), not save-existing-content requests.
    if _SHORT_NEW_FILE_DRAFT_RE.search(text):
        return True
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
    wrapped_quoted_content = _extract_quoted_authored_content_from_wrapper(normalized)
    if wrapped_quoted_content:
        return _normalize_markdown_spacing(wrapped_quoted_content)
    # Pre-normalize compact markdown so that headings on single-newline
    # boundaries are properly separated into distinct blocks for the
    # prose-body extractor.  Without this, a compact LLM reply where
    # headings and body text share a single block causes wrapper text
    # to leak and heading spacing to collapse.
    pre_normalized = _normalize_markdown_spacing(normalized)
    content = _extract_prose_body(pre_normalized)
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


def _normalize_markdown_spacing(text: str) -> str:
    """Ensure markdown headings are preceded and followed by blank lines.

    LLM outputs sometimes produce compact markdown where ``## Heading``
    immediately follows the previous line with only a single newline, or
    worse, where a heading appears inline mid-line (e.g.
    ``...the OS runtime. ### 2. Guarded Execution``).

    This normalizer:
    1. Splits inline headings onto their own lines.
    2. Inserts blank lines around heading boundaries so that downstream
       block-splitting (which relies on ``\\n{2,}``) correctly separates
       heading blocks from body text.

    This preserves all content — no text is removed or truncated.
    """
    if not text:
        return text
    # Phase 1: split inline headings onto their own lines.
    # Matches patterns like "...sentence end. ### Heading" or "...text. # Title"
    # where a heading marker appears mid-line after sentence-ending punctuation.
    text = re.sub(
        r"([.!?:;])\s+(#{1,6}\s+)",
        r"\1\n\n\2",
        text,
    )
    # Phase 2: ensure headings that start a line are surrounded by blank lines.
    lines = text.split("\n")
    result: list[str] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        is_heading = bool(re.match(r"^#{1,6}\s+", stripped))
        if is_heading and result and result[-1].strip():
            result.append("")
        result.append(line)
        if is_heading and i + 1 < len(lines) and lines[i + 1].strip():
            result.append("")
    return "\n".join(result)


def _extract_prose_body(content: str) -> str | None:
    blocks = [block.strip() for block in re.split(r"\n{2,}", content) if block.strip()]
    if not blocks:
        return None

    trimmed = list(blocks)
    if trimmed:
        inline_cleaned = _strip_leading_preface_sentences(trimmed[0])
        if inline_cleaned != trimmed[0]:
            if inline_cleaned:
                trimmed[0] = inline_cleaned
            else:
                trimmed = trimmed[1:]

    while trimmed and _looks_like_wrapper_block(
        trimmed[0], next_block=trimmed[1] if len(trimmed) > 1 else None
    ):
        trimmed.pop(0)
    while trimmed and _looks_like_trailing_wrapper_block(trimmed[-1]):
        trimmed.pop()

    if len(trimmed) >= 2 and _BODY_LABEL_RE.fullmatch(trimmed[0]):
        trimmed = trimmed[1:]

    if len(blocks) >= 3 and _looks_like_wrapper_block(blocks[0], next_block=blocks[1]):
        trimmed = blocks[1:]
        while trimmed and _looks_like_wrapper_block(
            trimmed[0], next_block=trimmed[1] if len(trimmed) > 1 else None
        ):
            trimmed = trimmed[1:]
        while trimmed and _looks_like_trailing_wrapper_block(trimmed[-1]):
            trimmed = trimmed[:-1]

    if trimmed:
        trimmed[0] = _strip_leading_preface_sentences(trimmed[0])
        if not trimmed[0]:
            trimmed = trimmed[1:]

    if not trimmed:
        return None
    return "\n\n".join(trimmed).strip()


def _looks_like_wrapper_block(block: str, *, next_block: str | None = None) -> bool:
    stripped = block.strip()
    lowered = stripped.lower()
    if not lowered:
        return True
    if _BODY_LABEL_RE.fullmatch(stripped):
        return True
    if _WRAPPER_PREFIX_RE.match(stripped):
        return True
    if lowered.endswith(":") and any(
        token in lowered for token in ("overview", "summary", "draft", "body")
    ):
        return True
    if len(block.split()) <= 32 and any(
        phrase in lowered
        for phrase in (
            "i can help you",
            "i can certainly help you",
            "i'd be happy to",
            "i would be happy to",
            "prepared a draft",
            "put together a draft",
            "put together a note",
            "put together a summary",
            "updated the draft preview",
            "updated the draft",
            "here's what i came up with",
            "here is what i came up with",
            "here's what i wrote",
            "here is what i wrote",
            "i've staged a request",
            "i have staged a request",
            "staged a request in the preview pane",
            "please review the content",
            "you can review the content",
            "authorize the file creation",
            "review and authorize",
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
    ):
        return True
    if _looks_like_preface_setup_sentence(stripped):
        if _strip_leading_preface_sentences(stripped) != stripped:
            return False
        return next_block is None or _looks_like_document_body_start(next_block)
    return False


def _looks_like_trailing_wrapper_block(block: str) -> bool:
    lowered = block.strip().lower()
    if not lowered:
        return True
    trailing_wrapper_phrases = (
        "i've drafted a plan",
        "i have drafted a plan",
        "i've staged a request",
        "i have staged a request",
        "staged a request in the preview pane",
        "i've prepared a preview",
        "i have prepared a preview",
        "preview-only",
        "this is preview-only",
        "please review the content",
        "you can review the content",
        "you can see the current draft",
        "you can review the content in the preview pane",
        "authorize the file creation",
        "review and authorize",
        "if you're happy with how it looks",
        "if you are happy with how it looks",
        "if that looks good",
        "click submit to save it",
        "just hit submit",
        "submit when you're ready",
        "submit when you are ready",
        "preview pane",
        "nothing has been submitted",
        "ready to submit",
        "send it whenever you're ready",
        "send it whenever you are ready",
        "let me know if you'd like to change",
        "let me know if you would like to change",
        "let me know if you'd like any changes",
        "let me know if you would like any changes",
        "let me know if you want to change",
        "let me know when you'd like to send",
        "let me know when you would like to send",
        "would you like me to save",
        "want me to save",
    )
    return any(phrase in lowered for phrase in trailing_wrapper_phrases)


def _strip_leading_preface_sentences(block: str) -> str:
    current = block.strip()
    while current:
        match = re.match(r"^(.+?[.!?])(?:\s+|$)(.*)$", current, re.DOTALL)
        if not match:
            return current
        sentence = match.group(1).strip()
        remainder = (match.group(2) or "").strip()
        if _looks_like_conversational_preamble_sentence(sentence):
            if not remainder:
                return ""
            current = remainder
            continue
        if not _looks_like_preface_setup_sentence(sentence):
            return current
        if not remainder:
            return ""
        if _looks_like_preface_setup_sentence(remainder):
            current = remainder
            continue
        if _looks_like_document_body_start(remainder):
            return remainder
        return current
    return current


def _looks_like_conversational_preamble_sentence(block: str) -> bool:
    normalized = block.strip().lower().strip(" :;-—.!?")
    return normalized in {"certainly", "sure", "absolutely", "of course"}


def _looks_like_preface_setup_sentence(block: str) -> bool:
    lowered = block.strip().lower()
    if len(lowered.split()) > 36:
        return False
    starts_with_setup = bool(
        re.match(
            r"^(?:i(?:'ll| will)|here(?:'s| is)|i(?:'ve| have)|you\s+can\s+(?:see|review)|please\s+review)\b",
            lowered,
        )
    )
    if not starts_with_setup:
        return False
    return any(
        phrase in lowered
        for phrase in (
            "refine",
            "rewrite",
            "draft",
            "prepare",
            "formal",
            "essay",
            "article",
            "writeup",
            "explanation",
            "preview pane",
            "write-up",
            "step-by-step",
            "save it as",
            "saved as",
            "authorize",
            "file creation",
            "content below",
            "content and",
            ".md",
            ".txt",
        )
    )


def _looks_like_document_body_start(block: str) -> bool:
    stripped = block.strip()
    if not stripped:
        return False
    if _is_heading_like(stripped):
        return True
    if re.fullmatch(r"\*\*[^*]{3,}\*\*", stripped):
        return True
    return len(stripped.split()) >= 6 and stripped[0].isupper()


def _is_heading_like(block: str) -> bool:
    stripped = block.strip()
    if not stripped:
        return False
    if stripped.startswith("#"):
        return True
    return bool(_HEADING_RE.fullmatch(stripped)) and len(stripped.split()) <= 12


def _extract_quoted_authored_content_from_wrapper(text: str) -> str | None:
    lowered = text.lower()
    wrapper_signals = (
        "added a new joke",
        "added this to the file content",
        "added that to the file content",
        "to the file content",
        "current draft",
        "preview pane",
        "ready to submit",
    )
    if not any(signal in lowered for signal in wrapper_signals):
        return None

    quoted_candidates = [
        match.group(1).strip() for match in re.finditer(r'"([^"\n]{4,})"', text)
    ] + [
        match.group(1).strip()
        # Treat single-quoted payloads as quoted blocks only when the quote
        # marks are not apostrophes inside words (e.g. don't, I've).
        for match in re.finditer(r"(?<!\w)'([^'\n]{4,})'(?!\w)", text)
    ]
    if not quoted_candidates:
        return None

    for candidate in sorted(quoted_candidates, key=len, reverse=True):
        lowered_candidate = candidate.lower()
        if any(
            token in lowered_candidate
            for token in ("current draft", "preview pane", "ready to submit", "queue")
        ):
            continue
        if len(candidate.split()) >= 4 or len(candidate) >= 24:
            return candidate
    return None
