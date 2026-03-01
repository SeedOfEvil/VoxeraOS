from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

# All searchable buckets for primary-job enumeration.
_ALL_BUCKETS: tuple[str, ...] = ("inbox", "pending", "done", "failed", "canceled")

# Terminal buckets where sidecars may be orphaned.
_TERMINAL_BUCKETS: tuple[str, ...] = ("done", "failed", "canceled")

# Only files matching this glob are primary job files.
_JOB_GLOB = "job-*.json"

# Conservative sidecar suffixes (must have a primary in the same bucket).
_SIDECAR_SUFFIXES: tuple[str, ...] = (".error.json", ".state.json")

# Approval files live in pending/approvals/ and follow <stem>.approval.json.
_APPROVAL_SUFFIX = ".approval.json"

# Maximum example paths included in the report per issue type.
_MAX_EXAMPLES = 10


def _collect_primary_jobs(queue_dir: Path) -> dict[str, list[str]]:
    """Return mapping of job filename → sorted list of buckets where it appears.

    Only primary job files (``job-*.json``, excluding sidecar suffixes) are
    counted.  Missing bucket directories are silently skipped.
    """
    result: dict[str, list[str]] = {}
    for bucket in _ALL_BUCKETS:
        bucket_dir = queue_dir / bucket
        if not bucket_dir.is_dir():
            continue
        try:
            for p in bucket_dir.glob(_JOB_GLOB):
                if any(p.name.endswith(s) for s in _SIDECAR_SUFFIXES):
                    continue
                result.setdefault(p.name, [])
                result[p.name].append(bucket)
        except OSError:
            continue
    return result


def _detect_orphan_sidecars(queue_dir: Path) -> list[str]:
    """Find sidecar files in terminal buckets that have no primary job partner.

    For ``job-XYZ.error.json`` expects ``job-XYZ.json`` in the **same** bucket.
    """
    orphans: list[str] = []
    for bucket in _TERMINAL_BUCKETS:
        bucket_dir = queue_dir / bucket
        if not bucket_dir.is_dir():
            continue
        try:
            for p in sorted(bucket_dir.iterdir(), key=lambda x: x.name):
                if not (p.is_file() or p.is_symlink()):
                    continue
                for suffix in _SIDECAR_SUFFIXES:
                    if p.name.endswith(suffix):
                        stem = p.name[: -len(suffix)]  # e.g. "job-XYZ"
                        primary = bucket_dir / (stem + ".json")
                        if not primary.exists():
                            orphans.append(str(p))
                        break
        except OSError:
            continue
    return sorted(orphans)


def _detect_orphan_approvals(queue_dir: Path) -> list[str]:
    """Find approval files in pending/approvals/ with no corresponding pending job.

    Approval naming: ``<stem>.approval.json`` → expects ``pending/<stem>.json``.
    """
    approvals_dir = queue_dir / "pending" / "approvals"
    pending_dir = queue_dir / "pending"
    if not approvals_dir.is_dir():
        return []

    orphans: list[str] = []
    try:
        for p in sorted(approvals_dir.iterdir(), key=lambda x: x.name):
            if not p.name.endswith(_APPROVAL_SUFFIX):
                continue
            if not (p.is_file() or p.is_symlink()):
                continue
            stem = p.name[: -len(_APPROVAL_SUFFIX)]  # e.g. "job-abc123"
            pending_job = pending_dir / (stem + ".json")
            if not pending_job.exists():
                orphans.append(str(p))
    except OSError:
        pass
    return sorted(orphans)


def _detect_orphan_artifact_candidates(queue_dir: Path, all_job_names: set[str]) -> list[str]:
    """Find direct children of artifacts/ with no matching job across any bucket.

    Conservative: reports as ``orphan_candidate`` — does not claim certainty.
    Only direct children of ``<queue_dir>/artifacts/`` are checked (not recursive).
    """
    artifacts_dir = queue_dir / "artifacts"
    if not artifacts_dir.is_dir():
        return []

    candidates: list[str] = []
    try:
        for p in sorted(artifacts_dir.iterdir(), key=lambda x: x.name):
            # Artifact entry name is the job stem; primary filename = stem + ".json".
            job_filename = p.name + ".json"
            if job_filename not in all_job_names:
                candidates.append(str(p))
    except OSError:
        pass
    return sorted(candidates)


def _detect_duplicate_jobs(
    all_primary: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """Find job filenames that appear in more than one bucket.

    Returns a deterministically sorted list of dicts with ``job_name`` and
    ``buckets`` (sorted list of bucket names).
    """
    duplicates: list[dict[str, Any]] = []
    for job_name in sorted(all_primary.keys()):
        buckets = sorted(all_primary[job_name])
        if len(buckets) > 1:
            duplicates.append({"job_name": job_name, "buckets": buckets})
    return duplicates


def reconcile_queue(queue_dir: Path) -> dict[str, Any]:
    """Run queue hygiene diagnostics.  **Read-only — no mutations.**

    Scans the queue directory for four categories of hygiene issues:

    1. Orphan sidecars in terminal buckets (done/failed/canceled).
    2. Orphan approval files in pending/approvals/.
    3. Orphan artifact candidates under artifacts/.
    4. Duplicate job filenames across buckets.

    Args:
        queue_dir: Path to the queue root directory.

    Returns:
        A dict with a stable schema::

            {
                "status": "ok",
                "queue_dir": str,
                "issue_counts": {
                    "orphan_sidecars": int,
                    "orphan_approvals": int,
                    "orphan_artifacts_candidate": int,
                    "duplicate_jobs": int,
                },
                "examples": {
                    "orphan_sidecars": [str, ...],          # up to 10 paths
                    "orphan_approvals": [str, ...],         # up to 10 paths
                    "orphan_artifacts_candidate": [str, ...],  # up to 10 paths
                    "duplicate_jobs": [{"job_name": str, "buckets": [str]}],
                },
            }

        All arrays are deterministically sorted.  Missing directories are
        treated as 0 issues for that category.
    """
    all_primary = _collect_primary_jobs(queue_dir)
    all_job_names: set[str] = set(all_primary.keys())

    orphan_sidecars = _detect_orphan_sidecars(queue_dir)
    orphan_approvals = _detect_orphan_approvals(queue_dir)
    orphan_artifacts = _detect_orphan_artifact_candidates(queue_dir, all_job_names)
    duplicate_jobs = _detect_duplicate_jobs(all_primary)

    return {
        "status": "ok",
        "queue_dir": str(queue_dir),
        "issue_counts": {
            "orphan_sidecars": len(orphan_sidecars),
            "orphan_approvals": len(orphan_approvals),
            "orphan_artifacts_candidate": len(orphan_artifacts),
            "duplicate_jobs": len(duplicate_jobs),
        },
        "examples": {
            "orphan_sidecars": orphan_sidecars[:_MAX_EXAMPLES],
            "orphan_approvals": orphan_approvals[:_MAX_EXAMPLES],
            "orphan_artifacts_candidate": orphan_artifacts[:_MAX_EXAMPLES],
            "duplicate_jobs": duplicate_jobs[:_MAX_EXAMPLES],
        },
    }


def _default_quarantine_dir(queue_dir: Path) -> Path:
    """Return a deterministic default quarantine directory under queue_dir."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return queue_dir / "quarantine" / f"reconcile-{ts}"


def _safe_relative(path: Path, queue_dir: Path) -> Path:
    """Return path relative to queue_dir; raise ValueError if it escapes.

    For symlinks: validates containment using the symlink's own filesystem
    location (not its target) so that symlinks pointing outside queue_dir can
    still be safely quarantined as filesystem entries.
    For non-symlinks: uses resolve() as before.
    """
    queue_root = queue_dir.resolve()
    if path.is_symlink():
        # Do NOT dereference the symlink – absolute() resolves parent directory
        # components without following the final symlink entry itself.
        loc = path.expanduser().absolute()
        if not loc.is_relative_to(queue_root):
            raise ValueError(f"Path escape detected: {path} is not under {queue_dir}")
        return loc.relative_to(queue_root)
    resolved = path.resolve()
    try:
        return resolved.relative_to(queue_root)
    except ValueError as exc:
        raise ValueError(f"Path escape detected: {path} is not under {queue_dir}") from exc


def _quarantine_file(src: Path, queue_dir: Path, quarantine_dir: Path) -> Path:
    """Move src into quarantine_dir preserving relative path under queue_dir.

    Returns the destination path.  Raises OSError on move failure.
    Never follows symlinks (symlinks are quarantined as-is, not dereferenced).
    """
    rel = _safe_relative(src, queue_dir)
    dest = quarantine_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    # shutil.move handles cross-device moves gracefully.
    shutil.move(str(src), str(dest))
    return dest


def quarantine_reconcile_fixes(
    queue_dir: Path,
    quarantine_dir: Path,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """Quarantine safe orphan files found by re-scanning the queue directory.

    Quarantines two conservative categories only:

    1. Orphan sidecars in terminal buckets (no matching primary job).
    2. Orphan approvals in pending/approvals/ (no corresponding pending job).

    Artifact candidates and duplicate jobs are intentionally left as report-only.

    Args:
        queue_dir: Queue root directory (already resolved).
        quarantine_dir: Directory to move orphan files into.  Must be under
            queue_dir (caller is responsible for validation).
        dry_run: When True, no files are moved; returns would-quarantine counts.

    Returns:
        A dict with keys:
            ``orphan_sidecars_quarantined``, ``orphan_approvals_quarantined``
            (actual counts when dry_run=False, else 0)
            ``orphan_sidecars_would_quarantine``, ``orphan_approvals_would_quarantine``
            (always set; equals actual counts when dry_run=False)
            ``quarantined_paths``: sorted list of paths (up to _MAX_EXAMPLES),
                actual when dry_run=False else would-quarantine paths.
    """
    # Re-run detection to get the complete set (report stores only up to _MAX_EXAMPLES).
    all_orphan_sidecars = sorted(_detect_orphan_sidecars(queue_dir))
    all_orphan_approvals = sorted(_detect_orphan_approvals(queue_dir))

    quarantined: list[str] = []
    errors: list[str] = []

    if dry_run:
        return {
            "orphan_sidecars_quarantined": 0,
            "orphan_approvals_quarantined": 0,
            "orphan_sidecars_would_quarantine": len(all_orphan_sidecars),
            "orphan_approvals_would_quarantine": len(all_orphan_approvals),
            "quarantined_paths": sorted(
                (all_orphan_sidecars + all_orphan_approvals)[:_MAX_EXAMPLES]
            ),
            "errors": [],
        }

    sidecar_count = 0
    approval_count = 0

    quarantine_dir.mkdir(parents=True, exist_ok=True)

    for path_str in all_orphan_sidecars:
        src = Path(path_str)
        if not src.exists() and not src.is_symlink():
            # Entry disappeared mid-run (dangling symlinks still qualify); non-fatal.
            continue
        try:
            dest = _quarantine_file(src, queue_dir, quarantine_dir)
            quarantined.append(str(dest))
            sidecar_count += 1
        except OSError as exc:
            errors.append(f"sidecar {path_str}: {exc}")

    for path_str in all_orphan_approvals:
        src = Path(path_str)
        if not src.exists() and not src.is_symlink():
            continue
        try:
            dest = _quarantine_file(src, queue_dir, quarantine_dir)
            quarantined.append(str(dest))
            approval_count += 1
        except OSError as exc:
            errors.append(f"approval {path_str}: {exc}")

    return {
        "orphan_sidecars_quarantined": sidecar_count,
        "orphan_approvals_quarantined": approval_count,
        "orphan_sidecars_would_quarantine": sidecar_count,
        "orphan_approvals_would_quarantine": approval_count,
        "quarantined_paths": sorted(quarantined)[:_MAX_EXAMPLES],
        "errors": errors,
    }


__all__ = ["quarantine_reconcile_fixes", "reconcile_queue"]
