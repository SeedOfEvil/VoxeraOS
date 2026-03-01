from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Terminal buckets are the only buckets eligible for pruning.
# inbox/ and pending/ (including pending/approvals/) are never touched.
TERMINAL_BUCKETS: tuple[str, ...] = ("done", "failed", "canceled")

# Only files matching this glob are considered primary job files.
_JOB_GLOB = "job-*.json"

# Conservative sidecar suffixes that clearly belong to a job file.
_SIDECAR_SUFFIXES = (".error.json", ".state.json")


@dataclass
class JobEntry:
    """Represents a primary job file plus any associated sidecars."""

    path: Path
    bucket: str
    mtime: float
    sidecars: list[Path] = field(default_factory=list)

    @property
    def size(self) -> int:
        """Best-effort total size of job + sidecars (no symlink follow)."""
        total = _file_size(self.path)
        for s in self.sidecars:
            total += _file_size(s)
        return total


def _file_size(p: Path) -> int:
    """Return file size without following symlinks. Returns 0 on any error."""
    if p.is_symlink():
        return 0
    if p.is_file():
        try:
            return p.stat().st_size
        except OSError:
            return 0
    return 0


def _entry_mtime(p: Path) -> float:
    """Return mtime without following symlinks."""
    try:
        return p.lstat().st_mtime
    except OSError:
        return 0.0


def _is_safe(entry: Path, root: Path) -> bool:
    """Return True iff entry physically resides inside root (no symlink escape).

    For symlinks: checks the link's parent directory, not the target.
    For non-symlinks: resolves fully to prevent bind-mount/hardlink escapes.
    ``root`` must already be fully resolved.
    """
    try:
        if entry.is_symlink():
            return entry.parent.resolve() == root
        resolved = entry.resolve()
    except OSError:
        return False
    return str(resolved).startswith(str(root) + "/")


def _find_sidecars(job: Path) -> list[Path]:
    """Return conservative sidecars for *job* that exist on disk.

    Given ``job-XYZ.json``, looks for ``job-XYZ.error.json`` and
    ``job-XYZ.state.json`` in the same directory.  Only returns files that
    actually exist (or are dangling symlinks — handled by the caller).
    """
    stem = job.stem  # e.g. "job-XYZ"
    bucket_dir = job.parent
    result: list[Path] = []
    for suffix in _SIDECAR_SUFFIXES:
        candidate = bucket_dir / (stem + suffix)
        if candidate.exists() or candidate.is_symlink():
            result.append(candidate)
    return result


def list_jobs_in_bucket(queue_dir: Path, bucket: str) -> list[JobEntry]:
    """Return job entries in *bucket* under *queue_dir*, sorted newest-first.

    Missing bucket directories are handled gracefully — returns ``[]``.
    Only files matching ``job-*.json`` are returned as primary entries.
    Each entry includes its discovered sidecars.

    Sorting is deterministic: primary key is ``-mtime``, tie-break is the
    string representation of the path.
    """
    bucket_dir = queue_dir / bucket
    if not bucket_dir.exists():
        return []

    entries: list[JobEntry] = []
    try:
        for job in bucket_dir.glob(_JOB_GLOB):
            mtime = _entry_mtime(job)
            sidecars = _find_sidecars(job)
            entries.append(
                JobEntry(
                    path=job,
                    bucket=bucket,
                    mtime=mtime,
                    sidecars=sidecars,
                )
            )
    except OSError:
        return []

    # Newest-first by mtime; tie-break by path string for determinism.
    entries.sort(key=lambda e: (-e.mtime, str(e.path)))
    return entries


def select_union_prune(
    entries: list[JobEntry],
    *,
    max_age_days: int | None = None,
    max_count: int | None = None,
) -> list[JobEntry]:
    """Select entries to prune from *entries* using union policy.

    An entry is selected if it satisfies *either*:
    - Age rule: ``mtime`` is older than ``max_age_days`` days ago.
    - Count rule: it falls outside the newest ``max_count`` entries
      (entries are assumed to be already sorted newest-first).

    ``entries`` must be sorted newest-first (as returned by
    :func:`list_jobs_in_bucket`).

    Returns a list of selected :class:`JobEntry` objects in the same order
    they appear in *entries* (preserves newest-first).  Returns ``[]`` when
    both rules are ``None``.
    """
    if max_age_days is None and max_count is None:
        return []

    prune_paths: set[Path] = set()

    if max_age_days is not None and max_age_days > 0:
        cutoff = time.time() - float(max_age_days) * 86400.0
        for entry in entries:
            if entry.mtime < cutoff:
                prune_paths.add(entry.path)

    if max_count is not None and max_count >= 0 and len(entries) > max_count:
        for entry in entries[max_count:]:
            prune_paths.add(entry.path)

    return [e for e in entries if e.path in prune_paths]


def safe_delete_entry(path: Path, root: Path) -> bool:
    """Delete *path* only if it safely resides within *root*.

    - Never follows symlinks.
    - Never deletes paths that resolve outside *root*.
    - *root* must be fully resolved before calling.

    Returns ``True`` if the file was deleted, ``False`` if skipped or an
    error occurred.
    """
    if not _is_safe(path, root):
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


def prune_queue_buckets(
    queue_dir: Path,
    *,
    buckets: tuple[str, ...] = TERMINAL_BUCKETS,
    max_age_days: int | None = None,
    max_count: int | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Prune terminal bucket job files according to retention rules.

    Selection policy: union — a job is pruned if it exceeds *either* the
    age rule (older than ``max_age_days``) or the count rule (outside the
    newest ``max_count`` entries **per bucket**).

    Args:
        queue_dir:    Path to the queue root directory.
        buckets:      Which terminal buckets to scan (default: all three).
        max_age_days: Prune jobs older than this many days.  ``None`` = rule off.
        max_count:    Keep newest N jobs per bucket; prune the rest.
                      ``None`` = rule off.
        dry_run:      If ``True`` (default), report what *would* be pruned
                      without deleting anything.

    Returns:
        A dict with keys:

        - ``status``:         ``"ok"`` | ``"no_rules"``
        - ``queue_dir``:      str path
        - ``dry_run``:        bool
        - ``per_bucket``:     dict bucket → ``{candidates, selected, pruned}``
        - ``reclaimed_bytes``: int (best-effort estimate)
        - ``errors``:         list[str]
    """
    if max_age_days is None and max_count is None:
        return {
            "status": "no_rules",
            "queue_dir": str(queue_dir),
            "dry_run": dry_run,
            "per_bucket": {},
            "reclaimed_bytes": 0,
            "errors": [],
        }

    per_bucket: dict[str, dict[str, int]] = {}
    reclaimed_bytes = 0
    errors: list[str] = []

    for bucket in buckets:
        entries = list_jobs_in_bucket(queue_dir, bucket)
        selected = select_union_prune(entries, max_age_days=max_age_days, max_count=max_count)

        bucket_root = (queue_dir / bucket).resolve()
        pruned_count = 0

        if not dry_run:
            for entry in selected:
                reclaimed_bytes += entry.size
                # Delete primary job file
                deleted = safe_delete_entry(entry.path, bucket_root)
                if deleted:
                    pruned_count += 1
                else:
                    errors.append(f"skipped (unsafe path): {entry.path}")
                # Delete sidecars
                for sidecar in entry.sidecars:
                    safe_delete_entry(sidecar, bucket_root)
        else:
            for entry in selected:
                reclaimed_bytes += entry.size
            pruned_count = len(selected)

        per_bucket[bucket] = {
            "candidates": len(entries),
            "selected": len(selected),
            "pruned": pruned_count,
        }

    return {
        "status": "ok",
        "queue_dir": str(queue_dir),
        "dry_run": dry_run,
        "per_bucket": per_bucket,
        "reclaimed_bytes": reclaimed_bytes,
        "errors": errors,
    }


__all__ = [
    "TERMINAL_BUCKETS",
    "JobEntry",
    "list_jobs_in_bucket",
    "select_union_prune",
    "safe_delete_entry",
    "prune_queue_buckets",
]
