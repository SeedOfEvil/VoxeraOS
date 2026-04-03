"""Characterization tests for the early-exit intent handler dispatch extraction.

These tests anchor the behavior of every early-exit branch extracted from
``chat()`` into ``vera_web.chat_early_exit_dispatch``.

Coverage:
  1. Diagnostics refusal — blocked system-diagnostics phrasing.
  2. Job review / evidence review — review request and explicit job ID.
  3. Follow-up preview request — with and without resolvable evidence.
  4. Investigation derived-save — with and without current derived output.
  5. Investigation compare — with and without investigation context.
  6. Investigation summary — with and without investigation context.
  7. Investigation expand — invalid reference (error early-exit only).
  8. Investigation save — with and without investigation context.
  9. Near-miss submit phrase — fail-closed behavior.
  10. Stale draft reference — fail-closed when no active draft/preview.
  11. No-match fallthrough — confirms normal flow is not interrupted.

Trust-sensitive boundaries verified:
  - ``matched=False`` never triggers session writes.
  - Preview/handoff write flags are only set when a real payload is produced.
  - Fail-closed branches always set ``matched=True`` and a non-empty status.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from voxera.vera_web.chat_early_exit_dispatch import (
    EarlyExitResult,
    dispatch_early_exit_intent,
)

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
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
    """Thin wrapper so tests don't have to pass every keyword argument."""
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


def _sample_investigation() -> dict[str, object]:
    """Minimal investigation payload with three results."""
    return {
        "query": "incident response best practices",
        "retrieved_at_ms": 123456,
        "results": [
            {
                "result_id": 1,
                "title": "Guide A",
                "url": "https://example.com/a",
                "source": "example.com",
                "snippet": "Triage and containment.",
                "why_it_matched": "Fast-response guidance.",
                "rank": 1,
            },
            {
                "result_id": 2,
                "title": "Guide B",
                "url": "https://example.com/b",
                "source": "example.com",
                "snippet": "Human review and escalation.",
                "why_it_matched": "Human oversight.",
                "rank": 2,
            },
            {
                "result_id": 3,
                "title": "Guide C",
                "url": "https://example.com/c",
                "source": "example.com",
                "snippet": "Evidence collection.",
                "why_it_matched": "Evidence-first.",
                "rank": 3,
            },
        ],
    }


# ---------------------------------------------------------------------------
# EarlyExitResult dataclass defaults
# ---------------------------------------------------------------------------


class TestEarlyExitResultDefaults:
    def test_matched_false_has_empty_fields(self) -> None:
        r = EarlyExitResult(matched=False)
        assert r.assistant_text == ""
        assert r.status == ""
        assert r.preview_payload is None
        assert r.write_preview is False
        assert r.write_handoff_ready is False
        assert r.derived_output is None
        assert r.write_derived_output is False

    def test_matched_true_defaults(self) -> None:
        r = EarlyExitResult(matched=True, assistant_text="hello", status="ok")
        assert r.write_preview is False
        assert r.write_handoff_ready is False
        assert r.write_derived_output is False


# ---------------------------------------------------------------------------
# 1. Diagnostics refusal
# ---------------------------------------------------------------------------


class TestDiagnosticsRefusal:
    def test_diagnostics_request_is_blocked(self, tmp_path: Path) -> None:
        result = _dispatch(
            message="Check the status of /etc/shadow",
            queue_root=tmp_path,
        )
        assert result.matched is True
        assert result.status == "blocked_diagnostics"
        assert result.assistant_text
        # No writes should be instructed
        assert result.write_preview is False
        assert result.write_handoff_ready is False
        assert result.write_derived_output is False

    def test_diagnostics_refusal_text_is_non_empty(self, tmp_path: Path) -> None:
        result = _dispatch(
            message="Show me the logs for /etc/passwd",
            queue_root=tmp_path,
        )
        assert result.matched is True
        assert len(result.assistant_text) > 0

    def test_normal_message_does_not_trigger_diagnostics(self, tmp_path: Path) -> None:
        result = _dispatch(message="What is the capital of France?", queue_root=tmp_path)
        # Should not have matched diagnostics (may or may not match other branches)
        if result.matched:
            assert result.status != "blocked_diagnostics"


# ---------------------------------------------------------------------------
# 2. Job review / evidence review
# ---------------------------------------------------------------------------


class TestJobReview:
    def test_review_request_without_job_returns_review_missing_job(self, tmp_path: Path) -> None:
        # No session handoff state, no job ID → can't resolve
        result = _dispatch(
            message="what happened to the last job",
            queue_root=tmp_path,
        )
        assert result.matched is True
        assert result.status == "review_missing_job"
        assert "could not resolve" in result.assistant_text.lower()
        assert result.write_preview is False

    def test_explicit_job_id_returns_review_missing_when_no_artifacts(self, tmp_path: Path) -> None:
        result = _dispatch(
            message="hello",
            requested_job_id="job-nonexistent-abc123",
            queue_root=tmp_path,
        )
        assert result.matched is True
        assert result.status == "review_missing_job"

    def test_review_request_with_diagnostics_service_turn_does_not_match_review(
        self, tmp_path: Path
    ) -> None:
        # When diagnostics_service_turn is True, the review branch is bypassed
        # (the message is treated as a service status intent, not a review request).
        # The "what is the status" phrasing might also trigger diagnostics refusal
        # if it looks like a blocked path.  We just confirm it does NOT return
        # "review_missing_job" when diagnostics_service_turn=True.
        result = _dispatch(
            message="status of the last job",
            diagnostics_service_turn=True,
            queue_root=tmp_path,
        )
        assert result.status != "review_missing_job"

    def test_review_request_with_resolvable_evidence(self, tmp_path: Path) -> None:
        from voxera.vera.evidence_review import ReviewedJobEvidence

        mock_evidence = ReviewedJobEvidence(
            job_id="job-20260101-abc123",
            state="done",
            lifecycle_state="done",
            terminal_outcome="succeeded",
            approval_status="",
            latest_summary="Task completed successfully.",
            failure_summary="",
            artifact_families=("note",),
            artifact_refs=("note-abc.md",),
            evidence_trace=("done",),
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
            result = _dispatch(
                message="what happened to the last job",
                queue_root=tmp_path,
            )
        assert result.matched is True
        assert result.status == "reviewed_job_outcome"
        assert result.assistant_text
        assert result.write_preview is False


# ---------------------------------------------------------------------------
# 3. Follow-up preview request
# ---------------------------------------------------------------------------


class TestFollowupPreview:
    def test_followup_without_evidence_returns_followup_missing(self, tmp_path: Path) -> None:
        result = _dispatch(
            message="prepare the next step",
            queue_root=tmp_path,
        )
        assert result.matched is True
        assert result.status == "followup_missing_evidence"
        assert "follow-up preview" in result.assistant_text.lower()
        assert result.write_preview is False
        assert result.write_handoff_ready is False

    def test_followup_with_evidence_returns_preview_ready(self, tmp_path: Path) -> None:
        from voxera.vera.evidence_review import ReviewedJobEvidence

        mock_evidence = ReviewedJobEvidence(
            job_id="job-20260101-abcdef",
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
            result = _dispatch(
                message="prepare the next step",
                queue_root=tmp_path,
            )
        assert result.matched is True
        assert result.status == "followup_preview_ready"
        assert result.write_preview is True
        assert result.write_handoff_ready is True
        assert result.preview_payload is not None
        assert "job-20260101-abcdef" in result.assistant_text
        assert "preview-only" in result.assistant_text.lower()

    def test_draft_a_followup_based_on_job_result_regression(self, tmp_path: Path) -> None:
        """Regression: 'Draft a follow-up based on that job's result.' must reach the
        followup branch.  Previously failed because _FOLLOWUP_HINTS lacked the
        'draft a follow-up' variant (with article 'a'), causing the message to fall
        through all dispatch branches and hit the LLM, which returned a fail-closed
        'I was not able to prepare a governed preview' response.
        """
        from voxera.vera.evidence_review import ReviewedJobEvidence

        mock_evidence = ReviewedJobEvidence(
            job_id="job-20260101-regression",
            state="done",
            lifecycle_state="done",
            terminal_outcome="succeeded",
            approval_status="",
            latest_summary="Linked goal completed.",
            failure_summary="",
            artifact_families=("note",),
            artifact_refs=("note-regression.md",),
            evidence_trace=("done",),
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
            result = _dispatch(
                message="Draft a follow-up based on that job's result.",
                queue_root=tmp_path,
            )
        assert result.matched is True
        assert result.status == "followup_preview_ready", (
            "Regression: 'Draft a follow-up based on that job's result.' must reach "
            "the followup branch and return 'followup_preview_ready', not fall through "
            f"to the LLM. Got status={result.status!r}"
        )
        assert result.write_preview is True
        assert result.write_handoff_ready is True
        assert result.preview_payload is not None
        assert "job-20260101-regression" in result.assistant_text

    @pytest.mark.parametrize(
        "phrase",
        [
            # Regression phrase — the exact text that exposed the bug
            "draft a follow-up based on that job's result",
            # Variants using 'write / create / make' + 'a follow-up'
            "write a follow-up based on that result",
            "create a follow-up for the completed job",
            "make a follow-up based on the result",
            # Bare new hint
            "draft a follow-up",
            # 'based on that job' hint (no review-hint collision — does not contain 'last job')
            "please draft something based on that job outcome",
            # 'based on the result' hint
            "based on the result, prepare the next step",
        ],
    )
    def test_followup_hint_variants_reach_followup_branch(
        self, tmp_path: Path, phrase: str
    ) -> None:
        """New _FOLLOWUP_HINTS variants must route to the followup branch.

        Evidence is None (tmp_path has no artifacts), so the branch returns
        ``followup_missing_evidence`` — but the key assertion is that the
        followup branch WAS reached (not ``matched=False`` or a different status).
        """
        result = _dispatch(message=phrase, queue_root=tmp_path)
        assert result.matched is True
        assert result.status == "followup_missing_evidence", (
            f"Phrase {phrase!r} did not reach the followup branch. Got status={result.status!r}"
        )


# ---------------------------------------------------------------------------
# 4. Investigation derived-save
# ---------------------------------------------------------------------------


class TestInvestigationDerivedSave:
    def test_derived_save_with_no_derived_output_returns_missing(self, tmp_path: Path) -> None:
        result = _dispatch(
            message="save that comparison",
            should_attempt_derived_save=True,
            session_derived_output=None,
            queue_root=tmp_path,
        )
        assert result.matched is True
        assert result.status == "investigation_derived_missing"
        assert result.write_preview is False
        assert result.write_handoff_ready is False

    def test_derived_save_with_valid_output_returns_prepared_preview(self, tmp_path: Path) -> None:
        # draft_investigation_derived_save_preview requires "markdown" and
        # "derivation_type" keys.  See investigation_derivations.py.
        derived_output = {
            "markdown": "Result 1 is more thorough than Result 2.\n",
            "derivation_type": "comparison",
            "answer": "Result 1 is more thorough than Result 2.",
            "source_result_ids": [1, 2],
        }
        result = _dispatch(
            message="save that comparison",
            should_attempt_derived_save=True,
            session_derived_output=derived_output,
            queue_root=tmp_path,
        )
        assert result.matched is True
        assert result.status == "prepared_preview"
        assert result.write_preview is True
        assert result.write_handoff_ready is True
        assert result.preview_payload is not None
        assert "Nothing has been submitted" in result.assistant_text

    def test_derived_save_flag_false_does_not_match(self, tmp_path: Path) -> None:
        result = _dispatch(
            message="save that comparison",
            should_attempt_derived_save=False,
            queue_root=tmp_path,
        )
        # With flag=False, the derived-save branch is skipped entirely
        assert result.status != "investigation_derived_missing"
        assert result.status != "prepared_preview"


# ---------------------------------------------------------------------------
# 5. Investigation compare
# ---------------------------------------------------------------------------


class TestInvestigationCompare:
    def test_compare_without_investigation_returns_invalid(self, tmp_path: Path) -> None:
        result = _dispatch(
            message="compare results 1 and 2",
            session_investigation=None,
            queue_root=tmp_path,
        )
        assert result.matched is True
        assert result.status == "investigation_reference_invalid"
        assert result.write_derived_output is False

    def test_compare_with_valid_investigation_returns_comparison(self, tmp_path: Path) -> None:
        investigation = _sample_investigation()
        result = _dispatch(
            message="compare results 1 and 2",
            session_investigation=investigation,
            queue_root=tmp_path,
        )
        assert result.matched is True
        assert result.status == "ok:investigation_comparison"
        assert result.write_derived_output is True
        assert result.derived_output is not None
        assert result.assistant_text  # non-empty answer

    def test_compare_result_carries_answer_text(self, tmp_path: Path) -> None:
        investigation = _sample_investigation()
        result = _dispatch(
            message="compare results 1 and 2",
            session_investigation=investigation,
            queue_root=tmp_path,
        )
        assert result.matched is True
        # derived_output must have an "answer" key
        assert isinstance(result.derived_output, dict)
        assert result.derived_output.get("answer")


# ---------------------------------------------------------------------------
# 6. Investigation summary
# ---------------------------------------------------------------------------


class TestInvestigationSummary:
    def test_summary_without_investigation_returns_invalid(self, tmp_path: Path) -> None:
        result = _dispatch(
            message="summarize all findings",
            session_investigation=None,
            queue_root=tmp_path,
        )
        assert result.matched is True
        assert result.status == "investigation_reference_invalid"
        assert result.write_derived_output is False

    def test_summary_with_valid_investigation_returns_summary(self, tmp_path: Path) -> None:
        investigation = _sample_investigation()
        result = _dispatch(
            message="summarize result 1",
            session_investigation=investigation,
            queue_root=tmp_path,
        )
        assert result.matched is True
        assert result.status == "ok:investigation_summary"
        assert result.write_derived_output is True
        assert result.derived_output is not None

    def test_summary_result_carries_derived_output(self, tmp_path: Path) -> None:
        investigation = _sample_investigation()
        result = _dispatch(
            message="summarize all findings",
            session_investigation=investigation,
            queue_root=tmp_path,
        )
        assert result.matched is True
        assert isinstance(result.derived_output, dict)
        assert result.derived_output.get("answer")


# ---------------------------------------------------------------------------
# 7. Investigation expand — invalid reference early exit
# ---------------------------------------------------------------------------


class TestInvestigationExpand:
    def test_expand_without_investigation_returns_invalid(self, tmp_path: Path) -> None:
        result = _dispatch(
            message="expand result 1 please",
            session_investigation=None,
            queue_root=tmp_path,
        )
        assert result.matched is True
        assert result.status == "investigation_reference_invalid"
        assert result.write_derived_output is False

    def test_expand_ambiguous_reference_returns_invalid(self, tmp_path: Path) -> None:
        # Ask to expand multiple results — dispatch should block (only 1 allowed)
        investigation = _sample_investigation()
        result = _dispatch(
            message="expand results 1 and 2",
            session_investigation=investigation,
            queue_root=tmp_path,
        )
        # Either matched with invalid-reference, or did not match at all.
        # It must NOT return ok:investigation_expansion (that's the normal flow).
        if result.matched:
            assert result.status == "investigation_reference_invalid"

    def test_expand_valid_single_reference_does_not_early_exit(self, tmp_path: Path) -> None:
        # A valid single-result expand reference should NOT fire an early exit —
        # the normal LLM flow handles the actual expansion.
        investigation = _sample_investigation()
        result = _dispatch(
            message="expand result 1 please",
            session_investigation=investigation,
            queue_root=tmp_path,
        )
        # Should NOT match as an error early exit
        assert result.matched is False


# ---------------------------------------------------------------------------
# 8. Investigation save
# ---------------------------------------------------------------------------


class TestInvestigationSave:
    def test_save_without_investigation_returns_invalid(self, tmp_path: Path) -> None:
        result = _dispatch(
            message="save result 2 to a note",
            session_investigation=None,
            queue_root=tmp_path,
        )
        assert result.matched is True
        assert result.status == "investigation_reference_invalid"
        assert result.write_preview is False
        assert result.write_handoff_ready is False

    def test_save_with_valid_investigation_returns_prepared_preview(self, tmp_path: Path) -> None:
        investigation = _sample_investigation()
        result = _dispatch(
            message="save result 1 to a note",
            session_investigation=investigation,
            queue_root=tmp_path,
        )
        assert result.matched is True
        assert result.status == "prepared_preview"
        assert result.write_preview is True
        assert result.write_handoff_ready is True
        assert result.preview_payload is not None
        assert "Nothing has been submitted" in result.assistant_text

    def test_save_all_findings_returns_prepared_preview(self, tmp_path: Path) -> None:
        investigation = _sample_investigation()
        result = _dispatch(
            message="save all findings",
            session_investigation=investigation,
            queue_root=tmp_path,
        )
        assert result.matched is True
        assert result.status == "prepared_preview"
        assert result.write_preview is True


# ---------------------------------------------------------------------------
# 9. Near-miss submit phrase (fail-closed)
# ---------------------------------------------------------------------------


class TestNearMissSubmit:
    @pytest.mark.parametrize(
        "phrase",
        [
            "sned it",
            "sendit",
            "submt it",
            "sumbit it",
            "submitt it",
            "sedn it",
        ],
    )
    def test_near_miss_phrases_are_blocked(self, tmp_path: Path, phrase: str) -> None:
        result = _dispatch(message=phrase, queue_root=tmp_path)
        assert result.matched is True
        assert result.status == "near_miss_submit_rejected"
        assert "did not submit" in result.assistant_text.lower()
        # Fail-closed: no writes
        assert result.write_preview is False
        assert result.write_handoff_ready is False
        assert result.write_derived_output is False

    def test_canonical_submit_phrase_is_not_near_miss(self, tmp_path: Path) -> None:
        # "submit it" is a canonical submit phrase and is handled by
        # the submit-handoff path in app.py, not the near-miss blocker.
        result = _dispatch(message="submit it", queue_root=tmp_path)
        assert result.status != "near_miss_submit_rejected"

    def test_send_it_is_not_near_miss(self, tmp_path: Path) -> None:
        result = _dispatch(message="send it", queue_root=tmp_path)
        assert result.status != "near_miss_submit_rejected"


# ---------------------------------------------------------------------------
# 10. No-match fallthrough
# ---------------------------------------------------------------------------


class TestNoMatchFallthrough:
    def test_plain_question_returns_no_match(self, tmp_path: Path) -> None:
        result = _dispatch(
            message="What is the capital of Canada?",
            queue_root=tmp_path,
        )
        assert result.matched is False
        assert result.assistant_text == ""
        assert result.status == ""
        assert result.write_preview is False
        assert result.write_handoff_ready is False
        assert result.write_derived_output is False

    def test_write_draft_request_returns_no_match(self, tmp_path: Path) -> None:
        # Writing draft turns must proceed to the normal LLM path.
        result = _dispatch(
            message="Write me a short poem about autumn",
            queue_root=tmp_path,
        )
        assert result.matched is False

    def test_code_request_returns_no_match(self, tmp_path: Path) -> None:
        result = _dispatch(
            message="Write a Python function to sort a list",
            queue_root=tmp_path,
        )
        assert result.matched is False

    def test_conversational_request_returns_no_match(self, tmp_path: Path) -> None:
        result = _dispatch(
            message="Can you help me plan my tasks for today?",
            queue_root=tmp_path,
        )
        assert result.matched is False


# ---------------------------------------------------------------------------
# 11. Write-flag integrity (fail-closed invariants)
# ---------------------------------------------------------------------------


class TestWriteFlagIntegrity:
    """Verify that write flags are only set with consistent payloads."""

    def test_write_preview_always_comes_with_payload(self, tmp_path: Path) -> None:
        """If write_preview=True, preview_payload must not be None."""
        investigation = _sample_investigation()
        result = _dispatch(
            message="save result 1 to a note",
            session_investigation=investigation,
            queue_root=tmp_path,
        )
        if result.write_preview:
            assert result.preview_payload is not None

    def test_write_handoff_ready_only_when_preview_also_written(self, tmp_path: Path) -> None:
        """write_handoff_ready must not be True when write_preview is False."""
        # Near-miss: no writes at all
        result = _dispatch(message="sned it", queue_root=tmp_path)
        assert result.write_handoff_ready is False

    def test_write_derived_output_always_comes_with_derived_data(self, tmp_path: Path) -> None:
        """If write_derived_output=True, derived_output must not be None."""
        investigation = _sample_investigation()
        result = _dispatch(
            message="compare results 1 and 2",
            session_investigation=investigation,
            queue_root=tmp_path,
        )
        if result.write_derived_output:
            assert result.derived_output is not None

    def test_no_match_has_no_write_flags_set(self, tmp_path: Path) -> None:
        result = _dispatch(message="Hello there!", queue_root=tmp_path)
        assert result.matched is False
        assert result.write_preview is False
        assert result.write_handoff_ready is False
        assert result.write_derived_output is False

    def test_review_missing_has_no_write_flags(self, tmp_path: Path) -> None:
        result = _dispatch(
            message="what happened to the last job",
            queue_root=tmp_path,
        )
        assert result.matched is True
        assert result.status == "review_missing_job"
        assert result.write_preview is False
        assert result.write_handoff_ready is False
        assert result.write_derived_output is False


# ---------------------------------------------------------------------------
# 12. Linked-job review — result inspection / summary phrases
# ---------------------------------------------------------------------------


class TestLinkedJobReviewInspection:
    """Regression: natural result-inspection phrases must reach the review branch."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "summarize the result",
            "summarize that result",
            "summarize the job result",
            "inspect output details",
            "inspect output",
            "inspect the output",
            "review the result",
            "review that result",
            "show me the result",
            "show the result",
            "what was the outcome",
            "what was the result",
        ],
    )
    def test_review_inspection_phrases_reach_review_branch(
        self, tmp_path: Path, phrase: str
    ) -> None:
        result = _dispatch(message=phrase, queue_root=tmp_path)
        assert result.matched is True
        assert result.status == "review_missing_job", (
            f"Phrase {phrase!r} did not reach the review branch. Got status={result.status!r}"
        )

    def test_inspect_output_details_with_evidence_returns_reviewed(self, tmp_path: Path) -> None:
        from voxera.vera.evidence_review import ReviewedJobEvidence

        mock_evidence = ReviewedJobEvidence(
            job_id="job-20260401-inspect",
            state="succeeded",
            lifecycle_state="done",
            terminal_outcome="succeeded",
            approval_status="",
            latest_summary="Generated report.pdf successfully.",
            failure_summary="",
            artifact_families=("report",),
            artifact_refs=("report:report.pdf",),
            evidence_trace=("terminal_outcome=succeeded",),
            child_summary=None,
            execution_capabilities=None,
            capability_boundary_violation=None,
            expected_artifacts=("report.pdf",),
            observed_expected_artifacts=("report.pdf",),
            missing_expected_artifacts=(),
            expected_artifact_status="observed",
            normalized_outcome_class="success",
            value_forward_text="Report generated.",
        )
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=mock_evidence,
        ):
            result = _dispatch(
                message="inspect output details",
                queue_root=tmp_path,
            )
        assert result.matched is True
        assert result.status == "reviewed_job_outcome"
        assert "job-20260401-inspect" in result.assistant_text
        assert "succeeded" in result.assistant_text.lower()
        assert "report" in result.assistant_text.lower()

    def test_summarize_the_result_with_evidence_returns_reviewed(self, tmp_path: Path) -> None:
        from voxera.vera.evidence_review import ReviewedJobEvidence

        mock_evidence = ReviewedJobEvidence(
            job_id="job-20260401-summary",
            state="succeeded",
            lifecycle_state="done",
            terminal_outcome="succeeded",
            approval_status="",
            latest_summary="Backup completed.",
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
            result = _dispatch(
                message="summarize the result",
                queue_root=tmp_path,
            )
        assert result.matched is True
        assert result.status == "reviewed_job_outcome"
        assert "Backup completed" in result.assistant_text


# ---------------------------------------------------------------------------
# 13. Linked-job follow-up — revise-from-evidence and save follow-up phrases
# ---------------------------------------------------------------------------


class TestLinkedJobFollowupRevision:
    """Regression: revise-from-evidence and save-follow-up phrases must reach the followup branch."""

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
    def test_revision_phrases_reach_followup_branch(self, tmp_path: Path, phrase: str) -> None:
        result = _dispatch(message=phrase, queue_root=tmp_path)
        assert result.matched is True
        assert result.status == "followup_missing_evidence", (
            f"Phrase {phrase!r} did not reach the followup branch. Got status={result.status!r}"
        )

    def test_revise_based_on_result_with_evidence_returns_revised_preview(
        self, tmp_path: Path
    ) -> None:
        from voxera.vera.evidence_review import ReviewedJobEvidence

        mock_evidence = ReviewedJobEvidence(
            job_id="job-20260401-revise",
            state="succeeded",
            lifecycle_state="done",
            terminal_outcome="succeeded",
            approval_status="",
            latest_summary="Analysis completed.",
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
            result = _dispatch(
                message="revise that based on the result",
                queue_root=tmp_path,
            )
        assert result.matched is True
        assert result.status == "revised_preview_ready"
        assert result.write_preview is True
        assert result.write_handoff_ready is True
        assert result.preview_payload is not None
        assert "job-20260401-revise" in result.assistant_text
        assert "preview-only" in result.assistant_text.lower()
        # Must include evidence detail
        assert "succeeded" in result.assistant_text.lower()
        # Goal must be revision-oriented
        goal = str(result.preview_payload.get("goal") or "")
        assert "revise" in goal.lower()
        assert "job-20260401-revise" in goal

    def test_save_followup_with_evidence_returns_saveable_preview(self, tmp_path: Path) -> None:
        from voxera.vera.evidence_review import ReviewedJobEvidence

        mock_evidence = ReviewedJobEvidence(
            job_id="job-20260401-save",
            state="succeeded",
            lifecycle_state="done",
            terminal_outcome="succeeded",
            approval_status="",
            latest_summary="Deployment completed.",
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
            result = _dispatch(
                message="save the follow-up",
                queue_root=tmp_path,
            )
        assert result.matched is True
        assert result.status == "save_followup_preview_ready"
        assert result.write_preview is True
        assert result.write_handoff_ready is True
        assert result.preview_payload is not None
        # Saveable preview must have write_file with content
        assert "write_file" in result.preview_payload
        wf = result.preview_payload["write_file"]
        assert isinstance(wf, dict)
        assert wf.get("content")
        assert wf.get("path")
        # Assistant text must communicate preview-only
        assert "preview-only" in result.assistant_text.lower()
        assert "nothing has been submitted yet" in result.assistant_text.lower()
        assert "job-20260401-save" in result.assistant_text

    def test_save_followup_as_file_produces_write_file_preview(self, tmp_path: Path) -> None:
        from voxera.vera.evidence_review import ReviewedJobEvidence

        mock_evidence = ReviewedJobEvidence(
            job_id="job-20260401-filsave",
            state="succeeded",
            lifecycle_state="done",
            terminal_outcome="succeeded",
            approval_status="",
            latest_summary="Report generated.",
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
            result = _dispatch(
                message="save the follow-up as a file",
                queue_root=tmp_path,
            )
        assert result.matched is True
        assert result.status == "save_followup_preview_ready"
        wf = result.preview_payload["write_file"]
        # File path must reference the job
        assert "followup-" in str(wf.get("path") or "")
        # Content must be evidence-grounded
        content = str(wf.get("content") or "")
        assert "job-20260401-filsave" in content
        assert "Report generated" in content

    def test_followup_preview_includes_evidence_detail(self, tmp_path: Path) -> None:
        """Follow-up preview replies must include evidence-grounded detail."""
        from voxera.vera.evidence_review import ReviewedJobEvidence

        mock_evidence = ReviewedJobEvidence(
            job_id="job-20260401-detail",
            state="failed",
            lifecycle_state="failed",
            terminal_outcome="failed",
            approval_status="",
            latest_summary="",
            failure_summary="Permission denied on /etc/shadow",
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
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=mock_evidence,
        ):
            result = _dispatch(
                message="now prepare the follow-up",
                queue_root=tmp_path,
            )
        assert result.matched is True
        assert result.status == "followup_preview_ready"
        # Must contain evidence detail about the failure
        assert "failed" in result.assistant_text.lower()
        assert "Permission denied" in result.assistant_text

    def test_followup_goal_for_succeeded_job_is_follow_up_not_inspect(self, tmp_path: Path) -> None:
        """Regression: succeeded-job follow-up goal must say 'follow-up step',
        not 'inspect output details'."""
        from voxera.vera.evidence_review import ReviewedJobEvidence

        mock_evidence = ReviewedJobEvidence(
            job_id="job-20260401-goal",
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
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=mock_evidence,
        ):
            result = _dispatch(
                message="what should we do next based on that",
                queue_root=tmp_path,
            )
        assert result.matched is True
        assert result.status == "followup_preview_ready"
        assert result.preview_payload is not None
        goal = str(result.preview_payload.get("goal") or "")
        assert "follow-up" in goal.lower()
        assert "inspect output details" not in goal.lower()

    def test_revise_from_evidence_for_failed_job_references_failure(self, tmp_path: Path) -> None:
        """Revise from failed job must produce a goal referencing the failure."""
        from voxera.vera.evidence_review import ReviewedJobEvidence

        mock_evidence = ReviewedJobEvidence(
            job_id="job-20260401-failrev",
            state="failed",
            lifecycle_state="failed",
            terminal_outcome="failed",
            approval_status="",
            latest_summary="",
            failure_summary="Disk full on /var/log",
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
            normalized_outcome_class="runtime_error",
            value_forward_text="",
        )
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=mock_evidence,
        ):
            result = _dispatch(
                message="revise that based on the result",
                queue_root=tmp_path,
            )
        assert result.matched is True
        assert result.status == "revised_preview_ready"
        goal = str(result.preview_payload.get("goal") or "")
        assert "revise" in goal.lower()
        assert "Disk full" in goal
        assert "job-20260401-failrev" in goal

    def test_save_followup_for_failed_job_includes_failure_in_content(self, tmp_path: Path) -> None:
        """Save follow-up for a failed job must include failure details in write_file content."""
        from voxera.vera.evidence_review import ReviewedJobEvidence

        mock_evidence = ReviewedJobEvidence(
            job_id="job-20260401-failsave",
            state="failed",
            lifecycle_state="failed",
            terminal_outcome="failed",
            approval_status="",
            latest_summary="",
            failure_summary="OOM killed",
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
            normalized_outcome_class="runtime_error",
            value_forward_text="",
        )
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=mock_evidence,
        ):
            result = _dispatch(
                message="save that follow-up",
                queue_root=tmp_path,
            )
        assert result.matched is True
        assert result.status == "save_followup_preview_ready"
        wf = result.preview_payload["write_file"]
        content = str(wf.get("content") or "")
        assert "OOM killed" in content
        assert "failed" in content.lower()


# ---------------------------------------------------------------------------
# Session-context reference resolution in early-exit dispatch
# ---------------------------------------------------------------------------


class TestSessionContextJobResolution:
    """Session context provides fallback job-ID resolution for review/follow-up."""

    def test_review_uses_session_context_when_handoff_empty(self, tmp_path: Path) -> None:
        """When handoff state has no job_id, session context fallback is used."""
        from voxera.vera.evidence_review import ReviewedJobEvidence

        mock_evidence = ReviewedJobEvidence(
            job_id="inbox-ctx-resolved.json",
            state="succeeded",
            lifecycle_state="done",
            terminal_outcome="succeeded",
            approval_status="not_applicable",
            latest_summary="Context-resolved job",
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
            normalized_outcome_class="succeeded",
            value_forward_text="",
        )
        session_ctx = {"last_completed_job_ref": "inbox-ctx-resolved.json"}
        with (
            patch(
                "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
                return_value=mock_evidence,
            ) as mock_review,
            patch(
                "voxera.vera_web.chat_early_exit_dispatch.read_session_handoff_state",
                return_value={},
            ),
        ):
            result = _dispatch(
                message="summarize the result",
                queue_root=tmp_path,
                session_context=session_ctx,
            )
        assert result.matched is True
        assert result.status == "reviewed_job_outcome"
        # Verify session context job-id was passed to review_job_outcome.
        mock_review.assert_called_once_with(
            queue_root=tmp_path, requested_job_id="inbox-ctx-resolved.json"
        )

    def test_review_sets_last_reviewed_job_ref_in_context_updates(self, tmp_path: Path) -> None:
        """Successful review returns context_updates with last_reviewed_job_ref."""
        from voxera.vera.evidence_review import ReviewedJobEvidence

        mock_evidence = ReviewedJobEvidence(
            job_id="inbox-reviewed.json",
            state="succeeded",
            lifecycle_state="done",
            terminal_outcome="succeeded",
            approval_status="not_applicable",
            latest_summary="Test",
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
            normalized_outcome_class="succeeded",
            value_forward_text="",
        )
        with (
            patch(
                "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
                return_value=mock_evidence,
            ),
            patch(
                "voxera.vera_web.chat_early_exit_dispatch.read_session_handoff_state",
                return_value={"job_id": "inbox-reviewed.json"},
            ),
        ):
            result = _dispatch(message="what happened", queue_root=tmp_path)
        assert result.matched is True
        assert result.context_updates == {"last_reviewed_job_ref": "inbox-reviewed.json"}

    def test_followup_uses_session_context_when_handoff_empty(self, tmp_path: Path) -> None:
        """Follow-up dispatch falls back to session context for job resolution."""
        from voxera.vera.evidence_review import ReviewedJobEvidence

        mock_evidence = ReviewedJobEvidence(
            job_id="inbox-followup-ctx.json",
            state="succeeded",
            lifecycle_state="done",
            terminal_outcome="succeeded",
            approval_status="not_applicable",
            latest_summary="Completed successfully",
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
            normalized_outcome_class="succeeded",
            value_forward_text="",
        )
        session_ctx = {"last_completed_job_ref": "inbox-followup-ctx.json"}
        with (
            patch(
                "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
                return_value=mock_evidence,
            ) as mock_review,
            patch(
                "voxera.vera_web.chat_early_exit_dispatch.read_session_handoff_state",
                return_value={},
            ),
        ):
            result = _dispatch(
                message="draft the follow-up",
                queue_root=tmp_path,
                session_context=session_ctx,
            )
        assert result.matched is True
        assert result.status == "followup_preview_ready"
        mock_review.assert_called_once_with(
            queue_root=tmp_path, requested_job_id="inbox-followup-ctx.json"
        )

    def test_review_fails_closed_with_no_context(self, tmp_path: Path) -> None:
        """No handoff, no session context → review fails closed."""
        with (
            patch(
                "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
                return_value=None,
            ),
            patch(
                "voxera.vera_web.chat_early_exit_dispatch.read_session_handoff_state",
                return_value={},
            ),
        ):
            result = _dispatch(
                message="summarize the result",
                queue_root=tmp_path,
                session_context={},
            )
        assert result.matched is True
        assert result.status == "review_missing_job"

    def test_context_updates_none_by_default(self) -> None:
        """Non-review early exits do not set context_updates."""
        # Near-miss submit is an early exit that doesn't involve job review.
        result = _dispatch(message="submt it")
        assert result.matched is True
        assert result.context_updates is None

    def test_explicit_job_id_takes_precedence_over_context(self, tmp_path: Path) -> None:
        """An explicit requested_job_id always wins over session context."""
        from voxera.vera.evidence_review import ReviewedJobEvidence

        mock_evidence = ReviewedJobEvidence(
            job_id="inbox-explicit.json",
            state="succeeded",
            lifecycle_state="done",
            terminal_outcome="succeeded",
            approval_status="not_applicable",
            latest_summary="Explicit job",
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
            normalized_outcome_class="succeeded",
            value_forward_text="",
        )
        session_ctx = {"last_completed_job_ref": "inbox-context.json"}
        with patch(
            "voxera.vera_web.chat_early_exit_dispatch.review_job_outcome",
            return_value=mock_evidence,
        ) as mock_review:
            result = _dispatch(
                message="review inbox-explicit.json",
                requested_job_id="inbox-explicit.json",
                queue_root=tmp_path,
                session_context=session_ctx,
            )
        assert result.matched is True
        # Explicit job ID was used, not context.
        mock_review.assert_called_once_with(
            queue_root=tmp_path, requested_job_id="inbox-explicit.json"
        )


# ---------------------------------------------------------------------------
# 10. Stale draft reference — fail-closed when no active draft/preview
# ---------------------------------------------------------------------------


class TestStaleDraftReference:
    """Verify stale draft references fail closed instead of reaching builder."""

    def test_save_that_draft_no_active_draft_fails_closed(self):
        """'save that draft' with no active draft/preview → stale_draft_reference."""
        result = _dispatch(message="save that draft", session_context={})
        assert result.matched is True
        assert result.status == "stale_draft_reference"
        assert "no active draft" in result.assistant_text.lower()

    def test_save_the_draft_no_active_draft_fails_closed(self):
        result = _dispatch(message="save the draft", session_context={})
        assert result.matched is True
        assert result.status == "stale_draft_reference"

    def test_the_draft_no_active_draft_fails_closed(self):
        result = _dispatch(message="where is the draft?", session_context={})
        assert result.matched is True
        assert result.status == "stale_draft_reference"

    def test_save_that_draft_with_active_draft_passes_through(self):
        """'save that draft' with an active draft → no match (builder handles)."""
        ctx = {"active_draft_ref": "notes/plan.md", "active_preview_ref": "preview"}
        result = _dispatch(message="save that draft", session_context=ctx)
        assert result.matched is False

    def test_save_that_draft_with_only_preview_ref_passes_through(self):
        """active_preview_ref alone suffices — there is still a preview in play."""
        ctx = {"active_preview_ref": "preview"}
        result = _dispatch(message="save that draft", session_context=ctx)
        assert result.matched is False

    def test_after_handoff_no_draft_context_fails_closed(self):
        """Simulates post-handoff: preview refs are None, job refs are set."""
        ctx = {
            "active_draft_ref": None,
            "active_preview_ref": None,
            "last_submitted_job_ref": "inbox-abc.json",
            "last_completed_job_ref": "inbox-abc.json",
        }
        result = _dispatch(message="save that draft", session_context=ctx)
        assert result.matched is True
        assert result.status == "stale_draft_reference"

    def test_no_session_context_fails_closed(self):
        """None session_context → fail closed for draft reference."""
        result = _dispatch(message="save that draft", session_context=None)
        assert result.matched is True
        assert result.status == "stale_draft_reference"

    def test_non_draft_reference_not_blocked(self):
        """A message without draft-class reference phrases should not be blocked."""
        result = _dispatch(message="tell me more about the weather", session_context={})
        assert result.matched is False

    def test_no_reference_phrase_not_blocked(self):
        """Messages without draft phrases should not be blocked."""
        result = _dispatch(message="write me a poem", session_context={})
        assert result.matched is False
