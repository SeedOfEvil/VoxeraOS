"""Live-path characterization tests for Vera evidence-grounded workflows.

Protects the strongest real user paths with regression coverage:

1. Natural drafting → preview truth binding
2. Preview prepared / updated / unchanged / submitted wording clarity
3. Linked-job result review (evidence-grounded)
4. Evidence-grounded follow-up preview preparation
5. Fail-closed behavior when no resolvable completed job exists
6. Explicit handoff wording after preview

These tests assert meaningful behavior and truth surfaces, not incidental strings.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from voxera.vera import service as vera_service
from voxera.vera.evidence_review import (
    ReviewedJobEvidence,
    draft_followup_preview,
    draft_revised_preview,
    draft_saveable_followup_preview,
    is_followup_preview_request,
    is_review_request,
    is_revise_from_evidence_request,
    is_save_followup_request,
    review_message,
)
from voxera.vera_web import app as vera_app_module
from voxera.vera_web.chat_early_exit_dispatch import dispatch_early_exit_intent
from voxera.vera_web.response_shaping import assemble_assistant_reply

from .vera_session_helpers import make_vera_session

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_PROSE_PREVIEW = {
    "goal": "save note",
    "write_file": {"path": "~/VoxeraOS/notes/foo.md", "content": "hello world"},
}


def _base_assemble_kwargs(**overrides):
    base = dict(
        message="tell me about the weather",
        pending_preview=None,
        builder_payload=None,
        in_voxera_preview_flow=False,
        is_code_draft_turn=False,
        is_writing_draft_turn=False,
        is_enrichment_turn=False,
        conversational_answer_first_turn=False,
        is_json_content_request=False,
        is_voxera_control_turn=False,
        explicit_targeted_content_refinement=False,
        preview_update_rejected=False,
        generation_content_refresh_failed_closed=False,
        reply_status="ok:conversational",
    )
    base.update(overrides)
    return base


def _dispatch(
    *,
    message: str,
    queue_root: Path,
    session_id: str = "test-session",
    diagnostics_service_turn: bool = False,
    requested_job_id: str | None = None,
    should_attempt_derived_save: bool = False,
    session_investigation: dict[str, object] | None = None,
    session_derived_output: dict[str, object] | None = None,
    session_context: dict[str, object] | None = None,
):
    return dispatch_early_exit_intent(
        message=message,
        diagnostics_service_turn=diagnostics_service_turn,
        requested_job_id=requested_job_id,
        should_attempt_derived_save=should_attempt_derived_save,
        session_investigation=session_investigation,
        session_derived_output=session_derived_output,
        queue_root=queue_root,
        session_id=session_id,
        session_context=session_context,
    )


def _make_evidence(
    *,
    job_id: str = "job-20260401-abc123",
    state: str = "succeeded",
    terminal_outcome: str = "succeeded",
    latest_summary: str = "Completed successfully.",
    failure_summary: str = "",
    lifecycle_state: str = "done",
    normalized_outcome_class: str = "success",
) -> ReviewedJobEvidence:
    return ReviewedJobEvidence(
        job_id=job_id,
        state=state,
        lifecycle_state=lifecycle_state,
        terminal_outcome=terminal_outcome,
        approval_status="",
        latest_summary=latest_summary,
        failure_summary=failure_summary,
        artifact_families=("execution_result",),
        artifact_refs=("execution_result:execution_result.json",),
        evidence_trace=(
            f"lifecycle_state={lifecycle_state}",
            f"terminal_outcome={terminal_outcome}",
        ),
        child_summary=None,
        execution_capabilities=None,
        capability_boundary_violation=None,
        expected_artifacts=(),
        observed_expected_artifacts=(),
        missing_expected_artifacts=(),
        expected_artifact_status="",
        normalized_outcome_class=normalized_outcome_class,
        value_forward_text="",
    )


# ---------------------------------------------------------------------------
# 1. Natural drafting → preview truth binding
# ---------------------------------------------------------------------------


class TestNaturalDraftingPreviewTruth:
    """Verify that natural drafting flows bind authored content to preview truth."""

    def test_write_me_a_note_about_artifact_evidence_creates_preview(
        self, tmp_path, monkeypatch
    ) -> None:
        """Live-path: 'write me a note about the artifact evidence model'."""
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message, **kw):
            return {
                "answer": (
                    "The artifact evidence model in VoxeraOS tracks execution outcomes, "
                    "attaches structured results as artifacts, and provides operators "
                    "with canonical truth about what ran and what it produced."
                ),
                "status": "ok:test",
            }

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        res = session.chat("write me a note about the artifact evidence model")
        assert res.status_code == 200

        preview = session.preview()
        assert preview is not None, "Preview must exist after drafting request"
        assert "write_file" in preview
        content = preview["write_file"]["content"]
        # Authored content must reach the preview — not a control message
        assert "artifact evidence model" in content.lower()
        assert "execution outcomes" in content.lower()
        # Must NOT contain control-plane text
        assert "nothing has been submitted" not in content.lower()
        assert "prepared a preview" not in content.lower()

    def test_make_it_shorter_updates_preview_content(self, tmp_path, monkeypatch) -> None:
        """Live-path: 'make it shorter and more operator-facing' after initial draft."""
        session = make_vera_session(monkeypatch, tmp_path)
        call_count = {"n": 0}

        async def _fake_reply(*, turns, user_message, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {
                    "answer": (
                        "The artifact evidence model in VoxeraOS tracks execution outcomes, "
                        "attaches structured results as artifacts, and provides operators "
                        "with canonical truth about what ran and what it produced."
                    ),
                    "status": "ok:test",
                }
            return {
                "answer": (
                    "VoxeraOS artifacts give operators canonical proof of execution "
                    "outcomes and structured results."
                ),
                "status": "ok:test",
            }

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        session.chat("write me a note about the artifact evidence model")
        first_preview = session.preview()
        assert first_preview is not None

        session.chat("make it shorter and more operator-facing")
        updated_preview = session.preview()
        assert updated_preview is not None
        updated_content = updated_preview["write_file"]["content"]
        # Updated content should reflect the refinement
        assert "operators" in updated_content.lower() or "canonical" in updated_content.lower()
        # Should be shorter than original or at least different
        assert updated_content != first_preview["write_file"]["content"]

    def test_save_as_named_file_preserves_content_and_path(self, tmp_path, monkeypatch) -> None:
        """Live-path: 'save it as artifact-evidence-operator-note.md' after drafting."""
        session = make_vera_session(monkeypatch, tmp_path)
        call_count = {"n": 0}

        async def _fake_reply(*, turns, user_message, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {
                    "answer": (
                        "VoxeraOS artifacts give operators canonical proof of execution "
                        "outcomes and structured results."
                    ),
                    "status": "ok:test",
                }
            return {"answer": "ok", "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        session.chat("write me a note about the artifact evidence model")
        session.chat("save it as artifact-evidence-operator-note.md")

        preview = session.preview()
        assert preview is not None
        wf = preview["write_file"]
        assert wf["path"] == "~/VoxeraOS/notes/artifact-evidence-operator-note.md"
        # Content must survive the rename — not get replaced by control text
        assert len(wf["content"]) > 10
        assert "nothing has been submitted" not in wf["content"].lower()


# ---------------------------------------------------------------------------
# 2. Preview wording: prepared vs updated vs unchanged vs submitted
# ---------------------------------------------------------------------------


class TestPreviewWordingLivePaths:
    """Verify that preview-state wording is truthful for each transition."""

    def test_first_draft_says_prepared_and_preview_only(self) -> None:
        """New preview (no prior) → 'prepared' + 'preview-only' + 'not submitted'."""
        result = assemble_assistant_reply(
            "I set up a preview for you.",
            **_base_assemble_kwargs(
                message="write me a note about the artifact evidence model",
                builder_payload=_PROSE_PREVIEW,
                pending_preview=None,
                is_writing_draft_turn=True,
                in_voxera_preview_flow=True,
            ),
        )
        text = result.assistant_text.lower()
        assert "prepared" in text
        assert "preview-only" in text
        assert "nothing has been submitted yet" in text

    def test_refinement_says_updated_and_preview_only(self) -> None:
        """Update to existing preview → 'updated' + 'preview-only' + 'not submitted'."""
        result = assemble_assistant_reply(
            "Here is the shorter version.",
            **_base_assemble_kwargs(
                message="make it shorter and more operator-facing",
                builder_payload=_PROSE_PREVIEW,
                pending_preview=_PROSE_PREVIEW,
                is_writing_draft_turn=True,
                in_voxera_preview_flow=True,
            ),
        )
        text = result.assistant_text.lower()
        assert "updated" in text
        assert "preview-only" in text
        assert "nothing has been submitted yet" in text

    def test_unchanged_preview_does_not_claim_update(self) -> None:
        """When builder_payload is None (no update), wording must not claim an update."""
        result = assemble_assistant_reply(
            "Sure, the note looks good.",
            **_base_assemble_kwargs(
                message="looks good",
                builder_payload=None,
                pending_preview=_PROSE_PREVIEW,
            ),
        )
        text = result.assistant_text.lower()
        # When no builder_payload exists, the reply must NOT use the governed
        # "I've updated the preview" phrasing (reserved for builder_payload=set).
        # Check both curly-quote (\u2019) and straight-quote variants because
        # response shaping emits curly quotes but tests may normalize differently.
        assert "i\u2019ve updated the preview" not in text
        assert "i've updated the preview" not in text
        # Status must NOT be "prepared_preview" — nothing was prepared
        assert result.status != "prepared_preview"

    def test_explicit_handoff_wording_after_submit(self, tmp_path, monkeypatch) -> None:
        """After submit, the queue should have a job and preview should be cleared."""
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message, **kw):
            return {
                "answer": "VoxeraOS tracks execution outcomes with artifacts.",
                "status": "ok:test",
            }

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        session.chat("write me a note about artifacts")
        session.chat("save that to a note")
        preview_before = session.preview()
        assert preview_before is not None

        submit = session.chat("submit it")
        assert submit.status_code == 200

        # Preview must be cleared after submit
        preview_after = session.preview()
        assert preview_after is None

        # Job must exist in inbox
        inbox_files = list((session.queue / "inbox").glob("*.json"))
        assert len(inbox_files) == 1

        # Submitted job must contain authored content, not control text
        payload = json.loads(inbox_files[0].read_text(encoding="utf-8"))
        assert "write_file" in payload
        content = payload["write_file"]["content"]
        assert "execution outcomes" in content.lower()


# ---------------------------------------------------------------------------
# 3. Linked-job result review (evidence-grounded)
# ---------------------------------------------------------------------------


class TestLinkedJobReviewLivePaths:
    """Verify that linked-job review is evidence-grounded, not LLM-fabricated."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "summarize the result",
            "inspect output details",
            "what happened",
            "did it work",
            "review the result",
            "what was the outcome",
        ],
    )
    def test_review_phrases_are_recognized(self, phrase: str) -> None:
        assert is_review_request(phrase), f"Expected review request: {phrase!r}"

    def test_review_with_evidence_returns_grounded_message(self, tmp_path) -> None:
        """Review dispatch with resolvable evidence must return grounded review."""
        evidence = _make_evidence(
            latest_summary="Wrote artifact-evidence-operator-note.md successfully.",
        )
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=evidence,
        ) as mock_review:
            result = _dispatch(
                message="summarize the result",
                queue_root=tmp_path,
                session_context={"last_completed_job_ref": "job-20260401-abc123"},
            )
        mock_review.assert_called_once()
        assert result.matched is True
        assert result.status == "reviewed_job_outcome"
        # Must contain evidence-grounded fields
        assert evidence.job_id in result.assistant_text
        assert "succeeded" in result.assistant_text.lower()
        assert evidence.latest_summary in result.assistant_text
        # Must NOT set write flags (review is read-only)
        assert result.write_preview is False
        assert result.write_handoff_ready is False

    def test_review_message_surfaces_canonical_fields(self) -> None:
        """review_message must surface job_id, state, outcome, and summary."""
        evidence = _make_evidence(
            job_id="job-20260401-review123",
            latest_summary="Deployed config changes to staging.",
        )
        msg = review_message(evidence)
        assert "job-20260401-review123" in msg
        assert "succeeded" in msg.lower()
        assert "Deployed config changes to staging." in msg
        assert "Lifecycle state" in msg or "lifecycle_state" in msg.lower()
        assert "Terminal outcome" in msg or "terminal_outcome" in msg.lower()

    def test_review_failed_job_surfaces_failure_summary(self) -> None:
        evidence = _make_evidence(
            state="failed",
            terminal_outcome="failed",
            latest_summary="",
            failure_summary="Permission denied: /etc/config",
            normalized_outcome_class="runtime_error",
        )
        msg = review_message(evidence)
        assert "Permission denied" in msg
        assert "failed" in msg.lower()

    def test_inspect_output_details_via_dispatch(self, tmp_path) -> None:
        """'inspect output details' must reach the review branch and return evidence."""
        evidence = _make_evidence(
            latest_summary="Scanned 42 files, found 3 issues.",
        )
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=evidence,
        ) as mock_review:
            result = _dispatch(
                message="inspect output details",
                queue_root=tmp_path,
                session_context={"last_completed_job_ref": "job-20260401-abc123"},
            )
        mock_review.assert_called_once()
        assert result.matched is True
        assert result.status == "reviewed_job_outcome"
        assert "42 files" in result.assistant_text


# ---------------------------------------------------------------------------
# 4. Evidence-grounded follow-up preview preparation
# ---------------------------------------------------------------------------


class TestFollowUpPreviewLivePaths:
    """Verify follow-up preview preparation is grounded in real evidence."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "now prepare the follow-up",
            "what should we do next based on that",
            "draft a follow-up",
            "prepare the follow-up",
            "based on that result",
        ],
    )
    def test_followup_phrases_are_recognized(self, phrase: str) -> None:
        assert is_followup_preview_request(phrase), f"Expected followup: {phrase!r}"

    def test_followup_with_succeeded_evidence_prepares_grounded_preview(self, tmp_path) -> None:
        """Follow-up dispatch with succeeded evidence must return a grounded preview."""
        evidence = _make_evidence(
            job_id="job-20260401-followup1",
            latest_summary="Applied security patches to 5 hosts.",
        )
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=evidence,
        ) as mock_review:
            result = _dispatch(
                message="now prepare the follow-up",
                queue_root=tmp_path,
                session_context={"last_completed_job_ref": "job-20260401-followup1"},
            )
        mock_review.assert_called_once()
        assert result.matched is True
        assert result.status == "followup_preview_ready"
        assert result.write_preview is True
        assert result.write_handoff_ready is True
        assert result.preview_payload is not None
        # Follow-up text must be grounded in evidence
        assert "job-20260401-followup1" in result.assistant_text
        assert "preview-only" in result.assistant_text.lower()
        assert "nothing has been submitted yet" in result.assistant_text.lower()
        # Evidence detail must appear
        assert "succeeded" in result.assistant_text.lower()

    def test_followup_preview_goal_references_prior_job(self) -> None:
        """draft_followup_preview must produce a goal grounded in the prior job."""
        evidence = _make_evidence(
            job_id="job-20260401-goaltest",
            latest_summary="Completed backup rotation.",
        )
        payload = draft_followup_preview(evidence)
        assert "goal" in payload
        goal = payload["goal"]
        assert "job-20260401-goaltest" in goal
        # Must reference evidence, not be generic
        assert "follow-up" in goal.lower() or "grounded" in goal.lower()

    def test_followup_for_failed_job_references_failure(self) -> None:
        """Follow-up for a failed job must reference the failure for correction."""
        evidence = _make_evidence(
            state="failed",
            terminal_outcome="failed",
            failure_summary="Disk full on /var/log",
            latest_summary="",
            normalized_outcome_class="runtime_error",
        )
        payload = draft_followup_preview(evidence)
        goal = payload["goal"]
        # Failed follow-up goal must include both: reference to the failure summary
        # and a correction/retry intent. Current output:
        # "prepare a corrected retry for job-test after addressing: Disk full on /var/log"
        assert "Disk full" in goal, "Goal must include the failure summary verbatim"
        assert evidence.job_id in goal, "Goal must reference the job id"

    def test_what_should_we_do_next_based_on_that_reaches_followup(self, tmp_path) -> None:
        """Natural phrasing 'what should we do next based on that' must reach followup."""
        evidence = _make_evidence()
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=evidence,
        ) as mock_review:
            result = _dispatch(
                message="what should we do next based on that",
                queue_root=tmp_path,
                session_context={"last_completed_job_ref": "job-20260401-abc123"},
            )
        mock_review.assert_called_once()
        assert result.matched is True
        assert result.status == "followup_preview_ready"
        assert result.write_preview is True


# ---------------------------------------------------------------------------
# 5. Fail-closed behavior: no resolvable completed job
# ---------------------------------------------------------------------------


class TestFailClosedNoResolvableJob:
    """Verify fail-closed behavior when evidence cannot be resolved."""

    def test_review_fails_closed_with_no_linked_job(self, tmp_path) -> None:
        """'summarize the result' with no job context fails closed honestly."""
        result = _dispatch(
            message="summarize the result",
            queue_root=tmp_path,
        )
        assert result.matched is True
        assert result.status == "review_missing_job"

    def test_followup_fails_closed_with_no_linked_job(self, tmp_path) -> None:
        """'now prepare the follow-up' with no job context fails closed honestly."""
        result = _dispatch(
            message="now prepare the follow-up",
            queue_root=tmp_path,
        )
        assert result.matched is True
        assert result.status == "followup_missing_evidence"

    def test_inspect_output_details_fails_closed_with_no_job(self, tmp_path) -> None:
        """'inspect output details' with no job context fails closed honestly."""
        result = _dispatch(
            message="inspect output details",
            queue_root=tmp_path,
        )
        assert result.matched is True
        assert result.status == "review_missing_job"

    def test_what_should_we_do_next_fails_closed_with_no_job(self, tmp_path) -> None:
        """'what should we do next based on that' with no job context fails closed."""
        result = _dispatch(
            message="what should we do next based on that",
            queue_root=tmp_path,
        )
        assert result.matched is True
        assert result.status == "followup_missing_evidence"

    @pytest.mark.parametrize(
        "phrase",
        [
            "summarize the result",
            "inspect output details",
            "review the result",
            "what was the outcome",
            "did it work",
        ],
    )
    def test_review_fails_closed_without_job_context(self, tmp_path, phrase: str) -> None:
        result = _dispatch(message=phrase, queue_root=tmp_path)
        assert result.matched is True
        assert result.status == "review_missing_job"

    @pytest.mark.parametrize(
        "phrase",
        [
            "now prepare the follow-up",
            "draft the follow-up",
            "what should we do next based on that",
            "based on that result",
        ],
    )
    def test_followup_fails_closed_without_job_context(self, tmp_path, phrase: str) -> None:
        result = _dispatch(message=phrase, queue_root=tmp_path)
        assert result.matched is True
        assert result.status == "followup_missing_evidence"


# ---------------------------------------------------------------------------
# 6. Session-level evidence review and follow-up integration
# ---------------------------------------------------------------------------


class TestSessionLevelEvidenceReviewFollowUp:
    """Session-level tests verifying the full chat → dispatch → reply path
    for linked-job review and follow-up flows."""

    @staticmethod
    def _seed_completed_job(
        session, *, stem: str, latest_summary: str, goal: str = "test goal"
    ) -> str:
        """Seed a completed job with artifacts and link it to the session.

        Returns the job_id string.
        """
        job_id = f"{stem}.json"
        bucket_dir = session.queue / "done"
        bucket_dir.mkdir(parents=True, exist_ok=True)
        (bucket_dir / job_id).write_text(json.dumps({"goal": goal}), encoding="utf-8")
        art = session.queue / "artifacts" / stem
        art.mkdir(parents=True, exist_ok=True)
        (art / "execution_result.json").write_text(
            json.dumps(
                {
                    "lifecycle_state": "done",
                    "terminal_outcome": "succeeded",
                    "review_summary": {
                        "latest_summary": latest_summary,
                        "terminal_outcome": "succeeded",
                    },
                }
            ),
            encoding="utf-8",
        )
        vera_service.write_session_handoff_state(
            session.queue,
            session.session_id,
            attempted=True,
            queue_path=str(bucket_dir / job_id),
            status="submitted",
            job_id=job_id,
        )
        return job_id

    def test_review_request_in_session_returns_evidence_grounded_reply(
        self, tmp_path, monkeypatch
    ) -> None:
        """Chat 'summarize the result' with a linked job returns evidence, not LLM."""
        session = make_vera_session(monkeypatch, tmp_path)
        self._seed_completed_job(
            session,
            stem="job-20260401-session1",
            latest_summary="Wrote operator note successfully.",
        )

        async def _fake_reply(*, turns, user_message, **kw):
            return {"answer": "fallback", "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        res = session.chat("summarize the result")
        assert res.status_code == 200

        # The reply must come from evidence, not the LLM fallback
        last_turn = session.turns()[-1]["text"]
        assert "Wrote operator note successfully." in last_turn
        assert "fallback" not in last_turn

    def test_followup_request_in_session_creates_preview_from_evidence(
        self, tmp_path, monkeypatch
    ) -> None:
        """Chat 'now prepare the follow-up' with linked job creates evidence-grounded preview."""
        session = make_vera_session(monkeypatch, tmp_path)
        self._seed_completed_job(
            session,
            stem="job-20260401-session2",
            latest_summary="Config deployed to staging.",
            goal="deploy config",
        )

        async def _fake_reply(*, turns, user_message, **kw):
            return {"answer": "fallback", "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        res = session.chat("now prepare the follow-up")
        assert res.status_code == 200

        # A preview must now exist, grounded in the prior job
        preview = session.preview()
        assert preview is not None, "Follow-up must create a preview"
        assert "goal" in preview
        goal = preview["goal"]
        assert "job-20260401-session2" in goal or "follow-up" in goal.lower()

        # Last turn must communicate preview-only semantics and no submission.
        # Check exact governed phrase rather than loose word presence.
        last_turn = session.turns()[-1]["text"].lower()
        assert "preview-only" in last_turn
        assert "nothing has been submitted" in last_turn
        # Must NOT use LLM fallback text
        assert "fallback" not in last_turn

    def test_review_request_with_no_linked_job_fails_closed_in_session(
        self, tmp_path, monkeypatch
    ) -> None:
        """Chat 'summarize the result' with no linked job fails closed honestly."""
        session = make_vera_session(monkeypatch, tmp_path)

        res = session.chat("summarize the result")
        assert res.status_code == 200

        last_turn = session.turns()[-1]["text"].lower()
        # Without job context, the review branch fails closed with honest message
        assert "could not resolve" in last_turn

    def test_followup_with_no_linked_job_fails_closed_in_session(
        self, tmp_path, monkeypatch
    ) -> None:
        """Chat 'now prepare the follow-up' with no linked job fails closed honestly."""
        session = make_vera_session(monkeypatch, tmp_path)

        res = session.chat("now prepare the follow-up")
        assert res.status_code == 200

        last_turn = session.turns()[-1]["text"].lower()
        # Without job context, the followup branch fails closed with honest message
        assert "follow-up preview" in last_turn


# ---------------------------------------------------------------------------
# 7. Revise/update from completed job evidence
# ---------------------------------------------------------------------------


class TestReviseFromEvidenceLivePaths:
    """Verify revise/update workflows produce revision-oriented previews grounded in evidence."""

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
        ],
    )
    def test_revise_phrases_are_recognized(self, phrase: str) -> None:
        assert is_revise_from_evidence_request(phrase), f"Expected revise request: {phrase!r}"
        # All revise phrases must also be recognized as follow-up requests
        assert is_followup_preview_request(phrase), f"Expected followup superset: {phrase!r}"

    def test_revise_produces_revision_oriented_goal(self) -> None:
        """draft_revised_preview must produce a goal with 'revise' intent, not generic follow-up."""
        evidence = _make_evidence(
            job_id="job-20260401-rev1",
            latest_summary="Applied config changes to prod.",
        )
        payload = draft_revised_preview(evidence)
        goal = payload["goal"]
        assert "revise" in goal.lower()
        assert "job-20260401-rev1" in goal
        assert "Applied config changes to prod." in goal

    def test_revise_failed_job_references_failure(self) -> None:
        """Revision for a failed job must reference the failure in the goal."""
        evidence = _make_evidence(
            state="failed",
            terminal_outcome="failed",
            failure_summary="Connection refused on port 5432",
            latest_summary="",
            normalized_outcome_class="runtime_error",
        )
        payload = draft_revised_preview(evidence)
        goal = payload["goal"]
        assert "revise" in goal.lower()
        assert "Connection refused" in goal

    def test_revise_dispatch_with_evidence_returns_revised_status(self, tmp_path) -> None:
        """Revise dispatch with resolvable evidence must return revised_preview_ready."""
        evidence = _make_evidence(
            job_id="job-20260401-revdisp",
            latest_summary="Deployed hotfix.",
        )
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=evidence,
        ):
            result = _dispatch(
                message="revise that based on the result",
                queue_root=tmp_path,
                session_context={"last_completed_job_ref": "job-20260401-revdisp"},
            )
        assert result.matched is True
        assert result.status == "revised_preview_ready"
        assert result.write_preview is True
        assert result.write_handoff_ready is True
        assert "preview-only" in result.assistant_text.lower()
        assert "nothing has been submitted yet" in result.assistant_text.lower()
        # Goal must be revision-oriented
        goal = str(result.preview_payload.get("goal") or "")
        assert "revise" in goal.lower()

    def test_update_dispatch_with_evidence_returns_revised_status(self, tmp_path) -> None:
        """'update that based on the result' must also route to revised_preview_ready."""
        evidence = _make_evidence(
            job_id="job-20260401-upd",
            latest_summary="Scan completed.",
        )
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=evidence,
        ):
            result = _dispatch(
                message="update that based on the result",
                queue_root=tmp_path,
                session_context={"last_completed_job_ref": "job-20260401-upd"},
            )
        assert result.matched is True
        assert result.status == "revised_preview_ready"
        assert result.preview_payload is not None

    @pytest.mark.parametrize(
        "phrase",
        [
            "revise that based on the result",
            "revise based on evidence",
            "update that based on the result",
        ],
    )
    def test_revise_fails_closed_with_no_job(self, tmp_path, phrase: str) -> None:
        """Revise requests without job context fail closed honestly."""
        result = _dispatch(message=phrase, queue_root=tmp_path)
        assert result.matched is True
        assert result.status == "followup_missing_evidence"

    def test_revise_preview_payload_is_coherent(self) -> None:
        """Revised preview payload must have a goal, no write_file (bare revision intent)."""
        evidence = _make_evidence(
            job_id="job-20260401-coh",
            latest_summary="Completed analysis.",
        )
        payload = draft_revised_preview(evidence)
        assert "goal" in payload
        # Revised preview is a bare goal (not a write_file), same as generic follow-up
        assert "write_file" not in payload

    def test_revise_chat_and_preview_truth_alignment(self, tmp_path) -> None:
        """Chat text and preview goal must both reference revision intent and evidence."""
        evidence = _make_evidence(
            job_id="job-20260401-align",
            latest_summary="Metrics collected.",
        )
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=evidence,
        ):
            result = _dispatch(
                message="revise that based on the result",
                queue_root=tmp_path,
                session_context={"last_completed_job_ref": "job-20260401-align"},
            )
        # Chat text says "revised preview"
        assert "revised preview" in result.assistant_text.lower()
        # Preview goal says "revise"
        goal = str(result.preview_payload.get("goal") or "")
        assert "revise" in goal.lower()
        # Both reference the same job
        assert "job-20260401-align" in result.assistant_text
        assert "job-20260401-align" in goal


# ---------------------------------------------------------------------------
# 8. Save-follow-up from completed job evidence
# ---------------------------------------------------------------------------


class TestSaveFollowUpLivePaths:
    """Verify save-follow-up workflows produce saveable previews with write_file."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "save the follow-up",
            "save that follow-up",
            "save the follow-up as a file",
        ],
    )
    def test_save_followup_phrases_are_recognized(self, phrase: str) -> None:
        assert is_save_followup_request(phrase), f"Expected save-followup: {phrase!r}"
        # All save phrases must also be recognized as follow-up requests
        assert is_followup_preview_request(phrase), f"Expected followup superset: {phrase!r}"

    def test_saveable_preview_has_write_file_with_content(self) -> None:
        """draft_saveable_followup_preview must produce a preview with write_file."""
        evidence = _make_evidence(
            job_id="job-20260401-sav1",
            latest_summary="Built and deployed service.",
        )
        payload = draft_saveable_followup_preview(evidence)
        assert "goal" in payload
        assert "write_file" in payload
        wf = payload["write_file"]
        assert wf["content"]
        assert wf["path"]
        # Content must reference the job
        assert "job-20260401-sav1" in wf["content"]
        assert "Built and deployed service." in wf["content"]
        # Path must be a followup note
        assert "followup-" in wf["path"]

    def test_saveable_preview_for_failed_job_includes_failure(self) -> None:
        """Save-follow-up for a failed job must reference the failure in content."""
        evidence = _make_evidence(
            state="failed",
            terminal_outcome="failed",
            failure_summary="Timeout after 300s",
            latest_summary="",
            normalized_outcome_class="runtime_error",
        )
        payload = draft_saveable_followup_preview(evidence)
        content = payload["write_file"]["content"]
        assert "Timeout after 300s" in content
        assert "failed" in content.lower()
        # Goal should indicate corrective intent
        goal = payload["goal"]
        assert "corrective" in goal.lower() or "failed" in goal.lower()

    def test_save_followup_dispatch_returns_saveable_status(self, tmp_path) -> None:
        """Save-follow-up dispatch must return save_followup_preview_ready."""
        evidence = _make_evidence(
            job_id="job-20260401-savdisp",
            latest_summary="Backup completed.",
        )
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=evidence,
        ):
            result = _dispatch(
                message="save the follow-up",
                queue_root=tmp_path,
                session_context={"last_completed_job_ref": "job-20260401-savdisp"},
            )
        assert result.matched is True
        assert result.status == "save_followup_preview_ready"
        assert result.write_preview is True
        assert result.write_handoff_ready is True
        assert result.preview_payload is not None
        assert "write_file" in result.preview_payload
        assert "preview-only" in result.assistant_text.lower()
        assert "nothing has been submitted yet" in result.assistant_text.lower()

    def test_save_followup_as_file_dispatch(self, tmp_path) -> None:
        """'save the follow-up as a file' must produce a file-oriented saveable preview."""
        evidence = _make_evidence(
            job_id="job-20260401-savfile",
            latest_summary="Report generated.",
        )
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=evidence,
        ):
            result = _dispatch(
                message="save the follow-up as a file",
                queue_root=tmp_path,
                session_context={"last_completed_job_ref": "job-20260401-savfile"},
            )
        assert result.matched is True
        assert result.status == "save_followup_preview_ready"
        wf = result.preview_payload["write_file"]
        assert wf["path"].endswith(".md")
        content = str(wf["content"])
        assert "job-20260401-savfile" in content

    @pytest.mark.parametrize(
        "phrase",
        [
            "save the follow-up",
            "save that follow-up",
            "save the follow-up as a file",
        ],
    )
    def test_save_followup_fails_closed_with_no_job(self, tmp_path, phrase: str) -> None:
        """Save-follow-up without job context fails closed honestly."""
        result = _dispatch(message=phrase, queue_root=tmp_path)
        assert result.matched is True, f"Phrase {phrase!r} should fail closed without job context"
        assert result.status == "followup_missing_evidence", (
            f"Phrase {phrase!r} should fail closed with followup_missing_evidence"
        )

    def test_save_followup_chat_and_preview_truth_alignment(self, tmp_path) -> None:
        """Chat text and preview content must both be evidence-grounded and aligned."""
        evidence = _make_evidence(
            job_id="job-20260401-salign",
            latest_summary="Cleanup ran successfully.",
        )
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=evidence,
        ):
            result = _dispatch(
                message="save the follow-up",
                queue_root=tmp_path,
                session_context={"last_completed_job_ref": "job-20260401-salign"},
            )
        # Chat references the job
        assert "job-20260401-salign" in result.assistant_text
        # Preview content references the job and summary
        content = str(result.preview_payload["write_file"]["content"])
        assert "job-20260401-salign" in content
        assert "Cleanup ran successfully." in content
        # Chat says "saveable follow-up draft"
        assert "saveable follow-up draft" in result.assistant_text.lower()

    def test_save_followup_session_level_creates_write_file_preview(
        self, tmp_path, monkeypatch
    ) -> None:
        """Full session: 'save the follow-up' with linked job creates write_file preview."""
        session = make_vera_session(monkeypatch, tmp_path)
        TestSessionLevelEvidenceReviewFollowUp._seed_completed_job(
            session,
            stem="job-20260401-sessav",
            latest_summary="Deployed to production.",
        )

        async def _fake_reply(*, turns, user_message, **kw):
            return {"answer": "fallback", "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        res = session.chat("save the follow-up")
        assert res.status_code == 200

        preview = session.preview()
        assert preview is not None, "Save-follow-up must create a preview"
        assert "write_file" in preview
        content = preview["write_file"]["content"]
        assert "job-20260401-sessav" in content
        assert "Deployed to production." in content

        last_turn = session.turns()[-1]["text"].lower()
        assert "preview-only" in last_turn
        assert "nothing has been submitted" in last_turn
        assert "fallback" not in last_turn

    def test_revise_session_level_creates_revision_preview(self, tmp_path, monkeypatch) -> None:
        """Full session: 'revise that based on the result' with linked job creates revision preview."""
        session = make_vera_session(monkeypatch, tmp_path)
        TestSessionLevelEvidenceReviewFollowUp._seed_completed_job(
            session,
            stem="job-20260401-sesrev",
            latest_summary="Analysis completed.",
        )

        async def _fake_reply(*, turns, user_message, **kw):
            return {"answer": "fallback", "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        res = session.chat("revise that based on the result")
        assert res.status_code == 200

        preview = session.preview()
        assert preview is not None, "Revise must create a preview"
        assert "goal" in preview
        goal = preview["goal"]
        assert "revise" in goal.lower()

        last_turn = session.turns()[-1]["text"].lower()
        assert "preview-only" in last_turn
        assert "nothing has been submitted" in last_turn
        assert "fallback" not in last_turn
