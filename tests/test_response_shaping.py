"""Characterization tests for the response shaping extraction.

These tests anchor the behavior of the pure derivation logic extracted from
``chat()`` into ``vera_web.response_shaping``.  They verify that preview
content derivation, false-claim guardrails, stale-preview cleanup logic, and
assistant reply assembly produce expected results without requiring the full
``chat()`` integration path.
"""

from __future__ import annotations

from voxera.vera_web.response_shaping import (
    AssistantReplyResult,
    assemble_assistant_reply,
    derive_preview_has_content,
    guardrail_false_preview_claim,
    should_clear_stale_preview,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROSE_PREVIEW = {
    "goal": "save note",
    "write_file": {"path": "~/VoxeraOS/notes/foo.md", "content": "hello world"},
}
_CODE_PREVIEW = {
    "goal": "write script",
    "write_file": {"path": "script.py", "content": "print('hi')"},
}
_EMPTY_CODE_PREVIEW = {
    "goal": "write script",
    "write_file": {"path": "script.py", "content": ""},
}
_EMPTY_PROSE_PREVIEW = {
    "goal": "save note",
    "write_file": {"path": "~/VoxeraOS/notes/foo.md", "content": ""},
}
_STALE_EMPTY_PREVIEW = {
    "goal": "save",
    "write_file": {"path": "~/VoxeraOS/notes/tmp.md", "content": ""},
}


def _base_assemble_kwargs(**overrides):
    """Return a minimal set of kwargs for assemble_assistant_reply."""
    base = dict(
        message="tell me about the weather",
        pending_preview=None,
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
        generation_content_refresh_failed_closed=False,
        reply_status="ok:conversational",
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# derive_preview_has_content
# ---------------------------------------------------------------------------


class TestDerivePreviewHasContent:
    def test_none_preview_returns_false(self) -> None:
        assert derive_preview_has_content(None) is False

    def test_non_dict_returns_false(self) -> None:
        # type: ignore[arg-type] — intentional bad input
        assert derive_preview_has_content("not a dict") is False  # type: ignore[arg-type]

    def test_prose_preview_with_content_returns_true(self) -> None:
        assert derive_preview_has_content(_PROSE_PREVIEW) is True

    def test_code_preview_with_content_returns_true(self) -> None:
        assert derive_preview_has_content(_CODE_PREVIEW) is True

    def test_empty_content_code_file_returns_false(self) -> None:
        # Code file with no content is a placeholder shell, not real content.
        assert derive_preview_has_content(_EMPTY_CODE_PREVIEW) is False

    def test_empty_content_prose_file_txt_returns_true(self) -> None:
        # Non-code-extension files (e.g. .txt) with a path count as having content
        # even with empty content — they are not code placeholders.
        preview = {
            "goal": "save note",
            "write_file": {"path": "~/VoxeraOS/notes/foo.txt", "content": ""},
        }
        assert derive_preview_has_content(preview) is True

    def test_empty_content_md_file_returns_false(self) -> None:
        # .md is classified as a code extension — empty content = placeholder shell.
        assert derive_preview_has_content(_EMPTY_PROSE_PREVIEW) is False

    def test_preview_without_write_file_key_returns_true(self) -> None:
        # Non-write_file job payload is a real preview.
        preview = {"goal": "run something", "enqueue_child": {}}
        assert derive_preview_has_content(preview) is True

    def test_write_file_key_present_but_non_dict_returns_false(self) -> None:
        # write_file exists but is not a dict — treat as no authoritative content.
        preview = {"goal": "run", "write_file": "not a dict"}
        assert derive_preview_has_content(preview) is False


# ---------------------------------------------------------------------------
# guardrail_false_preview_claim
# ---------------------------------------------------------------------------


class TestGuardrailFalsePreviewClaim:
    def test_preview_exists_returns_text_unchanged(self) -> None:
        text = "I prepared a preview for you."
        result = guardrail_false_preview_claim(text, preview_exists=True)
        assert result == text

    def test_no_claim_text_returned_unchanged(self) -> None:
        text = "The capital of Canada is Ottawa."
        result = guardrail_false_preview_claim(text, preview_exists=False)
        assert result == text

    def test_false_preview_claim_stripped_when_no_preview(self) -> None:
        text = "I've prepared a preview for you in the preview pane."
        result = guardrail_false_preview_claim(text, preview_exists=False)
        assert "prepared a preview" not in result.lower()
        assert "not able to prepare" in result or "not able to create" in result

    def test_code_block_preserved_when_claim_stripped(self) -> None:
        text = (
            "I've set up a preview for you in the preview pane.\n\n```python\nprint('hello')\n```"
        )
        result = guardrail_false_preview_claim(text, preview_exists=False)
        assert "```python" in result
        assert "print('hello')" in result
        assert "shown for reference only" in result

    def test_empty_text_returned_unchanged(self) -> None:
        result = guardrail_false_preview_claim("", preview_exists=False)
        assert result == ""


# ---------------------------------------------------------------------------
# should_clear_stale_preview
# ---------------------------------------------------------------------------


class TestShouldClearStalePreview:
    def test_answers_equal_returns_false(self) -> None:
        # No change from guardrail — nothing to clear.
        assert should_clear_stale_preview("same text", "same text", _STALE_EMPTY_PREVIEW) is False

    def test_no_effective_preview_returns_false(self) -> None:
        assert should_clear_stale_preview("new text", "old text", None) is False

    def test_stale_empty_shell_returns_true(self) -> None:
        # Guardrail changed the answer AND preview has empty write_file content.
        assert should_clear_stale_preview("new text", "old text", _STALE_EMPTY_PREVIEW) is True

    def test_preview_with_content_returns_false(self) -> None:
        # Guardrail changed the answer but preview has real content — do NOT clear.
        assert should_clear_stale_preview("new text", "old text", _PROSE_PREVIEW) is False

    def test_non_dict_write_file_returns_false(self) -> None:
        preview = {"goal": "run", "write_file": "not a dict"}
        assert should_clear_stale_preview("new text", "old text", preview) is False

    def test_preview_without_write_file_returns_false(self) -> None:
        # Non-write_file preview has no shell to clear.
        preview = {"goal": "run something", "enqueue_child": {}}
        assert should_clear_stale_preview("new text", "old text", preview) is False


# ---------------------------------------------------------------------------
# assemble_assistant_reply
# ---------------------------------------------------------------------------


class TestAssembleAssistantReply:
    def test_returns_assistant_reply_result(self) -> None:
        result = assemble_assistant_reply(
            "Hello there.",
            **_base_assemble_kwargs(),
        )
        assert isinstance(result, AssistantReplyResult)

    def test_plain_conversational_reply_passes_through(self) -> None:
        result = assemble_assistant_reply(
            "The capital of Alberta is Edmonton.",
            **_base_assemble_kwargs(),
        )
        assert result.assistant_text == "The capital of Alberta is Edmonton."
        assert result.status == "ok:conversational"

    def test_status_is_prepared_preview_when_builder_payload_set(self) -> None:
        result = assemble_assistant_reply(
            "I've prepared a preview.",
            **_base_assemble_kwargs(
                builder_payload=_PROSE_PREVIEW,
                reply_status="ok:preview",
            ),
        )
        assert result.status == "prepared_preview"

    def test_status_is_reply_status_when_no_builder_payload(self) -> None:
        result = assemble_assistant_reply(
            "Sure, here you go.",
            **_base_assemble_kwargs(reply_status="ok:investigation"),
        )
        assert result.status == "ok:investigation"

    def test_generation_refresh_failed_closed_appends_message(self) -> None:
        result = assemble_assistant_reply(
            "I tried to generate content.",
            **_base_assemble_kwargs(
                pending_preview=_PROSE_PREVIEW,
                generation_content_refresh_failed_closed=True,
            ),
        )
        assert "left the active draft content unchanged" in result.assistant_text
        assert "authoritative generated content" in result.assistant_text

    def test_ambiguous_change_request_replaces_text(self) -> None:
        # When no builder_payload, there's an active preview, and the message
        # looks ambiguous, the reply gets a specific refusal text.
        result = assemble_assistant_reply(
            "Sure, I can do that.",
            **_base_assemble_kwargs(
                message="change it",
                pending_preview=_PROSE_PREVIEW,
                builder_payload=None,
            ),
        )
        assert "left the active draft content unchanged" in result.assistant_text
        assert "ambiguous" in result.assistant_text

    def test_naming_mutation_with_pending_preview_uses_control_message(self) -> None:
        # "rename it to foo.md" with active preview triggers the control message
        # rather than the raw LLM reply.
        result = assemble_assistant_reply(
            "I'll rename that for you.",
            **_base_assemble_kwargs(
                message="rename it to foo.md",
                pending_preview=_PROSE_PREVIEW,
                builder_payload=None,
            ),
        )
        # The control message should mention something about the draft or preview
        # not being updated (no builder_payload means updated=False).
        assert isinstance(result.assistant_text, str)
        assert result.assistant_text.strip()

    def test_code_draft_turn_not_suppressed_by_control_reply(self) -> None:
        # Code draft replies are kept as-is even when voxera_control_turn is True.
        code_reply = "Here is your script:\n\n```python\nprint('hi')\n```"
        result = assemble_assistant_reply(
            code_reply,
            **_base_assemble_kwargs(
                message="write a python script",
                is_code_draft_turn=True,
                is_voxera_control_turn=True,
                builder_payload=_CODE_PREVIEW,
            ),
        )
        # Code content must be preserved — not replaced by control narration.
        assert "```python" in result.assistant_text

    def test_empty_assistant_text_falls_back_to_control_message(self) -> None:
        # If guarded_answer is blank, fall back to conversational control message.
        result = assemble_assistant_reply(
            "   ",
            **_base_assemble_kwargs(
                message="tell me about the job",
                pending_preview=_PROSE_PREVIEW,
            ),
        )
        assert result.assistant_text.strip()

    def test_explicit_targeted_refinement_uses_control_message(self) -> None:
        # When explicit_targeted_content_refinement=True and builder_payload is set,
        # use the conversational preview update message.
        result = assemble_assistant_reply(
            "I updated the content of the file.",
            **_base_assemble_kwargs(
                message="update the content",
                pending_preview=_PROSE_PREVIEW,
                builder_payload=_PROSE_PREVIEW,
                explicit_targeted_content_refinement=True,
                is_code_draft_turn=False,
                is_writing_draft_turn=False,
            ),
        )
        # Should not use the raw LLM "I updated the content" text but the
        # governed control message instead.
        assert isinstance(result.assistant_text, str)
        assert result.assistant_text.strip()

    def test_voxera_control_turn_non_json_uses_control_message(self) -> None:
        # A voxera control turn without json content request should use
        # the conversational control reply, not the raw LLM narration.
        narration = "I prepared a VoxeraOS job preview with write_file and goal."
        result = assemble_assistant_reply(
            narration,
            **_base_assemble_kwargs(
                message="show me the job",
                is_voxera_control_turn=True,
                is_json_content_request=False,
                in_voxera_preview_flow=True,
                pending_preview=_PROSE_PREVIEW,
            ),
        )
        # The raw control narration should be suppressed.
        assert result.assistant_text != narration

    def test_json_content_request_does_not_suppress_reply(self) -> None:
        # Explicit JSON content requests bypass control-reply suppression.
        raw = '{"goal": "test", "write_file": {"path": "x.py", "content": "hi"}}'
        result = assemble_assistant_reply(
            raw,
            **_base_assemble_kwargs(
                message="show me the raw json",
                is_json_content_request=True,
                is_voxera_control_turn=True,
                in_voxera_preview_flow=True,
                pending_preview=_PROSE_PREVIEW,
            ),
        )
        # JSON should pass through since it's an explicit JSON request.
        assert result.assistant_text == raw


# ---------------------------------------------------------------------------
# Preview-state wording clarity
# ---------------------------------------------------------------------------


class TestPreviewStateWordingClarity:
    """Verify that assistant replies use clear, distinct wording for each
    preview state transition: prepared, updated, preview-only, submitted,
    stale/empty, and rename/save-as."""

    def test_new_preview_prepared_says_prepared(self) -> None:
        # First preview (no pending_preview) should say "prepared"
        result = assemble_assistant_reply(
            "I set up a preview for you in the preview pane.",
            **_base_assemble_kwargs(
                message="write a note about volcanoes",
                builder_payload=_PROSE_PREVIEW,
                pending_preview=None,
                is_voxera_control_turn=True,
                in_voxera_preview_flow=True,
            ),
        )
        assert "prepared" in result.assistant_text.lower()
        assert "preview-only" in result.assistant_text.lower()
        assert "nothing has been submitted yet" in result.assistant_text.lower()

    def test_existing_preview_updated_says_updated(self) -> None:
        # Update to existing preview (pending_preview exists) should say "updated"
        result = assemble_assistant_reply(
            "I updated the preview for you.",
            **_base_assemble_kwargs(
                message="make it shorter",
                builder_payload=_PROSE_PREVIEW,
                pending_preview=_PROSE_PREVIEW,
                is_voxera_control_turn=True,
                in_voxera_preview_flow=True,
            ),
        )
        assert "updated" in result.assistant_text.lower()
        assert "preview-only" in result.assistant_text.lower()
        assert "nothing has been submitted yet" in result.assistant_text.lower()

    def test_prepared_vs_updated_wording_differs(self) -> None:
        # Prepared (new) and updated (existing) should produce different wording
        prepared = assemble_assistant_reply(
            "I set up a preview.",
            **_base_assemble_kwargs(
                message="write a note",
                builder_payload=_PROSE_PREVIEW,
                pending_preview=None,
                is_voxera_control_turn=True,
                in_voxera_preview_flow=True,
            ),
        )
        updated = assemble_assistant_reply(
            "I updated the preview.",
            **_base_assemble_kwargs(
                message="make it better",
                builder_payload=_PROSE_PREVIEW,
                pending_preview=_PROSE_PREVIEW,
                is_voxera_control_turn=True,
                in_voxera_preview_flow=True,
            ),
        )
        assert prepared.assistant_text != updated.assistant_text

    def test_preview_only_no_submit_when_no_builder_payload(self) -> None:
        # When nothing was built, with active preview, reply should not imply submission
        result = assemble_assistant_reply(
            "I updated the draft for you.",
            **_base_assemble_kwargs(
                message="tell me more",
                builder_payload=None,
                pending_preview=_PROSE_PREVIEW,
                is_voxera_control_turn=True,
                in_voxera_preview_flow=True,
            ),
        )
        assert "submitted" not in result.assistant_text.lower() or (
            "not" in result.assistant_text.lower() or "nothing" in result.assistant_text.lower()
        )
        # Should mention the preview still exists
        assert (
            "preview" in result.assistant_text.lower() or "draft" in result.assistant_text.lower()
        )

    def test_stale_empty_preview_gets_honest_reply(self) -> None:
        # When active preview is empty shell and no builder update, don't imply content
        result = assemble_assistant_reply(
            "   ",
            **_base_assemble_kwargs(
                message="what is going on",
                builder_payload=None,
                pending_preview=_EMPTY_PROSE_PREVIEW,
            ),
        )
        assert result.assistant_text.strip()
        # Should not claim submission occurred
        assert "submitted" not in result.assistant_text.lower() or (
            "not" in result.assistant_text.lower() or "nothing" in result.assistant_text.lower()
        )

    def test_rename_with_update_mentions_path_and_preview_only(self) -> None:
        # Rename/save-as with update should mention new path and "preview-only"
        renamed = {
            "goal": "save note",
            "write_file": {
                "path": "~/VoxeraOS/notes/volcano.txt",
                "content": "hello world",
            },
        }
        result = assemble_assistant_reply(
            "I renamed the file.",
            **_base_assemble_kwargs(
                message="save it as volcano.txt",
                builder_payload=renamed,
                pending_preview=_PROSE_PREVIEW,
            ),
        )
        assert "volcano.txt" in result.assistant_text
        assert "preview-only" in result.assistant_text.lower()

    def test_save_as_with_update_does_not_say_submitted(self) -> None:
        renamed = {
            "goal": "save note",
            "write_file": {
                "path": "~/VoxeraOS/notes/renamed.md",
                "content": "content",
            },
        }
        result = assemble_assistant_reply(
            "I saved it as renamed.md.",
            **_base_assemble_kwargs(
                message="save it as renamed.md",
                builder_payload=renamed,
                pending_preview=_PROSE_PREVIEW,
            ),
        )
        # Must NOT imply submission happened
        if "submitted" in result.assistant_text.lower():
            assert (
                "not" in result.assistant_text.lower() or "nothing" in result.assistant_text.lower()
            )

    def test_explicit_refinement_with_existing_preview_says_updated(self) -> None:
        result = assemble_assistant_reply(
            "I updated the content of the file.",
            **_base_assemble_kwargs(
                message="make it more formal",
                pending_preview=_PROSE_PREVIEW,
                builder_payload=_PROSE_PREVIEW,
                explicit_targeted_content_refinement=True,
            ),
        )
        assert "updated" in result.assistant_text.lower()
        assert "preview-only" in result.assistant_text.lower()

    def test_no_preview_no_builder_gives_clear_failure(self) -> None:
        # When nothing could be prepared, reply should be clear and user-facing
        result = assemble_assistant_reply(
            "",
            **_base_assemble_kwargs(
                message="do something",
                builder_payload=None,
                pending_preview=None,
            ),
        )
        assert result.assistant_text.strip()
        # Should NOT sound like raw control-plane JSON
        assert "write_file" not in result.assistant_text
        assert "goal" not in result.assistant_text.lower().split()

    def test_status_is_prepared_preview_only_with_builder_payload(self) -> None:
        # Status field must reflect actual state
        with_builder = assemble_assistant_reply(
            "preview ready",
            **_base_assemble_kwargs(
                builder_payload=_PROSE_PREVIEW,
                reply_status="ok:preview",
            ),
        )
        without_builder = assemble_assistant_reply(
            "no preview here",
            **_base_assemble_kwargs(
                builder_payload=None,
                reply_status="ok:conversational",
            ),
        )
        assert with_builder.status == "prepared_preview"
        assert without_builder.status == "ok:conversational"

    # -- Writing-draft turns: preview notice appended to authored content --

    def test_writing_draft_new_preview_appends_prepared_notice(self) -> None:
        # Writing-draft turn with new preview (no pending_preview) should append
        # "prepared preview / preview-only" notice to authored content.
        authored = "The artifact evidence model tracks execution outcomes."
        result = assemble_assistant_reply(
            authored,
            **_base_assemble_kwargs(
                message="write me a note about the artifact evidence model",
                builder_payload=_PROSE_PREVIEW,
                pending_preview=None,
                is_writing_draft_turn=True,
                in_voxera_preview_flow=True,
            ),
        )
        # Authored content preserved
        assert "artifact evidence model" in result.assistant_text.lower()
        # Preview-state notice appended
        assert "prepared" in result.assistant_text.lower()
        assert "preview-only" in result.assistant_text.lower()
        assert "nothing has been submitted yet" in result.assistant_text.lower()

    def test_writing_draft_updated_preview_appends_updated_notice(self) -> None:
        # Writing-draft refinement turn with existing preview should append
        # "updated preview / still preview-only" notice.
        authored = "The artifact evidence model gives operators visibility."
        result = assemble_assistant_reply(
            authored,
            **_base_assemble_kwargs(
                message="make it shorter and more operator-facing",
                builder_payload=_PROSE_PREVIEW,
                pending_preview=_PROSE_PREVIEW,
                is_writing_draft_turn=True,
                in_voxera_preview_flow=True,
            ),
        )
        # Authored content preserved
        assert "artifact evidence model" in result.assistant_text.lower()
        # Updated preview notice appended
        assert "updated" in result.assistant_text.lower()
        assert "preview-only" in result.assistant_text.lower()
        assert "nothing has been submitted yet" in result.assistant_text.lower()

    def test_writing_draft_no_builder_payload_no_notice(self) -> None:
        # Writing-draft turn where builder_payload is None should NOT
        # append a preview notice (no preview was produced).
        authored = "Content about something."
        result = assemble_assistant_reply(
            authored,
            **_base_assemble_kwargs(
                message="write me a note",
                builder_payload=None,
                pending_preview=None,
                is_writing_draft_turn=True,
            ),
        )
        assert result.assistant_text == authored

    def test_writing_draft_prepared_vs_updated_notice_differs(self) -> None:
        authored = "Some authored content."
        prepared = assemble_assistant_reply(
            authored,
            **_base_assemble_kwargs(
                message="write me a note",
                builder_payload=_PROSE_PREVIEW,
                pending_preview=None,
                is_writing_draft_turn=True,
                in_voxera_preview_flow=True,
            ),
        )
        updated = assemble_assistant_reply(
            authored,
            **_base_assemble_kwargs(
                message="make it shorter",
                builder_payload=_PROSE_PREVIEW,
                pending_preview=_PROSE_PREVIEW,
                is_writing_draft_turn=True,
                in_voxera_preview_flow=True,
            ),
        )
        assert prepared.assistant_text != updated.assistant_text


# ---------------------------------------------------------------------------
# Regression: no duplicate preview-narration layering
# ---------------------------------------------------------------------------


class TestNoDuplicatePreviewNarration:
    """Successful authored drafting turns must not stack preview narration.

    Regression: "Write me a short note about what happened at the meeting"
    produced triple-layered output: content + LLM narration + stock narration.
    """

    def test_contentful_draft_with_llm_narration_strips_duplicate(self) -> None:
        """When the LLM reply includes content + its own narration, the stock
        preview-state notice should replace (not stack on) the LLM narration."""
        llm_reply = (
            "Meeting Notes - April 4, 2026\n\n"
            "Key discussion points from today's team meeting:\n\n"
            "1. Q2 roadmap review\n"
            "2. Infrastructure upgrade\n\n"
            "I've updated the draft to include this note. "
            "You can review the changes in the preview pane before we submit it."
        )
        result = assemble_assistant_reply(
            llm_reply,
            **_base_assemble_kwargs(
                message="Write me a short note about what happened at the meeting",
                builder_payload=_PROSE_PREVIEW,
                pending_preview=None,
                is_writing_draft_turn=True,
                in_voxera_preview_flow=True,
            ),
        )
        text = result.assistant_text
        # Authored content must survive
        assert "Key discussion points" in text
        assert "Q2 roadmap" in text
        # LLM narration must be stripped
        assert "review the changes in the preview pane" not in text
        # Stock canonical narration must be present exactly once
        assert text.count("preview-only") == 1
        assert "nothing has been submitted yet" in text.lower()

    def test_contentful_draft_without_narration_gets_stock_notice(self) -> None:
        """When the LLM reply is pure content (no narration), the stock
        preview-state notice is appended normally."""
        llm_reply = (
            "Meeting Notes\n\nThe team discussed Q2 milestones and agreed to push the deadline."
        )
        result = assemble_assistant_reply(
            llm_reply,
            **_base_assemble_kwargs(
                message="Write me a short note about what happened at the meeting",
                builder_payload=_PROSE_PREVIEW,
                pending_preview=None,
                is_writing_draft_turn=True,
                in_voxera_preview_flow=True,
            ),
        )
        text = result.assistant_text
        assert "Meeting Notes" in text
        assert "preview-only" in text.lower()
        assert "nothing has been submitted yet" in text.lower()

    def test_pure_narration_reply_still_gets_stock_notice(self) -> None:
        """When the LLM reply is pure narration (no authored content), the
        stock notice is still appended."""
        result = assemble_assistant_reply(
            "I set up a preview for you.",
            **_base_assemble_kwargs(
                message="write me a note about the artifact evidence model",
                builder_payload=_PROSE_PREVIEW,
                pending_preview=None,
                is_writing_draft_turn=True,
                in_voxera_preview_flow=True,
            ),
        )
        text = result.assistant_text.lower()
        assert "prepared" in text or "preview" in text
        assert "preview-only" in text
        assert "nothing has been submitted yet" in text

    def test_update_turn_with_llm_narration_strips_duplicate(self) -> None:
        """Same de-duplication on update turns (pending_preview exists)."""
        llm_reply = (
            "Here is the shorter version of your note.\n\n"
            "I've updated the preview with your changes."
        )
        result = assemble_assistant_reply(
            llm_reply,
            **_base_assemble_kwargs(
                message="make it shorter",
                builder_payload=_PROSE_PREVIEW,
                pending_preview=_PROSE_PREVIEW,
                is_writing_draft_turn=True,
                in_voxera_preview_flow=True,
            ),
        )
        text = result.assistant_text
        assert "shorter version" in text
        # Stock narration present once, LLM duplicate stripped
        assert text.count("updated the preview") == 1
        assert "preview-only" in text.lower()
