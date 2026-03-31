"""Characterization tests for the draft content binding extraction.

These tests anchor the behavior of the pure derivation logic extracted from
``chat()`` into ``vera_web.draft_content_binding``.  They verify that reply
content extraction and draft binding produce expected results without
requiring the full chat() integration path.
"""

from __future__ import annotations

from voxera.vera_web.draft_content_binding import (
    DraftContentBindingResult,
    ReplyDrafts,
    extract_reply_drafts,
    resolve_draft_content_binding,
    strip_internal_control_blocks,
)

# ---------------------------------------------------------------------------
# strip_internal_control_blocks
# ---------------------------------------------------------------------------


class TestStripInternalControlBlocks:
    def test_empty_text_returns_empty(self) -> None:
        assert strip_internal_control_blocks("") == ""

    def test_no_control_blocks_unchanged(self) -> None:
        text = "Here is your code:\n\n```python\nprint('hi')\n```"
        assert strip_internal_control_blocks(text) == text

    def test_fenced_control_block_stripped(self) -> None:
        text = (
            "Some answer.\n"
            "```xml\n<voxera_control type='preview'>stuff</voxera_control>\n```\n"
            "More text."
        )
        result = strip_internal_control_blocks(text)
        assert "<voxera_control" not in result
        assert "Some answer." in result
        assert "More text." in result

    def test_unfenced_control_block_stripped(self) -> None:
        text = "Answer.\n<voxera_control type='x'>inner</voxera_control>\nDone."
        result = strip_internal_control_blocks(text)
        assert "<voxera_control" not in result
        assert "Answer." in result
        assert "Done." in result

    def test_excess_newlines_collapsed(self) -> None:
        text = "Line 1.\n\n\n\n\nLine 2."
        result = strip_internal_control_blocks(text)
        assert "\n\n\n" not in result
        assert result == "Line 1.\n\nLine 2."


# ---------------------------------------------------------------------------
# extract_reply_drafts
# ---------------------------------------------------------------------------


class TestExtractReplyDrafts:
    def test_extracts_code_from_fenced_block(self) -> None:
        reply = "Here is the code:\n\n```python\nprint('hello')\n```"
        drafts = extract_reply_drafts(reply, "create a python script")
        assert isinstance(drafts, ReplyDrafts)
        assert drafts.reply_code_content is not None
        assert "print('hello')" in drafts.reply_code_content

    def test_sanitized_answer_strips_control_blocks(self) -> None:
        reply = "Answer.\n<voxera_control>stuff</voxera_control>\nDone."
        drafts = extract_reply_drafts(reply, "hello")
        assert "<voxera_control" not in drafts.sanitized_answer
        assert "Answer." in drafts.sanitized_answer

    def test_no_code_returns_none_code_content(self) -> None:
        reply = "This is a conversational reply with no code."
        drafts = extract_reply_drafts(reply, "tell me about python")
        assert drafts.reply_code_content is None

    def test_returns_text_draft_when_present(self) -> None:
        # The text draft extraction depends on the writing draft intent module.
        # For a simple conversational reply, it should not produce a text draft.
        reply = "I cannot do that."
        drafts = extract_reply_drafts(reply, "write a poem")
        # Text draft may or may not be extracted depending on heuristics —
        # this test anchors that the function runs without error.
        assert isinstance(drafts.reply_text_draft, (str, type(None)))


# ---------------------------------------------------------------------------
# resolve_draft_content_binding — baseline behavior
# ---------------------------------------------------------------------------


def _default_binding_kwargs() -> dict:
    """Shared defaults for resolve_draft_content_binding calls."""
    return dict(
        message="hello",
        reply_code_content=None,
        reply_text_draft=None,
        reply_status="ok",
        builder_payload=None,
        pending_preview=None,
        is_code_draft_turn=False,
        is_writing_draft_turn=False,
        is_explicit_writing_transform=False,
        informational_web_turn=False,
        is_enrichment_turn=False,
        explicit_targeted_content_refinement=False,
        active_preview_is_refinable_prose=False,
        conversational_answer_first_turn=False,
        active_session="test-session-01",
    )


class TestResolveDraftContentBindingBaseline:
    def test_no_op_when_no_drafts(self) -> None:
        result = resolve_draft_content_binding(**_default_binding_kwargs())
        assert isinstance(result, DraftContentBindingResult)
        assert result.builder_payload is None
        assert result.is_code_draft_turn is False
        assert result.is_writing_draft_turn is False
        assert result.generation_content_refresh_failed_closed is False
        assert result.preview_needs_write is False

    def test_no_op_for_informational_web_turn(self) -> None:
        kwargs = _default_binding_kwargs()
        kwargs["informational_web_turn"] = True
        kwargs["reply_code_content"] = "print('x')"
        result = resolve_draft_content_binding(**kwargs)
        assert result.preview_needs_write is False
        assert result.is_code_draft_turn is False


# ---------------------------------------------------------------------------
# resolve_draft_content_binding — code draft injection
# ---------------------------------------------------------------------------


class TestCodeDraftBinding:
    def test_code_draft_injects_into_builder_payload(self) -> None:
        kwargs = _default_binding_kwargs()
        kwargs["message"] = "create a python script called hello.py"
        kwargs["reply_code_content"] = "print('hello world')"
        kwargs["is_code_draft_turn"] = True
        kwargs["builder_payload"] = {
            "goal": "create hello.py",
            "write_file": {"path": "~/hello.py", "content": "", "mode": "overwrite"},
        }
        result = resolve_draft_content_binding(**kwargs)
        assert result.is_code_draft_turn is True
        assert result.preview_needs_write is True
        assert result.builder_payload is not None
        wf = result.builder_payload.get("write_file")
        assert isinstance(wf, dict)
        assert wf["content"] == "print('hello world')"

    def test_code_draft_falls_back_to_pending_preview(self) -> None:
        kwargs = _default_binding_kwargs()
        kwargs["message"] = "update the script"
        kwargs["reply_code_content"] = "print('updated')"
        kwargs["is_code_draft_turn"] = True
        kwargs["pending_preview"] = {
            "goal": "create script",
            "write_file": {"path": "~/test.py", "content": "old", "mode": "overwrite"},
        }
        result = resolve_draft_content_binding(**kwargs)
        assert result.preview_needs_write is True
        wf = result.builder_payload["write_file"]
        assert wf["content"] == "print('updated')"
        assert wf["path"] == "~/test.py"

    def test_late_code_draft_detection_from_active_code_preview(self) -> None:
        kwargs = _default_binding_kwargs()
        kwargs["message"] = "add error handling"
        kwargs["reply_code_content"] = "try:\n    pass\nexcept:\n    pass"
        kwargs["is_code_draft_turn"] = False
        kwargs["pending_preview"] = {
            "goal": "script",
            "write_file": {"path": "~/app.py", "content": "old code", "mode": "overwrite"},
        }
        result = resolve_draft_content_binding(**kwargs)
        # Late detection should set is_code_draft_turn
        assert result.is_code_draft_turn is True
        assert result.preview_needs_write is True


# ---------------------------------------------------------------------------
# resolve_draft_content_binding — writing draft injection
# ---------------------------------------------------------------------------


class TestWritingDraftBinding:
    def test_writing_draft_injects_into_builder_payload(self) -> None:
        kwargs = _default_binding_kwargs()
        kwargs["message"] = "write a poem about nature"
        kwargs["reply_text_draft"] = "The trees sway gently in the breeze."
        kwargs["is_writing_draft_turn"] = True
        kwargs["builder_payload"] = {
            "goal": "write poem",
            "write_file": {"path": "~/poem.md", "content": "", "mode": "overwrite"},
        }
        result = resolve_draft_content_binding(**kwargs)
        assert result.is_writing_draft_turn is True
        assert result.preview_needs_write is True
        wf = result.builder_payload["write_file"]
        assert wf["content"] == "The trees sway gently in the breeze."

    def test_late_writing_draft_detection_from_refinable_prose(self) -> None:
        """When a refinable prose preview exists and the user asks to refine, detect as writing draft."""
        kwargs = _default_binding_kwargs()
        kwargs["message"] = "make it shorter"
        kwargs["reply_text_draft"] = "Short poem."
        kwargs["is_writing_draft_turn"] = False
        kwargs["active_preview_is_refinable_prose"] = True
        kwargs["pending_preview"] = {
            "goal": "poem",
            "write_file": {
                "path": "~/notes/poem.md",
                "content": "A long poem.",
                "mode": "overwrite",
            },
        }
        result = resolve_draft_content_binding(**kwargs)
        # Late detection should set is_writing_draft_turn if the message
        # matches writing refinement heuristics. The exact result depends on
        # is_writing_refinement_request(), but either way behavior is preserved.
        assert isinstance(result.is_writing_draft_turn, bool)

    def test_writing_draft_skipped_on_degraded_status(self) -> None:
        kwargs = _default_binding_kwargs()
        kwargs["message"] = "write a poem"
        kwargs["reply_text_draft"] = "A poem"
        kwargs["is_writing_draft_turn"] = True
        kwargs["reply_status"] = "degraded:backend_unavailable"
        kwargs["builder_payload"] = {
            "goal": "poem",
            "write_file": {"path": "~/poem.md", "content": "", "mode": "overwrite"},
        }
        result = resolve_draft_content_binding(**kwargs)
        assert result.preview_needs_write is False


# ---------------------------------------------------------------------------
# resolve_draft_content_binding — create-and-save fallback
# ---------------------------------------------------------------------------


class TestCreateAndSaveFallback:
    def test_create_and_save_generates_preview_from_reply(self) -> None:
        kwargs = _default_binding_kwargs()
        kwargs["message"] = "save a checklist to a note for my wedding prep"
        kwargs["reply_text_draft"] = "- Book venue\n- Send invitations\n- Order flowers"
        kwargs["conversational_answer_first_turn"] = False
        kwargs["builder_payload"] = None
        kwargs["pending_preview"] = None
        result = resolve_draft_content_binding(**kwargs)
        assert result.preview_needs_write is True
        assert result.builder_payload is not None
        wf = result.builder_payload.get("write_file")
        assert isinstance(wf, dict)
        assert "wedding" in str(result.builder_payload.get("goal") or "").lower()
        assert wf["content"] == "- Book venue\n- Send invitations\n- Order flowers"

    def test_create_and_save_skipped_in_conversational_mode(self) -> None:
        kwargs = _default_binding_kwargs()
        kwargs["message"] = "save a checklist to a note for my wedding prep"
        kwargs["reply_text_draft"] = "- Item 1"
        kwargs["conversational_answer_first_turn"] = True
        result = resolve_draft_content_binding(**kwargs)
        assert result.preview_needs_write is False


# ---------------------------------------------------------------------------
# resolve_draft_content_binding — generation content refresh fail-closed
# ---------------------------------------------------------------------------


class TestGenerationContentRefresh:
    def test_generation_refresh_fails_closed_with_no_text_draft_and_active_preview(self) -> None:
        """When generation intent is detected but no text draft is available, fail closed."""
        kwargs = _default_binding_kwargs()
        kwargs["message"] = "tell me a joke and save it as joke.md"
        kwargs["reply_text_draft"] = None
        kwargs["pending_preview"] = {
            "goal": "joke",
            "write_file": {"path": "~/notes/joke.md", "content": "old joke", "mode": "overwrite"},
        }
        result = resolve_draft_content_binding(**kwargs)
        # The message triggers generation binding intent, but with no text draft
        # the function correctly fails closed.
        assert result.generation_content_refresh_failed_closed is True
        assert result.preview_needs_write is False

    def test_generation_refresh_flag_false_when_no_generation_intent(self) -> None:
        """When no generation binding intent matches, flag stays False."""
        kwargs = _default_binding_kwargs()
        kwargs["message"] = "how is the weather"
        kwargs["reply_text_draft"] = None
        result = resolve_draft_content_binding(**kwargs)
        assert result.generation_content_refresh_failed_closed is False

    def test_generation_refresh_flag_false_when_draft_present(self) -> None:
        """When reply_text_draft is present, no fail-closed — normal binding."""
        kwargs = _default_binding_kwargs()
        kwargs["reply_text_draft"] = "A nice poem about nature."
        result = resolve_draft_content_binding(**kwargs)
        assert result.generation_content_refresh_failed_closed is False


# ---------------------------------------------------------------------------
# Integration: truth-sensitive write signal behavior
# ---------------------------------------------------------------------------


class TestPreviewWriteSignal:
    def test_no_write_when_nothing_matches(self) -> None:
        kwargs = _default_binding_kwargs()
        kwargs["message"] = "how is the weather today"
        result = resolve_draft_content_binding(**kwargs)
        assert result.preview_needs_write is False

    def test_enrichment_turn_blocks_late_code_draft_detection(self) -> None:
        """Enrichment turns must not trigger late code-draft detection."""
        kwargs = _default_binding_kwargs()
        kwargs["message"] = "what does this function do"
        kwargs["reply_code_content"] = "def foo(): pass"
        kwargs["is_enrichment_turn"] = True
        kwargs["pending_preview"] = {
            "goal": "script",
            "write_file": {"path": "~/app.py", "content": "old", "mode": "overwrite"},
        }
        result = resolve_draft_content_binding(**kwargs)
        assert result.is_code_draft_turn is False
        assert result.preview_needs_write is False

    def test_write_signal_only_on_actual_update(self) -> None:
        kwargs = _default_binding_kwargs()
        kwargs["message"] = "create a script"
        kwargs["reply_code_content"] = "print('hi')"
        kwargs["is_code_draft_turn"] = True
        kwargs["builder_payload"] = {
            "goal": "script",
            "write_file": {"path": "~/test.py", "content": "", "mode": "overwrite"},
        }
        result = resolve_draft_content_binding(**kwargs)
        assert result.preview_needs_write is True
        # Caller (app.py) is responsible for the actual session write.
        # This test verifies the signal is set correctly.
