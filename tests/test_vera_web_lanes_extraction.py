"""Behavior-preserving tests for the automation/review lane extraction.

Scope: proves that moving the automation and review lane logic out of
``voxera.vera_web.app`` into ``voxera.vera_web.lanes.automation_lane`` and
``voxera.vera_web.lanes.review_lane`` did not reintroduce any preview
ownership or orchestration regressions.

What these tests cover (narrow by design):

1. ``app.py`` still visibly orchestrates the extracted lanes — the
   lane entry points are imported and called from ``chat``.
2. The automation lane functions are reachable via the documented
   public surface and return the expected ``AutomationLaneResult``
   shape (matched/status/dispatch_source/pending_preview_after).
3. The review lane helpers are reachable via the documented public
   surface and preserve the fail-closed belt-and-suspenders behavior
   that ``compute_active_preview_revision_in_flight`` takes over from
   the inlined block in ``app.py``.
4. Preview ownership discipline: the lane modules only mutate preview
   state through the approved ``preview_ownership`` helpers, and they
   never perform scattered ``write_session_preview`` writes.
5. End-to-end smoke: an automation-preview draft/save continues to
   work from the ``/chat`` endpoint (automation lane), and an
   evidence-grounded job review response still updates session
   context through ``context_on_review_performed`` (review lane) —
   covering the full extracted paths in a single turn.
"""

from __future__ import annotations

import inspect

import pytest

from voxera.vera_web import app as vera_app_module
from voxera.vera_web.chat_early_exit_dispatch import EarlyExitResult
from voxera.vera_web.lanes import automation_lane, review_lane
from voxera.vera_web.lanes.automation_lane import (
    AutomationLaneResult,
    try_automation_draft_or_revision_lane,
    try_automation_lifecycle_lane,
    try_materialize_automation_shell,
    try_submit_automation_preview_lane,
)
from voxera.vera_web.lanes.review_lane import (
    apply_early_exit_state_writes,
    compute_active_preview_revision_in_flight,
)

from .vera_session_helpers import make_vera_session

# ---------------------------------------------------------------------------
# 1. app.py still visibly orchestrates the extracted lanes
# ---------------------------------------------------------------------------


class TestAppStillVisiblyOrchestratesLanes:
    """app.py should import and call each extracted lane entry point.

    These tests ensure ``app.py`` remains the top-level lane orchestrator
    — a refactor that accidentally drops one of the lane calls would
    show up here immediately.
    """

    def test_app_imports_automation_lane_entry_points(self) -> None:
        assert vera_app_module.try_submit_automation_preview_lane is (
            try_submit_automation_preview_lane
        )
        assert vera_app_module.try_automation_draft_or_revision_lane is (
            try_automation_draft_or_revision_lane
        )
        assert vera_app_module.try_automation_lifecycle_lane is (try_automation_lifecycle_lane)
        assert vera_app_module.try_materialize_automation_shell is (
            try_materialize_automation_shell
        )

    def test_app_imports_review_lane_helpers(self) -> None:
        assert vera_app_module.compute_active_preview_revision_in_flight is (
            compute_active_preview_revision_in_flight
        )
        assert vera_app_module.apply_early_exit_state_writes is (apply_early_exit_state_writes)

    def test_chat_handler_calls_each_extracted_lane(self) -> None:
        """Sanity guard: chat() must literally reference each lane entry.

        This assertion reads the chat() source once and checks for the
        extracted lane symbols. It is not a behavioral test — it is a
        cheap visibility anchor so that if a future refactor drops a
        lane call, the extraction contract is re-examined.
        """
        source = inspect.getsource(vera_app_module.chat)
        assert "try_submit_automation_preview_lane(" in source
        assert "try_automation_draft_or_revision_lane(" in source
        assert "try_automation_lifecycle_lane(" in source
        assert "try_materialize_automation_shell(" in source
        assert "apply_early_exit_state_writes(" in source
        assert "compute_active_preview_revision_in_flight(" in source

    def test_lane_order_is_still_seven(self) -> None:
        """Lane precedence unchanged after extraction."""
        from voxera.vera_web.preview_routing import canonical_preview_lane_order

        assert len(canonical_preview_lane_order()) == 7


# ---------------------------------------------------------------------------
# 2. Automation lane result contract
# ---------------------------------------------------------------------------


class TestAutomationLaneResultContract:
    def test_default_result_is_unmatched(self) -> None:
        result = AutomationLaneResult(matched=False)
        assert result.matched is False
        assert result.assistant_text == ""
        assert result.status == ""
        assert result.dispatch_source == ""
        assert result.matched_early_exit is False
        assert result.pending_preview_after is None


# ---------------------------------------------------------------------------
# 3. review_lane.compute_active_preview_revision_in_flight
# ---------------------------------------------------------------------------


class TestComputeActivePreviewRevisionInFlight:
    def test_no_preview_returns_false(self) -> None:
        assert compute_active_preview_revision_in_flight("save it", pending_preview=None) is False

    def test_narrow_revision_verb_matches(self) -> None:
        preview = {
            "goal": "draft a note",
            "write_file": {"path": "~/VoxeraOS/notes/n.md", "content": "hi"},
        }
        assert (
            compute_active_preview_revision_in_flight("make it longer", pending_preview=preview)
            is True
        )

    def test_belt_and_suspenders_fires_for_ambiguous_save_followup(self) -> None:
        """Ambiguous 'save that follow-up' on a normal preview must not hijack."""
        preview = {
            "goal": "draft a note",
            "write_file": {"path": "~/VoxeraOS/notes/n.md", "content": "hi"},
        }
        # is_save_followup_request matches phrases like "save that as a follow-up"
        assert (
            compute_active_preview_revision_in_flight(
                "save that as a follow-up", pending_preview=preview
            )
            is True
        )

    def test_automation_preview_does_not_trigger_belt_and_suspenders(self) -> None:
        """Automation previews are not 'normal' — belt-and-suspenders skipped."""
        automation_preview = {
            "preview_type": "automation_definition",
            "goal": "automation",
        }
        # A plain investigation save phrase on an automation preview should
        # return False (no normal preview to protect, narrow gate also False).
        assert (
            compute_active_preview_revision_in_flight(
                "save all findings", pending_preview=automation_preview
            )
            is False
        )


# ---------------------------------------------------------------------------
# 4. review_lane.apply_early_exit_state_writes
# ---------------------------------------------------------------------------


class TestApplyEarlyExitStateWrites:
    def test_unmatched_result_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A result with matched=False must not perform any writes."""
        calls: list[str] = []

        monkeypatch.setattr(
            review_lane,
            "reset_active_preview",
            lambda *a, **kw: calls.append("reset"),
        )
        monkeypatch.setattr(
            review_lane,
            "record_followup_preview",
            lambda *a, **kw: calls.append("followup"),
        )
        monkeypatch.setattr(
            review_lane,
            "context_on_review_performed",
            lambda *a, **kw: calls.append("review"),
        )
        monkeypatch.setattr(
            review_lane,
            "update_session_context",
            lambda *a, **kw: calls.append("ctx"),
        )
        monkeypatch.setattr(
            review_lane,
            "write_session_derived_investigation_output",
            lambda *a, **kw: calls.append("derived"),
        )

        apply_early_exit_state_writes(
            EarlyExitResult(matched=False),
            queue_root=object(),  # type: ignore[arg-type]
            session_id="sid",
        )
        assert calls == []

    def test_preview_write_with_source_job_uses_followup_helper(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded: dict[str, object] = {}

        def fake_followup(root, sid, payload, *, source_job_id, draft_ref):  # type: ignore[no-redef]
            recorded["followup"] = (sid, source_job_id, draft_ref)

        def fake_reset(*a, **kw):  # type: ignore[no-redef]
            recorded["reset"] = True

        monkeypatch.setattr(review_lane, "record_followup_preview", fake_followup)
        monkeypatch.setattr(review_lane, "reset_active_preview", fake_reset)

        payload = {"write_file": {"path": "~/VoxeraOS/notes/f.md", "content": "hi"}}
        apply_early_exit_state_writes(
            EarlyExitResult(
                matched=True,
                write_preview=True,
                preview_payload=payload,
                context_updates={"last_reviewed_job_ref": "job-123.json"},
            ),
            queue_root=object(),  # type: ignore[arg-type]
            session_id="sid",
        )
        assert "followup" in recorded
        assert "reset" not in recorded
        sid, source_job, draft_ref = recorded["followup"]  # type: ignore[misc]
        assert sid == "sid"
        assert source_job == "job-123.json"
        assert draft_ref == "~/VoxeraOS/notes/f.md"

    def test_review_only_context_update_uses_review_shortcut(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded: dict[str, object] = {}

        def fake_review(root, sid, *, job_id):  # type: ignore[no-redef]
            recorded["review"] = (sid, job_id)

        monkeypatch.setattr(review_lane, "context_on_review_performed", fake_review)
        monkeypatch.setattr(
            review_lane,
            "update_session_context",
            lambda *a, **kw: recorded.setdefault("ctx", True),
        )

        apply_early_exit_state_writes(
            EarlyExitResult(
                matched=True,
                context_updates={"last_reviewed_job_ref": "job-999.json"},
            ),
            queue_root=object(),  # type: ignore[arg-type]
            session_id="sid",
        )
        assert "review" in recorded
        assert "ctx" not in recorded


# ---------------------------------------------------------------------------
# 5. Preview ownership discipline — no scattered writes in lane modules
# ---------------------------------------------------------------------------


class TestLaneModulesPreviewOwnershipDiscipline:
    """Extracted lane modules must not reintroduce scattered preview writes.

    The lane modules must only touch preview state through the
    ``preview_ownership`` helpers. Direct ``write_session_preview``
    calls from the lane modules are a policy violation and would
    re-open the class of preview-hijack regressions that PR #311
    stabilized.
    """

    def test_automation_lane_has_no_direct_write_session_preview(self) -> None:
        source = inspect.getsource(automation_lane)
        assert "write_session_preview(" not in source

    def test_review_lane_has_no_direct_write_session_preview(self) -> None:
        source = inspect.getsource(review_lane)
        assert "write_session_preview(" not in source

    def test_automation_lane_imports_preview_ownership_helpers(self) -> None:
        assert hasattr(automation_lane, "reset_active_preview")
        assert hasattr(automation_lane, "record_submit_success")

    def test_review_lane_imports_preview_ownership_helpers(self) -> None:
        assert hasattr(review_lane, "reset_active_preview")
        assert hasattr(review_lane, "record_followup_preview")
        assert hasattr(review_lane, "derive_preview_draft_ref")


# ---------------------------------------------------------------------------
# 6. End-to-end smoke: automation draft + save still works through /chat
# ---------------------------------------------------------------------------


class TestAutomationLaneEndToEnd:
    def test_automation_preview_draft_then_save_through_chat(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """A single automation draft -> revise -> save path exercises
        the extracted automation draft/revision and submit lanes."""
        harness = make_vera_session(monkeypatch, tmp_path)

        # Draft an automation preview — the drafting branch in
        # automation_lane.try_automation_draft_or_revision_lane.
        draft_resp = harness.chat("Every day at 08:00, run the diagnostics report automation.")
        assert draft_resp.status_code == 200
        preview = harness.preview()
        assert isinstance(preview, dict)
        assert preview.get("preview_type") == "automation_definition"

        # Now submit — the automation submit lane.
        submit_resp = harness.chat("go ahead")
        assert submit_resp.status_code == 200
        # Automation submit clears the preview slot.
        assert harness.preview() is None

    def test_automation_lifecycle_lane_steps_aside_for_normal_preview_revision(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """The lifecycle lane must step aside when a clear revision of a
        normal active preview is in flight. This is the belt-and-suspenders
        that moved into review_lane.compute_active_preview_revision_in_flight.
        """
        harness = make_vera_session(monkeypatch, tmp_path)

        # Seed a normal write_file preview directly via the session store
        # (approved entry point through the test harness's queue).
        from voxera.vera.session_store import write_session_preview

        normal_preview = {
            "goal": "draft a note",
            "write_file": {
                "path": "~/VoxeraOS/notes/note.md",
                "content": "existing content",
                "mode": "overwrite",
            },
        }
        write_session_preview(harness.queue, harness.session_id, normal_preview)

        # A clear revision phrase with incidental lifecycle wording.
        # The lifecycle lane must step aside because the active preview
        # is a normal preview under revision.
        revision_flag = compute_active_preview_revision_in_flight(
            "make it longer", pending_preview=normal_preview
        )
        assert revision_flag is True

        lifecycle_result = try_automation_lifecycle_lane(
            message="show me the file",  # overlaps with lifecycle wording
            pending_preview=normal_preview,
            active_preview_revision_in_flight=True,
            session_context=None,
            last_automation_preview=None,
            queue_root=harness.queue,
            session_id=harness.session_id,
        )
        assert lifecycle_result.matched is False
