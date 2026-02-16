from __future__ import annotations

import subprocess

from voxera.models import RunResult


def run() -> RunResult:
    commands = [
        ["wl-paste", "--no-newline"],
        ["xclip", "-selection", "clipboard", "-o"],
    ]
    for cmd in commands:
        try:
            out = subprocess.check_output(cmd, text=True)
            return RunResult(ok=True, output=out)
        except FileNotFoundError:
            continue
        except subprocess.CalledProcessError as exc:
            return RunResult(ok=False, error=f"Clipboard command failed: {exc}")

    return RunResult(ok=False, error="No clipboard tool found (wl-paste/xclip)")
