"""Tests for the automation object model and file-backed storage.

PR1 intentionally covers only the durable definition layer. These tests
exercise validation, round-trip save/load, listing, deletion, updated_at
behavior, and best-effort tolerance for malformed files on disk.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from voxera.automation import (
    AUTOMATION_CANONICAL_REQUEST_FIELDS,
    AUTOMATION_ID_PATTERN,
    AUTOMATION_TRIGGER_KINDS,
    AutomationDefinition,
    AutomationNotFoundError,
    AutomationStoreError,
    automations_root,
    definition_path,
    definitions_dir,
    delete_automation_definition,
    ensure_automation_dirs,
    history_dir,
    list_automation_definitions,
    load_automation_definition,
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
        "created_from": "vera",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Model validation
# ---------------------------------------------------------------------------


def test_valid_definition_defaults_enabled_and_empty_history() -> None:
    definition = AutomationDefinition(**_valid_kwargs())  # type: ignore[arg-type]

    assert definition.enabled is True
    assert definition.run_history_refs == []
    assert definition.policy_posture == "standard"
    assert definition.created_from == "vera"
    assert definition.created_at_ms > 0
    assert definition.updated_at_ms >= definition.created_at_ms
    assert definition.last_run_at_ms is None
    assert definition.next_run_at_ms is None


def test_all_supported_trigger_kinds_parse() -> None:
    cases = {
        "once_at": {"run_at_ms": 1_700_000_000_000},
        "delay": {"delay_ms": 60_000},
        "recurring_interval": {"interval_ms": 15_000},
        "recurring_cron": {"cron": "*/5 * * * *"},
        "watch_path": {"path": "~/VoxeraOS/notes/incoming", "event": "created"},
    }
    assert set(cases.keys()) == AUTOMATION_TRIGGER_KINDS
    for kind, config in cases.items():
        definition = AutomationDefinition(
            **_valid_kwargs(trigger_kind=kind, trigger_config=config)  # type: ignore[arg-type]
        )
        assert definition.trigger_kind == kind
        assert definition.trigger_config == config


def test_unknown_trigger_kind_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AutomationDefinition(
            **_valid_kwargs(trigger_kind="not_a_kind", trigger_config={})  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("kind", "bad_config"),
    [
        ("once_at", {}),
        ("once_at", {"run_at_ms": -1}),
        ("once_at", {"run_at_ms": "2026-04-08"}),
        ("once_at", {"run_at_ms": 1_700_000_000_000, "extra": 1}),
        ("delay", {}),
        ("delay", {"delay_ms": 0}),
        ("recurring_interval", {"interval_ms": 0}),
        ("recurring_cron", {"cron": "  "}),
        ("recurring_cron", {}),
        ("watch_path", {"path": ""}),
        ("watch_path", {"path": "~/x", "event": "exploded"}),
        ("watch_path", {"path": "~/x", "event": "created", "extra": True}),
    ],
)
def test_invalid_trigger_config_is_rejected(kind: str, bad_config: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AutomationDefinition(
            **_valid_kwargs(trigger_kind=kind, trigger_config=bad_config)  # type: ignore[arg-type]
        )


def test_payload_template_must_be_non_empty() -> None:
    with pytest.raises(ValidationError):
        AutomationDefinition(**_valid_kwargs(payload_template={}))  # type: ignore[arg-type]


def test_payload_template_must_carry_canonical_request_field() -> None:
    with pytest.raises(ValidationError):
        AutomationDefinition(
            **_valid_kwargs(payload_template={"title": "no anchor field"})  # type: ignore[arg-type]
        )


def test_payload_template_canonical_fields_match_queue_contract() -> None:
    # Guardrail: if someone expands the canonical queue payload, this set
    # should be reviewed explicitly rather than silently drifting.
    expected = frozenset({"mission_id", "goal", "steps", "file_organize", "write_file"})
    assert expected == AUTOMATION_CANONICAL_REQUEST_FIELDS


def test_write_file_payload_template_delegates_to_queue_contract() -> None:
    # A malformed write_file template should be rejected via the same
    # extractor the queue daemon uses at intake.
    with pytest.raises(ValidationError):
        AutomationDefinition(
            **_valid_kwargs(
                payload_template={"write_file": {"path": "", "content": "hi"}},
            )  # type: ignore[arg-type]
        )


def test_file_organize_payload_template_delegates_to_queue_contract() -> None:
    with pytest.raises(ValidationError):
        AutomationDefinition(
            **_valid_kwargs(
                payload_template={"file_organize": {"source_path": "", "destination_dir": ""}},
            )  # type: ignore[arg-type]
        )


def test_steps_payload_template_requires_non_empty_list() -> None:
    with pytest.raises(ValidationError):
        AutomationDefinition(
            **_valid_kwargs(payload_template={"steps": []})  # type: ignore[arg-type]
        )


def test_goal_payload_template_requires_non_empty_string() -> None:
    with pytest.raises(ValidationError):
        AutomationDefinition(
            **_valid_kwargs(payload_template={"goal": "   "})  # type: ignore[arg-type]
        )


def test_bad_id_is_rejected() -> None:
    for bad in ("bad id", "with/slash", "..", "", "x" * 200):
        with pytest.raises(ValidationError):
            AutomationDefinition(**_valid_kwargs(id=bad))  # type: ignore[arg-type]


def test_disabled_definition_round_trips(tmp_path: Path) -> None:
    definition = AutomationDefinition(**_valid_kwargs(enabled=False))  # type: ignore[arg-type]
    assert definition.enabled is False
    save_automation_definition(definition, tmp_path)
    loaded = load_automation_definition(definition.id, tmp_path)
    assert loaded.enabled is False


def test_updated_at_must_not_be_before_created_at() -> None:
    with pytest.raises(ValidationError):
        AutomationDefinition(
            **_valid_kwargs(
                created_at_ms=1_700_000_001_000,
                updated_at_ms=1_700_000_000_000,
            )  # type: ignore[arg-type]
        )


def test_touch_updated_advances_timestamp_and_returns_copy() -> None:
    definition = AutomationDefinition(
        **_valid_kwargs(
            created_at_ms=1_700_000_000_000,
            updated_at_ms=1_700_000_000_000,
        )  # type: ignore[arg-type]
    )
    later = definition.touch_updated(now_ms=1_700_000_010_000)
    assert later.updated_at_ms == 1_700_000_010_000
    # Original reference is untouched.
    assert definition.updated_at_ms == 1_700_000_000_000
    # A "now" that is older than created_at is clamped forward, not accepted.
    clamped = definition.touch_updated(now_ms=1_600_000_000_000)
    assert clamped.updated_at_ms == definition.created_at_ms


def test_touch_updated_never_regresses_past_current_updated_at() -> None:
    # Clock skew must not pull updated_at_ms backward: an automation that was
    # last updated at t=1500 should still report at least 1500 even if a
    # later touch says "now is 1200".
    definition = AutomationDefinition(
        **_valid_kwargs(
            created_at_ms=1_000,
            updated_at_ms=1_500,
        )  # type: ignore[arg-type]
    )
    refreshed = definition.touch_updated(now_ms=1_200)
    assert refreshed.updated_at_ms == 1_500


def test_touch_updated_rejects_non_positive_now_ms() -> None:
    definition = AutomationDefinition(**_valid_kwargs())  # type: ignore[arg-type]
    for bad in (0, -1, True, False, "1700000000000", 1.5):
        with pytest.raises(ValueError):
            definition.touch_updated(now_ms=bad)  # type: ignore[arg-type]


def test_description_is_stripped() -> None:
    definition = AutomationDefinition(
        **_valid_kwargs(description="   trimmed body  \n")  # type: ignore[arg-type]
    )
    assert definition.description == "trimmed body"


def test_last_job_ref_round_trip_and_empty_string_rejected() -> None:
    definition = AutomationDefinition(
        **_valid_kwargs(last_job_ref="inbox-goal-42")  # type: ignore[arg-type]
    )
    assert definition.last_job_ref == "inbox-goal-42"
    with pytest.raises(ValidationError):
        AutomationDefinition(**_valid_kwargs(last_job_ref="   "))  # type: ignore[arg-type]


def test_run_history_refs_round_trip_and_invalid_items_rejected() -> None:
    definition = AutomationDefinition(
        **_valid_kwargs(run_history_refs=["one", "two"])  # type: ignore[arg-type]
    )
    assert definition.run_history_refs == ["one", "two"]
    with pytest.raises(ValidationError):
        AutomationDefinition(**_valid_kwargs(run_history_refs=["ok", ""]))  # type: ignore[arg-type]


def test_trigger_config_numeric_fields_reject_bool_and_float() -> None:
    # Pydantic treats ``True`` as an int (value 1) unless we reject it
    # explicitly. A bool trigger_config value is almost certainly a bug, so
    # fail closed.
    with pytest.raises(ValidationError):
        AutomationDefinition(
            **_valid_kwargs(
                trigger_kind="delay",
                trigger_config={"delay_ms": True},
            )  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError):
        AutomationDefinition(
            **_valid_kwargs(
                trigger_kind="once_at",
                trigger_config={"run_at_ms": 1_700_000_000_000.0},
            )  # type: ignore[arg-type]
        )


def test_watch_path_defaults_event_to_created_when_omitted() -> None:
    definition = AutomationDefinition(
        **_valid_kwargs(
            trigger_kind="watch_path",
            trigger_config={"path": "~/VoxeraOS/notes/incoming"},
        )  # type: ignore[arg-type]
    )
    assert definition.trigger_config == {
        "path": "~/VoxeraOS/notes/incoming",
        "event": "created",
    }


def test_automation_id_pattern_accepts_valid_ids_and_rejects_others() -> None:
    for good in ("demo-1", "A", "mission_abc", "job-123", "x" * 128):
        assert AUTOMATION_ID_PATTERN.match(good) is not None
    for bad in (
        "",
        " ",
        "-leading-hyphen",
        "_leading-underscore",
        "x" * 129,
        "with space",
        "with/slash",
        "..",
        ".hidden",
        "with\nnewline",
        "with\x00null",
    ):
        assert AUTOMATION_ID_PATTERN.match(bad) is None


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def test_ensure_automation_dirs_creates_definitions_and_history(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    root = ensure_automation_dirs(queue_root)
    assert root.exists()
    assert definitions_dir(queue_root).exists()
    assert history_dir(queue_root).exists()


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    definition = AutomationDefinition(**_valid_kwargs())  # type: ignore[arg-type]
    target = save_automation_definition(definition, queue_root)
    assert target == definition_path(queue_root, definition.id)
    assert target.exists()

    loaded = load_automation_definition(definition.id, queue_root)
    assert loaded.id == definition.id
    assert loaded.title == definition.title
    assert loaded.trigger_kind == definition.trigger_kind
    assert loaded.trigger_config == definition.trigger_config
    assert loaded.payload_template == definition.payload_template
    assert loaded.created_from == definition.created_from


def test_save_refreshes_updated_at_by_default(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    definition = AutomationDefinition(
        **_valid_kwargs(
            created_at_ms=1_700_000_000_000,
            updated_at_ms=1_700_000_000_000,
        )  # type: ignore[arg-type]
    )
    save_automation_definition(definition, queue_root, now_ms=1_800_000_000_000)
    loaded = load_automation_definition(definition.id, queue_root)
    assert loaded.updated_at_ms == 1_800_000_000_000
    assert loaded.created_at_ms == 1_700_000_000_000


def test_save_can_preserve_updated_at_for_imports(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    definition = AutomationDefinition(
        **_valid_kwargs(
            created_at_ms=1_700_000_000_000,
            updated_at_ms=1_700_000_500_000,
        )  # type: ignore[arg-type]
    )
    save_automation_definition(definition, queue_root, touch_updated=False)
    loaded = load_automation_definition(definition.id, queue_root)
    assert loaded.updated_at_ms == 1_700_000_500_000


def test_list_returns_all_valid_definitions_sorted(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    ids = ["alpha", "bravo", "charlie"]
    for automation_id in ids:
        save_automation_definition(
            AutomationDefinition(**_valid_kwargs(id=automation_id)),  # type: ignore[arg-type]
            queue_root,
        )

    listing = list_automation_definitions(queue_root)
    assert [item.id for item in listing] == sorted(ids)


def test_list_skips_malformed_files_in_best_effort_mode(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    save_automation_definition(
        AutomationDefinition(**_valid_kwargs(id="good-1")),  # type: ignore[arg-type]
        queue_root,
    )
    # Write garbage directly into the definitions directory.
    bad_path = definitions_dir(queue_root) / "broken.json"
    bad_path.write_text("{this is not json", encoding="utf-8")
    bad_schema = definitions_dir(queue_root) / "wrong-shape.json"
    bad_schema.write_text(json.dumps({"nope": "not an automation"}), encoding="utf-8")

    listing = list_automation_definitions(queue_root)
    assert [item.id for item in listing] == ["good-1"]


def test_list_strict_mode_raises_on_malformed_file(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    save_automation_definition(
        AutomationDefinition(**_valid_kwargs(id="good-2")),  # type: ignore[arg-type]
        queue_root,
    )
    bad_path = definitions_dir(queue_root) / "broken.json"
    bad_path.write_text("{this is not json", encoding="utf-8")

    with pytest.raises(AutomationStoreError):
        list_automation_definitions(queue_root, strict=True)


def test_load_missing_definition_raises_not_found(tmp_path: Path) -> None:
    with pytest.raises(AutomationNotFoundError):
        load_automation_definition("nope", tmp_path / "queue")


def test_load_invalid_file_raises_store_error(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    target = definitions_dir(queue_root) / "bad.json"
    target.write_text(json.dumps({"id": "bad", "not": "an automation"}), encoding="utf-8")

    with pytest.raises(AutomationStoreError):
        load_automation_definition("bad", queue_root)


def test_delete_removes_file_and_then_raises(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    save_automation_definition(
        AutomationDefinition(**_valid_kwargs(id="delme")),  # type: ignore[arg-type]
        queue_root,
    )
    assert delete_automation_definition("delme", queue_root) is True
    assert not definition_path(queue_root, "delme").exists()

    with pytest.raises(AutomationNotFoundError):
        delete_automation_definition("delme", queue_root)

    assert delete_automation_definition("delme", queue_root, missing_ok=True) is False


def test_definition_path_rejects_traversal_like_ids(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    for bad in (
        "../escape",
        "..",
        "./relative",
        "with/slash",
        "",
        ".hidden",
        "with\x00null",
        "with space",
        "-leading-hyphen",
        "_leading-underscore",
    ):
        with pytest.raises(AutomationStoreError):
            definition_path(queue_root, bad)


def test_definition_path_accepts_valid_ids(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    target = definition_path(queue_root, "demo-automation")
    assert target == definitions_dir(queue_root) / "demo-automation.json"
    # Leading/trailing whitespace is accepted but stripped, so the on-disk
    # filename matches the validated model id.
    assert (
        definition_path(queue_root, "  demo-automation  ")
        == definitions_dir(queue_root) / "demo-automation.json"
    )


def test_automations_root_is_queue_root_scoped(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    root = automations_root(queue_root)
    assert root == queue_root / "automations"
    assert definitions_dir(queue_root) == root / "definitions"
    assert history_dir(queue_root) == root / "history"


def test_save_overwrites_existing_definition_idempotently(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    first = AutomationDefinition(
        **_valid_kwargs(
            id="idem-1",
            title="first title",
            created_at_ms=1_700_000_000_000,
            updated_at_ms=1_700_000_000_000,
        )  # type: ignore[arg-type]
    )
    save_automation_definition(first, queue_root, touch_updated=False)
    # Same id, different content.
    second = AutomationDefinition(
        **_valid_kwargs(
            id="idem-1",
            title="second title",
            created_at_ms=1_700_000_000_000,
            updated_at_ms=1_800_000_000_000,
        )  # type: ignore[arg-type]
    )
    save_automation_definition(second, queue_root, touch_updated=False)

    loaded = load_automation_definition("idem-1", queue_root)
    assert loaded.title == "second title"
    assert loaded.updated_at_ms == 1_800_000_000_000
    # Only one file should exist for this id.
    matches = sorted(definitions_dir(queue_root).glob("idem-1*"))
    assert [p.name for p in matches] == ["idem-1.json"]


def test_list_ignores_tmp_sibling_left_behind_by_interrupted_save(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    save_automation_definition(
        AutomationDefinition(**_valid_kwargs(id="good-3")),  # type: ignore[arg-type]
        queue_root,
    )
    # Simulate an interrupted save that left a ``.json.tmp`` sidecar.
    leftover = definitions_dir(queue_root) / "mid-write.json.tmp"
    leftover.write_text("{not json", encoding="utf-8")

    listing = list_automation_definitions(queue_root)
    assert [item.id for item in listing] == ["good-3"]
    # Strict mode also ignores the .tmp file (glob is *.json, not *.json.tmp).
    assert [item.id for item in list_automation_definitions(queue_root, strict=True)] == ["good-3"]


def test_save_refreshes_updated_at_to_wall_clock_when_now_ms_omitted(
    tmp_path: Path,
) -> None:
    queue_root = tmp_path / "queue"
    # Deliberately old created_at/updated_at so the wall-clock refresh is
    # guaranteed to move the timestamp forward.
    definition = AutomationDefinition(
        **_valid_kwargs(
            created_at_ms=1_000,
            updated_at_ms=1_000,
        )  # type: ignore[arg-type]
    )
    save_automation_definition(definition, queue_root)
    loaded = load_automation_definition(definition.id, queue_root)
    assert loaded.updated_at_ms > 1_000
    assert loaded.created_at_ms == 1_000


def test_load_raises_on_id_shape_before_touching_filesystem(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    ensure_automation_dirs(queue_root)
    with pytest.raises(AutomationStoreError):
        load_automation_definition("../escape", queue_root)
    with pytest.raises(AutomationStoreError):
        load_automation_definition("with/slash", queue_root)
    with pytest.raises(AutomationStoreError):
        load_automation_definition("", queue_root)


def test_saved_file_is_deterministic_json(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    definition = AutomationDefinition(
        **_valid_kwargs(
            created_at_ms=1_700_000_000_000,
            updated_at_ms=1_700_000_000_000,
        )  # type: ignore[arg-type]
    )
    target = save_automation_definition(definition, queue_root, touch_updated=False)
    content = target.read_text(encoding="utf-8")
    parsed = json.loads(content)
    # Keys are sorted for deterministic output so diffs stay minimal.
    assert list(parsed.keys()) == sorted(parsed.keys())
    # Canonical fields survive the round trip verbatim.
    assert parsed["id"] == definition.id
    assert parsed["trigger_kind"] == "once_at"
    assert parsed["payload_template"] == {"goal": "open the dashboard"}
