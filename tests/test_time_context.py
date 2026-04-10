"""Tests for Vera time-context helpers.

Covers:
1. current_time_context returns sane structured context
2. format_elapsed for recent timestamps
3. format_time_until for future timestamps
4. classify_relative_day (today/yesterday/tomorrow)
5. Automation timing descriptions use human-readable phrasing
6. Time question detection and answers
7. No fabricated execution history when timestamps are absent
8. Prompt/instruction surfaces reflect time-aware capability
9. Time context block for prompt injection
10. Operator assistant system prompt includes time context
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from voxera.vera.time_context import (
    TimeContext,
    answer_time_question,
    classify_relative_day,
    current_time_context,
    current_time_summary,
    describe_last_run_ms,
    describe_next_run_ms,
    describe_timestamp_ms,
    format_elapsed,
    format_elapsed_since_ms,
    format_time_until,
    format_time_until_ms,
    is_time_question,
    time_context_block,
)

# ---------------------------------------------------------------------------
# 1. current_time_context returns sane structured context
# ---------------------------------------------------------------------------


class TestCurrentTimeContext:
    def test_returns_time_context_dataclass(self) -> None:
        ctx = current_time_context()
        assert isinstance(ctx, TimeContext)

    def test_fields_are_populated(self) -> None:
        ctx = current_time_context()
        assert ctx.local_iso  # non-empty string
        assert ctx.utc_iso
        assert ctx.timezone_name
        assert ctx.utc_offset.startswith("UTC")
        assert ctx.epoch_ms > 0
        assert ctx.day_of_week in {
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        }
        assert ctx.date_human  # non-empty

    def test_deterministic_with_fixed_now(self) -> None:
        fixed = datetime(2025, 6, 15, 14, 32, 7, tzinfo=timezone.utc)
        ctx = current_time_context(now=fixed)
        assert ctx.local_iso == "2025-06-15 14:32:07"
        assert ctx.utc_iso == "2025-06-15 14:32:07"
        assert ctx.day_of_week == "Sunday"
        assert "June 15, 2025" in ctx.date_human

    def test_epoch_ms_is_reasonable(self) -> None:
        ctx = current_time_context()
        now_ms = int(time.time() * 1000)
        # Within 5 seconds of now
        assert abs(ctx.epoch_ms - now_ms) < 5000


# ---------------------------------------------------------------------------
# 2. Elapsed time formatting
# ---------------------------------------------------------------------------


class TestFormatElapsed:
    def test_just_now(self) -> None:
        assert format_elapsed(500) == "just now"

    def test_seconds(self) -> None:
        assert format_elapsed(3_000) == "3 seconds ago"

    def test_one_second(self) -> None:
        assert format_elapsed(1_000) == "1 second ago"

    def test_minutes(self) -> None:
        assert format_elapsed(150_000) == "about 2 minutes ago"

    def test_one_minute(self) -> None:
        assert format_elapsed(60_000) == "about 1 minute ago"

    def test_hours(self) -> None:
        assert format_elapsed(7_200_000) == "about 2 hours ago"

    def test_one_hour(self) -> None:
        assert format_elapsed(3_600_000) == "about 1 hour ago"

    def test_days(self) -> None:
        assert format_elapsed(172_800_000) == "about 2 days ago"

    def test_one_day(self) -> None:
        assert format_elapsed(86_400_000) == "about 1 day ago"

    def test_negative_is_future(self) -> None:
        assert format_elapsed(-1000) == "in the future"

    def test_zero_is_just_now(self) -> None:
        assert format_elapsed(0) == "just now"


class TestFormatElapsedSinceMs:
    def test_recent_past(self) -> None:
        now_ms = 1_700_000_000_000
        past_ms = now_ms - 120_000  # 2 minutes ago
        result = format_elapsed_since_ms(past_ms, now_ms=now_ms)
        assert "2 minute" in result

    def test_uses_system_clock_when_no_now(self) -> None:
        past_ms = int(time.time() * 1000) - 5_000
        result = format_elapsed_since_ms(past_ms)
        assert "second" in result or "just now" in result


# ---------------------------------------------------------------------------
# 3. Time-until formatting
# ---------------------------------------------------------------------------


class TestFormatTimeUntil:
    def test_any_moment(self) -> None:
        assert format_time_until(500) == "any moment now"

    def test_seconds(self) -> None:
        assert format_time_until(3_000) == "in about 3 seconds"

    def test_one_second(self) -> None:
        assert format_time_until(1_000) == "in about 1 second"

    def test_minutes(self) -> None:
        assert format_time_until(840_000) == "in about 14 minutes"

    def test_hours(self) -> None:
        assert format_time_until(7_200_000) == "in about 2 hours"

    def test_days(self) -> None:
        assert format_time_until(172_800_000) == "in about 2 days"

    def test_already_past(self) -> None:
        assert format_time_until(-1000) == "already past"

    def test_zero_is_any_moment(self) -> None:
        assert format_time_until(0) == "any moment now"


class TestFormatTimeUntilMs:
    def test_future_timestamp(self) -> None:
        now_ms = 1_700_000_000_000
        future_ms = now_ms + 840_000  # 14 minutes from now
        result = format_time_until_ms(future_ms, now_ms=now_ms)
        assert "14 minute" in result


# ---------------------------------------------------------------------------
# 4. Relative-day classification
# ---------------------------------------------------------------------------


class TestClassifyRelativeDay:
    def test_today(self) -> None:
        now = datetime(2025, 6, 15, 14, 0, 0, tzinfo=timezone.utc)
        epoch_ms = int(datetime(2025, 6, 15, 10, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        assert classify_relative_day(epoch_ms, now=now) == "today"

    def test_yesterday(self) -> None:
        now = datetime(2025, 6, 15, 14, 0, 0, tzinfo=timezone.utc)
        epoch_ms = int(datetime(2025, 6, 14, 10, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        assert classify_relative_day(epoch_ms, now=now) == "yesterday"

    def test_tomorrow(self) -> None:
        now = datetime(2025, 6, 15, 14, 0, 0, tzinfo=timezone.utc)
        epoch_ms = int(datetime(2025, 6, 16, 10, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        assert classify_relative_day(epoch_ms, now=now) == "tomorrow"

    def test_older_date(self) -> None:
        now = datetime(2025, 6, 15, 14, 0, 0, tzinfo=timezone.utc)
        epoch_ms = int(datetime(2025, 6, 10, 10, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        result = classify_relative_day(epoch_ms, now=now)
        assert "June 10, 2025" in result


# ---------------------------------------------------------------------------
# 5. Automation timing descriptions
# ---------------------------------------------------------------------------


class TestDescribeTimestampMs:
    def test_last_run_description(self) -> None:
        now = datetime(2025, 6, 15, 15, 0, 0, tzinfo=timezone.utc)
        now_ms = int(now.timestamp() * 1000)
        last_run_ms = now_ms - 2_820_000  # 47 minutes ago
        result = describe_last_run_ms(last_run_ms, now_ms=now_ms, now=now)
        assert "Last run:" in result
        assert "47 minute" in result
        assert "today" in result

    def test_next_run_description(self) -> None:
        now = datetime(2025, 6, 15, 15, 0, 0, tzinfo=timezone.utc)
        now_ms = int(now.timestamp() * 1000)
        next_run_ms = now_ms + 840_000  # 14 minutes from now
        result = describe_next_run_ms(next_run_ms, now_ms=now_ms, now=now)
        assert "Next run:" in result
        assert "14 minute" in result

    def test_describe_with_custom_label(self) -> None:
        now = datetime(2025, 6, 15, 15, 0, 0, tzinfo=timezone.utc)
        now_ms = int(now.timestamp() * 1000)
        ts = now_ms - 3_600_000  # 1 hour ago
        result = describe_timestamp_ms(ts, label="triggered", now_ms=now_ms, now=now)
        assert "triggered:" in result
        assert "1 hour" in result


# ---------------------------------------------------------------------------
# 6. Time question detection and answers
# ---------------------------------------------------------------------------


class TestTimeQuestionDetection:
    def test_what_time_is_it(self) -> None:
        assert is_time_question("What time is it?") is True

    def test_whats_the_time(self) -> None:
        assert is_time_question("What's the time?") is True

    def test_what_day_is_it(self) -> None:
        assert is_time_question("What day is it?") is True

    def test_what_time_on_this_box(self) -> None:
        assert is_time_question("What time is it on this box?") is True

    def test_current_time(self) -> None:
        assert is_time_question("current time") is True

    def test_what_timezone(self) -> None:
        assert is_time_question("What timezone?") is True

    def test_not_a_time_question(self) -> None:
        assert is_time_question("How do I create a file?") is False

    def test_empty_string(self) -> None:
        assert is_time_question("") is False

    def test_every_30_minutes_is_not_time_question(self) -> None:
        assert is_time_question("Run diagnostics every 30 minutes") is False

    def test_how_long_ago_is_not_bare_time_question(self) -> None:
        # "How long ago did that run?" is not a simple time question;
        # it's a lifecycle question handled by automation lifecycle.
        assert is_time_question("How long ago did that run?") is False


class TestAnswerTimeQuestion:
    def test_answers_time_question(self) -> None:
        answer = answer_time_question("What time is it?")
        assert answer is not None
        assert "It's" in answer

    def test_returns_none_for_non_time(self) -> None:
        assert answer_time_question("How do I create a file?") is None

    def test_answer_includes_timezone(self) -> None:
        answer = answer_time_question("What time is it?")
        assert answer is not None
        assert "UTC" in answer


# ---------------------------------------------------------------------------
# 7. No fabricated history when timestamps absent
# ---------------------------------------------------------------------------


class TestTruthfulnessWithAbsentTimestamps:
    def test_handle_show_no_run_history(self, tmp_path: Path) -> None:
        """When an automation has never run, the show output says so."""
        from voxera.automation.models import AutomationDefinition
        from voxera.automation.store import save_automation_definition
        from voxera.vera.automation_lifecycle import handle_show

        defn = AutomationDefinition(
            id="test-no-runs-12345678",
            title="Test No Runs",
            enabled=True,
            trigger_kind="recurring_interval",
            trigger_config={"interval_ms": 3_600_000},
            payload_template={"goal": "test"},
            created_from="vera",
        )
        save_automation_definition(defn, tmp_path, touch_updated=False)
        result = handle_show(defn, tmp_path)
        assert "no runs yet" in result.lower()
        # Should NOT contain fabricated timestamps
        assert "Last run:" not in result

    def test_handle_history_no_records(self, tmp_path: Path) -> None:
        """When no history records exist, says so truthfully."""
        from voxera.automation.models import AutomationDefinition
        from voxera.automation.store import save_automation_definition
        from voxera.vera.automation_lifecycle import handle_history

        defn = AutomationDefinition(
            id="test-no-history-12345",
            title="Test No History",
            enabled=True,
            trigger_kind="recurring_interval",
            trigger_config={"interval_ms": 3_600_000},
            payload_template={"goal": "test"},
            created_from="vera",
        )
        save_automation_definition(defn, tmp_path, touch_updated=False)
        result = handle_history(defn, tmp_path)
        assert "has not run yet" in result.lower()


# ---------------------------------------------------------------------------
# 8. Automation lifecycle uses time-aware descriptions
# ---------------------------------------------------------------------------


class TestAutomationLifecycleTimeAware:
    def test_handle_show_with_last_run_uses_relative_time(self, tmp_path: Path) -> None:
        """handle_show with last_run_at_ms shows relative phrasing."""
        from voxera.automation.models import AutomationDefinition
        from voxera.automation.store import save_automation_definition
        from voxera.vera.automation_lifecycle import handle_show

        now_ms = int(time.time() * 1000)
        last_run_ms = now_ms - 2_820_000  # ~47 minutes ago
        defn = AutomationDefinition(
            id="test-timing-abcdef12",
            title="Timed Test",
            enabled=True,
            trigger_kind="recurring_interval",
            trigger_config={"interval_ms": 3_600_000},
            payload_template={"goal": "test"},
            created_from="vera",
            last_run_at_ms=last_run_ms,
        )
        save_automation_definition(defn, tmp_path, touch_updated=False)
        result = handle_show(defn, tmp_path)
        assert "Last run:" in result
        # Should contain relative phrasing
        assert "ago" in result

    def test_handle_show_with_next_run_uses_relative_time(self, tmp_path: Path) -> None:
        """handle_show with next_run_at_ms shows relative phrasing."""
        from voxera.automation.models import AutomationDefinition
        from voxera.automation.store import save_automation_definition
        from voxera.vera.automation_lifecycle import handle_show

        now_ms = int(time.time() * 1000)
        next_run_ms = now_ms + 840_000  # ~14 minutes from now
        defn = AutomationDefinition(
            id="test-nextrun-abcdef1",
            title="Next Run Test",
            enabled=True,
            trigger_kind="recurring_interval",
            trigger_config={"interval_ms": 3_600_000},
            payload_template={"goal": "test"},
            created_from="vera",
            next_run_at_ms=next_run_ms,
        )
        save_automation_definition(defn, tmp_path, touch_updated=False)
        result = handle_show(defn, tmp_path)
        assert "Next run:" in result
        assert "in about" in result

    def test_handle_history_uses_relative_time(self, tmp_path: Path) -> None:
        """handle_history timestamps use relative phrasing."""
        from voxera.automation.history import (
            build_history_record,
            write_history_record,
        )
        from voxera.automation.models import AutomationDefinition
        from voxera.automation.store import save_automation_definition
        from voxera.vera.automation_lifecycle import handle_history

        now_ms = int(time.time() * 1000)
        defn = AutomationDefinition(
            id="test-hist-time-abcde",
            title="History Timing Test",
            enabled=True,
            trigger_kind="recurring_interval",
            trigger_config={"interval_ms": 3_600_000},
            payload_template={"goal": "test"},
            created_from="vera",
        )
        save_automation_definition(defn, tmp_path, touch_updated=False)
        record = build_history_record(
            automation_id=defn.id,
            run_id=f"{now_ms}-abcd1234",
            triggered_at_ms=now_ms - 300_000,  # 5 minutes ago
            trigger_kind="recurring_interval",
            outcome="submitted",
            queue_job_ref="inbox-test.json",
            message="due (test)",
            payload_template={"goal": "test"},
        )
        write_history_record(tmp_path, record)
        result = handle_history(defn, tmp_path)
        assert "Triggered:" in result
        # Should show relative phrasing
        assert "ago" in result or "today" in result


# ---------------------------------------------------------------------------
# 9. Prompt/instruction surfaces reflect time-aware capability
# ---------------------------------------------------------------------------


class TestPromptSurfacesReflectTimeAwareness:
    def test_vera_role_doc_mentions_time_aware_reasoning(self) -> None:
        doc_path = Path("docs/prompts/roles/vera.md")
        content = doc_path.read_text(encoding="utf-8")
        assert "Time-Aware Reasoning" in content

    def test_output_quality_defaults_mentions_time_aware(self) -> None:
        doc_path = Path("docs/prompts/capabilities/output-quality-defaults.md")
        content = doc_path.read_text(encoding="utf-8")
        assert "Time-Aware Responses" in content

    def test_system_overview_mentions_time_aware_context(self) -> None:
        doc_path = Path("docs/prompts/00-system-overview.md")
        content = doc_path.read_text(encoding="utf-8")
        assert "Time-Aware Context" in content

    def test_runtime_overview_mentions_time_context_module(self) -> None:
        doc_path = Path("docs/prompts/03-runtime-technical-overview.md")
        content = doc_path.read_text(encoding="utf-8")
        assert "time_context.py" in content

    def test_vera_system_prompt_includes_time_context_block(self) -> None:
        """The Vera system prompt (composed at build time) includes
        the time-aware reasoning section from the role doc."""
        from voxera.vera.prompt import VERA_SYSTEM_PROMPT

        assert "Time-Aware Reasoning" in VERA_SYSTEM_PROMPT

    def test_build_vera_messages_includes_current_time(self) -> None:
        """build_vera_messages injects a time-context block into system content."""
        from voxera.vera.service import build_vera_messages

        messages = build_vera_messages(
            turns=[],
            user_message="hello",
        )
        system_msg = messages[0]
        assert system_msg["role"] == "system"
        assert "Current time:" in system_msg["content"]
        assert "Current UTC:" in system_msg["content"]

    def test_operator_assistant_includes_time_context(self) -> None:
        """The operator assistant system prompt includes time context."""
        from voxera.operator_assistant import build_assistant_messages

        messages = build_assistant_messages(
            "What's happening?",
            {"queue_counts": {}},
        )
        system_msg = messages[0]
        assert "Current time:" in system_msg["content"]
        assert "timing question" in system_msg["content"].lower()


# ---------------------------------------------------------------------------
# 10. Time context block for prompt injection
# ---------------------------------------------------------------------------


class TestTimeContextBlock:
    def test_block_includes_required_fields(self) -> None:
        block = time_context_block()
        assert "Current time:" in block
        assert "Current UTC:" in block
        assert "Day:" in block
        assert "Date:" in block
        assert "Epoch (ms):" in block

    def test_block_deterministic_with_fixed_now(self) -> None:
        fixed = datetime(2025, 6, 15, 14, 32, 7, tzinfo=timezone.utc)
        block = time_context_block(now=fixed)
        assert "2025-06-15 14:32:07" in block
        assert "Sunday" in block


# ---------------------------------------------------------------------------
# 11. Current time summary
# ---------------------------------------------------------------------------


class TestCurrentTimeSummary:
    def test_summary_includes_date_and_timezone(self) -> None:
        summary = current_time_summary()
        assert "It's" in summary
        assert "UTC" in summary

    def test_summary_deterministic_with_fixed_now(self) -> None:
        fixed = datetime(2025, 6, 15, 14, 32, 7, tzinfo=timezone.utc)
        summary = current_time_summary(now=fixed)
        assert "Sunday, June 15, 2025" in summary
        assert "UTC" in summary


# ---------------------------------------------------------------------------
# 12. Early exit dispatch handles time questions
# ---------------------------------------------------------------------------


class TestTimeQuestionEarlyExit:
    def test_time_question_dispatched_early(self, tmp_path: Path) -> None:
        """Time questions are handled by early exit dispatch."""
        from voxera.vera_web.chat_early_exit_dispatch import dispatch_early_exit_intent

        result = dispatch_early_exit_intent(
            message="What time is it?",
            diagnostics_service_turn=False,
            requested_job_id=None,
            should_attempt_derived_save=False,
            session_investigation=None,
            session_derived_output=None,
            queue_root=tmp_path,
            session_id="test-session",
        )
        assert result.matched is True
        assert result.status == "ok:time_question"
        assert "It's" in result.assistant_text

    def test_non_time_question_falls_through(self, tmp_path: Path) -> None:
        """Non-time questions are not matched by time question check."""
        from voxera.vera_web.chat_early_exit_dispatch import dispatch_early_exit_intent

        result = dispatch_early_exit_intent(
            message="Hello there",
            diagnostics_service_turn=False,
            requested_job_id=None,
            should_attempt_derived_save=False,
            session_investigation=None,
            session_derived_output=None,
            queue_root=tmp_path,
            session_id="test-session",
        )
        assert result.matched is False
