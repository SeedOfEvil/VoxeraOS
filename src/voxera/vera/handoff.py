from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.file_intent import classify_bounded_file_intent
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
    "file_organize",
    "steps",
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


def _normalize_extracted_content_block(candidate: str) -> str | None:
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
    if _message_requests_referenced_content(value) or _looks_like_ambiguous_reference_only(value):
        return None
    return value


def _extract_content_after_markers(text: str, markers: tuple[str, ...]) -> str | None:
    for marker in markers:
        match = re.search(marker, text, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        candidate = _normalize_extracted_content_block(match.group(1))
        if candidate:
            return candidate
    return None


def _message_requests_referenced_content(message: str) -> bool:
    lowered = message.lower()
    if not re.search(r"\b(that|this|previous|last|your)\b", lowered):
        return False
    return bool(
        re.search(
            r"\b(that\s+(?:joke|summary|text|answer|response)|that\s+into\s+(?:a\s+)?file|"
            r"use\s+your\s+previous\s+response|previous\s+(?:response|answer)|last\s+(?:response|answer)|"
            r"put\s+that\s+into\s+(?:a\s+)?file|use\s+that\s+as\s+(?:the\s+)?content)\b",
            lowered,
        )
    )


def _looks_like_ambiguous_reference_only(message: str) -> bool:
    lowered = message.lower()
    if not re.search(r"\b(that|this|it|previous|last)\b", lowered):
        return False
    if re.search(r"\b(joke|summary|text|answer|response|script|paragraph|note|content)\b", lowered):
        return False
    return bool(
        re.search(
            r"\b(?:save|put|write|add|use|make|create)\b.*\b(?:that|this|it|previous\s+(?:one|thing)|last\s+(?:one|thing))\b",
            lowered,
        )
        and not _message_requests_referenced_content(message)
    )


def _looks_like_non_authored_assistant_message(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return True
    non_authored_patterns = (
        r"\bi submitted the job to voxeraos\b",
        r"\bjob id:\b",
        r"\bthe request is now in the queue\b",
        r"\bexecution has not completed yet\b",
        r"\bcheck status and evidence\b",
        r"\bapproval status\b",
        r"\bexpected artifacts\b",
        r"\bqueue\s+state\b",
        r"\bmode status\b",
    )
    return any(re.search(pattern, lowered) for pattern in non_authored_patterns)


def _select_recent_assistant_content(
    *, message: str, assistant_content_candidates: list[str] | None
) -> str | None:
    if not assistant_content_candidates:
        return None
    if not _message_requests_referenced_content(message):
        return None
    for raw in reversed(assistant_content_candidates[-4:]):
        candidate = raw.strip()
        if not candidate or _looks_like_non_authored_assistant_message(candidate):
            continue
        return candidate
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


def is_investigation_save_request(message: str) -> bool:
    lowered = message.strip().lower()
    if not lowered:
        return False
    save_action = bool(re.search(r"\b(save|write|export)\b", lowered))
    findings_target = bool(re.search(r"\b(results?|findings?)\b", lowered))
    return save_action and findings_target


def _mentions_investigation_results_or_findings(message: str) -> bool:
    lowered = message.strip().lower()
    if not lowered:
        return False
    return bool(
        re.search(
            r"\b(result\s*\d+|results?|findings?|these\s+(?:results?|findings?)|all\s+(?:results?|findings?))\b",
            lowered,
        )
    )


def is_investigation_compare_request(message: str) -> bool:
    lowered = message.strip().lower()
    if not lowered:
        return False
    has_compare_signal = bool(
        re.search(
            r"\b(compare|different|difference|in\s+common|commonalities|commonality)\b",
            lowered,
        )
    )
    return has_compare_signal and _mentions_investigation_results_or_findings(lowered)


def is_investigation_summary_request(message: str) -> bool:
    lowered = message.strip().lower()
    if not lowered:
        return False
    has_summary_signal = bool(
        re.search(r"\b(summarize|summarise|summary|synthesis|common\s+thread)\b", lowered)
    )
    return has_summary_signal and _mentions_investigation_results_or_findings(lowered)


def is_investigation_derived_save_request(message: str) -> bool:
    lowered = message.strip().lower()
    if not lowered:
        return False
    save_action = bool(re.search(r"\b(save|write|export)\b", lowered))
    derived_target = bool(re.search(r"\b(that|comparison|summary)\b", lowered))
    note_target = bool(
        re.search(r"\b(note|notes|markdown|file)\b", lowered)
        or re.search(r"\b[~\/a-z0-9_.-]+\.md\b", lowered)
    )
    return save_action and derived_target and note_target


def _extract_result_selection(message: str) -> list[int] | str | None:
    lowered = message.strip().lower()
    if re.search(r"\b(all|everything)\b", lowered) and re.search(
        r"\b(results?|findings?)\b", lowered
    ):
        return "all"
    if re.search(r"\bthese\s+(results?|findings?)\b", lowered):
        return "all"

    explicit: set[int] = set()
    for match in re.finditer(r"\bresults?\s*(\d+(?:\s*(?:,|and)\s*\d+)*)", lowered):
        nums = re.findall(r"\d+", match.group(1))
        explicit.update(int(num) for num in nums)
    for match in re.finditer(r"\bresult\s*(\d+)\b", lowered):
        explicit.add(int(match.group(1)))

    if explicit:
        return sorted(explicit)
    return None


def select_investigation_results(
    message: str,
    *,
    investigation_context: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]] | None, list[int] | None]:
    if not isinstance(investigation_context, dict):
        return None, None
    results_raw = investigation_context.get("results")
    if not isinstance(results_raw, list) or not results_raw:
        return None, None

    results: list[dict[str, Any]] = []
    by_id: dict[int, dict[str, Any]] = {}
    for row in results_raw:
        if not isinstance(row, dict):
            continue
        result_id = int(row.get("result_id") or 0)
        if result_id <= 0:
            continue
        by_id[result_id] = row
        results.append(row)
    if not results:
        return None, None

    selection = _extract_result_selection(message)
    if selection == "all":
        selected = sorted(results, key=lambda r: int(r.get("result_id") or 0))
    elif isinstance(selection, list) and selection:
        if any(idx not in by_id for idx in selection):
            return None, None
        selected = [by_id[idx] for idx in selection]
    else:
        return None, None
    selected_ids = [int(item.get("result_id") or 0) for item in selected]
    return selected, selected_ids


def _investigation_note_content(*, query: str, selected: list[dict[str, Any]]) -> str:
    lines = ["# Investigation Results", "", "## Query", query, ""]
    for result in selected:
        rid = int(result.get("result_id") or 0)
        lines.extend(
            [
                f"## Result {rid}",
                f"- Title: {str(result.get('title') or '').strip()}",
                f"- Source: {str(result.get('source') or '').strip()}",
                f"- URL: {str(result.get('url') or '').strip()}",
                f"- Snippet: {str(result.get('snippet') or '').strip()}",
                f"- Why it matched: {str(result.get('why_it_matched') or '').strip()}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def draft_investigation_save_preview(
    message: str,
    *,
    investigation_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not is_investigation_save_request(message):
        return None
    selected, _ = select_investigation_results(message, investigation_context=investigation_context)
    if selected is None or not isinstance(investigation_context, dict):
        return None

    query = str(investigation_context.get("query") or "(unspecified query)").strip()
    target_match = re.search(
        r"\b(?:to|into|as)\s+([~\/a-zA-Z0-9_.-]+\.md)\b", message, re.IGNORECASE
    )
    output_path = (
        target_match.group(1).strip()
        if target_match
        else _generated_note_path().replace(".txt", ".md")
    )
    if not output_path.startswith("~") and not output_path.startswith("/"):
        output_path = f"~/VoxeraOS/notes/{output_path}"

    selected_ids = ", ".join(str(int(item.get("result_id") or 0)) for item in selected)
    return {
        "goal": f"write investigation findings ({selected_ids}) to markdown note",
        "write_file": {
            "path": output_path,
            "content": _investigation_note_content(query=query, selected=selected),
            "mode": "overwrite",
        },
    }


def draft_investigation_derived_save_preview(
    message: str,
    *,
    derived_output: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not is_investigation_derived_save_request(message):
        return None
    if not isinstance(derived_output, dict):
        return None
    markdown = str(derived_output.get("markdown") or "").strip()
    derivation_type = str(derived_output.get("derivation_type") or "").strip().lower()
    if not markdown or derivation_type not in {"comparison", "summary"}:
        return None

    target_match = re.search(
        r"\b(?:to|into|as)\s+([~\/a-zA-Z0-9_.-]+\.md)\b", message, re.IGNORECASE
    )
    output_path = (
        target_match.group(1).strip()
        if target_match
        else _generated_note_path().replace(".txt", ".md")
    )
    if not output_path.startswith("~") and not output_path.startswith("/"):
        output_path = f"~/VoxeraOS/notes/{output_path}"

    label = "comparison" if derivation_type == "comparison" else "summary"
    return {
        "goal": f"write investigation {label} to markdown note",
        "write_file": {
            "path": output_path,
            "content": markdown if markdown.endswith("\n") else f"{markdown}\n",
            "mode": "overwrite",
        },
    }


def _result_line(result: dict[str, Any]) -> str:
    rid = int(result.get("result_id") or 0)
    title = str(result.get("title") or "Untitled").strip()
    source = str(result.get("source") or "unknown").strip()
    snippet = str(result.get("snippet") or "").strip()
    return f"Result {rid}: {title} ({source}) — {snippet}"


def derive_investigation_comparison(
    message: str,
    *,
    investigation_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not is_investigation_compare_request(message):
        return None
    selected, selected_ids = select_investigation_results(
        message, investigation_context=investigation_context
    )
    if selected is None or selected_ids is None or not isinstance(investigation_context, dict):
        return None

    source_groups: dict[str, list[int]] = {}
    for row in selected:
        source = str(row.get("source") or "unknown").strip() or "unknown"
        source_groups.setdefault(source, []).append(int(row.get("result_id") or 0))

    query = str(investigation_context.get("query") or "(unspecified query)").strip()
    similarities = [
        f"All selected findings address query context: {query}",
        "All findings are from the active read-only investigation result set.",
    ]
    if len(source_groups) == 1:
        only_source = next(iter(source_groups))
        similarities.append(f"All selected findings share source domain: {only_source}.")
    else:
        similarities.append("Selected findings include multiple source domains.")

    differences = [
        _result_line(row) for row in sorted(selected, key=lambda r: int(r.get("result_id") or 0))
    ]
    source_distinctions = [
        f"- {source}: results {', '.join(str(i) for i in sorted(ids))}"
        for source, ids in sorted(source_groups.items())
    ]

    selected_label = ", ".join(str(x) for x in selected_ids)
    takeaway = (
        f"Compared {len(selected_ids)} selected findings; review source and snippet distinctions "
        "before any governed write action."
    )

    lines = [
        f"Compared results: {selected_label}",
        "Similarities:",
        *[f"- {item}" for item in similarities],
        "Differences:",
        *[f"- {item}" for item in differences],
        "Notable source distinctions:",
        *source_distinctions,
        f"Short overall takeaway: {takeaway}",
    ]
    answer = "\n".join(lines)

    markdown_lines = [
        "# Investigation Comparison",
        "",
        "## Query",
        query,
        "",
        "## Compared Results",
        selected_label,
        "",
        "## Similarities",
        *[f"- {item}" for item in similarities],
        "",
        "## Differences",
        *[f"- {item}" for item in differences],
        "",
        "## Notable source distinctions",
        *source_distinctions,
        "",
        "## Takeaway",
        takeaway,
        "",
    ]

    return {
        "derivation_type": "comparison",
        "query": query,
        "selected_result_ids": selected_ids,
        "answer": answer,
        "markdown": "\n".join(markdown_lines).rstrip() + "\n",
    }


def derive_investigation_summary(
    message: str,
    *,
    investigation_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not is_investigation_summary_request(message):
        return None
    selected, selected_ids = select_investigation_results(
        message, investigation_context=investigation_context
    )
    if selected is None or selected_ids is None or not isinstance(investigation_context, dict):
        return None

    key_points = [
        _result_line(row) for row in sorted(selected, key=lambda r: int(r.get("result_id") or 0))
    ]
    common_thread = (
        "Selected findings consistently match the active investigation query and should be treated "
        "as read-only evidence summaries."
    )
    takeaway = f"Summary synthesized from {len(selected_ids)} selected findings only."
    selected_label = ", ".join(str(x) for x in selected_ids)

    lines = [
        f"Selected results: {selected_label}",
        "Key points:",
        *[f"- {item}" for item in key_points],
        f"Common thread / synthesis: {common_thread}",
        f"Short takeaway: {takeaway}",
    ]
    answer = "\n".join(lines)

    query = str(investigation_context.get("query") or "(unspecified query)").strip()
    markdown_lines = [
        "# Investigation Summary",
        "",
        "## Query",
        query,
        "",
        "## Selected Results",
        selected_label,
        "",
        "## Summary",
        *[f"- {item}" for item in key_points],
        "",
        "## Common Thread",
        common_thread,
        "",
        "## Takeaway",
        takeaway,
        "",
    ]

    return {
        "derivation_type": "summary",
        "query": query,
        "selected_result_ids": selected_ids,
        "answer": answer,
        "markdown": "\n".join(markdown_lines).rstrip() + "\n",
    }


def _normalize_structured_file_write_payload(
    message: str,
    *,
    assistant_content_candidates: list[str] | None = None,
) -> dict[str, Any] | None:
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
    reference_requested = _message_requests_referenced_content(text)
    ambiguous_reference = _looks_like_ambiguous_reference_only(text)
    if content is None:
        content = _select_recent_assistant_content(
            message=text,
            assistant_content_candidates=assistant_content_candidates,
        )
    if content is None and (reference_requested or ambiguous_reference):
        return None
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
    named = re.search(
        r"\b(?:called|named|as|to|call\s+(?:it|that))\s+([^\s]+)",
        message,
        re.IGNORECASE,
    )
    if named:
        return named.group(1).strip("\"'.,!? ")
    tail = re.search(
        r"\b(?:rename|make\s+that|change\s+(?:the\s+)?(?:name|filename|file\s+name))\s+(?:it\s+)?([^\s]+)",
        message,
        re.IGNORECASE,
    )
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

    block_content = _extract_content_after_markers(
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
        candidate = _normalize_refinement_content_candidate(match.group(1))
        if candidate:
            return candidate

    return None


def _normalize_refinement_content_candidate(candidate: str) -> str | None:
    value = candidate.strip(" \"'`:\n\t")
    if not value:
        return None
    if re.fullmatch(r"(that|this|it|same|same thing)", value, re.IGNORECASE):
        return None
    if re.search(r"\bfile\s+called\b", value, re.IGNORECASE):
        return None
    if _message_requests_referenced_content(value) or _looks_like_ambiguous_reference_only(value):
        return None
    return value


def _extract_semantic_content_request(lowered: str) -> str | None:
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


def _refined_content_from_active_preview(
    *,
    text: str,
    lowered: str,
    existing_content: str,
) -> str | None:
    explicit = _extract_content_refinement(text, lowered)
    if explicit:
        return explicit

    semantic = _extract_semantic_content_request(lowered)
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


def _draft_revision_from_active_preview(
    message: str,
    active_preview: dict[str, Any] | None,
    *,
    enrichment_context: dict[str, Any] | None = None,
    assistant_content_candidates: list[str] | None = None,
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

    if re.search(
        r"\b(rename|make\s+that|call\s+(?:it|that)|change\s+(?:the\s+)?(?:name|filename|file\s+name))\b",
        lowered,
    ):
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
        filename = _filename_from_preview(active_preview) or "note.txt"
        write_file = active_preview.get("write_file")
        mode = "overwrite"
        existing_content = ""
        if isinstance(write_file, dict):
            path = str(write_file.get("path") or f"~/VoxeraOS/notes/{filename}")
            mode = str(write_file.get("mode") or "overwrite")
            existing_content = str(write_file.get("content") or "")
        else:
            path = f"~/VoxeraOS/notes/{filename}"

        refined_content = _extract_content_refinement(text, lowered, filename_hint=filename)
        if not refined_content:
            refined_content = _refined_content_from_active_preview(
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
            refined_content = _select_recent_assistant_content(
                message=text,
                assistant_content_candidates=assistant_content_candidates,
            )
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
    candidate: str,
    *,
    active_preview: dict[str, Any] | None,
    enrichment_context: dict[str, Any] | None = None,
    assistant_content_candidates: list[str] | None = None,
) -> dict[str, Any] | None:
    revision = _draft_revision_from_active_preview(
        candidate,
        active_preview,
        enrichment_context=enrichment_context,
        assistant_content_candidates=assistant_content_candidates,
    )
    if revision is not None:
        return revision

    normalized_open = _normalize_open_goal(candidate)
    if normalized_open:
        return {"goal": normalized_open}

    # Bounded file intent: exists, stat, read, mkdir, delete, copy, move, archive
    # Must run before the generic file-read goal so that info/stat/read intents
    # route to bounded skills instead of falling through to a generic goal string.
    bounded_file = classify_bounded_file_intent(candidate)
    if bounded_file is not None:
        return bounded_file

    normalized_read = _normalize_file_read_goal(candidate)
    if normalized_read:
        return {"goal": normalized_read}

    structured_write = _normalize_structured_file_write_payload(
        candidate, assistant_content_candidates=assistant_content_candidates
    )
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
    enrichment_context: dict[str, Any] | None = None,
    investigation_context: dict[str, Any] | None = None,
    recent_assistant_messages: list[str] | None = None,
) -> dict[str, Any] | None:
    normalized = message.strip()
    if not normalized:
        return None

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
        assistant_content_candidates=recent_assistant_messages,
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
            assistant_content_candidates=recent_assistant_messages,
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

    file_organize = cleaned.get("file_organize")
    if file_organize is not None:
        if not isinstance(file_organize, dict):
            raise ValueError("file_organize must be an object")
        source_path = str(file_organize.get("source_path") or "").strip()
        if not source_path:
            raise ValueError("file_organize.source_path is required")
        destination_dir = str(file_organize.get("destination_dir") or "").strip()
        if not destination_dir:
            raise ValueError("file_organize.destination_dir is required")
        fo_mode = str(file_organize.get("mode") or "copy").strip().lower()
        if fo_mode not in {"copy", "move"}:
            raise ValueError("file_organize.mode must be copy or move")
        overwrite = file_organize.get("overwrite", False)
        if not isinstance(overwrite, bool):
            raise ValueError("file_organize.overwrite must be a boolean")
        delete_original = file_organize.get("delete_original", False)
        if not isinstance(delete_original, bool):
            raise ValueError("file_organize.delete_original must be a boolean")
        cleaned["file_organize"] = {
            "source_path": source_path,
            "destination_dir": destination_dir,
            "mode": fo_mode,
            "overwrite": overwrite,
            "delete_original": delete_original,
        }

    steps = cleaned.get("steps")
    if steps is not None:
        if not isinstance(steps, list) or not steps:
            raise ValueError("steps must be a non-empty list")
        validated_steps: list[dict[str, Any]] = []
        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                raise ValueError(f"step {idx} must be an object")
            skill_id = str(step.get("skill_id") or "").strip()
            if not skill_id:
                raise ValueError(f"step {idx} requires a non-empty skill_id")
            args = step.get("args", {})
            if not isinstance(args, dict):
                raise ValueError(f"step {idx} args must be an object")
            validated_steps.append({"skill_id": skill_id, "args": args})
        cleaned["steps"] = validated_steps

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
