"""Comprehensive regression tests for Vera preview content truth handling.

Covers the full content pipeline for governed previews:

A. Referenced assistant-content binding (including "what you just said").
B. Same-turn generate-and-save binding.
C. Explicit literal content binding ("containing exactly: ...").
D. Active-draft content refresh.
E. Rename / save-as behavior.
F. Wrapper/status/control-text exclusion.
G. Streaming-finalization / shared-path confidence.
H. Empty-content fail-closed guard (at submit).
I. Active-preview content inspection ("Where is the content?").
J. Saveable artifact registry sanity.
K. Stale content prevention.

Architectural invariants verified:
    - Preview truth is authoritative before submit.
    - No empty write preview is submit-eligible without explicit empty-file intent.
    - No wrapper/status text is saved as authored content.
    - No fabricated content; no silent side effects.
"""

from __future__ import annotations

from pathlib import Path

from voxera.vera import session_store as vera_session_store
from voxera.vera.preview_drafting import maybe_draft_job_payload
from voxera.vera.preview_submission import (
    _is_explicit_empty_file_intent,
    submit_active_preview_for_session,
)
from voxera.vera.saveable_artifacts import (
    build_saveable_assistant_artifact,
    looks_like_non_authored_assistant_message,
    message_requests_referenced_content,
    select_recent_saveable_assistant_artifact,
)
from voxera.vera_web.chat_early_exit_dispatch import (
    dispatch_early_exit_intent,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _spacetime_answer() -> str:
    return (
        "Yes — in the standard cosmological picture every observer is always "
        "moving through spacetime at the speed of light c. In a local inertial "
        "frame your four-velocity has magnitude c, and the component along the "
        "time axis dominates when you are (spatially) at rest. As your spatial "
        "speed grows, the time-axis component shrinks — that geometric trade-off "
        "is what produces time dilation. Worldlines are tilted, not shortened."
    )


def _artifact(text: str) -> dict[str, str]:
    artifact = build_saveable_assistant_artifact(text)
    assert artifact is not None
    return artifact


# ---------------------------------------------------------------------------
# A. Referenced assistant-content binding
# ---------------------------------------------------------------------------


class TestReferencedAssistantContentBinding:
    """Section A: references like 'what you just said' must resolve to the
    latest meaningful assistant authored content and bind it into write_file."""

    def test_what_you_just_said_matches_reference_detection(self) -> None:
        msg = "Create a file called typed-smoke-test.txt containing exactly what you just said"
        assert message_requests_referenced_content(msg) is True

    def test_what_you_said_matches_reference_detection(self) -> None:
        assert message_requests_referenced_content("save what you said to a file") is True

    def test_your_last_answer_matches_reference_detection(self) -> None:
        assert message_requests_referenced_content("save your last answer as foo.txt") is True

    def test_your_previous_answer_matches_reference_detection(self) -> None:
        assert message_requests_referenced_content("save your previous answer to foo.txt") is True

    def test_the_previous_answer_matches_reference_detection(self) -> None:
        assert message_requests_referenced_content("save the previous answer as foo.txt") is True

    def test_that_explanation_matches_reference_detection(self) -> None:
        assert message_requests_referenced_content("save that explanation") is True

    def test_save_that_answer_matches_reference_detection(self) -> None:
        assert message_requests_referenced_content("save that answer as foo.txt") is True

    def test_put_that_in_a_file_matches_reference_detection(self) -> None:
        assert message_requests_referenced_content("put that in a file") is True

    def test_referenced_content_binds_to_preview(self) -> None:
        """The core regression: 'what you just said' must bind the spacetime
        answer into write_file.content — not leave it empty."""
        prior = _spacetime_answer()
        artifacts = [_artifact(prior)]
        msg = "Create a file called typed-smoke-test.txt containing exactly what you just said"
        payload = maybe_draft_job_payload(msg, recent_assistant_artifacts=artifacts)
        assert isinstance(payload, dict)
        wf = payload.get("write_file")
        assert isinstance(wf, dict)
        assert str(wf.get("path") or "").endswith("typed-smoke-test.txt")
        assert str(wf.get("content") or "").strip(), "content must not be empty"
        assert (
            "spacetime" in str(wf["content"]).lower() or "worldline" in str(wf["content"]).lower()
        )

    def test_save_last_answer_binds_latest_artifact(self) -> None:
        artifacts = [_artifact("First meaningful answer about python.")]
        payload = maybe_draft_job_payload(
            "save your last answer as notes.txt",
            recent_assistant_artifacts=artifacts,
        )
        assert isinstance(payload, dict)
        wf = payload["write_file"]
        assert isinstance(wf, dict)
        assert "python" in str(wf["content"]).lower()

    def test_reference_without_artifacts_fails_closed_empty(self) -> None:
        """When the user references 'what you just said' but no meaningful
        assistant artifact exists, the builder must not fabricate content
        into a governed write_file preview.  Higher-order note fallbacks
        may still run, but any resulting payload must not contain an empty
        write_file shell masquerading as authored content."""
        payload = maybe_draft_job_payload(
            "Create a file called x.txt containing exactly what you just said",
            recent_assistant_artifacts=[],
        )
        # Acceptable outcomes: no payload at all, or a note-like goal with no
        # write_file (higher-order fallback).  An empty write_file.content
        # governed preview is NOT acceptable — it would pose as authored.
        if payload is None:
            return
        wf = payload.get("write_file")
        if wf is None:
            return
        assert isinstance(wf, dict)
        content = str(wf.get("content") or "").strip()
        assert content, (
            "Builder must not emit an empty write_file preview when the user "
            "referenced prior assistant content but no artifact is available."
        )

    def test_save_that_answer_binds_latest_artifact(self) -> None:
        artifacts = [_artifact("A meaningful explanation of quantum mechanics.")]
        payload = maybe_draft_job_payload(
            "save that answer as qm.txt",
            recent_assistant_artifacts=artifacts,
        )
        assert isinstance(payload, dict)
        wf = payload["write_file"]
        assert isinstance(wf, dict)
        assert "quantum" in str(wf["content"]).lower()


# ---------------------------------------------------------------------------
# C. Explicit literal content binding
# ---------------------------------------------------------------------------


class TestExplicitLiteralContentBinding:
    def test_containing_exactly_colon_form_binds_literal(self) -> None:
        msg = "Create a file called typed-smoke-test.txt containing exactly: typed path works"
        payload = maybe_draft_job_payload(msg)
        assert isinstance(payload, dict)
        wf = payload["write_file"]
        assert isinstance(wf, dict)
        assert str(wf.get("content") or "").strip() == "typed path works"


# ---------------------------------------------------------------------------
# F. Wrapper/status/control-text exclusion
# ---------------------------------------------------------------------------


class TestWrapperExclusion:
    def test_preview_prepared_wrapper_is_non_authored(self) -> None:
        text = (
            "I’ve prepared a preview with this content. "
            "This is preview-only — nothing has been submitted yet. "
            "Let me know when you’d like to send it."
        )
        assert looks_like_non_authored_assistant_message(text) is True

    def test_draft_unchanged_is_non_authored(self) -> None:
        text = "I left the active draft content unchanged because the request was ambiguous."
        assert looks_like_non_authored_assistant_message(text) is True

    def test_job_completed_wrapper_is_non_authored(self) -> None:
        text = "Your linked write_file job completed successfully."
        assert looks_like_non_authored_assistant_message(text) is True

    def test_wrapper_text_not_saved_as_artifact(self) -> None:
        text = "I've prepared a preview with this content. Nothing has been submitted yet."
        assert build_saveable_assistant_artifact(text) is None

    def test_status_notification_not_saved_as_artifact(self) -> None:
        text = "I submitted the job to VoxeraOS. Job id: abc-123. The request is now in the queue."
        assert build_saveable_assistant_artifact(text) is None

    def test_wrapper_prior_then_reference_uses_latest_meaningful(self) -> None:
        """If the latest session artifact pool is [meaningful, wrapper],
        the non-authored filter would have already blocked the wrapper at
        append time, so the most-recent saveable artifact is still meaningful."""
        meaningful = _spacetime_answer()
        # The wrapper is filtered at build time — only meaningful artifact is
        # available to the resolver.
        artifact = build_saveable_assistant_artifact(
            "I’ve prepared a preview. This is preview-only — nothing has been submitted yet."
        )
        assert artifact is None
        meaningful_artifact = _artifact(meaningful)
        resolved = select_recent_saveable_assistant_artifact(
            message="save what you just said as foo.txt",
            assistant_artifacts=[meaningful_artifact],
        )
        assert isinstance(resolved, dict)
        assert (
            "spacetime" in str(resolved["content"]).lower()
            or "worldline" in str(resolved["content"]).lower()
        )


# ---------------------------------------------------------------------------
# H. Empty-content fail-closed guard at submit
# ---------------------------------------------------------------------------


class TestEmptyContentSubmitGuard:
    def test_empty_file_intent_detected(self) -> None:
        assert _is_explicit_empty_file_intent("create an empty file called x.txt") is True
        assert _is_explicit_empty_file_intent("touch x.txt") is True
        assert _is_explicit_empty_file_intent("make a blank file called x.txt") is True

    def test_non_empty_intent_not_detected(self) -> None:
        assert _is_explicit_empty_file_intent("write a file called x.txt with content") is False
        assert _is_explicit_empty_file_intent("save that answer") is False

    def test_submit_blocks_empty_content_without_empty_intent(self, tmp_path: Path) -> None:
        queue = tmp_path / "queue"
        session_id = "vera-test-empty-content"
        empty_preview = {
            "goal": "write a file called typed-smoke-test.txt with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/typed-smoke-test.txt",
                "content": "",
                "mode": "overwrite",
            },
        }
        vera_session_store.write_session_preview(queue, session_id, empty_preview)

        message, status = submit_active_preview_for_session(
            queue_root=queue,
            session_id=session_id,
            preview=empty_preview,
        )

        assert status == "handoff_empty_content_blocked"
        assert "empty" in message.lower()
        assert list((queue / "inbox").glob("inbox-*.json")) == []
        handoff = vera_session_store.read_session_handoff_state(queue, session_id)
        assert handoff is not None
        assert handoff["status"] == "empty_content_blocked"
        # Active preview must not be cleared by a blocked submit.
        assert vera_session_store.read_session_preview(queue, session_id) == empty_preview

    def test_submit_allows_empty_content_with_explicit_empty_intent(self, tmp_path: Path) -> None:
        queue = tmp_path / "queue"
        session_id = "vera-test-explicit-empty"
        preview = {
            "goal": "create an empty file called blank.txt",
            "write_file": {
                "path": "~/VoxeraOS/notes/blank.txt",
                "content": "",
                "mode": "overwrite",
            },
        }
        vera_session_store.write_session_preview(queue, session_id, preview)

        message, status = submit_active_preview_for_session(
            queue_root=queue,
            session_id=session_id,
            preview=preview,
        )

        assert status == "handoff_submitted"
        assert "I submitted the job to VoxeraOS" in message

    def test_submit_allows_non_empty_content(self, tmp_path: Path) -> None:
        queue = tmp_path / "queue"
        session_id = "vera-test-non-empty"
        preview = {
            "goal": "write a file called foo.txt with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/foo.txt",
                "content": "hello world",
                "mode": "overwrite",
            },
        }
        vera_session_store.write_session_preview(queue, session_id, preview)

        message, status = submit_active_preview_for_session(
            queue_root=queue,
            session_id=session_id,
            preview=preview,
        )

        assert status == "handoff_submitted"
        assert "hello world" not in message  # Ack message, not file content


# ---------------------------------------------------------------------------
# I. Active-preview content inspection
# ---------------------------------------------------------------------------


class TestActivePreviewContentInspection:
    """Section I: 'Where is the content?' / 'Show me the content' must be
    answered deterministically from the active preview — no vague LLM reply."""

    def _dispatch(
        self,
        *,
        message: str,
        queue_root: Path,
        session_id: str = "vera-inspection-test",
    ):
        return dispatch_early_exit_intent(
            message=message,
            diagnostics_service_turn=False,
            requested_job_id=None,
            should_attempt_derived_save=False,
            session_investigation=None,
            session_derived_output=None,
            queue_root=queue_root,
            session_id=session_id,
        )

    def test_where_is_the_content_with_non_empty_preview(self, tmp_path: Path) -> None:
        queue = tmp_path / "queue"
        session_id = "vera-inspection-nonempty"
        vera_session_store.write_session_preview(
            queue,
            session_id,
            {
                "goal": "write a file called typed-smoke-test.txt with provided content",
                "write_file": {
                    "path": "~/VoxeraOS/notes/typed-smoke-test.txt",
                    "content": _spacetime_answer(),
                    "mode": "overwrite",
                },
            },
        )

        result = self._dispatch(
            message="Where is the content?",
            queue_root=queue,
            session_id=session_id,
        )

        assert result.matched is True
        assert result.status == "ok:preview_content_inspection"
        assert "typed-smoke-test.txt" in result.assistant_text
        assert "spacetime" in result.assistant_text.lower() or "worldline" in (
            result.assistant_text.lower()
        )
        # Must NOT be the vague "unchanged" reply.
        assert "unchanged" not in result.assistant_text.lower()

    def test_where_is_the_content_with_empty_preview(self, tmp_path: Path) -> None:
        queue = tmp_path / "queue"
        session_id = "vera-inspection-empty"
        vera_session_store.write_session_preview(
            queue,
            session_id,
            {
                "goal": "write a file called typed-smoke-test.txt with provided content",
                "write_file": {
                    "path": "~/VoxeraOS/notes/typed-smoke-test.txt",
                    "content": "",
                    "mode": "overwrite",
                },
            },
        )

        result = self._dispatch(
            message="Where is the content?",
            queue_root=queue,
            session_id=session_id,
        )

        assert result.matched is True
        assert result.status == "ok:preview_content_inspection"
        assert "empty" in result.assistant_text.lower()
        # Explicit guidance that submission should be blocked until fixed.
        assert (
            "should not be submitted" in result.assistant_text.lower()
            or "before submitting" in result.assistant_text.lower()
            or "until the content" in result.assistant_text.lower()
        )

    def test_show_me_the_content_matches(self, tmp_path: Path) -> None:
        queue = tmp_path / "queue"
        session_id = "vera-inspection-show"
        vera_session_store.write_session_preview(
            queue,
            session_id,
            {
                "goal": "write a file called x.txt",
                "write_file": {
                    "path": "~/VoxeraOS/notes/x.txt",
                    "content": "hello world",
                    "mode": "overwrite",
                },
            },
        )

        result = self._dispatch(
            message="Show me the content",
            queue_root=queue,
            session_id=session_id,
        )

        assert result.matched is True
        assert result.status == "ok:preview_content_inspection"
        assert "hello world" in result.assistant_text

    def test_what_content_is_in_the_draft_matches(self, tmp_path: Path) -> None:
        queue = tmp_path / "queue"
        session_id = "vera-inspection-what-content"
        vera_session_store.write_session_preview(
            queue,
            session_id,
            {
                "goal": "write a file",
                "write_file": {
                    "path": "~/VoxeraOS/notes/q.txt",
                    "content": "some content",
                    "mode": "overwrite",
                },
            },
        )
        result = self._dispatch(
            message="What content is in the draft?",
            queue_root=queue,
            session_id=session_id,
        )
        assert result.matched is True
        assert "some content" in result.assistant_text

    def test_no_active_preview_returns_explicit_empty(self, tmp_path: Path) -> None:
        queue = tmp_path / "queue"
        result = self._dispatch(
            message="Show me the content",
            queue_root=queue,
            session_id="vera-inspection-none",
        )
        assert result.matched is True
        assert result.status == "ok:preview_content_inspection_empty"
        assert (
            "no active write preview" in result.assistant_text.lower()
            or "no active preview" in result.assistant_text.lower()
        )

    def test_content_inspection_truncates_long_content(self, tmp_path: Path) -> None:
        queue = tmp_path / "queue"
        session_id = "vera-inspection-long"
        long_content = "x" * 2000
        vera_session_store.write_session_preview(
            queue,
            session_id,
            {
                "goal": "write a file",
                "write_file": {
                    "path": "~/VoxeraOS/notes/long.txt",
                    "content": long_content,
                    "mode": "overwrite",
                },
            },
        )
        result = self._dispatch(
            message="Where is the content?",
            queue_root=queue,
            session_id=session_id,
        )
        assert result.matched is True
        assert "truncated" in result.assistant_text.lower()


# ---------------------------------------------------------------------------
# K. Stale content prevention
# ---------------------------------------------------------------------------


class TestStaleContentPrevention:
    def test_save_that_uses_latest_artifact_not_older(self) -> None:
        older = _artifact("An older answer about history.")
        newer = _artifact("A newer meaningful answer about physics today.")
        resolved = select_recent_saveable_assistant_artifact(
            message="save that answer to foo.txt",
            assistant_artifacts=[older, newer],
        )
        assert isinstance(resolved, dict)
        assert "physics" in str(resolved["content"]).lower()

    def test_save_that_with_only_wrapper_artifacts_is_safe(self) -> None:
        """Wrapper text is filtered at build time — resolver only ever
        sees meaningful artifacts."""
        wrapper_build = build_saveable_assistant_artifact(
            "I’ve prepared a preview with this content. "
            "This is preview-only — nothing has been submitted yet."
        )
        # Wrapper built-to-None means the artifact list stays empty of wrappers.
        assert wrapper_build is None
        resolved = select_recent_saveable_assistant_artifact(
            message="save that answer to foo.txt",
            assistant_artifacts=[],
        )
        assert resolved is None


# ---------------------------------------------------------------------------
# J. Saveable artifact registry sanity
# ---------------------------------------------------------------------------


class TestSaveableArtifactRegistry:
    def test_meaningful_content_registered(self) -> None:
        artifact = build_saveable_assistant_artifact(_spacetime_answer())
        assert artifact is not None
        assert artifact["content"]
        assert "artifact_type" in artifact

    def test_empty_content_rejected(self) -> None:
        assert build_saveable_assistant_artifact("") is None
        assert build_saveable_assistant_artifact("   ") is None

    def test_low_information_rejected(self) -> None:
        assert build_saveable_assistant_artifact("ok.") is None
        assert build_saveable_assistant_artifact("thanks!") is None

    def test_courtesy_rejected(self) -> None:
        assert build_saveable_assistant_artifact("You're welcome!") is None

    def test_preview_narration_rejected(self) -> None:
        text = (
            "I've prepared a preview with this content. "
            "This is preview-only — nothing has been submitted yet."
        )
        assert build_saveable_assistant_artifact(text) is None


# ---------------------------------------------------------------------------
# Shared-path (typed / voice-origin) content reference sanity
# ---------------------------------------------------------------------------


class TestSharedPathReferenceSanity:
    """Section G: typed and voice-origin chat must share the same content-
    binding behavior.  This test exercises the same deterministic helpers
    used by both code paths."""

    def test_voice_and_typed_references_resolve_identically(self) -> None:
        msg_typed = "save what you just said as notes.txt"
        msg_voice = "save what you just said as notes dot txt"
        # Both phrase forms must be detected as reference requests.
        assert message_requests_referenced_content(msg_typed) is True
        # Voice-normalized form still contains the reference phrase.
        assert message_requests_referenced_content(msg_voice) is True


# ---------------------------------------------------------------------------
# E. Rename / save-as preserves content (regression)
# ---------------------------------------------------------------------------


class TestRenamePreservesContent:
    """Rename / save-as must change the path but never drop authored content
    from the active preview.  Content-loss on rename would be a silent
    regression the user could not see until after submit."""

    def test_rename_preserves_nonempty_content(self) -> None:
        active = {
            "goal": "write a file called joke.txt with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/joke.txt",
                "content": "Why did the queue cross the road? To get to done.",
                "mode": "overwrite",
            },
        }
        payload = maybe_draft_job_payload(
            "rename it to joke-renamed.txt",
            active_preview=active,
        )
        assert isinstance(payload, dict)
        wf = payload.get("write_file")
        assert isinstance(wf, dict)
        assert str(wf.get("path") or "").endswith("joke-renamed.txt")
        assert str(wf.get("content") or "") == ("Why did the queue cross the road? To get to done.")

    def test_save_as_preserves_nonempty_content(self) -> None:
        active = {
            "goal": "write a file called draft.txt with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/draft.txt",
                "content": "Authored body stays intact.",
                "mode": "overwrite",
            },
        }
        payload = maybe_draft_job_payload(
            "save it as final.txt",
            active_preview=active,
        )
        assert isinstance(payload, dict)
        wf = payload.get("write_file")
        assert isinstance(wf, dict)
        assert str(wf.get("path") or "").endswith("final.txt")
        assert str(wf.get("content") or "") == "Authored body stays intact."


# ---------------------------------------------------------------------------
# D. Active-draft content refresh
# ---------------------------------------------------------------------------


class TestActiveDraftContentRefresh:
    """Section D: clear content-refresh requests on an active preview must
    replace content (path preserved); ambiguous requests must fail closed."""

    def test_different_joke_refresh_replaces_content(self) -> None:
        from voxera.vera.draft_revision import _is_clear_content_refresh_request

        assert _is_clear_content_refresh_request("tell me a different joke") is True
        assert _is_clear_content_refresh_request("generate a different poem") is True
        assert _is_clear_content_refresh_request("give me a shorter summary") is True

    def test_ambiguous_change_request_fails_closed(self) -> None:
        from voxera.vera.draft_revision import _is_ambiguous_change_request

        # Bare change/fix/improve without a content type → ambiguous.
        assert _is_ambiguous_change_request("change it") is True
        assert _is_ambiguous_change_request("fix it") is True
        assert _is_ambiguous_change_request("make it better") is True
        # With a specific content type → not ambiguous, has a clear target.
        assert _is_ambiguous_change_request("change the joke") is False

    def test_ambiguous_change_on_active_preview_returns_none(self) -> None:
        """'change it' on an active preview must not mutate anything —
        the builder returns None so response-shaping can produce the
        unchanged-with-reason reply."""
        active = {
            "goal": "write a file called draft.txt with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/draft.txt",
                "content": "Original authored body.",
                "mode": "overwrite",
            },
        }
        payload = maybe_draft_job_payload("change it", active_preview=active)
        assert payload is None


# ---------------------------------------------------------------------------
# H. Empty-content submit — additional empty-file intent coverage
# ---------------------------------------------------------------------------


class TestEmptyFileIntentVariants:
    """Explicit empty-file requests ('touch x.txt', 'create a blank file …')
    must be recognised by the guard so they can submit through.  Substring
    occurrences of empty/blank/touch in ordinary content goals must NOT
    bypass the guard."""

    def test_touch_intent_allowed(self) -> None:
        assert _is_explicit_empty_file_intent("touch placeholder.txt") is True

    def test_blank_intent_allowed(self) -> None:
        assert _is_explicit_empty_file_intent("make a blank file called x.txt") is True

    def test_empty_intent_allowed(self) -> None:
        assert _is_explicit_empty_file_intent("create an empty file called x.txt") is True

    def test_create_me_an_empty_file_allowed(self) -> None:
        assert _is_explicit_empty_file_intent("create me an empty file called x.txt") is True

    def test_zero_byte_intent_allowed(self) -> None:
        assert _is_explicit_empty_file_intent("create a zero-byte file called x.txt") is True

    def test_zero_space_byte_intent_allowed(self) -> None:
        assert _is_explicit_empty_file_intent("create a zero byte file called x.txt") is True

    def test_normal_content_intent_not_allowed(self) -> None:
        assert _is_explicit_empty_file_intent("write a file with a joke") is False
        assert _is_explicit_empty_file_intent("save that answer") is False

    # ── False-positive defenses (regression for over-permissive substring) ──

    def test_write_about_empty_set_not_allowed(self) -> None:
        """A content topic that contains the word 'empty' must not trigger
        the guard bypass — the user wants a file ABOUT empty sets, not an
        empty file."""
        assert _is_explicit_empty_file_intent("write a file about the empty set") is False

    def test_blank_slate_topic_not_allowed(self) -> None:
        assert _is_explicit_empty_file_intent("save a note on blank-slate theory") is False

    def test_touch_up_idiom_not_allowed(self) -> None:
        """'touch up that draft' is an editing idiom, not an empty-file create."""
        assert _is_explicit_empty_file_intent("touch up that draft") is False

    def test_empty_content_phrase_not_allowed(self) -> None:
        """A content goal that mentions 'empty content' (e.g. 'write a file
        with empty content') must not bypass — 'empty' is not adjacent to
        the file/note token in the create-empty-file shape."""
        assert _is_explicit_empty_file_intent("write me a file with empty content") is False

    def test_empty_field_phrase_not_allowed(self) -> None:
        assert _is_explicit_empty_file_intent("write a file with the empty field") is False


# ---------------------------------------------------------------------------
# F+J. Additional wrapper / status exclusion coverage
# ---------------------------------------------------------------------------


class TestAdditionalWrapperExclusion:
    """Additional wrapper / status / control phrasings must never bind as
    authored content."""

    def test_let_me_know_when_ready_is_non_authored(self) -> None:
        text = "Let me know when you're ready to save it."
        assert looks_like_non_authored_assistant_message(text) is True

    def test_nothing_submitted_is_non_authored(self) -> None:
        text = "Nothing has been submitted yet."
        assert looks_like_non_authored_assistant_message(text) is True

    def test_preview_only_is_non_authored(self) -> None:
        text = "This is preview-only — review or refine it."
        assert looks_like_non_authored_assistant_message(text) is True

    def test_i_submitted_job_is_non_authored(self) -> None:
        text = "I submitted the job to VoxeraOS. Job id: abc-123."
        assert looks_like_non_authored_assistant_message(text) is True

    def test_approval_status_is_non_authored(self) -> None:
        text = "approval status: awaiting operator review"
        assert looks_like_non_authored_assistant_message(text) is True


# ---------------------------------------------------------------------------
# I. Additional content-inspection phrasing
# ---------------------------------------------------------------------------


class TestAdditionalContentInspectionPhrasings:
    def _dispatch(self, *, message: str, queue_root: Path, session_id: str):
        return dispatch_early_exit_intent(
            message=message,
            diagnostics_service_turn=False,
            requested_job_id=None,
            should_attempt_derived_save=False,
            session_investigation=None,
            session_derived_output=None,
            queue_root=queue_root,
            session_id=session_id,
        )

    def test_what_are_you_going_to_write_matches(self, tmp_path: Path) -> None:
        queue = tmp_path / "queue"
        session_id = "vera-inspection-what-write"
        vera_session_store.write_session_preview(
            queue,
            session_id,
            {
                "goal": "write a file",
                "write_file": {
                    "path": "~/VoxeraOS/notes/note.txt",
                    "content": "authored body",
                    "mode": "overwrite",
                },
            },
        )
        result = self._dispatch(
            message="What are you going to write?",
            queue_root=queue,
            session_id=session_id,
        )
        assert result.matched is True
        assert result.status == "ok:preview_content_inspection"
        assert "authored body" in result.assistant_text

    def test_whats_in_the_preview_matches(self, tmp_path: Path) -> None:
        queue = tmp_path / "queue"
        session_id = "vera-inspection-whats-in"
        vera_session_store.write_session_preview(
            queue,
            session_id,
            {
                "goal": "write a file",
                "write_file": {
                    "path": "~/VoxeraOS/notes/note.txt",
                    "content": "body",
                    "mode": "overwrite",
                },
            },
        )
        result = self._dispatch(
            message="What's in the preview?",
            queue_root=queue,
            session_id=session_id,
        )
        assert result.matched is True
        assert result.status == "ok:preview_content_inspection"

    def test_plain_conversational_message_does_not_match_inspection(self, tmp_path: Path) -> None:
        """Regression: the inspection regex must be specific enough that
        normal conversational turns do not get hijacked into inspection."""
        queue = tmp_path / "queue"
        session_id = "vera-inspection-no-hijack"
        vera_session_store.write_session_preview(
            queue,
            session_id,
            {
                "goal": "write a file",
                "write_file": {
                    "path": "~/VoxeraOS/notes/note.txt",
                    "content": "body",
                    "mode": "overwrite",
                },
            },
        )
        # Ordinary conversational messages that should flow to normal orchestration.
        for msg in (
            "Hello Vera!",
            "What time is it?",
            "Tell me a joke.",
            "Good morning!",
        ):
            result = self._dispatch(
                message=msg,
                queue_root=queue,
                session_id=session_id,
            )
            assert not (
                result.matched is True
                and result.status
                in {
                    "ok:preview_content_inspection",
                    "ok:preview_content_inspection_empty",
                }
            ), f"Inspection must not hijack ordinary message: {msg!r}"

    def test_what_are_you_going_to_write_about_topic_does_not_hijack(self, tmp_path: Path) -> None:
        """Regression: 'what are you going to write about for the meeting?'
        must NOT route to the inspection handler — it's an open conversational
        question, not a draft-state inspection."""
        queue = tmp_path / "queue"
        session_id = "vera-inspection-write-about"
        vera_session_store.write_session_preview(
            queue,
            session_id,
            {
                "goal": "write a file",
                "write_file": {
                    "path": "~/VoxeraOS/notes/note.txt",
                    "content": "body",
                    "mode": "overwrite",
                },
            },
        )
        for msg in (
            "what are you going to write about for the meeting?",
            "what are you going to write next?",
            "What are you going to write in the speech?",
        ):
            result = self._dispatch(
                message=msg,
                queue_root=queue,
                session_id=session_id,
            )
            assert not (
                result.matched is True
                and result.status
                in {
                    "ok:preview_content_inspection",
                    "ok:preview_content_inspection_empty",
                }
            ), f"Inspection must not hijack open question: {msg!r}"

    def test_what_are_you_going_to_write_question_form_matches(self, tmp_path: Path) -> None:
        """The bounded form 'what are you going to write?' (and 'what are
        you going to write to the file?') must still route to inspection."""
        queue = tmp_path / "queue"
        session_id = "vera-inspection-write-bounded"
        vera_session_store.write_session_preview(
            queue,
            session_id,
            {
                "goal": "write a file",
                "write_file": {
                    "path": "~/VoxeraOS/notes/note.txt",
                    "content": "authored body",
                    "mode": "overwrite",
                },
            },
        )
        for msg in (
            "what are you going to write?",
            "What are you going to write to the file?",
            "what are you going to write to the note",
        ):
            result = self._dispatch(
                message=msg,
                queue_root=queue,
                session_id=session_id,
            )
            assert result.matched is True
            assert result.status == "ok:preview_content_inspection"


# ---------------------------------------------------------------------------
# Session-store integration — append_session_turn should register only
# meaningful assistant artifacts (filters wrappers & partial deltas).
# ---------------------------------------------------------------------------


class TestSessionTurnArtifactRegistration:
    def test_meaningful_assistant_turn_registered(self, tmp_path: Path) -> None:
        queue = tmp_path / "queue"
        session_id = "vera-session-register-meaningful"
        vera_session_store.append_session_turn(
            queue,
            session_id,
            role="assistant",
            text=_spacetime_answer(),
        )
        artifacts = vera_session_store.read_session_saveable_assistant_artifacts(queue, session_id)
        assert len(artifacts) == 1
        assert (
            "spacetime" in artifacts[0]["content"].lower()
            or "worldline" in artifacts[0]["content"].lower()
        )

    def test_wrapper_assistant_turn_not_registered(self, tmp_path: Path) -> None:
        queue = tmp_path / "queue"
        session_id = "vera-session-register-wrapper"
        vera_session_store.append_session_turn(
            queue,
            session_id,
            role="assistant",
            text=(
                "I've prepared a preview with this content. "
                "This is preview-only — nothing has been submitted yet."
            ),
        )
        artifacts = vera_session_store.read_session_saveable_assistant_artifacts(queue, session_id)
        assert artifacts == []

    def test_latest_meaningful_turn_wins_after_wrapper(self, tmp_path: Path) -> None:
        """Stale-content prevention: even if the latest raw assistant text is
        a wrapper, the registry only holds meaningful artifacts, so a later
        reference phrase resolves to the real prior answer."""
        queue = tmp_path / "queue"
        session_id = "vera-session-register-stale-safe"
        vera_session_store.append_session_turn(
            queue,
            session_id,
            role="assistant",
            text=_spacetime_answer(),
        )
        vera_session_store.append_session_turn(
            queue,
            session_id,
            role="assistant",
            text="I've prepared a preview. This is preview-only.",
        )
        artifacts = vera_session_store.read_session_saveable_assistant_artifacts(queue, session_id)
        assert len(artifacts) == 1
        content = artifacts[0]["content"].lower()
        assert "spacetime" in content or "worldline" in content
