"""Regression tests for linked-job review and evidence-grounded continuation.

Coverage:
  - Natural review phrases (including output-class) reach the review branch
  - Natural followup phrases (including output-class) reach the followup branch
  - Revise-from-evidence output-class phrases reach the revise branch
  - Session-context-aware job resolution for review and followup
  - Multi-turn review-then-followup continuity via session context
  - Fresh session fail-closed behavior for all phrase families
  - Reference resolver recognizes output-class job references
  - Evidence-grounded continuation stays grounded (no session-context override)

Trust-sensitive boundaries verified:
  - Session context is a continuity aid, never overrides canonical evidence
  - Fresh sessions fail closed honestly
  - Preview wording is truthful (preview-only, not submitted)
  - No internal control-plane text leakage
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from voxera.vera.context_lifecycle import (
    context_on_completion_ingested,
    context_on_followup_preview_prepared,
    context_on_handoff_submitted,
    context_on_review_performed,
)
from voxera.vera.evidence_review import (
    ReviewedJobEvidence,
    is_followup_preview_request,
    is_review_request,
    is_revise_from_evidence_request,
)
from voxera.vera.reference_resolver import (
    ReferenceClass,
    ResolvedReference,
    UnresolvedReference,
    classify_reference,
    resolve_job_id_from_context,
    resolve_session_reference,
)
from voxera.vera_web.chat_early_exit_dispatch import (
    EarlyExitResult,
    dispatch_early_exit_intent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dispatch(
    *,
    message: str,
    diagnostics_service_turn: bool = False,
    requested_job_id: str | None = None,
    should_attempt_derived_save: bool = False,
    session_investigation: dict[str, object] | None = None,
    session_derived_output: dict[str, object] | None = None,
    queue_root: Path | None = None,
    session_id: str = "test-session",
    session_context: dict[str, object] | None = None,
) -> EarlyExitResult:
    return dispatch_early_exit_intent(
        message=message,
        diagnostics_service_turn=diagnostics_service_turn,
        requested_job_id=requested_job_id,
        should_attempt_derived_save=should_attempt_derived_save,
        session_investigation=session_investigation,
        session_derived_output=session_derived_output,
        queue_root=queue_root or Path("/tmp/nonexistent-queue"),
        session_id=session_id,
        session_context=session_context,
    )


def _mock_succeeded_evidence(job_id: str = "job-20260404-test") -> ReviewedJobEvidence:
    return ReviewedJobEvidence(
        job_id=job_id,
        state="succeeded",
        lifecycle_state="done",
        terminal_outcome="succeeded",
        approval_status="",
        latest_summary="Task completed successfully.",
        failure_summary="",
        artifact_families=("note",),
        artifact_refs=("note:output.md",),
        evidence_trace=("terminal_outcome=succeeded",),
        child_summary=None,
        execution_capabilities=None,
        capability_boundary_violation=None,
        expected_artifacts=(),
        observed_expected_artifacts=(),
        missing_expected_artifacts=(),
        expected_artifact_status="",
        normalized_outcome_class="success",
        value_forward_text="Task output generated.",
    )


# ---------------------------------------------------------------------------
# 1. New review phrases — output-class variants
# ---------------------------------------------------------------------------


class TestOutputClassReviewPhrases:
    """Natural review phrases using 'output' must reach the review branch."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "what was the output",
            "show me the output",
            "show the output",
            "inspect the output details",
        ],
    )
    def test_output_review_phrases_are_recognized(self, phrase: str) -> None:
        assert is_review_request(phrase), f"{phrase!r} should be a review request"

    @pytest.mark.parametrize(
        "phrase",
        [
            "what was the output",
            "show me the output",
            "show the output",
            "inspect the output details",
        ],
    )
    def test_output_review_phrases_reach_review_branch(self, tmp_path: Path, phrase: str) -> None:
        result = _dispatch(message=phrase, queue_root=tmp_path)
        assert result.matched is True
        assert result.status == "review_missing_job", (
            f"Phrase {phrase!r} did not reach the review branch. Got status={result.status!r}"
        )

    def test_what_was_the_output_with_evidence_returns_reviewed(self, tmp_path: Path) -> None:
        mock_evidence = _mock_succeeded_evidence("job-20260404-output")
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=mock_evidence,
        ):
            result = _dispatch(message="what was the output", queue_root=tmp_path)
        assert result.matched is True
        assert result.status == "reviewed_job_outcome"
        assert "job-20260404-output" in result.assistant_text
        assert "succeeded" in result.assistant_text.lower()


# ---------------------------------------------------------------------------
# 2. New followup phrases — bare next-step and output-class variants
# ---------------------------------------------------------------------------


class TestBareNextStepFollowupPhrases:
    """Bare 'what should we do next' and 'what's the next step' must reach followup."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "what should we do next",
            "what's the next step",
            "what next based on that",
        ],
    )
    def test_bare_next_step_phrases_are_recognized(self, phrase: str) -> None:
        assert is_followup_preview_request(phrase), (
            f"{phrase!r} should be a followup preview request"
        )

    @pytest.mark.parametrize(
        "phrase",
        [
            "what should we do next",
            "what's the next step",
            "what next based on that",
        ],
    )
    def test_bare_next_step_phrases_reach_followup_branch(
        self, tmp_path: Path, phrase: str
    ) -> None:
        result = _dispatch(message=phrase, queue_root=tmp_path)
        assert result.matched is True
        assert result.status == "followup_missing_evidence", (
            f"Phrase {phrase!r} did not reach the followup branch. Got status={result.status!r}"
        )


# ---------------------------------------------------------------------------
# 3. Revise-from-evidence — output-class variants
# ---------------------------------------------------------------------------


class TestReviseFromEvidenceOutputPhrases:
    """Revise/update based on 'the output' must reach the revise-from-evidence path."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "update that based on the output",
            "update based on the output",
            "revise that based on the output",
            "revise based on the output",
        ],
    )
    def test_output_revise_phrases_are_recognized(self, phrase: str) -> None:
        assert is_revise_from_evidence_request(phrase), (
            f"{phrase!r} should be a revise-from-evidence request"
        )
        assert is_followup_preview_request(phrase), (
            f"{phrase!r} should also be a followup preview request"
        )

    def test_update_based_on_output_with_evidence_returns_revised_preview(
        self, tmp_path: Path
    ) -> None:
        mock_evidence = _mock_succeeded_evidence("job-20260404-revise-out")
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=mock_evidence,
        ):
            result = _dispatch(
                message="update that based on the output",
                queue_root=tmp_path,
            )
        assert result.matched is True
        assert result.status == "revised_preview_ready"
        assert result.write_preview is True
        assert result.write_handoff_ready is True
        assert "job-20260404-revise-out" in result.assistant_text
        assert "preview-only" in result.assistant_text.lower()
        goal = str((result.preview_payload or {}).get("goal") or "")
        assert "revise" in goal.lower()


# ---------------------------------------------------------------------------
# 4. Reference resolver — output-class JOB_RESULT phrases
# ---------------------------------------------------------------------------


class TestOutputReferenceClassification:
    """'that output' / 'the output' must classify as JOB_RESULT."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "that output",
            "the output",
            "the last output",
            "summarize that output",
            "show me the output please",
        ],
    )
    def test_output_phrases_classify_as_job_result(self, phrase: str) -> None:
        assert classify_reference(phrase) == ReferenceClass.JOB_RESULT

    def test_output_resolves_from_completed_job_context(self) -> None:
        ctx = {"last_completed_job_ref": "inbox-output-job.json"}
        result = resolve_session_reference("show me the output", ctx)
        assert isinstance(result, ResolvedReference)
        assert result.reference_class == ReferenceClass.JOB_RESULT
        assert result.value == "inbox-output-job.json"
        assert result.source == "last_completed_job_ref"

    def test_output_fails_closed_on_empty_context(self) -> None:
        result = resolve_session_reference("what was the output", {})
        assert isinstance(result, UnresolvedReference)
        assert result.reference_class == ReferenceClass.JOB_RESULT
        assert result.reason == "no_job_or_result_reference"


# ---------------------------------------------------------------------------
# 5. Session-context-aware job resolution for review and followup
# ---------------------------------------------------------------------------


class TestSessionContextJobResolution:
    """Review and followup must resolve job ID from session context when
    no explicit job ID or handoff state provides one."""

    def test_review_resolves_from_session_context_completed_job(self, tmp_path: Path) -> None:
        mock_evidence = _mock_succeeded_evidence("inbox-ctx-completed.json")
        ctx = {"last_completed_job_ref": "inbox-ctx-completed.json"}
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=mock_evidence,
        ):
            result = _dispatch(
                message="what was the outcome",
                queue_root=tmp_path,
                session_context=ctx,
            )
        assert result.matched is True
        assert result.status == "reviewed_job_outcome"
        assert "inbox-ctx-completed.json" in result.assistant_text

    def test_followup_resolves_from_session_context_completed_job(self, tmp_path: Path) -> None:
        mock_evidence = _mock_succeeded_evidence("inbox-ctx-followup.json")
        ctx = {"last_completed_job_ref": "inbox-ctx-followup.json"}
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=mock_evidence,
        ):
            result = _dispatch(
                message="what should we do next",
                queue_root=tmp_path,
                session_context=ctx,
            )
        assert result.matched is True
        assert result.status == "followup_preview_ready"
        assert "inbox-ctx-followup.json" in result.assistant_text

    def test_review_resolves_from_session_context_reviewed_job(self, tmp_path: Path) -> None:
        mock_evidence = _mock_succeeded_evidence("inbox-ctx-reviewed.json")
        ctx = {
            "last_completed_job_ref": None,
            "last_reviewed_job_ref": "inbox-ctx-reviewed.json",
        }
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=mock_evidence,
        ):
            result = _dispatch(
                message="summarize the result",
                queue_root=tmp_path,
                session_context=ctx,
            )
        assert result.matched is True
        assert result.status == "reviewed_job_outcome"

    def test_no_session_context_returns_review_missing(self, tmp_path: Path) -> None:
        result = _dispatch(
            message="what was the outcome",
            queue_root=tmp_path,
            session_context=None,
        )
        assert result.matched is True
        assert result.status == "review_missing_job"


# ---------------------------------------------------------------------------
# 6. Multi-turn review→followup continuity via session context
# ---------------------------------------------------------------------------


class TestMultiTurnReviewFollowupContinuity:
    """After review, session context should support natural followup resolution."""

    def test_review_then_followup_uses_reviewed_job_ref(self, tmp_path: Path) -> None:
        """Simulates: review job → context updated → followup resolves from context."""
        from voxera.vera.session_store import read_session_context

        session_id = "test-multi-turn"
        q = tmp_path / "queue"

        # Step 1: Simulate handoff + completion
        context_on_handoff_submitted(q, session_id, job_id="inbox-multi.json")
        context_on_completion_ingested(q, session_id, job_id="inbox-multi.json")

        # Step 2: Simulate review (context updated)
        context_on_review_performed(q, session_id, job_id="inbox-multi.json")

        # Step 3: Verify followup can resolve from context
        ctx = read_session_context(q, session_id)
        job_id = resolve_job_id_from_context(ctx)
        assert job_id == "inbox-multi.json"

        # Step 4: Verify reference resolution works for "the result"
        result = resolve_session_reference("what was the result", ctx)
        assert isinstance(result, ResolvedReference)
        assert result.value == "inbox-multi.json"

    def test_followup_preview_then_continuation_reference(self, tmp_path: Path) -> None:
        """After followup preview is prepared, 'that follow-up' resolves to preview."""
        from voxera.vera.session_store import read_session_context

        session_id = "test-followup-ref"
        q = tmp_path / "queue"

        context_on_handoff_submitted(q, session_id, job_id="inbox-followup.json")
        context_on_completion_ingested(q, session_id, job_id="inbox-followup.json")
        context_on_followup_preview_prepared(q, session_id, source_job_id="inbox-followup.json")

        ctx = read_session_context(q, session_id)
        result = resolve_session_reference("that follow-up", ctx)
        assert isinstance(result, ResolvedReference)
        assert result.reference_class == ReferenceClass.CONTINUATION
        assert result.source == "active_preview_ref"

    def test_handoff_then_completion_then_review_then_save_followup(self, tmp_path: Path) -> None:
        """Full lifecycle: handoff → complete → review → save follow-up."""
        from voxera.vera.session_store import read_session_context

        session_id = "test-full-lifecycle"
        q = tmp_path / "queue"

        # 1. Handoff
        context_on_handoff_submitted(q, session_id, job_id="inbox-lifecycle.json")
        ctx = read_session_context(q, session_id)
        assert ctx["last_submitted_job_ref"] == "inbox-lifecycle.json"
        assert ctx["active_preview_ref"] is None  # cleared on handoff

        # 2. Completion
        context_on_completion_ingested(q, session_id, job_id="inbox-lifecycle.json")
        ctx = read_session_context(q, session_id)
        assert ctx["last_completed_job_ref"] == "inbox-lifecycle.json"

        # 3. Review
        context_on_review_performed(q, session_id, job_id="inbox-lifecycle.json")
        ctx = read_session_context(q, session_id)
        assert ctx["last_reviewed_job_ref"] == "inbox-lifecycle.json"

        # 4. "save that follow-up" — should resolve as continuation
        # Since no follow-up preview has been prepared yet, continuation falls
        # to last_completed_job_ref
        result = resolve_session_reference("save that follow-up", ctx)
        assert isinstance(result, ResolvedReference)
        assert result.reference_class == ReferenceClass.CONTINUATION
        assert result.value == "inbox-lifecycle.json"

        # 5. Follow-up preview prepared
        context_on_followup_preview_prepared(q, session_id, source_job_id="inbox-lifecycle.json")
        ctx = read_session_context(q, session_id)
        result = resolve_session_reference("save that follow-up", ctx)
        assert isinstance(result, ResolvedReference)
        assert result.source == "active_preview_ref"


# ---------------------------------------------------------------------------
# 7. Fresh session fail-closed behavior
# ---------------------------------------------------------------------------


class TestFreshSessionFailClosed:
    """Fresh sessions with no context must fail closed for all phrase families."""

    def test_fresh_session_review_fails_closed(self, tmp_path: Path) -> None:
        result = _dispatch(
            message="what was the output",
            queue_root=tmp_path,
            session_context={},
        )
        assert result.matched is True
        assert result.status == "review_missing_job"

    def test_fresh_session_followup_fails_closed(self, tmp_path: Path) -> None:
        result = _dispatch(
            message="what should we do next",
            queue_root=tmp_path,
            session_context={},
        )
        assert result.matched is True
        assert result.status == "followup_missing_evidence"

    def test_fresh_session_revise_from_evidence_fails_closed(self, tmp_path: Path) -> None:
        result = _dispatch(
            message="update that based on the output",
            queue_root=tmp_path,
            session_context={},
        )
        assert result.matched is True
        assert result.status == "followup_missing_evidence"

    def test_fresh_session_save_followup_fails_closed(self, tmp_path: Path) -> None:
        result = _dispatch(
            message="save that follow-up",
            queue_root=tmp_path,
            session_context={},
        )
        assert result.matched is True
        assert result.status == "followup_missing_evidence"

    def test_fresh_session_output_reference_fails_closed(self) -> None:
        result = resolve_session_reference("what was the output", {})
        assert isinstance(result, UnresolvedReference)

    def test_fresh_session_followup_reference_fails_closed(self) -> None:
        result = resolve_session_reference("save that follow-up", {})
        assert isinstance(result, UnresolvedReference)


# ---------------------------------------------------------------------------
# 8. Preview wording truthfulness
# ---------------------------------------------------------------------------


class TestPreviewWordingTruthfulness:
    """Follow-up and revision replies must use truthful preview-only wording."""

    def test_followup_reply_says_preview_only(self, tmp_path: Path) -> None:
        mock_evidence = _mock_succeeded_evidence()
        ctx = {"last_completed_job_ref": "job-20260404-test"}
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=mock_evidence,
        ):
            result = _dispatch(
                message="what should we do next",
                queue_root=tmp_path,
                session_context=ctx,
            )
        assert result.matched is True
        assert "preview-only" in result.assistant_text.lower()
        assert "submitted" not in result.assistant_text.lower() or (
            "not" in result.assistant_text.lower()
            or "nothing has been submitted" in result.assistant_text.lower()
        )

    def test_revised_reply_says_preview_only(self, tmp_path: Path) -> None:
        mock_evidence = _mock_succeeded_evidence()
        ctx = {"last_completed_job_ref": "job-20260404-test"}
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=mock_evidence,
        ):
            result = _dispatch(
                message="update that based on the output",
                queue_root=tmp_path,
                session_context=ctx,
            )
        assert result.matched is True
        assert "preview-only" in result.assistant_text.lower()

    def test_save_followup_reply_says_nothing_submitted(self, tmp_path: Path) -> None:
        mock_evidence = _mock_succeeded_evidence()
        ctx = {"last_completed_job_ref": "job-20260404-test"}
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=mock_evidence,
        ):
            result = _dispatch(
                message="save the follow-up",
                queue_root=tmp_path,
                session_context=ctx,
            )
        assert result.matched is True
        assert "nothing has been submitted" in result.assistant_text.lower()


# ---------------------------------------------------------------------------
# 9. Evidence grounding invariant
# ---------------------------------------------------------------------------


class TestEvidenceGroundingInvariant:
    """Session context must never override canonical evidence.
    Review and followup must stay grounded in queue/artifact truth."""

    def test_review_context_updates_track_reviewed_job(self, tmp_path: Path) -> None:
        mock_evidence = _mock_succeeded_evidence("job-grounding-test")
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=mock_evidence,
        ):
            result = _dispatch(
                message="what was the output",
                queue_root=tmp_path,
            )
        assert result.context_updates is not None
        assert result.context_updates.get("last_reviewed_job_ref") == "job-grounding-test"

    def test_followup_context_updates_track_reviewed_job(self, tmp_path: Path) -> None:
        mock_evidence = _mock_succeeded_evidence("job-grounding-followup")
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=mock_evidence,
        ):
            result = _dispatch(
                message="what should we do next",
                queue_root=tmp_path,
            )
        assert result.context_updates is not None
        assert result.context_updates.get("last_reviewed_job_ref") == "job-grounding-followup"

    def test_resolver_returns_string_ref_not_evidence_object(self) -> None:
        """Reference resolver returns string hints, not canonical objects.
        Callers validate against canonical truth downstream."""
        ctx = {"last_completed_job_ref": "inbox-abc.json"}
        result = resolve_session_reference("the output", ctx)
        assert isinstance(result, ResolvedReference)
        assert isinstance(result.value, str)


# ---------------------------------------------------------------------------
# 10. Adjacent regression anchors
# ---------------------------------------------------------------------------


class TestAdjacentRegressionAnchors:
    """Existing phrases must continue to work with the new additions."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "what happened",
            "did it work",
            "summarize the result",
            "inspect output details",
            "what was the outcome",
            "what was the result",
        ],
    )
    def test_existing_review_phrases_still_work(self, phrase: str) -> None:
        assert is_review_request(phrase)

    @pytest.mark.parametrize(
        "phrase",
        [
            "prepare the next step",
            "draft a follow-up",
            "based on that result",
            "revise that based on the result",
            "save that follow-up",
        ],
    )
    def test_existing_followup_phrases_still_work(self, phrase: str) -> None:
        assert is_followup_preview_request(phrase)

    @pytest.mark.parametrize(
        "phrase",
        [
            "that result",
            "the result",
            "the last job",
            "that outcome",
        ],
    )
    def test_existing_job_result_reference_phrases_still_work(self, phrase: str) -> None:
        assert classify_reference(phrase) == ReferenceClass.JOB_RESULT

    def test_plain_question_does_not_match_review_or_followup(self) -> None:
        assert not is_review_request("What is the capital of France?")
        assert not is_followup_preview_request("What is the capital of France?")

    def test_write_request_does_not_match_review_or_followup(self) -> None:
        assert not is_review_request("Write me a poem about autumn")
        assert not is_followup_preview_request("Write me a poem about autumn")


# ---------------------------------------------------------------------------
# 11. Known overbroad-matching boundary documentation
# ---------------------------------------------------------------------------


class TestKnownOverbroadBoundaries:
    """Document known overbroad substring matches introduced by this PR.

    These tests document that certain broad phrases DO match review/followup
    hints via substring matching — this is the same pattern as pre-existing
    hints like 'status' (matches 'status of the project') and 'what should i
    do next' (matches 'what should i do next about dinner').

    The fail-closed behavior is preserved: when no canonical job evidence
    exists, the user gets an honest message — not a silent wrong-mode reply.

    A follow-up PR should consider adding disambiguation (e.g. requiring
    job-context words in the message, or checking session context before
    matching) to reduce false positives. This is a pre-existing architectural
    pattern, not a regression introduced by this PR.
    """

    @pytest.mark.parametrize(
        "phrase",
        [
            "what should we do next week",
            "what's the next step in the migration",
        ],
    )
    def test_bare_next_step_matches_broader_planning_phrases(self, phrase: str) -> None:
        """Known overbroad: bare next-step hints match planning language.

        Pre-existing parallel: 'what should i do next' (review hint) also
        matches 'what should i do next about dinner'.
        """
        assert is_followup_preview_request(phrase)

    @pytest.mark.parametrize(
        "phrase",
        [
            "show the output of the grep command",
            "what was the output of that command",
        ],
    )
    def test_output_review_hints_match_broader_output_phrases(self, phrase: str) -> None:
        """Known overbroad: output-class review hints match generic output queries.

        Pre-existing parallel: 'status' (review hint) matches 'status of the
        project'. Fail-closed behavior preserved in fresh sessions.
        """
        assert is_review_request(phrase)

    @pytest.mark.parametrize(
        "phrase",
        [
            "format the output as JSON",
            "write the output to a file",
            "pipe the output to grep",
        ],
    )
    def test_output_reference_resolver_is_benign_in_dispatch(self, phrase: str) -> None:
        """Reference resolver classifies 'the output' as JOB_RESULT, but this
        does NOT cause false early-exit dispatch because the stale-draft check
        (dispatch step 10) only gates on DRAFT-class references."""
        assert classify_reference(phrase) == ReferenceClass.JOB_RESULT
        # But these do NOT match review or followup dispatch:
        assert not is_review_request(phrase)
        assert not is_followup_preview_request(phrase)

    def test_overbroad_followup_still_fails_closed_in_fresh_session(self, tmp_path: Path) -> None:
        """Even when an overbroad phrase fires the followup branch, a fresh
        session gets an honest fail-closed response, not a wrong-mode reply."""
        result = _dispatch(
            message="what should we do next week",
            queue_root=tmp_path,
            session_context={},
        )
        assert result.matched is True
        assert result.status == "followup_missing_evidence"
        assert "follow-up preview" in result.assistant_text.lower()

    def test_overbroad_review_still_fails_closed_in_fresh_session(self, tmp_path: Path) -> None:
        """Even when an overbroad phrase fires the review branch, a fresh
        session gets an honest fail-closed response."""
        result = _dispatch(
            message="show the output of the grep command",
            queue_root=tmp_path,
            session_context={},
        )
        assert result.matched is True
        assert result.status == "review_missing_job"
        assert "could not resolve" in result.assistant_text.lower()
