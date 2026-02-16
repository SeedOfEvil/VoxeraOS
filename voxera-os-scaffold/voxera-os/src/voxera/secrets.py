from __future__ import annotations

import os
import stat
from pathlib import Path

import keyring

from .paths import config_dir, ensure_dirs

FALLBACK_SECRETS_FILE = "secrets.env"


def _fallback_path() -> Path:
    ensure_dirs()
    return config_dir() / FALLBACK_SECRETS_FILE


def set_secret(ref: str, value: str) -> str:
    """Store secret in keyring if possible; otherwise fallback to a 0600 file."""
    try:
        keyring.set_password("voxera", ref, value)
        return f"keyring:{ref}"
    except Exception:
        p = _fallback_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    existing[k.strip()] = v.strip()
        existing[ref] = value
        p.write_text("\n".join([f"{k}={v}" for k, v in existing.items()]) + "\n", encoding="utf-8")
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        return f"file:{ref}"


def get_secret(ref: str) -> str | None:
    try:
        v = keyring.get_password("voxera", ref)
        if v:
            return v
    except Exception:
        pass
    p = _fallback_path()
    if not p.exists():
        return None
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith(ref + "="):
            return line.split("=", 1)[1]
    return None
