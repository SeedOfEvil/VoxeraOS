from __future__ import annotations

import subprocess

from voxera.models import RunResult


def run(text: str) -> RunResult:
    payload = text if text is not None else ""
    commands = [
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
    ]
    for cmd in commands:
        try:
            subprocess.run(cmd, input=payload, text=True, check=True)
            return RunResult(ok=True, output="Copied text to clipboard")
        except FileNotFoundError:
            continue
        except subprocess.CalledProcessError as exc:
            return RunResult(ok=False, error=f"Clipboard command failed: {exc}")

    return RunResult(ok=False, error="No clipboard tool found (wl-copy/xclip)")
