from __future__ import annotations

import subprocess

from voxera.models import RunResult


def run() -> RunResult:
    try:
        output = subprocess.check_output(["wmctrl", "-l"], text=True)
        windows = [line.strip() for line in output.splitlines() if line.strip()]
        return RunResult(ok=True, data={"windows": windows}, output=f"Found {len(windows)} windows")
    except FileNotFoundError:
        return RunResult(ok=False, error="wmctrl not found")
    except subprocess.CalledProcessError as exc:
        return RunResult(ok=False, error=f"wmctrl failed: {exc}")
