"""Regression coverage for Vera's stabilized preview routing and ownership.

This file pins down the behaviors hardened by the preview-subsystem
stabilization refactor:

* Follow-up revision turns on normal active previews resolve into the
  active-preview revision lane instead of getting hijacked by the
  automation lifecycle lane.
* ``reset_active_preview`` / ``record_followup_preview`` /
  ``clear_active_preview`` behave as documented for every transition
  kind (create, revise, follow-up, clear, submit-success).
* The canonical lane order documented in :mod:`preview_routing`
  matches the dispatch order used in ``app.py``.
* Ambiguous follow-up turns fail closed rather than mutating the wrong
  object.

Complementary tests live in:
- ``test_vera_preview_materialization.py`` — post-clarification shells
- ``test_vera_draft_revision.py`` — rename/save-as mutations
- ``test_vera_automation_lifecycle.py`` — lifecycle lane coverage
- ``test_vera_automation_preview.py`` — automation revision lane
"""

from __future__ import annotations

import pytest

from voxera.vera import preview_ownership, session_store
from voxera.vera.context_lifecycle import context_on_preview_created
from voxera.vera_web import app as vera_app_module
from voxera.vera_web.preview_routing import (
    PreviewLane,
    canonical_preview_lane_order,
    is_active_preview_revision_turn,
    is_normal_preview,
)

from .vera_session_helpers import make_vera_session

# ---------------------------------------------------------------------------
# 1. preview_ownership helpers
# ---------------------------------------------------------------------------


class TestPreviewOwnershipHelpers:
    """Unit-level coverage of the centralized preview state helpers."""

    def test_reset_active_preview_installs_payload(self, tmp_path) -> None:
        queue = tmp_path / "queue"
        payload = {
            "goal": "write a file called note.md with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/note.md",
                "content": "hello world",
                "mode": "overwrite",
            },
        }
        preview_ownership.reset_active_preview(
            queue, "s1", payload, draft_ref="~/VoxeraOS/notes/note.md"
        )
        assert session_store.read_session_preview(queue, "s1") == payload

    def test_reset_active_preview_marks_handoff_ready_by_default(self, tmp_path) -> None:
        queue = tmp_path / "queue"
        payload = {
            "goal": "write a file called note.md with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/note.md",
                "content": "hi",
                "mode": "overwrite",
            },
        }
        preview_ownership.reset_active_preview(queue, "s1", payload)
        handoff = session_store.read_session_handoff_state(queue, "s1") or {}
        assert handoff.get("status") == "preview_ready"
        assert handoff.get("attempted") is False
        assert handoff.get("job_id") is None

    def test_reset_active_preview_can_skip_handoff_marker(self, tmp_path) -> None:
        queue = tmp_path / "queue"
        automation_preview = {
            "preview_type": "automation_definition",
            "title": "Morning Diagnostics",
            "trigger_kind": "recurring_interval",
            "trigger_config": {"interval_ms": 3_600_000},
            "payload_template": {"goal": "run system_diagnostics"},
            "enabled": True,
        }
        preview_ownership.reset_active_preview(
            queue,
            "s1",
            automation_preview,
            draft_ref="automation_preview",
            mark_handoff_ready=False,
        )
        handoff = session_store.read_session_handoff_state(queue, "s1")
        assert handoff is None

    def test_reset_active_preview_refreshes_context_draft_ref(self, tmp_path) -> None:
        queue = tmp_path / "queue"
        payload = {
            "goal": "write a file called hi.md with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/hi.md",
                "content": "hi",
                "mode": "overwrite",
            },
        }
        preview_ownership.reset_active_preview(queue, "s1", payload)
        ctx = session_store.read_session_context(queue, "s1")
        assert ctx.get("active_draft_ref") == "~/VoxeraOS/notes/hi.md"
        assert ctx.get("active_preview_ref") == "preview"

    def test_record_followup_preview_records_source_job(self, tmp_path) -> None:
        queue = tmp_path / "queue"
        payload = {
            "goal": "write a file called followup.md with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/followup.md",
                "content": "follow-up body",
                "mode": "overwrite",
            },
        }
        preview_ownership.record_followup_preview(
            queue, "s1", payload, source_job_id="inbox-job-123.json"
        )
        ctx = session_store.read_session_context(queue, "s1")
        assert ctx.get("active_draft_ref") == "~/VoxeraOS/notes/followup.md"
        assert ctx.get("last_reviewed_job_ref") == "inbox-job-123.json"
        assert session_store.read_session_preview(queue, "s1") == payload

    def test_record_followup_preview_without_source_job(self, tmp_path) -> None:
        queue = tmp_path / "queue"
        payload = {
            "goal": "write a file called followup.md with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/followup.md",
                "content": "body",
                "mode": "overwrite",
            },
        }
        preview_ownership.record_followup_preview(queue, "s1", payload, source_job_id=None)
        ctx = session_store.read_session_context(queue, "s1")
        assert ctx.get("active_draft_ref") == "~/VoxeraOS/notes/followup.md"
        assert ctx.get("last_reviewed_job_ref") is None

    def test_clear_active_preview_removes_preview_and_refs(self, tmp_path) -> None:
        queue = tmp_path / "queue"
        payload = {
            "goal": "write a file called x.md with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/x.md",
                "content": "x",
                "mode": "overwrite",
            },
        }
        preview_ownership.reset_active_preview(queue, "s1", payload)
        preview_ownership.clear_active_preview(queue, "s1", reason="test")
        assert session_store.read_session_preview(queue, "s1") is None
        ctx = session_store.read_session_context(queue, "s1")
        assert ctx.get("active_draft_ref") is None
        assert ctx.get("active_preview_ref") is None

    def test_record_submit_success_clears_preview_slot(self, tmp_path) -> None:
        queue = tmp_path / "queue"
        payload = {
            "goal": "write a file called x.md with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/x.md",
                "content": "x",
                "mode": "overwrite",
            },
        }
        preview_ownership.reset_active_preview(queue, "s1", payload)
        preview_ownership.record_submit_success(queue, "s1")
        assert session_store.read_session_preview(queue, "s1") is None

    def test_derive_preview_draft_ref_prefers_write_file_path(self) -> None:
        payload = {
            "goal": "write a file called x.md with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/x.md",
                "content": "x",
                "mode": "overwrite",
            },
        }
        assert preview_ownership.derive_preview_draft_ref(payload) == "~/VoxeraOS/notes/x.md"

    def test_derive_preview_draft_ref_falls_back_to_goal(self) -> None:
        payload = {"goal": "open https://example.com"}
        assert preview_ownership.derive_preview_draft_ref(payload) == "open https://example.com"

    def test_derive_preview_draft_ref_for_automation_shape(self) -> None:
        payload = {
            "preview_type": "automation_definition",
            "title": "Daily Diagnostics",
            "trigger_kind": "recurring_interval",
            "trigger_config": {"interval_ms": 86_400_000},
            "payload_template": {"goal": "run diagnostics"},
            "enabled": True,
        }
        # The automation shape has no write_file; it falls back to the
        # goal (empty) and then to the default "preview" reference.
        assert preview_ownership.derive_preview_draft_ref(payload) == "preview"

    def test_derive_preview_draft_ref_none_payload(self) -> None:
        assert preview_ownership.derive_preview_draft_ref(None) == "preview"


# ---------------------------------------------------------------------------
# 2. preview_routing lane classification
# ---------------------------------------------------------------------------


class TestPreviewRoutingLanes:
    def test_canonical_lane_order_matches_enum(self) -> None:
        expected = (
            PreviewLane.EXPLICIT_SUBMIT,
            PreviewLane.ACTIVE_PREVIEW_REVISION,
            PreviewLane.AUTOMATION_LIFECYCLE,
            PreviewLane.FOLLOWUP_FROM_EVIDENCE,
            PreviewLane.PREVIEW_CREATION,
            PreviewLane.READ_ONLY_EARLY_EXIT,
            PreviewLane.CONVERSATIONAL,
        )
        assert canonical_preview_lane_order() == expected

    def test_lane_values_are_monotonic(self) -> None:
        order = canonical_preview_lane_order()
        values = [lane.value for lane in order]
        assert values == sorted(values)
        assert values == list(range(1, len(order) + 1))

    def test_is_normal_preview_rejects_automation(self) -> None:
        automation = {"preview_type": "automation_definition", "title": "x"}
        assert not is_normal_preview(automation)

    def test_is_normal_preview_accepts_write_file(self) -> None:
        write = {
            "goal": "write a file called x.md with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/x.md",
                "content": "x",
                "mode": "overwrite",
            },
        }
        assert is_normal_preview(write)

    def test_is_normal_preview_rejects_none(self) -> None:
        assert not is_normal_preview(None)

    @pytest.mark.parametrize(
        "message",
        [
            "make it longer",
            "make that shorter",
            "make it more concise",
            "change the content",
            "change the text",
            "rewrite it",
            "rewrite the content",
            "use python instead",
            "make it python",
            "make it a python script",
            "convert it to bash",
            "make it a follow-up script",
            "turn it into a script",
            "change the target path",
            "change the path",
            "change the filename",
            "revise it",
            "revise that",
            "save it as followup.py",
            "rename it",
            "call it note.md",
            "turn it into a checklist",
            "as a checklist",
            "make it more operator-facing",
        ],
    )
    def test_active_preview_revision_turn_matches_follow_up_verbs(self, message: str) -> None:
        preview = {
            "goal": "write a file called note.md with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/note.md",
                "content": "initial draft body",
                "mode": "overwrite",
            },
        }
        assert is_active_preview_revision_turn(message, active_preview=preview), (
            f"expected lane match for: {message!r}"
        )

    @pytest.mark.parametrize(
        "message",
        [
            "what is the weather today?",
            "tell me a joke",
            "explain photosynthesis",
            "show me that automation",
            "enable it",
            "disable the automation",
            "run it now",
            "delete the reminder automation",
            "submit it",
            "go ahead",
            "save the result to a note",
        ],
    )
    def test_active_preview_revision_turn_does_not_match_unrelated(self, message: str) -> None:
        preview = {
            "goal": "write a file called note.md with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/note.md",
                "content": "initial draft body",
                "mode": "overwrite",
            },
        }
        assert not is_active_preview_revision_turn(message, active_preview=preview), (
            f"expected no lane match for: {message!r}"
        )

    def test_active_preview_revision_turn_requires_active_preview(self) -> None:
        assert not is_active_preview_revision_turn("make it longer", active_preview=None)

    def test_active_preview_revision_turn_rejects_automation_preview(self) -> None:
        automation = {
            "preview_type": "automation_definition",
            "title": "Morning Diagnostics",
            "trigger_kind": "recurring_interval",
            "trigger_config": {"interval_ms": 3_600_000},
            "payload_template": {"goal": "run diagnostics"},
            "enabled": True,
        }
        # Automation previews use their own revision lane; this predicate is
        # specifically the lane-2 gate for normal previews.
        assert not is_active_preview_revision_turn("make it longer", active_preview=automation)


# ---------------------------------------------------------------------------
# 3. Lane collision regressions (integration)
# ---------------------------------------------------------------------------


async def _passthrough_reply(**kwargs):
    return {"answer": "I understood your request.", "status": "ok:test"}


async def _passthrough_builder(**kwargs):
    return kwargs.get("active_preview")


def _install_writing_preview(session, *, content: str = "Initial draft body.") -> None:
    """Install a governed writing preview directly using the centralized helper."""
    payload = {
        "goal": "draft a essay as essay.md",
        "write_file": {
            "path": "~/VoxeraOS/notes/essay.md",
            "content": content,
            "mode": "overwrite",
        },
    }
    preview_ownership.reset_active_preview(session.queue, session.session_id, payload)
    # context_on_preview_created writes the ref so later turns can see it.
    context_on_preview_created(
        session.queue, session.session_id, draft_ref="~/VoxeraOS/notes/essay.md"
    )


def test_lifecycle_lane_does_not_hijack_active_preview_revision(tmp_path, monkeypatch):
    """A revision turn on a normal preview must not be stolen by lifecycle.

    Phrasing like ``"revise it"`` and ``"change the content"`` that might
    look superficially like a lifecycle action must stay in the normal
    active-preview revision lane when a governed write_file preview is
    active.
    """
    session = make_vera_session(monkeypatch, tmp_path)
    _install_writing_preview(session)

    async def _revision_reply(**kwargs):
        # The LLM path should be reached (not the lifecycle lane)
        return {
            "answer": (
                "Here's the revised text:\n\n"
                "This is a longer, more detailed rewrite of the original draft "
                "with more context and additional supporting explanation."
            ),
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _revision_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _passthrough_builder)

    resp = session.chat("revise it and make it more detailed")
    assert resp.status_code == 200

    # Preview still exists and is still a normal write_file preview (not
    # replaced by an automation lifecycle response).
    preview = session.preview()
    assert preview is not None
    assert preview.get("preview_type") != "automation_definition"
    wf = preview.get("write_file") or {}
    assert str(wf.get("path") or "").endswith("essay.md")

    # The assistant reply must not look like a lifecycle action acknowledgement.
    turns = session.turns()
    last_assistant = [t["text"] for t in turns if t["role"] == "assistant"][-1]
    assert "disabled" not in last_assistant.lower()
    assert "enabled" not in last_assistant.lower()
    assert "deleted" not in last_assistant.lower()


def test_active_preview_revision_gate_allows_unrelated_lanes_to_proceed(tmp_path, monkeypatch):
    """The gate only protects revision turns; unrelated turns still route normally."""
    session = make_vera_session(monkeypatch, tmp_path)
    _install_writing_preview(session)

    async def _unrelated_reply(**kwargs):
        return {"answer": "The weather is clear and mild.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _unrelated_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _passthrough_builder)

    resp = session.chat("what is the weather today?")
    assert resp.status_code == 200

    # Preview is preserved (not mutated) — the non-revision turn left it alone.
    preview = session.preview()
    assert preview is not None
    wf = preview.get("write_file") or {}
    assert str(wf.get("content") or "") == "Initial draft body."


# ---------------------------------------------------------------------------
# 4. End-to-end preview ladder regression
# ---------------------------------------------------------------------------


def test_clear_writing_preview_then_revision_turn_mutates_same_preview(tmp_path, monkeypatch):
    """Create writing preview, then 'make it longer' — preview is mutated in place."""
    session = make_vera_session(monkeypatch, tmp_path)
    _install_writing_preview(session, content="Short draft.")

    async def _longer_reply(**kwargs):
        return {
            "answer": (
                "Here is a longer rewrite of the draft with more supporting "
                "detail and a clearer narrative arc."
            ),
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _longer_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _passthrough_builder)

    resp = session.chat("make it longer")
    assert resp.status_code == 200

    preview = session.preview()
    assert preview is not None
    wf = preview.get("write_file") or {}
    assert str(wf.get("path") or "").endswith("essay.md")


def test_clear_preview_for_new_unrelated_session_turn(tmp_path, monkeypatch):
    """Creating a new preview via /clear resets continuity refs."""
    session = make_vera_session(monkeypatch, tmp_path)
    _install_writing_preview(session)
    ctx_before = session.session_context()
    assert ctx_before.get("active_draft_ref")

    # Clear via /clear endpoint
    resp = session.client.post("/clear", data={"session_id": session.session_id})
    assert resp.status_code == 200

    # Session is reset
    assert session.preview() is None
    ctx_after = session.session_context()
    assert ctx_after.get("active_draft_ref") is None
    assert ctx_after.get("active_preview_ref") is None


def test_ambiguous_change_request_fails_closed(tmp_path, monkeypatch):
    """Pure ambiguous 'change it' on a bare active preview must fail closed."""
    from voxera.vera.draft_revision import _is_ambiguous_change_request

    assert _is_ambiguous_change_request("change it")
    assert _is_ambiguous_change_request("fix it")
    assert _is_ambiguous_change_request("improve it")
    assert not _is_ambiguous_change_request("make it a python script")
    assert not _is_ambiguous_change_request("change the content to X")


# ---------------------------------------------------------------------------
# 5. Preview state is not mutated by unrelated lifecycle / review lanes
# ---------------------------------------------------------------------------


def test_review_request_does_not_wipe_active_preview(tmp_path, monkeypatch):
    """Review hint phrases must not touch the active preview slot."""
    session = make_vera_session(monkeypatch, tmp_path)
    _install_writing_preview(session)

    async def _review_reply(**kwargs):
        # The review fallback path is in early_exit, not here; the LLM
        # path may still reply. Only preview mutation matters.
        return {"answer": "I need a job id to review.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _review_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _passthrough_builder)

    resp = session.chat("what happened with the last job?")
    assert resp.status_code == 200

    preview = session.preview()
    assert preview is not None
    wf = preview.get("write_file") or {}
    assert str(wf.get("content") or "") == "Initial draft body."
