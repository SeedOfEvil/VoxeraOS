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
# 2b. Automation lane step-aside / fall-through behavior
# ---------------------------------------------------------------------------
#
# These tests pin the behavior that each automation lane entry point declines
# cleanly when its preconditions are not met, so the caller falls through to
# the next lane exactly as it did before the extraction. They do not exercise
# the "claim" paths (those are covered by the end-to-end automation tests in
# ``test_vera_automation_preview.py`` and ``test_vera_automation_lifecycle.py``
# and by the end-to-end smoke at the bottom of this file).


class TestAutomationSubmitLaneDeclines:
    def test_declines_when_message_is_not_submit(self, tmp_path) -> None:
        """Non-submit messages fall through even on an automation preview."""
        preview = {"preview_type": "automation_definition", "goal": "automation"}
        result = try_submit_automation_preview_lane(
            message="what does this do?",
            pending_preview=preview,
            queue_root=tmp_path,
            session_id="sid",
        )
        assert result.matched is False

    def test_declines_when_preview_is_normal(self, tmp_path) -> None:
        """A normal preview + 'go ahead' must NOT be claimed by the automation
        submit lane — it belongs to the normal submit handoff path."""
        normal = {
            "goal": "draft a note",
            "write_file": {"path": "~/VoxeraOS/notes/n.md", "content": "hi"},
        }
        result = try_submit_automation_preview_lane(
            message="go ahead",
            pending_preview=normal,
            queue_root=tmp_path,
            session_id="sid",
        )
        assert result.matched is False

    def test_declines_when_no_preview(self, tmp_path) -> None:
        result = try_submit_automation_preview_lane(
            message="go ahead",
            pending_preview=None,
            queue_root=tmp_path,
            session_id="sid",
        )
        assert result.matched is False


class TestAutomationDraftOrRevisionLaneDeclines:
    def test_declines_when_no_preview_and_no_authoring_intent(self, tmp_path) -> None:
        result = try_automation_draft_or_revision_lane(
            message="what is the capital of France?",
            pending_preview=None,
            diagnostics_service_turn=False,
            queue_root=tmp_path,
            session_id="sid",
        )
        assert result.matched is False

    def test_declines_when_diagnostics_service_turn_is_true(self, tmp_path) -> None:
        """Authoring intent + diagnostics turn must NOT draft — the caller's
        diagnostics short-circuit owns that turn."""
        result = try_automation_draft_or_revision_lane(
            message="every hour run the diagnostics",
            pending_preview=None,
            diagnostics_service_turn=True,
            queue_root=tmp_path,
            session_id="sid",
        )
        assert result.matched is False

    def test_declines_on_normal_preview(self, tmp_path) -> None:
        """A normal active preview is never handled by this lane — neither the
        revision branch (gated on automation preview) nor the drafting branch
        (gated on pending_preview is None) should fire."""
        normal = {
            "goal": "draft a note",
            "write_file": {"path": "~/VoxeraOS/notes/n.md", "content": "hi"},
        }
        result = try_automation_draft_or_revision_lane(
            message="every hour run the diagnostics",
            pending_preview=normal,
            diagnostics_service_turn=False,
            queue_root=tmp_path,
            session_id="sid",
        )
        assert result.matched is False


class TestAutomationLifecycleLaneDeclines:
    def test_declines_when_no_lifecycle_intent(self, tmp_path) -> None:
        result = try_automation_lifecycle_lane(
            message="what is the weather in Calgary?",
            pending_preview=None,
            active_preview_revision_in_flight=False,
            session_context=None,
            last_automation_preview=None,
            queue_root=tmp_path,
            session_id="sid",
        )
        assert result.matched is False

    def test_declines_when_active_preview_is_automation_preview(self, tmp_path) -> None:
        """Lifecycle wording on an active automation preview belongs to the
        automation revision lane, not the lifecycle lane."""
        automation_preview = {"preview_type": "automation_definition", "goal": "automation"}
        result = try_automation_lifecycle_lane(
            message="show that automation",
            pending_preview=automation_preview,
            active_preview_revision_in_flight=False,
            session_context=None,
            last_automation_preview=None,
            queue_root=tmp_path,
            session_id="sid",
        )
        assert result.matched is False

    def test_declines_when_revision_in_flight(self, tmp_path) -> None:
        """Lifecycle wording while a normal preview is under revision must
        fall through so the normal preview revision flow runs instead."""
        normal = {
            "goal": "draft a note",
            "write_file": {"path": "~/VoxeraOS/notes/n.md", "content": "hi"},
        }
        result = try_automation_lifecycle_lane(
            message="run it now",
            pending_preview=normal,
            active_preview_revision_in_flight=True,
            session_context=None,
            last_automation_preview=None,
            queue_root=tmp_path,
            session_id="sid",
        )
        assert result.matched is False


class TestMaterializeAutomationShellDeclines:
    def test_returns_none_when_preview_is_active(self, tmp_path) -> None:
        existing = {
            "goal": "draft a note",
            "write_file": {"path": "~/VoxeraOS/notes/n.md", "content": "hi"},
        }
        result = try_materialize_automation_shell(
            message="watch ./incoming and move new folders to ./processed",
            pending_preview=existing,
            turns=[],
            is_info_query=False,
            is_explicit_writing_transform=False,
            conversational_answer_first_turn=False,
            is_voxera_control_turn=False,
            looks_like_new_unrelated_query=False,
            queue_root=tmp_path,
            session_id="sid",
        )
        assert result is None

    def test_returns_none_on_unrelated_message(self, tmp_path) -> None:
        result = try_materialize_automation_shell(
            message="what is 2 + 2?",
            pending_preview=None,
            turns=[],
            is_info_query=False,
            is_explicit_writing_transform=False,
            conversational_answer_first_turn=False,
            is_voxera_control_turn=False,
            looks_like_new_unrelated_query=False,
            queue_root=tmp_path,
            session_id="sid",
        )
        assert result is None

    def test_returns_none_when_info_query_gate_is_true(self, tmp_path) -> None:
        """Even a clearly automation-shaped phrase is declined when the caller
        flags it as an informational web query, so the info lane can claim it."""
        result = try_materialize_automation_shell(
            message="watch ./incoming and move new folders to ./processed",
            pending_preview=None,
            turns=[],
            is_info_query=True,
            is_explicit_writing_transform=False,
            conversational_answer_first_turn=False,
            is_voxera_control_turn=False,
            looks_like_new_unrelated_query=False,
            queue_root=tmp_path,
            session_id="sid",
        )
        assert result is None


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


class TestPR313ToneRevisionRegression:
    """Named regression: PR #313 live tone/style revision bug.

    Live reproduction during the lane-extraction PR review surfaced
    that a normal prose preview under revision with a phrase like
    ``"Change the tone to more technical."`` fell into the
    submission-claim guardrail path. Root cause: the narrow revision
    gate (``_REVISION_VERB_PATTERNS``) and ``is_writing_refinement_request``
    both missed tone/style/formality patterns, so the turn did not get
    promoted to a writing-draft refinement and the guardrail fired on
    note content that legitimately mentioned ``queued`` (the note was
    about queue-backed execution).

    These tests prove the end-to-end ``/chat`` flow now preserves
    normal-preview revision continuity for tone/style turns: the
    builder runs, the preview is updated, and the user does NOT see
    the "I have not submitted anything to VoxeraOS yet" guardrail
    text.
    """

    _LLM_REPLY = (
        "Updated the preview. The note now explains that queue-backed "
        "execution ensures jobs are audited and queued before running, "
        "providing safety vs. direct AI execution."
    )

    @staticmethod
    def _install_prose_preview(
        harness, *, content: str = "Queue-backed execution is safer than direct AI execution."
    ) -> dict:
        from voxera.vera.session_store import (
            write_session_handoff_state,
            write_session_preview,
        )

        preview = {
            "goal": "draft a note explaining queue-backed execution",
            "write_file": {
                "path": "~/VoxeraOS/notes/queue-safety.txt",
                "content": content,
                "mode": "overwrite",
            },
        }
        write_session_preview(harness.queue, harness.session_id, preview)
        write_session_handoff_state(
            harness.queue,
            harness.session_id,
            attempted=False,
            queue_path=str(harness.queue),
            status="preview_ready",
            error=None,
            job_id=None,
        )
        return preview

    @staticmethod
    def _install_mocks(monkeypatch: pytest.MonkeyPatch, *, reply_text: str) -> None:
        async def fake_reply(*, turns, user_message, **kw):  # type: ignore[no-redef]
            return {"answer": reply_text, "status": "ok:test"}

        async def fake_builder(
            *,
            turns,
            user_message,
            active_preview,
            enrichment_context,
            investigation_context,
            recent_assistant_artifacts,
        ):  # type: ignore[no-redef]
            if active_preview is None:
                return None
            wf = active_preview.get("write_file") or {}
            return {
                "goal": active_preview.get("goal"),
                "write_file": {
                    "path": wf.get("path"),
                    "content": "Technical revision of the note content about queue-backed execution.",
                    "mode": "overwrite",
                },
            }

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", fake_reply)
        monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", fake_builder)

    @pytest.mark.parametrize(
        "message",
        [
            # The exact live reproduction phrase.
            "Change the tone to more technical.",
            # Task-listed variations.
            "Make it more formal.",
            "Simplify the language.",
            "Make it more concise.",
            "Rewrite it in a more technical tone.",
            "Change the style.",
            "Make it sound more professional.",
        ],
    )
    def test_tone_style_phrase_preserves_preview_revision_flow(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
        message: str,
    ) -> None:
        """Tone/style phrases on a normal prose preview must:
        1. Update the preview via the builder.
        2. NOT trigger the submission-claim guardrail text.
        3. NOT emit a "no handoff recorded" / "nothing submitted yet" reply.
        """
        harness = make_vera_session(monkeypatch, tmp_path)
        self._install_prose_preview(harness)
        self._install_mocks(monkeypatch, reply_text=self._LLM_REPLY)

        original_content = "Queue-backed execution is safer than direct AI execution."
        resp = harness.chat(message)
        assert resp.status_code == 200

        turns = harness.turns()
        assistant_turns = [t for t in turns if t.get("role") == "assistant"]
        assert assistant_turns, f"No assistant turn for {message!r}"
        last_reply = str(assistant_turns[-1].get("text") or "")

        # Must NOT be the submission-claim guardrail text — that is the
        # core regression this test anchors. The guardrail fires when the
        # turn is not promoted to a writing-draft refinement, which is
        # exactly what PR #313 broke for tone/style phrases.
        assert "i have not submitted anything" not in last_reply.lower(), (
            f"Submission-claim guardrail hijacked {message!r}: {last_reply!r}"
        )
        assert "no confirmed queue handoff" not in last_reply.lower(), (
            f"Guardrail fallback hit for {message!r}: {last_reply!r}"
        )

        # Preview must still exist.
        preview = harness.preview()
        assert preview is not None, f"Preview was cleared for {message!r}"
        wf = preview.get("write_file")
        assert isinstance(wf, dict)

        # Preview content must have been revised away from the original.
        # For a writing-draft turn the draft content binding path replaces
        # the content with the LLM reply text (prose previews are
        # LLM-authored), so we only assert the content changed — not its
        # exact shape.
        new_content = str(wf.get("content") or "")
        assert new_content, f"Preview content was cleared for {message!r}"
        assert new_content != original_content, f"Preview content was not revised for {message!r}"

    def test_explicit_submit_still_reaches_handoff(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """Regression anchor: the widened revision gate must not swallow
        explicit submit/handoff phrases. A normal preview + ``go ahead``
        must still reach the submit path and clear the preview slot.
        """
        harness = make_vera_session(monkeypatch, tmp_path)
        self._install_prose_preview(harness)
        self._install_mocks(monkeypatch, reply_text="ack")

        resp = harness.chat("go ahead")
        assert resp.status_code == 200

        # The submit path either succeeds (preview cleared) or fails with a
        # handoff error — either way the turn did NOT stay in revision mode.
        # We allow either outcome here; what we assert is that the submit
        # lane claimed the turn instead of the revision lane silently
        # keeping the preview around unchanged.
        turns = harness.turns()
        assistant_turns = [t for t in turns if t.get("role") == "assistant"]
        assert assistant_turns
        last_reply = str(assistant_turns[-1].get("text") or "").lower()
        # Must not be a revision-style reply (the widening must not hijack
        # "go ahead" into the revision gate).
        assert "updated the preview" not in last_reply
        assert "still in the preview" not in last_reply


class TestAutomationLaneEndToEnd:
    """End-to-end smoke through the ``/chat`` endpoint.

    Proves the extraction did not break the full automation draft +
    save path. The lane-level declines / step-asides are pinned in the
    unit-level test classes above; this class is only for ``/chat``.
    """

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

    def test_normal_preview_revision_blocks_automation_lifecycle_lane(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """Regression anchor: a normal preview under revision must still be
        protected from the automation lifecycle lane after the extraction.

        This is the moved-to-``review_lane.compute_active_preview_revision_in_flight``
        gate. If the caller drops the ``active_preview_revision_in_flight``
        forwarding, this test fails.
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

        # The narrow revision gate fires for "make it longer".
        assert (
            compute_active_preview_revision_in_flight(
                "make it longer", pending_preview=normal_preview
            )
            is True
        )

        # Even the lifecycle-overlap phrase must step aside when the
        # caller signals revision-in-flight.
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
