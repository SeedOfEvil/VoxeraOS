"""Tests for the automation operator CLI commands.

Covers:

- ``voxera automation list`` shows saved definitions from storage.
- ``voxera automation show <id>`` renders the correct definition.
- ``voxera automation enable <id>`` flips enabled to true and persists.
- ``voxera automation disable <id>`` flips enabled to false and persists.
- ``voxera automation history <id>`` shows linked history entries.
- ``voxera automation run-now <id>`` processes through the runner.
- Missing automation id returns a clean operator-facing error.
- Malformed definitions/history files fail safely.
- Runner/runtime semantics remain unchanged (run-now submits via queue).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voxera.automation import (
    AutomationDefinition,
    ensure_automation_dirs,
    history_dir,
    list_history_records,
    load_automation_definition,
    save_automation_definition,
    write_history_record,
)
from voxera.automation.history import build_history_record, generate_run_id


def _valid_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "demo-automation",
        "title": "Demo automation",
        "description": "a demo",
        "trigger_kind": "once_at",
        "trigger_config": {"run_at_ms": 1_700_000_000_000},
        "payload_template": {"goal": "open the dashboard"},
        "created_at_ms": 1_699_999_000_000,
        "updated_at_ms": 1_699_999_000_000,
        "created_from": "cli",
    }
    base.update(overrides)
    return base


def _make_defn(**overrides: object) -> AutomationDefinition:
    kwargs = _valid_kwargs(**overrides)
    return AutomationDefinition(**kwargs)  # type: ignore[arg-type]


def _invoke(args: list[str], tmp_path: Path) -> object:
    """Run a CLI command against a tmp queue dir and return the CliRunner result."""
    from typer.testing import CliRunner

    from voxera.cli import app

    runner = CliRunner()
    return runner.invoke(app, args, color=False)


def _inbox_files(queue_root: Path) -> list[Path]:
    inbox = queue_root / "inbox"
    if not inbox.exists():
        return []
    return sorted(inbox.glob("inbox-*.json"))


# ---------------------------------------------------------------------------
# automation list
# ---------------------------------------------------------------------------


def test_list_empty(tmp_path: Path) -> None:
    """List with no definitions shows a table with the placeholder row."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    result = _invoke(["automation", "list", "--queue-dir", str(queue_root)], tmp_path)
    assert result.exit_code == 0  # type: ignore[union-attr]


def test_list_shows_saved_definitions(tmp_path: Path) -> None:
    """List shows all saved definitions with key fields."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    save_automation_definition(
        _make_defn(id="alpha", title="Alpha"),
        queue_root,
        touch_updated=False,
    )
    save_automation_definition(
        _make_defn(id="beta", title="Beta", enabled=False),
        queue_root,
        touch_updated=False,
    )
    result = _invoke(["automation", "list", "--queue-dir", str(queue_root)], tmp_path)
    assert result.exit_code == 0  # type: ignore[union-attr]
    out = result.stdout  # type: ignore[union-attr]
    assert "alpha" in out
    assert "beta" in out
    assert "True" in out
    assert "False" in out
    assert "once_at" in out


def test_list_skips_malformed_files(tmp_path: Path) -> None:
    """Malformed definition files on disk are silently skipped during list."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    save_automation_definition(
        _make_defn(id="good", title="Good"),
        queue_root,
        touch_updated=False,
    )
    # Write a malformed file alongside the good one.
    bad_path = queue_root / "automations" / "definitions" / "bad.json"
    bad_path.write_text("not valid json {{{", encoding="utf-8")

    result = _invoke(["automation", "list", "--queue-dir", str(queue_root)], tmp_path)
    assert result.exit_code == 0  # type: ignore[union-attr]
    out = result.stdout  # type: ignore[union-attr]
    assert "good" in out
    # The malformed file should not cause an error or appear in the list.
    assert "bad" not in out


# ---------------------------------------------------------------------------
# automation show
# ---------------------------------------------------------------------------


def test_show_renders_definition(tmp_path: Path) -> None:
    """Show renders a full JSON view of the definition."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    save_automation_definition(
        _make_defn(id="show-test", title="Show Test"),
        queue_root,
        touch_updated=False,
    )
    result = _invoke(["automation", "show", "show-test", "--queue-dir", str(queue_root)], tmp_path)
    assert result.exit_code == 0  # type: ignore[union-attr]
    out = result.stdout  # type: ignore[union-attr]
    assert "show-test" in out
    assert "Show Test" in out
    assert "once_at" in out


def test_show_missing_id_exits_nonzero(tmp_path: Path) -> None:
    """Show with a missing id returns a clean error."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    result = _invoke(
        ["automation", "show", "does-not-exist", "--queue-dir", str(queue_root)], tmp_path
    )
    assert result.exit_code == 1  # type: ignore[union-attr]
    assert "automation not found" in result.stdout.lower()  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# automation enable
# ---------------------------------------------------------------------------


def test_enable_flips_to_true(tmp_path: Path) -> None:
    """Enable sets enabled=True and persists the change."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    save_automation_definition(
        _make_defn(id="enable-test", enabled=False),
        queue_root,
        touch_updated=False,
    )
    result = _invoke(
        ["automation", "enable", "enable-test", "--queue-dir", str(queue_root)], tmp_path
    )
    assert result.exit_code == 0  # type: ignore[union-attr]
    assert "enabled" in result.stdout.lower()  # type: ignore[union-attr]
    # Verify persistence.
    reloaded = load_automation_definition("enable-test", queue_root)
    assert reloaded.enabled is True


def test_enable_already_enabled(tmp_path: Path) -> None:
    """Enable on an already-enabled definition is a no-op with a message."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    save_automation_definition(
        _make_defn(id="already-on", enabled=True),
        queue_root,
        touch_updated=False,
    )
    result = _invoke(
        ["automation", "enable", "already-on", "--queue-dir", str(queue_root)], tmp_path
    )
    assert result.exit_code == 0  # type: ignore[union-attr]
    assert "already enabled" in result.stdout.lower()  # type: ignore[union-attr]


def test_enable_missing_id_exits_nonzero(tmp_path: Path) -> None:
    """Enable with a missing id returns a clean error."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    result = _invoke(["automation", "enable", "nope", "--queue-dir", str(queue_root)], tmp_path)
    assert result.exit_code == 1  # type: ignore[union-attr]
    assert "automation not found" in result.stdout.lower()  # type: ignore[union-attr]


def test_enable_preserves_unrelated_fields(tmp_path: Path) -> None:
    """Enable only changes enabled; other fields are preserved."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    original = _make_defn(
        id="preserve-test",
        enabled=False,
        description="keep this",
        last_run_at_ms=1_700_000_000_000,
        last_job_ref="inbox-1234.json",
    )
    save_automation_definition(original, queue_root, touch_updated=False)
    _invoke(["automation", "enable", "preserve-test", "--queue-dir", str(queue_root)], tmp_path)
    reloaded = load_automation_definition("preserve-test", queue_root)
    assert reloaded.enabled is True
    assert reloaded.description == "keep this"
    assert reloaded.last_run_at_ms == 1_700_000_000_000
    assert reloaded.last_job_ref == "inbox-1234.json"
    assert reloaded.trigger_kind == original.trigger_kind
    assert reloaded.trigger_config == original.trigger_config
    assert reloaded.payload_template == original.payload_template


# ---------------------------------------------------------------------------
# automation disable
# ---------------------------------------------------------------------------


def test_disable_flips_to_false(tmp_path: Path) -> None:
    """Disable sets enabled=False and persists the change."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    save_automation_definition(
        _make_defn(id="disable-test", enabled=True),
        queue_root,
        touch_updated=False,
    )
    result = _invoke(
        ["automation", "disable", "disable-test", "--queue-dir", str(queue_root)], tmp_path
    )
    assert result.exit_code == 0  # type: ignore[union-attr]
    assert "disabled" in result.stdout.lower()  # type: ignore[union-attr]
    reloaded = load_automation_definition("disable-test", queue_root)
    assert reloaded.enabled is False


def test_disable_already_disabled(tmp_path: Path) -> None:
    """Disable on an already-disabled definition is a no-op with a message."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    save_automation_definition(
        _make_defn(id="already-off", enabled=False),
        queue_root,
        touch_updated=False,
    )
    result = _invoke(
        ["automation", "disable", "already-off", "--queue-dir", str(queue_root)], tmp_path
    )
    assert result.exit_code == 0  # type: ignore[union-attr]
    assert "already disabled" in result.stdout.lower()  # type: ignore[union-attr]


def test_disable_missing_id_exits_nonzero(tmp_path: Path) -> None:
    """Disable with a missing id returns a clean error."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    result = _invoke(["automation", "disable", "nope", "--queue-dir", str(queue_root)], tmp_path)
    assert result.exit_code == 1  # type: ignore[union-attr]
    assert "automation not found" in result.stdout.lower()  # type: ignore[union-attr]


def test_disable_preserves_unrelated_fields(tmp_path: Path) -> None:
    """Disable only changes enabled; other fields are preserved."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    original = _make_defn(
        id="preserve-dis",
        enabled=True,
        description="keep this too",
        next_run_at_ms=1_700_001_000_000,
    )
    save_automation_definition(original, queue_root, touch_updated=False)
    _invoke(["automation", "disable", "preserve-dis", "--queue-dir", str(queue_root)], tmp_path)
    reloaded = load_automation_definition("preserve-dis", queue_root)
    assert reloaded.enabled is False
    assert reloaded.description == "keep this too"
    assert reloaded.next_run_at_ms == 1_700_001_000_000
    assert reloaded.trigger_kind == original.trigger_kind


# ---------------------------------------------------------------------------
# automation history
# ---------------------------------------------------------------------------


def test_history_shows_records(tmp_path: Path) -> None:
    """History shows linked history entries for a definition."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    save_automation_definition(
        _make_defn(id="hist-test"),
        queue_root,
        touch_updated=False,
    )
    # Write a history record manually.
    run_id = generate_run_id("hist-test", now_ms=1_700_000_000_000)
    record = build_history_record(
        automation_id="hist-test",
        run_id=run_id,
        triggered_at_ms=1_700_000_000_000,
        trigger_kind="once_at",
        outcome="submitted",
        queue_job_ref="inbox-123.json",
        message="due",
        payload_template={"goal": "open the dashboard"},
    )
    write_history_record(queue_root, record)

    result = _invoke(
        ["automation", "history", "hist-test", "--queue-dir", str(queue_root)], tmp_path
    )
    assert result.exit_code == 0  # type: ignore[union-attr]
    out = result.stdout  # type: ignore[union-attr]
    assert "submitted" in out
    assert "inbox-123.json" in out


def test_history_empty(tmp_path: Path) -> None:
    """History with no records shows a placeholder."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    save_automation_definition(
        _make_defn(id="no-hist"),
        queue_root,
        touch_updated=False,
    )
    result = _invoke(["automation", "history", "no-hist", "--queue-dir", str(queue_root)], tmp_path)
    assert result.exit_code == 0  # type: ignore[union-attr]
    # The placeholder text may be split across table columns.
    out = result.stdout  # type: ignore[union-attr]
    assert "No history records" in out or "found" in out


def test_history_missing_id_exits_nonzero(tmp_path: Path) -> None:
    """History with a missing definition id returns a clean error."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    result = _invoke(["automation", "history", "nope", "--queue-dir", str(queue_root)], tmp_path)
    assert result.exit_code == 1  # type: ignore[union-attr]
    assert "automation not found" in result.stdout.lower()  # type: ignore[union-attr]


def test_history_skips_malformed_files(tmp_path: Path) -> None:
    """Malformed history files are silently skipped."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    save_automation_definition(
        _make_defn(id="hist-bad"),
        queue_root,
        touch_updated=False,
    )
    # Write a good history record.
    run_id = generate_run_id("hist-bad", now_ms=1_700_000_000_000)
    record = build_history_record(
        automation_id="hist-bad",
        run_id=run_id,
        triggered_at_ms=1_700_000_000_000,
        trigger_kind="once_at",
        outcome="submitted",
        queue_job_ref="inbox-good.json",
        message="due",
        payload_template={"goal": "test"},
    )
    write_history_record(queue_root, record)
    # Write a malformed history file alongside.
    bad_path = history_dir(queue_root) / "auto-hist-bad-9999999999999-00000000.json"
    bad_path.write_text("not json {{", encoding="utf-8")

    result = _invoke(
        ["automation", "history", "hist-bad", "--queue-dir", str(queue_root)], tmp_path
    )
    assert result.exit_code == 0  # type: ignore[union-attr]
    out = result.stdout  # type: ignore[union-attr]
    assert "inbox-good.json" in out


# ---------------------------------------------------------------------------
# automation run-now
# ---------------------------------------------------------------------------


def test_run_now_submits_through_runner(tmp_path: Path) -> None:
    """run-now processes a definition through the runner and emits a queue job."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    save_automation_definition(
        _make_defn(
            id="run-now-test",
            trigger_kind="once_at",
            trigger_config={"run_at_ms": 1},
        ),
        queue_root,
        touch_updated=False,
    )
    result = _invoke(
        ["automation", "run-now", "run-now-test", "--queue-dir", str(queue_root)], tmp_path
    )
    assert result.exit_code == 0  # type: ignore[union-attr]
    out = result.stdout  # type: ignore[union-attr]
    assert "submitted" in out
    assert "run-now-test" in out
    # Verify queue job was emitted.
    assert len(_inbox_files(queue_root)) == 1


def test_run_now_missing_id_exits_nonzero(tmp_path: Path) -> None:
    """run-now with a missing id returns a clean error."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    result = _invoke(["automation", "run-now", "nope", "--queue-dir", str(queue_root)], tmp_path)
    assert result.exit_code == 1  # type: ignore[union-attr]
    assert "automation not found" in result.stdout.lower()  # type: ignore[union-attr]


def test_run_now_does_not_bypass_queue(tmp_path: Path) -> None:
    """run-now submits through the inbox path, not a direct execution path."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    save_automation_definition(
        _make_defn(
            id="queue-check",
            trigger_kind="once_at",
            trigger_config={"run_at_ms": 1},
        ),
        queue_root,
        touch_updated=False,
    )
    _invoke(["automation", "run-now", "queue-check", "--queue-dir", str(queue_root)], tmp_path)
    # The job must land in inbox/ — not in pending/ or done/.
    inbox_files = _inbox_files(queue_root)
    assert len(inbox_files) == 1
    payload = json.loads(inbox_files[0].read_text(encoding="utf-8"))
    assert payload.get("job_intent", {}).get("source_lane") == "automation_runner"
    # No files in pending or done.
    pending = queue_root / "pending"
    done = queue_root / "done"
    assert not pending.exists() or len(list(pending.glob("*.json"))) == 0
    assert not done.exists() or len(list(done.glob("*.json"))) == 0


def test_run_now_updates_definition_state(tmp_path: Path) -> None:
    """run-now updates the definition state after a successful fire."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    save_automation_definition(
        _make_defn(
            id="state-check",
            trigger_kind="once_at",
            trigger_config={"run_at_ms": 1},
        ),
        queue_root,
        touch_updated=False,
    )
    _invoke(["automation", "run-now", "state-check", "--queue-dir", str(queue_root)], tmp_path)
    reloaded = load_automation_definition("state-check", queue_root)
    # One-shot should be disabled after firing.
    assert reloaded.enabled is False
    assert reloaded.last_run_at_ms is not None
    assert reloaded.last_job_ref is not None
    assert len(reloaded.run_history_refs) == 1


# ---------------------------------------------------------------------------
# list_history_records helper
# ---------------------------------------------------------------------------


def test_list_history_records_returns_records_for_id(tmp_path: Path) -> None:
    """list_history_records returns records filtered by automation id."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)

    for auto_id in ("aaa", "bbb"):
        run_id = generate_run_id(auto_id, now_ms=1_700_000_000_000)
        record = build_history_record(
            automation_id=auto_id,
            run_id=run_id,
            triggered_at_ms=1_700_000_000_000,
            trigger_kind="once_at",
            outcome="submitted",
            queue_job_ref=f"inbox-{auto_id}.json",
            message="due",
            payload_template={"goal": "test"},
        )
        write_history_record(queue_root, record)

    aaa_records = list_history_records(queue_root, "aaa")
    assert len(aaa_records) == 1
    assert aaa_records[0]["automation_id"] == "aaa"

    bbb_records = list_history_records(queue_root, "bbb")
    assert len(bbb_records) == 1
    assert bbb_records[0]["automation_id"] == "bbb"


def test_list_history_records_newest_first(tmp_path: Path) -> None:
    """list_history_records sorts by triggered_at_ms descending."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)

    for ms in (1_700_000_000_000, 1_700_000_001_000, 1_700_000_002_000):
        run_id = generate_run_id("ordered", now_ms=ms)
        record = build_history_record(
            automation_id="ordered",
            run_id=run_id,
            triggered_at_ms=ms,
            trigger_kind="once_at",
            outcome="submitted",
            queue_job_ref=f"inbox-{ms}.json",
            message="due",
            payload_template={"goal": "test"},
        )
        write_history_record(queue_root, record)

    records = list_history_records(queue_root, "ordered")
    assert len(records) == 3
    assert records[0]["triggered_at_ms"] == 1_700_000_002_000
    assert records[1]["triggered_at_ms"] == 1_700_000_001_000
    assert records[2]["triggered_at_ms"] == 1_700_000_000_000


def test_list_history_records_empty_when_no_history(tmp_path: Path) -> None:
    """list_history_records returns empty list when no history exists."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    assert list_history_records(queue_root, "nonexistent") == []


def test_list_history_records_rejects_traversal_id(tmp_path: Path) -> None:
    """list_history_records rejects traversal-looking ids."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    with pytest.raises(ValueError, match="invalid"):
        list_history_records(queue_root, "../escape")
