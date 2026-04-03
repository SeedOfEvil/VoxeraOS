"""Tests for the explicit lifecycle update points in context_lifecycle.py.

Validates:
- Each lifecycle helper updates the correct context fields.
- Preview create/revise/rename sets active_draft_ref and active_preview_ref.
- Preview cleared removes preview-related refs without affecting other fields.
- Handoff clears preview refs and sets submitted job + optional saved file.
- Linked job registration records the submitted job ref.
- Completion ingestion records the completed job ref.
- Review records the reviewed job ref.
- Follow-up preparation sets preview refs and optionally records source job.
- Session clear resets all context to empty defaults.
- Lifecycle events compose correctly across a full workflow sequence.
- Stale/missing context behaves conservatively (fail-closed).
- Repeated lifecycle events remain stable.
"""

from __future__ import annotations

from pathlib import Path

from voxera.vera.context_lifecycle import (
    context_on_completion_ingested,
    context_on_followup_preview_prepared,
    context_on_handoff_submitted,
    context_on_linked_job_registered,
    context_on_preview_cleared,
    context_on_preview_created,
    context_on_review_performed,
    context_on_session_cleared,
)
from voxera.vera.session_store import (
    _empty_shared_context,
    new_session_id,
    read_session_context,
    update_session_context,
)


def _make_session(tmp_path: Path) -> tuple[Path, str]:
    queue = tmp_path / "queue"
    sid = new_session_id()
    return queue, sid


# ---------------------------------------------------------------------------
# 1. Preview lifecycle
# ---------------------------------------------------------------------------


class TestPreviewCreated:
    def test_sets_draft_and_preview_refs(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        ctx = context_on_preview_created(queue, sid, draft_ref="notes/plan.md")
        assert ctx["active_draft_ref"] == "notes/plan.md"
        assert ctx["active_preview_ref"] == "preview"

    def test_default_draft_ref_is_preview(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        ctx = context_on_preview_created(queue, sid)
        assert ctx["active_draft_ref"] == "preview"
        assert ctx["active_preview_ref"] == "preview"

    def test_preserves_unrelated_fields(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        update_session_context(queue, sid, active_topic="my topic")
        ctx = context_on_preview_created(queue, sid, draft_ref="notes/a.md")
        assert ctx["active_topic"] == "my topic"

    def test_revision_updates_draft_ref(self, tmp_path: Path):
        """Simulates preview revision: draft_ref changes."""
        queue, sid = _make_session(tmp_path)
        context_on_preview_created(queue, sid, draft_ref="notes/v1.md")
        ctx = context_on_preview_created(queue, sid, draft_ref="notes/v2.md")
        assert ctx["active_draft_ref"] == "notes/v2.md"
        assert ctx["active_preview_ref"] == "preview"

    def test_rename_save_as_updates_draft_ref(self, tmp_path: Path):
        """Simulates rename/save-as: draft_ref path changes."""
        queue, sid = _make_session(tmp_path)
        context_on_preview_created(queue, sid, draft_ref="notes/old-name.md")
        ctx = context_on_preview_created(queue, sid, draft_ref="notes/new-name.md")
        assert ctx["active_draft_ref"] == "notes/new-name.md"


class TestPreviewCleared:
    def test_clears_preview_refs(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        context_on_preview_created(queue, sid, draft_ref="notes/a.md")
        ctx = context_on_preview_cleared(queue, sid)
        assert ctx["active_draft_ref"] is None
        assert ctx["active_preview_ref"] is None

    def test_preserves_job_refs(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        update_session_context(queue, sid, last_submitted_job_ref="inbox-abc.json")
        context_on_preview_created(queue, sid, draft_ref="notes/a.md")
        ctx = context_on_preview_cleared(queue, sid)
        assert ctx["last_submitted_job_ref"] == "inbox-abc.json"
        assert ctx["active_draft_ref"] is None

    def test_preserves_topic(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        update_session_context(queue, sid, active_topic="research")
        context_on_preview_created(queue, sid, draft_ref="notes/a.md")
        ctx = context_on_preview_cleared(queue, sid)
        assert ctx["active_topic"] == "research"


# ---------------------------------------------------------------------------
# 2. Handoff / submit lifecycle
# ---------------------------------------------------------------------------


class TestHandoffSubmitted:
    def test_clears_preview_and_sets_job(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        context_on_preview_created(queue, sid, draft_ref="notes/plan.md")
        ctx = context_on_handoff_submitted(queue, sid, job_id="inbox-abc.json")
        assert ctx["active_preview_ref"] is None
        assert ctx["active_draft_ref"] is None
        assert ctx["last_submitted_job_ref"] == "inbox-abc.json"

    def test_tracks_saved_file_ref(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        ctx = context_on_handoff_submitted(
            queue, sid, job_id="inbox-xyz.json", saved_file_ref="~/VoxeraOS/notes/report.md"
        )
        assert ctx["last_saved_file_ref"] == "~/VoxeraOS/notes/report.md"

    def test_no_saved_file_when_not_provided(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        ctx = context_on_handoff_submitted(queue, sid, job_id="inbox-abc.json")
        assert ctx["last_saved_file_ref"] is None

    def test_preserves_topic(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        update_session_context(queue, sid, active_topic="deployment")
        ctx = context_on_handoff_submitted(queue, sid, job_id="inbox-123.json")
        assert ctx["active_topic"] == "deployment"


# ---------------------------------------------------------------------------
# 3. Linked job registration
# ---------------------------------------------------------------------------


class TestLinkedJobRegistered:
    def test_sets_submitted_job_ref(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        ctx = context_on_linked_job_registered(queue, sid, job_ref="inbox-abc.json")
        assert ctx["last_submitted_job_ref"] == "inbox-abc.json"


# ---------------------------------------------------------------------------
# 4. Completion ingestion
# ---------------------------------------------------------------------------


class TestCompletionIngested:
    def test_sets_completed_job_ref(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        ctx = context_on_completion_ingested(queue, sid, job_id="inbox-abc.json")
        assert ctx["last_completed_job_ref"] == "inbox-abc.json"

    def test_preserves_submitted_ref(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        update_session_context(queue, sid, last_submitted_job_ref="inbox-abc.json")
        ctx = context_on_completion_ingested(queue, sid, job_id="inbox-abc.json")
        assert ctx["last_submitted_job_ref"] == "inbox-abc.json"
        assert ctx["last_completed_job_ref"] == "inbox-abc.json"


# ---------------------------------------------------------------------------
# 5. Review
# ---------------------------------------------------------------------------


class TestReviewPerformed:
    def test_sets_reviewed_job_ref(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        ctx = context_on_review_performed(queue, sid, job_id="inbox-xyz.json")
        assert ctx["last_reviewed_job_ref"] == "inbox-xyz.json"

    def test_preserves_completed_ref(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        context_on_completion_ingested(queue, sid, job_id="inbox-abc.json")
        ctx = context_on_review_performed(queue, sid, job_id="inbox-abc.json")
        assert ctx["last_completed_job_ref"] == "inbox-abc.json"
        assert ctx["last_reviewed_job_ref"] == "inbox-abc.json"


# ---------------------------------------------------------------------------
# 6. Follow-up / continuation preview
# ---------------------------------------------------------------------------


class TestFollowupPreviewPrepared:
    def test_sets_preview_refs(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        ctx = context_on_followup_preview_prepared(queue, sid)
        assert ctx["active_draft_ref"] == "preview"
        assert ctx["active_preview_ref"] == "preview"

    def test_custom_draft_ref(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        ctx = context_on_followup_preview_prepared(queue, sid, draft_ref="notes/followup-abc.md")
        assert ctx["active_draft_ref"] == "notes/followup-abc.md"

    def test_records_source_job_as_reviewed(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        ctx = context_on_followup_preview_prepared(queue, sid, source_job_id="inbox-abc.json")
        assert ctx["last_reviewed_job_ref"] == "inbox-abc.json"

    def test_no_source_job_does_not_overwrite_reviewed(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        context_on_review_performed(queue, sid, job_id="inbox-old.json")
        ctx = context_on_followup_preview_prepared(queue, sid)
        assert ctx["last_reviewed_job_ref"] == "inbox-old.json"


# ---------------------------------------------------------------------------
# 7. Session clear
# ---------------------------------------------------------------------------


class TestSessionCleared:
    def test_resets_all_fields(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        context_on_preview_created(queue, sid, draft_ref="notes/a.md")
        context_on_handoff_submitted(queue, sid, job_id="inbox-abc.json")
        context_on_completion_ingested(queue, sid, job_id="inbox-abc.json")
        context_on_review_performed(queue, sid, job_id="inbox-abc.json")
        context_on_session_cleared(queue, sid)
        ctx = read_session_context(queue, sid)
        assert ctx == _empty_shared_context()


# ---------------------------------------------------------------------------
# 8. Full lifecycle sequence
# ---------------------------------------------------------------------------


class TestFullLifecycleSequence:
    def test_draft_to_review_sequence(self, tmp_path: Path):
        """Walk through: draft created → refined → rename → submit → complete → review."""
        queue, sid = _make_session(tmp_path)

        # Draft created
        ctx = context_on_preview_created(queue, sid, draft_ref="notes/report.md")
        assert ctx["active_draft_ref"] == "notes/report.md"
        assert ctx["active_preview_ref"] == "preview"

        # Draft refined
        ctx = context_on_preview_created(queue, sid, draft_ref="notes/report.md")
        assert ctx["active_draft_ref"] == "notes/report.md"

        # Rename / save-as
        ctx = context_on_preview_created(queue, sid, draft_ref="notes/final-report.md")
        assert ctx["active_draft_ref"] == "notes/final-report.md"

        # Submit
        ctx = context_on_handoff_submitted(
            queue, sid, job_id="inbox-rpt.json", saved_file_ref="~/VoxeraOS/notes/final-report.md"
        )
        assert ctx["active_preview_ref"] is None
        assert ctx["active_draft_ref"] is None
        assert ctx["last_submitted_job_ref"] == "inbox-rpt.json"
        assert ctx["last_saved_file_ref"] == "~/VoxeraOS/notes/final-report.md"

        # Completion
        ctx = context_on_completion_ingested(queue, sid, job_id="inbox-rpt.json")
        assert ctx["last_completed_job_ref"] == "inbox-rpt.json"

        # Review
        ctx = context_on_review_performed(queue, sid, job_id="inbox-rpt.json")
        assert ctx["last_reviewed_job_ref"] == "inbox-rpt.json"

        # All refs consistent
        ctx = read_session_context(queue, sid)
        assert ctx["last_submitted_job_ref"] == "inbox-rpt.json"
        assert ctx["last_completed_job_ref"] == "inbox-rpt.json"
        assert ctx["last_reviewed_job_ref"] == "inbox-rpt.json"
        assert ctx["active_preview_ref"] is None

    def test_followup_from_evidence_sequence(self, tmp_path: Path):
        """Completion → review → follow-up preview → submit follow-up."""
        queue, sid = _make_session(tmp_path)

        context_on_handoff_submitted(queue, sid, job_id="inbox-orig.json")
        context_on_completion_ingested(queue, sid, job_id="inbox-orig.json")
        context_on_review_performed(queue, sid, job_id="inbox-orig.json")

        # Follow-up prepared
        ctx = context_on_followup_preview_prepared(queue, sid, source_job_id="inbox-orig.json")
        assert ctx["active_preview_ref"] == "preview"
        assert ctx["last_reviewed_job_ref"] == "inbox-orig.json"

        # Submit the follow-up
        ctx = context_on_handoff_submitted(queue, sid, job_id="inbox-followup.json")
        assert ctx["active_preview_ref"] is None
        assert ctx["last_submitted_job_ref"] == "inbox-followup.json"
        # Prior job refs should still be present
        assert ctx["last_completed_job_ref"] == "inbox-orig.json"

    def test_save_followup_sequence(self, tmp_path: Path):
        """Completion → review → save-follow-up with file path."""
        queue, sid = _make_session(tmp_path)

        context_on_handoff_submitted(queue, sid, job_id="inbox-main.json")
        context_on_completion_ingested(queue, sid, job_id="inbox-main.json")

        # Save-follow-up prepared with file path
        ctx = context_on_followup_preview_prepared(
            queue,
            sid,
            draft_ref="~/VoxeraOS/notes/followup-inbox-main.md",
            source_job_id="inbox-main.json",
        )
        assert ctx["active_draft_ref"] == "~/VoxeraOS/notes/followup-inbox-main.md"
        assert ctx["active_preview_ref"] == "preview"
        assert ctx["last_reviewed_job_ref"] == "inbox-main.json"

    def test_stale_preview_cleanup_then_new_draft(self, tmp_path: Path):
        """Preview cleared → new draft created."""
        queue, sid = _make_session(tmp_path)
        context_on_preview_created(queue, sid, draft_ref="notes/stale.md")
        context_on_preview_cleared(queue, sid)

        ctx = read_session_context(queue, sid)
        assert ctx["active_draft_ref"] is None
        assert ctx["active_preview_ref"] is None

        # New draft
        ctx = context_on_preview_created(queue, sid, draft_ref="notes/fresh.md")
        assert ctx["active_draft_ref"] == "notes/fresh.md"

    def test_repeated_completions_stay_stable(self, tmp_path: Path):
        """Multiple completion ingestions overwrite to the latest."""
        queue, sid = _make_session(tmp_path)
        context_on_completion_ingested(queue, sid, job_id="inbox-1.json")
        context_on_completion_ingested(queue, sid, job_id="inbox-2.json")
        ctx = read_session_context(queue, sid)
        assert ctx["last_completed_job_ref"] == "inbox-2.json"

    def test_session_clear_then_fresh_chat_fail_closed(self, tmp_path: Path):
        """After session clear, context is empty — resolution should fail closed."""
        queue, sid = _make_session(tmp_path)
        context_on_preview_created(queue, sid, draft_ref="notes/a.md")
        context_on_handoff_submitted(queue, sid, job_id="inbox-abc.json")
        context_on_session_cleared(queue, sid)

        ctx = read_session_context(queue, sid)
        assert ctx == _empty_shared_context()
        # All refs are None — fail-closed for any resolution attempt
        assert ctx["active_draft_ref"] is None
        assert ctx["last_submitted_job_ref"] is None
        assert ctx["last_completed_job_ref"] is None
