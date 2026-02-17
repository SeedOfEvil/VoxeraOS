from __future__ import annotations

from pathlib import Path
from typing import Literal

from voxera.models import RunResult

ALLOWED_ROOT = Path.home() / "VoxeraOS" / "notes"


def _resolve_safe_path(path: str) -> Path:
    allowed = ALLOWED_ROOT.expanduser().resolve(strict=False)
    requested = Path(path).expanduser()
    target = requested if requested.is_absolute() else (allowed / requested)
    target_resolved = target.resolve(strict=False)

    if not target_resolved.is_relative_to(allowed):
        raise ValueError(f"Path is outside allowlist: {target_resolved}")
    return target_resolved


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
