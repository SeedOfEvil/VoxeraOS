from __future__ import annotations

import json
import re
from typing import Any


def recover_json_object(raw_text: str) -> tuple[dict[str, Any] | None, str | None]:
    """Recover a JSON object from model output.

    Returns `(parsed_object, recovery_note)` where recovery_note is one of:
    - None (no recovery needed)
    - "stripped_markdown_fence"
    - "extracted_json_object"
    """

    stripped = (raw_text or "").strip()
    if not stripped:
        return None, None

    parsed = _parse_json_object(stripped)
    if parsed is not None:
        return parsed, None

    fenced = _extract_markdown_fence_json(stripped)
    if fenced is not None:
        parsed = _parse_json_object(fenced)
        if parsed is not None:
            return parsed, "stripped_markdown_fence"
        extracted = _extract_first_balanced_object(fenced)
        if extracted is not None:
            parsed = _parse_json_object(extracted)
            if parsed is not None:
                return parsed, "extracted_json_object"

    extracted = _extract_first_balanced_object(stripped)
    if extracted is not None:
        parsed = _parse_json_object(extracted)
        if parsed is not None:
            return parsed, "extracted_json_object"

    return None, None


def _parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _extract_markdown_fence_json(text: str) -> str | None:
    # Prefer JSON-tagged fences, then generic fences.
    tagged = re.search(r"```json\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if tagged:
        return tagged.group(1).strip()
    generic = re.search(r"```\s*(.*?)\s*```", text, flags=re.DOTALL)
    if generic:
        return generic.group(1).strip()
    return None


def _extract_first_balanced_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]

    return None
