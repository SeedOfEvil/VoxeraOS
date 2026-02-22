from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

PACKAGE_NAME = "voxera-os"
FALLBACK_DEV_VERSION = "0.0.0+dev"


def _version_from_pyproject() -> str | None:
    repo_root = Path(__file__).resolve().parents[2]
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.exists():
        return None
    try:
        payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except Exception:
        return None
    version = payload.get("project", {}).get("version")
    return str(version) if version else None


def get_version() -> str:
    try:
        return package_version(PACKAGE_NAME)
    except PackageNotFoundError:
        return _version_from_pyproject() or FALLBACK_DEV_VERSION

