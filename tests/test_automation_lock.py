"""Tests for the automation runner single-writer lock and locked runner.

Coverage:

1. Lock acquisition succeeds for a normal run.
2. Second concurrent attempt fails cleanly / returns busy.
3. No queue submission happens when the lock is unavailable.
4. Runner semantics remain unchanged when lock is acquired normally.
5. Systemd unit files exist and have expected command/cadence wiring.
"""

from __future__ import annotations

import json
from pathlib import Path

from voxera.automation import (
    AutomationDefinition,
    ensure_automation_dirs,
    save_automation_definition,
)
from voxera.automation.lock import (
    RUNNER_LOCK_FILENAME,
    acquire_runner_lock,
    release_runner_lock,
)
from voxera.automation.runner import (
    RunnerPassResult,
    run_due_automations_locked,
)


def _valid_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "lock-test-auto",
        "title": "Lock test automation",
        "description": "for lock tests",
        "trigger_kind": "once_at",
        "trigger_config": {"run_at_ms": 1_700_000_000_000},
        "payload_template": {"goal": "open the dashboard"},
        "created_at_ms": 1_699_999_000_000,
        "updated_at_ms": 1_699_999_000_000,
        "created_from": "vera",
    }
    base.update(overrides)
    return base


def _make_once_at(**overrides: object) -> AutomationDefinition:
    kwargs = _valid_kwargs(**overrides)
    return AutomationDefinition(**kwargs)  # type: ignore[arg-type]


def _inbox_files(queue_root: Path) -> list[Path]:
    inbox = queue_root / "inbox"
    if not inbox.exists():
        return []
    return sorted(inbox.glob("inbox-*.json"))


# ---------------------------------------------------------------------------
# Lock acquisition
# ---------------------------------------------------------------------------


def test_lock_acquisition_succeeds_on_first_try(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)

    result = acquire_runner_lock(queue_root)
    try:
        assert result.acquired is True
        assert result.lock_path.name == RUNNER_LOCK_FILENAME
        assert result._fd is not None
    finally:
        release_runner_lock(result)


def test_second_concurrent_lock_fails_cleanly(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)

    first = acquire_runner_lock(queue_root)
    try:
        assert first.acquired is True

        second = acquire_runner_lock(queue_root)
        assert second.acquired is False
        assert "lock is held" in second.message
        assert second._fd is None
    finally:
        release_runner_lock(first)


def test_lock_release_allows_reacquisition(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)

    first = acquire_runner_lock(queue_root)
    assert first.acquired is True
    release_runner_lock(first)

    second = acquire_runner_lock(queue_root)
    try:
        assert second.acquired is True
    finally:
        release_runner_lock(second)


def test_lock_file_contains_valid_json_with_pid(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)

    result = acquire_runner_lock(queue_root)
    try:
        assert result.acquired is True
        content = result.lock_path.read_text(encoding="utf-8")
        payload = json.loads(content)
        assert "pid" in payload
        assert isinstance(payload["pid"], int)
        assert "ts" in payload
        assert isinstance(payload["ts"], float)
    finally:
        release_runner_lock(result)


def test_release_is_idempotent(tmp_path: Path) -> None:
    """Calling release twice does not raise."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)

    result = acquire_runner_lock(queue_root)
    assert result.acquired is True
    release_runner_lock(result)
    # Second release should be a no-op (fd already None).
    release_runner_lock(result)
    assert result._fd is None


# ---------------------------------------------------------------------------
# Locked runner — no submission when busy
# ---------------------------------------------------------------------------


def test_locked_runner_returns_busy_when_lock_held(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)

    save_automation_definition(
        _make_once_at(trigger_config={"run_at_ms": 1_700_000_000_000}),
        queue_root,
        touch_updated=False,
    )

    # Hold the lock externally.
    held = acquire_runner_lock(queue_root)
    assert held.acquired is True
    try:
        result = run_due_automations_locked(queue_root, now_ms=1_700_000_000_500)
        assert isinstance(result, RunnerPassResult)
        assert result.status == "busy"
        assert result.results == []
        # No inbox job was submitted.
        assert _inbox_files(queue_root) == []
    finally:
        release_runner_lock(held)


def test_locked_runner_submits_normally_when_lock_available(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)

    save_automation_definition(
        _make_once_at(id="alpha", trigger_config={"run_at_ms": 1_700_000_000_000}),
        queue_root,
        touch_updated=False,
    )

    result = run_due_automations_locked(queue_root, now_ms=1_700_000_000_500)
    assert result.status == "ok"
    assert len(result.results) == 1
    assert result.results[0].outcome == "submitted"
    assert result.results[0].automation_id == "alpha"
    assert len(_inbox_files(queue_root)) == 1


def test_locked_runner_summary_message_reflects_outcomes(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)

    # One due, one disabled.
    save_automation_definition(
        _make_once_at(id="due-one", trigger_config={"run_at_ms": 1_700_000_000_000}),
        queue_root,
        touch_updated=False,
    )
    save_automation_definition(
        _make_once_at(
            id="off-one",
            enabled=False,
            trigger_config={"run_at_ms": 1_700_000_000_000},
        ),
        queue_root,
        touch_updated=False,
    )

    result = run_due_automations_locked(queue_root, now_ms=1_700_000_000_500)
    assert result.status == "ok"
    assert "1 submitted" in result.message
    assert "1 skipped" in result.message


def test_locked_runner_empty_queue_returns_ok_no_definitions(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)

    result = run_due_automations_locked(queue_root, now_ms=1_700_000_000_500)
    assert result.status == "ok"
    assert result.results == []
    assert "no definitions" in result.message


# ---------------------------------------------------------------------------
# Systemd unit files
# ---------------------------------------------------------------------------

SYSTEMD_DIR = Path("deploy/systemd/user")


def test_automation_service_unit_exists_and_has_correct_shape() -> None:
    unit = (SYSTEMD_DIR / "voxera-automation.service").read_text(encoding="utf-8")
    assert "Type=oneshot" in unit
    assert "voxera automation run-due-once" in unit
    assert "Description=Voxera Automation Runner" in unit


def test_automation_timer_unit_exists_and_has_correct_cadence() -> None:
    unit = (SYSTEMD_DIR / "voxera-automation.timer").read_text(encoding="utf-8")
    assert "OnCalendar=minutely" in unit
    assert "Persistent=true" in unit
    assert "timers.target" in unit


def test_automation_units_in_makefile() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")
    assert "voxera-automation.service" in makefile
    assert "voxera-automation.timer" in makefile
