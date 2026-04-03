"""Regression tests for internal compiler/JSON payload leakage prevention.

Covers the live repro where a planning-style authored request (workout plan)
caused Vera to leak raw internal compiler payloads (intent, reasoning,
decisions, write_file) into visible assistant chat instead of producing a
clean response or a proper preview.

Root causes fixed:
- ``_CONVERSATIONAL_PLANNING_RE`` did not cover workout/training/routine/study
  planning requests, so they bypassed the nuclear conversational sanitizer.
- ``guardrail_false_preview_claim`` preserved fenced code blocks containing
  internal compiler payloads instead of stripping them.
- No defense-in-depth in GOVERNED_PREVIEW mode against bare internal payloads.
"""

from __future__ import annotations

import pytest

from voxera.vera_web.conversational_checklist import (
    has_conversational_planning_signal,
    is_conversational_answer_first_request,
    sanitize_false_preview_claims_from_answer,
    should_use_conversational_artifact_mode,
)
from voxera.vera_web.response_shaping import (
    _looks_like_internal_compiler_payload,
    guardrail_false_preview_claim,
    strip_internal_compiler_leakage,
)

# ---------------------------------------------------------------------------
# 1. Planning signal detection for workout / training / routine requests
# ---------------------------------------------------------------------------


class TestPlanningSignalExpansion:
    """Verify that workout/training/routine planning requests trigger the
    conversational planning signal and route into CONVERSATIONAL_ARTIFACT mode.
    """

    @pytest.mark.parametrize(
        "message",
        [
            "can you help me get a training workout course going?",
            "I need a workout plan for building muscle",
            "help me get a fitness routine going",
            "create a workout routine for me",
            "I want a training program for bodybuilding",
            "give me a study plan for my exams",
            "I need an exercise schedule for the week",
            "help me get a meal plan started",
            "can you make a fitness program for beginners",
            "I'd like a training schedule for marathon prep",
            "help me get a study routine going",
            "I need a revision schedule for finals",
        ],
    )
    def test_planning_signal_matches_authored_planning_requests(self, message: str) -> None:
        assert has_conversational_planning_signal(message), (
            f"Expected has_conversational_planning_signal({message!r}) to be True"
        )

    @pytest.mark.parametrize(
        "message",
        [
            "can you help me get a training workout course going?",
            "I need a workout plan for building muscle",
            "give me a study plan for my exams",
        ],
    )
    def test_authored_planning_classified_as_answer_first(self, message: str) -> None:
        assert is_conversational_answer_first_request(message), (
            f"Expected is_conversational_answer_first_request({message!r}) to be True"
        )

    @pytest.mark.parametrize(
        "message",
        [
            "can you help me get a training workout course going?",
            "I need a workout plan for building muscle",
        ],
    )
    def test_should_use_conversational_artifact_mode(self, message: str) -> None:
        assert should_use_conversational_artifact_mode(
            message,
            prior_planning_active=False,
            pending_preview=None,
            is_recent_assistant_content_save_request=False,
        )

    def test_prior_planning_keeps_followup_conversational(self) -> None:
        """When prior_planning_active is True, a bare detail follow-up stays
        in conversational mode (the user is providing details, not starting
        a new governed flow).
        """
        followup = "bodybuilding, gym 3-4 times a week, 1 hour sessions, 2-3 sets to failure"
        assert should_use_conversational_artifact_mode(
            followup,
            prior_planning_active=True,
            pending_preview=None,
            is_recent_assistant_content_save_request=False,
        )

    @pytest.mark.parametrize(
        "message",
        [
            # These should NOT trigger planning mode
            "what is the capital of France",
            "tell me a joke",
            "write a python script",
            "submit it",
            "save that to a file",
        ],
    )
    def test_non_planning_messages_stay_false(self, message: str) -> None:
        assert not has_conversational_planning_signal(message), (
            f"Expected has_conversational_planning_signal({message!r}) to be False"
        )


# ---------------------------------------------------------------------------
# 2. Internal compiler payload detection
# ---------------------------------------------------------------------------


class TestInternalCompilerPayloadDetection:
    """Verify that _looks_like_internal_compiler_payload correctly identifies
    internal payloads that must not leak into visible chat.
    """

    def test_detects_intent_and_write_file(self) -> None:
        block = '```json\n{"intent": "create workout", "write_file": {"path": "plan.md"}}\n```'
        assert _looks_like_internal_compiler_payload(block)

    def test_detects_reasoning_and_decisions(self) -> None:
        block = '```json\n{"reasoning": "user wants a plan", "decisions": ["create file"]}\n```'
        assert _looks_like_internal_compiler_payload(block)

    def test_detects_intent_and_tool(self) -> None:
        block = '```json\n{"intent": "plan", "tool": "write_file"}\n```'
        assert _looks_like_internal_compiler_payload(block)

    def test_normal_code_not_flagged(self) -> None:
        block = '```python\ndef workout():\n    print("day 1: chest")\n```'
        assert not _looks_like_internal_compiler_payload(block)

    def test_normal_json_not_flagged(self) -> None:
        block = '```json\n{"name": "workout plan", "days": 4}\n```'
        assert not _looks_like_internal_compiler_payload(block)

    def test_single_marker_not_flagged(self) -> None:
        block = '```json\n{"write_file": {"path": "plan.md"}}\n```'
        assert not _looks_like_internal_compiler_payload(block)


# ---------------------------------------------------------------------------
# 3. guardrail_false_preview_claim — compiler payload stripping
# ---------------------------------------------------------------------------


class TestGuardrailCompilerPayloadStripping:
    """Verify that guardrail_false_preview_claim strips internal compiler
    payloads from code blocks rather than preserving them.
    """

    def test_compiler_payload_in_code_block_stripped(self) -> None:
        text = (
            "I've prepared a preview.\n\n"
            "```json\n"
            '{"intent": "create workout plan", "reasoning": "user wants bodybuilding",\n'
            ' "decisions": ["create structured plan"], "write_file": {"path": "plan.md"}}\n'
            "```\n\n"
            "The preview is ready."
        )
        result = guardrail_false_preview_claim(text, preview_exists=False)
        assert '"intent"' not in result
        assert '"reasoning"' not in result
        assert '"decisions"' not in result
        assert '"write_file"' not in result
        assert "I was not able to prepare a governed preview" in result

    def test_normal_code_block_preserved(self) -> None:
        text = (
            "I've prepared a preview.\n\n"
            "```python\ndef workout():\n    print('day 1: chest')\n```\n\n"
            "Check the preview pane."
        )
        result = guardrail_false_preview_claim(text, preview_exists=False)
        assert "def workout():" in result
        assert "I was not able to create a governed preview for this code" in result

    def test_mixed_blocks_only_compiler_stripped(self) -> None:
        text = (
            "I've prepared a preview.\n\n"
            "```python\ndef workout():\n    pass\n```\n\n"
            '```json\n{"intent": "plan", "write_file": {"path": "x.md"}}\n```\n\n'
            "Preview pane."
        )
        result = guardrail_false_preview_claim(text, preview_exists=False)
        assert "def workout():" in result
        assert '"intent"' not in result

    def test_preview_exists_passes_through(self) -> None:
        """When a real preview exists, the guardrail does not alter the text."""
        text = '```json\n{"intent": "x", "write_file": {"path": "y"}}\n```'
        result = guardrail_false_preview_claim(text, preview_exists=True)
        assert result == text


# ---------------------------------------------------------------------------
# 4. strip_internal_compiler_leakage (defense-in-depth for GOVERNED_PREVIEW)
# ---------------------------------------------------------------------------


class TestStripInternalCompilerLeakage:
    """Verify the defense-in-depth guardrail that runs in GOVERNED_PREVIEW
    mode strips internal compiler payloads.
    """

    def test_strips_fenced_compiler_payload(self) -> None:
        text = (
            "Here's your workout plan.\n\n"
            '```json\n{"intent": "create plan", "reasoning": "bodybuilding",\n'
            ' "decisions": ["structured"], "write_file": {"path": "plan.md"}}\n```\n\n'
            "Let me know if you want changes."
        )
        result = strip_internal_compiler_leakage(text)
        assert '"intent"' not in result
        assert '"reasoning"' not in result
        assert "workout plan" in result
        assert "Let me know" in result

    def test_strips_bare_json_payload(self) -> None:
        text = (
            '{"intent": "create plan", "reasoning": "user wants workout",\n'
            ' "decisions": ["create file"], "write_file": {"path": "plan.md"}}'
        )
        result = strip_internal_compiler_leakage(text)
        assert '"intent"' not in result
        assert '"reasoning"' not in result

    def test_preserves_normal_content(self) -> None:
        text = (
            "Here's your workout plan:\n\n"
            "1. Day 1: Chest and Triceps\n"
            "2. Day 2: Back and Biceps\n"
            "3. Day 3: Legs and Shoulders"
        )
        result = strip_internal_compiler_leakage(text)
        assert result == text

    def test_preserves_normal_code_blocks(self) -> None:
        text = "```python\ndef hello():\n    print('hi')\n```"
        result = strip_internal_compiler_leakage(text)
        assert "def hello():" in result

    def test_strips_nested_bare_json_no_trailing_brace(self) -> None:
        """Nested JSON objects must not leave a trailing ``}`` residue."""
        text = '{"intent": "x", "write_file": {"path": "y", "content": "hello"}}'
        result = strip_internal_compiler_leakage(text)
        assert '"intent"' not in result
        assert "}" not in result

    def test_strips_multiline_bare_json(self) -> None:
        """Multi-line bare JSON with markers spread across lines must be stripped."""
        text = (
            "Here is the plan.\n\n"
            '{"intent": "create plan",\n'
            ' "reasoning": "user wants workout",\n'
            ' "write_file": {"path": "plan.md", "content": "Day 1: Chest"}}\n\n'
            "Hope you like it."
        )
        result = strip_internal_compiler_leakage(text)
        assert '"intent"' not in result
        assert '"reasoning"' not in result
        assert "Here is the plan." in result
        assert "Hope you like it." in result

    def test_empty_input(self) -> None:
        assert strip_internal_compiler_leakage("") == ""
        assert strip_internal_compiler_leakage("   ") == "   "


# ---------------------------------------------------------------------------
# 5. Conversational sanitizer catches compiler payloads (nuclear path)
# ---------------------------------------------------------------------------


class TestConversationalSanitizerCatchesPayloads:
    """When correctly routed to CONVERSATIONAL_ARTIFACT mode, the nuclear
    sanitizer should strip any compiler/JSON leakage.
    """

    def test_json_payload_stripped_by_sanitizer(self) -> None:
        raw = (
            '```json\n{"intent": "workout plan", "write_file": {"path": "plan.md"}}\n```\n\n'
            "I've prepared a preview for your workout plan."
        )
        result = sanitize_false_preview_claims_from_answer(raw)
        assert '"intent"' not in result
        assert '"write_file"' not in result

    def test_bare_json_stripped_by_defense_in_depth(self) -> None:
        """Bare JSON without preview claims is caught by strip_internal_compiler_leakage
        (defense-in-depth) rather than the conversational sanitizer alone.
        """
        raw = '{"intent": "plan", "reasoning": "bodybuilding", "decisions": ["create"]}'
        result = strip_internal_compiler_leakage(raw)
        assert '"intent"' not in result


# ---------------------------------------------------------------------------
# 6. Live repro anchors — exact user messages from the reported scenario
# ---------------------------------------------------------------------------


class TestLiveReproAnchors:
    """Exact repro anchors from the observed live failure sequence.
    Ensures the initial request and follow-up details both trigger the
    conversational planning path.
    """

    def test_initial_workout_request_is_planning(self) -> None:
        msg = "can you help me get a training workout course going?"
        assert has_conversational_planning_signal(msg)
        assert is_conversational_answer_first_request(msg)
        assert should_use_conversational_artifact_mode(
            msg,
            prior_planning_active=False,
            pending_preview=None,
            is_recent_assistant_content_save_request=False,
        )

    def test_detail_followup_stays_conversational_via_prior_planning(self) -> None:
        """The follow-up message with workout details stays in conversational
        mode because prior_planning_active is True from the first turn.
        """
        followup = (
            "bodybuilding / looking good, can sign up to gym if required, "
            "3 to 4 times a week, 1 hour sessions, likes 2 or 3 sets to failure"
        )
        assert should_use_conversational_artifact_mode(
            followup,
            prior_planning_active=True,
            pending_preview=None,
            is_recent_assistant_content_save_request=False,
        )

    def test_clearer_restatement_stays_conversational(self) -> None:
        restatement = (
            "I want a bodybuilding workout program, I can go to a gym, "
            "3-4 times per week, 1 hour each session, I like 2-3 sets to failure"
        )
        # Either direct match on "workout program" or via prior_planning_active
        is_direct = has_conversational_planning_signal(restatement)
        stays_via_prior = should_use_conversational_artifact_mode(
            restatement,
            prior_planning_active=True,
            pending_preview=None,
            is_recent_assistant_content_save_request=False,
        )
        assert is_direct or stays_via_prior, (
            "Clearer restatement should route to conversational mode "
            "either directly or via prior_planning_active"
        )

    def test_observed_leak_payload_stripped_by_guardrail(self) -> None:
        """The exact type of payload that leaked in the live failure is
        stripped by guardrail_false_preview_claim when no preview exists.
        """
        leaked_text = (
            "I've prepared a preview for your workout plan.\n\n"
            "```json\n"
            "{\n"
            '  "intent": "create workout plan",\n'
            '  "reasoning": "User wants a bodybuilding program with specific parameters",\n'
            '  "decisions": [\n'
            '    "Create structured 4-day split",\n'
            '    "Include sets to failure methodology"\n'
            "  ],\n"
            '  "tool": "write_file",\n'
            '  "write_file": {\n'
            '    "path": "~/VoxeraOS/notes/workout-plan.md",\n'
            '    "content": "# Workout Plan\\n\\n..."\n'
            "  }\n"
            "}\n"
            "```\n\n"
            "I was not able to create a governed preview for this code. "
            "The code above is shown for reference only — "
            "no preview is active in this session."
        )
        result = guardrail_false_preview_claim(leaked_text, preview_exists=False)
        assert '"intent"' not in result
        assert '"reasoning"' not in result
        assert '"decisions"' not in result
        assert '"write_file"' not in result

    def test_observed_leak_stripped_by_defense_in_depth(self) -> None:
        """Even in GOVERNED_PREVIEW mode (non-conversational), the defense-
        in-depth guardrail strips compiler payloads.
        """
        raw_reply = (
            "Here's your workout plan.\n\n"
            "```json\n"
            '{"intent": "create workout plan",\n'
            ' "reasoning": "bodybuilding program",\n'
            ' "decisions": ["4-day split"],\n'
            ' "write_file": {"path": "plan.md", "content": "..."}}\n'
            "```"
        )
        result = strip_internal_compiler_leakage(raw_reply)
        assert '"intent"' not in result
        assert '"reasoning"' not in result


# ---------------------------------------------------------------------------
# 7. Truthful behavior — preview truth alignment
# ---------------------------------------------------------------------------


class TestPreviewTruthAlignment:
    """When a preview is created, the reply should reference it truthfully.
    When no preview exists, the reply must not claim one does.
    """

    def test_no_preview_no_claim(self) -> None:
        text = "I've prepared a preview of your workout.\n\nCheck the preview pane to review it."
        result = guardrail_false_preview_claim(text, preview_exists=False)
        assert "I was not able to prepare a governed preview" in result

    def test_real_preview_claim_passes(self) -> None:
        text = "I've prepared a preview of your workout plan."
        result = guardrail_false_preview_claim(text, preview_exists=True)
        assert result == text
