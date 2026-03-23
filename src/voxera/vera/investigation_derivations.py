from __future__ import annotations

import re
import time
from typing import Any


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


def is_investigation_expand_request(message: str) -> bool:
    lowered = message.strip().lower()
    if not lowered:
        return False
    has_expand_signal = bool(
        re.search(
            r"\b(expand|elaborate|go\s+deeper|deep\s+dive|tell\s+me\s+more|more\s+detail)\b",
            lowered,
        )
    )
    return has_expand_signal and bool(re.search(r"\bresult\s*\d+\b", lowered))


def is_investigation_derived_save_request(message: str) -> bool:
    lowered = message.strip().lower()
    if not lowered:
        return False
    save_action = bool(re.search(r"\b(save|export)\b", lowered))
    derived_target = bool(
        re.search(
            r"\b(comparison|summary|expanded?\s+result|expanded?\s+finding|expansion|investigation\s+writeup)\b",
            lowered,
        )
    )
    return save_action and derived_target


def is_investigation_derived_followup_save_request(message: str) -> bool:
    lowered = message.strip().lower()
    if not lowered:
        return False
    save_action = bool(re.search(r"\b(save|export)\b", lowered))
    file_target_action = bool(re.search(r"\b(write|put|create|make)\b", lowered))
    file_target = bool(
        re.search(r"\b(note|file|markdown|disk|\.md\b|\.txt\b|save-as|save\s+as)\b", lowered)
    )
    pronoun_target = bool(re.search(r"\b(that|this|it)\b", lowered))
    return pronoun_target and (save_action or (file_target_action and file_target))


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
    if not (
        is_investigation_derived_save_request(message)
        or is_investigation_derived_followup_save_request(message)
    ):
        return None
    if not isinstance(derived_output, dict):
        return None
    markdown = str(derived_output.get("markdown") or "").strip()
    derivation_type = str(derived_output.get("derivation_type") or "").strip().lower()
    if not markdown or derivation_type not in {"comparison", "summary", "expanded_result"}:
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

    label = {
        "comparison": "comparison",
        "summary": "summary",
        "expanded_result": "expanded result",
    }[derivation_type]
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


def derive_investigation_expansion(
    message: str,
    *,
    investigation_context: dict[str, Any] | None,
    expanded_text: str,
) -> dict[str, Any] | None:
    if not is_investigation_expand_request(message):
        return None
    selected, selected_ids = select_investigation_results(
        message, investigation_context=investigation_context
    )
    if (
        selected is None
        or selected_ids is None
        or len(selected_ids) != 1
        or not isinstance(investigation_context, dict)
    ):
        return None

    answer = expanded_text.strip()
    if not answer:
        return None

    result = selected[0]
    result_id = selected_ids[0]
    query = str(investigation_context.get("query") or "(unspecified query)").strip()
    title = str(result.get("title") or "Untitled").strip()
    source = str(result.get("source") or "unknown").strip() or "unknown"
    url = str(result.get("url") or "").strip()
    snippet = str(result.get("snippet") or "").strip()
    why_it_matched = str(result.get("why_it_matched") or "").strip()

    markdown_lines = [
        f"# Expanded Investigation Result {result_id}",
        "",
        "## Query",
        query,
        "",
        "## Result Metadata",
        f"- Title: {title}",
        f"- Source: {source}",
    ]
    if url:
        markdown_lines.append(f"- URL: {url}")
    if snippet:
        markdown_lines.append(f"- Original snippet: {snippet}")
    if why_it_matched:
        markdown_lines.append(f"- Why it matched: {why_it_matched}")
    markdown_lines.extend(["", "## Expanded Writeup", answer, ""])

    return {
        "derivation_type": "expanded_result",
        "query": query,
        "selected_result_ids": selected_ids,
        "result_id": result_id,
        "result_title": title,
        "answer": answer,
        "markdown": "\n".join(markdown_lines).rstrip() + "\n",
    }
