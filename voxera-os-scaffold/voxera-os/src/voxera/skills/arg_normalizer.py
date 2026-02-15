from __future__ import annotations

from typing import Any, Dict

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


def _canonical_open_app(args: Dict[str, Any]) -> Dict[str, Any]:
    name = str(args.get("name", "")).strip().lower()
    if not name:
        return args
    return {**args, "name": _OPEN_APP_ALIAS.get(name, name)}


def _canonical_set_volume(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        raw = args.get("percent")
        value = int(float(raw))
    except (TypeError, ValueError):
        return args

    clamped = min(100, max(0, value))
    return {**args, "percent": str(clamped)}


def canonicalize_args(skill_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if skill_id == "system.open_app":
        return _canonical_open_app(args)
    if skill_id == "system.set_volume":
        return _canonical_set_volume(args)
    return args
