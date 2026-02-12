from __future__ import annotations

import subprocess
from voxera.models import RunResult

ALLOW = {
    "firefox": ["firefox"],
    "terminal": ["gnome-terminal"],
    "settings": ["gnome-control-center"],
}

def run(name: str) -> RunResult:
    key = name.strip().lower()
    if key not in ALLOW:
        return RunResult(ok=False, error=f"App not allowed in MVP allowlist: {name}")
    try:
        subprocess.Popen(ALLOW[key], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return RunResult(ok=True, output=f"Launched: {name}")
    except Exception as e:
        return RunResult(ok=False, error=repr(e))
