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


# ---------------------------------------------------------------------------
# L. Active-preview append / expand (additive follow-ups)
# ---------------------------------------------------------------------------


class TestActivePreviewExpandDetection:
    """Section D: 'add N more X', 'append', 'continue the list' must be
    detected as expand intents when an active preview exists.  Ambiguous
    'add it' / 'add that' must NOT match."""

    def test_add_n_more_items_matches(self) -> None:
        from voxera.vera.draft_revision import is_active_preview_content_expand_request

        assert is_active_preview_content_expand_request("add 10 more jokes to the list") is True
        assert is_active_preview_content_expand_request("add 10 more jokes") is True
        assert is_active_preview_content_expand_request("append 5 more bullets") is True
        assert (
            is_active_preview_content_expand_request("include 3 more examples to the content")
            is True
        )

    def test_word_count_matches(self) -> None:
        from voxera.vera.draft_revision import is_active_preview_content_expand_request

        assert is_active_preview_content_expand_request("add ten more jokes") is True
        assert is_active_preview_content_expand_request("add a few more examples") is True
        assert is_active_preview_content_expand_request("add several more bullets") is True

    def test_typo_variant_matches(self) -> None:
        from voxera.vera.draft_revision import is_active_preview_content_expand_request

        # 'jokees' / 'jokeys' — common voice/typed typos.
        assert is_active_preview_content_expand_request("add 10 more jokees") is True
        assert is_active_preview_content_expand_request("add 5 more jokeys") is True

    def test_continue_list_matches(self) -> None:
        from voxera.vera.draft_revision import is_active_preview_content_expand_request

        assert is_active_preview_content_expand_request("continue the list") is True
        assert is_active_preview_content_expand_request("continue the note") is True
        assert is_active_preview_content_expand_request("continue writing") is True

    def test_expand_it_matches(self) -> None:
        from voxera.vera.draft_revision import is_active_preview_content_expand_request

        assert is_active_preview_content_expand_request("expand it with more examples") is True
        assert is_active_preview_content_expand_request("expand the list") is True
        assert is_active_preview_content_expand_request("expand the content") is True

    def test_make_it_longer_matches(self) -> None:
        from voxera.vera.draft_revision import is_active_preview_content_expand_request

        assert is_active_preview_content_expand_request("make it longer") is True
        assert is_active_preview_content_expand_request("make that longer") is True

    def test_add_more_without_count_matches(self) -> None:
        from voxera.vera.draft_revision import is_active_preview_content_expand_request

        assert is_active_preview_content_expand_request("add more jokes") is True
        assert is_active_preview_content_expand_request("append more items") is True

    def test_ambiguous_add_does_not_match(self) -> None:
        from voxera.vera.draft_revision import is_active_preview_content_expand_request

        # Bare ambiguous adds — no countable item, no clear expand target.
        assert is_active_preview_content_expand_request("add it") is False
        assert is_active_preview_content_expand_request("add that") is False
        assert is_active_preview_content_expand_request("add a comment") is False
        # Rename / save-as family must not trigger expand.
        assert is_active_preview_content_expand_request("rename it to final.txt") is False
        assert is_active_preview_content_expand_request("save it as final.txt") is False


class TestExtractExpandRequestedCount:
    """Section D: numeric counts should be parseable so response shaping
    never overclaims what the user requested."""

    def test_digit_count_returns_value(self) -> None:
        from voxera.vera.draft_revision import extract_expand_requested_count

        assert extract_expand_requested_count("add 10 more jokes") == 10
        assert extract_expand_requested_count("append 5 more bullets") == 5
        assert extract_expand_requested_count("include 3 more examples") == 3

    def test_word_count_returns_value(self) -> None:
        from voxera.vera.draft_revision import extract_expand_requested_count

        assert extract_expand_requested_count("add ten more jokes") == 10
        assert extract_expand_requested_count("add three more bullets") == 3

    def test_no_count_returns_none(self) -> None:
        from voxera.vera.draft_revision import extract_expand_requested_count

        assert extract_expand_requested_count("add more jokes") is None
        assert extract_expand_requested_count("continue the list") is None

    def test_oversized_count_returns_none(self) -> None:
        """Bound the count to a safe 1..100 range."""
        from voxera.vera.draft_revision import extract_expand_requested_count

        assert extract_expand_requested_count("add 9999 more jokes") is None
        assert extract_expand_requested_count("add 0 more jokes") is None


class TestActivePreviewAppendBinding:
    """Section L: when an expand request arrives with a refinable active
    preview and the LLM reply contains authored text, the binding layer
    must APPEND the new text to the existing content (preserving path)."""

    def _default_kwargs(self) -> dict:
        return {
            "message": "add 10 more jokes to the list",
            "reply_code_content": None,
            "reply_text_draft": None,
            "reply_status": "ok",
            "builder_payload": None,
            "pending_preview": None,
            "is_code_draft_turn": False,
            "is_writing_draft_turn": False,
            "is_explicit_writing_transform": False,
            "informational_web_turn": False,
            "is_enrichment_turn": False,
            "explicit_targeted_content_refinement": False,
            "active_preview_is_refinable_prose": False,
            "conversational_answer_first_turn": False,
            "active_session": "test-session-expand",
        }

    def _active_preview_with_ten_jokes(self) -> dict:
        return {
            "goal": "write a file called jokiez.txt with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/jokiez.txt",
                "content": (
                    "1. Why did the scarecrow win an award? He was outstanding in his field.\n"
                    "2. I told my wife she was drawing her eyebrows too high. She looked surprised.\n"
                    "3. Why don't scientists trust atoms? They make up everything.\n"
                    "4. What do you call a fish without eyes? A fsh.\n"
                    "5. Why did the programmer quit? He didn't get arrays.\n"
                    "6. I'm reading a book on anti-gravity; it's impossible to put down.\n"
                    "7. Parallel lines have so much in common. It's a shame they'll never meet.\n"
                    "8. I would avoid sushi if I were you. It's a little fishy.\n"
                    "9. How does a penguin build its house? Igloos it together.\n"
                    "10. Why did the bicycle fall over? It was two tired."
                ),
                "mode": "overwrite",
            },
        }

    def test_append_succeeds_when_llm_provides_text(self) -> None:
        from voxera.vera_web.draft_content_binding import resolve_draft_content_binding

        kwargs = self._default_kwargs()
        kwargs["pending_preview"] = self._active_preview_with_ten_jokes()
        kwargs["active_preview_is_refinable_prose"] = True
        new_jokes = (
            "11. Why did the coffee file a police report? It got mugged.\n"
            "12. What do you call cheese that isn't yours? Nacho cheese."
        )
        kwargs["reply_text_draft"] = new_jokes
        result = resolve_draft_content_binding(**kwargs)
        assert result.preview_needs_write is True
        assert result.builder_payload is not None
        wf = result.builder_payload["write_file"]
        # Path preserved.
        assert wf["path"] == "~/VoxeraOS/notes/jokiez.txt"
        # Existing 10 jokes are still in content.
        assert "Why did the scarecrow" in wf["content"]
        # New jokes appended.
        assert "coffee file a police report" in wf["content"]
        assert "Nacho cheese" in wf["content"]
        # Fail-closed flag NOT set.
        assert result.generation_content_refresh_failed_closed is False

    def test_append_path_preserved(self) -> None:
        from voxera.vera_web.draft_content_binding import resolve_draft_content_binding

        kwargs = self._default_kwargs()
        pending = self._active_preview_with_ten_jokes()
        kwargs["pending_preview"] = pending
        kwargs["active_preview_is_refinable_prose"] = True
        kwargs["reply_text_draft"] = "11. A new joke."
        result = resolve_draft_content_binding(**kwargs)
        assert result.builder_payload is not None
        wf = result.builder_payload["write_file"]
        # Path MUST be preserved — expand never renames.
        assert wf["path"] == pending["write_file"]["path"]

    def test_append_fails_closed_when_llm_gives_no_text(self) -> None:
        from voxera.vera_web.draft_content_binding import resolve_draft_content_binding

        kwargs = self._default_kwargs()
        kwargs["pending_preview"] = self._active_preview_with_ten_jokes()
        kwargs["active_preview_is_refinable_prose"] = True
        kwargs["reply_text_draft"] = None  # LLM produced no authored text
        result = resolve_draft_content_binding(**kwargs)
        # Preview NOT updated.
        assert result.preview_needs_write is False
        assert result.builder_payload is None
        # Fail-closed flag set so response shaping replaces the LLM claim.
        assert result.generation_content_refresh_failed_closed is True

    def test_append_fails_closed_when_llm_emits_wrapper_text(self) -> None:
        """If the LLM reply is pure wrapper/status narration, the binding
        layer must reject it rather than writing wrapper text into the file."""
        from voxera.vera_web.draft_content_binding import resolve_draft_content_binding

        kwargs = self._default_kwargs()
        kwargs["pending_preview"] = self._active_preview_with_ten_jokes()
        kwargs["active_preview_is_refinable_prose"] = True
        kwargs["reply_text_draft"] = (
            "I've prepared a preview with this content. "
            "This is preview-only — nothing has been submitted yet."
        )
        result = resolve_draft_content_binding(**kwargs)
        assert result.preview_needs_write is False
        assert result.builder_payload is None
        assert result.generation_content_refresh_failed_closed is True

    def test_append_dedupes_when_llm_includes_full_existing_content(self) -> None:
        """If the LLM returned the full new content (existing + additions)
        instead of just the additions, the binding must detect that and use
        the LLM reply as-is (REPLACE) to avoid doubling the existing body."""
        from voxera.vera_web.draft_content_binding import resolve_draft_content_binding

        kwargs = self._default_kwargs()
        pending = self._active_preview_with_ten_jokes()
        kwargs["pending_preview"] = pending
        kwargs["active_preview_is_refinable_prose"] = True
        # LLM replied with full new list including original jokes.
        full_reply = pending["write_file"]["content"] + (
            "\n11. Why did the coffee file a police report? It got mugged."
        )
        kwargs["reply_text_draft"] = full_reply
        result = resolve_draft_content_binding(**kwargs)
        assert result.builder_payload is not None
        wf = result.builder_payload["write_file"]
        # Original content appears EXACTLY ONCE (no doubling).
        scarecrow_count = wf["content"].count("Why did the scarecrow")
        assert scarecrow_count == 1, (
            f"scarecrow joke appears {scarecrow_count} times — expected 1 (no doubling)"
        )
        assert "coffee file a police report" in wf["content"]


class TestResponseShapingFalseSuccessClaimReplacement:
    """Section C: when generation_content_refresh_failed_closed is True AND
    the LLM reply asserts a false success claim ('I've added 20 jokes',
    'appended 5 bullets', 'this brings the total to 30'), response shaping
    must REPLACE the LLM text with an honest 'draft unchanged' message —
    never leak the false count to the user."""

    def test_false_added_claim_gets_replaced(self) -> None:
        from voxera.vera_web.response_shaping import assemble_assistant_reply

        pending = {
            "goal": "write a file called jokiez.txt with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/jokiez.txt",
                "content": "1. Joke one.\n2. Joke two.",
                "mode": "overwrite",
            },
        }
        result = assemble_assistant_reply(
            "I've added 20 additional jokes to the content of your note. "
            "This brings the total list to 30.",
            message="add 10 more jokes to the list",
            pending_preview=pending,
            builder_payload=None,
            in_voxera_preview_flow=False,
            is_code_draft_turn=False,
            is_writing_draft_turn=False,
            is_enrichment_turn=False,
            conversational_answer_first_turn=False,
            is_json_content_request=False,
            is_voxera_control_turn=False,
            explicit_targeted_content_refinement=False,
            preview_update_rejected=False,
            generation_content_refresh_failed_closed=True,
            reply_status="ok:test",
        )
        # Primary invariant: the false count claim must not reach the user.
        assert "20 additional jokes" not in result.assistant_text
        assert "total list to 30" not in result.assistant_text
        # Message must honestly indicate the draft is unchanged / not mutated.
        lowered = result.assistant_text.lower()
        assert "unchanged" in lowered or "could not safely update" in lowered

    def test_neutral_llm_reply_still_appends_fail_closed_note(self) -> None:
        """Preserve existing behavior for neutral LLM replies — the honest
        note is appended rather than replacing the whole text."""
        from voxera.vera_web.response_shaping import assemble_assistant_reply

        pending = {
            "goal": "write a file called note.txt with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/note.txt",
                "content": "Existing body.",
                "mode": "overwrite",
            },
        }
        # Message intentionally neutral — does not trigger rename, refresh,
        # or ambiguous-change branches so we isolate the generation-failed
        # response-shaping behavior.
        result = assemble_assistant_reply(
            "Let me think about that.",
            message="hmm ok",
            pending_preview=pending,
            builder_payload=None,
            in_voxera_preview_flow=False,
            is_code_draft_turn=False,
            is_writing_draft_turn=False,
            is_enrichment_turn=False,
            conversational_answer_first_turn=False,
            is_json_content_request=False,
            is_voxera_control_turn=False,
            explicit_targeted_content_refinement=False,
            preview_update_rejected=False,
            generation_content_refresh_failed_closed=True,
            reply_status="ok:test",
        )
        # Neutral text preserved, honest note appended.
        assert "Let me think about that" in result.assistant_text
        assert "left the active draft content unchanged" in result.assistant_text

    def test_preview_update_claim_catches_numeric_added_form(self) -> None:
        from voxera.vera_web.conversational_checklist import looks_like_preview_update_claim

        assert looks_like_preview_update_claim("I've added 20 additional jokes") is True
        assert looks_like_preview_update_claim("Appended 5 more bullets") is True
        assert looks_like_preview_update_claim("This brings the total to 30") is True

    def test_preview_update_claim_catches_list_append_phrases(self) -> None:
        from voxera.vera_web.conversational_checklist import looks_like_preview_update_claim

        assert looks_like_preview_update_claim("Added to the list") is True
        assert looks_like_preview_update_claim("Expanded the content") is True
        assert looks_like_preview_update_claim("Extended the draft") is True

    def test_preview_update_claim_does_not_catch_ordinary_speech(self) -> None:
        """Guard against over-matching — innocent uses of 'added' must not
        fire the claim detector."""
        from voxera.vera_web.conversational_checklist import looks_like_preview_update_claim

        # "I added a link" is natural conversation, not a preview update claim.
        assert looks_like_preview_update_claim("I added a link in my message.") is False
        assert looks_like_preview_update_claim("Thanks for the info.") is False


# ---------------------------------------------------------------------------
# M. Phrase-variant coverage for active-preview content edits
# ---------------------------------------------------------------------------


class TestActivePreviewExpandPhraseVariants:
    """The user reported these exact phrasings landing on a 'draft unchanged'
    reply.  Each must be detected as an expand intent (so the binding layer
    has a chance to mutate or fail closed honestly), and the false-claim
    detector must catch the LLM's verbatim narration even with adjective
    fillers like 'dad jokes'."""

    def test_polite_prefix_with_adjective_phrase_matches(self) -> None:
        from voxera.vera.draft_revision import is_active_preview_content_expand_request

        for phrase in (
            "add 10 more jokes",
            "add 10 more jokes to the content",
            "please add 10 more dad jokes to the list",
            "please add 10 more dad jokes to the note content",
            "add 5 more bullet points to the list",
            "append 3 more good examples",
        ):
            assert is_active_preview_content_expand_request(phrase) is True, (
                f"expand intent must match: {phrase!r}"
            )

    def test_added_with_adjective_filler_caught_as_claim(self) -> None:
        from voxera.vera_web.conversational_checklist import looks_like_preview_update_claim

        for claim in (
            "I've added 10 more dad jokes to the list.",
            "I added 5 more good examples.",
            "I've appended 3 more bullet points.",
            "Added 10 more dad jokes to the note.",
        ):
            assert looks_like_preview_update_claim(claim) is True, (
                f"false-claim detector must catch: {claim!r}"
            )


class TestActivePreviewExpandIntegration:
    """End-to-end through the FastAPI test client.  These are the closest
    proxy to the real browser flow that exposed the regression."""

    @staticmethod
    def _set_queue_root(monkeypatch, queue):
        from types import SimpleNamespace

        from voxera.vera_web import app as vera_app_module

        monkeypatch.setattr(
            vera_app_module,
            "load_runtime_config",
            lambda: SimpleNamespace(queue_root=queue),
        )

    @staticmethod
    def _initial_jokes() -> str:
        return (
            "1. Why did the scarecrow win an award? He was outstanding in his field.\n"
            "2. Why don't scientists trust atoms? They make up everything.\n"
            "3. What do you call a fish without eyes? A fsh.\n"
            "4. I told my wife she was drawing her eyebrows too high. She looked surprised.\n"
            "5. Parallel lines have so much in common. It's a shame they'll never meet.\n"
            "6. I'm reading a book on anti-gravity; it's impossible to put down.\n"
            "7. Why did the bicycle fall over? It was two tired.\n"
            "8. How does a penguin build its house? Igloos it together.\n"
            "9. I would avoid sushi if I were you. It's a little fishy.\n"
            "10. Why did the programmer quit? He didn't get arrays."
        )

    def _install_jokekster_preview(self, queue, sid):
        initial = {
            "goal": "write a file called jokekster.txt with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/jokekster.txt",
                "content": self._initial_jokes(),
                "mode": "overwrite",
            },
        }
        vera_session_store.write_session_preview(queue, sid, initial)
        return initial

    def test_full_body_llm_reply_appends_to_preview(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from voxera.vera_web import app as vera_app_module

        queue = tmp_path / "queue"
        self._set_queue_root(monkeypatch, queue)
        client = TestClient(vera_app_module.app)
        client.get("/")
        sid = client.cookies.get("vera_session_id") or ""
        self._install_jokekster_preview(queue, sid)

        async def _fake_reply(*, turns, user_message, **_kw):
            return {
                "answer": (
                    "Here are 10 more dad jokes:\n\n"
                    "11. Why don't skeletons fight each other? They don't have the guts.\n"
                    "12. What's brown and sticky? A stick.\n"
                    "13. I'm afraid for the calendar. Its days are numbered."
                ),
                "status": "ok:test",
            }

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
        res = client.post("/chat", data={"session_id": sid, "message": "add 10 more jokes"})
        assert res.status_code == 200

        final_preview = vera_session_store.read_session_preview(queue, sid)
        assert isinstance(final_preview, dict)
        final_wf = final_preview["write_file"]
        # Path preserved.
        assert final_wf["path"] == "~/VoxeraOS/notes/jokekster.txt"
        # Content grew.
        assert len(final_wf["content"]) > len(self._initial_jokes())
        # Original jokes still present.
        assert "scarecrow" in final_wf["content"]
        # New jokes appended.
        assert "skeletons fight" in final_wf["content"]

    def test_pure_claim_llm_reply_fails_closed_honestly(self, tmp_path, monkeypatch):
        """When the LLM only asserts 'I've added 10 more dad jokes' without
        any actual joke text, the binding must reject it (no false content),
        the preview must stay unchanged, and the user must see an honest
        'draft unchanged' reply (not the false count claim)."""
        from fastapi.testclient import TestClient

        from voxera.vera_web import app as vera_app_module

        queue = tmp_path / "queue"
        self._set_queue_root(monkeypatch, queue)
        client = TestClient(vera_app_module.app)
        client.get("/")
        sid = client.cookies.get("vera_session_id") or ""
        self._install_jokekster_preview(queue, sid)

        async def _fake_reply(*, turns, user_message, **_kw):
            return {
                "answer": "I've added 10 more dad jokes to the list.",
                "status": "ok:test",
            }

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
        res = client.post(
            "/chat",
            data={"session_id": sid, "message": "please add 10 more dad jokes to the list"},
        )
        assert res.status_code == 200

        # Content unchanged.
        final_preview = vera_session_store.read_session_preview(queue, sid)
        assert isinstance(final_preview, dict)
        assert final_preview["write_file"]["content"] == self._initial_jokes()

        # Honest fail-closed surface.
        body = res.text.lower()
        assert (
            "could not safely update" in body
            or "draft is still in the preview, unchanged" in body
            or "left the active draft content unchanged" in body
        )
        # False count claim must not leak verbatim.
        assert "added 10 more dad jokes" not in body

    def test_phrase_variants_all_route_to_expand_path(self, tmp_path, monkeypatch):
        """Each user-reported phrasing must route to the expand binding path
        rather than falling through to a generic 'unchanged' reply with no
        binding attempt."""
        from fastapi.testclient import TestClient

        from voxera.vera_web import app as vera_app_module

        queue = tmp_path / "queue"
        self._set_queue_root(monkeypatch, queue)
        client = TestClient(vera_app_module.app)
        client.get("/")
        sid = client.cookies.get("vera_session_id") or ""

        async def _fake_reply(*, turns, user_message, **_kw):
            return {
                "answer": "11. A new joke about queues.\n12. Another joke about caches.",
                "status": "ok:test",
            }

        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)

        for phrase in (
            "add 10 more jokes",
            "add 10 more jokes to the content",
            "please add 10 more dad jokes to the list",
            "please add 10 more dad jokes to the note content",
        ):
            # Reset preview each iteration so we can verify each phrase
            # independently produces a content mutation.
            self._install_jokekster_preview(queue, sid)
            res = client.post("/chat", data={"session_id": sid, "message": phrase})
            assert res.status_code == 200
            final = vera_session_store.read_session_preview(queue, sid)
            assert isinstance(final, dict)
            content = str(final["write_file"]["content"])
            assert "queue" in content.lower() or "cache" in content.lower(), (
                f"phrase {phrase!r} did not route to the expand binding path"
            )


# ---------------------------------------------------------------------------
# N. Trailing control-prompt stripping
# ---------------------------------------------------------------------------


class TestStripTrailingControlPrompts:
    """The LLM commonly tacks closer prompts on the end of an authored reply
    ("You can check the preview pane for the full list", "Would you like to
    submit this?", "I've added 10 more dad jokes…").  These must be stripped
    BEFORE the reply text is bound into write_file.content — otherwise the
    closer narration becomes part of the saved file."""

    def test_strips_preview_pane_prompt(self) -> None:
        from voxera.vera_web.draft_content_binding import strip_trailing_control_prompts

        text = (
            "11. New joke about queues.\n"
            "12. Another joke about caches.\n\n"
            "You can check the preview pane for the full list."
        )
        result = strip_trailing_control_prompts(text)
        assert "preview pane" not in result.lower()
        assert "11. New joke about queues." in result
        assert "12. Another joke about caches." in result

    def test_strips_submit_prompt(self) -> None:
        from voxera.vera_web.draft_content_binding import strip_trailing_control_prompts

        text = (
            "Body line one.\nBody line two.\n\n"
            "Would you like to submit this to be saved, or should we refine it further?"
        )
        result = strip_trailing_control_prompts(text)
        assert "submit this" not in result.lower()
        assert "refine it further" not in result.lower()
        assert "Body line one." in result

    def test_strips_let_me_know_closer(self) -> None:
        from voxera.vera_web.draft_content_binding import strip_trailing_control_prompts

        text = "Body content here.\n\nLet me know if you'd like to refine it further."
        result = strip_trailing_control_prompts(text)
        assert "let me know" not in result.lower()
        assert "Body content here." in result

    def test_strips_preview_only_closer(self) -> None:
        from voxera.vera_web.draft_content_binding import strip_trailing_control_prompts

        text = "Authored body.\nThis is preview-only — nothing has been submitted yet."
        result = strip_trailing_control_prompts(text)
        assert "preview-only" not in result.lower()
        assert "nothing has been submitted" not in result.lower()
        assert "Authored body." in result

    def test_strips_leading_added_narration(self) -> None:
        from voxera.vera_web.draft_content_binding import strip_trailing_control_prompts

        text = "I've added 10 more dad jokes to your list.\n\n11. New joke A.\n12. New joke B."
        result = strip_trailing_control_prompts(text)
        assert "I've added 10 more" not in result
        assert "11. New joke A." in result

    def test_strips_stacked_closers(self) -> None:
        from voxera.vera_web.draft_content_binding import strip_trailing_control_prompts

        text = (
            "Real body.\n\n"
            "You can check the preview pane for the full list.\n\n"
            "Would you like to submit this to be saved?"
        )
        result = strip_trailing_control_prompts(text)
        assert "preview pane" not in result.lower()
        assert "submit this" not in result.lower()
        assert "Real body." in result

    def test_pure_closer_strips_to_empty(self) -> None:
        from voxera.vera_web.draft_content_binding import strip_trailing_control_prompts

        text = (
            "I've added 10 more dad jokes to the list.\n\n"
            "You can check the preview pane for the full list.\n\n"
            "Would you like to submit this?"
        )
        result = strip_trailing_control_prompts(text)
        assert result == "" or len(result.split()) < 4

    def test_normal_body_unchanged(self) -> None:
        from voxera.vera_web.draft_content_binding import strip_trailing_control_prompts

        text = (
            "1. Why did the chicken cross the road? To get to the other side.\n"
            "2. Why don't scientists trust atoms? They make up everything."
        )
        result = strip_trailing_control_prompts(text)
        assert "chicken cross the road" in result
        assert "scientists trust atoms" in result


# ---------------------------------------------------------------------------
# O. Append-binding truth guard: no-op change must not claim success
# ---------------------------------------------------------------------------


class TestAppendBindingTruthGuard:
    """Critical invariant: builder_payload must NEVER be set when the
    combined content equals the existing content (after stripping closers).
    Without this guard, the conversational 'updated the preview' reply
    fires with `updated=True` even though no real mutation happened."""

    def _kwargs(self, *, message: str, pending: dict, reply: str | None) -> dict:
        return {
            "message": message,
            "reply_code_content": None,
            "reply_text_draft": reply,
            "reply_status": "ok",
            "builder_payload": None,
            "pending_preview": pending,
            "is_code_draft_turn": False,
            "is_writing_draft_turn": False,
            "is_explicit_writing_transform": False,
            "informational_web_turn": False,
            "is_enrichment_turn": False,
            "explicit_targeted_content_refinement": False,
            "active_preview_is_refinable_prose": True,
            "conversational_answer_first_turn": False,
            "active_session": "test-session-truth-guard",
        }

    def test_pure_closer_reply_fails_closed_no_builder_payload(self) -> None:
        """When the LLM reply is ENTIRELY closer/control text (after strip
        nothing remains), the binding must fail closed instead of producing
        a no-op builder_payload that would trigger a false-success reply."""
        from voxera.vera_web.draft_content_binding import resolve_draft_content_binding

        pending = {
            "goal": "write a file called jokye.txt with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/jokye.txt",
                "content": "1. Joke A\n2. Joke B",
                "mode": "overwrite",
            },
        }
        # LLM reply is wholly closer narration — no actual joke text.
        reply = (
            "I've added 10 more dad jokes to your list.\n\n"
            "You can check the preview pane for the full list.\n\n"
            "Would you like to submit this to be saved, or should we refine it further?"
        )
        result = resolve_draft_content_binding(
            **self._kwargs(message="add 10 more jokes to the content", pending=pending, reply=reply)
        )
        assert result.builder_payload is None, (
            "no builder_payload — there was no real authored body to append"
        )
        assert result.preview_needs_write is False
        assert result.generation_content_refresh_failed_closed is True

    def test_real_body_with_trailing_closer_strips_and_appends(self) -> None:
        """When the LLM reply has real joke text PLUS a trailing closer,
        the closer is stripped and only the real body is appended."""
        from voxera.vera_web.draft_content_binding import resolve_draft_content_binding

        pending = {
            "goal": "write a file called jokye.txt with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/jokye.txt",
                "content": "1. Joke A\n2. Joke B",
                "mode": "overwrite",
            },
        }
        reply = (
            "11. Why did the queue cross the cache? To invalidate.\n"
            "12. What did the daemon say? Nothing.\n\n"
            "You can check the preview pane for the full list."
        )
        result = resolve_draft_content_binding(
            **self._kwargs(message="add 10 more jokes to the content", pending=pending, reply=reply)
        )
        assert result.builder_payload is not None
        wf = result.builder_payload["write_file"]
        # Real authored joke text appended.
        assert "queue cross the cache" in wf["content"]
        # Closer narration NOT in saved content.
        assert "preview pane" not in wf["content"].lower()
        # Existing body preserved.
        assert "Joke A" in wf["content"]


class TestAppendBindingResponseShaping:
    """When the append-binding fails closed on an active-preview expand
    turn, the user must see the explicit 'I could not safely update' message
    — never a vague 'draft unchanged' reply."""

    def test_expand_failure_uses_explicit_fail_closed_message(self) -> None:
        from voxera.vera_web.response_shaping import assemble_assistant_reply

        pending = {
            "goal": "write a file called jokye.txt with provided content",
            "write_file": {
                "path": "~/VoxeraOS/notes/jokye.txt",
                "content": "1. Joke A\n2. Joke B",
                "mode": "overwrite",
            },
        }
        result = assemble_assistant_reply(
            "I've added 10 more dad jokes to your list. "
            "You can check the preview pane for the full list.",
            message="add 10 more jokes to the content",
            pending_preview=pending,
            builder_payload=None,
            in_voxera_preview_flow=False,
            is_code_draft_turn=False,
            is_writing_draft_turn=False,
            is_enrichment_turn=False,
            conversational_answer_first_turn=False,
            is_json_content_request=False,
            is_voxera_control_turn=False,
            explicit_targeted_content_refinement=False,
            preview_update_rejected=False,
            generation_content_refresh_failed_closed=True,
            reply_status="ok:test",
        )
        # The clean explicit message must appear.
        assert "could not safely update" in result.assistant_text.lower()
        # The false count claim must NOT leak.
        assert "added 10 more dad jokes" not in result.assistant_text.lower()
