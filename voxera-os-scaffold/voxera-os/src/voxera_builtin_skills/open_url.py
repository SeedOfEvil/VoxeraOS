from __future__ import annotations

import subprocess
from urllib.parse import urlparse

from voxera.models import RunResult


def run(url: str) -> RunResult:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return RunResult(ok=False, error="Only http/https URLs are allowed")

    candidates = [
        ["firefox", "--new-tab", url],
        ["xdg-open", url],
    ]
    for cmd in candidates:
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return RunResult(ok=True, output=f"Opened URL: {url}")
        except FileNotFoundError:
            continue
        except Exception as exc:
            return RunResult(ok=False, error=repr(exc))

    return RunResult(ok=False, error="No browser launcher found (firefox/xdg-open)")
