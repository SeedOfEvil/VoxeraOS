from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from voxera import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job(bucket_dir: Path, name: str) -> Path:
    """Create a minimal primary job file."""
    bucket_dir.mkdir(parents=True, exist_ok=True)
    job = bucket_dir / name
    job.write_text(f'{{"id": "{name}"}}', encoding="utf-8")
    return job


def _make_sidecar(bucket_dir: Path, name: str) -> Path:
    """Create a sidecar file (e.g. job-XYZ.error.json)."""
    bucket_dir.mkdir(parents=True, exist_ok=True)
    p = bucket_dir / name
    p.write_text("{}", encoding="utf-8")
    return p


def _make_approval(approvals_dir: Path, name: str) -> Path:
    """Create an approval file in pending/approvals/."""
    approvals_dir.mkdir(parents=True, exist_ok=True)
    p = approvals_dir / name
    p.write_text('{"scope": {}}', encoding="utf-8")
    return p


def _make_artifact_dir(artifacts_dir: Path, name: str) -> Path:
    """Create a directory under artifacts/."""
    d = artifacts_dir / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_reconcile(tmp_path: Path, *, use_json: bool = True) -> tuple[int, dict]:
    """Invoke `voxera queue reconcile --json --queue-dir <path>` and parse output."""
    runner = CliRunner()
    args = ["queue", "reconcile", "--queue-dir", str(tmp_path)]
    if use_json:
        args.append("--json")
    result = runner.invoke(cli.app, args)
    data = json.loads(result.output) if use_json else {}
    return result.exit_code, data


# ---------------------------------------------------------------------------
# Test 1: Empty queue → 0 issues
# ---------------------------------------------------------------------------


def test_empty_queue_no_issues(tmp_path: Path) -> None:
    """An empty (or entirely missing) queue directory reports zero issues."""
    exit_code, data = _run_reconcile(tmp_path)

    assert exit_code == 0, data
    assert data["status"] == "ok"
    assert data["issue_counts"]["orphan_sidecars"] == 0
    assert data["issue_counts"]["orphan_approvals"] == 0
    assert data["issue_counts"]["orphan_artifacts_candidate"] == 0
    assert data["issue_counts"]["duplicate_jobs"] == 0


# ---------------------------------------------------------------------------
# Test 2: Orphan sidecar — failed/job-a.error.json with no failed/job-a.json
# ---------------------------------------------------------------------------


def test_orphan_sidecar_detected(tmp_path: Path) -> None:
    """Sidecar in terminal bucket without primary job is reported as orphan."""
    failed_dir = tmp_path / "failed"
    _make_sidecar(failed_dir, "job-a.error.json")
    # Note: failed/job-a.json is intentionally NOT created.

    exit_code, data = _run_reconcile(tmp_path)

    assert exit_code == 0
    assert data["issue_counts"]["orphan_sidecars"] == 1
    examples = data["examples"]["orphan_sidecars"]
    assert len(examples) == 1
    assert examples[0].endswith("job-a.error.json")


def test_sidecar_with_primary_not_orphan(tmp_path: Path) -> None:
    """Sidecar paired with its primary job is NOT reported as an orphan."""
    failed_dir = tmp_path / "failed"
    _make_job(failed_dir, "job-b.json")
    _make_sidecar(failed_dir, "job-b.error.json")

    exit_code, data = _run_reconcile(tmp_path)

    assert exit_code == 0
    assert data["issue_counts"]["orphan_sidecars"] == 0


# ---------------------------------------------------------------------------
# Test 3: Orphan approval — pending/approvals/job-x.approval.json, no pending/job-x.json
# ---------------------------------------------------------------------------


def test_orphan_approval_detected(tmp_path: Path) -> None:
    """Approval file with no corresponding pending job is reported."""
    approvals_dir = tmp_path / "pending" / "approvals"
    _make_approval(approvals_dir, "job-x.approval.json")
    # Note: pending/job-x.json is intentionally NOT created.

    exit_code, data = _run_reconcile(tmp_path)

    assert exit_code == 0
    assert data["issue_counts"]["orphan_approvals"] == 1
    examples = data["examples"]["orphan_approvals"]
    assert len(examples) == 1
    assert examples[0].endswith("job-x.approval.json")


def test_approval_with_pending_job_not_orphan(tmp_path: Path) -> None:
    """Approval file paired with a pending job is NOT reported as an orphan."""
    approvals_dir = tmp_path / "pending" / "approvals"
    pending_dir = tmp_path / "pending"
    _make_approval(approvals_dir, "job-y.approval.json")
    _make_job(pending_dir, "job-y.json")

    exit_code, data = _run_reconcile(tmp_path)

    assert exit_code == 0
    assert data["issue_counts"]["orphan_approvals"] == 0


# ---------------------------------------------------------------------------
# Test 4: Duplicate job filenames across buckets
# ---------------------------------------------------------------------------


def test_duplicate_job_detected(tmp_path: Path) -> None:
    """Same job filename in done/ and failed/ is reported as a duplicate."""
    _make_job(tmp_path / "done", "job-a.json")
    _make_job(tmp_path / "failed", "job-a.json")

    exit_code, data = _run_reconcile(tmp_path)

    assert exit_code == 0
    assert data["issue_counts"]["duplicate_jobs"] == 1
    entry = data["examples"]["duplicate_jobs"][0]
    assert entry["job_name"] == "job-a.json"
    assert sorted(entry["buckets"]) == ["done", "failed"]


def test_unique_job_per_bucket_not_duplicate(tmp_path: Path) -> None:
    """Different job files across buckets are not reported as duplicates."""
    _make_job(tmp_path / "done", "job-1.json")
    _make_job(tmp_path / "failed", "job-2.json")

    exit_code, data = _run_reconcile(tmp_path)

    assert exit_code == 0
    assert data["issue_counts"]["duplicate_jobs"] == 0


# ---------------------------------------------------------------------------
# Test 5: Orphan artifact candidate — artifacts/job-a exists, no job-a.json anywhere
# ---------------------------------------------------------------------------


def test_orphan_artifact_candidate_detected(tmp_path: Path) -> None:
    """Artifact entry with no matching job file anywhere is reported as a candidate."""
    artifacts_dir = tmp_path / "artifacts"
    _make_artifact_dir(artifacts_dir, "job-a")
    # Note: job-a.json is NOT present in any bucket.

    exit_code, data = _run_reconcile(tmp_path)

    assert exit_code == 0
    assert data["issue_counts"]["orphan_artifacts_candidate"] == 1
    examples = data["examples"]["orphan_artifacts_candidate"]
    assert len(examples) == 1
    assert examples[0].endswith("job-a")


def test_artifact_with_existing_job_not_orphan(tmp_path: Path) -> None:
    """Artifact entry with a matching job file in any bucket is NOT reported."""
    artifacts_dir = tmp_path / "artifacts"
    _make_artifact_dir(artifacts_dir, "job-b")
    _make_job(tmp_path / "done", "job-b.json")

    exit_code, data = _run_reconcile(tmp_path)

    assert exit_code == 0
    assert data["issue_counts"]["orphan_artifacts_candidate"] == 0


# ---------------------------------------------------------------------------
# Test 6: Human output includes "report-only" note and correct structure
# ---------------------------------------------------------------------------


def test_human_output_report_only_note(tmp_path: Path) -> None:
    """Human output always contains the report-only disclaimer."""
    exit_code, _ = _run_reconcile(tmp_path, use_json=False)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["queue", "reconcile", "--queue-dir", str(tmp_path)])

    assert result.exit_code == 0
    output_lower = result.output.lower()
    assert "report-only" in output_lower or "no changes made" in output_lower


# ---------------------------------------------------------------------------
# Test 7: JSON schema stability
# ---------------------------------------------------------------------------


def test_json_schema_fields_present(tmp_path: Path) -> None:
    """JSON output always contains the required top-level schema fields."""
    exit_code, data = _run_reconcile(tmp_path)

    assert exit_code == 0
    assert set(data.keys()) >= {"status", "queue_dir", "issue_counts", "examples"}
    counts = data["issue_counts"]
    assert set(counts.keys()) == {
        "orphan_sidecars",
        "orphan_approvals",
        "orphan_artifacts_candidate",
        "duplicate_jobs",
    }
    ex = data["examples"]
    assert set(ex.keys()) == {
        "orphan_sidecars",
        "orphan_approvals",
        "orphan_artifacts_candidate",
        "duplicate_jobs",
    }
    assert isinstance(ex["duplicate_jobs"], list)


# ---------------------------------------------------------------------------
# Test 8: Multiple issues reported together
# ---------------------------------------------------------------------------


def test_multiple_issues_combined(tmp_path: Path) -> None:
    """All four issue types can be detected in a single scan."""
    # Orphan sidecar
    _make_sidecar(tmp_path / "canceled", "job-z.state.json")
    # Orphan approval
    _make_approval(tmp_path / "pending" / "approvals", "job-missing.approval.json")
    # Duplicate job
    _make_job(tmp_path / "done", "job-dup.json")
    _make_job(tmp_path / "failed", "job-dup.json")
    # Orphan artifact
    _make_artifact_dir(tmp_path / "artifacts", "job-gone")

    exit_code, data = _run_reconcile(tmp_path)

    assert exit_code == 0
    counts = data["issue_counts"]
    assert counts["orphan_sidecars"] == 1
    assert counts["orphan_approvals"] == 1
    assert counts["duplicate_jobs"] == 1
    assert counts["orphan_artifacts_candidate"] == 1


# ---------------------------------------------------------------------------
# Test 9: Deterministic ordering — examples are always sorted
# ---------------------------------------------------------------------------


def test_examples_are_sorted(tmp_path: Path) -> None:
    """Example paths must be in stable sorted order regardless of filesystem order."""
    failed_dir = tmp_path / "failed"
    # Create multiple orphan sidecars; order on disk may vary.
    for name in ("job-c.error.json", "job-a.error.json", "job-b.error.json"):
        _make_sidecar(failed_dir, name)

    exit_code, data = _run_reconcile(tmp_path)

    assert exit_code == 0
    examples = data["examples"]["orphan_sidecars"]
    assert examples == sorted(examples)
    assert len(examples) == 3


# ---------------------------------------------------------------------------
# Test 10: Missing directories produce no errors
# ---------------------------------------------------------------------------


def test_missing_directories_no_error(tmp_path: Path) -> None:
    """Completely empty tmp_path (no subdirs at all) exits 0 with 0 issues."""
    # tmp_path is a bare directory with no queue subdirectories.
    exit_code, data = _run_reconcile(tmp_path)

    assert exit_code == 0
    assert all(v == 0 for v in data["issue_counts"].values())
