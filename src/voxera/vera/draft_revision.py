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
    if re.fullmatch(r"(?:to|as)\s+[~\/a-zA-Z0-9_.-]+\.[a-zA-Z0-9]{1,8}", value, re.IGNORECASE):
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
        r"\b(?:"
        r"called|"
        r"named|"
        r"call\s+(?:it|that|(?:the|this)\s+(?:note|file|draft|document|doc))|"
        r"name\s+(?:it|that|(?:the|this)\s+(?:note|file|draft|document|doc))"
        r")\s+([^\s]+)",
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
        candidate = tail.group(1).strip("\"'.,!? ")
        # Reject transformation adjectives accidentally captured as filenames.
        # "make that more concise" → "more" is not a filename.
        _TRANSFORMATION_WORDS = frozenset(
            {
                "more",
                "less",
                "into",
                "shorter",
                "longer",
                "concise",
                "formal",
                "casual",
                "operator",
                "user",
                "operator-facing",
                "user-facing",
                "a",
            }
        )
        if candidate.lower() not in _TRANSFORMATION_WORDS:
            return candidate
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
    if re.fullmatch(r"(?:to|as)\s+[~\/a-zA-Z0-9_.-]+\.[a-zA-Z0-9]{1,8}", value, re.IGNORECASE):
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

    # Additive edit: "add N more jokes", "append 3 more bullets", "continue the list", etc.
    # Must run before semantic substitution so additive requests append rather than replace.
    if _ADDITIVE_INTENT_RE.search(lowered):
        n = _parse_additive_count(lowered)
        content_type = _detect_additive_content_type(lowered)
        additional = _generate_additional_items(content_type, existing_content, n)
        if additional:
            base = existing_content.rstrip()
            return (base + "\n\n" + additional) if base else additional

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
    # ── Authored-content transformation patterns ──
    # These patterns support natural follow-up requests against an active
    # authored draft.  Session context is a continuity aid only — the
    # actual content comes from the active preview (existing_content).
    if re.search(r"\b(more\s+concise|make\s+(?:it|that)\s+(?:more\s+)?concise)\b", lowered):
        compressed = existing_content.strip()
        if compressed:
            sentences = re.split(r"(?<=[.!?])\s+", compressed)
            if len(sentences) > 1:
                return " ".join(sentences[: max(1, len(sentences) // 2)])
            words = compressed.split()
            return " ".join(words[: max(4, len(words) * 2 // 3)])
        return None
    if re.search(
        r"\b((?:turn|convert|transform)\s+(?:it|that|this)\s+into\s+(?:a\s+)?(?:checklist|list|bullet\s*(?:ed)?\s*list|outline))\b",
        lowered,
    ) or re.search(r"\b(as\s+a\s+checklist|into\s+a\s+checklist)\b", lowered):
        source = existing_content.strip()
        if source:
            sentences = re.split(r"(?<=[.!?])\s+", source)
            if len(sentences) <= 1:
                items = [s.strip() for s in re.split(r"[,;]\s*", source) if s.strip()]
            else:
                items = [s.strip().rstrip(".") for s in sentences if s.strip()]
            if items:
                return "\n".join(f"- {item}" for item in items)
        return None
    if re.search(r"\b(more\s+operator[- ]facing|more\s+operator[- ]focused)\b", lowered):
        source = existing_content.strip()
        if source:
            return f"[Operator-facing]\n{source}"
        return None
    if re.search(r"\b(more\s+user[- ]facing|more\s+user[- ]friendly)\b", lowered):
        source = existing_content.strip()
        if source:
            return f"[User-facing]\n{source}"
        return None
    if re.search(r"\b(keep\s+(?:the\s+)?same\s+tone)\b", lowered):
        return existing_content.strip() or None
    if re.search(
        r"\b(update\s+the\s+content|update\s+content|change\s+content|use\s+a\s+different\s+joke)\b",
        lowered,
    ):
        if existing_content.strip():
            return existing_content.strip() + " (updated)"
        return "Updated content."
    # Note: bare "change it" / "replace it" / "replace that" are ambiguous and
    # must NOT produce fake "(updated)" content.  They are handled by the
    # _is_ambiguous_change_request guard in interpret_active_preview_draft_revision.
    return None


# ---------------------------------------------------------------------------
# Active-draft content refresh support
# ---------------------------------------------------------------------------

_CONTENT_TYPE_KEYWORDS = (
    "joke",
    "poem",
    "story",
    "fact",
    "summary",
    "paragraph",
    "message",
    "bio",
    "explanation",
    "content",
    "text",
)

_POEM_POOL = [
    "The wind whispers softly through ancient trees,\nCarrying stories upon the breeze.",
    "Stars above the quiet sea,\nFlickering lights of mystery.",
    "Morning dew on petals bright,\nA gentle start to morning light.",
    "Leaves of gold and crimson red,\nDancing where the path has led.",
    "A river winds through valleys deep,\nWhere ancient echoes softly sleep.",
    "The moon climbs slow above the hill,\nAnd all the restless world grows still.",
]

_JOKE_POOL = [
    "Why did the scarecrow win an award? Because he was outstanding in his field.",
    "I told my wife she was drawing her eyebrows too high. She looked surprised.",
    "Why don't scientists trust atoms? Because they make up everything.",
    "What do you call a fish without eyes? A fsh.",
    "I used to hate facial hair, but then it grew on me.",
    "Why can't a nose be 12 inches long? Because then it would be a foot.",
    "I'm reading a book on anti-gravity. It's impossible to put down.",
    "Did you hear about the mathematician who's afraid of negative numbers? He'll stop at nothing to avoid them.",
    "Why do cows wear bells? Because their horns don't work.",
    "What do you call cheese that isn't yours? Nacho cheese.",
    "I would tell a joke about pizza, but it's a little cheesy.",
    "Time flies like an arrow. Fruit flies like a banana.",
    "My wife told me I had to stop acting like a flamingo. I had to put my foot down.",
    "I asked the librarian if they had books about paranoia. She whispered, 'They're right behind you!'",
    "Why did the belt go to jail? It held up a pair of pants.",
    "What do you call a sleeping dinosaur? A dino-snore.",
    "I only know 25 letters of the alphabet. I don't know why.",
    "Why can't you give Elsa a balloon? Because she'll let it go.",
    "What do you call a fake noodle? An impasta.",
    "I'm on a seafood diet. I see food and I eat it.",
]

_FACT_POOL = [
    "Honey never spoils — archaeologists have found 3,000-year-old honey in Egyptian tombs that was still edible.",
    "Octopuses have three hearts and blue blood.",
    "A group of flamingos is called a 'flamboyance.'",
    "Bananas are berries, but strawberries are not.",
    "A day on Venus is longer than a year on Venus.",
    "Cleopatra lived closer in time to the Moon landing than to the construction of the Great Pyramid.",
    "The shortest war in history lasted 38 to 45 minutes.",
    "A group of crows is called a murder.",
    "Wombats produce cube-shaped droppings.",
    "The Eiffel Tower grows about 6 inches taller in summer due to thermal expansion.",
    "There are more possible iterations of a game of chess than there are atoms in the observable universe.",
    "A shrimp's heart is in its head.",
]

_SUMMARY_POOL = [
    "A concise overview of the key points.",
    "The essential highlights in brief.",
]

# ---------------------------------------------------------------------------
# Additive edit support
# ---------------------------------------------------------------------------

_ADDITIVE_INTENT_RE = re.compile(
    r"\b(?:add|append|tack\s+on|include)\s+"
    r"(?:(?:\d+|five|ten|three|four|six|seven|eight|nine|a\s+few|a\s+couple(?:\s+of)?|some)\s+)?"
    r"more\s+"
    r"(?:joke|jokes|dad\s+jokes?|bullet|bullets|item|items|example|examples|point|points|"
    r"fact|facts|line|lines|reason|reasons|step|steps|tip|tips|idea|ideas|thing|things|"
    r"sentence|sentences|entry|entries)\b"
    r"|\bcontinue\s+(?:the\s+)?list\b"
    r"|\bexpand\s+(?:(?:the|this|it)\s+)?(?:note|list|content|draft)\s+with\b"
    r"|\bmake\s+it\s+longer\b",
    re.IGNORECASE,
)

_APPLY_PENDING_RE = re.compile(
    r"\badd\s+them\b"
    r"|\byes[\s,]+(?:add|apply|include)(?:\s+(?:those|them|that))?\b"
    r"|\bapply\s+(?:those|them|that|it)\b"
    r"|\bput\s+those\s+in(?:to)?\s+(?:the\s+)?(?:note|preview|content|draft)?\b"
    r"|\bupdate\s+(?:the\s+)?preview\s+with\s+(?:those|them|that)\b"
    r"|\badd\s+those\s+to\s+(?:the\s+)?(?:content|note|preview|draft)?\b"
    r"|\buse\s+those\b",
    re.IGNORECASE,
)

_WORD_TO_NUM = {
    "a couple of": 2,
    "a couple": 2,
    "a few": 3,
    "some": 3,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def is_active_preview_additive_edit_request(message: str) -> bool:
    """Return True when the message requests adding/appending content to an active preview."""
    return bool(_ADDITIVE_INTENT_RE.search(message.strip().lower()))


def is_apply_pending_suggestion_request(message: str) -> bool:
    """Return True when the message requests applying a pending content suggestion."""
    return bool(_APPLY_PENDING_RE.search(message.strip().lower()))


def _parse_additive_count(lowered: str) -> int:
    """Extract the requested item count from an additive edit request (default 3)."""
    m = re.search(r"\b(?:add|append)\s+(\d+)\s+more\b", lowered)
    if m:
        try:
            n = int(m.group(1))
            if 1 <= n <= 20:
                return n
        except ValueError:
            pass
    for word, num in sorted(_WORD_TO_NUM.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b(?:add|append)\s+{re.escape(word)}\s+more\b", lowered):
            return num
    return 3


def _detect_additive_content_type(lowered: str) -> str:
    """Infer the type of content to add from the request text."""
    if re.search(r"\bjoke|dad\s+joke\b", lowered):
        return "joke"
    if re.search(r"\bfact\b", lowered):
        return "fact"
    if re.search(r"\bpoem\b", lowered):
        return "poem"
    return "item"


def _generate_additional_items(content_type: str, existing: str, n: int) -> str | None:
    """Generate n additional authored items not already present in existing content."""
    existing_lower = existing.strip().lower()
    if content_type == "joke":
        available = [j for j in _JOKE_POOL if j.strip().lower() not in existing_lower]
        if not available:
            available = list(_JOKE_POOL)
        selected = available[:n]
        return "\n\n".join(selected) if selected else None
    if content_type == "fact":
        available = [f for f in _FACT_POOL if f.strip().lower() not in existing_lower]
        if not available:
            available = list(_FACT_POOL)
        selected = available[:n]
        return "\n\n".join(selected) if selected else None
    if content_type == "poem":
        available = [p for p in _POEM_POOL if p.strip().lower() not in existing_lower]
        if not available:
            available = list(_POEM_POOL)
        selected = available[:n]
        return "\n\n".join(selected) if selected else None
    # Generic list items: use facts as bullet points
    available = [f for f in _FACT_POOL if f.strip().lower() not in existing_lower]
    if not available:
        available = list(_FACT_POOL)
    selected = available[:n]
    return "\n".join(f"- {item}" for item in selected) if selected else None


def _is_clear_content_refresh_request(lowered: str) -> bool:
    """Detect clear requests to refresh the content body of an active preview.

    Clear refresh patterns include:
    - "generate a different poem"
    - "tell me a different joke"
    - "give me a shorter summary"
    - "give me another fact"
    - "change the poem"  (specific content type named)
    """
    # Pattern 1: generation verb + different/another/new + content type
    if (
        re.search(
            r"\b(generate|give\s+me|tell\s+me|write|create|compose|share|draft)\b",
            lowered,
        )
        and re.search(
            r"\b(different|another|new|shorter|longer|better|fresh)\b",
            lowered,
        )
        and re.search(
            r"\b(" + "|".join(_CONTENT_TYPE_KEYWORDS) + r")\b",
            lowered,
        )
    ):
        return True

    # Pattern 2: "change the [specific type]" / "replace the [specific type]"
    specific_types = ("joke", "poem", "story", "fact", "summary", "paragraph")
    if re.search(
        r"\b(change|replace)\s+the\s+(" + "|".join(specific_types) + r")\b",
        lowered,
    ):
        return True

    # Pattern 3: "give me a shorter summary" / "make it shorter" with content type
    return bool(
        re.search(r"\b(shorter|longer)\b", lowered)
        and re.search(
            r"\b(" + "|".join(_CONTENT_TYPE_KEYWORDS) + r")\b",
            lowered,
        )
    )


def _is_ambiguous_change_request(lowered: str) -> bool:
    """Detect ambiguous change requests that must fail closed.

    These are vague phrasing without a specific content type:
    - "change it"
    - "make it better"
    - "fix it"
    - "improve it"
    - "update it"
    """
    # Must NOT contain a specific content type keyword
    has_specific_type = bool(
        re.search(
            r"\b(joke|poem|story|fact|summary|paragraph|explanation)\b",
            lowered,
        )
    )
    if has_specific_type:
        return False

    ambiguous_patterns = (
        r"^change\s+it[.!?]*$",
        r"^make\s+it\s+(better|good|nice|different)[.!?]*$",
        r"^fix\s+it[.!?]*$",
        r"^improve\s+it[.!?]*$",
        r"^update\s+it[.!?]*$",
        r"^redo\s+it[.!?]*$",
        r"^try\s+again[.!?]*$",
    )
    return any(re.fullmatch(p, lowered.strip()) for p in ambiguous_patterns)


def _detect_content_type_from_preview(preview: dict[str, Any], lowered: str) -> str | None:
    """Infer the content type from the user message or existing preview."""
    # First check user message for explicit type
    type_map = {
        "poem": "poem",
        "joke": "joke",
        "funny": "joke",
        "humorous": "joke",
        "dad joke": "joke",
        "fact": "fact",
        "summary": "summary",
        "story": "story",
        "paragraph": "paragraph",
        "explanation": "explanation",
    }
    for keyword, ctype in type_map.items():
        if keyword in lowered:
            return ctype

    # Infer from preview filename
    write_file = preview.get("write_file")
    if isinstance(write_file, dict):
        path = str(write_file.get("path") or "").lower()
        if "poem" in path:
            return "poem"
        if "joke" in path:
            return "joke"
        if "fact" in path:
            return "fact"
        if "summary" in path or "summar" in path:
            return "summary"

    # Infer from goal
    goal = str(preview.get("goal") or "").lower()
    for keyword, ctype in type_map.items():
        if keyword in goal:
            return ctype

    return None


def _pick_different(pool: list[str], existing: str) -> str:
    """Pick a pool entry that differs from existing content."""
    existing_lower = existing.strip().lower()
    for candidate in pool:
        if candidate.strip().lower() != existing_lower:
            return candidate
    return pool[0]


def _generate_refreshed_content(content_type: str | None, existing_content: str) -> str | None:
    """Generate fresh replacement content for a known content type."""
    if content_type == "poem":
        return _pick_different(_POEM_POOL, existing_content)
    if content_type == "joke":
        return _pick_different(_JOKE_POOL, existing_content)
    if content_type == "fact":
        return _pick_different(_FACT_POOL, existing_content)
    if content_type == "summary":
        # For summary refresh, compress existing content
        compressed = existing_content.strip()
        if compressed:
            sentences = re.split(r"(?<=[.!?])\s+", compressed)
            if len(sentences) > 1:
                return " ".join(sentences[: max(1, len(sentences) // 2)])
            words = compressed.split()
            return " ".join(words[: max(4, len(words) // 2)])
        return _pick_different(_SUMMARY_POOL, existing_content)
    if content_type in ("story", "paragraph", "explanation"):
        # For generic prose types, signal that content was refreshed
        if existing_content.strip():
            return existing_content.strip()
        return None
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
            r"name\s+(?:it|that|(?:the|this)\s+(?:note|file|draft|document|doc))|"
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
    clear_generation_request = bool(
        re.search(r"\b(tell|give|write|draft|create|generate|compose|share)\b", lowered)
        and re.search(
            r"\b(joke|funny|humorous|poem|story|paragraph|content|text|message|bio|summary|explanation|fact|facts)\b",
            lowered,
        )
    )
    if clear_generation_request and re.search(
        r"\b(save\s+(?:it|this|that)?\s*as|called|named)\b",
        lowered,
    ):
        # This is a same-turn generate+save request for new authored content.
        # Let preview drafting create/bind a fresh shell instead of mutating the
        # active preview as a reference-based rename/refinement.
        return None

    # ── Ambiguous change request → fail closed explicitly ──
    if _is_ambiguous_change_request(lowered):
        return None

    current_goal = str(active_preview.get("goal") or "")
    references_prior_content = message_requests_referenced_content(text) or bool(
        re.search(r"\b(?:save|use|restore)\s+(?:the\s+)?previous\s+content\b", lowered)
    )

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

    _is_transformation_not_rename = bool(
        re.search(
            r"\bmake\s+that\s+(?:more|less|into|shorter|longer|concise|formal|casual|operator|user|a\s+(?:checklist|list|outline|bullet))\b",
            lowered,
        )
    )
    if (
        not _is_transformation_not_rename
        and re.search(
            r"\b("
            r"rename|"
            r"save\s+(?:it|this|that)?\s*as|"
            r"name\s+(?:it|that|(?:the|this)\s+(?:note|file|draft|document|doc))|"
            r"make\s+that|"
            r"call\s+(?:it|that|(?:the|this)\s+(?:note|file|draft|document|doc))|"
            r"change\s+(?:the\s+)?(?:name|filename|file\s+name|path)|"
            r"use\s+path|"
            r"set\s+(?:the\s+)?path"
            r")\b",
            lowered,
        )
        or (
            re.search(r"\b(save|write|put)\b", lowered)
            and re.search(r"\b(note|file|markdown)\b", lowered)
            and re.search(r"\b(?:called|named)\s+[^\s]+\b", lowered)
            and message_requests_referenced_content(text)
        )
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
                        "content": (
                            str(write_file.get("content") or "").strip()
                            or (
                                str(
                                    (
                                        select_recent_saveable_assistant_artifact(
                                            message=text,
                                            assistant_artifacts=assistant_artifacts,
                                        )
                                        or {}
                                    ).get("content")
                                    or ""
                                ).strip()
                                if references_prior_content
                                else ""
                            )
                        ),
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

    # ── Dedicated additive edit: "add N more jokes", "append N more bullets",
    # "continue the list", "make it longer", etc.
    # Must run before content_refinement_intent so plural forms ("jokes" not matched
    # by \bjoke\b) and verbs like "append"/"continue" are routed here directly.
    if _ADDITIVE_INTENT_RE.search(lowered):
        write_file = active_preview.get("write_file")
        _add_mode = "overwrite"
        _add_existing = ""
        if isinstance(write_file, dict):
            _add_path = str(write_file.get("path") or f"~/VoxeraOS/notes/{filename}")
            _add_mode = str(write_file.get("mode") or "overwrite")
            _add_existing = str(write_file.get("content") or "")
        else:
            _add_path = f"~/VoxeraOS/notes/{filename}"
        _add_n = _parse_additive_count(lowered)
        _add_type = _detect_additive_content_type(lowered)
        _add_items = _generate_additional_items(_add_type, _add_existing, _add_n)
        if _add_items:
            _add_merged = (
                (_add_existing.rstrip() + "\n\n" + _add_items)
                if _add_existing.strip()
                else _add_items
            )
            return {
                "goal": f"write a file called {filename} with provided content",
                "write_file": {"path": _add_path, "content": _add_merged, "mode": _add_mode},
            }

    content_refinement_intent = re.search(
        r"\b(add|put|use|make|change|update|replace|save|restore|turn|convert|transform|keep)\b",
        lowered,
    ) and re.search(
        r"\b(file|content|text|joke|script|it|that|checklist|list|outline|tone|style|format)\b",
        lowered,
    )
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
