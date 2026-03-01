from __future__ import annotations

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


__all__ = ["reconcile_queue"]
