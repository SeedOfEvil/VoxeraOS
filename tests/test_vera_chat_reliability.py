"""Characterization tests for Vera chat reliability improvements.

Covers:
  - Natural drafting prompt recognition (write me, draft a, put together, write up)
  - Follow-up phrasing after linked-job completion
  - Preview-only wording correctness for drafting flows
  - Wrapper stripping for new LLM reply patterns
  - Content-shape signal coverage (note, writeup as content targets)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from voxera.core.writing_draft_intent import (
    extract_text_draft_from_reply,
    is_writing_draft_request,
)
from voxera.vera.evidence_review import (
    ReviewedJobEvidence,
    is_followup_preview_request,
)
from voxera.vera_web import app as vera_app_module
from voxera.vera_web.chat_early_exit_dispatch import dispatch_early_exit_intent

from .vera_session_helpers import make_vera_session

# ---------------------------------------------------------------------------
# 1. Writing draft recognition — new natural phrasing variants
# ---------------------------------------------------------------------------


class TestWritingDraftRecognitionExpanded:
    """Verify that natural drafting phrasings are classified as writing-draft requests."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "write me a short note about photosynthesis",
            "draft a brief summary of the project",
            "write up a quick explanation of DNS",
            "put together a short writeup about volcanoes",
            "put together a summary of the meeting notes",
            "write up an explanation of how tides work",
            "draft a brief explanation of the solar system",
        ],
    )
    def test_natural_drafting_phrases_recognized(self, phrase: str) -> None:
        assert is_writing_draft_request(phrase), (
            f"Expected is_writing_draft_request to be True for: {phrase!r}"
        )

    @pytest.mark.parametrize(
        "phrase",
        [
            "what is the capital of France",
            "tell me a joke",
            "search the web for DNS documentation",
            "save that to a note",
            "submit it",
            # Regression guards: "put" in non-drafting contexts must stay False
            "put the book on the shelf",
            "put it in the queue",
            "put it in a note",
            "put this answer in a note",
            # "note" in non-drafting contexts
            "note that the meeting is at 3pm",
            "please note this for the record",
        ],
    )
    def test_non_drafting_phrases_not_matched(self, phrase: str) -> None:
        assert not is_writing_draft_request(phrase), (
            f"Expected is_writing_draft_request to be False for: {phrase!r}"
        )


# ---------------------------------------------------------------------------
# 2. Follow-up phrasing — expanded conversational hints
# ---------------------------------------------------------------------------


class TestFollowupPhrasingExpanded:
    """Verify that natural post-job follow-up phrasings reach the follow-up branch."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "now prepare the follow-up",
            "now draft the follow-up",
            "queue the next step",
            "queue a follow-up",
            "queue the follow-up",
            "do the next step",
            "do the follow-up",
            "let's do the next step",
            "okay now do the follow-up",
            "draft the follow-up",
            "prepare the follow-up",
            "write the follow-up",
            "based on that outcome",
            "based on the outcome",
            "what should we do next based on that",
            "what's the next step based on that",
        ],
    )
    def test_followup_hints_recognized(self, phrase: str) -> None:
        assert is_followup_preview_request(phrase), (
            f"Expected is_followup_preview_request to be True for: {phrase!r}"
        )

    @pytest.mark.parametrize(
        "phrase",
        [
            "what is the capital of France",
            "write me a poem",
            "tell me a joke",
            "save that to a note",
            # Regression guards: similar phrases that must NOT be follow-up
            "do the homework",
            "let's do this",
            "write the next chapter",
            "prepare the food",
            "queue the print job",
            "what should I do next",  # review hint, not follow-up
        ],
    )
    def test_non_followup_phrases_not_matched(self, phrase: str) -> None:
        assert not is_followup_preview_request(phrase), (
            f"Expected is_followup_preview_request to be False for: {phrase!r}"
        )

    def test_expanded_followup_hints_reach_dispatch_branch(self, tmp_path: Path) -> None:
        """New follow-up hints must actually reach the followup branch in dispatch."""
        phrases = [
            "now prepare the follow-up",
            "queue the next step",
            "do the follow-up",
            "let's do the next step",
        ]
        for phrase in phrases:
            result = dispatch_early_exit_intent(
                message=phrase,
                diagnostics_service_turn=False,
                requested_job_id=None,
                should_attempt_derived_save=False,
                session_investigation=None,
                session_derived_output=None,
                queue_root=tmp_path,
                session_id="test-session",
            )
            assert result.matched is True, f"Phrase {phrase!r} did not match any early-exit"
            assert result.status == "followup_missing_evidence", (
                f"Phrase {phrase!r} got status={result.status!r} instead of followup_missing_evidence"
            )

    def test_expanded_followup_with_evidence_returns_preview_ready(self, tmp_path: Path) -> None:
        mock_evidence = ReviewedJobEvidence(
            job_id="job-20260401-expanded",
            state="done",
            lifecycle_state="done",
            terminal_outcome="succeeded",
            approval_status="",
            latest_summary="Completed.",
            failure_summary="",
            artifact_families=(),
            artifact_refs=(),
            evidence_trace=(),
            child_summary=None,
            execution_capabilities=None,
            capability_boundary_violation=None,
            expected_artifacts=(),
            observed_expected_artifacts=(),
            missing_expected_artifacts=(),
            expected_artifact_status="",
            normalized_outcome_class="success",
            value_forward_text="",
        )
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=mock_evidence,
        ):
            result = dispatch_early_exit_intent(
                message="queue the next step",
                diagnostics_service_turn=False,
                requested_job_id=None,
                should_attempt_derived_save=False,
                session_investigation=None,
                session_derived_output=None,
                queue_root=tmp_path,
                session_id="test-session",
            )
        assert result.matched is True
        assert result.status == "followup_preview_ready"
        assert result.write_preview is True
        assert result.write_handoff_ready is True
        assert "preview-only" in result.assistant_text.lower()


# ---------------------------------------------------------------------------
# 3. Wrapper stripping — new LLM reply patterns
# ---------------------------------------------------------------------------


class TestWrapperStrippingExpanded:
    """Verify that new wrapper patterns are stripped from LLM replies."""

    def test_heres_what_i_came_up_with_stripped(self) -> None:
        text = (
            "Here's what I came up with:\n\n"
            "Volcanoes are mountains that can erupt with lava and ash, "
            "reshaping landscapes dramatically."
        )
        result = extract_text_draft_from_reply(text)
        assert result is not None
        assert result.startswith("Volcanoes are mountains")
        assert "here's what i came up with" not in result.lower()

    def test_ive_put_together_stripped(self) -> None:
        text = (
            "I've put together a short note:\n\n"
            "Photosynthesis is the process plants use to convert sunlight into energy."
        )
        result = extract_text_draft_from_reply(text)
        assert result is not None
        assert "photosynthesis" in result.lower()
        assert "i've put together" not in result.lower()

    def test_let_me_know_if_youd_like_any_changes_stripped(self) -> None:
        text = (
            "The water cycle describes how water moves through evaporation, condensation, "
            "and precipitation in a continuous loop.\n\n"
            "Let me know if you'd like any changes to the wording."
        )
        result = extract_text_draft_from_reply(text)
        assert result is not None
        assert "water cycle" in result.lower()
        assert "let me know" not in result.lower()

    def test_would_you_like_me_to_save_stripped(self) -> None:
        text = (
            "DNS translates human-readable domain names into IP addresses that computers use.\n\n"
            "Would you like me to save this to a file?"
        )
        result = extract_text_draft_from_reply(text)
        assert result is not None
        assert "dns translates" in result.lower()
        assert "would you like me to save" not in result.lower()


# ---------------------------------------------------------------------------
# 4. Session-level characterization — natural drafting flows
# ---------------------------------------------------------------------------


class TestNaturalDraftingSessionFlows:
    """End-to-end session tests for natural drafting phrasings."""

    def test_write_me_a_note_creates_preview(self, tmp_path, monkeypatch) -> None:
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message, **kw):
            _ = turns
            return {
                "answer": "Photosynthesis lets plants use sunlight to make sugar from water and CO2.",
                "status": "ok:test",
            }

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        res = session.chat("write me a short note about photosynthesis")
        assert res.status_code == 200
        preview = session.preview()
        assert preview is not None
        assert "write_file" in preview
        assert "photosynthesis" in preview["write_file"]["content"].lower()

    def test_draft_a_brief_summary_creates_preview(self, tmp_path, monkeypatch) -> None:
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message, **kw):
            _ = turns
            return {
                "answer": "The project aims to improve queue reliability through bounded extraction.",
                "status": "ok:test",
            }

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        res = session.chat("draft a brief summary of the project goals")
        assert res.status_code == 200
        preview = session.preview()
        assert preview is not None
        assert "write_file" in preview
        assert "queue reliability" in preview["write_file"]["content"].lower()

    def test_put_together_a_writeup_creates_preview(self, tmp_path, monkeypatch) -> None:
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message, **kw):
            _ = turns
            return {
                "answer": (
                    "Here's what I came up with:\n\n"
                    "Volcanoes form at tectonic boundaries where magma rises through the crust."
                ),
                "status": "ok:test",
            }

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        res = session.chat("put together a short writeup about volcanoes")
        assert res.status_code == 200
        preview = session.preview()
        assert preview is not None
        assert "write_file" in preview
        content = preview["write_file"]["content"].lower()
        assert "volcanoes" in content
        assert "here's what i came up with" not in content

    def test_write_up_an_explanation_creates_preview(self, tmp_path, monkeypatch) -> None:
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message, **kw):
            _ = turns
            return {
                "answer": "DNS is a hierarchical naming system that translates domain names to IP addresses.",
                "status": "ok:test",
            }

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        res = session.chat("write up a quick explanation of how DNS works")
        assert res.status_code == 200
        preview = session.preview()
        assert preview is not None
        assert "write_file" in preview
        assert "dns" in preview["write_file"]["content"].lower()

    def test_natural_draft_with_save_as_uses_specified_filename(
        self, tmp_path, monkeypatch
    ) -> None:
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message, **kw):
            _ = turns
            return {
                "answer": "Tides are caused by the gravitational pull of the moon and sun on Earth's oceans.",
                "status": "ok:test",
            }

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        res = session.chat("write me a short note about tides and save it as tides.txt")
        assert res.status_code == 200
        preview = session.preview()
        assert preview is not None
        assert preview["write_file"]["path"] == "~/VoxeraOS/notes/tides.txt"
        assert "tides" in preview["write_file"]["content"].lower()


# ---------------------------------------------------------------------------
# 5. Content generation with active preview — note/writeup as content shape
# ---------------------------------------------------------------------------


class TestContentShapeSignalExpanded:
    """Verify that 'note' and 'writeup' are recognized as content shape signals."""

    def test_tell_me_a_note_about_refreshes_active_preview(self, tmp_path, monkeypatch) -> None:
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message, **kw):
            _ = turns
            if "gravity" in user_message.lower():
                return {
                    "answer": "Gravity is the force that attracts objects toward one another.",
                    "status": "ok:test",
                }
            return {"answer": "Seed content.", "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        session.chat("what is 2 + 2?")
        session.chat("save that as draft.txt")
        res = session.chat("write me a note about gravity and add as content")
        assert res.status_code == 200
        preview = session.preview()
        assert preview is not None
        content = preview["write_file"]["content"].lower()
        assert "gravity" in content
