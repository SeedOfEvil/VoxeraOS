from __future__ import annotations

import re
import subprocess

from voxera.models import RunResult
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result

ALLOW = {
    "firefox": ["firefox"],
    "gnome-terminal": ["gnome-terminal"],
    "gnome-control-center": ["gnome-control-center"],
}

_SAFE_APP_RE = re.compile(r"^[a-z0-9._-]+$")


def run(name: str) -> RunResult:
    key = name.strip().lower()
    if not key or not _SAFE_APP_RE.match(key):
        return RunResult(
            ok=False,
            error="App name must be a simple allowlisted identifier",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Rejected unsafe app input",
                    machine_payload={"name": name},
                    operator_note="Use a simple app identifier from the allowlist.",
                    next_action_hint="provide_allowed_app",
                    retryable=False,
                    error_class="invalid_input",
                )
            },
        )
    if key not in ALLOW:
        return RunResult(
            ok=False,
            error=f"App not allowed in MVP allowlist: {name}",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Rejected non-allowlisted app",
                    machine_payload={"name": name, "allowlist": sorted(ALLOW.keys())},
                    operator_note="Only explicit allowlisted apps can be launched.",
                    next_action_hint="provide_allowed_app",
                    retryable=False,
                    error_class="invalid_input",
                )
            },
        )
    try:
        subprocess.Popen(ALLOW[key], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return RunResult(
            ok=True,
            output=f"Launched: {key}",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"Launched app {key}",
                    machine_payload={"name": key, "argv": ALLOW[key]},
                    operator_note="App launch requested using allowlisted argv.",
                    next_action_hint="continue",
                )
            },
        )
    except Exception as e:
        return RunResult(
            ok=False,
            error=repr(e),
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Failed to launch app",
                    machine_payload={"name": key, "exception": repr(e)},
                    operator_note="App launcher failed unexpectedly.",
                    next_action_hint="inspect_launcher",
                    retryable=True,
                    error_class="launcher_error",
                )
            },
        )
