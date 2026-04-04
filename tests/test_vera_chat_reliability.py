"""Characterization tests for Vera chat reliability improvements.

Covers:
  - Natural drafting prompt recognition (write me, draft a, put together, write up)
  - Follow-up phrasing after linked-job completion
  - Preview-only wording correctness for drafting flows
  - Wrapper stripping for new LLM reply patterns
  - Content-shape signal coverage (note, writeup as content targets)
  - Preview truth binding: authored content reaches preview write_file.content
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
from voxera.vera_web.draft_content_binding import extract_reply_drafts

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

    def test_expanded_followup_hints_fall_through_without_job_context(self, tmp_path: Path) -> None:
        """Follow-up hints without job context must NOT enter the followup branch."""
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
            assert result.status != "followup_missing_evidence", (
                f"Phrase {phrase!r} should fall through without job context"
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
                session_context={"last_completed_job_ref": "job-20260401-expanded"},
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


# ---------------------------------------------------------------------------
# 6. Preview truth binding — authored content reaches preview write_file.content
# ---------------------------------------------------------------------------


class TestPreviewTruthBinding:
    """Regression: authored prose must be bound into the authoritative preview
    payload, not just the assistant chat reply.  These tests verify the exact
    live-failing prompts where recognition was correct but preview content was
    empty or truncated."""

    def test_writeup_about_operator_truth_surfaces_binds_to_preview(
        self, tmp_path, monkeypatch
    ) -> None:
        """Live failure: 'put together a short writeup about operator truth surfaces'
        produced a good writeup in chat, but preview write_file.content was
        truncated junk ('who, what, and when.').

        Root cause: authored prose mentioned 'queue state' which triggered
        looks_like_non_authored_assistant_message, causing reply_text_draft=None
        and leaving the builder's junk content in the preview."""
        session = make_vera_session(monkeypatch, tmp_path)

        authored_content = (
            "Operator truth surfaces in VoxeraOS are the canonical points where "
            "system state becomes visible and actionable for human operators. "
            "These surfaces include the queue state display, job evidence bundles, "
            "artifact inspection views, and approval gates.\n\n"
            "The key design principle is that every truth surface must be grounded "
            "in real persisted state — never in LLM-generated claims or speculative "
            "summaries."
        )

        async def _fake_reply(*, turns, user_message, **kw):
            _ = turns
            return {"answer": authored_content, "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        res = session.chat("put together a short writeup about operator truth surfaces")
        assert res.status_code == 200
        preview = session.preview()
        assert preview is not None, "Preview must be created"
        assert "write_file" in preview
        wf = preview["write_file"]
        assert wf["path"], "write_file.path must be non-empty"
        content = wf["content"]
        assert content, "write_file.content must be non-empty"
        assert len(content) > 50, (
            f"write_file.content must be substantial authored prose, got {len(content)} chars"
        )
        assert "operator truth surfaces" in content.lower()
        assert "queue state" in content.lower()
        # Must not be the builder's junk
        assert content != "who, what, and when."

    def test_note_about_artifact_evidence_model_binds_to_preview(
        self, tmp_path, monkeypatch
    ) -> None:
        """Live failure: 'write me a note about the artifact evidence model'
        produced a good note in chat, but preview write_file.content was empty.

        Root cause: same as above — authored content about VoxeraOS concepts
        could trigger system-term filters, leaving reply_text_draft=None."""
        session = make_vera_session(monkeypatch, tmp_path)

        authored_content = (
            "The artifact evidence model in VoxeraOS provides a structured way "
            "to track what happened during queue job execution. Each job produces "
            "an evidence bundle containing the terminal outcome, approval status, "
            "step results, and expected artifacts.\n\n"
            "This model ensures operators can verify execution truth without "
            "relying on LLM-generated summaries."
        )

        async def _fake_reply(*, turns, user_message, **kw):
            _ = turns
            return {"answer": authored_content, "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        res = session.chat("write me a note about the artifact evidence model")
        assert res.status_code == 200
        preview = session.preview()
        assert preview is not None, "Preview must be created"
        assert "write_file" in preview
        wf = preview["write_file"]
        assert wf["path"], "write_file.path must be non-empty"
        content = wf["content"]
        assert content, "write_file.content must be non-empty"
        assert len(content) > 50, (
            f"write_file.content must be substantial authored prose, got {len(content)} chars"
        )
        assert "artifact evidence model" in content.lower()
        assert "evidence bundle" in content.lower()
        # Must not be empty string
        assert content != ""

    def test_preview_content_and_assistant_reply_stay_aligned(self, tmp_path, monkeypatch) -> None:
        """Preview content and assistant reply must be aligned enough that
        the governed UX is truthful: what the user sees in chat should
        materially match what the preview would submit."""
        session = make_vera_session(monkeypatch, tmp_path)

        authored_content = (
            "Tectonic plates are massive slabs of Earth's lithosphere that "
            "move, float, and sometimes fracture. Their interaction causes "
            "earthquakes, volcanic activity, and mountain formation."
        )

        async def _fake_reply(*, turns, user_message, **kw):
            _ = turns
            return {"answer": authored_content, "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        res = session.chat("draft a brief summary of plate tectonics")
        assert res.status_code == 200

        preview = session.preview()
        assert preview is not None
        preview_content = preview["write_file"]["content"]

        last_turn = session.turns()[-1]
        assert last_turn["role"] == "assistant"
        assistant_text = last_turn["text"]

        # The preview content must be a meaningful portion of the authored content
        assert "tectonic plates" in preview_content.lower()
        # The assistant text should contain or reference the same content
        assert "tectonic plates" in assistant_text.lower() or preview_content in assistant_text


# ---------------------------------------------------------------------------
# 7. Live failure regression: drafting + refinement preview-state wording
# ---------------------------------------------------------------------------


class TestDraftingAndRefinementPreviewStateWording:
    """Regression tests for the exact two-prompt live failure sequence:
      1. "write me a note about the artifact evidence model"
      2. "make it shorter and more operator-facing"

    Verifies:
    - preview is prepared after the first prompt
    - preview content contains meaningful drafted note content, not placeholder
    - assistant wording includes prepared-preview / preview-only / not submitted
    - after refinement, preview content changes to the refined version
    - assistant wording includes updated-preview / still preview-only / not submitted
    """

    def test_initial_draft_creates_preview_with_correct_content_and_wording(
        self, tmp_path, monkeypatch
    ) -> None:
        session = make_vera_session(monkeypatch, tmp_path)

        authored_content = (
            "The artifact evidence model in VoxeraOS provides a structured way "
            "to track what happened during queue job execution. Each job produces "
            "an evidence bundle containing the terminal outcome, approval status, "
            "step results, and expected artifacts."
        )

        async def _fake_reply(*, turns, user_message, **kw):
            _ = turns
            return {"answer": authored_content, "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        res = session.chat("write me a note about the artifact evidence model")
        assert res.status_code == 200

        # Preview must exist with real content
        preview = session.preview()
        assert preview is not None, "Preview must be created"
        assert "write_file" in preview
        wf = preview["write_file"]
        content = wf["content"]
        assert content, "write_file.content must be non-empty"
        assert len(content) > 50, (
            f"write_file.content must be substantial, got {len(content)} chars"
        )
        assert "artifact evidence model" in content.lower()
        # Must NOT be a stale placeholder
        assert content.lower() != "update the config file."

        # Assistant wording must include preview-state notice
        last_turn = session.turns()[-1]
        assert last_turn["role"] == "assistant"
        reply_text = last_turn["text"].lower()
        assert "preview-only" in reply_text, (
            "Reply must include 'preview-only' notice for writing-draft turns"
        )
        assert "nothing has been submitted yet" in reply_text

    def test_refinement_updates_preview_content_and_wording(self, tmp_path, monkeypatch) -> None:
        """Regression: refinement of a note containing system terms (queue state,
        approval status, expected artifacts) must not be rejected by the
        non-authored-message filter.  The refined content must be bound into
        the authoritative preview, not truncated or stale."""
        session = make_vera_session(monkeypatch, tmp_path)

        initial_content = (
            "The artifact evidence model in VoxeraOS provides a structured way "
            "to track what happened during queue job execution. Each job produces "
            "an evidence bundle containing the terminal outcome, approval status, "
            "step results, and expected artifacts."
        )
        # Refined content still mentions system terms — this is the live failure
        # case where looks_like_non_authored_assistant_message would reject
        # the content, causing reply_text_draft=None and preview drift.
        refined_content = (
            "The artifact evidence model tracks execution outcomes. "
            "Each job's evidence bundle records terminal outcome, approval status, "
            "and expected artifacts so operators can verify queue state truth."
        )

        call_count = 0

        async def _fake_reply(*, turns, user_message, **kw):
            nonlocal call_count
            call_count += 1
            _ = turns
            if call_count == 1:
                return {"answer": initial_content, "status": "ok:test"}
            return {"answer": refined_content, "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        # First turn: create the draft
        session.chat("write me a note about the artifact evidence model")
        initial_preview = session.preview()
        assert initial_preview is not None
        initial_bound = initial_preview["write_file"]["content"]
        assert "artifact evidence model" in initial_bound.lower()

        # Second turn: refine the draft
        res = session.chat("make it shorter and more operator-facing")
        assert res.status_code == 200

        # Preview must be updated with refined content
        updated_preview = session.preview()
        assert updated_preview is not None
        updated_content = updated_preview["write_file"]["content"]
        assert updated_content != initial_bound, "Preview content must change after refinement"
        # Refined content must contain the system terms — not be truncated
        assert "approval status" in updated_content.lower(), (
            "Refined content with system terms must not be rejected"
        )
        assert "expected artifacts" in updated_content.lower()
        assert len(updated_content) > 50, (
            f"Preview content must not be truncated, got {len(updated_content)} chars"
        )

        # Assistant wording must include updated-preview notice
        last_turn = session.turns()[-1]
        assert last_turn["role"] == "assistant"
        reply_text = last_turn["text"].lower()
        assert "preview-only" in reply_text or "not submitted" in reply_text, (
            "Refinement reply must include preview-only or not-submitted notice"
        )

    def test_draft_preview_content_matches_chat_not_builder_placeholder(
        self, tmp_path, monkeypatch
    ) -> None:
        """When the builder produces placeholder content ('Update the config file.')
        but the LLM produces real authored content, the preview must contain
        the authored content, not the builder placeholder."""
        session = make_vera_session(monkeypatch, tmp_path)

        authored_content = (
            "The artifact evidence model in VoxeraOS provides a structured way "
            "to track what happened during queue job execution."
        )

        async def _fake_reply(*, turns, user_message, **kw):
            _ = turns
            return {"answer": authored_content, "status": "ok:test"}

        # Builder returns a shell with placeholder content
        async def _fake_builder(**kw):
            return {
                "goal": "draft a explanation as artifact-evidence-model-explanation.txt",
                "write_file": {
                    "path": "~/VoxeraOS/notes/artifact-evidence-model-explanation.txt",
                    "content": "Update the config file.",
                    "mode": "overwrite",
                },
            }

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
        monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _fake_builder)

        res = session.chat("write me a note about the artifact evidence model")
        assert res.status_code == 200

        preview = session.preview()
        assert preview is not None
        content = preview["write_file"]["content"]
        # Must be the authored content, NOT the builder placeholder
        assert content != "Update the config file.", (
            "Preview must contain authored content, not builder placeholder"
        )
        assert "artifact evidence" in content.lower()


# ---------------------------------------------------------------------------
# 8. Refinement content extraction — non-authored filter bypass
# ---------------------------------------------------------------------------


class TestRefinementContentExtraction:
    """Regression: refinement turns on active prose previews must not have
    their reply_text_draft rejected by the non-authored-message filter when
    the authored content mentions system terms like queue state, approval
    status, or expected artifacts."""

    def test_refinement_with_system_terms_preserves_reply_text_draft(self) -> None:
        content = (
            "The artifact evidence model tracks execution outcomes. "
            "Each job records terminal outcome, approval status, "
            "and expected artifacts so operators can verify queue state truth."
        )
        # Without active prose preview: content rejected
        result_no_preview = extract_reply_drafts(
            content, "make it shorter", active_preview_is_refinable_prose=False
        )
        assert result_no_preview.reply_text_draft is None, (
            "Without active prose preview, system-term content should be rejected"
        )

        # With active prose preview: content preserved
        result_with_preview = extract_reply_drafts(
            content, "make it shorter", active_preview_is_refinable_prose=True
        )
        assert result_with_preview.reply_text_draft is not None, (
            "With active prose preview, system-term content must NOT be rejected"
        )
        assert "approval status" in result_with_preview.reply_text_draft.lower()
        assert "expected artifacts" in result_with_preview.reply_text_draft.lower()

    def test_refinement_without_system_terms_works_either_way(self) -> None:
        content = (
            "Volcanoes form when magma from the mantle reaches the surface. "
            "They are classified as active, dormant, or extinct."
        )
        result = extract_reply_drafts(
            content, "make it shorter", active_preview_is_refinable_prose=True
        )
        assert result.reply_text_draft is not None

    def test_non_refinement_message_not_affected(self) -> None:
        # Regular messages should not bypass the filter even with active preview
        content = "The approval status shows the queue state for expected artifacts."
        result = extract_reply_drafts(
            content, "what is this", active_preview_is_refinable_prose=True
        )
        # "what is this" is not a refinement request, so the filter applies
        assert result.reply_text_draft is None


# ---------------------------------------------------------------------------
# 9. Linked-job review — result inspection phrase recognition
# ---------------------------------------------------------------------------


class TestLinkedJobReviewPhraseRecognition:
    """Verify that result-inspection phrases are recognized as review requests."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "summarize the result",
            "summarize that result",
            "inspect output details",
            "inspect output",
            "inspect the output",
            "review the result",
            "review that result",
            "show me the result",
            "show the result",
            "what was the outcome",
            "what was the result",
            "summarize the job result",
        ],
    )
    def test_review_inspection_phrases_recognized(self, phrase: str) -> None:
        from voxera.vera.evidence_review import is_review_request

        assert is_review_request(phrase), f"Expected is_review_request to be True for: {phrase!r}"

    @pytest.mark.parametrize(
        "phrase",
        [
            "write me a summary about dogs",
            "inspect my code for bugs",
            "show me how to cook pasta",
            "review my essay draft",
        ],
    )
    def test_non_review_inspection_phrases_not_matched(self, phrase: str) -> None:
        from voxera.vera.evidence_review import is_review_request

        assert not is_review_request(phrase), (
            f"Expected is_review_request to be False for: {phrase!r}"
        )


# ---------------------------------------------------------------------------
# 10. Linked-job follow-up — revise-from-evidence phrase recognition
# ---------------------------------------------------------------------------


class TestLinkedJobReviseFromEvidence:
    """Verify that revise-from-evidence phrases reach the follow-up branch."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "revise that based on the result",
            "revise based on the result",
            "revise that based on the evidence",
            "revise that based on the outcome",
            "revise based on evidence",
            "update that based on the result",
            "update based on the result",
            "save the follow-up",
            "save that follow-up",
            "save the follow-up as a file",
        ],
    )
    def test_revision_phrases_recognized_as_followup(self, phrase: str) -> None:
        assert is_followup_preview_request(phrase), (
            f"Expected is_followup_preview_request to be True for: {phrase!r}"
        )

    @pytest.mark.parametrize(
        "phrase",
        [
            "revise my essay",
            "update the config file",
            "save the document",
            "save it as a note",
        ],
    )
    def test_non_revision_phrases_not_matched(self, phrase: str) -> None:
        assert not is_followup_preview_request(phrase), (
            f"Expected is_followup_preview_request to be False for: {phrase!r}"
        )

    def test_revision_phrases_fall_through_without_job_context(self, tmp_path: Path) -> None:
        """Revise-from-evidence phrases without job context fall through."""
        phrases = [
            "revise that based on the result",
            "update that based on the result",
            "save the follow-up",
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
            assert result.status != "followup_missing_evidence", (
                f"Phrase {phrase!r} should fall through without job context"
            )

    def test_revision_with_evidence_returns_preview_ready(self, tmp_path: Path) -> None:
        mock_evidence = ReviewedJobEvidence(
            job_id="job-20260402-revise",
            state="succeeded",
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
                message="revise that based on the result",
                diagnostics_service_turn=False,
                requested_job_id=None,
                should_attempt_derived_save=False,
                session_investigation=None,
                session_derived_output=None,
                queue_root=tmp_path,
                session_id="test-session",
                session_context={"last_completed_job_ref": "job-20260402-revise"},
            )
        assert result.matched is True
        assert result.status == "revised_preview_ready"
        assert result.write_preview is True
        assert "preview-only" in result.assistant_text.lower()


# ---------------------------------------------------------------------------
# 11. Follow-up preview goal text correctness
# ---------------------------------------------------------------------------


class TestFollowupPreviewGoalText:
    """Verify that follow-up preview goals are evidence-grounded, not vague."""

    def test_succeeded_job_goal_says_follow_up_not_inspect(self) -> None:
        from voxera.vera.evidence_review import draft_followup_preview

        evidence = ReviewedJobEvidence(
            job_id="job-20260402-goal",
            state="succeeded",
            lifecycle_state="done",
            terminal_outcome="succeeded",
            approval_status="",
            latest_summary="Scan completed.",
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
        payload = draft_followup_preview(evidence)
        goal = payload["goal"]
        assert "follow-up" in goal.lower()
        assert "inspect output details" not in goal.lower()
        assert "job-20260402-goal" in goal

    def test_failed_job_goal_mentions_corrected_retry(self) -> None:
        from voxera.vera.evidence_review import draft_followup_preview

        evidence = ReviewedJobEvidence(
            job_id="job-20260402-fail",
            state="failed",
            lifecycle_state="failed",
            terminal_outcome="failed",
            approval_status="",
            latest_summary="",
            failure_summary="Permission denied",
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
            normalized_outcome_class="policy_denied",
            value_forward_text="",
        )
        payload = draft_followup_preview(evidence)
        goal = payload["goal"]
        assert "corrected retry" in goal.lower()
        assert "Permission denied" in goal

    def test_canceled_job_goal_mentions_replacement(self) -> None:
        from voxera.vera.evidence_review import draft_followup_preview

        evidence = ReviewedJobEvidence(
            job_id="job-20260402-cancel",
            state="canceled",
            lifecycle_state="canceled",
            terminal_outcome="canceled",
            approval_status="",
            latest_summary="",
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
            normalized_outcome_class="",
            value_forward_text="",
        )
        payload = draft_followup_preview(evidence)
        goal = payload["goal"]
        assert "replacement" in goal.lower() or "canceled" in goal.lower()


# ---------------------------------------------------------------------------
# 12. Preview truth — "write me a short note about queue truth" regression
# ---------------------------------------------------------------------------


class TestPreviewTruthQueueTruthNote:
    """Regression: 'write me a short note about queue truth' must produce a
    preview with substantial authored content in write_file.content, not a
    short builder fragment like 'hallucination of success,'.

    Root cause: the builder LLM may produce a fragmentary content snippet
    from the authored text.  The writing-draft injection should override it
    with the full reply_text_draft, but pathological LLM response structures
    can cause the extraction to fail.  The post-binding guardrail ensures
    the final preview content is not a truncated fragment.
    """

    def test_queue_truth_note_preview_has_substantial_content(self, tmp_path, monkeypatch) -> None:
        session = make_vera_session(monkeypatch, tmp_path)

        authored_content = (
            "Queue truth in VoxeraOS refers to the principle that the queue is the "
            "single authoritative source of execution state. Every job progresses "
            "through well-defined lifecycle states and the queue state is the only "
            "surface that operators should trust for determining what has actually "
            "happened.\n\n"
            "This means that approval status, terminal outcome, and artifact evidence "
            "must all be grounded in persisted queue state, not in LLM-generated "
            "claims or conversational inference. The hallucination of success, "
            "progress, or completion without queue evidence is explicitly treated "
            "as a trust violation."
        )

        async def _fake_reply(*, turns, user_message, **kw):
            _ = turns
            return {"answer": authored_content, "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        res = session.chat("write me a short note about queue truth")
        assert res.status_code == 200

        preview = session.preview()
        assert preview is not None, "Preview must be created"
        assert "write_file" in preview
        wf = preview["write_file"]
        content = wf["content"]
        assert content, "write_file.content must be non-empty"
        assert len(content) > 100, (
            f"write_file.content must be substantial authored prose, got {len(content)} chars"
        )
        assert "queue truth" in content.lower() or "queue" in content.lower()
        # Must NOT be the builder fragment
        assert content != "hallucination of success,"

    def test_queue_truth_note_builder_fragment_overridden(self, tmp_path, monkeypatch) -> None:
        """When the builder produces a short fragment from the authored content,
        the writing-draft pipeline must override it with the full authored text."""
        session = make_vera_session(monkeypatch, tmp_path)

        authored_content = (
            "Queue truth in VoxeraOS refers to the principle that the queue is the "
            "single authoritative source of execution state. Every job progresses "
            "through well-defined lifecycle states and the queue state is the only "
            "surface that operators should trust for determining what has actually "
            "happened.\n\n"
            "This means that approval status, terminal outcome, and artifact evidence "
            "must all be grounded in persisted queue state, not in LLM-generated "
            "claims or conversational inference. The hallucination of success, "
            "progress, or completion without queue evidence is explicitly treated "
            "as a trust violation."
        )

        async def _fake_reply(*, turns, user_message, **kw):
            _ = turns
            return {"answer": authored_content, "status": "ok:test"}

        async def _fake_builder(**kw):
            return {
                "goal": "draft a explanation as queue-truth-explanation.txt",
                "write_file": {
                    "path": "~/VoxeraOS/notes/queue-truth-explanation.txt",
                    "content": "hallucination of success,",
                    "mode": "overwrite",
                },
            }

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
        monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _fake_builder)

        res = session.chat("write me a short note about queue truth")
        assert res.status_code == 200

        preview = session.preview()
        assert preview is not None, "Preview must be created"
        assert "write_file" in preview
        wf = preview["write_file"]
        content = wf["content"]
        assert content, "write_file.content must be non-empty"
        # Must NOT be the builder fragment
        assert content != "hallucination of success,", (
            "Preview must contain authored content, not builder fragment"
        )
        assert len(content) > 100, f"Preview content must be substantial, got {len(content)} chars"
        assert "queue truth" in content.lower()
        assert "trust violation" in content.lower()

    def test_queue_truth_note_assistant_wording_correct(self, tmp_path, monkeypatch) -> None:
        """Assistant wording must clearly state preview-only + nothing submitted."""
        session = make_vera_session(monkeypatch, tmp_path)

        authored_content = (
            "Queue truth in VoxeraOS refers to the principle that the queue is the "
            "single authoritative source of execution state."
        )

        async def _fake_reply(*, turns, user_message, **kw):
            _ = turns
            return {"answer": authored_content, "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        res = session.chat("write me a short note about queue truth")
        assert res.status_code == 200

        last_turn = session.turns()[-1]
        assert last_turn["role"] == "assistant"
        reply_text = last_turn["text"].lower()
        assert "preview-only" in reply_text
        assert "nothing has been submitted yet" in reply_text

    def test_preview_content_aligned_with_chat(self, tmp_path, monkeypatch) -> None:
        """Preview content and assistant chat reply must be materially aligned."""
        session = make_vera_session(monkeypatch, tmp_path)

        authored_content = (
            "Queue truth in VoxeraOS refers to the principle that the queue is the "
            "single authoritative source of execution state. Every job progresses "
            "through well-defined lifecycle states."
        )

        async def _fake_reply(*, turns, user_message, **kw):
            _ = turns
            return {"answer": authored_content, "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        res = session.chat("write me a short note about queue truth")
        assert res.status_code == 200

        preview = session.preview()
        assert preview is not None
        preview_content = preview["write_file"]["content"]

        last_turn = session.turns()[-1]
        assert last_turn["role"] == "assistant"
        assistant_text = last_turn["text"]

        # The preview content must be a meaningful portion of the authored content
        assert "queue truth" in preview_content.lower()
        # The assistant text should contain or reference the same content
        assert "queue truth" in assistant_text.lower() or preview_content in assistant_text

    def test_builder_fragment_overridden_even_when_extraction_fails(
        self, tmp_path, monkeypatch
    ) -> None:
        """Regression for the exact live failure: builder produces 'what is
        happening now.' as a fragment, and the LLM reply is structured in a
        way that extract_text_draft_from_reply returns None.  The guardrail
        must use the sanitized_answer fallback to override the fragment."""
        session = make_vera_session(monkeypatch, tmp_path)

        # Content that the LLM produces as good authored prose.
        # The extract_text_draft_from_reply might fail on certain LLM reply
        # structures, but the sanitized_answer should contain this text.
        authored_content = (
            "Queue truth in VoxeraOS refers to the principle that the queue is the "
            "single authoritative source of execution state. Every job progresses "
            "through well-defined lifecycle states and the queue state is the only "
            "surface that operators should trust for determining what is happening "
            "now.\n\n"
            "This means that approval status, terminal outcome, and artifact "
            "evidence must all be grounded in persisted queue state, not in "
            "LLM-generated claims or conversational inference."
        )

        async def _fake_reply(*, turns, user_message, **kw):
            _ = turns
            return {"answer": authored_content, "status": "ok:test"}

        # Builder produces a fragmentary snippet
        async def _fake_builder(**kw):
            return {
                "goal": "draft a explanation as queue-truth-explanation.txt",
                "write_file": {
                    "path": "~/VoxeraOS/notes/queue-truth-explanation.txt",
                    "content": "what is happening now.",
                    "mode": "overwrite",
                },
            }

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
        monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _fake_builder)

        res = session.chat("write me a short note about queue truth")
        assert res.status_code == 200

        preview = session.preview()
        assert preview is not None, "Preview must be created"
        assert "write_file" in preview
        wf = preview["write_file"]
        content = wf["content"]

        # The authoritative preview must NOT be the builder fragment
        assert content != "what is happening now.", (
            "Preview must not be builder fragment 'what is happening now.'"
        )
        assert len(content) > 50, (
            f"Preview content must be substantial, got {len(content)} chars: {content!r}"
        )
        assert "queue truth" in content.lower() or "queue" in content.lower()
