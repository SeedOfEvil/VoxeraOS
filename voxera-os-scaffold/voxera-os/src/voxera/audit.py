from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .paths import data_dir, ensure_dirs


def audit_path() -> Path:
    ensure_dirs()
    ts = time.strftime("%Y%m%d")
    return data_dir() / "audit" / f"audit-{ts}.jsonl"


def log(event: dict[str, Any]) -> None:
    p = audit_path()
    event = dict(event)
    event.setdefault("ts", time.time())
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def tail(n: int = 50) -> list[dict]:
    p = audit_path()
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines[-n:]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
