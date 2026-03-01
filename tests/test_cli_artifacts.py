from __future__ import annotations

import json
import os
import time
from pathlib import Path

from typer.testing import CliRunner

from voxera import cli


def _seed_artifacts(artifacts_dir: Path) -> tuple[Path, Path, Path, Path]:
    """Create old_file, new_file, old_dir, new_dir with controlled mtimes.

    old entries are stamped 2 days ago; new entries use current time.
    """
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    old_mtime = time.time() - 86400 * 2  # 2 days ago

    old_file = artifacts_dir / "old-job.log"
    old_file.write_text("old data", encoding="utf-8")
    os.utime(old_file, (old_mtime, old_mtime))

    new_file = artifacts_dir / "new-job.log"
    new_file.write_text("new data", encoding="utf-8")

    old_dir = artifacts_dir / "old-job-dir"
    old_dir.mkdir()
    (old_dir / "plan.json").write_text('{"step": 1}', encoding="utf-8")
    os.utime(old_dir, (old_mtime, old_mtime))

    new_dir = artifacts_dir / "new-job-dir"
    new_dir.mkdir()
    (new_dir / "plan.json").write_text('{"step": 2}', encoding="utf-8")

    return old_file, new_file, old_dir, new_dir


def test_prune_dry_run_default_no_deletion(tmp_path: Path) -> None:
    """Default (no --yes) must never delete anything."""
    queue_dir = tmp_path / "queue"
    artifacts_dir = queue_dir / "artifacts"
    old_file, new_file, old_dir, new_dir = _seed_artifacts(artifacts_dir)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["artifacts", "prune", "--max-age-days", "1", "--queue-dir", str(queue_dir)],
    )

    assert result.exit_code == 0, result.output
    # Nothing deleted
    assert old_file.exists()
    assert new_file.exists()
    assert old_dir.exists()
    assert new_dir.exists()
    # Output mentions dry-run
    assert "dry-run" in result.output.lower() or "Would prune" in result.output


def test_prune_max_age_days_selects_old(tmp_path: Path) -> None:
    """--max-age-days 1 should report 2 candidates (old_file, old_dir)."""
    queue_dir = tmp_path / "queue"
    artifacts_dir = queue_dir / "artifacts"
    _seed_artifacts(artifacts_dir)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["artifacts", "prune", "--max-age-days", "1", "--queue-dir", str(queue_dir)],
    )

    assert result.exit_code == 0, result.output
    # 4 total candidates, 2 old ones selected
    assert "4" in result.output  # total_candidates
    assert "2" in result.output  # pruned_count (dry-run)


def test_prune_max_count_keeps_newest(tmp_path: Path) -> None:
    """--max-count 2 with 4 entries keeps 2 newest, reports 2 would-prune."""
    queue_dir = tmp_path / "queue"
    artifacts_dir = queue_dir / "artifacts"
    _seed_artifacts(artifacts_dir)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["artifacts", "prune", "--max-count", "2", "--queue-dir", str(queue_dir)],
    )

    assert result.exit_code == 0, result.output
    assert "4" in result.output  # total_candidates
    assert "2" in result.output  # pruned_count (dry-run)


def test_prune_yes_deletes_and_reports_bytes(tmp_path: Path) -> None:
    """--yes --max-age-days 1 should delete old entries and report reclaimed_bytes > 0."""
    queue_dir = tmp_path / "queue"
    artifacts_dir = queue_dir / "artifacts"
    old_file, new_file, old_dir, new_dir = _seed_artifacts(artifacts_dir)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "artifacts",
            "prune",
            "--max-age-days",
            "1",
            "--yes",
            "--queue-dir",
            str(queue_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    # Old entries deleted
    assert not old_file.exists()
    assert not old_dir.exists()
    # New entries preserved
    assert new_file.exists()
    assert new_dir.exists()
    # Output mentions 2 pruned (not dry-run)
    assert "2" in result.output
    # dry-run hint should NOT appear
    assert "--yes" not in result.output or "Pruned" in result.output


def test_prune_missing_artifacts_dir_exits_zero(tmp_path: Path) -> None:
    """If artifacts/ does not exist, command exits 0 with helpful message."""
    queue_dir = tmp_path / "empty-queue"
    queue_dir.mkdir()
    # Do NOT create queue_dir/artifacts/

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["artifacts", "prune", "--max-age-days", "1", "--queue-dir", str(queue_dir)],
    )

    assert result.exit_code == 0, result.output
    assert "no artifacts" in result.output.lower() or "nothing to prune" in result.output.lower()


def test_prune_no_rules_configured_exits_zero(tmp_path: Path) -> None:
    """No flags and no config should exit 0 with 'no pruning rules' message."""
    queue_dir = tmp_path / "queue"
    artifacts_dir = queue_dir / "artifacts"
    artifacts_dir.mkdir(parents=True)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["artifacts", "prune", "--queue-dir", str(queue_dir)],
    )

    assert result.exit_code == 0, result.output
    assert "no pruning rules" in result.output.lower()


def test_prune_json_output(tmp_path: Path) -> None:
    """--json flag emits parseable JSON with expected keys."""
    queue_dir = tmp_path / "queue"
    artifacts_dir = queue_dir / "artifacts"
    _seed_artifacts(artifacts_dir)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "artifacts",
            "prune",
            "--max-age-days",
            "1",
            "--json",
            "--queue-dir",
            str(queue_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "status" in data
    assert "total_candidates" in data
    assert "pruned_count" in data
    assert "reclaimed_bytes" in data
    assert "dry_run" in data
    assert data["dry_run"] is True
    assert data["total_candidates"] == 4
    assert data["pruned_count"] == 2
    assert data["reclaimed_bytes"] > 0
