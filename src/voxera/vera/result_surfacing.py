"""Evidence-grounded result surfacing layer.

Extracts meaningful user-facing values from canonical step_results /
machine_payload evidence and formats them as concise, bounded,
operator-usable completion text.

Design principles:
- Never invent values not present in evidence.
- Deterministic: same evidence always produces same output.
- Bounded: large payloads are excerpted, never dumped in full.
- Fallback: returns None when no useful value can be surfaced,
  letting callers keep current status-oriented messaging.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Boundedness limits
# ---------------------------------------------------------------------------
_MAX_TEXT_EXCERPT_CHARS = 480
_MAX_LOG_LINES_SHOWN = 8
_MAX_LIST_DIR_ENTRIES = 12

# ---------------------------------------------------------------------------
# Result class enum (for callers that want to branch on it)
# ---------------------------------------------------------------------------
RESULT_CLASS_TEXT_CONTENT = "text_content"
RESULT_CLASS_EXISTENCE = "existence"
RESULT_CLASS_STAT_INFO = "stat_info"
RESULT_CLASS_LIST_DIR = "list_dir"
RESULT_CLASS_SERVICE_STATE = "service_state"
RESULT_CLASS_RECENT_LOGS = "recent_logs"
RESULT_CLASS_DIAGNOSTICS_SNAPSHOT = "diagnostics_snapshot"
RESULT_CLASS_PROCESS_LIST = "process_list"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_value_forward_text(
    *,
    structured: dict[str, Any],
    mission_id: str = "",
) -> str | None:
    """Try to produce a concise, evidence-grounded result string.

    Returns None when no useful value can be surfaced beyond status text.
    Callers should fall back to current status-oriented completion messaging.
    """
    # Try each extractor in priority order; first match wins.
    for extractor in _EXTRACTORS:
        result = extractor(structured=structured, mission_id=mission_id)
        if result is not None:
            return result
    return None


def classify_result_family(
    *,
    structured: dict[str, Any],
    mission_id: str = "",
) -> str | None:
    """Return the result class string if a value-forward extraction is possible."""
    for classifier in _CLASSIFIERS:
        result = classifier(structured=structured, mission_id=mission_id)
        if result is not None:
            return result
    return None


# ---------------------------------------------------------------------------
# Step-level machine payload helpers
# ---------------------------------------------------------------------------


def _step_payload_for_skill(structured: dict[str, Any], skill_id: str) -> dict[str, Any]:
    """Extract machine_payload from the *last* step matching *skill_id*.

    Iterates from the end so that workflows invoking the same skill
    multiple times surface terminal evidence rather than stale state.
    """
    step_summaries = structured.get("step_summaries")
    if not isinstance(step_summaries, list):
        return {}
    for item in reversed(step_summaries):
        if not isinstance(item, dict):
            continue
        if str(item.get("skill_id") or "").strip() == skill_id:
            payload = item.get("machine_payload")
            if isinstance(payload, dict):
                return payload
    return {}


def _step_summary_for_skill(structured: dict[str, Any], skill_id: str) -> str:
    """Extract summary string from the *last* step matching *skill_id*."""
    step_summaries = structured.get("step_summaries")
    if not isinstance(step_summaries, list):
        return ""
    for item in reversed(step_summaries):
        if not isinstance(item, dict):
            continue
        if str(item.get("skill_id") or "").strip() == skill_id:
            return str(item.get("summary") or "")
    return ""


def _step_status_for_skill(structured: dict[str, Any], skill_id: str) -> str:
    """Return the status of the *last* step matching *skill_id*, or empty string."""
    step_summaries = structured.get("step_summaries")
    if not isinstance(step_summaries, list):
        return ""
    for item in reversed(step_summaries):
        if not isinstance(item, dict):
            continue
        if str(item.get("skill_id") or "").strip() == skill_id:
            return str(item.get("status") or "").strip()
    return ""


def _last_step_skill_id(structured: dict[str, Any]) -> str:
    """Return the skill_id of the last step, or empty string."""
    step_summaries = structured.get("step_summaries")
    if not isinstance(step_summaries, list) or not step_summaries:
        return ""
    last = step_summaries[-1]
    if not isinstance(last, dict):
        return ""
    return str(last.get("skill_id") or "").strip()


def _last_step_machine_payload(structured: dict[str, Any]) -> dict[str, Any]:
    """Return the machine_payload of the last step."""
    mp = structured.get("machine_payload")
    if isinstance(mp, dict) and mp:
        return mp
    step_summaries = structured.get("step_summaries")
    if not isinstance(step_summaries, list) or not step_summaries:
        return {}
    last = step_summaries[-1]
    if not isinstance(last, dict):
        return {}
    payload = last.get("machine_payload")
    return payload if isinstance(payload, dict) else {}


# ---------------------------------------------------------------------------
# Bounded text helpers
# ---------------------------------------------------------------------------


def _truncate(text: str, max_chars: int = _MAX_TEXT_EXCERPT_CHARS) -> str:
    """Return text truncated to *max_chars* with ellipsis if needed."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


# ---------------------------------------------------------------------------
# Extractors — each returns str | None
# ---------------------------------------------------------------------------


def _extract_file_read(*, structured: dict[str, Any], mission_id: str) -> str | None:
    """files.read_text -> file content or bounded excerpt.

    Answer-first: surface actual file content when available in machine_payload,
    falling back to latest_summary or path+size metadata.
    """
    payload = _step_payload_for_skill(structured, "files.read_text")
    if not payload:
        return None

    # Failed file reads should not produce success-sounding text.
    step_status = _step_status_for_skill(structured, "files.read_text")
    if step_status != "succeeded":
        return None

    path = str(payload.get("path") or "").strip()
    byte_count = payload.get("bytes")
    line_count = payload.get("line_count")
    content = payload.get("content")
    content_truncated = payload.get("content_truncated", False)

    # Strategy 1: content is directly in machine_payload (preferred, most reliable).
    if isinstance(content, str) and content:
        excerpt = _truncate(content)
        meta_parts: list[str] = []
        if isinstance(byte_count, int):
            meta_parts.append(f"{byte_count} bytes")
        if isinstance(line_count, int):
            meta_parts.append(f"{line_count} lines")
        meta = f" ({', '.join(meta_parts)})" if meta_parts else ""
        trunc_note = (
            " [truncated]" if content_truncated or len(content) > _MAX_TEXT_EXCERPT_CHARS else ""
        )
        header = (
            f"Contents of {path}{meta}{trunc_note}:"
            if path
            else f"File contents{meta}{trunc_note}:"
        )
        return f"{header}\n{excerpt}"

    # Strategy 2: latest_summary has richer content than the skill summary.
    summary = _step_summary_for_skill(structured, "files.read_text")
    latest_summary = str(structured.get("latest_summary") or "").strip()
    last_skill = _last_step_skill_id(structured)
    if (
        last_skill == "files.read_text"
        and latest_summary
        and latest_summary != summary
        and len(latest_summary) > len(summary)
    ):
        excerpt = _truncate(latest_summary)
        if path:
            return f"Contents of {path}:\n{excerpt}"
        return f"File contents:\n{excerpt}"

    # Fallback: surface path + size as operator-grade metadata.
    if path and isinstance(byte_count, int):
        return f"File {path} ({byte_count} bytes). Content not available in evidence."
    if path:
        return f"Read file: {path}."
    return None


def _extract_file_exists(*, structured: dict[str, Any], mission_id: str) -> str | None:
    """files.exists -> exists / missing."""
    payload = _step_payload_for_skill(structured, "files.exists")
    if not payload:
        return None
    path = str(payload.get("path") or "").strip()
    exists = payload.get("exists")
    kind = str(payload.get("kind") or "").strip()
    if not path or exists is None:
        return None
    if exists:
        kind_label = f" ({kind})" if kind and kind != "other" else ""
        return f"{path} exists{kind_label}."
    return f"{path} does not exist."


def _extract_file_stat(*, structured: dict[str, Any], mission_id: str) -> str | None:
    """files.stat -> key metadata."""
    payload = _step_payload_for_skill(structured, "files.stat")
    if not payload:
        return None
    path = str(payload.get("path") or "").strip()
    kind = str(payload.get("kind") or "").strip()
    size_bytes = payload.get("size_bytes")
    modified = str(payload.get("modified_ts") or "").strip()
    if not path:
        return None
    parts = [f"{path}:"]
    if kind:
        parts.append(f"type={kind}")
    if isinstance(size_bytes, int):
        parts.append(f"size={size_bytes}B")
    if modified:
        parts.append(f"modified={modified}")
    return " ".join(parts) if len(parts) > 1 else None


def _extract_list_dir(*, structured: dict[str, Any], mission_id: str) -> str | None:
    """files.list_dir -> bounded directory listing."""
    payload = _step_payload_for_skill(structured, "files.list_dir")
    if not payload:
        return None
    path = str(payload.get("path") or "").strip()
    entry_count = payload.get("entry_count")
    entries = payload.get("entries")
    if not path:
        return None
    if isinstance(entries, list) and entries:
        names = [
            str(e.get("name") or "")
            for e in entries[:_MAX_LIST_DIR_ENTRIES]
            if isinstance(e, dict) and e.get("name")
        ]
        count = entry_count if isinstance(entry_count, int) else len(entries)
        listing = ", ".join(names)
        suffix = f" (+{count - len(names)} more)" if count > len(names) else ""
        return f"{path}: {count} entries — {listing}{suffix}"
    if isinstance(entry_count, int):
        return f"{path}: {entry_count} entries."
    return None


def _extract_service_status(*, structured: dict[str, Any], mission_id: str) -> str | None:
    """system.service_status -> actual service state with scope context."""
    payload = _step_payload_for_skill(structured, "system.service_status")
    if not payload:
        return None
    service = str(payload.get("service") or payload.get("Id") or "service").strip()
    active = str(payload.get("ActiveState") or "").strip()
    sub = str(payload.get("SubState") or "").strip()
    scope = str(payload.get("scope") or "").strip()
    if not active:
        return None
    state = f"{active}/{sub}" if sub else active
    scope_label = f" ({scope} service)" if scope else ""

    # If both scopes were checked and differ, surface the other scope too.
    other_scope = str(payload.get("other_scope") or "").strip()
    other_active = str(payload.get("other_ActiveState") or "").strip()
    other_sub = str(payload.get("other_SubState") or "").strip()
    scope_warning = str(payload.get("scope_warning") or "").strip()
    parts: list[str] = []
    parts.append(f"Service {service} is {state}{scope_label}.")
    if other_scope and other_active:
        other_state = f"{other_active}/{other_sub}" if other_sub else other_active
        parts.append(f"In {other_scope} scope: {other_state}.")
    if scope_warning:
        parts.append(f"Warning: {scope_warning}.")
    return " ".join(parts)


def _extract_recent_logs(*, structured: dict[str, Any], mission_id: str) -> str | None:
    """system.recent_service_logs -> bounded log excerpt or clear no-entries statement."""
    payload = _step_payload_for_skill(structured, "system.recent_service_logs")
    if not payload:
        return None
    service = str(payload.get("service") or "service").strip()
    line_count = payload.get("line_count")
    since_minutes = payload.get("since_minutes")
    logs = payload.get("logs")
    truncated = payload.get("truncated")
    scope = str(payload.get("scope") or "").strip()
    scope_warning = str(payload.get("scope_warning") or "").strip()

    if not isinstance(line_count, int):
        return None

    time_ctx = f" in the last {since_minutes}m" if isinstance(since_minutes, int) else ""
    scope_ctx = f" ({scope} scope)" if scope else ""
    warning_suffix = f" Warning: {scope_warning}." if scope_warning else ""
    has_log_lines = isinstance(logs, list) and bool(logs)

    # No entries: clear operator-grade statement.
    if line_count == 0 and not has_log_lines:
        return f"No recent logs for {service}{time_ctx}{scope_ctx}.{warning_suffix}"

    parts: list[str] = []

    # Context line
    ctx = f"Recent logs for {service}{scope_ctx}: {line_count} line{'s' if line_count != 1 else ''}{time_ctx}"
    if truncated:
        ctx += " [truncated]"
    ctx += "."
    if warning_suffix:
        ctx += warning_suffix
    parts.append(ctx)

    # Bounded excerpt of actual log lines
    if not has_log_lines:
        return "\n".join(parts) if parts else None
    assert isinstance(logs, list)
    shown = [str(line).strip() for line in logs[-_MAX_LOG_LINES_SHOWN:] if str(line).strip()]
    if shown:
        excerpt = "\n".join(shown)
        if len(excerpt) > _MAX_TEXT_EXCERPT_CHARS:
            excerpt = _truncate(excerpt)
        parts.append(excerpt)

    return "\n".join(parts) if parts else None


def _extract_diagnostics_snapshot(*, structured: dict[str, Any], mission_id: str) -> str | None:
    """system_diagnostics mission -> compact multi-value snapshot."""
    if mission_id != "system_diagnostics":
        return None

    values: list[str] = []

    host = _step_payload_for_skill(structured, "system.host_info")
    hostname = str(host.get("hostname") or "").strip()
    uptime_seconds = host.get("uptime_seconds")
    if hostname:
        uptime_part = ""
        if isinstance(uptime_seconds, (int, float)):
            uptime_hours = round(float(uptime_seconds) / 3600, 1)
            uptime_part = f", uptime={uptime_hours}h"
        values.append(f"host={hostname}{uptime_part}")

    memory = _step_payload_for_skill(structured, "system.memory_usage")
    used_gib = memory.get("used_gib")
    total_gib = memory.get("total_gib")
    used_percent = memory.get("used_percent")
    if isinstance(used_gib, (int, float)) and isinstance(total_gib, (int, float)):
        mem_line = f"memory={used_gib}/{total_gib}GiB"
        if isinstance(used_percent, (int, float)):
            mem_line += f" ({used_percent}%)"
        values.append(mem_line)

    load = _step_payload_for_skill(structured, "system.load_snapshot")
    load_1m = load.get("load_1m")
    load_5m = load.get("load_5m")
    load_15m = load.get("load_15m")
    if all(isinstance(v, (int, float)) for v in (load_1m, load_5m, load_15m)):
        values.append(f"load(1/5/15m)={load_1m}/{load_5m}/{load_15m}")

    disk = _step_payload_for_skill(structured, "system.disk_usage")
    used_pct = disk.get("used_percent")
    free_gb = disk.get("free_gb")
    if isinstance(used_pct, (int, float)):
        disk_line = f"disk={used_pct}%"
        if isinstance(free_gb, (int, float)):
            disk_line += f", {free_gb}GB free"
        values.append(disk_line)

    if not values:
        return None

    return "Diagnostics snapshot: " + "; ".join(values) + "."


def _extract_process_list(*, structured: dict[str, Any], mission_id: str) -> str | None:
    """system.process_list -> bounded process summary."""
    payload = _step_payload_for_skill(structured, "system.process_list")
    if not payload:
        return None
    processes = payload.get("processes")
    if not isinstance(processes, list):
        count = payload.get("count")
        if isinstance(count, int):
            return f"Listed {count} running processes."
        return None
    # Prefer the payload's count field — the processes list may be truncated
    # to a subset while count reflects the true total.
    payload_count = payload.get("count")
    count = payload_count if isinstance(payload_count, int) else len(processes)
    top = processes[:6]
    names = [
        str(p.get("name") or p.get("command") or "?").strip() for p in top if isinstance(p, dict)
    ]
    if names:
        listing = ", ".join(names)
        suffix = f" (+{count - len(names)} more)" if count > len(names) else ""
        return f"{count} running processes. Top: {listing}{suffix}"
    return f"Listed {count} running processes."


# ---------------------------------------------------------------------------
# Classifier functions (same signature, return result class string or None)
# ---------------------------------------------------------------------------


def _classify_file_read(*, structured: dict[str, Any], mission_id: str) -> str | None:
    if _step_payload_for_skill(structured, "files.read_text"):
        return RESULT_CLASS_TEXT_CONTENT
    return None


def _classify_file_exists(*, structured: dict[str, Any], mission_id: str) -> str | None:
    if _step_payload_for_skill(structured, "files.exists"):
        return RESULT_CLASS_EXISTENCE
    return None


def _classify_file_stat(*, structured: dict[str, Any], mission_id: str) -> str | None:
    if _step_payload_for_skill(structured, "files.stat"):
        return RESULT_CLASS_STAT_INFO
    return None


def _classify_list_dir(*, structured: dict[str, Any], mission_id: str) -> str | None:
    if _step_payload_for_skill(structured, "files.list_dir"):
        return RESULT_CLASS_LIST_DIR
    return None


def _classify_service_status(*, structured: dict[str, Any], mission_id: str) -> str | None:
    if _step_payload_for_skill(structured, "system.service_status"):
        return RESULT_CLASS_SERVICE_STATE
    return None


def _classify_recent_logs(*, structured: dict[str, Any], mission_id: str) -> str | None:
    if _step_payload_for_skill(structured, "system.recent_service_logs"):
        return RESULT_CLASS_RECENT_LOGS
    return None


def _classify_diagnostics_snapshot(*, structured: dict[str, Any], mission_id: str) -> str | None:
    if mission_id == "system_diagnostics":
        return RESULT_CLASS_DIAGNOSTICS_SNAPSHOT
    return None


def _classify_process_list(*, structured: dict[str, Any], mission_id: str) -> str | None:
    if _step_payload_for_skill(structured, "system.process_list"):
        return RESULT_CLASS_PROCESS_LIST
    return None


# ---------------------------------------------------------------------------
# Registry (order matters — first match wins)
# ---------------------------------------------------------------------------

_EXTRACTORS = [
    _extract_diagnostics_snapshot,
    _extract_file_exists,
    _extract_file_stat,
    _extract_file_read,
    _extract_list_dir,
    _extract_service_status,
    _extract_recent_logs,
    _extract_process_list,
]

_CLASSIFIERS = [
    _classify_diagnostics_snapshot,
    _classify_file_exists,
    _classify_file_stat,
    _classify_file_read,
    _classify_list_dir,
    _classify_service_status,
    _classify_recent_logs,
    _classify_process_list,
]
