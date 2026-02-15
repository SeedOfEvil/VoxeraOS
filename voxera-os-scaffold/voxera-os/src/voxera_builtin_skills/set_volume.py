from __future__ import annotations

import subprocess

from voxera.models import RunResult


def run(percent: str) -> RunResult:
    try:
        p = int(float(percent))
        p = min(100, max(0, p))
    except Exception:
        return RunResult(ok=False, error="percent must be numeric")

    try:
        subprocess.check_call(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{p}%"])
        return RunResult(ok=True, output=f"Volume set to {p}%")
    except FileNotFoundError:
        return RunResult(ok=False, error="pactl not found. Install pipewire-pulse or pulseaudio tools.")
    except subprocess.CalledProcessError as e:
        return RunResult(ok=False, error=f"pactl failed: {e}")
