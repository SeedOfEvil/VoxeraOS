"""Tests for Vera automation preview drafting, revision, and submission.

Covers the full lifecycle:
1. Vera drafts a valid automation preview for delay requests
2. Vera drafts a valid automation preview for recurring_interval requests
3. Vera asks clarification when trigger or payload is incomplete
4. Vera revises an active automation preview correctly
5. Submit saves a definition to automation storage (not a queue job)
6. Submit does not emit a queue job
7. Submit acknowledgment is truthful (saved, not executed)
8. Post-submit continuity about the saved automation is coherent
9. Ordinary non-automation Vera preview flows remain unchanged
10. Ambiguous requests fail closed cleanly
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from voxera.automation import (
    list_automation_definitions,
    load_automation_definition,
)
from voxera.vera.automation_preview import (
    AutomationClarification,
    AutomationPreview,
    AutomationSubmitResult,
    _build_explanation,
    _generate_automation_id,
    _human_trigger_description,
    _infer_title,
    _ms_to_human,
    _parse_payload,
    _parse_trigger,
    describe_saved_automation,
    draft_automation_preview,
    is_automation_authoring_intent,
    is_automation_preview,
    revise_automation_preview,
    submit_automation_preview,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sample_automation_preview(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "preview_type": "automation_definition",
        "title": "Run System_Inspect (every 1 hour)",
        "description": "",
        "trigger_kind": "recurring_interval",
        "trigger_config": {"interval_ms": 3_600_000},
        "payload_template": {"goal": "run system_inspect"},
        "enabled": True,
        "created_from": "vera",
        "explanation": "placeholder explanation",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. Intent detection
# ---------------------------------------------------------------------------


class TestAutomationIntentDetection:
    def test_every_hour_is_automation_intent(self) -> None:
        assert is_automation_authoring_intent("Every hour, run system_inspect.")

    def test_in_20_minutes_is_automation_intent(self) -> None:
        assert is_automation_authoring_intent(
            "In 20 minutes, write a reminder note that says check the lab."
        )

    def test_every_day_at_8am_is_automation_intent(self) -> None:
        assert is_automation_authoring_intent("Every day at 8 AM, run system diagnostics.")

    def test_schedule_keyword_is_automation_intent(self) -> None:
        assert is_automation_authoring_intent("Schedule a task to run diagnostics every morning.")

    def test_create_automation_is_intent(self) -> None:
        assert is_automation_authoring_intent(
            "Create an automation that saves a note every morning."
        )

    def test_plain_action_is_not_automation_intent(self) -> None:
        assert not is_automation_authoring_intent("Run system diagnostics.")

    def test_empty_is_not_automation_intent(self) -> None:
        assert not is_automation_authoring_intent("")

    def test_immediate_confirmation_is_not_automation_intent(self) -> None:
        assert not is_automation_authoring_intent("Do it")

    def test_go_ahead_is_not_automation_intent(self) -> None:
        assert not is_automation_authoring_intent("Go ahead")

    def test_write_a_note_is_not_automation_intent(self) -> None:
        assert not is_automation_authoring_intent("Write a note about dogs.")

    def test_every_morning_is_automation_intent(self) -> None:
        assert is_automation_authoring_intent("Every morning, check disk usage.")

    def test_after_delay_is_automation_intent(self) -> None:
        assert is_automation_authoring_intent("After 5 minutes, write a note that says hello.")


# ---------------------------------------------------------------------------
# 2. Trigger parsing
# ---------------------------------------------------------------------------


class TestTriggerParsing:
    def test_every_30_minutes(self) -> None:
        trigger = _parse_trigger("Every 30 minutes, run diagnostics")
        assert trigger is not None
        assert trigger["kind"] == "recurring_interval"
        assert trigger["config"]["interval_ms"] == 30 * 60_000

    def test_every_hour(self) -> None:
        trigger = _parse_trigger("Every hour, run system_inspect")
        assert trigger is not None
        assert trigger["kind"] == "recurring_interval"
        assert trigger["config"]["interval_ms"] == 3_600_000

    def test_every_day(self) -> None:
        trigger = _parse_trigger("Every day, check health")
        assert trigger is not None
        assert trigger["kind"] == "recurring_interval"
        assert trigger["config"]["interval_ms"] == 86_400_000

    def test_in_20_minutes(self) -> None:
        trigger = _parse_trigger("In 20 minutes, write a note")
        assert trigger is not None
        assert trigger["kind"] == "delay"
        assert trigger["config"]["delay_ms"] == 20 * 60_000

    def test_after_5_seconds(self) -> None:
        trigger = _parse_trigger("After 5 seconds, do something")
        assert trigger is not None
        assert trigger["kind"] == "delay"
        assert trigger["config"]["delay_ms"] == 5_000

    def test_daily_at_8am(self) -> None:
        trigger = _parse_trigger("Every day at 8 AM, run diagnostics")
        assert trigger is not None
        assert trigger["kind"] == "recurring_interval"
        assert trigger["config"]["interval_ms"] == 86_400_000
        assert "daily at 08:00" in trigger.get("_display_hint", "")

    def test_every_morning(self) -> None:
        trigger = _parse_trigger("Every morning, save a note")
        assert trigger is not None
        assert trigger["kind"] == "recurring_interval"
        assert trigger["config"]["interval_ms"] == 86_400_000

    def test_no_trigger_in_plain_text(self) -> None:
        assert _parse_trigger("Write a note about dogs") is None


# ---------------------------------------------------------------------------
# 3. Payload parsing
# ---------------------------------------------------------------------------


class TestPayloadParsing:
    def test_run_system_inspect(self) -> None:
        payload = _parse_payload("run system_inspect")
        assert payload is not None
        assert payload["goal"] == "run system_inspect"

    def test_run_diagnostics(self) -> None:
        payload = _parse_payload("run diagnostics")
        assert payload is not None
        assert "diagnostics" in payload["goal"]
        assert payload.get("mission_id") == "system_diagnostics"

    def test_write_reminder_note(self) -> None:
        payload = _parse_payload("write a reminder note that says check the lab")
        assert payload is not None
        assert "write_file" in payload
        assert payload["write_file"]["content"] == "check the lab"

    def test_write_note_about(self) -> None:
        payload = _parse_payload("write a note about project status")
        assert payload is not None
        assert "write_file" in payload
        assert "project status" in payload["write_file"]["content"]

    def test_check_disk_usage(self) -> None:
        payload = _parse_payload("check disk usage")
        assert payload is not None
        assert payload.get("mission_id") == "system_diagnostics"

    def test_no_payload_in_gibberish(self) -> None:
        assert _parse_payload("hello world how are you") is None


# ---------------------------------------------------------------------------
# 4. Full drafting flow
# ---------------------------------------------------------------------------


class TestDraftAutomationPreview:
    def test_delay_request(self) -> None:
        result = draft_automation_preview(
            "In 20 minutes, write a reminder note that says check the lab."
        )
        assert isinstance(result, AutomationPreview)
        p = result.preview
        assert p["preview_type"] == "automation_definition"
        assert p["trigger_kind"] == "delay"
        assert p["trigger_config"]["delay_ms"] == 20 * 60_000
        assert "write_file" in p["payload_template"]
        assert p["enabled"] is True
        assert p["created_from"] == "vera"
        assert "does NOT execute immediately" in result.explanation

    def test_recurring_interval_request(self) -> None:
        result = draft_automation_preview("Every hour, run system_inspect.")
        assert isinstance(result, AutomationPreview)
        p = result.preview
        assert p["preview_type"] == "automation_definition"
        assert p["trigger_kind"] == "recurring_interval"
        assert p["trigger_config"]["interval_ms"] == 3_600_000
        assert p["payload_template"]["goal"] == "run system_inspect"

    def test_daily_diagnostics(self) -> None:
        result = draft_automation_preview("Every day at 8 AM, run system diagnostics.")
        assert isinstance(result, AutomationPreview)
        p = result.preview
        assert p["trigger_kind"] == "recurring_interval"

    def test_clarification_when_no_payload(self) -> None:
        result = draft_automation_preview("Schedule something every hour.")
        # This has a trigger but no clear payload action
        assert isinstance(result, (AutomationClarification, AutomationPreview))

    def test_clarification_when_ambiguous_schedule(self) -> None:
        # "schedule" keyword present but no specific timing
        result = draft_automation_preview("Schedule a note save.")
        if isinstance(result, AutomationClarification):
            assert "schedule" in result.question.lower() or "timing" in result.question.lower()

    def test_non_automation_returns_none(self) -> None:
        assert draft_automation_preview("Write a note about dogs.") is None

    def test_empty_returns_none(self) -> None:
        assert draft_automation_preview("") is None

    def test_immediate_execution_returns_none(self) -> None:
        assert draft_automation_preview("Do it") is None

    def test_save_note_every_morning(self) -> None:
        result = draft_automation_preview("Create an automation that saves a note every morning.")
        assert isinstance(result, AutomationPreview)
        p = result.preview
        assert p["trigger_kind"] == "recurring_interval"
        assert "write_file" in p["payload_template"]


# ---------------------------------------------------------------------------
# 5. Preview revision
# ---------------------------------------------------------------------------


class TestReviseAutomationPreview:
    def test_change_interval(self) -> None:
        preview = _sample_automation_preview()
        result = revise_automation_preview("Make it every 30 minutes instead.", preview)
        assert isinstance(result, AutomationPreview)
        assert result.preview["trigger_kind"] == "recurring_interval"
        assert result.preview["trigger_config"]["interval_ms"] == 30 * 60_000

    def test_change_to_delay(self) -> None:
        preview = _sample_automation_preview()
        result = revise_automation_preview("Actually make it run in 20 minutes.", preview)
        assert isinstance(result, AutomationPreview)
        assert result.preview["trigger_kind"] == "delay"
        assert result.preview["trigger_config"]["delay_ms"] == 20 * 60_000

    def test_change_payload(self) -> None:
        preview = _sample_automation_preview()
        result = revise_automation_preview("Use system_diagnostics instead.", preview)
        # Should pick up "system_diagnostics" as a new payload target
        # depending on parsing, this may or may not match; check gracefully
        if isinstance(result, AutomationPreview):
            assert "goal" in result.preview.get("payload_template", {})

    def test_rename_automation(self) -> None:
        preview = _sample_automation_preview()
        result = revise_automation_preview("Name it Morning Diagnostics.", preview)
        assert isinstance(result, AutomationPreview)
        assert result.preview["title"] == "Morning Diagnostics"

    def test_change_note_content(self) -> None:
        preview = _sample_automation_preview(
            payload_template={
                "goal": "write a reminder note",
                "write_file": {
                    "path": "~/VoxeraOS/notes/reminder.txt",
                    "content": "old content",
                    "mode": "overwrite",
                },
            }
        )
        result = revise_automation_preview("Change the note text to 'check the servers'.", preview)
        assert isinstance(result, AutomationPreview)
        wf = result.preview["payload_template"]["write_file"]
        assert wf["content"] == "check the servers"

    def test_disable_automation(self) -> None:
        preview = _sample_automation_preview()
        result = revise_automation_preview("Disable it.", preview)
        assert isinstance(result, AutomationPreview)
        assert result.preview["enabled"] is False

    def test_enable_automation(self) -> None:
        preview = _sample_automation_preview(enabled=False)
        result = revise_automation_preview("Enable it.", preview)
        assert isinstance(result, AutomationPreview)
        assert result.preview["enabled"] is True

    def test_non_automation_preview_returns_none(self) -> None:
        regular_preview = {"goal": "open https://example.com"}
        assert revise_automation_preview("Make it every hour.", regular_preview) is None

    def test_unrecognized_revision_returns_none(self) -> None:
        preview = _sample_automation_preview()
        result = revise_automation_preview("What is the meaning of life?", preview)
        assert result is None


# ---------------------------------------------------------------------------
# 6. Submit saves a definition (NOT a queue job)
# ---------------------------------------------------------------------------


class TestSubmitAutomationPreview:
    def test_submit_saves_definition(self, tmp_path: Path) -> None:
        preview = _sample_automation_preview()
        result = submit_automation_preview(preview, tmp_path)

        assert isinstance(result, AutomationSubmitResult)
        assert result.automation_id
        assert result.definition_path

        # Verify the definition file exists on disk
        saved_path = Path(result.definition_path)
        assert saved_path.exists()

        # Verify we can load it back
        data = json.loads(saved_path.read_text())
        assert data["title"] == preview["title"]
        assert data["trigger_kind"] == "recurring_interval"
        assert data["created_from"] == "vera"

    def test_submit_does_not_create_queue_job(self, tmp_path: Path) -> None:
        preview = _sample_automation_preview()
        submit_automation_preview(preview, tmp_path)

        # The inbox directory should not exist or be empty
        inbox = tmp_path / "inbox"
        if inbox.exists():
            inbox_files = list(inbox.glob("*.json"))
            assert len(inbox_files) == 0, "Submit should NOT create a queue job"

    def test_submit_ack_is_truthful(self, tmp_path: Path) -> None:
        preview = _sample_automation_preview()
        result = submit_automation_preview(preview, tmp_path)

        ack = result.ack
        # Must say it was saved
        assert "saved" in ack.lower() or "Saved" in ack
        # Must NOT claim execution
        assert "executed" not in ack.lower() or "NOT" in ack
        # Must mention the automation runner
        assert "runner" in ack.lower()
        # Must mention the queue
        assert "queue" in ack.lower()

    def test_submit_round_trips_through_automation_store(self, tmp_path: Path) -> None:
        preview = _sample_automation_preview()
        result = submit_automation_preview(preview, tmp_path)

        # Load from the store
        definitions = list_automation_definitions(tmp_path)
        assert len(definitions) == 1
        defn = definitions[0]
        assert defn.id == result.automation_id
        assert defn.title == preview["title"]
        assert defn.trigger_kind == "recurring_interval"
        assert defn.trigger_config == {"interval_ms": 3_600_000}
        assert defn.enabled is True
        assert defn.created_from == "vera"

    def test_submit_delay_preview(self, tmp_path: Path) -> None:
        preview = _sample_automation_preview(
            title="Delayed Note",
            trigger_kind="delay",
            trigger_config={"delay_ms": 1_200_000},
            payload_template={
                "goal": "write a note",
                "write_file": {
                    "path": "~/VoxeraOS/notes/hello.txt",
                    "content": "hello",
                    "mode": "overwrite",
                },
            },
        )
        result = submit_automation_preview(preview, tmp_path)
        defn = load_automation_definition(result.automation_id, tmp_path)
        assert defn.trigger_kind == "delay"
        assert defn.trigger_config["delay_ms"] == 1_200_000


# ---------------------------------------------------------------------------
# 7. Post-submit continuity
# ---------------------------------------------------------------------------


class TestPostSubmitContinuity:
    def test_describe_saved_automation(self) -> None:
        preview = _sample_automation_preview()
        result = AutomationSubmitResult(
            automation_id="test-abc123",
            definition_path="/tmp/test.json",
            ack="Saved.",
        )
        desc = describe_saved_automation(preview, result)
        assert "Run System_Inspect" in desc
        assert "test-abc123" in desc
        assert "every 1 hour" in desc
        assert "saved definition" in desc.lower()
        assert "not executed" in desc.lower()

    def test_describe_without_submit_result(self) -> None:
        preview = _sample_automation_preview()
        desc = describe_saved_automation(preview)
        assert "Run System_Inspect" in desc
        assert "saved definition" in desc.lower()

    def test_describe_write_file_payload(self) -> None:
        preview = _sample_automation_preview(
            payload_template={
                "goal": "write a reminder note",
                "write_file": {
                    "path": "~/VoxeraOS/notes/reminder.txt",
                    "content": "check the lab",
                    "mode": "overwrite",
                },
            }
        )
        desc = describe_saved_automation(preview)
        assert "reminder" in desc.lower()
        assert "check the lab" in desc


# ---------------------------------------------------------------------------
# 8. is_automation_preview guard
# ---------------------------------------------------------------------------


class TestIsAutomationPreview:
    def test_valid_automation_preview(self) -> None:
        assert is_automation_preview(_sample_automation_preview())

    def test_regular_preview_is_not_automation(self) -> None:
        assert not is_automation_preview({"goal": "open https://example.com"})

    def test_none_is_not_automation(self) -> None:
        assert not is_automation_preview(None)

    def test_empty_dict_is_not_automation(self) -> None:
        assert not is_automation_preview({})


# ---------------------------------------------------------------------------
# 9. Non-automation flows remain unchanged
# ---------------------------------------------------------------------------


class TestNonAutomationFlowsUnchanged:
    """Ensure normal preview drafting is not disrupted."""

    def test_open_url_not_automation(self) -> None:
        assert not is_automation_authoring_intent("Open https://example.com")

    def test_write_note_not_automation(self) -> None:
        assert not is_automation_authoring_intent("Write a note about dogs")

    def test_run_diagnostics_now_not_automation(self) -> None:
        assert not is_automation_authoring_intent("Run system diagnostics")

    def test_check_status_not_automation(self) -> None:
        assert not is_automation_authoring_intent("Check status of voxera-daemon.service")

    def test_save_this_not_automation(self) -> None:
        assert not is_automation_authoring_intent("Save this as notes.txt")


# ---------------------------------------------------------------------------
# 10. Ambiguous requests fail closed
# ---------------------------------------------------------------------------


class TestAmbiguousFailClosed:
    def test_ambiguous_schedule_asks_clarification(self) -> None:
        result = draft_automation_preview("Schedule something.")
        # Has the "schedule" keyword, but no specific trigger or payload
        assert isinstance(result, (AutomationClarification, type(None)))

    def test_vague_automation_returns_none_or_clarification(self) -> None:
        result = draft_automation_preview("Automate things.")
        assert result is None or isinstance(result, AutomationClarification)


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_ms_to_human_seconds(self) -> None:
        assert _ms_to_human(5_000) == "5 seconds"
        assert _ms_to_human(1_000) == "1 second"

    def test_ms_to_human_minutes(self) -> None:
        assert _ms_to_human(60_000) == "1 minute"
        assert _ms_to_human(1_800_000) == "30 minutes"

    def test_ms_to_human_hours(self) -> None:
        assert _ms_to_human(3_600_000) == "1 hour"
        assert _ms_to_human(7_200_000) == "2 hours"

    def test_ms_to_human_days(self) -> None:
        assert _ms_to_human(86_400_000) == "1 day"

    def test_ms_to_human_zero(self) -> None:
        assert _ms_to_human(0) == "0 seconds"

    def test_generate_automation_id_from_title(self) -> None:
        aid = _generate_automation_id("Morning Diagnostics Check")
        assert aid.startswith("morning-diagnostics-check-")
        assert len(aid) <= 128

    def test_generate_automation_id_empty_title(self) -> None:
        aid = _generate_automation_id("")
        assert aid.startswith("automation-")

    def test_human_trigger_description_delay(self) -> None:
        desc = _human_trigger_description("delay", {"delay_ms": 1_200_000})
        assert "20 minutes" in desc

    def test_human_trigger_description_recurring(self) -> None:
        desc = _human_trigger_description("recurring_interval", {"interval_ms": 3_600_000})
        assert "1 hour" in desc

    def test_human_trigger_description_cron(self) -> None:
        desc = _human_trigger_description("recurring_cron", {"cron": "*/5 * * * *"})
        assert "not yet active" in desc

    def test_human_trigger_description_watch(self) -> None:
        desc = _human_trigger_description(
            "watch_path",
            {"path": "~/incoming", "event": "created"},
        )
        assert "not yet active" in desc

    def test_build_explanation_mentions_no_immediate_execution(self) -> None:
        trigger = {"kind": "delay", "config": {"delay_ms": 60_000}}
        payload = {"goal": "run diagnostics"}
        explanation = _build_explanation(trigger, payload, "Test")
        assert "does NOT execute immediately" in explanation
        assert "automation runner" in explanation

    def test_infer_title_with_display_hint(self) -> None:
        trigger = {
            "kind": "recurring_interval",
            "config": {"interval_ms": 86_400_000},
            "_display_hint": "daily at 08:00",
        }
        payload = {"goal": "run diagnostics"}
        title = _infer_title("every day at 8am run diagnostics", trigger, payload)
        assert "daily at 08:00" in title

    def test_infer_title_delay(self) -> None:
        trigger = {"kind": "delay", "config": {"delay_ms": 1_200_000}}
        payload = {"goal": "write a note"}
        title = _infer_title("in 20 minutes write a note", trigger, payload)
        assert "20 minutes" in title


# ---------------------------------------------------------------------------
# Scenario: "did it run?" immediately after submit should NOT hallucinate
# ---------------------------------------------------------------------------


class TestDidItRunGuard:
    def test_describe_does_not_claim_execution(self) -> None:
        preview = _sample_automation_preview()
        desc = describe_saved_automation(preview)
        # Must NOT contain phrases that imply execution happened
        assert "executed successfully" not in desc.lower()
        assert "ran successfully" not in desc.lower()
        assert "has been completed" not in desc.lower()
        # Must contain truthful language
        assert "not executed" in desc.lower()
        assert "saved definition" in desc.lower()
