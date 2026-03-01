from __future__ import annotations

import shutil
import subprocess

from voxera.models import RunResult


def run(keep_open: bool = True) -> RunResult:
    if not shutil.which("gnome-terminal"):
        return RunResult(ok=False, error="gnome-terminal not found")

    lines = [
        "clear",
        "echo '--- Voxera Terminal Demo ---'",
        'echo \'Command: echo "Hello, world!"\'',
        'echo "Hello, world!"',
    ]
    if keep_open:
        lines.append("read -rp 'Press Enter to close...'")

    script = "; ".join(lines)

    subprocess.Popen(
        ["gnome-terminal", "--", "bash", "-lc", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return RunResult(ok=True, output="Opened terminal and ran hello-world demo")
