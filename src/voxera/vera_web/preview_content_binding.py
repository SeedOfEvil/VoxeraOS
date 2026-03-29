from __future__ import annotations

import re

from ..core.code_draft_intent import has_code_file_extension
from ..vera.draft_revision import filename_from_preview
from ..vera.saveable_artifacts import looks_like_non_authored_assistant_message


def looks_like_builder_refinement_placeholder(content: str) -> bool:
    lowered = content.strip().lower()
    if not lowered:
        return False
    placeholder_values = {
        "formal rewrite requested for the existing file content.",
        "summary of today's top news headlines.",
        "short summary of today's top news headlines.",
        "top stories:\n- headline 1\n- headline 2\n- headline 3",
    }
    return lowered in placeholder_values


def preview_body_looks_like_control_narration(preview: dict[str, object] | None) -> bool:
    if not isinstance(preview, dict):
        return False
    write_file = preview.get("write_file")
    if not isinstance(write_file, dict):
        return False
    content = str(write_file.get("content") or "").strip()
    if not content:
        return False
    return looks_like_non_authored_assistant_message(content)


def is_targeted_code_preview_refinement(
    message: str, *, active_preview: dict[str, object] | None
) -> bool:
    if not isinstance(active_preview, dict):
        return False
    filename = filename_from_preview(active_preview)
    if not filename:
        return False
    write_file = active_preview.get("write_file")
    path = str(write_file.get("path") or "").strip() if isinstance(write_file, dict) else ""
    if not path or not has_code_file_extension(path) or path.lower().endswith(".md"):
        return False
    return bool(
        re.search(r"\badd\s+content\s+to\b", message, re.IGNORECASE)
        and re.search(rf"\b{re.escape(filename)}\b", message, re.IGNORECASE)
    )
