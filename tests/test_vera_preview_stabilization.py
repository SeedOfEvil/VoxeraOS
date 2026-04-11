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

    def test_is_normal_preview_rejects_empty_dict(self) -> None:
        # A phantom empty dict is not a real preview we should protect.
        assert not is_normal_preview({})

    def test_is_normal_preview_rejects_dict_without_authoring_surface(self) -> None:
        # A dict without goal/write_file/steps/file_organize/mission_id
        # has no authoring surface and is not a real preview.
        assert not is_normal_preview({"random_key": "random_value"})

    def test_is_normal_preview_accepts_mission_preview(self) -> None:
        assert is_normal_preview({"goal": "run diagnostics", "mission_id": "system_diagnostics"})

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


# ---------------------------------------------------------------------------
# 6. Early-exit dispatch: active-preview revision gate
# ---------------------------------------------------------------------------
#
# These tests lock in the stronger protection added after the first
# stabilization pass. The early-exit dispatch previously ran before any
# revision-lane gate, so preview-writing branches in
# chat_early_exit_dispatch.py (follow-up-from-evidence, save-follow-up,
# revise-from-evidence, investigation-save, investigation-derived-save)
# could clobber an active normal preview that the user was clearly
# mutating. The fix threads ``active_preview_revision_in_flight`` into
# ``dispatch_early_exit_intent`` and skips the preview-writing branches
# when that flag is set. Non-mutating branches still run.


def _install_script_preview(session, *, content: str = "print('hello')") -> None:
    """Install a governed script preview for revision-hijack scenarios."""
    payload = {
        "goal": "draft a python script as script.py",
        "write_file": {
            "path": "~/VoxeraOS/notes/script.py",
            "content": content,
            "mode": "overwrite",
        },
    }
    preview_ownership.reset_active_preview(session.queue, session.session_id, payload)
    context_on_preview_created(
        session.queue, session.session_id, draft_ref="~/VoxeraOS/notes/script.py"
    )


def test_revise_from_evidence_does_not_hijack_active_revision_turn(tmp_path, monkeypatch):
    """'revise that based on the result' with an active preview must NOT overwrite it.

    Before the fix the early-exit dispatch would match
    ``is_revise_from_evidence_request`` and, if any handoff job id was
    resolvable, would call ``draft_revised_preview(evidence)`` and write
    the resulting payload directly to the active preview slot —
    silently clobbering the user's in-flight revision.  With the fix,
    the revision-in-flight flag short-circuits the follow-up branch in
    ``dispatch_early_exit_intent`` so the turn falls through to the
    normal revision path.
    """
    session = make_vera_session(monkeypatch, tmp_path)
    _install_script_preview(session, content="print('initial version')")

    # Install a handoff state that would otherwise satisfy the follow-up
    # branch's job resolver.
    session_store.write_session_handoff_state(
        session.queue,
        session.session_id,
        attempted=True,
        queue_path=str(session.queue),
        status="submitted",
        job_id="fake-job-1",
    )

    async def _pass_reply(**kwargs):
        return {
            "answer": "Understood — I'll revise the active script.",
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _pass_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _passthrough_builder)

    resp = session.chat("revise that based on the result")
    assert resp.status_code == 200

    preview = session.preview()
    assert preview is not None
    wf = preview.get("write_file") or {}
    # Preview is still the active script (not overwritten by a
    # follow-up-from-evidence payload rooted elsewhere).
    assert str(wf.get("path") or "").endswith("script.py")


def test_save_followup_phrase_does_not_hijack_rename_on_active_preview(tmp_path, monkeypatch):
    """'save it as followup.py' with an active preview is a rename, not a new follow-up.

    ``save_the_follow-up`` phrasing is in the evidence-review save-follow-up
    hint list.  With an active preview, the revision gate catches
    ``save.*as.*followup.py`` via the rename/save-as pattern and the
    early-exit follow-up branch is short-circuited, leaving the rename
    fallback in the normal flow to do its job.
    """
    session = make_vera_session(monkeypatch, tmp_path)
    _install_script_preview(session, content="print('hi')")

    session_store.write_session_handoff_state(
        session.queue,
        session.session_id,
        attempted=True,
        queue_path=str(session.queue),
        status="submitted",
        job_id="fake-job-1",
    )

    async def _pass_reply(**kwargs):
        return {"answer": "Renamed.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _pass_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _passthrough_builder)

    resp = session.chat("save it as followup.py")
    assert resp.status_code == 200

    preview = session.preview()
    assert preview is not None
    wf = preview.get("write_file") or {}
    path = str(wf.get("path") or "")
    # The active preview's path was renamed to followup.py rather than
    # being clobbered with a save-follow-up evidence payload.
    assert path.endswith("followup.py")
    # Script content is preserved (not replaced with an evidence-based
    # body that would have come from draft_saveable_followup_preview).
    assert str(wf.get("content") or "").strip() == "print('hi')"


def test_make_it_a_script_does_not_hijack_with_followup_phrasing(tmp_path, monkeypatch):
    """'make it a follow-up script' with an active preview is a revision."""
    session = make_vera_session(monkeypatch, tmp_path)
    _install_writing_preview(session, content="A short note body.")

    session_store.write_session_handoff_state(
        session.queue,
        session.session_id,
        attempted=True,
        queue_path=str(session.queue),
        status="submitted",
        job_id="fake-job-1",
    )

    async def _pass_reply(**kwargs):
        return {"answer": "Understood.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _pass_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _passthrough_builder)

    resp = session.chat("make it a follow-up script")
    assert resp.status_code == 200

    # Preview is NOT silently replaced with a general follow-up preview
    # drafted from evidence (which would come from draft_followup_preview
    # and could point at a different path).
    preview = session.preview()
    assert preview is not None
    wf = preview.get("write_file") or {}
    assert str(wf.get("path") or "").endswith("essay.md")


def test_investigation_save_does_not_hijack_active_revision(tmp_path, monkeypatch):
    """'save that to a note' with an active preview and a recent investigation must not hijack.

    ``is_investigation_save_request`` would normally produce a
    save-to-note preview from the session investigation. With an
    active normal preview under revision, that branch is skipped so
    the active preview is not silently replaced.
    """
    session = make_vera_session(monkeypatch, tmp_path)
    _install_writing_preview(session, content="Original note body.")

    # Seed an investigation that would normally be eligible for save.
    from .vera_session_helpers import sample_investigation_payload

    session.write_investigation(sample_investigation_payload())

    async def _pass_reply(**kwargs):
        return {"answer": "Understood.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _pass_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _passthrough_builder)

    # "save that as more_concise.md" is a rename-style revision that
    # matches both the save-as pattern and the investigation-save hint.
    # The revision gate must win so the active preview is renamed, not
    # replaced with an investigation-save preview.
    resp = session.chat("save it as more_concise.md")
    assert resp.status_code == 200

    preview = session.preview()
    assert preview is not None
    wf = preview.get("write_file") or {}
    path = str(wf.get("path") or "")
    # Active preview was renamed (its content preserved), not replaced
    # with an investigation-save payload that would point somewhere
    # else and contain investigation findings.
    assert path.endswith("more_concise.md")
    assert "Original note body." in str(wf.get("content") or "")


def test_save_the_followup_phrasing_does_not_hijack_active_preview(tmp_path, monkeypatch):
    """Belt-and-suspenders: ambiguous 'save the follow-up' on an active preview.

    ``is_save_followup_request("save the follow-up as a file")`` is True,
    and the phrase does NOT match the narrow revision-gate patterns —
    but with a normal preview active, the fail-closed rule in app.py
    marks the revision as in flight so the save-follow-up branch is
    skipped.  Without this rule, the branch would call
    ``draft_saveable_followup_preview(evidence)`` and overwrite the
    active preview.
    """
    session = make_vera_session(monkeypatch, tmp_path)
    _install_writing_preview(session, content="Active note body.")

    # Installing a fake handoff state would otherwise make evidence
    # resolvable for the save-follow-up branch.
    session_store.write_session_handoff_state(
        session.queue,
        session.session_id,
        attempted=True,
        queue_path=str(session.queue),
        status="submitted",
        job_id="fake-job-1",
    )

    async def _pass_reply(**kwargs):
        return {"answer": "Understood.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _pass_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _passthrough_builder)

    resp = session.chat("save the follow-up as a file")
    assert resp.status_code == 200

    preview = session.preview()
    assert preview is not None
    wf = preview.get("write_file") or {}
    # Active preview is untouched (its content preserved).
    assert str(wf.get("path") or "").endswith("essay.md")
    assert "Active note body." in str(wf.get("content") or "")


def test_revise_from_evidence_fallback_does_not_hijack_active_preview(tmp_path, monkeypatch):
    """Belt-and-suspenders regression for 'update that based on the result'.

    ``update that based on the result`` matches
    ``is_revise_from_evidence_request`` but does NOT match the narrow
    revision verb gate (``update`` is deliberately excluded as too
    ambiguous).  The belt-and-suspenders rule in app.py catches it.
    """
    session = make_vera_session(monkeypatch, tmp_path)
    _install_writing_preview(session, content="Active note body.")

    session_store.write_session_handoff_state(
        session.queue,
        session.session_id,
        attempted=True,
        queue_path=str(session.queue),
        status="submitted",
        job_id="fake-job-1",
    )

    async def _pass_reply(**kwargs):
        return {"answer": "Understood.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _pass_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _passthrough_builder)

    resp = session.chat("update that based on the result")
    assert resp.status_code == 200

    preview = session.preview()
    assert preview is not None
    wf = preview.get("write_file") or {}
    # Active preview is untouched.
    assert str(wf.get("path") or "").endswith("essay.md")
    assert "Active note body." in str(wf.get("content") or "")


def test_time_question_still_fires_with_active_preview(tmp_path, monkeypatch):
    """Non-mutating early-exit branches still run when a preview is active.

    The revision-in-flight flag only short-circuits preview-writing
    branches. Time, diagnostics refusal, near-miss submit rejection,
    and stale-draft reference must still fire normally.
    """
    session = make_vera_session(monkeypatch, tmp_path)
    _install_writing_preview(session)

    # Time question even with an active preview should be answered
    # deterministically.
    resp = session.chat("what time is it?")
    assert resp.status_code == 200
    turns = session.turns()
    last_reply = [t["text"] for t in turns if t["role"] == "assistant"][-1]
    # Deterministic time reply contains "local time" (from time_context)
    # and the preview is still intact.
    assert "local time" in last_reply.lower() or "utc" in last_reply.lower()
    preview = session.preview()
    assert preview is not None


def test_early_exit_follow_up_still_works_without_active_preview(tmp_path, monkeypatch):
    """Revision gate must not disable follow-up lane when no preview exists.

    The flag gates preview-writing branches only when an active normal
    preview exists. With no preview, the follow-up branch must still
    behave as before (fail closed honestly when no job is resolvable).
    """
    session = make_vera_session(monkeypatch, tmp_path)
    assert session.preview() is None

    async def _pass_reply(**kwargs):
        return {"answer": "Understood.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _pass_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _passthrough_builder)

    # No preview, no job, no evidence — follow-up branch fails closed
    # with its canonical "no completed job" message (not the stale
    # draft reference or the near-miss submit).
    resp = session.chat("draft a follow-up based on the result")
    assert resp.status_code == 200
    turns = session.turns()
    last_reply = [t["text"] for t in turns if t["role"] == "assistant"][-1]
    # Canonical fail-closed message from the follow-up branch:
    assert "no completed job" in last_reply.lower() or "follow-up" in last_reply.lower()


# ---------------------------------------------------------------------------
# 7. Early-exit dispatch unit tests: the flag is respected
# ---------------------------------------------------------------------------


class TestEarlyExitRevisionFlag:
    """Direct unit tests for the active_preview_revision_in_flight parameter."""

    def _call_early_exit(self, tmp_path, message: str, *, flag: bool):
        from voxera.vera_web.chat_early_exit_dispatch import dispatch_early_exit_intent

        queue = tmp_path / "queue"
        session_store.write_session_handoff_state(
            queue,
            "s1",
            attempted=True,
            queue_path=str(queue),
            status="submitted",
            job_id="fake-job-1",
        )
        return dispatch_early_exit_intent(
            message=message,
            diagnostics_service_turn=False,
            requested_job_id=None,
            should_attempt_derived_save=False,
            session_investigation=None,
            session_derived_output=None,
            queue_root=queue,
            session_id="s1",
            session_context={},
            active_preview_revision_in_flight=flag,
        )

    def test_followup_branch_fires_without_flag(self, tmp_path) -> None:
        result = self._call_early_exit(
            tmp_path, "draft a follow-up based on the result", flag=False
        )
        # With no real evidence in the fake job, the branch enters and
        # fails closed with its canonical error — that still counts as
        # "matched" because it claimed the turn.
        assert result.matched

    def test_followup_branch_skipped_with_flag(self, tmp_path) -> None:
        result = self._call_early_exit(tmp_path, "draft a follow-up based on the result", flag=True)
        # With the flag set, the follow-up branch is skipped; no other
        # preview-writing branch matches this phrase, so the result is
        # unmatched (falls through to the normal LLM flow).
        assert not result.matched

    def test_review_branch_still_fires_with_flag(self, tmp_path) -> None:
        # Review lane is non-mutating, so it still runs.
        result = self._call_early_exit(tmp_path, "what happened with the last job?", flag=True)
        assert result.matched
        assert not result.write_preview

    def test_time_branch_still_fires_with_flag(self, tmp_path) -> None:
        result = self._call_early_exit(tmp_path, "what time is it?", flag=True)
        assert result.matched
        assert not result.write_preview

    def test_near_miss_submit_still_fires_with_flag(self, tmp_path) -> None:
        # Near-miss submit is also non-mutating (refusal).
        result = self._call_early_exit(tmp_path, "send iit", flag=True)
        assert result.matched
        assert not result.write_preview


# ---------------------------------------------------------------------------
# 8. Gate predicate coverage on follow-up script phrasing
# ---------------------------------------------------------------------------


class TestFollowUpScriptPhrasing:
    """Phrases that the task brief specifically flagged as follow-up mutations."""

    @pytest.mark.parametrize(
        "message",
        [
            "make it a follow-up script",
            "make that into a follow-up script",
            "make this into a follow-up script",
            "turn it into a script",
            "turn that into a script",
        ],
    )
    def test_follow_up_script_phrasing_matches_revision_gate(self, message: str) -> None:
        preview = {
            "goal": "write a file called note.md with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/note.md",
                "content": "body",
                "mode": "overwrite",
            },
        }
        assert is_active_preview_revision_turn(message, active_preview=preview)


# ---------------------------------------------------------------------------
# 9. Script-enhancement hijack regressions (live reproduction from PR #311)
# ---------------------------------------------------------------------------
#
# Live product reproduction from PR #311 review:
#
#   Turn 1: "Draft a Python script that scans a folder and lists all .txt files."
#            → normal active script preview created at ~/VoxeraOS/notes/scan.py
#   Turn 2: "Make it save the results to a file."
#            → EXPECTED: active preview revision lane
#            → ACTUAL (before this fix): early-exit investigation-save branch
#              fired because is_investigation_save_request matched
#              (save + results), draft_investigation_save_preview returned
#              None (no investigation in session), and the branch returned
#              "I couldn't resolve those investigation result references
#              in this session" — hijacking the turn.
#
# Two-part fix landed in PR #311:
#   (a) The active-preview revision gate is widened with narrow
#       script-enhancement patterns ("make it save/write/output/export/log",
#       "have it write/report", "add file logging", etc.). These patterns
#       require a subject pronoun (it/that/this) or an explicit "the
#       script/code/program/draft/note" anchor so bare investigation-save
#       phrasing is still routed to the investigation lane when no active
#       preview exists.
#   (b) app.py's belt-and-suspenders layer also treats
#       is_investigation_save_request / is_investigation_derived_save_request
#       matches as revision candidates whenever a normal active preview is
#       present — closing the gap for overly-broad phrases the narrow gate
#       patterns do not cover.


class TestScriptEnhancementGate:
    """Unit tests on the widened revision gate for script-enhancement phrasing."""

    @pytest.fixture
    def script_preview(self) -> dict:
        return {
            "goal": "draft a python script as scan.py",
            "write_file": {
                "path": "~/VoxeraOS/notes/scan.py",
                "content": (
                    "import os\n"
                    "for name in os.listdir('.'):\n"
                    "    if name.endswith('.txt'):\n"
                    "        print(name)\n"
                ),
                "mode": "overwrite",
            },
        }

    @pytest.mark.parametrize(
        "message",
        [
            # Exact live regression from PR #311
            "Make it save the results to a file.",
            # Task-listed variations
            "Make it write the output to a file.",
            "Add file logging.",
            "Make it export the results.",
            "Save the scan results to a file.",
            "Have it write a report.",
            "Make the script output to a file.",
            # Additional natural-language variants
            "make it also save the output to a file",
            "have it log the results",
            "have it print the results",
            "make it emit a report",
            "make the program write the findings to a file",
            "make the script log the output",
            "add output logging",
            "add a report step",
        ],
    )
    def test_script_enhancement_matches_revision_gate(
        self, script_preview: dict, message: str
    ) -> None:
        assert is_active_preview_revision_turn(message, active_preview=script_preview)

    @pytest.mark.parametrize(
        "message",
        [
            # Bare investigation-save phrases WITHOUT a subject pronoun /
            # active-preview anchor must NOT match the gate even when a
            # preview is present — those are genuine investigation-save
            # intents and belt-and-suspenders in app.py handles the active-
            # preview case separately.
            "save the findings to a note",
            "save result 1 to a file",
            "export all findings",
            "save the comparison to a note",
        ],
    )
    def test_bare_investigation_save_does_not_match_gate(
        self, script_preview: dict, message: str
    ) -> None:
        # These return False from the narrow gate even with a preview —
        # the belt-and-suspenders layer in app.py is what catches them
        # when a normal active preview is in play. That split keeps the
        # narrow gate honest (it only fires on phrases that clearly
        # reference the active draft/script).
        assert not is_active_preview_revision_turn(message, active_preview=script_preview)

    @pytest.mark.parametrize(
        "message",
        [
            # Without an active preview, none of the script-enhancement
            # patterns should fire — they require is_normal_preview.
            "Make it save the results to a file.",
            "Add file logging.",
            "Have it write a report.",
        ],
    )
    def test_script_enhancement_requires_active_preview(self, message: str) -> None:
        assert not is_active_preview_revision_turn(message, active_preview=None)


# ---------------------------------------------------------------------------
# 9b. Tone / style / voice revision gate (PR #313 live regression follow-up)
# ---------------------------------------------------------------------------


class TestToneAndStyleRevisionGate:
    """Regression coverage for PR #313 live repro: tone/style/voice revisions.

    A normal active prose preview plus a phrase like "change the tone to
    more technical" must be classified as an active-preview revision turn
    so downstream code (``is_writing_refinement_request``) promotes the
    turn to a writing-draft refinement and the submission-claim guardrail
    stays off — otherwise note content that legitimately mentions
    ``queued`` (e.g. a note about queue-backed execution) trips the
    guardrail and the user sees "I have not submitted anything" instead
    of their revision.
    """

    @pytest.fixture
    def prose_preview(self) -> dict:
        return {
            "goal": "draft a note explaining queue-backed execution",
            "write_file": {
                "path": "~/VoxeraOS/notes/queue-safety.txt",
                "content": (
                    "Queue-backed execution is safer than direct AI execution "
                    "because every job is audited and queued before running."
                ),
                "mode": "overwrite",
            },
        }

    @pytest.mark.parametrize(
        "message",
        [
            # The exact live reproduction phrase from PR #313.
            "Change the tone to more technical.",
            # The task-listed variations.
            "Make it more formal.",
            "Simplify the language.",
            "Make it more concise.",
            "Rewrite it in a more technical tone.",
            "Change the style.",
            "Make it sound more professional.",
            # Additional natural variants.
            "Simplify it.",
            "Change the voice.",
            "Adjust the tone.",
            "Make it more technical.",
            "Make it more readable.",
            "Have it sound more professional.",
            "Make it simpler.",
            "change the wording",
            "shift the register",
            "update the phrasing",
            "simplify the wording",
        ],
    )
    def test_tone_style_phrases_match_revision_gate(
        self, prose_preview: dict, message: str
    ) -> None:
        assert is_active_preview_revision_turn(message, active_preview=prose_preview)

    @pytest.mark.parametrize(
        "message",
        [
            # Without an active preview, tone/style phrases do not fire —
            # the gate still requires is_normal_preview.
            "Change the tone to more technical.",
            "Make it more formal.",
            "Simplify the language.",
        ],
    )
    def test_tone_style_phrases_require_active_preview(self, message: str) -> None:
        assert not is_active_preview_revision_turn(message, active_preview=None)

    @pytest.mark.parametrize(
        "message",
        [
            # Unrelated phrases that superficially share verbs but are not
            # revisions — the gate must stay narrow enough that unrelated
            # turns still fall through.
            "what is the weather in Calgary?",
            "show me the automation",
            "run it now",
            "what time is it?",
            "send it",
            "go ahead",
            "save all findings",
            "expand result 1",
        ],
    )
    def test_unrelated_phrases_do_not_match_gate(self, prose_preview: dict, message: str) -> None:
        assert not is_active_preview_revision_turn(message, active_preview=prose_preview)


# ---------------------------------------------------------------------------
# 10. Live regression (PR #311): end-to-end chat flow
# ---------------------------------------------------------------------------


def _install_pr311_scan_script(session) -> None:
    """Install the exact preview shape produced by PR #311's live repro turn 1."""
    payload = {
        "goal": "draft a python script as scan.py",
        "write_file": {
            "path": "~/VoxeraOS/notes/scan.py",
            "content": (
                "import os\n"
                "for name in os.listdir('.'):\n"
                "    if name.endswith('.txt'):\n"
                "        print(name)\n"
            ),
            "mode": "overwrite",
        },
    }
    preview_ownership.reset_active_preview(session.queue, session.session_id, payload)
    context_on_preview_created(
        session.queue, session.session_id, draft_ref="~/VoxeraOS/notes/scan.py"
    )


def test_pr311_live_hijack_make_it_save_the_results_to_a_file(tmp_path, monkeypatch):
    """Named regression: PR #311 live reproduction.

    Turn 1: script preview.
    Turn 2: "Make it save the results to a file." — the live product
    showed the investigation-save branch firing and returning
    "I couldn't resolve those investigation result references...".
    After the fix this must land on the active preview revision lane.
    """
    session = make_vera_session(monkeypatch, tmp_path)
    _install_pr311_scan_script(session)

    async def _pass_reply(**kwargs):
        return {
            "answer": (
                "Here's the updated script that also saves the scan results "
                "to ~/VoxeraOS/notes/scan_results.txt."
            ),
            "status": "ok:test",
        }

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _pass_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _passthrough_builder)

    resp = session.chat("Make it save the results to a file.")
    assert resp.status_code == 200

    turns = session.turns()
    last_reply = [t["text"] for t in turns if t["role"] == "assistant"][-1]
    # The bug we are fixing: the early-exit investigation-save branch
    # used to fire and return this exact error string. After the fix
    # the turn must land anywhere EXCEPT that error.
    assert "couldn't resolve those investigation result references" not in last_reply.lower()
    assert "run a fresh read-only investigation first" not in last_reply.lower()

    # Active preview is preserved (path is still scan.py). Content may
    # be updated by the LLM/builder path or left intact — we only
    # assert the preview was not clobbered into an investigation-save
    # payload pointing at a note path like "note.md".
    preview = session.preview()
    assert preview is not None
    wf = preview.get("write_file") or {}
    assert str(wf.get("path") or "").endswith("scan.py"), (
        f"Expected scan.py preview, got: {wf.get('path')!r}"
    )


@pytest.mark.parametrize(
    "message",
    [
        "make it save the results to a file",
        "add file logging",
        "have it write a report",
        "make it export the results",
        "save the scan results to a file",
        "make the script output to a file",
        "make it write the output to a file",
    ],
)
def test_pr311_script_enhancement_does_not_hijack_active_preview(
    tmp_path, monkeypatch, message: str
):
    """Each task-listed phrase must NOT be hijacked by investigation-save.

    Parametrized over the full list of phrases the task brief flagged,
    asserting that (a) the early-exit investigation-save error never
    reaches chat, and (b) the active preview slot remains a
    ``scan.py`` preview rather than an investigation-save payload.
    """
    session = make_vera_session(monkeypatch, tmp_path)
    _install_pr311_scan_script(session)

    async def _pass_reply(**kwargs):
        return {"answer": "Understood.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _pass_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _passthrough_builder)

    resp = session.chat(message)
    assert resp.status_code == 200

    turns = session.turns()
    last_reply = [t["text"] for t in turns if t["role"] == "assistant"][-1]
    assert "couldn't resolve those investigation result references" not in last_reply.lower()

    preview = session.preview()
    assert preview is not None
    wf = preview.get("write_file") or {}
    assert str(wf.get("path") or "").endswith("scan.py")


def test_pr311_legitimate_investigation_save_still_works(tmp_path, monkeypatch):
    """Genuine investigation-save still works when NO active preview is in play.

    The two-part fix must not globally disable investigation-save
    behavior — it only prefers the revision lane when a normal active
    preview is present.  Without an active preview, "save the findings
    to a note" still lands on the investigation-save branch (which
    fails closed honestly when no investigation has been run).
    """
    session = make_vera_session(monkeypatch, tmp_path)
    assert session.preview() is None

    async def _pass_reply(**kwargs):
        return {"answer": "Understood.", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _pass_reply)
    monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _passthrough_builder)

    resp = session.chat("save the findings to a note")
    assert resp.status_code == 200

    turns = session.turns()
    last_reply = [t["text"] for t in turns if t["role"] == "assistant"][-1]
    # With no investigation context, the investigation-save branch
    # fails closed with its canonical error — which is the correct
    # behavior when there is no active normal preview.
    assert "investigation" in last_reply.lower() or "result" in last_reply.lower()


def test_pr311_ambiguous_change_still_fails_closed(tmp_path, monkeypatch):
    """Pure ambiguous 'change it' on an active script preview still fails closed.

    The script-enhancement patterns require a concrete verb
    (save/write/output/log/etc.) — they do NOT match bare "change it"
    or "make it better". Those remain handled by the existing
    ambiguous-change guard in draft_revision.
    """
    session = make_vera_session(monkeypatch, tmp_path)
    _install_pr311_scan_script(session)

    # The gate must NOT match ambiguous phrases, even with an active
    # script preview present.
    preview = session.preview()
    assert not is_active_preview_revision_turn("change it", active_preview=preview)
    assert not is_active_preview_revision_turn("fix it", active_preview=preview)
    assert not is_active_preview_revision_turn("make it better", active_preview=preview)
