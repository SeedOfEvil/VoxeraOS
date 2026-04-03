"""Tests for the bounded session-scoped reference resolution layer.

Coverage:
  - classify_reference: phrase → reference class mapping
  - resolve_session_reference: happy paths for all reference classes
  - resolve_session_reference: fail-closed on missing context
  - resolve_session_reference: fail-closed on empty/invalid context
  - resolve_job_id_from_context: priority ordering and fallback
  - ambiguous/unrecognized references fail closed

Trust-sensitive boundaries verified:
  - Missing references always produce UnresolvedReference
  - Empty session context always produces UnresolvedReference
  - No guessing or speculative resolution
  - Reference classification is bounded and conservative
"""

from __future__ import annotations

import pytest

from voxera.vera.reference_resolver import (
    ReferenceClass,
    ResolvedReference,
    UnresolvedReference,
    classify_reference,
    resolve_job_id_from_context,
    resolve_session_reference,
)

# ---------------------------------------------------------------------------
# classify_reference
# ---------------------------------------------------------------------------


class TestClassifyReference:
    """Phrase → reference class mapping."""

    @pytest.mark.parametrize(
        "message,expected",
        [
            ("save that draft", ReferenceClass.DRAFT),
            ("save the draft", ReferenceClass.DRAFT),
            ("use the last draft", ReferenceClass.DRAFT),
            ("the current draft", ReferenceClass.DRAFT),
            ("the active draft", ReferenceClass.DRAFT),
            ("my draft", ReferenceClass.DRAFT),
        ],
    )
    def test_draft_phrases(self, message: str, expected: ReferenceClass) -> None:
        assert classify_reference(message) == expected

    @pytest.mark.parametrize(
        "message,expected",
        [
            ("rename that file", ReferenceClass.FILE),
            ("rename the file", ReferenceClass.FILE),
            ("the saved file", ReferenceClass.FILE),
            ("rename that note", ReferenceClass.FILE),
            ("the note", ReferenceClass.FILE),
        ],
    )
    def test_file_phrases(self, message: str, expected: ReferenceClass) -> None:
        assert classify_reference(message) == expected

    @pytest.mark.parametrize(
        "message,expected",
        [
            ("summarize that result", ReferenceClass.JOB_RESULT),
            ("the result", ReferenceClass.JOB_RESULT),
            ("that job", ReferenceClass.JOB_RESULT),
            ("the last job", ReferenceClass.JOB_RESULT),
            ("the outcome", ReferenceClass.JOB_RESULT),
        ],
    )
    def test_job_result_phrases(self, message: str, expected: ReferenceClass) -> None:
        assert classify_reference(message) == expected

    @pytest.mark.parametrize(
        "message,expected",
        [
            ("save that follow-up", ReferenceClass.CONTINUATION),
            ("the follow-up", ReferenceClass.CONTINUATION),
            ("save the followup", ReferenceClass.CONTINUATION),
            ("the last one", ReferenceClass.CONTINUATION),
            ("that one", ReferenceClass.CONTINUATION),
        ],
    )
    def test_continuation_phrases(self, message: str, expected: ReferenceClass) -> None:
        assert classify_reference(message) == expected

    def test_no_recognizable_phrase(self) -> None:
        assert classify_reference("hello world") is None

    def test_empty_message(self) -> None:
        assert classify_reference("") is None

    def test_whitespace_only(self) -> None:
        assert classify_reference("   ") is None

    def test_case_insensitive(self) -> None:
        assert classify_reference("Save That Draft") == ReferenceClass.DRAFT

    def test_continuation_takes_priority_over_draft(self) -> None:
        """'the last one' is continuation, not draft, even if 'last' appears."""
        assert classify_reference("the last one") == ReferenceClass.CONTINUATION


# ---------------------------------------------------------------------------
# resolve_session_reference — happy paths
# ---------------------------------------------------------------------------


class TestResolveSessionReferenceHappy:
    """Successful resolution when context provides a clear referent."""

    def test_draft_from_active_draft_ref(self) -> None:
        ctx = {"active_draft_ref": "~/notes/todo.md", "active_preview_ref": "preview"}
        result = resolve_session_reference("save that draft", ctx)
        assert isinstance(result, ResolvedReference)
        assert result.reference_class == ReferenceClass.DRAFT
        assert result.value == "~/notes/todo.md"
        assert result.source == "active_draft_ref"

    def test_draft_falls_back_to_preview_ref(self) -> None:
        ctx = {"active_draft_ref": None, "active_preview_ref": "preview"}
        result = resolve_session_reference("save the draft", ctx)
        assert isinstance(result, ResolvedReference)
        assert result.reference_class == ReferenceClass.DRAFT
        assert result.value == "preview"
        assert result.source == "active_preview_ref"

    def test_file_from_last_saved_file_ref(self) -> None:
        ctx = {"last_saved_file_ref": "~/notes/meeting.md"}
        result = resolve_session_reference("rename that file", ctx)
        assert isinstance(result, ResolvedReference)
        assert result.reference_class == ReferenceClass.FILE
        assert result.value == "~/notes/meeting.md"
        assert result.source == "last_saved_file_ref"

    def test_file_falls_back_to_draft_ref_when_path_like(self) -> None:
        ctx = {"last_saved_file_ref": None, "active_draft_ref": "~/notes/todo.md"}
        result = resolve_session_reference("rename the file", ctx)
        assert isinstance(result, ResolvedReference)
        assert result.reference_class == ReferenceClass.FILE
        assert result.value == "~/notes/todo.md"
        assert result.source == "active_draft_ref"

    def test_file_no_fallback_when_draft_ref_is_not_path_like(self) -> None:
        """'preview' is not a file path — should not resolve."""
        ctx = {"last_saved_file_ref": None, "active_draft_ref": "preview"}
        result = resolve_session_reference("rename the file", ctx)
        assert isinstance(result, UnresolvedReference)

    def test_job_result_from_completed_job(self) -> None:
        ctx = {"last_completed_job_ref": "inbox-abc123.json"}
        result = resolve_session_reference("summarize that result", ctx)
        assert isinstance(result, ResolvedReference)
        assert result.reference_class == ReferenceClass.JOB_RESULT
        assert result.value == "inbox-abc123.json"
        assert result.source == "last_completed_job_ref"

    def test_job_result_priority_order(self) -> None:
        """completed > reviewed > submitted."""
        ctx = {
            "last_completed_job_ref": "completed-job.json",
            "last_reviewed_job_ref": "reviewed-job.json",
            "last_submitted_job_ref": "submitted-job.json",
        }
        result = resolve_session_reference("the result", ctx)
        assert isinstance(result, ResolvedReference)
        assert result.value == "completed-job.json"
        assert result.source == "last_completed_job_ref"

    def test_job_result_falls_to_reviewed(self) -> None:
        ctx = {
            "last_completed_job_ref": None,
            "last_reviewed_job_ref": "reviewed-job.json",
            "last_submitted_job_ref": "submitted-job.json",
        }
        result = resolve_session_reference("the result", ctx)
        assert isinstance(result, ResolvedReference)
        assert result.value == "reviewed-job.json"

    def test_job_result_falls_to_submitted(self) -> None:
        ctx = {
            "last_completed_job_ref": None,
            "last_reviewed_job_ref": None,
            "last_submitted_job_ref": "submitted-job.json",
        }
        result = resolve_session_reference("the result", ctx)
        assert isinstance(result, ResolvedReference)
        assert result.value == "submitted-job.json"

    def test_continuation_from_active_preview(self) -> None:
        ctx = {"active_preview_ref": "preview"}
        result = resolve_session_reference("save that follow-up", ctx)
        assert isinstance(result, ResolvedReference)
        assert result.reference_class == ReferenceClass.CONTINUATION
        assert result.source == "active_preview_ref"

    def test_continuation_falls_to_completed_job(self) -> None:
        ctx = {"active_preview_ref": None, "last_completed_job_ref": "job-done.json"}
        result = resolve_session_reference("the follow-up", ctx)
        assert isinstance(result, ResolvedReference)
        assert result.reference_class == ReferenceClass.CONTINUATION
        assert result.value == "job-done.json"


# ---------------------------------------------------------------------------
# resolve_session_reference — fail-closed paths
# ---------------------------------------------------------------------------


class TestResolveSessionReferenceFailClosed:
    """Missing or ambiguous references fail closed."""

    def test_no_reference_phrase(self) -> None:
        result = resolve_session_reference("hello world", {})
        assert isinstance(result, UnresolvedReference)
        assert result.reference_class is None
        assert result.reason == "no_recognizable_reference"

    def test_draft_no_active_context(self) -> None:
        result = resolve_session_reference("save that draft", {})
        assert isinstance(result, UnresolvedReference)
        assert result.reference_class == ReferenceClass.DRAFT
        assert result.reason == "no_active_draft_or_preview"

    def test_file_no_saved_context(self) -> None:
        result = resolve_session_reference("rename that file", {})
        assert isinstance(result, UnresolvedReference)
        assert result.reference_class == ReferenceClass.FILE
        assert result.reason == "no_saved_file_reference"

    def test_job_result_no_job_context(self) -> None:
        result = resolve_session_reference("summarize the result", {})
        assert isinstance(result, UnresolvedReference)
        assert result.reference_class == ReferenceClass.JOB_RESULT
        assert result.reason == "no_job_or_result_reference"

    def test_continuation_no_context(self) -> None:
        result = resolve_session_reference("save that follow-up", {})
        assert isinstance(result, UnresolvedReference)
        assert result.reference_class == ReferenceClass.CONTINUATION
        assert result.reason == "no_continuation_reference"

    def test_empty_string_refs_treated_as_missing(self) -> None:
        ctx = {"active_draft_ref": "", "active_preview_ref": "   "}
        result = resolve_session_reference("save that draft", ctx)
        assert isinstance(result, UnresolvedReference)

    def test_none_context_treated_as_empty(self) -> None:
        # Passing a non-dict is handled gracefully.
        result = resolve_session_reference("save that draft", None)  # type: ignore[arg-type]
        assert isinstance(result, UnresolvedReference)

    def test_fresh_session_follow_up_fails_closed(self) -> None:
        """Fresh chat → 'save that follow-up' must fail closed."""
        result = resolve_session_reference("save that follow-up", {})
        assert isinstance(result, UnresolvedReference)
        assert result.reason == "no_continuation_reference"


# ---------------------------------------------------------------------------
# resolve_job_id_from_context
# ---------------------------------------------------------------------------


class TestResolveJobIdFromContext:
    """Job-ID fallback resolution for early-exit dispatch."""

    def test_completed_first(self) -> None:
        ctx = {
            "last_completed_job_ref": "completed.json",
            "last_reviewed_job_ref": "reviewed.json",
            "last_submitted_job_ref": "submitted.json",
        }
        assert resolve_job_id_from_context(ctx) == "completed.json"

    def test_reviewed_second(self) -> None:
        ctx = {
            "last_completed_job_ref": None,
            "last_reviewed_job_ref": "reviewed.json",
            "last_submitted_job_ref": "submitted.json",
        }
        assert resolve_job_id_from_context(ctx) == "reviewed.json"

    def test_submitted_third(self) -> None:
        ctx = {
            "last_completed_job_ref": None,
            "last_reviewed_job_ref": None,
            "last_submitted_job_ref": "submitted.json",
        }
        assert resolve_job_id_from_context(ctx) == "submitted.json"

    def test_none_when_empty(self) -> None:
        assert resolve_job_id_from_context({}) is None

    def test_none_when_all_blank(self) -> None:
        ctx = {
            "last_completed_job_ref": "",
            "last_reviewed_job_ref": "  ",
            "last_submitted_job_ref": None,
        }
        assert resolve_job_id_from_context(ctx) is None

    def test_non_dict_returns_none(self) -> None:
        assert resolve_job_id_from_context(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Canonical truth boundary invariants
# ---------------------------------------------------------------------------


class TestTruthBoundaryInvariants:
    """Session-scoped references must never override canonical truth."""

    def test_resolver_returns_ref_value_only(self) -> None:
        """The resolver returns string refs — never canonical objects.
        Callers validate against preview/queue/artifact truth downstream."""
        ctx = {"active_draft_ref": "~/notes/draft.md", "active_preview_ref": "preview"}
        result = resolve_session_reference("save that draft", ctx)
        assert isinstance(result, ResolvedReference)
        # The value is a string hint, not a preview object.
        assert isinstance(result.value, str)

    def test_result_references_are_evidence_grounded_strings(self) -> None:
        """Job result refs are job-ID strings — actual evidence is validated
        by the review_job_outcome call downstream, not by the resolver."""
        ctx = {"last_completed_job_ref": "inbox-abc.json"}
        result = resolve_session_reference("summarize the result", ctx)
        assert isinstance(result, ResolvedReference)
        assert isinstance(result.value, str)
