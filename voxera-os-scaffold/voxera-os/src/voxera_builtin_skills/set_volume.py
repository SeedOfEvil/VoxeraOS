from __future__ import annotations

import subprocess
from voxera.models import RunResult

def run(percent: str) -> RunResult:
    try:
        p = int(percent)
        if p < 0 or p > 150:
            return RunResult(ok=False, error="percent must be between 0 and 150")
    except Exception:
        return RunResult(ok=False, error="percent must be an integer")

    try:
        subprocess.check_call(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{p}%"])
        return RunResult(ok=True, output=f"Volume set to {p}%")
    except FileNotFoundError:
        return RunResult(ok=False, error="pactl not found. Install pipewire-pulse or pulseaudio tools.")
    except subprocess.CalledProcessError as e:
        return RunResult(ok=False, error=f"pactl failed: {e}")
