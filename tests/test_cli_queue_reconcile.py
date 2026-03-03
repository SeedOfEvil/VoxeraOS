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


# ---------------------------------------------------------------------------
# Fix mode helpers
# ---------------------------------------------------------------------------


def _run_reconcile_fix(
    tmp_path: Path,
    *,
    yes: bool = False,
    quarantine_dir: str = "",
    use_json: bool = True,
) -> tuple[int, dict]:
    """Invoke `voxera queue reconcile --fix [--yes] [--quarantine-dir D] --json --queue-dir P`."""
    runner = CliRunner()
    args = ["queue", "reconcile", "--fix", "--queue-dir", str(tmp_path)]
    if yes:
        args.append("--yes")
    if quarantine_dir:
        args += ["--quarantine-dir", quarantine_dir]
    if use_json:
        args.append("--json")
    result = runner.invoke(cli.app, args)
    data = json.loads(result.output) if use_json else {}
    return result.exit_code, data


# ---------------------------------------------------------------------------
# Fix mode test 1: --fix without --yes is a dry-run (preview); no FS changes
# ---------------------------------------------------------------------------


def test_fix_preview_does_not_change_filesystem(tmp_path: Path) -> None:
    """--fix without --yes must not move any files (pure preview)."""
    failed_dir = tmp_path / "failed"
    sidecar = _make_sidecar(failed_dir, "job-orphan.error.json")
    approvals_dir = tmp_path / "pending" / "approvals"
    approval = _make_approval(approvals_dir, "job-gone.approval.json")

    exit_code, data = _run_reconcile_fix(tmp_path, yes=False)

    assert exit_code == 0
    # Files must still exist in their original locations.
    assert sidecar.exists(), "Orphan sidecar was moved in preview mode!"
    assert approval.exists(), "Orphan approval was moved in preview mode!"
    # Output indicates preview mode.
    assert data["mode"] == "fix_preview"
    # Would-quarantine counts reflect the orphans.
    fc = data["fix_counts"]
    assert fc["orphan_sidecars_would_quarantine"] == 1
    assert fc["orphan_approvals_would_quarantine"] == 1
    # No actual quarantine happened.
    assert fc["orphan_sidecars_quarantined"] == 0
    assert fc["orphan_approvals_quarantined"] == 0


def test_fix_preview_human_output_indicates_preview(tmp_path: Path) -> None:
    """Human output for --fix (no --yes) contains 'preview' or 'dry-run'."""
    failed_dir = tmp_path / "failed"
    _make_sidecar(failed_dir, "job-orphan.error.json")

    exit_code, _ = _run_reconcile_fix(tmp_path, yes=False, use_json=False)
    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["queue", "reconcile", "--fix", "--queue-dir", str(tmp_path)],
    )
    assert result.exit_code == 0
    output_lower = result.output.lower()
    assert "preview" in output_lower or "dry-run" in output_lower or "dry run" in output_lower


# ---------------------------------------------------------------------------
# Fix mode test 2: --fix --yes quarantines only safe orphans
# ---------------------------------------------------------------------------


def test_fix_yes_quarantines_orphan_sidecar_and_approval(tmp_path: Path) -> None:
    """--fix --yes moves orphan sidecar + orphan approval; originals are gone."""
    failed_dir = tmp_path / "failed"
    sidecar = _make_sidecar(failed_dir, "job-orphan.error.json")
    approvals_dir = tmp_path / "pending" / "approvals"
    approval = _make_approval(approvals_dir, "job-gone.approval.json")

    exit_code, data = _run_reconcile_fix(tmp_path, yes=True)

    assert exit_code == 0
    assert data["mode"] == "fix_applied"
    fc = data["fix_counts"]
    assert fc["orphan_sidecars_quarantined"] == 1
    assert fc["orphan_approvals_quarantined"] == 1
    # Originals must be gone.
    assert not sidecar.exists(), "Orphan sidecar still exists after quarantine!"
    assert not approval.exists(), "Orphan approval still exists after quarantine!"
    # Quarantined copies must exist.
    q_dir = Path(data["quarantine_dir"])
    assert q_dir.exists()
    quarantined = list(q_dir.rglob("*.json"))
    assert len(quarantined) == 2, f"Expected 2 quarantined files, got: {quarantined}"


def test_fix_yes_does_not_quarantine_artifact_candidates(tmp_path: Path) -> None:
    """Artifact candidates are never moved in fix mode (report-only category)."""
    artifacts_dir = tmp_path / "artifacts"
    artifact_entry = _make_artifact_dir(artifacts_dir, "job-orphan-art")

    exit_code, data = _run_reconcile_fix(tmp_path, yes=True)

    assert exit_code == 0
    # Artifact directory must still be present.
    assert artifact_entry.exists(), "Artifact candidate was moved — must not be!"
    # Issue still reported but not fixed.
    assert data["issue_counts"]["orphan_artifacts_candidate"] == 1
    assert data["fix_counts"]["orphan_sidecars_quarantined"] == 0
    assert data["fix_counts"]["orphan_approvals_quarantined"] == 0


def test_fix_yes_does_not_quarantine_duplicates(tmp_path: Path) -> None:
    """Duplicate job files are never moved in fix mode (report-only category)."""
    _make_job(tmp_path / "done", "job-dup.json")
    _make_job(tmp_path / "failed", "job-dup.json")

    exit_code, data = _run_reconcile_fix(tmp_path, yes=True)

    assert exit_code == 0
    assert (tmp_path / "done" / "job-dup.json").exists()
    assert (tmp_path / "failed" / "job-dup.json").exists()
    assert data["issue_counts"]["duplicate_jobs"] == 1
    assert data["fix_counts"]["orphan_sidecars_quarantined"] == 0
    assert data["fix_counts"]["orphan_approvals_quarantined"] == 0


def test_fix_yes_paired_sidecar_not_quarantined(tmp_path: Path) -> None:
    """A sidecar paired with its primary job must NOT be quarantined."""
    failed_dir = tmp_path / "failed"
    _make_job(failed_dir, "job-ok.json")
    sidecar = _make_sidecar(failed_dir, "job-ok.error.json")

    exit_code, data = _run_reconcile_fix(tmp_path, yes=True)

    assert exit_code == 0
    assert sidecar.exists(), "Paired sidecar was incorrectly quarantined!"
    assert data["fix_counts"]["orphan_sidecars_quarantined"] == 0


# ---------------------------------------------------------------------------
# Fix mode test 3: Quarantine dir is under queue_dir and has deterministic format
# ---------------------------------------------------------------------------


def test_quarantine_dir_default_under_queue_dir(tmp_path: Path) -> None:
    """Default quarantine directory is created inside queue_dir with correct prefix."""
    failed_dir = tmp_path / "failed"
    _make_sidecar(failed_dir, "job-stray.error.json")

    exit_code, data = _run_reconcile_fix(tmp_path, yes=True)

    assert exit_code == 0
    q_dir_str = data["quarantine_dir"]
    assert q_dir_str is not None
    q_dir = Path(q_dir_str)
    # Must be within queue_dir.
    q_dir.relative_to(tmp_path)  # raises ValueError if not under tmp_path
    # Must match expected prefix pattern: <queue_dir>/quarantine/reconcile-
    assert str(q_dir).startswith(str(tmp_path / "quarantine" / "reconcile-"))


def test_quarantine_dir_custom_within_queue_dir(tmp_path: Path) -> None:
    """A custom --quarantine-dir within queue_dir is accepted."""
    failed_dir = tmp_path / "failed"
    _make_sidecar(failed_dir, "job-stray.error.json")
    custom_q = str(tmp_path / "my-quarantine")

    exit_code, data = _run_reconcile_fix(tmp_path, yes=True, quarantine_dir=custom_q)

    assert exit_code == 0
    assert data["quarantine_dir"] == custom_q
    assert Path(custom_q).exists()


def test_quarantine_dir_outside_queue_dir_rejected(tmp_path: Path) -> None:
    """A --quarantine-dir outside queue_dir must be rejected with exit code 1."""
    import tempfile

    with tempfile.TemporaryDirectory() as external:
        runner = CliRunner()
        result = runner.invoke(
            cli.app,
            [
                "queue",
                "reconcile",
                "--fix",
                "--yes",
                "--queue-dir",
                str(tmp_path),
                "--quarantine-dir",
                external,
            ],
        )
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Fix mode test 4: JSON output has required schema fields for fix mode
# ---------------------------------------------------------------------------


def test_json_fix_preview_schema(tmp_path: Path) -> None:
    """JSON output in fix_preview mode has mode, quarantine_dir, and fix_counts."""
    failed_dir = tmp_path / "failed"
    _make_sidecar(failed_dir, "job-orphan.error.json")

    exit_code, data = _run_reconcile_fix(tmp_path, yes=False)

    assert exit_code == 0
    assert data["status"] == "ok"
    assert data["mode"] == "fix_preview"
    assert data["quarantine_dir"] is not None
    assert "fix_counts" in data
    fc = data["fix_counts"]
    assert set(fc.keys()) == {
        "orphan_sidecars_quarantined",
        "orphan_sidecars_would_quarantine",
        "orphan_approvals_quarantined",
        "orphan_approvals_would_quarantine",
    }
    assert "quarantined_paths" in data
    assert isinstance(data["quarantined_paths"], list)


def test_json_fix_applied_schema(tmp_path: Path) -> None:
    """JSON output in fix_applied mode has correct mode and quarantined paths."""
    failed_dir = tmp_path / "failed"
    _make_sidecar(failed_dir, "job-orphan.error.json")
    approvals_dir = tmp_path / "pending" / "approvals"
    _make_approval(approvals_dir, "job-missing.approval.json")

    exit_code, data = _run_reconcile_fix(tmp_path, yes=True)

    assert exit_code == 0
    assert data["mode"] == "fix_applied"
    assert data["quarantine_dir"] is not None
    fc = data["fix_counts"]
    assert fc["orphan_sidecars_quarantined"] == 1
    assert fc["orphan_approvals_quarantined"] == 1
    # quarantined_paths must be a sorted list (deterministic).
    paths = data["quarantined_paths"]
    assert isinstance(paths, list)
    assert paths == sorted(paths)


def test_json_report_mode_fix_counts_are_zero(tmp_path: Path) -> None:
    """JSON output in report-only mode always has zeroed fix_counts."""
    exit_code, data = _run_reconcile(tmp_path)

    assert exit_code == 0
    assert data["mode"] == "report"
    fc = data["fix_counts"]
    assert all(v == 0 for v in fc.values())
    assert data["quarantine_dir"] is None
    assert data["quarantined_paths"] == []


# ---------------------------------------------------------------------------
# Regression: symlink orphan quarantine (HF — fix --yes symlink crash)
# ---------------------------------------------------------------------------


def test_fix_yes_quarantines_symlink_orphan_without_following_target(tmp_path: Path) -> None:
    """--fix --yes quarantines a symlink orphan without touching its target.

    Repro for: _safe_relative() followed symlinks via resolve(), causing
    "path escape detected" when the symlink target was outside queue_dir.
    """
    external_target = tmp_path.parent / "DO_NOT_DELETE_symlink_test.txt"
    external_target.write_text("do not delete", encoding="utf-8")
    try:
        failed_dir = tmp_path / "failed"
        failed_dir.mkdir(parents=True, exist_ok=True)
        symlink_orphan = failed_dir / "job-orphan.error.json"
        symlink_orphan.symlink_to(external_target)

        exit_code, data = _run_reconcile_fix(tmp_path, yes=True)

        # Must not crash.
        assert exit_code == 0, f"Command crashed; output: {data}"
        # External target must be untouched.
        assert external_target.exists(), "External target was deleted — must never happen!"
        # Symlink must have been moved out of failed/.
        assert not symlink_orphan.exists(), "Symlink orphan still in failed/ after quarantine"
        assert not symlink_orphan.is_symlink(), "Symlink still exists at original location"
        # Quarantined entry must exist under quarantine dir and still be a symlink.
        q_dir = Path(data["quarantine_dir"])
        quarantined_link = q_dir / "failed" / "job-orphan.error.json"
        assert quarantined_link.is_symlink(), "Quarantined entry is not a symlink"
        # One sidecar quarantined.
        assert data["fix_counts"]["orphan_sidecars_quarantined"] == 1
    finally:
        external_target.unlink(missing_ok=True)


def test_reconcile_json_output_non_empty_with_ts(tmp_path: Path) -> None:
    exit_code, data = _run_reconcile(tmp_path)
    assert exit_code == 0
    assert isinstance(data, dict)
    assert data
    assert "issue_counts" in data
    assert "ts_ms" in data
