"""Regression tests: pytest must not modify the repo's notes/queue/health.json.

These tests verify that the VOXERA_HEALTH_PATH isolation fixture (conftest.py)
correctly intercepts all health snapshot writes during the test suite so that
the real operator snapshot is never mutated by pytest.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from voxera.health import read_health_snapshot, record_brain_fallback_attempt

# Canonical location of the operator health snapshot inside the repo checkout.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_REPO_HEALTH_JSON = _REPO_ROOT / "notes" / "queue" / "health.json"


def _file_hash(path: Path) -> str | None:
    """Return a SHA-256 hex digest of *path*, or ``None`` if it does not exist."""
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_mtime(path: Path) -> float | None:
    """Return the mtime of *path*, or ``None`` if it does not exist."""
    if not path.exists():
        return None
    return path.stat().st_mtime


class TestHealthSnapshotIsolation:
    """Health snapshot writes during tests must not touch the repo file."""

    def test_repo_health_json_not_written_by_fallback_records(self, tmp_path: Path) -> None:
        """record_brain_fallback_attempt() must write to VOXERA_HEALTH_PATH, not repo file."""
        existed_before = _REPO_HEALTH_JSON.exists()
        mtime_before = _file_mtime(_REPO_HEALTH_JSON)
        hash_before = _file_hash(_REPO_HEALTH_JSON)

        queue_root = tmp_path / "queue"
        queue_root.mkdir()

        # Simulate the mutation sequence that previously polluted the repo snapshot.
        for _ in range(5):
            record_brain_fallback_attempt(queue_root)

        assert _REPO_HEALTH_JSON.exists() == existed_before, (
            "repo notes/queue/health.json existence changed after record_brain_fallback_attempt"
        )
        if existed_before:
            assert _file_mtime(_REPO_HEALTH_JSON) == mtime_before, (
                "repo notes/queue/health.json mtime changed after record_brain_fallback_attempt"
            )
            assert _file_hash(_REPO_HEALTH_JSON) == hash_before, (
                "repo notes/queue/health.json content changed after record_brain_fallback_attempt"
            )

    def test_voxera_health_path_env_var_is_set(self) -> None:
        """Confirm the isolation fixture sets VOXERA_HEALTH_PATH for every test."""
        assert "VOXERA_HEALTH_PATH" in os.environ, (
            "VOXERA_HEALTH_PATH should be set by the conftest _isolate_health_snapshot fixture"
        )
        health_file = Path(os.environ["VOXERA_HEALTH_PATH"])
        assert health_file.exists(), (
            f"VOXERA_HEALTH_PATH file was not seeded by the isolation fixture: {health_file}"
        )

    def test_explicit_queue_root_wins_over_voxera_health_path(self, tmp_path: Path) -> None:
        """Explicit queue_root always writes to queue_root/health.json; VOXERA_HEALTH_PATH is ignored."""
        isolated_path = Path(os.environ["VOXERA_HEALTH_PATH"])
        isolated_mtime_before = isolated_path.stat().st_mtime

        queue_root = tmp_path / "queue"
        queue_root.mkdir()

        for _ in range(3):
            record_brain_fallback_attempt(queue_root)

        # Writes went to queue_root/health.json (explicit path wins).
        assert (queue_root / "health.json").exists(), (
            "write_health_snapshot did not write to queue_root/health.json "
            "when an explicit queue_root was provided"
        )
        snap = read_health_snapshot(queue_root)
        assert snap["consecutive_brain_failures"] == 3

        # VOXERA_HEALTH_PATH (isolated file) was NOT modified by explicit-queue_root calls.
        assert isolated_path.stat().st_mtime == isolated_mtime_before, (
            "VOXERA_HEALTH_PATH should not be modified when an explicit queue_root is used"
        )

    def test_isolation_is_independent_across_tests(self, tmp_path: Path) -> None:
        """Each test starts with a clean (empty) health snapshot via the fixture."""
        queue_root = tmp_path / "queue"
        queue_root.mkdir()

        # Fresh test: consecutive_brain_failures must start at 0.
        snap = read_health_snapshot(queue_root)
        assert snap["consecutive_brain_failures"] == 0
        assert snap["daemon_state"] == "healthy"
