from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PathBoundaryError(ValueError):
    message: str
    error_class: str

    def __str__(self) -> str:
        return self.message


def normalize_confined_path(*, path: str, allowed_root: Path, must_exist: bool) -> Path:
    if not isinstance(path, str) or not path.strip():
        raise PathBoundaryError("path must be a non-empty string", "invalid_path")
    if "\x00" in path:
        raise PathBoundaryError("path contains a null byte", "invalid_path")

    root = allowed_root.expanduser().resolve(strict=False)
    candidate = Path(path).expanduser()
    target = candidate if candidate.is_absolute() else root / candidate

    resolved = target.resolve(strict=False)
    if must_exist:
        if not resolved.is_relative_to(root):
            raise PathBoundaryError(f"Path is outside allowlist: {resolved}", "path_out_of_bounds")
        if not resolved.exists():
            raise FileNotFoundError(path)
    else:
        parent = target.parent
        try:
            resolved_parent = parent.resolve(strict=True)
        except FileNotFoundError:
            resolved_parent = parent.resolve(strict=False)
        if not resolved_parent.is_relative_to(root):
            raise PathBoundaryError(
                f"Path parent escapes allowlist: {resolved_parent}", "path_out_of_bounds"
            )
        resolved = target.resolve(strict=False)

    if not resolved.is_relative_to(root):
        raise PathBoundaryError(f"Path is outside allowlist: {resolved}", "path_out_of_bounds")
    return resolved
