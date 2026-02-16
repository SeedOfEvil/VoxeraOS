from __future__ import annotations

import os
import platform
from pathlib import Path

from voxera.models import RunResult


def run() -> RunResult:
    info = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cwd": str(Path.cwd()),
        "user": os.getenv("USER") or os.getenv("USERNAME") or "unknown",
    }
    return RunResult(ok=True, output=str(info), data=info)
