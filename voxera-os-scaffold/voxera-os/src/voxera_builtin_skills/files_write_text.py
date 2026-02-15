from __future__ import annotations

from pathlib import Path
from typing import Literal

from voxera.models import RunResult

ALLOWED_ROOT = Path.home() / "VoxeraOS" / "notes"


def _resolve_safe_path(path: str) -> Path:
    raw = Path(path).expanduser()
    resolved = raw.resolve()
    allowed = ALLOWED_ROOT.resolve()
    if resolved == allowed or allowed in resolved.parents:
        return resolved
    raise ValueError(f"Path is outside allowlist: {allowed}")


def run(path: str, text: str, mode: Literal["append", "overwrite"] = "overwrite") -> RunResult:
    try:
        target = _resolve_safe_path(path)
    except Exception as exc:
        return RunResult(ok=False, error=repr(exc))

    if mode not in {"append", "overwrite"}:
        return RunResult(ok=False, error="ValueError('mode must be append or overwrite')")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        write_mode = "a" if mode == "append" else "w"
        with target.open(write_mode, encoding="utf-8") as f:
            f.write(text)
        action = "Appended" if mode == "append" else "Wrote"
        return RunResult(ok=True, output=f"{action} text to {target}")
    except Exception as exc:
        return RunResult(ok=False, error=repr(exc))
