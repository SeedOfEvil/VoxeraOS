"""Focused tests for the interactive first-run Vera walkthrough.

Coverage:
1. Tour request detection: positive matches and false-positive rejection.
2. Fresh session detection: appears in fresh state, suppressed otherwise.
3. Walkthrough start: creates preview + walkthrough state.
4. Walkthrough step advancement: each step updates the preview.
5. Walkthrough final step: does not auto-submit, lets user submit normally.
6. Tour hint on landing page: shown only for fresh sessions.
7. End-to-end via /chat: tour request → refinement steps → submit.
"""

from __future__ import annotations

import pytest

from voxera.vera.first_run_tour import (
    WALKTHROUGH_TOTAL_STEPS,
    advance_walkthrough,
    clear_walkthrough,
    is_first_run_tour_request,
    is_fresh_vera_session,
    is_walkthrough_active,
    start_walkthrough,
)
from voxera.vera.session_store import (
    read_session_preview,
    read_session_walkthrough,
)

# ---------------------------------------------------------------------------
# 1. Tour request detection
# ---------------------------------------------------------------------------


class TestTourRequestDetection:
    @pytest.mark.parametrize(
        "message",
        [
            "start Voxera tour",
            "Start the Voxera tour",
            "START VOXERA TOUR",
            "run the Voxera tour",
            "run the first-run tour",
            "run first run tour",
            "Voxera tour",
            "first-run tour",
            "first run tour",
        ],
    )
    def test_matches_tour_phrases(self, message: str) -> None:
        assert is_first_run_tour_request(message) is True

    @pytest.mark.parametrize(
        "message",
        [
            "hello",
            "what is voxera",
            "write me a note",
            "run diagnostics",
            "",
            "   ",
            "tour de france",
            "start the engine",
            # Must NOT match without Voxera/first-run anchor
            "start the tour of the codebase",
            "start the tour",
            "run the tour of duty",
        ],
    )
    def test_rejects_non_tour_phrases(self, message: str) -> None:
        assert is_first_run_tour_request(message) is False


# ---------------------------------------------------------------------------
# 2. Fresh session detection
# ---------------------------------------------------------------------------


class TestFreshSessionDetection:
    def test_fresh_with_empty_state(self) -> None:
        assert is_fresh_vera_session([], {}) is True

    def test_fresh_with_one_user_turn(self) -> None:
        turns = [{"role": "user", "text": "hello"}]
        assert is_fresh_vera_session(turns, {}) is True

    def test_suppressed_with_two_user_turns(self) -> None:
        turns = [
            {"role": "user", "text": "a"},
            {"role": "assistant", "text": "b"},
            {"role": "user", "text": "c"},
        ]
        assert is_fresh_vera_session(turns, {}) is False

    def test_suppressed_with_prior_job(self) -> None:
        assert is_fresh_vera_session([], {"last_submitted_job_ref": "j"}) is False

    def test_suppressed_with_prior_completion(self) -> None:
        assert is_fresh_vera_session([], {"last_completed_job_ref": "j"}) is False

    def test_suppressed_with_prior_review(self) -> None:
        assert is_fresh_vera_session([], {"last_reviewed_job_ref": "j"}) is False


# ---------------------------------------------------------------------------
# 3. Walkthrough start
# ---------------------------------------------------------------------------


class TestWalkthroughStart:
    def test_start_creates_preview_and_state(self, tmp_path) -> None:
        queue = tmp_path / "queue"
        text, status = start_walkthrough(queue, "s1")

        assert "preview" in text.lower()
        assert status == "walkthrough_step_0"

        # Preview was installed
        preview = read_session_preview(queue, "s1")
        assert preview is not None
        assert "write_file" in preview
        assert preview["write_file"]["path"].endswith(".md")

        # Walkthrough state was stored
        state = read_session_walkthrough(queue, "s1")
        assert state is not None
        assert state["step"] == 0
        assert state["active"] is True

    def test_start_text_includes_next_step_instruction(self, tmp_path) -> None:
        queue = tmp_path / "queue"
        text, _ = start_walkthrough(queue, "s1")
        # Must tell the user what to type next
        assert "type" in text.lower() or "Type" in text


# ---------------------------------------------------------------------------
# 4. Walkthrough step advancement
# ---------------------------------------------------------------------------


class TestWalkthroughAdvancement:
    def test_advance_moves_to_next_step(self, tmp_path) -> None:
        queue = tmp_path / "queue"
        start_walkthrough(queue, "s1")

        result = advance_walkthrough(queue, "s1")
        assert result is not None
        text, status = result
        assert status == "walkthrough_step_1"

        # Preview was updated
        preview = read_session_preview(queue, "s1")
        assert preview is not None

        # State was updated
        state = read_session_walkthrough(queue, "s1")
        assert state["step"] == 1

    def test_advance_through_all_steps(self, tmp_path) -> None:
        queue = tmp_path / "queue"
        start_walkthrough(queue, "s1")

        previews_seen: list[dict] = []
        for i in range(1, WALKTHROUGH_TOTAL_STEPS):
            result = advance_walkthrough(queue, "s1")
            assert result is not None, f"step {i} returned None"
            preview = read_session_preview(queue, "s1")
            previews_seen.append(preview)

        # All previews should be write_file previews
        for i, p in enumerate(previews_seen, start=1):
            assert "write_file" in p, f"step {i} preview missing write_file"

    def test_advance_returns_none_when_not_active(self, tmp_path) -> None:
        queue = tmp_path / "queue"
        assert advance_walkthrough(queue, "s1") is None

    def test_rename_step_changes_path(self, tmp_path) -> None:
        """Step 2 (rename) should change the file path in the preview."""
        queue = tmp_path / "queue"
        start_walkthrough(queue, "s1")
        path_0 = read_session_preview(queue, "s1")["write_file"]["path"]

        advance_walkthrough(queue, "s1")  # step 1: content refine
        advance_walkthrough(queue, "s1")  # step 2: rename
        path_2 = read_session_preview(queue, "s1")["write_file"]["path"]

        assert path_0 != path_2, "rename step should change the file path"
        assert "quick-start" in path_2.lower()


# ---------------------------------------------------------------------------
# 5. Final step does not auto-submit
# ---------------------------------------------------------------------------


class TestWalkthroughFinalStep:
    def test_advance_returns_none_after_final_step(self, tmp_path) -> None:
        """After all steps, advance returns None so submit flows through."""
        queue = tmp_path / "queue"
        start_walkthrough(queue, "s1")

        for _ in range(1, WALKTHROUGH_TOTAL_STEPS):
            advance_walkthrough(queue, "s1")

        # One more advance should return None
        assert advance_walkthrough(queue, "s1") is None

    def test_walkthrough_still_active_after_final_step(self, tmp_path) -> None:
        """Walkthrough state remains active until submit clears it."""
        queue = tmp_path / "queue"
        start_walkthrough(queue, "s1")
        for _ in range(1, WALKTHROUGH_TOTAL_STEPS):
            advance_walkthrough(queue, "s1")

        assert is_walkthrough_active(queue, "s1") is True

    def test_preview_exists_after_final_step(self, tmp_path) -> None:
        """Preview must still exist — user hasn't submitted yet."""
        queue = tmp_path / "queue"
        start_walkthrough(queue, "s1")
        for _ in range(1, WALKTHROUGH_TOTAL_STEPS):
            advance_walkthrough(queue, "s1")

        preview = read_session_preview(queue, "s1")
        assert preview is not None
        assert "write_file" in preview

    def test_clear_walkthrough_removes_state(self, tmp_path) -> None:
        queue = tmp_path / "queue"
        start_walkthrough(queue, "s1")
        assert is_walkthrough_active(queue, "s1") is True

        clear_walkthrough(queue, "s1")
        assert is_walkthrough_active(queue, "s1") is False


# ---------------------------------------------------------------------------
# 6. Tour hint on landing page
# ---------------------------------------------------------------------------


class TestTourHintRendering:
    def test_fresh_session_landing_page_includes_tour_hint(self, monkeypatch, tmp_path) -> None:
        from fastapi.testclient import TestClient

        from voxera.vera_web import app as vera_app_module

        from .vera_session_helpers import set_vera_queue_root

        queue = tmp_path / "queue"
        set_vera_queue_root(monkeypatch, queue)
        client = TestClient(vera_app_module.app)

        res = client.get("/")
        assert res.status_code == 200
        assert "start Voxera tour" in res.text

    def test_hint_suppressed_after_chat_turn(self, monkeypatch, tmp_path) -> None:
        from .vera_session_helpers import make_vera_session

        harness = make_vera_session(monkeypatch, tmp_path)
        resp = harness.chat("hello")
        assert resp.status_code == 200
        # After chat, turns exist — guidance (and hint) are hidden
        assert "empty-tour-hint" not in resp.text

    def test_guidance_tour_hint_conditional_on_flag(self) -> None:
        from voxera.vera_web.app import _main_screen_guidance

        assert "tour_hint" in _main_screen_guidance(show_tour_hint=True)
        assert "tour_hint" not in _main_screen_guidance(show_tour_hint=False)

    def test_hint_not_in_persisted_assistant_turns(self, monkeypatch, tmp_path) -> None:
        from .vera_session_helpers import make_vera_session

        harness = make_vera_session(monkeypatch, tmp_path)
        harness.chat("hello")
        for turn in harness.turns():
            assert "start Voxera tour" not in str(turn.get("text", ""))


# ---------------------------------------------------------------------------
# 7. End-to-end via /chat
# ---------------------------------------------------------------------------


class TestWalkthroughEndToEnd:
    def test_tour_request_creates_preview_via_chat(self, monkeypatch, tmp_path) -> None:
        from .vera_session_helpers import make_vera_session

        harness = make_vera_session(monkeypatch, tmp_path)
        resp = harness.chat("start Voxera tour")
        assert resp.status_code == 200

        # Preview must exist
        preview = harness.preview()
        assert preview is not None
        assert "write_file" in preview
        assert preview["write_file"]["path"].endswith(".md")

    def test_refinement_steps_update_preview(self, monkeypatch, tmp_path) -> None:
        from .vera_session_helpers import make_vera_session

        harness = make_vera_session(monkeypatch, tmp_path)
        harness.chat("start Voxera tour")
        initial_preview = harness.preview()

        # Step 1: content refinement
        harness.chat("Change the content to something shorter")
        step1_preview = harness.preview()
        assert step1_preview is not None
        assert step1_preview != initial_preview

        # Step 2: rename
        harness.chat("Rename it to voxera-quick-start.md")
        step2_preview = harness.preview()
        assert step2_preview is not None
        assert step2_preview["write_file"]["path"] != initial_preview["write_file"]["path"]

    def test_submit_after_walkthrough_uses_governed_path(self, monkeypatch, tmp_path) -> None:
        from voxera.vera.session_store import read_session_context

        from .vera_session_helpers import make_vera_session

        harness = make_vera_session(monkeypatch, tmp_path)

        # Walk through all steps
        harness.chat("start Voxera tour")
        harness.chat("make it shorter")
        harness.chat("rename it")
        harness.chat("add a line")

        # Final step: submit
        resp = harness.chat("submit it")
        assert resp.status_code == 200

        # Preview should be cleared (submit consumes it)
        assert harness.preview() is None

        # Context should show a submitted job
        ctx = read_session_context(harness.queue, harness.session_id)
        assert ctx.get("last_submitted_job_ref") is not None

    def test_walkthrough_cleared_after_submit(self, monkeypatch, tmp_path) -> None:
        from .vera_session_helpers import make_vera_session

        harness = make_vera_session(monkeypatch, tmp_path)
        harness.chat("start Voxera tour")
        harness.chat("step 1")
        harness.chat("step 2")
        harness.chat("step 3")
        harness.chat("submit it")

        # Walkthrough state must be cleared
        state = read_session_walkthrough(harness.queue, harness.session_id)
        assert state is None

    def test_non_tour_message_does_not_trigger_walkthrough(self, monkeypatch, tmp_path) -> None:
        from .vera_session_helpers import make_vera_session

        harness = make_vera_session(monkeypatch, tmp_path)
        harness.chat("hello")

        state = read_session_walkthrough(harness.queue, harness.session_id)
        assert state is None

    def test_third_refinement_advances_to_final_step_not_restart(
        self, monkeypatch, tmp_path
    ) -> None:
        """Regression: message containing 'Voxera tour' during active walkthrough
        must advance to step 3, not restart from step 0."""
        from .vera_session_helpers import make_vera_session

        harness = make_vera_session(monkeypatch, tmp_path)
        harness.chat("start Voxera tour")
        harness.chat("Change the content to something shorter and more casual.")
        harness.chat("Rename it to voxera-quick-start.md.")

        # This message contains "Voxera tour" — must NOT restart the walkthrough
        harness.chat("Add a final line saying this note was created during the Voxera tour.")

        state = read_session_walkthrough(harness.queue, harness.session_id)
        assert state is not None
        assert state["step"] == 3, f"expected step 3 (final edit), got {state['step']}"

        # Preview must retain the renamed path, not revert to the initial one
        preview = harness.preview()
        assert preview is not None
        assert "quick-start" in preview["write_file"]["path"]

    def test_full_guided_sequence_with_exact_prompts(self, monkeypatch, tmp_path) -> None:
        """Full walkthrough using the exact prompts Vera instructs the user to type."""
        from voxera.vera.session_store import read_session_context

        from .vera_session_helpers import make_vera_session

        harness = make_vera_session(monkeypatch, tmp_path)

        # Step 0 — start
        harness.chat("start Voxera tour")
        assert read_session_walkthrough(harness.queue, harness.session_id)["step"] == 0

        # Step 1 — content refinement (exact prompt from step 0 text)
        harness.chat("Change the content to something shorter and more casual.")
        assert read_session_walkthrough(harness.queue, harness.session_id)["step"] == 1

        # Step 2 — rename (exact prompt from step 1 text)
        harness.chat("Rename it to voxera-quick-start.md.")
        state = read_session_walkthrough(harness.queue, harness.session_id)
        assert state["step"] == 2
        assert "quick-start" in harness.preview()["write_file"]["path"]

        # Step 3 — final edit (exact prompt from step 2 text, contains "Voxera tour")
        harness.chat("Add a final line saying this note was created during the Voxera tour.")
        state = read_session_walkthrough(harness.queue, harness.session_id)
        assert state["step"] == 3

        # Final submit — preview consumed, walkthrough cleared, job submitted
        resp = harness.chat("submit it")
        assert resp.status_code == 200
        assert harness.preview() is None
        assert read_session_walkthrough(harness.queue, harness.session_id) is None
        ctx = read_session_context(harness.queue, harness.session_id)
        assert ctx.get("last_submitted_job_ref") is not None

    def test_active_walkthrough_blocks_tour_restart(self, monkeypatch, tmp_path) -> None:
        """Explicitly typing 'start Voxera tour' mid-walkthrough must advance,
        not restart."""
        from .vera_session_helpers import make_vera_session

        harness = make_vera_session(monkeypatch, tmp_path)
        harness.chat("start Voxera tour")
        assert read_session_walkthrough(harness.queue, harness.session_id)["step"] == 0

        # Typing the trigger phrase again should advance to step 1, not restart
        harness.chat("start Voxera tour")
        state = read_session_walkthrough(harness.queue, harness.session_id)
        assert state["step"] == 1, f"expected step 1, got {state['step']}"
