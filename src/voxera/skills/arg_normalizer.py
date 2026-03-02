from __future__ import annotations

import shlex
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


_SANDBOX_ARGV_EMPTY_MSG = (
    "sandbox.exec command must be a non-empty list of strings. "
    "Provide args.command as a list like ['bash','-lc','echo hello'] or a non-empty string."
)


def canonicalize_argv(args: dict[str, Any]) -> list[str]:
    """Resolve and normalise sandbox command argv from an args dict.

    Looks for keys in priority order: ``command``, ``argv``, ``cmd``.
    Accepts a string (tokenised via :func:`shlex.split`) or a ``list[str]``
    (whitespace-stripped; empty tokens silently dropped).

    :raises ValueError: when no recognised key is present, the value type is
        unsupported, a list element is not a string, or the resulting argv
        would be empty.
    """
    found_key = False
    raw: Any = None
    for key in ("command", "argv", "cmd"):
        if key in args:
            raw = args[key]
            found_key = True
            break

    if not found_key:
        raise ValueError(_SANDBOX_ARGV_EMPTY_MSG)

    if isinstance(raw, str):
        argv = shlex.split(raw.strip())
    elif isinstance(raw, list):
        argv = []
        for token in raw:
            if not isinstance(token, str):
                raise ValueError(_SANDBOX_ARGV_EMPTY_MSG)
            stripped = token.strip()
            if stripped:
                argv.append(stripped)
    else:
        raise ValueError(_SANDBOX_ARGV_EMPTY_MSG)

    if not argv:
        raise ValueError(_SANDBOX_ARGV_EMPTY_MSG)

    return argv


def _canonical_sandbox_exec(args: dict[str, Any]) -> dict[str, Any]:
    try:
        argv = canonicalize_argv(args)
    except ValueError:
        return args  # Runner will surface a RunResult error.
    return {**args, "command": argv}


def canonicalize_args(skill_id: str, args: dict[str, Any]) -> dict[str, Any]:
    if skill_id == "system.open_app":
        return _canonical_open_app(args)
    if skill_id == "system.set_volume":
        return _canonical_set_volume(args)
    if skill_id == "files.write_text":
        return _canonical_write_text(args)
    if skill_id == "sandbox.exec":
        return _canonical_sandbox_exec(args)
    return args
