"""Tests for the automation runner.

The runner consumes saved ``AutomationDefinition`` objects and, when they
are due, emits a normal canonical queue payload via the existing
``core/inbox.add_inbox_payload`` path. The runner supports ``once_at``,
``delay``, and ``recurring_interval`` trigger kinds. These tests cover:

- due ``once_at`` emits exactly one normal queue job
- due ``delay`` emits exactly one normal queue job
- ``recurring_interval`` fires, re-arms ``next_run_at_ms``, and fires again
- non-due definitions do not emit jobs
- disabled definitions do not emit jobs
- malformed definition files on disk are skipped safely
- unsupported trigger kinds (``recurring_cron``, ``watch_path``) are
  explicitly skipped and do not emit jobs
- history records are written on submit and include queue job linkage
- updated definition fields (``last_run_at_ms``, ``last_job_ref``,
  ``run_history_refs``, ``enabled``, ``next_run_at_ms``) are saved
- one-shot semantics prevent double-submit on repeated runner passes
- recurring semantics allow re-submit after interval elapses
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


# ---------------------------------------------------------------------------
# Canonical payload-family regression coverage (PR2 bug fix)
#
# An earlier version of ``add_inbox_payload`` hard-required a non-empty
# ``goal`` field on every submission, which meant the automation runner
# could only ever fire ``goal``-kind payloads. A valid automation
# definition with e.g. ``{"mission_id": "system_inspect"}`` was rejected
# at emit time with ``job payload requires a non-empty goal`` even though
# the queue execution layer itself accepts mission_id-only, steps-only,
# write_file-only, and file_organize-only payloads.
#
# These tests pin the fix: every canonical request anchor documented in
# ``core/queue_execution.py`` (``mission_id``, ``goal``, inline ``steps``,
# ``write_file``, ``file_organize``) must fire cleanly through the
# runner, emit exactly one canonical queue job, record history, and
# advance the one-shot definition state.
# ---------------------------------------------------------------------------


def _emit_and_assert_canonical(
    tmp_path: Path,
    *,
    automation_id: str,
    payload_template: dict[str, object],
    expected_request_kind: str,
) -> None:
    """Fire one due automation definition and assert canonical linkage.

    Shared body for the per-family regression tests. Each test
    constructs a once_at definition with the canonical shape under
    test, runs it through ``process_automation_definition``, and
    verifies that:

    - the outcome is ``submitted``
    - exactly one inbox file exists
    - the emitted payload's ``job_intent.source_lane`` is
      ``automation_runner`` (proving it went through the canonical
      inbox path and not a private file drop)
    - the emitted payload's ``job_intent.request_kind`` matches the
      shape the test asserts on (the queue's own request-kind
      detection classifies it correctly)
    - the saved definition is advanced with ``enabled=False``,
      ``last_run_at_ms``, ``last_job_ref``, and one history ref
    - the payload template is preserved verbatim on disk
    """
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    definition = _make_once_at(
        id=automation_id,
        trigger_config={"run_at_ms": 1_700_000_000_000},
        payload_template=payload_template,
    )
    save_automation_definition(definition, queue_root, touch_updated=False)

    result = process_automation_definition(definition, queue_root, now_ms=1_700_000_000_500)
    assert result.outcome == "submitted", result.message
    assert result.queue_job_ref is not None
    assert result.history_ref is not None

    inbox_files = _inbox_files(queue_root)
    assert len(inbox_files) == 1
    emitted = json.loads(inbox_files[0].read_text(encoding="utf-8"))
    assert isinstance(emitted.get("job_intent"), dict)
    assert emitted["job_intent"].get("source_lane") == AUTOMATION_SOURCE_LANE
    assert emitted["job_intent"].get("request_kind") == expected_request_kind

    # Each top-level key from the saved template must survive to the
    # inbox file verbatim — the inbox helper is allowed to add the
    # canonical ``id`` and ``job_intent`` but not rewrite template fields.
    for key, value in payload_template.items():
        assert emitted.get(key) == value, f"template field {key!r} drifted: {emitted.get(key)!r}"

    loaded = load_automation_definition(definition.id, queue_root)
    assert loaded.enabled is False
    assert loaded.last_run_at_ms == 1_700_000_000_500
    assert loaded.last_job_ref == result.queue_job_ref
    assert loaded.run_history_refs == [result.history_ref]
    assert loaded.payload_template == payload_template

    history_files = _history_files(queue_root)
    assert len(history_files) == 1
    record = json.loads(history_files[0].read_text(encoding="utf-8"))
    assert record["outcome"] == "submitted"
    assert record["queue_job_ref"] == result.queue_job_ref


def test_mission_id_payload_template_fires_through_runner(tmp_path: Path) -> None:
    """Regression: PR #301 bug — mission_id-only payloads were rejected.

    Exact shape from the task description. Must submit cleanly.
    """
    _emit_and_assert_canonical(
        tmp_path,
        automation_id="mission-id-auto",
        payload_template={"mission_id": "system_inspect"},
        expected_request_kind="mission_id",
    )


def test_goal_payload_template_still_fires_through_runner(tmp_path: Path) -> None:
    """The goal path must continue to work after the broadening fix."""
    _emit_and_assert_canonical(
        tmp_path,
        automation_id="goal-auto",
        payload_template={"goal": "collect a read-only diagnostic snapshot of the current system"},
        expected_request_kind="goal",
    )


def test_write_file_payload_template_fires_through_runner(tmp_path: Path) -> None:
    """Regression: write_file-only payloads were rejected before the fix.

    The queue execution layer accepts ``write_file`` at intake; the
    inbox helper must accept it too. Note that the queue's own
    ``detect_request_kind`` returns ``"unknown"`` for a bare write_file
    payload because write_file is not in the canonical request-kind
    set — that's a separate intentional shape in the queue object
    model. What matters for PR2 is that the runner can successfully
    submit the payload and the queue will pick it up; this test
    asserts the submission path, not the request-kind name.
    """
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    payload_template: dict[str, object] = {
        "write_file": {
            "path": "~/VoxeraOS/notes/automation-runner-test.txt",
            "content": "hello from runner",
            "mode": "overwrite",
        }
    }
    definition = _make_once_at(
        id="write-file-auto",
        trigger_config={"run_at_ms": 1_700_000_000_000},
        payload_template=payload_template,
    )
    save_automation_definition(definition, queue_root, touch_updated=False)

    result = process_automation_definition(definition, queue_root, now_ms=1_700_000_000_500)
    assert result.outcome == "submitted", result.message
    assert result.queue_job_ref is not None

    inbox_files = _inbox_files(queue_root)
    assert len(inbox_files) == 1
    emitted = json.loads(inbox_files[0].read_text(encoding="utf-8"))
    assert emitted["job_intent"]["source_lane"] == AUTOMATION_SOURCE_LANE
    assert emitted.get("write_file") == payload_template["write_file"]

    loaded = load_automation_definition(definition.id, queue_root)
    assert loaded.enabled is False
    assert loaded.last_run_at_ms == 1_700_000_000_500
    assert loaded.last_job_ref == result.queue_job_ref


def test_inline_steps_payload_template_fires_through_runner(tmp_path: Path) -> None:
    """Regression: inline-steps-only payloads were rejected before the fix."""
    _emit_and_assert_canonical(
        tmp_path,
        automation_id="inline-steps-auto",
        payload_template={
            "steps": [
                {"skill_id": "system.status", "args": {}},
                {"skill_id": "system.disk_usage", "args": {}},
            ]
        },
        expected_request_kind="inline_steps",
    )


def test_file_organize_payload_template_fires_through_runner(tmp_path: Path) -> None:
    """Regression: file_organize-only payloads were rejected before the fix."""
    _emit_and_assert_canonical(
        tmp_path,
        automation_id="file-organize-auto",
        payload_template={
            "file_organize": {
                "source_path": "~/VoxeraOS/notes/source.txt",
                "destination_dir": "~/VoxeraOS/notes/archive",
                "mode": "copy",
                "overwrite": False,
                "delete_original": False,
            }
        },
        expected_request_kind="file_organize",
    )


def test_mission_id_one_shot_does_not_double_submit(tmp_path: Path) -> None:
    """A mission_id-only automation must still honor one-shot semantics.

    This is a deeper regression check: the earlier bug blocked the
    first submit, so one-shot semantics for non-goal payloads had
    never been exercised end-to-end before PR #301. A second runner
    pass must skip with "already fired".
    """
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    definition = _make_once_at(
        id="mission-id-one-shot",
        trigger_config={"run_at_ms": 1_700_000_000_000},
        payload_template={"mission_id": "system_inspect"},
    )
    save_automation_definition(definition, queue_root, touch_updated=False)

    first = run_due_automations(queue_root, now_ms=1_700_000_000_500)
    assert [r.outcome for r in first] == ["submitted"]
    assert len(_inbox_files(queue_root)) == 1
    assert len(_history_files(queue_root)) == 1

    second = run_due_automations(queue_root, now_ms=1_700_000_000_900)
    assert [r.outcome for r in second] == ["skipped"]
    # Either reason is fine: after a successful submit the definition is
    # saved with both ``enabled=False`` *and* ``last_run_at_ms`` set, and
    # ``evaluate_due_automation`` checks ``enabled`` first, so the skipped
    # reason is "definition is disabled". The important invariant is that
    # a second pass produces no additional inbox job and no additional
    # history row — the double-submit guard holds.
    assert "disabled" in second[0].message or "already fired" in second[0].message
    assert len(_inbox_files(queue_root)) == 1
    assert len(_history_files(queue_root)) == 1


def test_add_inbox_payload_rejects_payload_with_no_canonical_anchor(tmp_path: Path) -> None:
    """``add_inbox_payload`` must still fail closed for junk payloads.

    Broadening the helper must not turn it into a write-anything-to-inbox
    backdoor. A payload with none of the canonical anchor fields has
    no canonical request kind, and the daemon would reject it
    downstream anyway, so we reject it here with a clear message.
    """
    from voxera.core.inbox import add_inbox_payload

    queue_root = tmp_path / "queue"

    junk_payloads = (
        {},
        {"title": "only a title"},
        {"notes": "hello"},
        {"mission_id": ""},  # present but empty
        {"goal": "   "},  # present but whitespace
        {"steps": []},  # empty list is not a valid anchor
        {"write_file": {"content": "no path"}},
        {"file_organize": {"mode": "copy"}},
    )
    for junk in junk_payloads:
        with pytest.raises(ValueError, match="canonical request anchor"):
            add_inbox_payload(queue_root, junk)


def test_add_inbox_payload_accepts_every_canonical_anchor(tmp_path: Path) -> None:
    """Per-family smoke at the inbox helper level.

    Each canonical anchor must produce an ``inbox-*.json`` file with
    the canonical ``id`` and ``job_intent`` enrichment applied. This
    locks in the helper-level contract independently of the runner
    tests above so a future refactor of the runner cannot accidentally
    mask a helper-layer regression.
    """
    from voxera.core.inbox import add_inbox_payload

    cases: tuple[tuple[str, dict[str, object]], ...] = (
        ("mission-id", {"mission_id": "system_inspect"}),
        ("goal", {"goal": "open the dashboard"}),
        ("steps", {"steps": [{"skill_id": "system.status", "args": {}}]}),
        (
            "write-file",
            {"write_file": {"path": "~/notes/x.txt", "content": "hi", "mode": "overwrite"}},
        ),
        (
            "file-organize",
            {
                "file_organize": {
                    "source_path": "~/notes/a.txt",
                    "destination_dir": "~/notes/archive",
                    "mode": "copy",
                    "overwrite": False,
                    "delete_original": False,
                }
            },
        ),
    )
    for label, payload in cases:
        queue_root = tmp_path / label
        created = add_inbox_payload(
            queue_root,
            payload,
            job_id=f"canonical-{label}",
            source_lane="automation_runner",
        )
        assert created.name == f"inbox-canonical-{label}.json"
        emitted = json.loads(created.read_text(encoding="utf-8"))
        assert emitted["id"] == f"canonical-{label}"
        assert isinstance(emitted.get("job_intent"), dict)
        assert emitted["job_intent"]["source_lane"] == "automation_runner"
        # Every top-level key from the input payload survives verbatim.
        for key, value in payload.items():
            assert emitted.get(key) == value


# ---------------------------------------------------------------------------
# recurring_interval trigger support
# ---------------------------------------------------------------------------


def _make_recurring(**overrides: object) -> AutomationDefinition:
    defaults: dict[str, object] = {
        "id": "recurring-automation",
        "title": "Recurring automation",
        "description": "fires on an interval",
        "trigger_kind": "recurring_interval",
        "trigger_config": {"interval_ms": 60_000},
        "payload_template": {"goal": "collect diagnostic snapshot"},
        "created_at_ms": 1_700_000_000_000,
        "updated_at_ms": 1_700_000_000_000,
        "created_from": "cli",
    }
    defaults.update(overrides)
    return AutomationDefinition(**defaults)  # type: ignore[arg-type]


def test_recurring_interval_not_due_before_first_interval() -> None:
    """A fresh recurring_interval definition is not due until created_at_ms + interval_ms."""
    definition = _make_recurring(
        created_at_ms=1_700_000_000_000,
        updated_at_ms=1_700_000_000_000,
        trigger_config={"interval_ms": 60_000},
    )
    # 59 seconds after creation — not yet due.
    due, reason = evaluate_due_automation(definition, now_ms=1_700_000_059_000)
    assert due is False
    assert "not yet due" in reason


def test_recurring_interval_due_at_created_plus_interval() -> None:
    """When next_run_at_ms is unset, due anchor is created_at_ms + interval_ms."""
    definition = _make_recurring(
        created_at_ms=1_700_000_000_000,
        updated_at_ms=1_700_000_000_000,
        trigger_config={"interval_ms": 60_000},
    )
    # Exactly at created_at_ms + interval_ms => due.
    due, reason = evaluate_due_automation(definition, now_ms=1_700_000_060_000)
    assert due is True
    assert "1700000060000" in reason


def test_recurring_interval_emits_one_queue_job(tmp_path: Path) -> None:
    """A due recurring_interval emits exactly one queue job via the inbox path."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    definition = _make_recurring()
    save_automation_definition(definition, queue_root, touch_updated=False)

    result = process_automation_definition(definition, queue_root, now_ms=1_700_000_060_000)
    assert result.outcome == "submitted"
    assert result.queue_job_ref is not None
    assert result.trigger_kind == "recurring_interval"

    inbox_files = _inbox_files(queue_root)
    assert len(inbox_files) == 1
    emitted = json.loads(inbox_files[0].read_text(encoding="utf-8"))
    assert emitted["goal"] == "collect diagnostic snapshot"
    assert emitted["job_intent"]["source_lane"] == AUTOMATION_SOURCE_LANE


def test_recurring_interval_state_after_successful_fire(tmp_path: Path) -> None:
    """After a successful fire:

    - enabled remains True
    - last_run_at_ms updates to the fire time
    - last_job_ref updates to the inbox filename
    - run_history_refs appends the new history ref
    - next_run_at_ms is re-armed to fired_at_ms + interval_ms
    """
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    definition = _make_recurring(trigger_config={"interval_ms": 60_000})
    save_automation_definition(definition, queue_root, touch_updated=False)

    fire_time = 1_700_000_060_000
    result = process_automation_definition(definition, queue_root, now_ms=fire_time)
    assert result.outcome == "submitted"

    loaded = load_automation_definition(definition.id, queue_root)
    assert loaded.enabled is True
    assert loaded.last_run_at_ms == fire_time
    assert loaded.last_job_ref == result.queue_job_ref
    assert len(loaded.run_history_refs) == 1
    assert loaded.run_history_refs[0] == result.history_ref
    assert loaded.next_run_at_ms == fire_time + 60_000


def test_recurring_interval_no_double_submit_before_next_interval(tmp_path: Path) -> None:
    """A runner pass before the next interval elapses must not emit a second job."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    definition = _make_recurring(trigger_config={"interval_ms": 60_000})
    save_automation_definition(definition, queue_root, touch_updated=False)

    # First fire at created + interval.
    first = run_due_automations(queue_root, now_ms=1_700_000_060_000)
    assert [r.outcome for r in first] == ["submitted"]
    assert len(_inbox_files(queue_root)) == 1

    # Second pass 30s later — next_run_at_ms is 1_700_000_120_000, so not due.
    second = run_due_automations(queue_root, now_ms=1_700_000_090_000)
    assert [r.outcome for r in second] == ["skipped"]
    assert len(_inbox_files(queue_root)) == 1  # no new inbox file
    assert len(_history_files(queue_root)) == 1  # no new history


def test_recurring_interval_fires_again_after_next_interval(tmp_path: Path) -> None:
    """After the next interval elapses, the recurring definition fires again."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    definition = _make_recurring(trigger_config={"interval_ms": 60_000})
    save_automation_definition(definition, queue_root, touch_updated=False)

    # First fire.
    first = run_due_automations(queue_root, now_ms=1_700_000_060_000)
    assert [r.outcome for r in first] == ["submitted"]
    assert len(_inbox_files(queue_root)) == 1
    assert len(_history_files(queue_root)) == 1

    # Second fire at next_run_at_ms (60_000 + 60_000 = 120_000).
    second = run_due_automations(queue_root, now_ms=1_700_000_120_000)
    assert [r.outcome for r in second] == ["submitted"]
    assert len(_inbox_files(queue_root)) == 2
    assert len(_history_files(queue_root)) == 2

    # Verify re-armed state.
    loaded = load_automation_definition(definition.id, queue_root)
    assert loaded.enabled is True
    assert loaded.last_run_at_ms == 1_700_000_120_000
    assert loaded.next_run_at_ms == 1_700_000_180_000
    assert len(loaded.run_history_refs) == 2


def test_recurring_interval_mission_id_payload(tmp_path: Path) -> None:
    """recurring_interval works with a mission_id payload (non-goal canonical anchor)."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    definition = _make_recurring(
        id="recurring-mission",
        payload_template={"mission_id": "system_inspect"},
        trigger_config={"interval_ms": 30_000},
    )
    save_automation_definition(definition, queue_root, touch_updated=False)

    result = process_automation_definition(definition, queue_root, now_ms=1_700_000_030_000)
    assert result.outcome == "submitted"
    assert result.queue_job_ref is not None

    inbox_files = _inbox_files(queue_root)
    assert len(inbox_files) == 1
    emitted = json.loads(inbox_files[0].read_text(encoding="utf-8"))
    assert emitted["mission_id"] == "system_inspect"
    assert emitted["job_intent"]["source_lane"] == AUTOMATION_SOURCE_LANE

    loaded = load_automation_definition(definition.id, queue_root)
    assert loaded.enabled is True
    assert loaded.next_run_at_ms == 1_700_000_060_000


def test_recurring_interval_emit_failure_preserves_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On submit failure, definition state is unchanged and next_run_at_ms is not advanced."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    definition = _make_recurring(trigger_config={"interval_ms": 60_000})
    save_automation_definition(definition, queue_root, touch_updated=False)

    from voxera.automation import runner as runner_module

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated inbox failure")

    monkeypatch.setattr(runner_module, "add_inbox_payload", _boom)

    result = process_automation_definition(definition, queue_root, now_ms=1_700_000_060_000)
    assert result.outcome == "error"
    assert result.queue_job_ref is None
    assert "simulated inbox failure" in result.message

    # Error history written.
    history_files = _history_files(queue_root)
    assert len(history_files) == 1
    record = json.loads(history_files[0].read_text(encoding="utf-8"))
    assert record["outcome"] == "error"

    # Definition on disk is unchanged — enabled, no last_run, no next_run advance.
    loaded = load_automation_definition(definition.id, queue_root)
    assert loaded.enabled is True
    assert loaded.last_run_at_ms is None
    assert loaded.last_job_ref is None
    assert loaded.run_history_refs == []
    assert loaded.next_run_at_ms is None


def test_once_at_and_delay_semantics_unchanged(tmp_path: Path) -> None:
    """Verify that once_at and delay still behave as one-shots after the recurring change."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)

    # once_at
    save_automation_definition(
        _make_once_at(
            id="once-at-check",
            trigger_config={"run_at_ms": 1_700_000_000_000},
        ),
        queue_root,
        touch_updated=False,
    )
    # delay
    save_automation_definition(
        _make_delay(
            id="delay-check",
            created_at_ms=1_700_000_000_000,
            updated_at_ms=1_700_000_000_000,
            trigger_config={"delay_ms": 30_000},
        ),
        queue_root,
        touch_updated=False,
    )

    results = run_due_automations(queue_root, now_ms=1_700_000_100_000)
    by_id = {r.automation_id: r for r in results}
    assert by_id["once-at-check"].outcome == "submitted"
    assert by_id["delay-check"].outcome == "submitted"

    # Both disabled after fire.
    for auto_id in ("once-at-check", "delay-check"):
        loaded = load_automation_definition(auto_id, queue_root)
        assert loaded.enabled is False
        assert loaded.next_run_at_ms is None
        assert loaded.last_run_at_ms == 1_700_000_100_000

    # Second pass: both skipped.
    second = run_due_automations(queue_root, now_ms=1_700_000_200_000)
    assert all(r.outcome == "skipped" for r in second)
    assert len(_inbox_files(queue_root)) == 2  # no new files


def test_recurring_interval_late_wakeup_emits_one_job_no_burst(tmp_path: Path) -> None:
    """If the runner wakes up long after the due time, emit one job only.

    The next interval is anchored on the actual fire time, not the
    missed anchor, so no catch-up burst can occur.
    """
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    definition = _make_recurring(trigger_config={"interval_ms": 60_000})
    save_automation_definition(definition, queue_root, touch_updated=False)

    # Due at 1_700_000_060_000 but runner wakes up 5 minutes late.
    late_time = 1_700_000_360_000
    results = run_due_automations(queue_root, now_ms=late_time)
    assert [r.outcome for r in results] == ["submitted"]
    assert len(_inbox_files(queue_root)) == 1  # exactly one, not five

    loaded = load_automation_definition(definition.id, queue_root)
    # next_run_at_ms anchored on actual fire time, not the old missed anchor.
    assert loaded.next_run_at_ms == late_time + 60_000
    assert loaded.last_run_at_ms == late_time

    # Immediate second pass: still one job — next isn't due yet.
    second = run_due_automations(queue_root, now_ms=late_time + 1000)
    assert [r.outcome for r in second] == ["skipped"]
    assert len(_inbox_files(queue_root)) == 1


def test_recurring_interval_with_preset_next_run_at_ms(tmp_path: Path) -> None:
    """When next_run_at_ms is already set, it is used as the due anchor."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    # Created at T=0, interval=60s, but next_run_at_ms explicitly set to T+30s.
    definition = _make_recurring(
        trigger_config={"interval_ms": 60_000},
        next_run_at_ms=1_700_000_030_000,
    )
    save_automation_definition(definition, queue_root, touch_updated=False)

    # Before preset anchor: not due.
    due, reason = evaluate_due_automation(definition, now_ms=1_700_000_029_000)
    assert due is False
    assert "not yet due" in reason

    # At preset anchor: due (ignoring created_at + interval = T+60s).
    due, reason = evaluate_due_automation(definition, now_ms=1_700_000_030_000)
    assert due is True
    assert "1700000030000" in reason

    # Actually fire and verify re-arm.
    result = process_automation_definition(definition, queue_root, now_ms=1_700_000_030_000)
    assert result.outcome == "submitted"
    loaded = load_automation_definition(definition.id, queue_root)
    assert loaded.next_run_at_ms == 1_700_000_090_000  # 30_000 + 60_000


def test_run_due_automations_mixed_with_recurring(tmp_path: Path) -> None:
    """Inventory pass handles recurring alongside one-shot and unsupported kinds."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)

    # Due once_at.
    save_automation_definition(
        _make_once_at(id="alpha", trigger_config={"run_at_ms": 1_700_000_000_000}),
        queue_root,
        touch_updated=False,
    )
    # Due recurring_interval.
    save_automation_definition(
        _make_recurring(
            id="bravo",
            trigger_config={"interval_ms": 30_000},
            created_at_ms=1_700_000_000_000,
            updated_at_ms=1_700_000_000_000,
        ),
        queue_root,
        touch_updated=False,
    )
    # Unsupported recurring_cron.
    save_automation_definition(
        _make_once_at(
            id="charlie",
            trigger_kind="recurring_cron",
            trigger_config={"cron": "*/5 * * * *"},
        ),
        queue_root,
        touch_updated=False,
    )

    results = run_due_automations(queue_root, now_ms=1_700_000_100_000)
    by_id = {r.automation_id: r for r in results}
    assert by_id["alpha"].outcome == "submitted"
    assert by_id["bravo"].outcome == "submitted"
    assert by_id["charlie"].outcome == "skipped"
    assert len(_inbox_files(queue_root)) == 2

    # once_at is disabled, recurring stays enabled.
    alpha = load_automation_definition("alpha", queue_root)
    assert alpha.enabled is False
    bravo = load_automation_definition("bravo", queue_root)
    assert bravo.enabled is True
    assert bravo.next_run_at_ms == 1_700_000_100_000 + 30_000


def test_disabled_recurring_interval_is_skipped(tmp_path: Path) -> None:
    """An explicitly disabled recurring_interval definition does not fire."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    definition = _make_recurring(enabled=False)
    save_automation_definition(definition, queue_root, touch_updated=False)

    result = process_automation_definition(definition, queue_root, now_ms=1_700_000_060_000)
    assert result.outcome == "skipped"
    assert "disabled" in result.message
    assert _inbox_files(queue_root) == []
    assert _history_files(queue_root) == []


def test_recurring_save_failure_after_emit_preserves_enabled_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Save failure after emit for recurring: enabled stays True, next_run_at_ms unchanged."""
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    definition = _make_recurring(trigger_config={"interval_ms": 60_000})
    save_automation_definition(definition, queue_root, touch_updated=False)

    from voxera.automation import runner as runner_module

    def _fail_save(*args: object, **kwargs: object) -> object:
        raise OSError("simulated disk full")

    monkeypatch.setattr(runner_module, "save_automation_definition", _fail_save)

    result = process_automation_definition(definition, queue_root, now_ms=1_700_000_060_000)
    assert result.outcome == "error"
    assert result.queue_job_ref is not None
    assert "was emitted but definition state save failed" in result.message

    # Queue job was emitted.
    assert len(_inbox_files(queue_root)) == 1

    # Two history records: submit + save-error.
    history_files = _history_files(queue_root)
    assert len(history_files) == 2

    # Re-enable real save to check stored definition.
    monkeypatch.undo()
    loaded = load_automation_definition(definition.id, queue_root)
    # Definition was never advanced — operator must reconcile.
    assert loaded.enabled is True
    assert loaded.last_run_at_ms is None
    assert loaded.last_job_ref is None
    assert loaded.run_history_refs == []
    assert loaded.next_run_at_ms is None  # not advanced
