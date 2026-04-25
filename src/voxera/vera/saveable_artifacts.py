from __future__ import annotations

import re

from ..core.writing_draft_intent import extract_text_draft_from_reply

_LOW_INFORMATION_ASSISTANT_PATTERNS = (
    r"^(?:ok|okay|sure|got it|understood|sounds good|will do|done)[.!]*$",
    r"^(?:thanks|thank you|thank-you)[.!]*$",
)

# Matched only against trailing lines — never stripped from mid-content.
_TRAILING_CONTROL_PHRASES = (
    "let me know if",
    "let me know when",
    "would you like me to",
    "would you like to submit",
    "is there anything else",
    "should we refine",
    "do you want me to",
    "do you want to save",
    "you can check the preview",
    "this is preview-only",
    "nothing has been submitted",
    "i still have the current request",
)


def _strip_trailing_control_text(text: str) -> str:
    """Strip trailing workflow/control narration lines from assistant content."""
    if not text:
        return text
    lines = text.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    while lines:
        last = lines[-1].strip().lower()
        if last and any(last.startswith(phrase) for phrase in _TRAILING_CONTROL_PHRASES):
            lines.pop()
            while lines and not lines[-1].strip():
                lines.pop()
        else:
            break
    return "\n".join(lines).strip()


def message_requests_referenced_content(message: str) -> bool:
    lowered = message.lower()
    if not re.search(r"\b(that|this|it|previous|last|your)\b", lowered):
        return False
    return bool(
        re.search(
            r"\b("
            r"that\s+(?:joke|summary|text|answer|response|explanation|previous\s+summary|previous\s+answer|previous\s+response|previous\s+explanation)|"
            r"save\s+(?:that|this)(?:\s+as|\s+to|\s+into|\s+in|\b)|"
            r"save\s+it(?:\s+as|\s+to|\s+into|\s+in|\b)|"
            r"savee\s+(?:that|this|it)(?:\s+as|\s+to|\s+into|\s+in|\b)|"
            r"save\s+(?:the\s+)?previous\s+(?:content|text|answer|response|summary|explanation)(?:\s+as|\s+to|\s+into|\s+in|\b)|"
            r"save\s+previous\s+content(?:\s+as|\s+to|\s+into|\s+in|\b)|"
            r"save\s+last\s+content(?:\s+as|\s+to|\s+into|\s+in|\b)|"
            r"(?:the\s+)?previous\s+content|"
            r"(?:the\s+)?last\s+content|"
            r"put\s+it\s+in(?:to)?\s+(?:my\s+)?(?:a\s+)?(?:file|note|notes)|"
            r"create\s+(?:a\s+)?note\s+from\s+it|"
            r"make\s+that\s+a\s+note|"
            r"(?:the\s+)?previous\s+(?:summary|response|answer|explanation)|"
            r"(?:the\s+)?last\s+(?:summary|response|answer|explanation)|"
            r"your\s+previous\s+(?:summary|response|answer|explanation)|"
            r"that\s+into\s+(?:a\s+)?(?:file|note)|"
            r"save\s+that\s+in(?:to)?\s+(?:my\s+)?(?:a\s+)?(?:file|note|notes)|"
            r"save\s+that\s+to\s+(?:my\s+)?(?:a\s+)?(?:file|note|notes)|"
            r"put\s+that\s+in(?:to)?\s+(?:my\s+)?(?:a\s+)?(?:file|note|notes)|"
            r"put\s+this\s+in(?:to)?\s+(?:my\s+)?(?:a\s+)?(?:file|note|notes)|"
            r"add\s+(?:that|this|it)\s+to\s+(?:my\s+)?(?:a\s+)?(?:file|note|notes)|"
            r"add\s+(?:that|this|it)\s+in(?:to)?\s+(?:my\s+)?(?:a\s+)?(?:file|note|notes)|"
            r"write\s+your\s+previous\s+(?:answer|response|summary|explanation)\s+to\s+(?:a\s+)?file|"
            r"use\s+your\s+previous\s+response|"
            r"put\s+that\s+into\s+(?:a\s+)?file|"
            r"put\s+(?:that|this|it)\s+in(?:to)?\s+\S+\.\w{2,10}|"
            r"use\s+that\s+as\s+(?:the\s+)?content"
            r")\b",
            lowered,
        )
    )


def looks_like_ambiguous_reference_only(message: str) -> bool:
    lowered = message.lower()
    if not re.search(r"\b(that|this|it|previous|last)\b", lowered):
        return False
    if re.search(
        r"\b(joke|summary|text|answer|response|explanation|script|paragraph|note|content)\b",
        lowered,
    ):
        return False
    return bool(
        re.search(
            r"\b(?:save|put|write|add|use|make|create)\b.*\b(?:that|this|it|previous\s+(?:one|thing)|last\s+(?:one|thing))\b",
            lowered,
        )
        and not message_requests_referenced_content(message)
    )


def looks_like_plural_reference_request(message: str) -> bool:
    lowered = message.lower()
    if not re.search(r"\b(those|these|both|all)\b", lowered):
        return False
    return bool(
        re.search(
            r"\b(?:save|put|write|add|use|make|create)\b.*\b(?:those|these|both|all)\b",
            lowered,
        )
    )


def looks_like_non_authored_assistant_message(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return True
    if re.search(r"\byour linked .+ job completed successfully\b", lowered):
        return True
    if re.search(r"\byour linked request is paused pending approval\b", lowered):
        return True
    if re.search(r"\byour linked .+ job failed\b", lowered):
        return True
    if re.search(r"\bwrote text to\b", lowered):
        return True
    if re.search(r"\bi(?:'ve| have)\s+updated\s+the\s+draft\b", lowered):
        return True
    if re.search(r"\bupdated\s+the\s+draft\s+for\b", lowered):
        return True
    if re.search(r"\blet me know when you(?:'re| are)\s+ready\s+to\s+save\b", lowered):
        return True
    if re.search(r"\bready to save (?:it|this|that)\b", lowered):
        return True
    if re.search(r"\bready to submit\b", lowered):
        return True
    if re.search(r"\bsend it whenever you(?:'re| are)\s+ready\b", lowered):
        return True
    if re.search(r"\byou can see the current draft\b", lowered):
        return True
    if re.search(r"\blet me know if you(?:'d| would)\s+like to change\b", lowered):
        return True
    if re.search(r"\bi(?:'ve| have)\s+drafted\s+.+\s+for\s+you\b", lowered):
        return True
    if re.search(r"\bprepared\s+(?:a|the)\s+preview\b", lowered):
        return True
    non_authored_patterns = (
        r"\bi submitted the job to voxeraos\b",
        r"\bjob id:\b",
        r"\bthe request is now in the queue\b",
        r"\bexecution has not completed yet\b",
        r"\bnothing has been submitted\b",
        r"\bi still have the current request ready\b",
        r"\bi prepared a governed save-to-note preview\b",
        r"\bpreview-only\b",
        r"\bprepared preview\b",
        r"\bcheck status and evidence\b",
        r"\bapproval status\b",
        r"\bexpected artifacts\b",
        r"\bqueue\s+state\b",
        r"\bmode status\b",
        # Surfaced runtime/result output — file stat, existence, listing, evidence
        r"\btype=\w+\s+size=\d+",
        r"\bdoes not exist\.\s*$",
        r"^\S+\s+exists\s*(?:\(\w+\))?\.\s*$",
        r"\b\d+\s+entries?\b.*\bnames?:",
        r"\bi reviewed canonical voxeraos evidence\b",
        r"\b- state:\s+`",
        r"\b- lifecycle state:\s+`",
        r"\b- terminal outcome:\s+`",
        r"\bnext step:\b.*\b(?:approval|submit|rerun|inspect|draft a follow-up)\b",
        r"\bi have the canonical result available for follow-up\b",
        r"\bcanonical evidence highlights\b",
        r"\bdiagnostics snapshot\b",
        r"\bthere is no active draft or preview\b",
    )
    return any(re.search(pattern, lowered) for pattern in non_authored_patterns)


def _looks_like_trivial_courtesy_assistant_message(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return True
    normalized = re.sub(r"\s+", " ", lowered.replace("—", "-")).strip()
    courtesy_prefixes = (
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
    if any(normalized.startswith(prefix) for prefix in courtesy_prefixes):
        if len(normalized.split()) <= 24:
            return True
        if any(
            phrase in normalized
            for phrase in (
                "if you'd like",
                "if you would like",
                "let me know",
                "feel free",
                "i can save that",
                "i can also",
            )
        ):
            return True
    return False


def _infer_saveable_assistant_artifact_type(text: str) -> str:
    lowered = text.strip().lower()
    if any(token in lowered for token in ("# investigation comparison", "compared results:")):
        return "comparison"
    if any(
        token in lowered
        for token in ("# investigation summary", "selected results:", "short takeaway:")
    ):
        return "summary"
    if "# expanded investigation result" in lowered:
        return "expanded_result"
    if any(token in lowered for token in ("# ", "essay", "article", "writeup")):
        if "article" in lowered:
            return "article"
        if "essay" in lowered:
            return "essay"
        return "writeup"
    if "script" in lowered and "explanation" in lowered:
        return "code_explanation"
    if "explanation" in lowered or lowered.startswith("because ") or lowered.startswith("a "):
        return "explanation"
    return "info"


def build_saveable_assistant_artifact(text: str) -> dict[str, str] | None:
    candidate = text.strip()
    if not candidate:
        return None

    extracted = extract_text_draft_from_reply(candidate)
    cleaned = extracted or candidate
    cleaned = _strip_trailing_control_text(cleaned)
    cleaned = cleaned.strip()
    if not cleaned:
        return None
    if looks_like_non_authored_assistant_message(cleaned):
        return None
    if _looks_like_trivial_courtesy_assistant_message(cleaned):
        return None
    normalized = re.sub(r"\s+", " ", cleaned.replace("—", "-")).strip().lower()
    if any(re.fullmatch(pattern, normalized) for pattern in _LOW_INFORMATION_ASSISTANT_PATTERNS):
        return None

    words = cleaned.split()
    if len(words) < 2:
        return None
    if len(cleaned) < 8:
        return None
    if len(words) <= 5 and not re.search(r"[.!?:\n]", cleaned):
        return None

    artifact_type = _infer_saveable_assistant_artifact_type(cleaned)
    return {"content": cleaned, "artifact_type": artifact_type}


def collect_recent_saveable_assistant_artifacts(
    assistant_content_candidates: list[str] | None,
) -> list[dict[str, str]]:
    if not assistant_content_candidates:
        return []
    artifacts: list[dict[str, str]] = []
    for raw in assistant_content_candidates[-8:]:
        artifact = build_saveable_assistant_artifact(raw)
        if artifact is None:
            continue
        artifacts.append(artifact)
    return artifacts[-6:]


def select_recent_saveable_assistant_artifact(
    *, message: str, assistant_artifacts: list[dict[str, str]] | None
) -> dict[str, str] | None:
    if not assistant_artifacts:
        return None
    if not message_requests_referenced_content(message):
        return None

    preferred_type: str | None = None
    if re.search(r"\b(summary|summari[sz]e|synthesis|overview|recap)\b", message, re.IGNORECASE):
        preferred_type = "summary"
    elif re.search(r"\bcomparison\b", message, re.IGNORECASE):
        preferred_type = "comparison"
    elif re.search(r"\b(article|essay|writeup)\b", message, re.IGNORECASE):
        preferred_type = "article"
    elif re.search(r"\bexplanation\b", message, re.IGNORECASE):
        preferred_type = "explanation"

    vague_reference_only = bool(
        re.search(r"\b(save|write|put|create|make)\b", message, re.IGNORECASE)
        and re.search(r"\b(that|this|it)\b", message, re.IGNORECASE)
        and not re.search(
            r"\b(summary|answer|response|text|content|artifact|essay|article|explanation)\b",
            message,
            re.IGNORECASE,
        )
    )
    plural_or_explicitly_ambiguous_reference = bool(
        looks_like_plural_reference_request(message)
        or re.search(r"\b(previous|last)\s+(two|2|few|several|multiple)\b", message, re.IGNORECASE)
        or re.search(r"\b(earlier\s+one|older\s+one|prior\s+one)\b", message, re.IGNORECASE)
    )

    viable = list(reversed(assistant_artifacts[-6:]))
    if not viable:
        return None
    if plural_or_explicitly_ambiguous_reference:
        return None
    if preferred_type is not None:
        preferred_matches = []
        for artifact in viable:
            artifact_type = str(artifact.get("artifact_type") or "")
            is_article_match = preferred_type == "article" and artifact_type in {
                "article",
                "essay",
                "writeup",
            }
            is_explanation_match = preferred_type == "explanation" and artifact_type in {
                "explanation",
                "code_explanation",
            }
            if is_article_match or is_explanation_match or artifact_type == preferred_type:
                preferred_matches.append(artifact)
        if preferred_matches:
            return preferred_matches[0]
    if vague_reference_only:
        return viable[0]
    return viable[0]
