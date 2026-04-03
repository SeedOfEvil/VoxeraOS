"""Regression tests for session-aware authored drafting and planning continuity.

These tests protect the bounded seam added to make authored drafting and
planning flows feel naturally session-aware without weakening VoxeraOS
trust boundaries.

Covered behaviour:
- Active authored draft refinement (concise, checklist, tone shifts)
- Session-context-aware follow-up when no active preview exists
- Planning-style multi-turn continuity
- Fail-closed fresh-session behaviour
- Adjacent regression anchors for preview wording and wrong-mode replies

Architectural rules preserved:
- Session context is a continuity aid only, not a truth surface.
- Preview truth > Queue truth > Artifact truth > Session context.
- Fail closed when there is no valid authored/planning target.
- No cross-session memory.
"""

from __future__ import annotations

import pytest

from voxera.vera.draft_revision import refined_content_from_active_preview
from voxera.vera.preview_drafting import (
    _is_session_aware_authored_followup,
    _resolve_authored_followup_from_session_context,
    maybe_draft_job_payload,
)

# ---------------------------------------------------------------------------
# Unit tests: authored-content transformation patterns
# ---------------------------------------------------------------------------


class TestRefinedContentTransformations:
    """Tests for new transformation patterns in refined_content_from_active_preview."""

    @pytest.fixture()
    def sample_content(self) -> str:
        return (
            "Check disk usage. Review service logs. Restart failing services. Update documentation."
        )

    def test_make_that_more_concise_compresses(self, sample_content: str) -> None:
        result = refined_content_from_active_preview(
            text="make that more concise",
            lowered="make that more concise",
            existing_content=sample_content,
        )
        assert result is not None
        assert len(result) < len(sample_content)

    def test_more_concise_with_single_sentence(self) -> None:
        result = refined_content_from_active_preview(
            text="more concise",
            lowered="more concise",
            existing_content="A single sentence about a topic here in the world.",
        )
        assert result is not None
        # Single sentence gets word-trimmed, not split
        words = result.split()
        assert len(words) < 10

    def test_more_concise_with_empty_content_returns_none(self) -> None:
        result = refined_content_from_active_preview(
            text="more concise",
            lowered="more concise",
            existing_content="",
        )
        assert result is None

    def test_turn_into_checklist(self, sample_content: str) -> None:
        result = refined_content_from_active_preview(
            text="turn that into a checklist",
            lowered="turn that into a checklist",
            existing_content=sample_content,
        )
        assert result is not None
        assert result.startswith("- ")
        lines = result.strip().split("\n")
        assert len(lines) >= 2
        assert all(line.startswith("- ") for line in lines)

    def test_convert_into_bullet_list(self, sample_content: str) -> None:
        result = refined_content_from_active_preview(
            text="turn this into a bullet list",
            lowered="turn this into a bullet list",
            existing_content=sample_content,
        )
        assert result is not None
        assert "- " in result

    def test_into_a_checklist_shorthand(self, sample_content: str) -> None:
        result = refined_content_from_active_preview(
            text="as a checklist",
            lowered="as a checklist",
            existing_content=sample_content,
        )
        assert result is not None
        assert result.startswith("- ")

    def test_checklist_with_empty_content_returns_none(self) -> None:
        result = refined_content_from_active_preview(
            text="turn that into a checklist",
            lowered="turn that into a checklist",
            existing_content="",
        )
        assert result is None

    def test_more_operator_facing(self) -> None:
        result = refined_content_from_active_preview(
            text="make that more operator-facing",
            lowered="make that more operator-facing",
            existing_content="Check the logs.",
        )
        assert result is not None
        assert "[Operator-facing]" in result
        assert "Check the logs." in result

    def test_more_user_facing(self) -> None:
        result = refined_content_from_active_preview(
            text="make that more user-friendly",
            lowered="make that more user-friendly",
            existing_content="Run diagnostics.",
        )
        assert result is not None
        assert "[User-facing]" in result
        assert "Run diagnostics." in result

    def test_keep_same_tone_preserves_content(self) -> None:
        result = refined_content_from_active_preview(
            text="keep the same tone",
            lowered="keep the same tone",
            existing_content="Brief and clear.",
        )
        assert result == "Brief and clear."

    def test_keep_same_tone_with_empty_content_returns_none(self) -> None:
        result = refined_content_from_active_preview(
            text="keep the same tone",
            lowered="keep the same tone",
            existing_content="",
        )
        assert result is None


# ---------------------------------------------------------------------------
# Unit tests: session-aware authored follow-up detection
# ---------------------------------------------------------------------------


class TestSessionAwareAuthoredFollowupDetection:
    """Tests for _is_session_aware_authored_followup pattern matching."""

    @pytest.mark.parametrize(
        "message",
        [
            "make that more concise",
            "turn that into a checklist",
            "make it more operator-facing",
            "more user-facing",
            "keep the same tone",
            "make that more formal",
            "make that shorter",
            "more formal tone",
        ],
    )
    def test_recognizes_authored_followup_patterns(self, message: str) -> None:
        assert _is_session_aware_authored_followup(message) is True

    @pytest.mark.parametrize(
        "message",
        [
            "change it",
            "fix it",
            "make it better",
            "hello",
            "what is 2+2",
            "open google.com",
            "write a note",
            "",
            # Intentionally excluded: patterns with no resolution handler
            "continue that plan",
            "make it more detailed",
            "less verbose",
            "more technical",
            "make it longer",
        ],
    )
    def test_rejects_non_followup_patterns(self, message: str) -> None:
        assert _is_session_aware_authored_followup(message) is False


# ---------------------------------------------------------------------------
# Unit tests: session-context-aware resolution
# ---------------------------------------------------------------------------


class TestResolveAuthoredFollowupFromSessionContext:
    """Tests for _resolve_authored_followup_from_session_context."""

    @pytest.fixture()
    def active_context(self) -> dict:
        return {
            "active_draft_ref": "~/VoxeraOS/notes/plan.txt",
            "active_preview_ref": "preview",
        }

    @pytest.fixture()
    def artifacts(self) -> list[dict[str, str]]:
        return [
            {"content": "Step one. Step two. Step three.", "artifact_type": "explanation"},
        ]

    def test_resolves_concise_with_active_context(
        self, active_context: dict, artifacts: list
    ) -> None:
        result = _resolve_authored_followup_from_session_context(
            "make that more concise",
            session_context=active_context,
            assistant_artifacts=artifacts,
        )
        assert result is not None
        assert "write_file" in result
        assert len(result["write_file"]["content"]) < len(artifacts[0]["content"])

    def test_resolves_checklist_with_active_context(
        self, active_context: dict, artifacts: list
    ) -> None:
        result = _resolve_authored_followup_from_session_context(
            "turn that into a checklist",
            session_context=active_context,
            assistant_artifacts=artifacts,
        )
        assert result is not None
        assert result["write_file"]["content"].startswith("- ")

    def test_preserves_draft_ref_path_when_file_like(
        self, active_context: dict, artifacts: list
    ) -> None:
        result = _resolve_authored_followup_from_session_context(
            "make that more concise",
            session_context=active_context,
            assistant_artifacts=artifacts,
        )
        assert result is not None
        assert result["write_file"]["path"] == "~/VoxeraOS/notes/plan.txt"

    def test_fail_closed_no_session_context(self, artifacts: list) -> None:
        result = _resolve_authored_followup_from_session_context(
            "make that more concise",
            session_context=None,
            assistant_artifacts=artifacts,
        )
        assert result is None

    def test_fail_closed_no_active_draft_ref(self, artifacts: list) -> None:
        result = _resolve_authored_followup_from_session_context(
            "make that more concise",
            session_context={"active_draft_ref": None},
            assistant_artifacts=artifacts,
        )
        assert result is None

    def test_fail_closed_empty_draft_ref(self, artifacts: list) -> None:
        result = _resolve_authored_followup_from_session_context(
            "make that more concise",
            session_context={"active_draft_ref": "  "},
            assistant_artifacts=artifacts,
        )
        assert result is None

    def test_fail_closed_no_artifacts(self, active_context: dict) -> None:
        result = _resolve_authored_followup_from_session_context(
            "make that more concise",
            session_context=active_context,
            assistant_artifacts=[],
        )
        assert result is None

    def test_fail_closed_empty_artifact_content(self, active_context: dict) -> None:
        result = _resolve_authored_followup_from_session_context(
            "make that more concise",
            session_context=active_context,
            assistant_artifacts=[{"content": "", "artifact_type": "explanation"}],
        )
        assert result is None

    def test_fail_closed_non_followup_message(self, active_context: dict, artifacts: list) -> None:
        result = _resolve_authored_followup_from_session_context(
            "what is 2+2",
            session_context=active_context,
            assistant_artifacts=artifacts,
        )
        assert result is None

    def test_generates_path_for_non_file_draft_ref(self, artifacts: list) -> None:
        result = _resolve_authored_followup_from_session_context(
            "make that more concise",
            session_context={"active_draft_ref": "preview"},
            assistant_artifacts=artifacts,
        )
        assert result is not None
        assert result["write_file"]["path"].startswith("~/VoxeraOS/notes/note-")


# ---------------------------------------------------------------------------
# Unit tests: maybe_draft_job_payload with session_context
# ---------------------------------------------------------------------------


class TestMaybeDraftJobPayloadSessionContext:
    """Tests for session_context parameter in maybe_draft_job_payload."""

    def test_session_context_followup_creates_preview_when_no_active_preview(self) -> None:
        result = maybe_draft_job_payload(
            "make that more concise",
            active_preview=None,
            session_context={
                "active_draft_ref": "~/VoxeraOS/notes/plan.txt",
            },
            recent_assistant_artifacts=[
                {"content": "First point. Second point. Third point.", "artifact_type": "summary"},
            ],
        )
        assert result is not None
        assert "write_file" in result
        assert len(result["write_file"]["content"]) < len("First point. Second point. Third point.")

    def test_active_preview_takes_precedence_over_session_context(self) -> None:
        """When active_preview exists, session_context follow-up path is skipped."""
        active_preview = {
            "goal": "write a file called plan.txt with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/plan.txt",
                "content": "Original plan content here.",
                "mode": "overwrite",
            },
        }
        result = maybe_draft_job_payload(
            "make it shorter",
            active_preview=active_preview,
            session_context={
                "active_draft_ref": "~/VoxeraOS/notes/plan.txt",
            },
            recent_assistant_artifacts=[
                {"content": "Different artifact content.", "artifact_type": "summary"},
            ],
        )
        # Active preview revision path handles this, not session_context path
        if result is not None:
            # Content should come from active preview, not session artifacts
            assert "Different artifact content" not in str(result)

    def test_session_context_none_is_safe(self) -> None:
        """Passing session_context=None should not break anything."""
        result = maybe_draft_job_payload(
            "make that more concise",
            active_preview=None,
            session_context=None,
        )
        assert result is None

    def test_fresh_session_fail_closed(self) -> None:
        """Fresh session with no context should fail closed."""
        result = maybe_draft_job_payload(
            "make that more concise",
            active_preview=None,
            session_context={},
        )
        assert result is None

    def test_ambiguous_change_request_still_fails_closed(self) -> None:
        """Ambiguous patterns like 'change it' must not resolve via session context."""
        result = maybe_draft_job_payload(
            "change it",
            active_preview=None,
            session_context={
                "active_draft_ref": "~/VoxeraOS/notes/plan.txt",
            },
            recent_assistant_artifacts=[
                {"content": "Some content.", "artifact_type": "summary"},
            ],
        )
        assert result is None


# ---------------------------------------------------------------------------
# Unit tests: planning continuation patterns
# ---------------------------------------------------------------------------


class TestContextualRefinementPatterns:
    """Tests that session-aware phrases are recognized as contextual refinement."""

    def test_keep_same_tone_is_contextual_refinement(self) -> None:
        from voxera.vera.preview_drafting import _looks_like_contextual_refinement

        assert _looks_like_contextual_refinement("keep the same tone") is True
        assert _looks_like_contextual_refinement("keep same tone") is True

    def test_more_concise_is_contextual_refinement(self) -> None:
        from voxera.vera.preview_drafting import _looks_like_contextual_refinement

        assert _looks_like_contextual_refinement("more concise") is True

    def test_into_a_checklist_is_contextual_refinement(self) -> None:
        from voxera.vera.preview_drafting import _looks_like_contextual_refinement

        assert _looks_like_contextual_refinement("into a checklist") is True

    def test_more_formal_is_contextual_refinement(self) -> None:
        from voxera.vera.preview_drafting import _looks_like_contextual_refinement

        assert _looks_like_contextual_refinement("more formal") is True

    def test_continue_that_plan_is_not_contextual_refinement(self) -> None:
        """'continue that plan' has no resolution handler — must not trigger refinement."""
        from voxera.vera.preview_drafting import _looks_like_contextual_refinement

        assert _looks_like_contextual_refinement("continue that plan") is False


# ---------------------------------------------------------------------------
# Regression anchors: trust boundary invariants
# ---------------------------------------------------------------------------


class TestDetectionResolutionHonesty:
    """Every pattern detected by _is_session_aware_authored_followup must resolve."""

    @pytest.mark.parametrize(
        "message",
        [
            "make that more concise",
            "turn that into a checklist",
            "make it more operator-facing",
            "more user-facing",
            "keep the same tone",
            "make that more formal",
            "make that shorter",
        ],
    )
    def test_detected_patterns_actually_resolve(self, message: str) -> None:
        """If detection fires, resolution must produce a result (no dead patterns)."""
        assert _is_session_aware_authored_followup(message) is True
        result = _resolve_authored_followup_from_session_context(
            message,
            session_context={"active_draft_ref": "~/VoxeraOS/notes/plan.txt"},
            assistant_artifacts=[
                {
                    "content": "Point one. Point two. Point three.",
                    "artifact_type": "explanation",
                },
            ],
        )
        assert result is not None, f"'{message}' detected but resolution returned None"
        assert "write_file" in result


class TestTrustBoundaryInvariants:
    """Regression anchors ensuring session context does not become a truth surface."""

    def test_session_context_does_not_claim_submission(self) -> None:
        """Session context follow-up must not produce submission language."""
        result = _resolve_authored_followup_from_session_context(
            "make that more concise",
            session_context={"active_draft_ref": "~/VoxeraOS/notes/plan.txt"},
            assistant_artifacts=[
                {"content": "Step one. Step two.", "artifact_type": "explanation"},
            ],
        )
        assert result is not None
        goal = result.get("goal", "")
        assert "submit" not in goal.lower()
        assert "queue" not in goal.lower()
        assert "hand" not in goal.lower()

    def test_runtime_text_not_promoted_to_authored(self) -> None:
        """Non-authored assistant content should not be saveable as a draft."""
        # Artifacts list is empty (no authored content was produced)
        result = _resolve_authored_followup_from_session_context(
            "make that more concise",
            session_context={"active_draft_ref": "preview"},
            assistant_artifacts=[],
        )
        assert result is None

    def test_mode_field_is_always_overwrite(self) -> None:
        """Session-context follow-up preview should use overwrite mode."""
        result = _resolve_authored_followup_from_session_context(
            "turn that into a checklist",
            session_context={"active_draft_ref": "~/VoxeraOS/notes/plan.txt"},
            assistant_artifacts=[
                {"content": "First. Second. Third.", "artifact_type": "explanation"},
            ],
        )
        assert result is not None
        assert result["write_file"]["mode"] == "overwrite"
