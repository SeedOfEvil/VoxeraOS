"""Tests for the session context and routing debug surfaces.

Validates:
- routing debug entry persistence and bounded history
- routing debug normalization (malformed entries, missing fields)
- session debug snapshot assembles session + context + routing
- routing debug is cleared on session clear
- routing debug is preserved across turn appends
- debug JSON endpoint returns bounded operator-safe data
- no behavior drift in standard chat paths
- bounded output shape (no raw internal payloads)
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from voxera.vera import session_store as vera_session_store
from voxera.vera.session_store import (
    _MAX_ROUTING_DEBUG_HISTORY,
    _ROUTING_DEBUG_FIELD,
    _empty_routing_debug,
    append_routing_debug_entry,
    append_session_turn,
    clear_session_routing_debug,
    new_session_id,
    read_session_routing_debug,
    session_debug_snapshot,
    update_session_context,
)
from voxera.vera_web import app as vera_app_module

from .vera_session_helpers import make_vera_session, set_vera_queue_root


def _make_session(tmp_path: Path) -> tuple[Path, str]:
    queue = tmp_path / "queue"
    sid = new_session_id()
    return queue, sid


# ---------------------------------------------------------------------------
# 1. Routing debug persistence
# ---------------------------------------------------------------------------


class TestRoutingDebugPersistence:
    def test_empty_routing_debug_on_fresh_session(self, tmp_path):
        queue, sid = _make_session(tmp_path)
        result = read_session_routing_debug(queue, sid)
        assert result == _empty_routing_debug()
        assert result["entries"] == []
        assert result["updated_at_ms"] == 0

    def test_append_single_entry(self, tmp_path):
        queue, sid = _make_session(tmp_path)
        append_routing_debug_entry(
            queue,
            sid,
            route_status="reviewed_job_outcome",
            dispatch_source="early_exit_dispatch",
            matched_early_exit=True,
            turn_index=2,
        )
        result = read_session_routing_debug(queue, sid)
        assert len(result["entries"]) == 1
        entry = result["entries"][0]
        assert entry["route_status"] == "reviewed_job_outcome"
        assert entry["dispatch_source"] == "early_exit_dispatch"
        assert entry["matched_early_exit"] is True
        assert entry["turn_index"] == 2
        assert isinstance(entry["timestamp_ms"], int)
        assert entry["timestamp_ms"] > 0

    def test_append_multiple_entries(self, tmp_path):
        queue, sid = _make_session(tmp_path)
        append_routing_debug_entry(
            queue,
            sid,
            route_status="status_a",
            dispatch_source="source_a",
        )
        append_routing_debug_entry(
            queue,
            sid,
            route_status="status_b",
            dispatch_source="source_b",
            matched_early_exit=True,
        )
        result = read_session_routing_debug(queue, sid)
        assert len(result["entries"]) == 2
        assert result["entries"][0]["route_status"] == "status_a"
        assert result["entries"][1]["route_status"] == "status_b"
        assert result["entries"][1]["matched_early_exit"] is True

    def test_bounded_history(self, tmp_path):
        queue, sid = _make_session(tmp_path)
        for i in range(_MAX_ROUTING_DEBUG_HISTORY + 5):
            append_routing_debug_entry(
                queue,
                sid,
                route_status=f"status_{i}",
                dispatch_source="test",
            )
        result = read_session_routing_debug(queue, sid)
        assert len(result["entries"]) == _MAX_ROUTING_DEBUG_HISTORY
        # Oldest entries are dropped
        assert result["entries"][0]["route_status"] == "status_5"
        assert result["entries"][-1]["route_status"] == f"status_{_MAX_ROUTING_DEBUG_HISTORY + 4}"

    def test_clear_routing_debug(self, tmp_path):
        queue, sid = _make_session(tmp_path)
        append_routing_debug_entry(
            queue,
            sid,
            route_status="some_status",
            dispatch_source="test",
        )
        assert len(read_session_routing_debug(queue, sid)["entries"]) == 1
        clear_session_routing_debug(queue, sid)
        result = read_session_routing_debug(queue, sid)
        assert result["entries"] == []


# ---------------------------------------------------------------------------
# 2. Routing debug normalization
# ---------------------------------------------------------------------------


class TestRoutingDebugNormalization:
    def test_malformed_entries_dropped(self, tmp_path):
        queue, sid = _make_session(tmp_path)
        # Write raw payload with bad entries
        vera_session_store._write_session_payload(
            queue,
            sid,
            {
                "session_id": sid,
                "updated_at_ms": 1000,
                "turns": [],
                _ROUTING_DEBUG_FIELD: {
                    "entries": [
                        "not_a_dict",
                        {"route_status": ""},  # empty status
                        {"route_status": "valid_status", "dispatch_source": "test"},
                        42,
                    ],
                    "updated_at_ms": 1000,
                },
            },
        )
        result = read_session_routing_debug(queue, sid)
        assert len(result["entries"]) == 1
        assert result["entries"][0]["route_status"] == "valid_status"

    def test_missing_dispatch_source_defaults_to_unknown(self, tmp_path):
        queue, sid = _make_session(tmp_path)
        vera_session_store._write_session_payload(
            queue,
            sid,
            {
                "session_id": sid,
                "updated_at_ms": 1000,
                "turns": [],
                _ROUTING_DEBUG_FIELD: {
                    "entries": [{"route_status": "test_status"}],
                    "updated_at_ms": 1000,
                },
            },
        )
        result = read_session_routing_debug(queue, sid)
        assert result["entries"][0]["dispatch_source"] == "unknown"

    def test_non_dict_routing_debug_returns_empty(self, tmp_path):
        queue, sid = _make_session(tmp_path)
        vera_session_store._write_session_payload(
            queue,
            sid,
            {
                "session_id": sid,
                "updated_at_ms": 1000,
                "turns": [],
                _ROUTING_DEBUG_FIELD: "not_a_dict",
            },
        )
        result = read_session_routing_debug(queue, sid)
        assert result == _empty_routing_debug()


# ---------------------------------------------------------------------------
# 3. Routing debug preserved across turn appends
# ---------------------------------------------------------------------------


class TestRoutingDebugPreservedAcrossTurns:
    def test_routing_debug_survives_turn_append(self, tmp_path):
        queue, sid = _make_session(tmp_path)
        append_routing_debug_entry(
            queue,
            sid,
            route_status="original_route",
            dispatch_source="test",
        )
        # Append a turn (this should preserve routing debug)
        append_session_turn(queue, sid, role="user", text="hello")
        result = read_session_routing_debug(queue, sid)
        assert len(result["entries"]) == 1
        assert result["entries"][0]["route_status"] == "original_route"


# ---------------------------------------------------------------------------
# 4. Session debug snapshot
# ---------------------------------------------------------------------------


class TestSessionDebugSnapshot:
    def test_snapshot_includes_base_debug_info(self, tmp_path):
        queue, sid = _make_session(tmp_path)
        snapshot = session_debug_snapshot(queue, sid, mode_status="test_mode")
        # Base session_debug_info fields
        assert snapshot["session_id"] == sid
        assert snapshot["mode_status"] == "test_mode"
        assert snapshot["dev_mode"] is True

    def test_snapshot_includes_context_refs(self, tmp_path):
        queue, sid = _make_session(tmp_path)
        update_session_context(
            queue,
            sid,
            active_draft_ref="my-draft.md",
            last_submitted_job_ref="job-123.json",
            active_topic="test topic",
        )
        snapshot = session_debug_snapshot(queue, sid, mode_status="test")
        assert snapshot["context_active_draft_ref"] == "my-draft.md"
        assert snapshot["context_last_submitted_job_ref"] == "job-123.json"
        assert snapshot["context_active_topic"] == "test topic"
        assert snapshot["context_active_preview_ref"] is None
        assert snapshot["context_last_completed_job_ref"] is None

    def test_snapshot_includes_routing_debug(self, tmp_path):
        queue, sid = _make_session(tmp_path)
        append_routing_debug_entry(
            queue,
            sid,
            route_status="ok:test",
            dispatch_source="llm_orchestration",
        )
        snapshot = session_debug_snapshot(queue, sid, mode_status="test")
        assert snapshot["routing_debug_entry_count"] == 1
        assert len(snapshot["routing_debug_entries"]) == 1
        assert snapshot["routing_debug_entries"][0]["route_status"] == "ok:test"

    def test_snapshot_empty_session_has_safe_defaults(self, tmp_path):
        queue, sid = _make_session(tmp_path)
        snapshot = session_debug_snapshot(queue, sid, mode_status="conversation")
        # All context refs should be None
        for key in (
            "context_active_draft_ref",
            "context_active_preview_ref",
            "context_last_submitted_job_ref",
            "context_last_completed_job_ref",
            "context_last_reviewed_job_ref",
            "context_last_saved_file_ref",
            "context_active_topic",
        ):
            assert snapshot[key] is None, f"Expected None for {key}"
        assert snapshot["context_ambiguity_flags"] == []
        assert snapshot["routing_debug_entries"] == []
        assert snapshot["routing_debug_entry_count"] == 0


# ---------------------------------------------------------------------------
# 5. Debug JSON endpoint
# ---------------------------------------------------------------------------


class TestDebugJsonEndpoint:
    def test_returns_400_without_session_id(self, tmp_path, monkeypatch):
        set_vera_queue_root(monkeypatch, tmp_path / "queue")
        client = TestClient(vera_app_module.app)
        resp = client.get("/vera/debug/session.json")
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "no_session_id"

    def test_returns_snapshot_for_valid_session(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)
        client = session.client
        resp = client.get(f"/vera/debug/session.json?session_id={session.session_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == session.session_id
        assert data["dev_mode"] is True
        assert "context_active_draft_ref" in data
        assert "routing_debug_entries" in data
        assert "routing_debug_entry_count" in data

    def test_endpoint_reads_session_cookie(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)
        client = session.client
        # The session cookie should have been set by make_vera_session
        resp = client.get("/vera/debug/session.json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == session.session_id

    def test_endpoint_does_not_mutate_state(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)
        # Record state before
        ctx_before = session.session_context()
        turns_before = session.turns()
        # Call debug endpoint
        session.client.get(f"/vera/debug/session.json?session_id={session.session_id}")
        # State must not change
        assert session.session_context() == ctx_before
        assert session.turns() == turns_before


# ---------------------------------------------------------------------------
# 6. Chat flow produces routing debug entries
# ---------------------------------------------------------------------------


class TestChatFlowRoutingDebug:
    def test_normal_chat_produces_routing_debug_entry(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message, **_kw):
            return {"answer": "Hello!", "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        resp = session.chat("hi there")
        assert resp.status_code == 200

        routing = read_session_routing_debug(session.queue, session.session_id)
        assert len(routing["entries"]) >= 1
        last_entry = routing["entries"][-1]
        assert last_entry["route_status"] == "ok:test"
        assert last_entry["dispatch_source"] == "llm_orchestration"
        assert last_entry["matched_early_exit"] is False

    def test_early_exit_produces_routing_debug_entry(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)

        # Submit with no preview triggers submit_no_preview handoff path, which
        # records a routing debug entry with the handoff status.
        resp = session.chat("send it")
        assert resp.status_code == 200

        routing = read_session_routing_debug(session.queue, session.session_id)
        assert len(routing["entries"]) >= 1
        last_entry = routing["entries"][-1]
        # The exact status depends on the handoff path — the key assertion is
        # that the routing debug entry was recorded for a non-LLM path.
        assert last_entry["route_status"] != ""
        assert last_entry["dispatch_source"] in (
            "early_exit_dispatch",
            "submit_no_preview",
            "submit_active_preview",
        )

    def test_session_clear_resets_routing_debug(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message, **_kw):
            return {"answer": "Hello!", "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        session.chat("hello")
        routing = read_session_routing_debug(session.queue, session.session_id)
        assert len(routing["entries"]) >= 1

        # Clear session
        session.client.post("/clear", data={"session_id": session.session_id})

        routing = read_session_routing_debug(session.queue, session.session_id)
        assert routing["entries"] == []


# ---------------------------------------------------------------------------
# 7. No behavior drift — standard chat paths
# ---------------------------------------------------------------------------


class TestNoBehaviorDrift:
    """Verify that adding routing debug does not change observable chat behavior."""

    def test_normal_reply_content_unchanged(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message, **_kw):
            return {"answer": "The answer is 42.", "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        resp = session.chat("What is the meaning of life?")
        assert resp.status_code == 200
        turns = session.turns()
        assert any("42" in t["text"] for t in turns if t["role"] == "assistant")

    def test_preview_flow_unchanged(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)

        async def _fake_reply(*, turns, user_message, **_kw):
            return {"answer": "Done.", "status": "ok:test"}

        async def _fake_builder(*, turns, user_message, active_preview, **_kw):
            return {
                "goal": "test goal",
                "write_file": {
                    "path": "~/VoxeraOS/notes/test.md",
                    "content": "test content",
                    "mode": "overwrite",
                },
            }

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
        monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _fake_builder)

        resp = session.chat("write a note about testing")
        assert resp.status_code == 200
        preview = session.preview()
        assert preview is not None


# ---------------------------------------------------------------------------
# 8. Bounded output shape
# ---------------------------------------------------------------------------


class TestBoundedOutputShape:
    def test_snapshot_keys_are_bounded(self, tmp_path):
        """The snapshot must not contain raw internal payloads."""
        queue, sid = _make_session(tmp_path)
        append_routing_debug_entry(
            queue,
            sid,
            route_status="test",
            dispatch_source="test",
        )
        update_session_context(queue, sid, active_draft_ref="draft.md")
        snapshot = session_debug_snapshot(queue, sid, mode_status="test")
        # No raw turns, no raw preview payloads, no system prompt in snapshot
        assert "turns" not in snapshot
        assert "pending_job_preview" not in snapshot
        assert "system_prompt" not in snapshot
        # Context refs are scalar or None, not dicts
        for key in (
            "context_active_draft_ref",
            "context_active_preview_ref",
            "context_last_submitted_job_ref",
        ):
            val = snapshot[key]
            assert val is None or isinstance(val, str), (
                f"Expected str|None for {key}, got {type(val)}"
            )

    def test_routing_entries_have_bounded_fields(self, tmp_path):
        queue, sid = _make_session(tmp_path)
        append_routing_debug_entry(
            queue,
            sid,
            route_status="test_status",
            dispatch_source="test_source",
            matched_early_exit=True,
            turn_index=3,
        )
        result = read_session_routing_debug(queue, sid)
        entry = result["entries"][0]
        expected_keys = {
            "route_status",
            "dispatch_source",
            "matched_early_exit",
            "turn_index",
            "timestamp_ms",
        }
        assert set(entry.keys()) == expected_keys

    def test_json_endpoint_is_json_serializable(self, tmp_path, monkeypatch):
        session = make_vera_session(monkeypatch, tmp_path)
        resp = session.client.get(f"/vera/debug/session.json?session_id={session.session_id}")
        assert resp.status_code == 200
        # Must be valid JSON
        data = resp.json()
        # Round-trip through json.dumps must work
        json.dumps(data)
