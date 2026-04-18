"""Focused tests for the Voice Workbench action-oriented classifier.

Pins the bounded, deterministic classification seam that decides
whether a spoken request looks like real governed work.  The
classifier is conservative by design:

- question-form phrasings are always informational
- action-oriented firing requires either a direct mutation verb with
  a plausible target, or an action verb inside an imperative prefix
  ("please delete it")
- empty / missing transcripts classify as informational
- the classifier never calls Vera and never touches queue state

These tests do NOT exercise the panel HTTP surface; the route-level
rendering is pinned separately in
``test_panel_voice_workbench_action_guidance.py``.
"""

from __future__ import annotations

import pytest

from voxera.panel.voice_workbench_classifier import (
    CLASSIFICATION_ACTION_ORIENTED,
    CLASSIFICATION_INFORMATIONAL,
    VoiceWorkbenchClassification,
    classify_workbench_transcript,
)


class TestInformationalClassification:
    """Conversational / read-only phrasings must NOT flip to action-oriented."""

    @pytest.mark.parametrize(
        "transcript",
        [
            "what time is it?",
            "what is the weather in paris today",
            "how do i delete a file in linux?",
            "tell me about the queue constitution",
            "explain how automations work",
            "why does vera ask for approval?",
            "describe the governed handoff model",
            "when was the last deploy?",
            "who owns the voxera-vera service?",
            "where are artifacts stored?",
            "is the daemon running right now?",
            "does vera support gemini?",
            "show me recent jobs",
        ],
    )
    def test_question_form_is_informational(self, transcript: str) -> None:
        result = classify_workbench_transcript(transcript)
        assert result.kind == CLASSIFICATION_INFORMATIONAL
        assert result.is_action_oriented is False

    @pytest.mark.parametrize(
        "transcript",
        [
            "please check system health",
            "i was thinking about the architecture",
            "hey vera, just brainstorming here",
            "the lab feels slow today",
            "nothing urgent, just exploring",
        ],
    )
    def test_plain_conversational_is_informational(self, transcript: str) -> None:
        result = classify_workbench_transcript(transcript)
        assert result.kind == CLASSIFICATION_INFORMATIONAL

    def test_empty_transcript_is_informational(self) -> None:
        assert classify_workbench_transcript("").kind == CLASSIFICATION_INFORMATIONAL
        assert classify_workbench_transcript("   ").kind == CLASSIFICATION_INFORMATIONAL

    def test_none_transcript_is_informational(self) -> None:
        assert classify_workbench_transcript(None).kind == CLASSIFICATION_INFORMATIONAL


class TestActionOrientedClassification:
    """Clear mutation phrasings must flip the classifier."""

    @pytest.mark.parametrize(
        "transcript",
        [
            "delete the report file from notes",
            "move the uploads folder into the archive",
            "rename the script to run.sh",
            "create a new file called status.md",
            "write a python script that prints hello",
            "install the latest voxera package",
            "run the system_inspect mission",
            "restart the voxera-daemon service",
            "stop the panel service",
            "schedule a daily automation",
            "submit a job for log rotation",
            "disable that automation",
            "organize my notes folder",
            "save the current draft as a file",
        ],
    )
    def test_action_verb_with_target_is_action_oriented(self, transcript: str) -> None:
        result = classify_workbench_transcript(transcript)
        assert result.kind == CLASSIFICATION_ACTION_ORIENTED
        assert result.is_action_oriented is True
        assert result.matched_signals, "expected matched signals to be populated"

    @pytest.mark.parametrize(
        "transcript",
        [
            "please delete it",
            "go ahead and restart",
            "could you run it now",
            "i need you to stop",
        ],
    )
    def test_imperative_prefix_with_action_verb_is_action_oriented(self, transcript: str) -> None:
        result = classify_workbench_transcript(transcript)
        assert result.kind == CLASSIFICATION_ACTION_ORIENTED


class TestConservativeDefaults:
    """The classifier leans conservative: ambiguous stays informational."""

    def test_mutation_verb_inside_question_stays_informational(self) -> None:
        """Asking HOW to do something is not the same as asking to do it."""
        assert (
            classify_workbench_transcript("how do i delete a stale artifact?").kind
            == CLASSIFICATION_INFORMATIONAL
        )
        assert (
            classify_workbench_transcript("what does 'run a mission' mean?").kind
            == CLASSIFICATION_INFORMATIONAL
        )

    def test_bare_verb_without_target_or_imperative_is_informational(self) -> None:
        """A bare 'delete' with no target and no imperative framing does not
        fire action-oriented — the signal is too weak."""
        assert (
            classify_workbench_transcript("the word delete keeps coming up").kind
            == CLASSIFICATION_INFORMATIONAL
        )

    def test_word_boundaries_protect_against_substring_matches(self) -> None:
        """'recreate' / 'running late' must not match 'create' / 'run'."""
        assert (
            classify_workbench_transcript("the recreation area is nice").kind
            == CLASSIFICATION_INFORMATIONAL
        )
        assert (
            classify_workbench_transcript("running late on the doc review").kind
            == CLASSIFICATION_INFORMATIONAL
        )


class TestReasonAndMatchedSignalsAreExplainable:
    """The classifier result carries the branch that fired, for debug."""

    def test_result_is_frozen_dataclass(self) -> None:
        result = classify_workbench_transcript("delete the file")
        assert isinstance(result, VoiceWorkbenchClassification)
        with pytest.raises(AttributeError):
            # frozen dataclass
            result.kind = "other"  # type: ignore[misc]

    def test_matched_signals_include_verb_and_target(self) -> None:
        result = classify_workbench_transcript("please delete the file now")
        assert "delete" in result.matched_signals
        assert "file" in result.matched_signals

    def test_reason_is_populated(self) -> None:
        assert classify_workbench_transcript("").reason
        assert classify_workbench_transcript("what is x?").reason
        assert classify_workbench_transcript("delete that file").reason
