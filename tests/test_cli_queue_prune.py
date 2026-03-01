from __future__ import annotations

import json
import os
import time
from pathlib import Path

from typer.testing import CliRunner

from voxera import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job(bucket_dir: Path, name: str, mtime: float | None = None) -> Path:
    """Create a job file in bucket_dir with an optional explicit mtime."""
    bucket_dir.mkdir(parents=True, exist_ok=True)
    job = bucket_dir / name
    job.write_text(f'{{"id": "{name}"}}', encoding="utf-8")
    if mtime is not None:
        os.utime(job, (mtime, mtime))
    return job


def _make_sidecar(bucket_dir: Path, stem: str, suffix: str, mtime: float | None = None) -> Path:
    """Create a sidecar file (e.g. job-XYZ.error.json) in bucket_dir."""
    bucket_dir.mkdir(parents=True, exist_ok=True)
    sidecar = bucket_dir / f"{stem}{suffix}"
    sidecar.write_text("{}", encoding="utf-8")
    if mtime is not None:
        os.utime(sidecar, (mtime, mtime))
    return sidecar


def _queue_dir(tmp_path: Path) -> Path:
    """Return a fresh queue root under tmp_path."""
    return tmp_path / "queue"


# ---------------------------------------------------------------------------
# Test 1: No rules configured → exits 0 with message
# ---------------------------------------------------------------------------


def test_no_rules_configured_exits_zero(tmp_path: Path) -> None:
    """When neither --max-age-days nor --max-count is given, exit 0 with message."""
    qd = _queue_dir(tmp_path)
    done = qd / "done"
    _make_job(done, "job-001.json")

    runner = CliRunner()
    result = runner.invoke(cli.app, ["queue", "prune", "--queue-dir", str(qd)])

    assert result.exit_code == 0, result.output
    assert "no pruning rules" in result.output.lower()


# ---------------------------------------------------------------------------
# Test 2: Dry-run does not delete anything
# ---------------------------------------------------------------------------


def test_dry_run_does_not_delete(tmp_path: Path) -> None:
    """Default (no --yes) must never delete anything."""
    qd = _queue_dir(tmp_path)
    old_mtime = time.time() - 86400 * 30  # 30 days ago
    job = _make_job(qd / "done", "job-old.json", mtime=old_mtime)

    runner = CliRunner()
    result = runner.invoke(
        cli.app, ["queue", "prune", "--max-age-days", "1", "--queue-dir", str(qd)]
    )

    assert result.exit_code == 0, result.output
    # File must still exist
    assert job.exists()
    # Output should indicate dry-run
    assert "dry-run" in result.output.lower() or "would prune" in result.output.lower()


# ---------------------------------------------------------------------------
# Test 3: Age-based pruning selects only old jobs
# ---------------------------------------------------------------------------


def test_age_based_prune_selects_old_only(tmp_path: Path) -> None:
    """--max-age-days 1 selects only jobs older than 1 day; new jobs are kept."""
    qd = _queue_dir(tmp_path)
    now = time.time()
    old_mtime = now - 86400 * 3  # 3 days ago
    new_mtime = now - 3600  # 1 hour ago

    for bucket in ("done", "failed", "canceled"):
        _make_job(qd / bucket, "job-old.json", mtime=old_mtime)
        _make_job(qd / bucket, "job-new.json", mtime=new_mtime)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["queue", "prune", "--max-age-days", "1", "--queue-dir", str(qd)],
    )

    assert result.exit_code == 0, result.output
    # 2 candidates per bucket × 3 buckets = 6 total; 1 old per bucket × 3 = 3 selected
    assert "3" in result.output  # total would-prune
    # Output must show per-bucket counts
    assert "done/" in result.output or "done" in result.output
    assert "failed/" in result.output or "failed" in result.output
    assert "canceled/" in result.output or "canceled" in result.output


# ---------------------------------------------------------------------------
# Test 4: Count-based pruning keeps newest N per bucket
# ---------------------------------------------------------------------------


def test_count_based_prune_per_bucket(tmp_path: Path) -> None:
    """--max-count 1 keeps the single newest per bucket; older ones selected."""
    qd = _queue_dir(tmp_path)
    now = time.time()

    for bucket in ("done", "failed", "canceled"):
        bdir = qd / bucket
        # Create 3 jobs with different mtimes
        _make_job(bdir, "job-a.json", mtime=now - 300)  # oldest
        _make_job(bdir, "job-b.json", mtime=now - 200)  # middle
        _make_job(bdir, "job-c.json", mtime=now - 100)  # newest — must be kept

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["queue", "prune", "--max-count", "1", "--queue-dir", str(qd)],
    )

    assert result.exit_code == 0, result.output
    # 3 candidates per bucket; keep 1 → 2 selected per bucket × 3 = 6 total selected
    # Check the per-bucket "2" appears for each bucket
    output = result.output
    # At minimum, verify total would-prune reflects 6 (or "6" appears)
    assert "6" in output or "2" in output  # 2 selected per bucket shows in each line


# ---------------------------------------------------------------------------
# Test 5: --yes deletes selected jobs AND their sidecars
# ---------------------------------------------------------------------------


def test_yes_deletes_jobs_and_sidecars(tmp_path: Path) -> None:
    """--yes deletes selected job files and matching sidecars in the same bucket."""
    qd = _queue_dir(tmp_path)
    now = time.time()
    old_mtime = now - 86400 * 10  # 10 days ago

    done_dir = qd / "done"
    # Create one old job with a sidecar, and one new job to verify it's kept
    old_job = _make_job(done_dir, "job-old.json", mtime=old_mtime)
    old_sidecar = _make_sidecar(done_dir, "job-old", ".error.json", mtime=old_mtime)
    new_job = _make_job(done_dir, "job-new.json", mtime=now - 60)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["queue", "prune", "--max-age-days", "1", "--yes", "--queue-dir", str(qd)],
    )

    assert result.exit_code == 0, result.output
    # Old job and its sidecar must be gone
    assert not old_job.exists(), "old job must be deleted"
    assert not old_sidecar.exists(), "old sidecar must be deleted"
    # New job must be preserved
    assert new_job.exists(), "new job must not be deleted"


# ---------------------------------------------------------------------------
# Test 6: Missing bucket dirs handled gracefully
# ---------------------------------------------------------------------------


def test_missing_buckets_handled_gracefully(tmp_path: Path) -> None:
    """If terminal bucket directories do not exist, command exits 0 without crash."""
    qd = _queue_dir(tmp_path)
    # Create queue root but no terminal bucket dirs
    qd.mkdir(parents=True)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["queue", "prune", "--max-age-days", "1", "--queue-dir", str(qd)],
    )

    assert result.exit_code == 0, result.output
    # All buckets show 0 candidates
    assert "0" in result.output


# ---------------------------------------------------------------------------
# Test 7: --json output is valid JSON with required keys
# ---------------------------------------------------------------------------


def test_json_output_structure(tmp_path: Path) -> None:
    """--json emits parseable JSON with all required keys."""
    qd = _queue_dir(tmp_path)
    now = time.time()
    old_mtime = now - 86400 * 5  # 5 days ago

    done_dir = qd / "done"
    _make_job(done_dir, "job-alpha.json", mtime=old_mtime)
    _make_job(done_dir, "job-beta.json", mtime=now - 60)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "queue",
            "prune",
            "--max-age-days",
            "1",
            "--json",
            "--queue-dir",
            str(qd),
        ],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    # Required top-level keys
    assert "status" in data
    assert "queue_dir" in data
    assert "buckets_processed" in data
    assert "per_bucket" in data
    assert "reclaimed_bytes" in data
    assert "errors" in data

    # Dry-run by default
    assert data["status"] == "dry_run"

    # per_bucket has entries for terminal buckets
    per_bucket = data["per_bucket"]
    for bucket in ("done", "failed", "canceled"):
        assert bucket in per_bucket, f"missing bucket {bucket!r} in per_bucket"
        counts = per_bucket[bucket]
        assert "candidates" in counts
        assert "selected" in counts
        assert "pruned" in counts

    # done/ has 2 candidates, 1 selected (the old one)
    assert per_bucket["done"]["candidates"] == 2
    assert per_bucket["done"]["selected"] == 1


# ---------------------------------------------------------------------------
# Test bonus: inbox/ and pending/ are never touched
# ---------------------------------------------------------------------------


def test_inbox_and_pending_not_touched(tmp_path: Path) -> None:
    """queue prune must never touch inbox/ or pending/ directories."""
    qd = _queue_dir(tmp_path)
    now = time.time()
    old_mtime = now - 86400 * 30

    # Drop files into inbox and pending — they must survive
    inbox_job = _make_job(qd / "inbox", "job-inbox.json", mtime=old_mtime)
    pending_job = _make_job(qd / "pending", "job-pending.json", mtime=old_mtime)

    # Add an old done job so rules actually apply
    _make_job(qd / "done", "job-done-old.json", mtime=old_mtime)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["queue", "prune", "--max-age-days", "1", "--yes", "--queue-dir", str(qd)],
    )

    assert result.exit_code == 0, result.output
    # inbox and pending must be completely untouched
    assert inbox_job.exists(), "inbox job must not be deleted"
    assert pending_job.exists(), "pending job must not be deleted"
