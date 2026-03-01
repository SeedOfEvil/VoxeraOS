from __future__ import annotations

import contextlib
import shutil
import time
from pathlib import Path
from typing import Any


def _entry_mtime(entry: Path) -> float:
    """Return mtime of entry without following symlinks."""
    return entry.lstat().st_mtime


def _entry_size(entry: Path) -> int:
    """Estimate byte size of entry without following symlinks.

    For symlinks: returns 0 (we do not follow them).
    For files: stat().st_size.
    For directories: recursive walk of real files (symlinks inside are skipped).
    """
    if entry.is_symlink():
        return 0
    if entry.is_file():
        try:
            return entry.stat().st_size
        except OSError:
            return 0
    if entry.is_dir():
        total = 0
        for child in entry.rglob("*"):
            if child.is_symlink():
                continue
            if child.is_file():
                with contextlib.suppress(OSError):
                    total += child.stat().st_size
        return total
    return 0


def _is_safe(entry: Path, root: Path) -> bool:
    """Return True iff entry is safely inside root (no symlink escape)."""
    try:
        resolved = entry.resolve()
    except OSError:
        return False
    return str(resolved).startswith(str(root.resolve()))


def prune_artifacts(
    artifacts_root: Path,
    *,
    max_age_s: float | None = None,
    max_count: int | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Prune top-level entries in artifacts_root according to retention rules.

    Selection policy: union — an entry is pruned if it exceeds *either* the
    age rule (older than max_age_s seconds) or the count rule (outside the
    newest max_count entries).

    Args:
        artifacts_root: Path to the artifacts directory to prune.
        max_age_s: Prune entries with mtime older than this many seconds.
                   None means this rule is not applied.
        max_count: Keep the newest N entries; prune the rest.
                   None means this rule is not applied.
        dry_run: If True (default), no files are deleted.

    Returns:
        Dict with keys:
          status: "ok" | "no_artifacts_dir" | "no_rules"
          total_candidates: int
          pruned_count: int (or would-prune in dry-run)
          reclaimed_bytes: int (best-effort estimate)
          dry_run: bool
          top_entries: list[dict] — up to 10 largest entries by byte size
    """
    if not artifacts_root.exists():
        return {
            "status": "no_artifacts_dir",
            "total_candidates": 0,
            "pruned_count": 0,
            "reclaimed_bytes": 0,
            "dry_run": dry_run,
            "top_entries": [],
        }

    if max_age_s is None and max_count is None:
        return {
            "status": "no_rules",
            "total_candidates": 0,
            "pruned_count": 0,
            "reclaimed_bytes": 0,
            "dry_run": dry_run,
            "top_entries": [],
        }

    # Collect top-level entries only (not recursive)
    entries: list[Path] = []
    try:
        for child in artifacts_root.iterdir():
            entries.append(child)
    except OSError:
        entries = []

    # Sort newest-first by mtime, tie-break by path string for determinism
    entries.sort(key=lambda e: (-_entry_mtime(e), str(e)))

    total_candidates = len(entries)

    # Build prune set using union policy
    prune_set: set[Path] = set()

    if max_age_s is not None and max_age_s > 0:
        cutoff = time.time() - max_age_s
        for entry in entries:
            if _entry_mtime(entry) < cutoff:
                prune_set.add(entry)

    if max_count is not None and max_count >= 0 and len(entries) > max_count:
        # After applying age filter, prune anything outside top max_count
        for entry in entries[max_count:]:
            prune_set.add(entry)

    # Compute top_entries (up to 10 largest) from all candidates
    sized: list[tuple[int, str]] = []
    for entry in entries:
        sized.append((_entry_size(entry), entry.name))
    sized.sort(reverse=True)
    top_entries = [{"name": name, "bytes": sz} for sz, name in sized[:10]]

    # Compute reclaimed_bytes from prune candidates
    reclaimed_bytes = 0
    for entry in prune_set:
        reclaimed_bytes += _entry_size(entry)

    pruned_count = 0
    if not dry_run:
        root_resolved = artifacts_root.resolve()
        for entry in prune_set:
            if not _is_safe(entry, root_resolved):
                # Safety: skip entries that resolve outside the root
                continue
            try:
                if entry.is_symlink():
                    entry.unlink()
                elif entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
                pruned_count += 1
            except OSError:
                pass
    else:
        pruned_count = len(prune_set)

    return {
        "status": "ok",
        "total_candidates": total_candidates,
        "pruned_count": pruned_count,
        "reclaimed_bytes": reclaimed_bytes,
        "dry_run": dry_run,
        "top_entries": top_entries,
    }


def format_bytes(n: int) -> str:
    """Human-readable byte count."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n} {unit}"
        n //= 1024
    return f"{n} TB"


__all__ = ["prune_artifacts", "format_bytes"]
