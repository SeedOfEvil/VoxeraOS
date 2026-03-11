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


def _read_fallback_entries(path: Path) -> dict[str, str]:
    existing: dict[str, str] = {}
    if not path.exists():
        return existing
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            existing[k.strip()] = v.strip()
    return existing


def write_secret(ref: str, value: str) -> str:
    """Store secret in keyring if possible; otherwise fallback to a 0600 file."""
    try:
        keyring.set_password("voxera", ref, value)
        return f"keyring:{ref}"
    except Exception:
        p = _fallback_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        existing = _read_fallback_entries(p)
        existing[ref] = value
        p.write_text("\n".join([f"{k}={v}" for k, v in existing.items()]) + "\n", encoding="utf-8")
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        return f"file:{ref}"


def set_secret(ref: str, value: str) -> str:
    """Backward-compatible alias for write_secret."""
    return write_secret(ref, value)


def get_secret(ref: str) -> str | None:
    try:
        v = keyring.get_password("voxera", ref)
        if v:
            return v
    except Exception:
        pass
    p = _fallback_path()
    entries = _read_fallback_entries(p)
    return entries.get(ref)


def unset_secret(ref: str) -> bool:
    """Remove a stored secret from both keyring and fallback file when present."""
    removed = False

    try:
        if keyring.get_password("voxera", ref) is not None:
            keyring.delete_password("voxera", ref)
            removed = True
    except Exception:
        pass

    p = _fallback_path()
    entries = _read_fallback_entries(p)
    if ref in entries:
        del entries[ref]
        if entries:
            p.write_text(
                "\n".join([f"{k}={v}" for k, v in entries.items()]) + "\n",
                encoding="utf-8",
            )
            os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
        else:
            p.unlink(missing_ok=True)
        removed = True

    return removed
