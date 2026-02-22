from __future__ import annotations

from typing import Any

_OPEN_APP_ALIAS = {
    "firefox": "firefox",
    "mozilla firefox": "firefox",
    "terminal": "gnome-terminal",
    "gnome terminal": "gnome-terminal",
    "gnome-terminal": "gnome-terminal",
    "settings": "gnome-control-center",
    "system settings": "gnome-control-center",
    "gnome-control-center": "gnome-control-center",
}

_WRITE_TEXT_ALIASES = {
    "content": "text",
    "body": "text",
}


def _canonical_open_app(args: dict[str, Any]) -> dict[str, Any]:
    name = str(args.get("name", "")).strip().lower()
    if not name:
        return args
    return {**args, "name": _OPEN_APP_ALIAS.get(name, name)}


def _canonical_set_volume(args: dict[str, Any]) -> dict[str, Any]:
    raw = args.get("percent")
    if raw is None:
        return args

    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return args

    clamped = min(100, max(0, value))
    return {**args, "percent": str(clamped)}


def _canonical_write_text(args: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(args)
    if "text" not in normalized:
        for alias, target in _WRITE_TEXT_ALIASES.items():
            if alias in normalized:
                normalized[target] = normalized[alias]
                break
    return normalized


def canonicalize_args(skill_id: str, args: dict[str, Any]) -> dict[str, Any]:
    if skill_id == "system.open_app":
        return _canonical_open_app(args)
    if skill_id == "system.set_volume":
        return _canonical_set_volume(args)
    if skill_id == "files.write_text":
        return _canonical_write_text(args)
    return args
