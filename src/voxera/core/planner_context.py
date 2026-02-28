from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from ..audit import log

_DEFAULT_AGENT_NAME = "Vera"
_ENV_AGENT_NAME = "VOXERA_PLANNER_AGENT_NAME"
_ENV_PREAMBLE = "VOXERA_PLANNER_PREAMBLE"
_ENV_PREAMBLE_PATH = "VOXERA_PLANNER_PREAMBLE_PATH"


def get_planner_agent_name(*, env: Mapping[str, str] | None = None) -> str:
    values = env if env is not None else os.environ
    configured = str(values.get(_ENV_AGENT_NAME, "")).strip()
    return configured or _DEFAULT_AGENT_NAME


def _build_default_preamble(agent_name: str) -> str:
    return (
        f"You are {agent_name}, Voxera's Linux OS wrangler mission planner.\n"
        "Plan with the smallest reliable sequence (1-5 steps), then stop.\n"
        "Tool-selection heuristics:\n"
        "- Goal is a URL/domain: prefer system.open_url.\n"
        "- Goal says open an app: use system.open_app and pick only from CAPABILITIES.allowed_apps.\n"
        "- If a requested action is outside CAPABILITIES, suggest the closest supported alternative and ask one clarifying question when needed.\n"
        "- Never invent mission IDs, enum values, or capabilities; runtime snapshot is authoritative."
    )


def get_planner_preamble(*, env: Mapping[str, str] | None = None) -> str:
    values = env if env is not None else os.environ

    configured = str(values.get(_ENV_PREAMBLE, "")).strip()
    if configured:
        return configured

    path_value = str(values.get(_ENV_PREAMBLE_PATH, "")).strip()
    if path_value:
        try:
            file_text = Path(path_value).expanduser().read_text(encoding="utf-8").strip()
        except OSError as exc:
            log(
                {
                    "event": "planner_preamble_load_failed",
                    "path": path_value,
                    "error": repr(exc),
                }
            )
        else:
            if file_text:
                return file_text

    return _build_default_preamble(get_planner_agent_name(env=values))
