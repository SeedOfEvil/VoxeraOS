from __future__ import annotations

import json
import re

from ..vera.draft_revision import looks_like_preview_rename_or_save_as_request


def looks_like_voxera_preview_dump(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return False
    if (
        "proposed voxeraos job" in lowered
        or "proposal for voxeraos" in lowered
        or "submit-ready voxeraos preview" in lowered
    ):
        return True
    return "```json" in lowered and any(
        marker in lowered
        for marker in (
            '"goal"',
            '"write_file"',
            '"enqueue_child"',
            "voxeraos",
        )
    )


def looks_like_preview_update_claim(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return False
    if any(
        phrase in lowered
        for phrase in (
            "prepared a proposal",
            "prepared a preview",
            "prepared a draft",
            "prepared the following job",
            "drafted a proposal",
            "drafted a preview",
            "i drafted",
            "i've drafted",
            "i have drafted",
            "i've prepared",
            "i have prepared",
            "created a preview",
            "created a draft",
            "set up a preview",
            "set up a draft",
            "here is the prepared proposal",
            "here is the json",
            "here's a draft",
            "here is a draft",
            "updated the draft",
            "updated the preview",
            "preview is ready",
            "draft is ready",
            "preview ready",
            "latest version is ready in the preview",
            "proposal in the preview",
            "refined the proposal in the preview",
            # Active-preview append / expand claim phrases — when the LLM
            # says it added/appended/expanded the list/content/draft without
            # the binding layer actually mutating the preview, these would
            # leak as a false-success reply if the conversational reply
            # reshape did not take over.
            "added to the list",
            "added to the content",
            "added to the draft",
            "added to the note",
            "added to the file",
            "added to the preview",
            "appended to the list",
            "appended to the content",
            "appended to the draft",
            "appended to the note",
            "expanded the list",
            "expanded the content",
            "expanded the draft",
            "expanded the note",
            "expanded the preview",
            "extended the list",
            "extended the content",
            "extended the draft",
        )
    ):
        return True
    # Numeric "added N <items>" / "appended N <items>" false-success claims.
    # "I've added 20 jokes", "appended 5 bullets", "added 20 additional jokes",
    # "added 10 more dad jokes", "this brings the total to 30".  Allows filler
    # adjectives (more|additional|new|extra|further|another) and up to two
    # arbitrary descriptive adjectives between the count and the item noun.
    if re.search(
        r"\b(?:added|appended|extended|expanded\s+with)\s+\d+\s+"
        r"(?:(?:more|additional|new|extra|further|another)\s+)?"
        r"(?:[a-z][a-z-]*\s+){0,2}"
        r"(?:jokes?|jokees?|jokeys?|items?|bullets?|examples?|lines?|entries?|"
        r"points?|facts?|stories?|poems?|things?|stanzas?|verses?|paragraphs?|"
        r"sentences?|steps?|ideas?|tips?|quotes?|rows?)\b",
        lowered,
    ):
        return True
    return bool(
        re.search(
            r"\bthis\s+brings\s+the\s+(?:total|list|count)\s+to\s+\d+\b",
            lowered,
        )
    )


def _looks_like_submission_claim(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return False
    suspicious_phrases = (
        "submitted to voxeraos",
        "submitted the job",
        "request is now in the queue",
        "handed off",
        "queued",
        "sent it to voxeraos",
        "sent it to the queue",
        "i sent it",
        "i queued it",
    )
    return any(phrase in lowered for phrase in suspicious_phrases)


def _text_outside_code_blocks(text: str) -> str:
    return re.sub(r"```[^\n]*\n.*?```", "", text, flags=re.DOTALL).strip()


def _looks_like_preview_pane_claim(text: str) -> bool:
    outside = _text_outside_code_blocks(text)
    lowered = outside.lower()
    if not lowered:
        return False
    claim_phrases = (
        "preview pane",
        "preview panel",
        "in the preview",
        "in your preview",
        "review it in",
        "check the preview",
        "available in preview",
        "visible in preview",
        "find it in the preview",
        "see it in the preview",
    )
    if any(p in lowered for p in claim_phrases):
        return True
    return looks_like_preview_update_claim(text)


def looks_like_preview_pane_claim(text: str) -> bool:
    return _looks_like_preview_pane_claim(text)


_PREVIEW_PANE_SENTENCE_RE = re.compile(
    r"(?:^|\n)\s*[^\n]*?\b("
    r"preview\s+pane|preview\s+panel|"
    r"in\s+the\s+preview|in\s+your\s+preview|"
    r"check\s+the\s+preview|available\s+in\s+preview|"
    r"visible\s+in\s+preview|find\s+it\s+in\s+the\s+preview|"
    r"see\s+it\s+in\s+the\s+preview|"
    r"review\s+it\s+in\s+the\s+preview"
    r")\b[^\n]*",
    re.IGNORECASE,
)

_FALSE_CLAIM_PHRASES = (
    "prepared a proposal",
    "prepared a preview",
    "prepared a draft",
    "prepared the following job",
    "drafted a proposal",
    "drafted a preview",
    "i drafted",
    "i've drafted",
    "i have drafted",
    "i've prepared",
    "i have prepared",
    "created a preview",
    "created a draft",
    "set up a preview",
    "set up a draft",
    "here is the prepared proposal",
    "here is the json",
    "here's a draft",
    "here is a draft",
    "updated the draft",
    "updated the preview",
    "preview is ready",
    "draft is ready",
    "preview ready",
    "latest version is ready in the preview",
    "proposal in the preview",
    "refined the proposal in the preview",
    "submitted to voxeraos",
    "submitted the job",
    "submitted that",
    "submitted the checklist",
    "submitted your",
    "submitted it to",
    "request is now in the queue",
    "handed off to voxeraos",
    "handed it off",
    "sent it to voxeraos",
    "sent it to the queue",
    "sent to the queue",
    "i sent it",
    "i queued it",
    "added to the queue",
    "added it to the queue",
    "it to the queue",
    "ready to submit",
    "submit whenever",
    "you can submit",
    "you can send it",
    "i'll submit",
    "i can submit",
    "i will submit",
    "i'll send it",
    "i can send it",
    "i will send it",
    "save this when",
    "save it when",
    "i can save",
    "i'll save",
    "i will save",
    "save this for",
    "save it for",
    "when you're ready",
    "whenever you're ready",
    "when you are ready",
    "whenever you are ready",
    "before we save",
    "before saving",
    "ready to save",
    "want me to save",
    "shall i save",
    "would you like me to save",
    "does this look right",
    "does this look good",
    "does that look right",
    "does that look good",
    "look right before",
    "look good before",
    "take a look",
    "let me know when",
    "let me know if you'd like",
    "let me know if you want",
)

_PREVIEW_OR_DRAFT_REFERENCE_LINE_RE = re.compile(
    r"(?:^|\n)\s*(?![0-9]+[.\)]\s|-\s|\*\s|\[[ x]\])[^\n]*?"
    r"\b(?:the\s+preview|the\s+draft|the\s+preview\s+pane|system\s+queue)\b[^\n]*",
    re.IGNORECASE,
)

_HARD_BANNED_CONVERSATIONAL_TOKENS_RE = re.compile(
    r"\b(?:preview|draft|submit|submitted|submission|queue|queued)\b",
    re.IGNORECASE,
)

_WORKFLOW_NARRATION_LINE_RE = re.compile(
    r"(?i)"
    r"(?:"
    r"when(?:ever)?\s+you(?:'re|\s+are)\s+ready"
    r"|before\s+(?:we\s+)?sav(?:e|ing)"
    r"|(?:want|shall|would you like)\s+me\s+to\s+save"
    r"|i\s+can\s+save"
    r"|i'?ll\s+save"
    r"|ready\s+to\s+save"
    r"|let\s+me\s+know\s+(?:when|if)"
    r"|does\s+(?:this|that)\s+look\s+(?:right|good|ok)"
    r"|look\s+(?:right|good)\s+before"
    r"|take\s+a\s+look"
    r")",
)

_META_COMMENTARY_RE = re.compile(
    r"(?i)^(?:"
    r"i'?ve\s+(?:organized|grouped|categorized|arranged|sorted|structured|compiled|put together|"
    r"broken\s+(?:it|this|them)\s+down|laid\s+(?:it|this|them)\s+out|"
    r"set\s+(?:it|this|them)\s+up|listed|prepared|created|made|built)"
    r"|i\s+(?:organized|grouped|categorized|arranged|sorted|structured|compiled|"
    r"broke\s+(?:it|this|them)\s+down|laid\s+(?:it|this|them)\s+out)"
    r"|here(?:'s|\s+is)\s+(?:a\s+)?(?:quick\s+)?(?:summary|overview|breakdown|rundown)"
    r"|here(?:'s|\s+is)\s+what\s+i\s+(?:came\s+up\s+with|put\s+together|prepared)"
    r")\b"
)

_BARE_JSON_PAYLOAD_RE = re.compile(
    r'^\s*\{[^}]*"(?:intent|goal|action|write_file|enqueue_child)"',
)

_FILE_RESIDUE_ITEM_RE = re.compile(
    r"(?i)\b(?:create[_ -]?file|write_file|enqueue_child|payload|intent|goal|action)\b|"
    r"\.(?:md|txt|json)\b"
)

_CONVERSATIONAL_PLANNING_RE = re.compile(
    r"\b("
    r"checklist|check\s*list|prep\s+list|to[- ]?do\s+list|action\s+items|"
    r"grocery\s+list|packing\s+list|shopping\s+list|bucket\s+list|"
    r"(?:plan|steps|ideas|suggestions|tips)\s+for\b|"
    r"steps\s+to\b|"
    r"brainstorm|"
    r"help\s+me\s+(?:organize|plan|prepare|think\s+through|figure\s+out|prioritize|"
    r"get\s+\S+.*?\b(?:going|started|set\s+up))|"
    r"give\s+me\s+(?:a\s+)?(?:plan|list|steps|ideas|suggestions|tips)|"
    r"make\s+(?:me\s+)?a\s+(?:plan|list|checklist)|"
    r"create\s+a\s+(?:plan|list|checklist)|"
    r"what\s+(?:do\s+i|should\s+i)\s+need\s+(?:to|for)|"
    r"itinerary|"
    r"organize\s+(?:my|the|a)\b|"
    r"prepare\s+(?:a|my)\s+(?:plan|list)|"
    r"to\s+do(?:\s+for)?\b|"
    r"prep(?:aration)?\s+(?:list|plan|guide)|"
    r"(?:workout|training|fitness|exercise|study|meal|revision|review)\s+"
    r"(?:plan|routine|program|course|schedule|regimen)"
    r")\b",
    re.IGNORECASE,
)

_SAVE_WRITE_FILE_SIGNAL_RE = re.compile(
    r"\b("
    r"save\s+(?:that|this|it|as|to|into)|"
    r"save\s+\S+.*?\b(?:to|as|into)\s+(?:a\s+)?(?:my\s+)?(?:file|note|notes)\b|"
    r"write\s+(?:that|this|it)\s+(?:to|into)|"
    r"write\s+(?:to|into)\s+(?:a\s+)?(?:file|note)|"
    r"write\s+\S+.*?\b(?:to|as|into)\s+(?:a\s+)?(?:my\s+)?(?:file|note|notes)\b|"
    r"create\s+(?:a\s+)?(?:file|note)\s+(?:called|named|with)|"
    r"put\s+(?:that|this|it)\s+in(?:to)?\s+(?:a\s+)?(?:file|note)|"
    r"as\s+a\s+(?:file|note|markdown)\b|"
    r"\.(?:md|txt|json|ps1|sh|py)\b"
    r")\b",
    re.IGNORECASE,
)


def has_save_write_file_signal(message: str) -> bool:
    return _SAVE_WRITE_FILE_SIGNAL_RE.search(message) is not None


def has_conversational_planning_signal(message: str) -> bool:
    return _CONVERSATIONAL_PLANNING_RE.search(message) is not None


def _is_list_item_line(line: str) -> bool:
    stripped = line.lstrip()
    if not stripped:
        return False
    if re.match(r"[0-9]+[.\)]\s", stripped):
        return True
    if stripped.startswith(("- ", "* ", "• ")):
        return True
    if re.match(r"\[[ x]\]\s", stripped):
        return True
    return bool(re.match(r"-\s+\[[ x]\]\s", stripped))


def _has_list_content(text: str) -> bool:
    return any(_is_list_item_line(ln) for ln in text.split("\n"))


def _extract_list_items(text: str) -> list[str]:
    items: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        m = re.match(r"[0-9]+[.\)]\s+(.*)", stripped)
        if m:
            items.append(m.group(1).strip())
            continue
        m = re.match(r"-\s+\[[ x]\]\s+(.*)", stripped)
        if m:
            items.append(m.group(1).strip())
            continue
        m = re.match(r"\[[ x]\]\s+(.*)", stripped)
        if m:
            items.append(m.group(1).strip())
            continue
        m = re.match(r"[-*•]\s+(.*)", stripped)
        if m:
            items.append(m.group(1).strip())
            continue
    return [item for item in items if item]


def _render_plain_checklist(items: list[str]) -> str:
    numbered = "\n".join(f"{idx}. {item}" for idx, item in enumerate(items, start=1))
    return f"Here's your checklist:\n\n{numbered}"


def _normalize_extracted_item(item: str) -> str:
    cleaned = re.sub(r"\s+", " ", item.strip(" \t-–—•*.,;:!?\"'`()[]{}")).strip()
    cleaned = re.sub(r"^(?:and|also)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+(?:and)\s*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("+1", "plus-one")
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    if lowered in {
        "and",
        "or",
        "to",
        "for",
        "with",
        "it",
        "this",
        "that",
        "do",
        "things",
        "many things i need to do",
    }:
        return ""
    if _FILE_RESIDUE_ITEM_RE.search(cleaned):
        return ""
    return cleaned[0].upper() + cleaned[1:] if cleaned else ""


def _split_candidate_items(segment: str) -> list[str]:
    parts: list[str] = []
    for chunk in re.split(r"[,;\n]+", segment):
        chunk = chunk.strip()
        if not chunk:
            continue
        for and_chunk in re.split(r"\s+\band\b\s+", chunk, flags=re.IGNORECASE):
            normalized = _normalize_extracted_item(and_chunk)
            if normalized:
                parts.append(normalized)
    return parts


def _clean_item_candidates(items: list[str]) -> list[str]:
    cleaned_items: list[str] = []
    seen: set[str] = set()
    for raw in items:
        normalized = _normalize_extracted_item(raw)
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned_items.append(normalized)
    return cleaned_items


def _extract_items_from_user_message(user_message: str) -> list[str]:
    text = user_message.strip()
    if not text:
        return []

    extracted: list[str] = []
    explicit_following = re.search(r"(?:following|include|items?)\s*:\s*(.+)$", text, re.IGNORECASE)
    if explicit_following:
        extracted.extend(_split_candidate_items(explicit_following.group(1)))

    if not extracted:
        need_to_parts = re.split(
            r"\b(?:first\s+i\s+need\s+to|i\s+also\s+need\s+to|i\s+need\s+to)\b",
            text,
            flags=re.IGNORECASE,
        )
        if len(need_to_parts) > 1:
            for part in need_to_parts[1:]:
                extracted.extend(_split_candidate_items(part))

    if not extracted:
        need_list = re.search(r"\bi\s+need\s+(.+)$", text, re.IGNORECASE)
        if need_list and "," in need_list.group(1):
            extracted.extend(_split_candidate_items(need_list.group(1)))

    return _clean_item_candidates(extracted)


def _extract_json_object_items(text: str) -> list[str]:
    if not text.strip():
        return []
    candidates: list[str] = []
    fenced_blocks = re.findall(r"```json\s*\n(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    candidates.extend(fenced_blocks)
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)

    items: list[str] = []
    for raw in candidates:
        try:
            parsed = json.loads(raw)
        except Exception:
            continue
        if isinstance(parsed, dict):
            for key in ("items", "checklist", "steps", "tasks", "todo", "to_do"):
                value = parsed.get(key)
                if isinstance(value, list):
                    for entry in value:
                        cleaned = str(entry).strip()
                        if cleaned:
                            normalized = _normalize_extracted_item(cleaned)
                            if normalized:
                                items.append(normalized)
            if not items:
                for value in parsed.values():
                    if isinstance(value, str) and value.strip():
                        normalized = _normalize_extracted_item(value)
                        if normalized:
                            items.append(normalized)
        elif isinstance(parsed, list):
            for entry in parsed:
                cleaned = str(entry).strip()
                normalized = _normalize_extracted_item(cleaned)
                if normalized:
                    items.append(normalized)
    return items


def _fallback_conversational_checklist_for_message(user_message: str) -> str:
    lowered = user_message.lower()
    if "wedding" in lowered:
        return "\n".join(
            (
                "- Finalize guest list and plus-one confirmations",
                "- Confirm attire, fittings, and accessories",
                "- Book travel, lodging, and local transportation",
                "- Verify venue timeline and vendor confirmations",
                "- Prepare required documents and day-of essentials",
            )
        )
    if any(token in lowered for token in ("grocery", "shopping list", "meal prep", "supermarket")):
        return "\n".join(
            (
                "- Produce: fruit, leafy greens, onions, and tomatoes",
                "- Protein: eggs, chicken or tofu, and yogurt",
                "- Pantry: rice, pasta, beans, and cooking oil",
                "- Dairy/alternatives: milk, cheese, and butter",
                "- Household: paper goods and cleaning supplies",
            )
        )
    return "\n".join(
        (
            "- Define the goal and outcome",
            "- Break the work into concrete steps",
            "- Order steps by priority and dependencies",
            "- Add owners, dates, or needed resources",
            "- Review and adjust after first progress check",
        )
    )


def sanitize_false_preview_claims_from_answer(text: str) -> str:
    if not text.strip():
        return text

    cleaned = re.sub(r"```json\s*\n.*?```", "", text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"^\s*\{[^}]*\n(?:[^}]*\n)*[^}]*\}", "", cleaned, flags=re.MULTILINE)

    lowered_full = cleaned.lower()
    has_false_claim = (
        _looks_like_preview_pane_claim(cleaned)
        or _looks_like_submission_claim(cleaned)
        or any(p in lowered_full for p in _FALSE_CLAIM_PHRASES)
        or _PREVIEW_OR_DRAFT_REFERENCE_LINE_RE.search(cleaned) is not None
    )
    if has_false_claim:
        cleaned = _PREVIEW_PANE_SENTENCE_RE.sub("", cleaned)
        cleaned = _PREVIEW_OR_DRAFT_REFERENCE_LINE_RE.sub("", cleaned)
        out_lines: list[str] = []
        for line in cleaned.split("\n"):
            lowered_line = line.lower()
            if any(phrase in lowered_line for phrase in _FALSE_CLAIM_PHRASES):
                continue
            out_lines.append(line)
        cleaned = "\n".join(out_lines)

    hard_lines: list[str] = []
    for line in cleaned.split("\n"):
        if _is_list_item_line(line):
            hard_lines.append(line)
            continue
        if _HARD_BANNED_CONVERSATIONAL_TOKENS_RE.search(line):
            continue
        hard_lines.append(line)
    cleaned = "\n".join(hard_lines)

    workflow_lines: list[str] = []
    for line in cleaned.split("\n"):
        if _is_list_item_line(line):
            workflow_lines.append(line)
            continue
        if _WORKFLOW_NARRATION_LINE_RE.search(line):
            continue
        workflow_lines.append(line)
    cleaned = "\n".join(workflow_lines)

    has_list_items = any(_is_list_item_line(ln) for ln in cleaned.split("\n"))
    if has_list_items:
        meta_lines: list[str] = []
        for line in cleaned.split("\n"):
            stripped_line = line.strip()
            if stripped_line and _META_COMMENTARY_RE.match(stripped_line):
                continue
            meta_lines.append(line)
        cleaned = "\n".join(meta_lines)

    json_lines: list[str] = []
    for line in cleaned.split("\n"):
        if _BARE_JSON_PAYLOAD_RE.match(line):
            continue
        json_lines.append(line)
    cleaned = "\n".join(json_lines)

    cleaned = cleaned.strip()
    if cleaned:
        return cleaned

    items = _extract_list_items(text)
    if items:
        return _render_plain_checklist(items)
    if _HARD_BANNED_CONVERSATIONAL_TOKENS_RE.search(text):
        return "Here's what I have so far — could you share more details so I can put together the list?"
    return text


def enforce_conversational_checklist_output(
    text: str, *, raw_answer: str, user_message: str
) -> str:
    user_items = _extract_items_from_user_message(user_message)
    if len(user_items) >= 2:
        return _render_plain_checklist(user_items)

    if not text.strip():
        items = _clean_item_candidates(
            _extract_list_items(raw_answer) or _extract_json_object_items(raw_answer)
        )
        if items:
            return _render_plain_checklist(items)
        return _fallback_conversational_checklist_for_message(user_message)

    has_violation = False
    for line in text.split("\n"):
        if _is_list_item_line(line):
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if _HARD_BANNED_CONVERSATIONAL_TOKENS_RE.search(stripped):
            has_violation = True
            break
        if _BARE_JSON_PAYLOAD_RE.match(stripped):
            has_violation = True
            break

    if has_violation:
        items = _clean_item_candidates(
            _extract_list_items(text)
            or _extract_list_items(raw_answer)
            or _extract_json_object_items(text)
            or _extract_json_object_items(raw_answer)
        )
        if items:
            return _render_plain_checklist(items)
        return _fallback_conversational_checklist_for_message(user_message)

    if not _has_list_content(text):
        items = _clean_item_candidates(
            _extract_json_object_items(text) or _extract_json_object_items(raw_answer)
        )
        if items:
            return _render_plain_checklist(items)
        return _fallback_conversational_checklist_for_message(user_message)
    return text


def is_conversational_answer_first_request(message: str) -> bool:
    text = message.strip()
    if not text:
        return False
    if has_save_write_file_signal(text):
        return False
    return has_conversational_planning_signal(text)


def should_use_conversational_artifact_mode(
    message: str,
    *,
    prior_planning_active: bool,
    pending_preview: dict[str, object] | None,
    is_recent_assistant_content_save_request: bool,
) -> bool:
    return (
        (is_conversational_answer_first_request(message) or prior_planning_active)
        and not is_recent_assistant_content_save_request
        and not has_save_write_file_signal(message)
        and pending_preview is None
    )


def conversational_preview_update_message(
    *,
    updated: bool,
    has_active_preview: bool,
    user_message: str,
    is_recent_assistant_content_save_request: bool,
    rejected: bool = False,
    updated_preview: dict[str, object] | None = None,
    preview_already_existed: bool = False,
) -> str:
    naming_request = looks_like_preview_rename_or_save_as_request(user_message)
    updated_write_file = (
        updated_preview.get("write_file") if isinstance(updated_preview, dict) else None
    )
    updated_path = (
        str(updated_write_file.get("path") or "").strip()
        if isinstance(updated_write_file, dict)
        else ""
    )
    if rejected:
        return (
            "I couldn’t apply that update — the requested path is outside the safe notes workspace "
            "or contains an invalid traversal. The existing draft is unchanged."
        )
    if updated:
        if naming_request and updated_path:
            return (
                f"Updated the draft destination to `{updated_path}`. "
                "This is preview-only — nothing has been submitted yet."
            )
        if preview_already_existed:
            return (
                "I’ve updated the preview with your changes. "
                "This is still preview-only — nothing has been submitted yet."
            )
        return (
            "I’ve prepared a preview of your request. "
            "This is preview-only — nothing has been submitted yet. "
            "Let me know when you’d like to send it."
        )
    if naming_request and has_active_preview:
        return (
            "I couldn’t safely apply that naming update, so the draft destination is unchanged. "
            "Please provide a specific filename (for example: name it bigvolcano.txt)."
        )
    if has_active_preview:
        return (
            "The current draft is still in the preview, unchanged. "
            "Nothing has been submitted. Let me know when you’d like to proceed."
        )
    if is_recent_assistant_content_save_request:
        return (
            "I couldn’t find a recent response to save in this session. "
            "Could you point to a specific response or ask me to generate something first?"
        )
    return "I wasn’t able to prepare a preview for this request. Could you share more details so I can try again?"
