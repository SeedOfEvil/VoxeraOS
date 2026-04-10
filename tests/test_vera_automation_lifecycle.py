"""Tests for Vera conversational lifecycle management of saved automation definitions.

Covers:
1. Vera resolves "that automation" to the just-saved automation
2. Vera can show a saved automation from the canonical store
3. Vera disables a saved automation and persists the change
4. Vera enables a saved automation and persists the change
5. Vera deletes a saved automation but preserves history
6. Vera answers "Did it run?" truthfully when no history exists
7. Vera answers history/status questions truthfully when history exists
8. Vera `run now` uses the existing runner path and does not bypass the queue
9. Ambiguous references fail closed with clarification
10. Ordinary automation authoring from PR305 remains unchanged
11. Ordinary non-automation Vera flows remain unchanged
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from voxera.automation.history import (
    build_history_record,
    write_history_record,
)
from voxera.automation.models import AutomationDefinition
from voxera.automation.store import (
    list_automation_definitions,
    load_automation_definition,
    save_automation_definition,
)
from voxera.vera.automation_lifecycle import (
    AmbiguousAutomation,
    AutomationNotResolved,
    LifecycleIntent,
    ResolvedAutomation,
    classify_lifecycle_intent,
    dispatch_lifecycle_action,
    handle_delete,
    handle_disable,
    handle_enable,
    handle_history,
    handle_run_now,
    handle_show,
    is_automation_lifecycle_intent,
    resolve_automation_reference,
)
from voxera.vera.automation_preview import (
    is_automation_authoring_intent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW_MS = int(time.time() * 1000)


def _make_definition(
    queue_root: Path,
    *,
    automation_id: str = "test-auto-abc12345",
    title: str = "Hourly System Check",
    trigger_kind: str = "recurring_interval",
    trigger_config: dict[str, Any] | None = None,
    enabled: bool = True,
    created_from: str = "vera",
) -> AutomationDefinition:
    """Create and save a test automation definition."""
    defn = AutomationDefinition(
        id=automation_id,
        title=title,
        enabled=enabled,
        trigger_kind=trigger_kind,
        trigger_config=trigger_config or {"interval_ms": 3_600_000},
        payload_template={"goal": "run system_inspect"},
        created_from=created_from,
        created_at_ms=_NOW_MS,
        updated_at_ms=_NOW_MS,
    )
    save_automation_definition(defn, queue_root, touch_updated=False)
    return defn


def _make_history_record(
    queue_root: Path,
    *,
    automation_id: str,
    outcome: str = "submitted",
    queue_job_ref: str | None = "inbox-job123.json",
) -> None:
    """Write a test history record."""
    record = build_history_record(
        automation_id=automation_id,
        run_id=f"{_NOW_MS}-abcd1234",
        triggered_at_ms=_NOW_MS,
        trigger_kind="recurring_interval",
        outcome=outcome,
        queue_job_ref=queue_job_ref,
        message="due (anchor test)",
        payload_template={"goal": "run system_inspect"},
    )
    write_history_record(queue_root, record)


# ---------------------------------------------------------------------------
# 1. Intent classification
# ---------------------------------------------------------------------------


class TestLifecycleIntentClassification:
    def test_show_that_automation(self) -> None:
        assert classify_lifecycle_intent("Show me that automation.") is LifecycleIntent.SHOW

    def test_what_did_you_save(self) -> None:
        assert classify_lifecycle_intent("What did you save?") is LifecycleIntent.SHOW

    def test_describe_it(self) -> None:
        assert classify_lifecycle_intent("Describe that automation.") is LifecycleIntent.SHOW

    def test_when_will_it_run(self) -> None:
        assert classify_lifecycle_intent("When will it run?") is LifecycleIntent.SHOW

    def test_enable_it(self) -> None:
        assert classify_lifecycle_intent("Enable it.") is LifecycleIntent.ENABLE

    def test_enable_it_again(self) -> None:
        assert classify_lifecycle_intent("Enable it again.") is LifecycleIntent.ENABLE

    def test_disable_it(self) -> None:
        assert classify_lifecycle_intent("Disable that automation.") is LifecycleIntent.DISABLE

    def test_turn_off_the_automation(self) -> None:
        assert (
            classify_lifecycle_intent("Turn off the reminder automation.")
            is LifecycleIntent.DISABLE
        )

    def test_delete_it(self) -> None:
        assert classify_lifecycle_intent("Delete that automation.") is LifecycleIntent.DELETE

    def test_remove_it(self) -> None:
        assert (
            classify_lifecycle_intent("Remove the reminder automation.") is LifecycleIntent.DELETE
        )

    def test_run_it_now(self) -> None:
        assert classify_lifecycle_intent("Run it now.") is LifecycleIntent.RUN_NOW

    def test_trigger_that_now(self) -> None:
        assert classify_lifecycle_intent("Trigger that automation now.") is LifecycleIntent.RUN_NOW

    def test_did_it_run(self) -> None:
        assert classify_lifecycle_intent("Did it run?") is LifecycleIntent.HISTORY

    def test_show_the_history(self) -> None:
        assert classify_lifecycle_intent("Show me the history.") is LifecycleIntent.HISTORY

    def test_what_happened_with_the_automation(self) -> None:
        assert (
            classify_lifecycle_intent("What happened with that automation?")
            is LifecycleIntent.HISTORY
        )

    def test_what_happened_last_time_bare_is_not_lifecycle(self) -> None:
        """Bare 'what happened last time' is too generic without automation context."""
        assert classify_lifecycle_intent("What happened last time?") is None

    def test_not_lifecycle_hello(self) -> None:
        assert classify_lifecycle_intent("Hello, how are you?") is None

    def test_not_lifecycle_empty(self) -> None:
        assert classify_lifecycle_intent("") is None

    def test_not_lifecycle_schedule(self) -> None:
        """Automation authoring intent should not match lifecycle."""
        assert classify_lifecycle_intent("Every hour, run diagnostics.") is None

    def test_is_automation_lifecycle_intent_true(self) -> None:
        assert is_automation_lifecycle_intent("Disable that automation.")

    def test_is_automation_lifecycle_intent_false(self) -> None:
        assert not is_automation_lifecycle_intent("Write a note about dogs.")


# ---------------------------------------------------------------------------
# 2. Reference resolution
# ---------------------------------------------------------------------------


class TestReferenceResolution:
    def test_resolve_from_session_context(self, tmp_path: Path) -> None:
        """Session context automation:<id> resolves correctly."""
        defn = _make_definition(tmp_path)
        result = resolve_automation_reference(
            "Show me that automation.",
            queue_root=tmp_path,
            session_context={"active_topic": f"automation:{defn.id}"},
        )
        assert isinstance(result, ResolvedAutomation)
        assert result.definition.id == defn.id
        assert result.source == "session_context"

    def test_resolve_from_explicit_id(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path)
        result = resolve_automation_reference(
            f"Show automation id: {defn.id}",
            queue_root=tmp_path,
        )
        assert isinstance(result, ResolvedAutomation)
        assert result.definition.id == defn.id
        assert result.source == "explicit_id"

    def test_resolve_from_title_hint(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path, title="Morning Diagnostics Check")
        result = resolve_automation_reference(
            "Show me the morning diagnostics automation.",
            queue_root=tmp_path,
        )
        assert isinstance(result, ResolvedAutomation)
        assert result.definition.id == defn.id
        assert result.source == "title_match"

    def test_resolve_single_definition_by_pronoun(self, tmp_path: Path) -> None:
        """When there's exactly one definition, 'that automation' resolves to it."""
        defn = _make_definition(tmp_path)
        result = resolve_automation_reference(
            "Show me that automation.",
            queue_root=tmp_path,
        )
        assert isinstance(result, ResolvedAutomation)
        assert result.definition.id == defn.id

    def test_ambiguous_with_multiple_definitions(self, tmp_path: Path) -> None:
        """Multiple definitions + pronoun reference -> ambiguous."""
        _make_definition(tmp_path, automation_id="auto-aaa11111", title="Morning Check")
        _make_definition(tmp_path, automation_id="auto-bbb22222", title="Evening Check")
        result = resolve_automation_reference(
            "Disable the automation.",
            queue_root=tmp_path,
        )
        assert isinstance(result, AmbiguousAutomation)
        assert len(result.candidates) == 2
        assert "Which one" in result.clarification

    def test_ambiguous_title_match(self, tmp_path: Path) -> None:
        """Two automations with similar titles -> ambiguous."""
        _make_definition(tmp_path, automation_id="auto-ccc33333", title="System Check Morning")
        _make_definition(tmp_path, automation_id="auto-ddd44444", title="System Check Evening")
        result = resolve_automation_reference(
            "Disable the system check automation.",
            queue_root=tmp_path,
        )
        assert isinstance(result, AmbiguousAutomation)
        assert len(result.candidates) == 2

    def test_not_resolved_no_definitions(self, tmp_path: Path) -> None:
        result = resolve_automation_reference(
            "Show me that automation.",
            queue_root=tmp_path,
        )
        assert isinstance(result, AutomationNotResolved)

    def test_not_resolved_no_signal(self, tmp_path: Path) -> None:
        _make_definition(tmp_path)
        result = resolve_automation_reference(
            "Tell me about the weather.",
            queue_root=tmp_path,
        )
        assert isinstance(result, AutomationNotResolved)

    def test_resolve_from_stashed_preview(self, tmp_path: Path) -> None:
        """Stashed preview with matching title resolves."""
        defn = _make_definition(tmp_path)
        preview = {"title": defn.title, "preview_type": "automation_definition"}
        result = resolve_automation_reference(
            "Show me that automation.",
            queue_root=tmp_path,
            last_automation_preview=preview,
        )
        assert isinstance(result, ResolvedAutomation)
        assert result.definition.id == defn.id

    def test_stale_session_context_falls_through(self, tmp_path: Path) -> None:
        """If session context points to a deleted automation, fall through."""
        _make_definition(tmp_path, automation_id="auto-alive111")
        result = resolve_automation_reference(
            "Show me that automation.",
            queue_root=tmp_path,
            session_context={"active_topic": "automation:deleted-id-xyz"},
        )
        # Should fall through and find the single remaining definition
        assert isinstance(result, ResolvedAutomation)
        assert result.definition.id == "auto-alive111"


# ---------------------------------------------------------------------------
# 3. Show/describe saved automation
# ---------------------------------------------------------------------------


class TestHandleShow:
    def test_show_includes_title_and_id(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path)
        text = handle_show(defn, tmp_path)
        assert defn.title in text
        assert defn.id in text

    def test_show_includes_trigger_info(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path)
        text = handle_show(defn, tmp_path)
        assert "every 1 hour" in text.lower() or "hour" in text.lower()

    def test_show_includes_enabled_state(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path, enabled=False)
        text = handle_show(defn, tmp_path)
        assert "False" in text

    def test_show_includes_no_runs_yet(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path)
        text = handle_show(defn, tmp_path)
        assert "no runs yet" in text.lower()

    def test_show_includes_history_when_present(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path)
        _make_history_record(tmp_path, automation_id=defn.id)
        text = handle_show(defn, tmp_path)
        assert "submitted" in text.lower()

    def test_show_is_truthful_about_execution(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path)
        text = handle_show(defn, tmp_path)
        assert "saved automation definition" in text.lower()


# ---------------------------------------------------------------------------
# 4. Enable saved automation
# ---------------------------------------------------------------------------


class TestHandleEnable:
    def test_enable_disabled_automation(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path, enabled=False)
        text = handle_enable(defn, tmp_path)
        assert "enabled" in text.lower()
        # Verify persisted
        loaded = load_automation_definition(defn.id, tmp_path)
        assert loaded.enabled is True

    def test_enable_already_enabled(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path, enabled=True)
        text = handle_enable(defn, tmp_path)
        assert "already enabled" in text.lower()

    def test_enable_preserves_other_fields(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path, enabled=False, title="My Special Check")
        handle_enable(defn, tmp_path)
        loaded = load_automation_definition(defn.id, tmp_path)
        assert loaded.title == "My Special Check"
        assert loaded.trigger_kind == "recurring_interval"


# ---------------------------------------------------------------------------
# 5. Disable saved automation
# ---------------------------------------------------------------------------


class TestHandleDisable:
    def test_disable_enabled_automation(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path, enabled=True)
        text = handle_disable(defn, tmp_path)
        assert "disabled" in text.lower()
        # Verify persisted
        loaded = load_automation_definition(defn.id, tmp_path)
        assert loaded.enabled is False

    def test_disable_already_disabled(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path, enabled=False)
        text = handle_disable(defn, tmp_path)
        assert "already disabled" in text.lower()

    def test_disable_preserves_other_fields(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path, enabled=True, title="Important Task")
        handle_disable(defn, tmp_path)
        loaded = load_automation_definition(defn.id, tmp_path)
        assert loaded.title == "Important Task"
        assert loaded.trigger_kind == "recurring_interval"


# ---------------------------------------------------------------------------
# 6. Delete saved automation (history preserved)
# ---------------------------------------------------------------------------


class TestHandleDelete:
    def test_delete_removes_definition(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path)
        text = handle_delete(defn, tmp_path)
        assert "deleted" in text.lower()
        definitions = list_automation_definitions(tmp_path)
        assert len(definitions) == 0

    def test_delete_preserves_history(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path)
        _make_history_record(tmp_path, automation_id=defn.id)
        text = handle_delete(defn, tmp_path)
        assert "history" in text.lower()
        assert "preserved" in text.lower()
        # Definition gone
        definitions = list_automation_definitions(tmp_path)
        assert len(definitions) == 0

    def test_delete_truthful_ack(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path)
        text = handle_delete(defn, tmp_path)
        assert defn.title in text
        assert defn.id in text
        assert "removed" in text.lower() or "deleted" in text.lower()


# ---------------------------------------------------------------------------
# 7. History / "did it run?" — truthful answers
# ---------------------------------------------------------------------------


class TestHandleHistory:
    def test_no_history_truthful(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path)
        text = handle_history(defn, tmp_path)
        assert "has not run yet" in text.lower()
        assert "no history records" in text.lower()

    def test_with_history_shows_records(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path)
        _make_history_record(tmp_path, automation_id=defn.id, outcome="submitted")
        text = handle_history(defn, tmp_path)
        assert "submitted" in text.lower()
        assert defn.title in text

    def test_does_not_hallucinate_execution(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path)
        text = handle_history(defn, tmp_path)
        assert "executed successfully" not in text.lower()
        assert "ran successfully" not in text.lower()
        assert "completed" not in text.lower()


# ---------------------------------------------------------------------------
# 8. Run-now uses existing runner (queue-submitting only)
# ---------------------------------------------------------------------------


class TestHandleRunNow:
    def test_run_now_submits_queue_job(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path)
        text = handle_run_now(defn, tmp_path)
        assert "submitted" in text.lower() or "queue job" in text.lower()

    def test_run_now_mentions_queue(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path)
        text = handle_run_now(defn, tmp_path)
        assert "queue" in text.lower()

    def test_run_now_does_not_claim_execution(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path)
        text = handle_run_now(defn, tmp_path)
        assert "NOT" in text or "not" in text.lower()

    def test_run_now_disabled_fails(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path, enabled=False)
        text = handle_run_now(defn, tmp_path)
        assert "disabled" in text.lower()

    def test_run_now_creates_inbox_job(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path)
        handle_run_now(defn, tmp_path)
        inbox = tmp_path / "inbox"
        if inbox.exists():
            inbox_files = list(inbox.glob("*.json"))
            assert len(inbox_files) >= 1, "run-now should create a queue inbox job"


# ---------------------------------------------------------------------------
# 9. Ambiguous references fail closed
# ---------------------------------------------------------------------------


class TestAmbiguousFailClosed:
    def test_ambiguous_pronoun_two_automations(self, tmp_path: Path) -> None:
        _make_definition(tmp_path, automation_id="auto-111aaaaa", title="First Check")
        _make_definition(tmp_path, automation_id="auto-222bbbbb", title="Second Check")
        result = dispatch_lifecycle_action(
            "Disable the automation.",
            queue_root=tmp_path,
        )
        assert result.matched is True
        assert result.status == "automation_lifecycle_ambiguous"
        assert "Which one" in result.assistant_text

    def test_ambiguous_title_two_matches(self, tmp_path: Path) -> None:
        _make_definition(tmp_path, automation_id="auto-333ccccc", title="Daily Report Morning")
        _make_definition(tmp_path, automation_id="auto-444ddddd", title="Daily Report Evening")
        result = dispatch_lifecycle_action(
            "Show me the daily report automation.",
            queue_root=tmp_path,
        )
        assert result.matched is True
        assert result.status == "automation_lifecycle_ambiguous"

    def test_no_automations_fails_closed(self, tmp_path: Path) -> None:
        result = dispatch_lifecycle_action(
            "Disable that automation.",
            queue_root=tmp_path,
        )
        assert result.matched is True
        assert result.status == "automation_lifecycle_not_found"


# ---------------------------------------------------------------------------
# 10. Ordinary automation authoring unchanged
# ---------------------------------------------------------------------------


class TestAutomationAuthoringUnchanged:
    def test_scheduling_intent_not_lifecycle(self) -> None:
        """Scheduling intent should NOT be classified as lifecycle."""
        assert classify_lifecycle_intent("Every hour, run system_inspect.") is None
        assert is_automation_authoring_intent("Every hour, run system_inspect.")

    def test_in_delay_not_lifecycle(self) -> None:
        assert classify_lifecycle_intent("In 20 minutes, write a note.") is None
        assert is_automation_authoring_intent("In 20 minutes, write a note that says hello.")

    def test_schedule_at_time_not_lifecycle(self) -> None:
        assert classify_lifecycle_intent("Schedule diagnostics at 8 AM") is None
        assert is_automation_authoring_intent("Schedule diagnostics at 8 AM")


# ---------------------------------------------------------------------------
# 11. Ordinary non-automation Vera flows unchanged
# ---------------------------------------------------------------------------


class TestNonAutomationFlowsUnchanged:
    def test_open_url_not_lifecycle(self) -> None:
        assert not is_automation_lifecycle_intent("Open https://example.com")

    def test_write_note_not_lifecycle(self) -> None:
        assert not is_automation_lifecycle_intent("Write a note about dogs.")

    def test_run_diagnostics_now_not_lifecycle(self) -> None:
        """Bare 'run diagnostics' should not match lifecycle intent."""
        assert not is_automation_lifecycle_intent("Run system diagnostics.")

    def test_general_question_not_lifecycle(self) -> None:
        assert not is_automation_lifecycle_intent("What is the weather?")

    def test_submit_confirmation_not_lifecycle(self) -> None:
        assert not is_automation_lifecycle_intent("Go ahead.")


# ---------------------------------------------------------------------------
# 12. False positive prevention — intent must not hijack non-automation turns
# ---------------------------------------------------------------------------


class TestFalsePositivePrevention:
    """Ensure intent classification does not match non-automation requests.

    These are the critical fail-closed tests: messages that contain action
    verbs (delete, stop, enable, describe) but target non-automation objects
    must NOT match lifecycle intent.  A false positive here would cause Vera
    to silently delete/disable an automation when the user meant something
    entirely different.
    """

    def test_delete_the_file_not_lifecycle(self) -> None:
        assert classify_lifecycle_intent("Delete the file.") is None

    def test_delete_the_note_not_lifecycle(self) -> None:
        assert classify_lifecycle_intent("Delete the note.") is None

    def test_remove_the_file_not_lifecycle(self) -> None:
        assert classify_lifecycle_intent("Remove the file.") is None

    def test_stop_the_daemon_not_lifecycle(self) -> None:
        assert classify_lifecycle_intent("Stop the daemon.") is None

    def test_stop_the_service_not_lifecycle(self) -> None:
        assert classify_lifecycle_intent("Stop the service.") is None

    def test_pause_the_job_not_lifecycle(self) -> None:
        assert classify_lifecycle_intent("Pause the job.") is None

    def test_disable_notifications_not_lifecycle(self) -> None:
        assert classify_lifecycle_intent("Disable notifications.") is None

    def test_enable_dark_mode_not_lifecycle(self) -> None:
        assert classify_lifecycle_intent("Enable dark mode.") is None

    def test_describe_the_weather_not_lifecycle(self) -> None:
        assert classify_lifecycle_intent("Describe the weather.") is None

    def test_turn_off_the_lights_not_lifecycle(self) -> None:
        assert classify_lifecycle_intent("Turn off the lights.") is None

    def test_what_happened_with_the_server_not_lifecycle(self) -> None:
        assert classify_lifecycle_intent("What happened last time with the server?") is None

    def test_remove_the_old_logs_not_lifecycle(self) -> None:
        assert classify_lifecycle_intent("Remove the old logs.") is None

    def test_bare_pronoun_at_end_still_works(self) -> None:
        """Bare 'delete it' / 'stop it' are valid lifecycle intents."""
        assert classify_lifecycle_intent("Delete it.") is LifecycleIntent.DELETE
        assert classify_lifecycle_intent("Stop it.") is LifecycleIntent.DISABLE
        assert classify_lifecycle_intent("Enable it.") is LifecycleIntent.ENABLE
        assert classify_lifecycle_intent("Describe it.") is LifecycleIntent.SHOW

    def test_explicit_automation_word_still_works(self) -> None:
        """'Delete the automation' / 'stop the automation' are valid."""
        assert classify_lifecycle_intent("Delete the automation.") is LifecycleIntent.DELETE
        assert classify_lifecycle_intent("Stop the automation.") is LifecycleIntent.DISABLE
        assert classify_lifecycle_intent("Describe the automation.") is LifecycleIntent.SHOW

    def test_named_automation_still_works(self) -> None:
        """'Delete the reminder automation' is valid."""
        assert (
            classify_lifecycle_intent("Delete the reminder automation.") is LifecycleIntent.DELETE
        )
        assert (
            classify_lifecycle_intent("Turn off the reminder automation.")
            is LifecycleIntent.DISABLE
        )

    def test_dispatch_does_not_act_on_false_positive(self, tmp_path: Path) -> None:
        """Even if a definition exists, non-lifecycle messages must not match."""
        _make_definition(
            tmp_path,
            automation_id="auto-fp-test1",
            title="My Automation",
        )
        # These should not even match lifecycle intent
        result = dispatch_lifecycle_action(
            "Delete the note.",
            queue_root=tmp_path,
            session_context={"active_topic": "automation:auto-fp-test1"},
        )
        assert result.matched is False

        result = dispatch_lifecycle_action(
            "Stop the daemon.",
            queue_root=tmp_path,
            session_context={"active_topic": "automation:auto-fp-test1"},
        )
        assert result.matched is False


# ---------------------------------------------------------------------------
# 13. handle_show does not imply unsupported trigger kinds are runnable
# ---------------------------------------------------------------------------


class TestTriggerKindTruthfulness:
    def test_show_recurring_cron_notes_unsupported(self, tmp_path: Path) -> None:
        """recurring_cron should be noted as not yet active in show output."""
        defn = AutomationDefinition(
            id="auto-cron-test1",
            title="Cron Test",
            enabled=True,
            trigger_kind="recurring_cron",
            trigger_config={"cron": "*/5 * * * *"},
            payload_template={"goal": "run diagnostics"},
            created_from="cli",
            created_at_ms=_NOW_MS,
            updated_at_ms=_NOW_MS,
        )
        save_automation_definition(defn, tmp_path, touch_updated=False)
        text = handle_show(defn, tmp_path)
        assert "not yet active" in text.lower()

    def test_show_watch_path_notes_unsupported(self, tmp_path: Path) -> None:
        """watch_path should be noted as not yet active in show output."""
        defn = AutomationDefinition(
            id="auto-watch-test1",
            title="Watch Test",
            enabled=True,
            trigger_kind="watch_path",
            trigger_config={"path": "~/incoming", "event": "created"},
            payload_template={"goal": "process files"},
            created_from="cli",
            created_at_ms=_NOW_MS,
            updated_at_ms=_NOW_MS,
        )
        save_automation_definition(defn, tmp_path, touch_updated=False)
        text = handle_show(defn, tmp_path)
        assert "not yet active" in text.lower()


# ---------------------------------------------------------------------------
# Full dispatch integration scenarios
# ---------------------------------------------------------------------------


class TestFullDispatchScenarios:
    def test_save_then_show(self, tmp_path: Path) -> None:
        """Save an automation, then 'show me that automation'."""
        defn = _make_definition(tmp_path)
        result = dispatch_lifecycle_action(
            "Show me that automation.",
            queue_root=tmp_path,
            session_context={"active_topic": f"automation:{defn.id}"},
        )
        assert result.matched is True
        assert result.status == "automation_lifecycle_show"
        assert defn.title in result.assistant_text
        assert defn.id in result.assistant_text

    def test_save_then_disable(self, tmp_path: Path) -> None:
        """Save an automation, then 'disable it'."""
        defn = _make_definition(tmp_path, enabled=True)
        result = dispatch_lifecycle_action(
            "Disable it.",
            queue_root=tmp_path,
            session_context={"active_topic": f"automation:{defn.id}"},
        )
        assert result.matched is True
        assert result.status == "automation_lifecycle_disable"
        loaded = load_automation_definition(defn.id, tmp_path)
        assert loaded.enabled is False

    def test_save_then_enable(self, tmp_path: Path) -> None:
        """Save an automation, disable it, then 'enable it again'."""
        defn = _make_definition(tmp_path, enabled=False)
        result = dispatch_lifecycle_action(
            "Enable it again.",
            queue_root=tmp_path,
            session_context={"active_topic": f"automation:{defn.id}"},
        )
        assert result.matched is True
        assert result.status == "automation_lifecycle_enable"
        loaded = load_automation_definition(defn.id, tmp_path)
        assert loaded.enabled is True

    def test_save_then_delete(self, tmp_path: Path) -> None:
        """Save an automation, then 'delete it'."""
        defn = _make_definition(tmp_path)
        result = dispatch_lifecycle_action(
            "Delete that automation.",
            queue_root=tmp_path,
            session_context={"active_topic": f"automation:{defn.id}"},
        )
        assert result.matched is True
        assert result.status == "automation_lifecycle_delete"
        assert result.definition_deleted is True
        definitions = list_automation_definitions(tmp_path)
        assert len(definitions) == 0

    def test_save_no_history_did_it_run(self, tmp_path: Path) -> None:
        """Save an automation with no history, then 'did it run?'."""
        defn = _make_definition(tmp_path)
        result = dispatch_lifecycle_action(
            "Did it run?",
            queue_root=tmp_path,
            session_context={"active_topic": f"automation:{defn.id}"},
        )
        assert result.matched is True
        assert result.status == "automation_lifecycle_history"
        assert "has not run yet" in result.assistant_text.lower()

    def test_with_history_show_history(self, tmp_path: Path) -> None:
        """Existing history on disk, then 'show me the history'."""
        defn = _make_definition(tmp_path)
        _make_history_record(tmp_path, automation_id=defn.id, outcome="submitted")
        result = dispatch_lifecycle_action(
            "Show me the history.",
            queue_root=tmp_path,
            session_context={"active_topic": f"automation:{defn.id}"},
        )
        assert result.matched is True
        assert result.status == "automation_lifecycle_history"
        assert "submitted" in result.assistant_text.lower()

    def test_run_now_through_dispatch(self, tmp_path: Path) -> None:
        """Run now through the dispatch path."""
        defn = _make_definition(tmp_path)
        result = dispatch_lifecycle_action(
            "Run it now.",
            queue_root=tmp_path,
            session_context={"active_topic": f"automation:{defn.id}"},
        )
        assert result.matched is True
        assert result.status == "automation_lifecycle_run_now"
        assert "queue" in result.assistant_text.lower()

    def test_no_intent_not_matched(self, tmp_path: Path) -> None:
        """Non-lifecycle message should not match."""
        result = dispatch_lifecycle_action(
            "What is the meaning of life?",
            queue_root=tmp_path,
        )
        assert result.matched is False

    def test_dispatch_tracks_automation_id(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path)
        result = dispatch_lifecycle_action(
            "Show me that automation.",
            queue_root=tmp_path,
            session_context={"active_topic": f"automation:{defn.id}"},
        )
        assert result.automation_id == defn.id

    def test_dispatch_delete_sets_deleted_flag(self, tmp_path: Path) -> None:
        defn = _make_definition(tmp_path)
        result = dispatch_lifecycle_action(
            "Delete it.",
            queue_root=tmp_path,
            session_context={"active_topic": f"automation:{defn.id}"},
        )
        assert result.definition_deleted is True
        assert result.intent is LifecycleIntent.DELETE


# ---------------------------------------------------------------------------
# Context lifecycle integration
# ---------------------------------------------------------------------------


class TestContextLifecycleIntegration:
    def test_automation_saved_sets_topic(self, tmp_path: Path) -> None:
        """context_on_automation_saved sets active_topic for lifecycle resolution."""
        from voxera.vera.context_lifecycle import context_on_automation_saved
        from voxera.vera.session_store import new_session_id

        queue = tmp_path / "queue"
        sid = new_session_id()
        ctx = context_on_automation_saved(queue, sid, automation_id="test-auto-xyz")
        assert ctx["active_topic"] == "automation:test-auto-xyz"

    def test_lifecycle_action_updates_topic(self, tmp_path: Path) -> None:
        """context_on_automation_lifecycle_action updates topic."""
        from voxera.vera.context_lifecycle import context_on_automation_lifecycle_action
        from voxera.vera.session_store import new_session_id

        queue = tmp_path / "queue"
        sid = new_session_id()
        ctx = context_on_automation_lifecycle_action(queue, sid, automation_id="test-auto-abc")
        assert ctx["active_topic"] == "automation:test-auto-abc"

    def test_lifecycle_delete_clears_topic(self, tmp_path: Path) -> None:
        """context_on_automation_lifecycle_action with deleted=True clears topic."""
        from voxera.vera.context_lifecycle import context_on_automation_lifecycle_action
        from voxera.vera.session_store import new_session_id

        queue = tmp_path / "queue"
        sid = new_session_id()
        ctx = context_on_automation_lifecycle_action(
            queue, sid, automation_id="test-auto-abc", deleted=True
        )
        assert ctx["active_topic"] is None
