"""Tests for the minimal PR2 automation runner.

The runner consumes saved ``AutomationDefinition`` objects and, when they
are due, emits a normal canonical queue payload via the existing
``core/inbox.add_inbox_payload`` path. PR2 only supports the ``once_at``
and ``delay`` trigger kinds. These tests cover:

- due ``once_at`` emits exactly one normal queue job
- due ``delay`` emits exactly one normal queue job
- non-due definitions do not emit jobs
- disabled definitions do not emit jobs
- malformed definition files on disk are skipped safely
- unsupported trigger kinds (``recurring_interval``, ``recurring_cron``,
  ``watch_path``) are explicitly skipped and do not emit jobs
- history records are written on submit and include queue job linkage
- updated definition fields (``last_run_at_ms``, ``last_job_ref``,
  ``run_history_refs``, ``enabled``, ``next_run_at_ms``) are saved
- one-shot semantics prevent double-submit on repeated runner passes
- the emitted payload matches the saved ``payload_template`` (allowing
  for normal inbox intake enrichment like ``job_intent`` + ``id``)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voxera.automation import (
    AUTOMATION_SOURCE_LANE,
    AutomationDefinition,
    AutomationRunResult,
    definitions_dir,
    ensure_automation_dirs,
    evaluate_due_automation,
    history_dir,
    history_record_ref,
    list_automation_definitions,
    load_automation_definition,
    process_automation_definition,
    run_automation_once,
    run_due_automations,
    save_automation_definition,
)


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
        "created_from": "vera",
    }
    base.update(overrides)
    return base


def _make_once_at(**overrides: object) -> AutomationDefinition:
    kwargs = _valid_kwargs(**overrides)
    return AutomationDefinition(**kwargs)  # type: ignore[arg-type]


def _make_delay(**overrides: object) -> AutomationDefinition:
    defaults: dict[str, object] = {
        "id": "delay-automation",
        "trigger_kind": "delay",
        "trigger_config": {"delay_ms": 60_000},
    }
    defaults.update(overrides)
    kwargs = _valid_kwargs(**defaults)
    return AutomationDefinition(**kwargs)  # type: ignore[arg-type]


def _inbox_files(queue_root: Path) -> list[Path]:
    inbox = queue_root / "inbox"
    if not inbox.exists():
        return []
    return sorted(inbox.glob("inbox-*.json"))


def _history_files(queue_root: Path) -> list[Path]:
    directory = history_dir(queue_root)
    if not directory.exists():
        return []
    return sorted(directory.glob("auto-*.json"))


# ---------------------------------------------------------------------------
# evaluate_due_automation semantics
# ---------------------------------------------------------------------------


def test_evaluate_due_once_at_not_yet_due() -> None:
    definition = _make_once_at(
        trigger_config={"run_at_ms": 2_000_000_000_000},
    )
    due, reason = evaluate_due_automation(definition, now_ms=1_000_000_000_000)
    assert due is False
    assert "not yet due" in reason


def test_evaluate_due_once_at_due_exactly_at_anchor() -> None:
    definition = _make_once_at(trigger_config={"run_at_ms": 1_700_000_000_000})
    due, reason = evaluate_due_automation(definition, now_ms=1_700_000_000_000)
    assert due is True
    assert "due" in reason


def test_evaluate_due_delay_anchored_on_created_at() -> None:
    definition = _make_delay(
        created_at_ms=1_700_000_000_000,
        updated_at_ms=1_700_000_000_000,
        trigger_config={"delay_ms": 60_000},
    )
    # Anchor is 1_700_000_060_000. Before the anchor => not due.
    due, _ = evaluate_due_automation(definition, now_ms=1_700_000_059_000)
    assert due is False
    # At the anchor => due.
    due, reason = evaluate_due_automation(definition, now_ms=1_700_000_060_000)
    assert due is True
    assert "1700000060000" in reason


def test_evaluate_due_skips_disabled_definitions() -> None:
    definition = _make_once_at(enabled=False)
    due, reason = evaluate_due_automation(definition, now_ms=1_800_000_000_000)
    assert due is False
    assert reason == "definition is disabled"


def test_evaluate_due_skips_already_fired_one_shot() -> None:
    definition = _make_once_at(last_run_at_ms=1_700_000_000_500)
    due, reason = evaluate_due_automation(definition, now_ms=1_800_000_000_000)
    assert due is False
    assert "already fired" in reason


@pytest.mark.parametrize(
    ("kind", "config"),
    [
        ("recurring_interval", {"interval_ms": 60_000}),
        ("recurring_cron", {"cron": "*/5 * * * *"}),
        ("watch_path", {"path": "~/VoxeraOS/notes/incoming", "event": "created"}),
    ],
)
def test_evaluate_due_skips_unsupported_trigger_kinds(kind: str, config: dict[str, object]) -> None:
    definition = _make_once_at(trigger_kind=kind, trigger_config=config)
    due, reason = evaluate_due_automation(definition, now_ms=1_800_000_000_000)
    assert due is False
    assert "not supported" in reason
    assert kind in reason


# ---------------------------------------------------------------------------
# process_automation_definition — submit path
# ---------------------------------------------------------------------------


def test_due_once_at_emits_one_normal_queue_job(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    definition = _make_once_at(trigger_config={"run_at_ms": 1_700_000_000_000})
    save_automation_definition(definition, queue_root, touch_updated=False)

    result = process_automation_definition(definition, queue_root, now_ms=1_700_000_000_500)
    assert isinstance(result, AutomationRunResult)
    assert result.outcome == "submitted"
    assert result.queue_job_ref is not None
    assert result.history_ref is not None

    inbox_files = _inbox_files(queue_root)
    assert len(inbox_files) == 1
    emitted = json.loads(inbox_files[0].read_text(encoding="utf-8"))
    # Payload template goal is preserved verbatim.
    assert emitted["goal"] == "open the dashboard"
    # Inbox intake enrichment added a job_intent block on the canonical lane.
    assert isinstance(emitted.get("job_intent"), dict)
    assert emitted["job_intent"].get("source_lane") == AUTOMATION_SOURCE_LANE
    # The inbox-generated id is present and is used as the queue job ref.
    assert isinstance(emitted.get("id"), str) and emitted["id"]
    assert result.queue_job_ref == inbox_files[0].name


def test_due_delay_emits_one_normal_queue_job(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    definition = _make_delay(
        created_at_ms=1_700_000_000_000,
        updated_at_ms=1_700_000_000_000,
        trigger_config={"delay_ms": 30_000},
    )
    save_automation_definition(definition, queue_root, touch_updated=False)

    result = process_automation_definition(definition, queue_root, now_ms=1_700_000_030_000)
    assert result.outcome == "submitted"
    assert result.queue_job_ref is not None

    inbox_files = _inbox_files(queue_root)
    assert len(inbox_files) == 1
    emitted = json.loads(inbox_files[0].read_text(encoding="utf-8"))
    assert emitted["goal"] == "open the dashboard"
    assert emitted["job_intent"].get("source_lane") == AUTOMATION_SOURCE_LANE


def test_non_due_definition_does_not_emit(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    definition = _make_once_at(trigger_config={"run_at_ms": 2_000_000_000_000})
    save_automation_definition(definition, queue_root, touch_updated=False)

    result = process_automation_definition(definition, queue_root, now_ms=1_700_000_000_500)
    assert result.outcome == "skipped"
    assert _inbox_files(queue_root) == []
    assert _history_files(queue_root) == []

    # Definition on disk was not mutated.
    loaded = load_automation_definition(definition.id, queue_root)
    assert loaded.enabled is True
    assert loaded.last_run_at_ms is None
    assert loaded.last_job_ref is None
    assert loaded.run_history_refs == []


def test_disabled_definition_does_not_emit(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    definition = _make_once_at(enabled=False)
    save_automation_definition(definition, queue_root, touch_updated=False)

    result = process_automation_definition(definition, queue_root, now_ms=1_800_000_000_000)
    assert result.outcome == "skipped"
    assert _inbox_files(queue_root) == []
    assert _history_files(queue_root) == []


@pytest.mark.parametrize(
    ("kind", "config"),
    [
        ("recurring_interval", {"interval_ms": 60_000}),
        ("recurring_cron", {"cron": "*/5 * * * *"}),
        ("watch_path", {"path": "~/VoxeraOS/notes/incoming", "event": "created"}),
    ],
)
def test_unsupported_trigger_kinds_are_skipped(
    tmp_path: Path, kind: str, config: dict[str, object]
) -> None:
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    definition = _make_once_at(
        id=f"unsupported-{kind}".replace("_", "-"),
        trigger_kind=kind,
        trigger_config=config,
    )
    save_automation_definition(definition, queue_root, touch_updated=False)

    result = process_automation_definition(definition, queue_root, now_ms=2_000_000_000_000)
    assert result.outcome == "skipped"
    assert kind in result.message
    assert _inbox_files(queue_root) == []
    assert _history_files(queue_root) == []


# ---------------------------------------------------------------------------
# History record + definition state updates on submit
# ---------------------------------------------------------------------------


def test_submit_writes_history_record_with_queue_linkage(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    definition = _make_once_at(trigger_config={"run_at_ms": 1_700_000_000_000})
    save_automation_definition(definition, queue_root, touch_updated=False)

    result = process_automation_definition(definition, queue_root, now_ms=1_700_000_000_500)
    assert result.outcome == "submitted"
    history_files = _history_files(queue_root)
    assert len(history_files) == 1
    record = json.loads(history_files[0].read_text(encoding="utf-8"))

    assert record["schema_version"] == 1
    assert record["automation_id"] == definition.id
    assert record["run_id"] == result.run_id
    assert record["triggered_at_ms"] == 1_700_000_000_500
    assert record["trigger_kind"] == "once_at"
    assert record["outcome"] == "submitted"
    assert record["queue_job_ref"] == result.queue_job_ref
    assert record["payload_summary"].get("goal") == "open the dashboard"
    assert isinstance(record["payload_hash"], str) and len(record["payload_hash"]) == 64

    assert result.history_ref == history_record_ref(definition.id, result.run_id or "")
    assert history_files[0].name.endswith(".json")


def test_submit_updates_saved_definition_fields(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    definition = _make_once_at(
        trigger_config={"run_at_ms": 1_700_000_000_000},
        next_run_at_ms=1_700_000_000_000,
    )
    save_automation_definition(definition, queue_root, touch_updated=False)

    result = process_automation_definition(definition, queue_root, now_ms=1_700_000_000_500)
    assert result.outcome == "submitted"

    loaded = load_automation_definition(definition.id, queue_root)
    assert loaded.enabled is False  # one-shot
    assert loaded.last_run_at_ms == 1_700_000_000_500
    assert loaded.last_job_ref == result.queue_job_ref
    assert loaded.run_history_refs == [result.history_ref]
    assert loaded.next_run_at_ms is None


def test_one_shot_does_not_double_submit_on_repeated_runner_passes(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    definition = _make_once_at(trigger_config={"run_at_ms": 1_700_000_000_000})
    save_automation_definition(definition, queue_root, touch_updated=False)

    first = run_due_automations(queue_root, now_ms=1_700_000_000_500)
    assert [r.outcome for r in first] == ["submitted"]
    assert len(_inbox_files(queue_root)) == 1
    assert len(_history_files(queue_root)) == 1

    # Second pass: the definition is now disabled + already-fired. No new
    # queue job should be emitted and no new history row should be written.
    second = run_due_automations(queue_root, now_ms=1_700_000_000_700)
    assert [r.outcome for r in second] == ["skipped"]
    assert len(_inbox_files(queue_root)) == 1
    assert len(_history_files(queue_root)) == 1


def test_emitted_payload_matches_saved_payload_template(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    # Use a richer payload_template so we can verify all template fields
    # survive to the inbox file verbatim.
    template = {
        "goal": "open the dashboard",
        "title": "Open dashboard",
        "steps": [{"skill_id": "builtin.noop"}],
    }
    definition = _make_once_at(payload_template=template)
    save_automation_definition(definition, queue_root, touch_updated=False)

    result = process_automation_definition(definition, queue_root, now_ms=1_700_000_000_500)
    assert result.outcome == "submitted"
    inbox_files = _inbox_files(queue_root)
    assert len(inbox_files) == 1
    emitted = json.loads(inbox_files[0].read_text(encoding="utf-8"))

    # Template fields survive verbatim.
    assert emitted["goal"] == template["goal"]
    assert emitted["title"] == template["title"]
    assert emitted["steps"] == template["steps"]
    # Inbox intake adds an id + job_intent; it must not corrupt template.
    assert "id" in emitted
    assert isinstance(emitted.get("job_intent"), dict)


def test_queue_linkage_is_preserved_in_history_and_definition(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    definition = _make_once_at(trigger_config={"run_at_ms": 1_700_000_000_000})
    save_automation_definition(definition, queue_root, touch_updated=False)

    result = process_automation_definition(definition, queue_root, now_ms=1_700_000_000_500)
    assert result.outcome == "submitted"
    assert result.queue_job_ref is not None

    loaded = load_automation_definition(definition.id, queue_root)
    history_files = _history_files(queue_root)
    record = json.loads(history_files[0].read_text(encoding="utf-8"))

    # Definition -> queue job ref linkage.
    assert loaded.last_job_ref == result.queue_job_ref
    # History -> queue job ref linkage.
    assert record["queue_job_ref"] == result.queue_job_ref
    # Definition -> history ref linkage.
    assert loaded.run_history_refs == [result.history_ref]
    # History file lives under automations/history/ next to the definitions.
    assert history_files[0].parent == history_dir(queue_root)


# ---------------------------------------------------------------------------
# run_due_automations — inventory-level behavior
# ---------------------------------------------------------------------------


def test_run_due_automations_processes_every_valid_definition(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)

    # One due once_at.
    save_automation_definition(
        _make_once_at(id="alpha", trigger_config={"run_at_ms": 1_700_000_000_000}),
        queue_root,
        touch_updated=False,
    )
    # One due delay.
    save_automation_definition(
        _make_delay(
            id="bravo",
            created_at_ms=1_700_000_000_000,
            updated_at_ms=1_700_000_000_000,
            trigger_config={"delay_ms": 30_000},
        ),
        queue_root,
        touch_updated=False,
    )
    # One disabled.
    save_automation_definition(
        _make_once_at(
            id="charlie",
            enabled=False,
            trigger_config={"run_at_ms": 1_700_000_000_000},
        ),
        queue_root,
        touch_updated=False,
    )
    # One unsupported kind.
    save_automation_definition(
        _make_once_at(
            id="delta",
            trigger_kind="recurring_cron",
            trigger_config={"cron": "*/5 * * * *"},
        ),
        queue_root,
        touch_updated=False,
    )

    results = run_due_automations(queue_root, now_ms=1_700_000_100_000)
    by_id = {r.automation_id: r for r in results}
    assert set(by_id.keys()) == {"alpha", "bravo", "charlie", "delta"}
    assert by_id["alpha"].outcome == "submitted"
    assert by_id["bravo"].outcome == "submitted"
    assert by_id["charlie"].outcome == "skipped"
    assert by_id["delta"].outcome == "skipped"

    inbox_files = _inbox_files(queue_root)
    assert len(inbox_files) == 2
    history_files = _history_files(queue_root)
    # Only ``submitted`` runs produce a history record in PR2.
    assert len(history_files) == 2


def test_malformed_definition_files_are_skipped_safely(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)

    # A good, due definition.
    good = _make_once_at(id="good-auto", trigger_config={"run_at_ms": 1_700_000_000_000})
    save_automation_definition(good, queue_root, touch_updated=False)

    # Write garbage directly into the definitions directory.
    bad_path = definitions_dir(queue_root) / "broken.json"
    bad_path.write_text("{not valid json", encoding="utf-8")
    wrong_shape = definitions_dir(queue_root) / "wrong-shape.json"
    wrong_shape.write_text(json.dumps({"nope": "not an automation"}), encoding="utf-8")

    # The store's best-effort listing already hides malformed files, but we
    # also want to assert that the runner does not somehow surface them as
    # errors — it should simply process every valid definition.
    valid = list_automation_definitions(queue_root)
    assert [d.id for d in valid] == ["good-auto"]

    results = run_due_automations(queue_root, now_ms=1_700_000_000_500)
    assert [r.automation_id for r in results] == ["good-auto"]
    assert results[0].outcome == "submitted"
    assert len(_inbox_files(queue_root)) == 1


def test_run_automation_once_loads_by_id_and_submits(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    save_automation_definition(
        _make_once_at(id="solo", trigger_config={"run_at_ms": 1_700_000_000_000}),
        queue_root,
        touch_updated=False,
    )
    result = run_automation_once("solo", queue_root, now_ms=1_700_000_000_500)
    assert result.outcome == "submitted"
    assert result.automation_id == "solo"
    assert len(_inbox_files(queue_root)) == 1


# ---------------------------------------------------------------------------
# Fail-closed edge cases (emit + save failures, dict mutation, history
# defense-in-depth, CLI entrypoint)
# ---------------------------------------------------------------------------


def test_skipped_runs_do_not_write_history_records(tmp_path: Path) -> None:
    """History is an audit trail of actual fires, not of idle passes.

    A runner pass over a non-due or disabled definition should leave the
    history directory empty. Only ``submitted`` and ``error`` outcomes
    are durable audit rows.
    """
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    save_automation_definition(
        _make_once_at(
            id="future-1",
            trigger_config={"run_at_ms": 2_000_000_000_000},
        ),
        queue_root,
        touch_updated=False,
    )
    save_automation_definition(
        _make_once_at(id="disabled-1", enabled=False),
        queue_root,
        touch_updated=False,
    )
    save_automation_definition(
        _make_once_at(
            id="unsupported-1",
            trigger_kind="recurring_cron",
            trigger_config={"cron": "*/5 * * * *"},
        ),
        queue_root,
        touch_updated=False,
    )

    results = run_due_automations(queue_root, now_ms=1_700_000_000_500)
    assert {r.outcome for r in results} == {"skipped"}
    assert _history_files(queue_root) == []
    assert _inbox_files(queue_root) == []


def test_emit_failure_writes_error_history_and_leaves_definition_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``add_inbox_payload`` raises, the runner records an error.

    The stored definition must not be advanced (``last_run_at_ms`` stays
    None, ``enabled`` stays True), so a subsequent pass after the
    underlying problem is fixed can still fire the automation normally.
    """
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    definition = _make_once_at(trigger_config={"run_at_ms": 1_700_000_000_000})
    save_automation_definition(definition, queue_root, touch_updated=False)

    from voxera.automation import runner as runner_module

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated inbox write failure")

    monkeypatch.setattr(runner_module, "add_inbox_payload", _boom)

    result = process_automation_definition(definition, queue_root, now_ms=1_700_000_000_500)
    assert result.outcome == "error"
    assert result.queue_job_ref is None
    assert "simulated inbox write failure" in result.message
    assert result.history_ref is not None

    # Inbox is empty; the queue side was never touched.
    assert _inbox_files(queue_root) == []

    # Exactly one error history row exists and it points at no queue job.
    history_files = _history_files(queue_root)
    assert len(history_files) == 1
    record = json.loads(history_files[0].read_text(encoding="utf-8"))
    assert record["outcome"] == "error"
    assert record["queue_job_ref"] is None
    assert "simulated inbox write failure" in record["message"]

    # Definition on disk is unchanged — the next runner pass can still fire.
    loaded = load_automation_definition(definition.id, queue_root)
    assert loaded.enabled is True
    assert loaded.last_run_at_ms is None
    assert loaded.last_job_ref is None
    assert loaded.run_history_refs == []


def test_save_failure_after_emit_records_mixed_state_and_returns_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the queue job is emitted but the follow-up save fails.

    This is the mixed-state path: the inbox file and the submit history
    record already exist on disk, but the durable definition cannot be
    updated. The runner must (a) return an ``error`` result that still
    carries the real ``queue_job_ref``, and (b) write a second history
    record that references the successful queue job plus the save error
    so a reviewer can reconcile the mixed state.
    """
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    definition = _make_once_at(trigger_config={"run_at_ms": 1_700_000_000_000})
    save_automation_definition(definition, queue_root, touch_updated=False)

    from voxera.automation import runner as runner_module

    real_save = runner_module.save_automation_definition
    call_count = {"n": 0}

    def _fail_only_post_emit(*args: object, **kwargs: object) -> object:
        # The fixture save (above) is not intercepted because we install
        # the monkeypatch AFTER it. The first call under the patch is
        # the runner's post-emit save, which we force to fail.
        call_count["n"] += 1
        raise OSError("simulated disk full")

    monkeypatch.setattr(runner_module, "save_automation_definition", _fail_only_post_emit)

    result = process_automation_definition(definition, queue_root, now_ms=1_700_000_000_500)
    assert call_count["n"] == 1
    assert result.outcome == "error"
    assert result.queue_job_ref is not None
    assert "simulated disk full" in result.message
    assert "was emitted but definition state save failed" in result.message

    # The queue job really was emitted and the submit history was
    # written before the save failure was noticed.
    inbox_files = _inbox_files(queue_root)
    assert len(inbox_files) == 1
    assert inbox_files[0].name == result.queue_job_ref

    # Two history records on disk: the initial ``submitted`` row plus
    # the follow-up ``error`` row that references the same queue_job_ref.
    history_files = _history_files(queue_root)
    assert len(history_files) == 2
    records = [json.loads(p.read_text(encoding="utf-8")) for p in history_files]
    outcomes = sorted(r["outcome"] for r in records)
    assert outcomes == ["error", "submitted"]
    submitted_record = next(r for r in records if r["outcome"] == "submitted")
    error_record = next(r for r in records if r["outcome"] == "error")
    assert submitted_record["queue_job_ref"] == result.queue_job_ref
    assert error_record["queue_job_ref"] == result.queue_job_ref
    assert "simulated disk full" in error_record["message"]

    # Re-enable real save behavior and confirm the stored definition was
    # never advanced (this is the mixed-state the operator must reconcile).
    monkeypatch.setattr(runner_module, "save_automation_definition", real_save)
    loaded = load_automation_definition(definition.id, queue_root)
    assert loaded.enabled is True
    assert loaded.last_run_at_ms is None
    assert loaded.last_job_ref is None
    assert loaded.run_history_refs == []


def test_payload_template_is_not_mutated_by_runner(tmp_path: Path) -> None:
    """Submitting through the runner must not mutate the in-memory template.

    ``add_inbox_payload`` enriches the payload with ``job_intent``, ``id``,
    and friends. The runner already copies the template with ``dict(...)``
    before handing it off, so the durable definition object (and the
    file on disk) must survive the submit untouched.
    """
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    template = {
        "goal": "open the dashboard",
        "steps": [{"skill_id": "builtin.noop"}],
    }
    definition = _make_once_at(payload_template=template)
    save_automation_definition(definition, queue_root, touch_updated=False)
    template_snapshot = json.loads(json.dumps(definition.payload_template))

    result = process_automation_definition(definition, queue_root, now_ms=1_700_000_000_500)
    assert result.outcome == "submitted"

    # In-memory definition's payload_template is unchanged.
    assert definition.payload_template == template_snapshot
    # Reloaded definition from disk also unchanged (aside from the runner
    # state fields which are updated on submit).
    loaded = load_automation_definition(definition.id, queue_root)
    assert loaded.payload_template == template_snapshot


def test_write_history_record_rejects_traversal_ids(tmp_path: Path) -> None:
    """Defense-in-depth: the history module validates both ids itself.

    The runner never constructs records with bad ids, but a direct caller
    of ``write_history_record`` must not be able to escape the history
    directory by supplying a traversal-looking ``automation_id`` or
    ``run_id`` on a hand-built record.
    """
    from voxera.automation import build_history_record, history_record_ref, write_history_record

    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)

    bad_automation_ids = (
        "../escape",
        "with/slash",
        ".hidden",
        "with\x00null",
        "",
        " ",
        "-leading-hyphen",
    )
    for bad_id in bad_automation_ids:
        with pytest.raises(ValueError):
            history_record_ref(bad_id, "1700000000000-deadbeef")
        record = {
            "schema_version": 1,
            "automation_id": bad_id,
            "run_id": "1700000000000-deadbeef",
            "triggered_at_ms": 1_700_000_000_000,
            "trigger_kind": "once_at",
            "outcome": "submitted",
            "queue_job_ref": "inbox-x.json",
            "message": "ok",
            "payload_summary": {},
            "payload_hash": None,
        }
        with pytest.raises(ValueError):
            write_history_record(queue_root, record)

    bad_run_ids = ("../escape", "with/slash", "", " ", ".hidden")
    for bad_run_id in bad_run_ids:
        with pytest.raises(ValueError):
            history_record_ref("demo", bad_run_id)

    # Positive: a well-formed record writes successfully.
    good = build_history_record(
        automation_id="demo",
        run_id="1700000000000-deadbeef",
        triggered_at_ms=1_700_000_000_000,
        trigger_kind="once_at",
        outcome="submitted",
        queue_job_ref="inbox-1700000000000-deadbeef.json",
        message="ok",
        payload_template={"goal": "x"},
    )
    target = write_history_record(queue_root, good)
    assert target.parent == history_dir(queue_root)
    assert target.name == "auto-demo-1700000000000-deadbeef.json"


def test_cli_run_due_once_outputs_table_and_emits(tmp_path: Path) -> None:
    """The minimal ``voxera automation run-due-once`` entrypoint works end-to-end."""
    from typer.testing import CliRunner

    from voxera.cli import app

    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    save_automation_definition(
        _make_once_at(id="cli-auto", trigger_config={"run_at_ms": 1_700_000_000_000}),
        queue_root,
        touch_updated=False,
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["automation", "run-due-once", "--queue-dir", str(queue_root)],
        color=False,
    )
    assert result.exit_code == 0
    assert "cli-auto" in result.stdout
    assert "submitted" in result.stdout
    assert len(_inbox_files(queue_root)) == 1


def test_cli_run_due_once_with_missing_id_exits_nonzero(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from voxera.cli import app

    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "automation",
            "run-due-once",
            "--queue-dir",
            str(queue_root),
            "--id",
            "does-not-exist",
        ],
        color=False,
    )
    assert result.exit_code == 1
    assert "automation not found" in result.stdout.lower()


def test_run_id_format_matches_automation_id_pattern() -> None:
    """Runner run_ids must pass the same id validation used for filenames."""
    from voxera.automation import AUTOMATION_ID_PATTERN, generate_run_id

    run_id = generate_run_id("demo-automation", now_ms=1_700_000_000_000)
    assert AUTOMATION_ID_PATTERN.match(run_id) is not None
    # Two different automation ids at the same ms still differ in the digest.
    other = generate_run_id("other-automation", now_ms=1_700_000_000_000)
    assert run_id != other
    assert AUTOMATION_ID_PATTERN.match(other) is not None
