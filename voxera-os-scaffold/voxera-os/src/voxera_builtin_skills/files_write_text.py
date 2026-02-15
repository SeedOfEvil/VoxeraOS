from __future__ import annotations

from pathlib import Path

from voxera.models import RunResult

ALLOWED_ROOT = Path.home() / "VoxeraOS" / "notes"


def _resolve_safe_path(path: str) -> Path:
    raw = Path(path).expanduser()
    resolved = raw.resolve()
    allowed = ALLOWED_ROOT.resolve()
    if resolved == allowed or allowed in resolved.parents:
        return resolved
    raise ValueError(f"Path is outside allowlist: {allowed}")


def run(path: str, text: str) -> RunResult:
    try:
        target = _resolve_safe_path(path)
    except Exception as exc:
        return RunResult(ok=False, error=repr(exc))

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return RunResult(ok=True, output=f"Wrote text to {target}")
    except Exception as exc:
        return RunResult(ok=False, error=repr(exc))
