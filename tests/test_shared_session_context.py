"""Tests for the shared session context model.

Validates:
- context initialization and schema coherence
- persistence across reads/writes/updates
- field-level update semantics (merge, not replace)
- preservation across turn appends
- conservative behavior on missing/invalid/ambiguous data
- precedence: context never overrides preview/queue/artifact truth
- lifecycle update points (preview write, submit, completion, clear)
"""

from __future__ import annotations

from pathlib import Path

from voxera.vera.session_store import (
    _SHARED_CONTEXT_FIELD,
    _empty_shared_context,
    _normalize_shared_context,
    _write_session_payload,
    append_session_turn,
    clear_session_context,
    new_session_id,
    read_session_context,
    read_session_preview,
    update_session_context,
    write_session_context,
    write_session_preview,
)


def _make_session(tmp_path: Path) -> tuple[Path, str]:
    queue = tmp_path / "queue"
    sid = new_session_id()
    return queue, sid


# ---------------------------------------------------------------------------
# 1. Schema coherence and initialization
# ---------------------------------------------------------------------------


class TestEmptyContextSchema:
    def test_empty_context_has_canonical_keys(self):
        ctx = _empty_shared_context()
        assert "active_draft_ref" in ctx
        assert "active_preview_ref" in ctx
        assert "last_submitted_job_ref" in ctx
        assert "last_completed_job_ref" in ctx
        assert "last_reviewed_job_ref" in ctx
        assert "last_saved_file_ref" in ctx
        assert "active_topic" in ctx
        assert "ambiguity_flags" in ctx
        assert "updated_at_ms" in ctx

    def test_empty_context_ref_fields_are_none(self):
        ctx = _empty_shared_context()
        for key in (
            "active_draft_ref",
            "active_preview_ref",
            "last_submitted_job_ref",
            "last_completed_job_ref",
            "last_reviewed_job_ref",
            "last_saved_file_ref",
            "active_topic",
        ):
            assert ctx[key] is None, f"Expected None for {key}"

    def test_empty_context_ambiguity_flags_is_empty_list(self):
        ctx = _empty_shared_context()
        assert ctx["ambiguity_flags"] == []

    def test_empty_context_timestamp_is_zero(self):
        ctx = _empty_shared_context()
        assert ctx["updated_at_ms"] == 0


# ---------------------------------------------------------------------------
# 2. Normalization
# ---------------------------------------------------------------------------


class TestNormalization:
    def test_normalize_non_dict_returns_empty(self):
        assert _normalize_shared_context(None) == _empty_shared_context()
        assert _normalize_shared_context("junk") == _empty_shared_context()
        assert _normalize_shared_context(42) == _empty_shared_context()
        assert _normalize_shared_context([]) == _empty_shared_context()

    def test_normalize_preserves_valid_refs(self):
        raw = {"active_draft_ref": "notes/todo.md", "active_topic": "weather check"}
        ctx = _normalize_shared_context(raw)
        assert ctx["active_draft_ref"] == "notes/todo.md"
        assert ctx["active_topic"] == "weather check"

    def test_normalize_strips_whitespace(self):
        raw = {"active_draft_ref": "  notes/file.md  "}
        ctx = _normalize_shared_context(raw)
        assert ctx["active_draft_ref"] == "notes/file.md"

    def test_normalize_clears_empty_strings(self):
        raw = {"active_draft_ref": "", "active_topic": "  "}
        ctx = _normalize_shared_context(raw)
        assert ctx["active_draft_ref"] is None
        assert ctx["active_topic"] is None

    def test_normalize_clears_non_string_refs(self):
        raw = {"active_draft_ref": 42, "last_saved_file_ref": True}
        ctx = _normalize_shared_context(raw)
        assert ctx["active_draft_ref"] is None
        assert ctx["last_saved_file_ref"] is None

    def test_normalize_drops_unknown_keys(self):
        raw = {"active_draft_ref": "a.md", "unknown_key": "surprise"}
        ctx = _normalize_shared_context(raw)
        assert "unknown_key" not in ctx

    def test_normalize_ambiguity_flags_bounded(self):
        raw = {"ambiguity_flags": [f"flag-{i}" for i in range(20)]}
        ctx = _normalize_shared_context(raw)
        assert len(ctx["ambiguity_flags"]) == 8

    def test_normalize_ambiguity_flags_strips_empty(self):
        raw = {"ambiguity_flags": ["valid", "", "  ", "also-valid"]}
        ctx = _normalize_shared_context(raw)
        assert ctx["ambiguity_flags"] == ["valid", "also-valid"]

    def test_normalize_ambiguity_flags_non_list(self):
        raw = {"ambiguity_flags": "not-a-list"}
        ctx = _normalize_shared_context(raw)
        assert ctx["ambiguity_flags"] == []

    def test_normalize_timestamp_negative(self):
        raw = {"updated_at_ms": -5}
        ctx = _normalize_shared_context(raw)
        assert ctx["updated_at_ms"] == 0

    def test_normalize_timestamp_valid(self):
        raw = {"updated_at_ms": 1234567890}
        ctx = _normalize_shared_context(raw)
        assert ctx["updated_at_ms"] == 1234567890


# ---------------------------------------------------------------------------
# 3. Persistence: read / write / update
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_read_empty_session_returns_empty_context(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        ctx = read_session_context(queue, sid)
        assert ctx == _empty_shared_context()

    def test_write_then_read_roundtrip(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        write_session_context(queue, sid, {"active_draft_ref": "notes/draft.md"})
        ctx = read_session_context(queue, sid)
        assert ctx["active_draft_ref"] == "notes/draft.md"
        assert ctx["updated_at_ms"] > 0

    def test_update_merges_not_replaces(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        write_session_context(
            queue,
            sid,
            {
                "active_draft_ref": "notes/a.md",
                "last_submitted_job_ref": "inbox-abc.json",
            },
        )
        update_session_context(queue, sid, active_topic="plan review")
        ctx = read_session_context(queue, sid)
        assert ctx["active_draft_ref"] == "notes/a.md"
        assert ctx["last_submitted_job_ref"] == "inbox-abc.json"
        assert ctx["active_topic"] == "plan review"

    def test_update_ignores_unknown_keys(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        ctx = update_session_context(queue, sid, bogus_field="nope")
        assert "bogus_field" not in ctx

    def test_update_cannot_set_timestamp_directly(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        update_session_context(queue, sid, updated_at_ms=1)
        ctx = read_session_context(queue, sid)
        # timestamp is managed internally, should be current time not 1
        assert ctx["updated_at_ms"] > 1

    def test_clear_resets_to_none(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        write_session_context(queue, sid, {"active_draft_ref": "notes/file.md"})
        clear_session_context(queue, sid)
        ctx = read_session_context(queue, sid)
        assert ctx == _empty_shared_context()

    def test_write_normalizes_before_persist(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        write_session_context(
            queue,
            sid,
            {"active_draft_ref": "  padded  ", "unknown": "dropped"},
        )
        ctx = read_session_context(queue, sid)
        assert ctx["active_draft_ref"] == "padded"
        assert "unknown" not in ctx


# ---------------------------------------------------------------------------
# 4. Preservation across turn appends
# ---------------------------------------------------------------------------


class TestTurnPreservation:
    def test_context_survives_user_turn(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        write_session_context(
            queue,
            sid,
            {"active_draft_ref": "notes/hello.md", "active_topic": "greeting"},
        )
        append_session_turn(queue, sid, role="user", text="hello")
        ctx = read_session_context(queue, sid)
        assert ctx["active_draft_ref"] == "notes/hello.md"
        assert ctx["active_topic"] == "greeting"

    def test_context_survives_assistant_turn(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        write_session_context(
            queue,
            sid,
            {"last_submitted_job_ref": "inbox-abc.json"},
        )
        append_session_turn(queue, sid, role="assistant", text="Done!")
        ctx = read_session_context(queue, sid)
        assert ctx["last_submitted_job_ref"] == "inbox-abc.json"

    def test_context_survives_multiple_turns(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        write_session_context(
            queue,
            sid,
            {"active_topic": "multi-turn test"},
        )
        for i in range(5):
            append_session_turn(queue, sid, role="user", text=f"msg {i}")
            append_session_turn(queue, sid, role="assistant", text=f"reply {i}")
        ctx = read_session_context(queue, sid)
        assert ctx["active_topic"] == "multi-turn test"


# ---------------------------------------------------------------------------
# 5. Context does NOT override preview/queue/artifact truth
# ---------------------------------------------------------------------------


class TestTruthPrecedence:
    def test_context_does_not_affect_preview_read(self, tmp_path: Path):
        """Context claiming a draft ref does not fabricate a preview."""
        queue, sid = _make_session(tmp_path)
        update_session_context(queue, sid, active_draft_ref="notes/phantom.md")
        preview = read_session_preview(queue, sid)
        assert preview is None

    def test_preview_truth_independent_of_context(self, tmp_path: Path):
        """Preview is authoritative even when context says something different."""
        queue, sid = _make_session(tmp_path)
        write_session_preview(
            queue, sid, {"write_file": {"path": "~/VoxeraOS/notes/real.md", "content": "hi"}}
        )
        update_session_context(queue, sid, active_draft_ref="notes/other.md")
        preview = read_session_preview(queue, sid)
        assert preview is not None
        assert preview["write_file"]["path"] == "~/VoxeraOS/notes/real.md"

    def test_context_and_preview_coexist_independently(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        write_session_preview(
            queue, sid, {"write_file": {"path": "~/VoxeraOS/notes/a.md", "content": "content"}}
        )
        update_session_context(
            queue,
            sid,
            active_draft_ref="notes/a.md",
            active_preview_ref="preview",
        )
        # Both exist independently
        preview = read_session_preview(queue, sid)
        ctx = read_session_context(queue, sid)
        assert preview is not None
        assert ctx["active_preview_ref"] == "preview"
        # Clearing preview does not clear context
        write_session_preview(queue, sid, None)
        assert read_session_preview(queue, sid) is None
        ctx2 = read_session_context(queue, sid)
        assert ctx2["active_preview_ref"] == "preview"


# ---------------------------------------------------------------------------
# 6. Conservative behavior: missing, ambiguous, corrupt data
# ---------------------------------------------------------------------------


class TestConservativeBehavior:
    def test_read_from_nonexistent_session_file(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        ctx = read_session_context(queue, sid)
        assert ctx == _empty_shared_context()

    def test_corrupt_context_field_returns_empty(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        # Write a session with corrupt shared_context
        _write_session_payload(
            queue, sid, {"session_id": sid, "turns": [], _SHARED_CONTEXT_FIELD: "not-a-dict"}
        )
        ctx = read_session_context(queue, sid)
        assert ctx == _empty_shared_context()

    def test_partial_context_fills_missing_with_defaults(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        _write_session_payload(
            queue,
            sid,
            {
                "session_id": sid,
                "turns": [],
                _SHARED_CONTEXT_FIELD: {"active_topic": "partial"},
            },
        )
        ctx = read_session_context(queue, sid)
        assert ctx["active_topic"] == "partial"
        assert ctx["active_draft_ref"] is None
        assert ctx["ambiguity_flags"] == []

    def test_ambiguity_flags_are_bounded(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        flags = [f"ambig-{i}" for i in range(20)]
        update_session_context(queue, sid, ambiguity_flags=flags)
        ctx = read_session_context(queue, sid)
        assert len(ctx["ambiguity_flags"]) == 8

    def test_update_returns_normalized_result(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        result = update_session_context(
            queue, sid, active_draft_ref="  spaced  ", unknown="dropped"
        )
        assert result["active_draft_ref"] == "spaced"
        assert "unknown" not in result


# ---------------------------------------------------------------------------
# 7. Lifecycle integration (via session store, not full app)
# ---------------------------------------------------------------------------


class TestLifecycleUpdatePoints:
    def test_preview_write_does_not_auto_update_context(self, tmp_path: Path):
        """session_store.write_session_preview does not touch context.

        Context updates happen at the app layer, not the store layer.
        This ensures store functions remain composable.
        """
        queue, sid = _make_session(tmp_path)
        write_session_preview(
            queue, sid, {"write_file": {"path": "~/VoxeraOS/notes/x.md", "content": "c"}}
        )
        ctx = read_session_context(queue, sid)
        assert ctx == _empty_shared_context()

    def test_context_update_at_preview_creation(self, tmp_path: Path):
        """Simulate what app.py does: write preview + update context."""
        queue, sid = _make_session(tmp_path)
        preview = {"write_file": {"path": "~/VoxeraOS/notes/plan.md", "content": "plan"}}
        write_session_preview(queue, sid, preview)
        update_session_context(
            queue,
            sid,
            active_draft_ref="notes/plan.md",
            active_preview_ref="preview",
        )
        ctx = read_session_context(queue, sid)
        assert ctx["active_draft_ref"] == "notes/plan.md"
        assert ctx["active_preview_ref"] == "preview"

    def test_context_update_at_submit(self, tmp_path: Path):
        """Simulate what app.py does: clear preview refs, set submitted job."""
        queue, sid = _make_session(tmp_path)
        update_session_context(
            queue,
            sid,
            active_draft_ref="notes/plan.md",
            active_preview_ref="preview",
        )
        # Simulate successful submit
        update_session_context(
            queue,
            sid,
            active_preview_ref=None,
            active_draft_ref=None,
            last_submitted_job_ref="inbox-123abc.json",
        )
        ctx = read_session_context(queue, sid)
        assert ctx["active_preview_ref"] is None
        assert ctx["active_draft_ref"] is None
        assert ctx["last_submitted_job_ref"] == "inbox-123abc.json"

    def test_context_update_at_completion(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        update_session_context(queue, sid, last_submitted_job_ref="inbox-abc.json")
        update_session_context(queue, sid, last_completed_job_ref="inbox-abc.json")
        ctx = read_session_context(queue, sid)
        assert ctx["last_submitted_job_ref"] == "inbox-abc.json"
        assert ctx["last_completed_job_ref"] == "inbox-abc.json"

    def test_context_update_at_review(self, tmp_path: Path):
        queue, sid = _make_session(tmp_path)
        update_session_context(queue, sid, last_reviewed_job_ref="inbox-xyz.json")
        ctx = read_session_context(queue, sid)
        assert ctx["last_reviewed_job_ref"] == "inbox-xyz.json"

    def test_context_survives_full_lifecycle_sequence(self, tmp_path: Path):
        """Walk through: draft → preview → submit → complete → review."""
        queue, sid = _make_session(tmp_path)

        # Draft created
        update_session_context(
            queue, sid, active_draft_ref="notes/report.md", active_topic="report"
        )

        # Preview built
        update_session_context(queue, sid, active_preview_ref="preview")
        append_session_turn(queue, sid, role="user", text="looks good, submit it")

        # Submitted
        update_session_context(
            queue,
            sid,
            active_preview_ref=None,
            active_draft_ref=None,
            last_submitted_job_ref="inbox-rpt.json",
        )
        append_session_turn(queue, sid, role="assistant", text="Submitted.")

        # Completed
        update_session_context(queue, sid, last_completed_job_ref="inbox-rpt.json")

        # Reviewed
        update_session_context(queue, sid, last_reviewed_job_ref="inbox-rpt.json")

        ctx = read_session_context(queue, sid)
        assert ctx["active_preview_ref"] is None
        assert ctx["active_draft_ref"] is None
        assert ctx["last_submitted_job_ref"] == "inbox-rpt.json"
        assert ctx["last_completed_job_ref"] == "inbox-rpt.json"
        assert ctx["last_reviewed_job_ref"] == "inbox-rpt.json"
        assert ctx["active_topic"] == "report"
