"""Tests for the referenced save-to-note preview flow restoration.

Regression: PR #365 hardened preview content handling but inadvertently
broke the core referenced-content save flow so that "add that to a note"
returned a generic refusal instead of a governed write_file preview.

Root cause: ``message_requests_referenced_content`` did not recognise
"add that/this/it to a note/file" or "put this in a note" variants.
The typo form "savee it to a note" was also unhandled.

These tests cover:
1. Intent recognition — ``message_requests_referenced_content``
2. Full preview payload — ``_normalize_structured_file_write_payload``
3. Trailing control-text stripping — ``_strip_trailing_control_text``
4. Artifact storage — ``build_saveable_assistant_artifact``
5. Artifact selection — ``select_recent_saveable_assistant_artifact``
6. End-to-end builder — ``maybe_draft_job_payload``
7. Fail-closed when no authored content exists
8. Wrapper/status boilerplate correctly excluded from artifacts
"""

from __future__ import annotations

import pytest

from voxera.vera.preview_drafting import (
    _normalize_structured_file_write_payload,
    is_recent_assistant_content_save_request,
    maybe_draft_job_payload,
)
from voxera.vera.saveable_artifacts import (
    _strip_trailing_control_text,
    build_saveable_assistant_artifact,
    message_requests_referenced_content,
    select_recent_saveable_assistant_artifact,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_DAD_JOKES_CONTENT = (
    "Here are 20 dad jokes:\n"
    "1. Why did the bicycle fall over? Because it was two-tired!\n"
    "2. What do you call a fish without eyes? A fsh!\n"
    "3. I'm reading a book about anti-gravity. It's impossible to put down!\n"
    "4. Did you hear about the guy who invented Lifesavers? He made a mint!\n"
    "5. Why don't eggs tell jokes? They'd crack each other up!\n"
    "6. I would avoid the sushi if I was you. It's a little fishy!\n"
    "7. Want to hear a joke about construction? I'm still working on it!\n"
    "8. Why can't your nose be 12 inches long? Because then it'd be a foot!\n"
    "9. What do you call someone with no body and no nose? Nobody knows!\n"
    "10. Did I tell you the time joke? Never mind, it's past your time!\n"
    "11. Why did the math book look so sad? Because it had too many problems!\n"
    "12. I used to hate facial hair but then it grew on me!\n"
    "13. What's a vampire's favourite fruit? A blood orange!\n"
    "14. Why did the golfer bring an extra pair of socks? In case he got a hole in one!\n"
    "15. A skeleton walks into a bar and orders a beer and a mop!\n"
    "16. What do you call a sleeping dinosaur? A dino-snore!\n"
    "17. Why did the scarecrow win an award? Because he was outstanding in his field!\n"
    "18. What do you call cheese that isn't yours? Nacho cheese!\n"
    "19. I'm on a seafood diet. I see food and I eat it!\n"
    "20. Why can't you give Elsa a balloon? Because she'll let it go!"
)

_DAD_JOKES_WITH_TRAILING = (
    _DAD_JOKES_CONTENT
    + "\nLet me know if you need any more—or if you actually want to get some work done!"
)

_ARTIFACT_JOKES = {"content": _DAD_JOKES_CONTENT, "artifact_type": "info"}


# ---------------------------------------------------------------------------
# 1. Intent recognition — message_requests_referenced_content
# ---------------------------------------------------------------------------


class TestMessageRequestsReferencedContent:
    """Verify that the expanded pattern set recognises all required phrases."""

    # Phrases that MUST be recognised (required by spec)
    @pytest.mark.parametrize(
        "message",
        [
            # Core referenced-save phrases
            "save that to a note",
            "save this to a note",
            "add that to a note",
            "add this to a note",
            "put that in a note",
            "put this in a note",
            "save that answer",
            "save your last answer",
            "save it to a note",
            # Typo-tolerant
            "savee it to a note",
            "savee that to a note",
            "savee this to a note",
            # With "ok" prefix (search finds embedded phrase)
            "ok add that to a note",
            # Filename supplied
            "save that to dad-jokes.txt",
            "save that to a note called dad-jokes.txt",
            "add that to a note called dad-jokes.txt",
            "put that in a note called dad-jokes.txt",
            "put this in a note named dad-jokes.txt",
            "put that in dad-jokes.txt",
            # Existing patterns must still work
            "save it as notes.txt",
            "put that into a file",
            "put it in a note",
            "use that as the content",
            "write your previous answer to a file",
        ],
    )
    def test_recognised(self, message: str) -> None:
        assert message_requests_referenced_content(message) is True, (
            f"Expected True for {message!r}"
        )

    # Phrases that must NOT be recognised (safety / false-positive prevention)
    @pytest.mark.parametrize(
        "message",
        [
            "hello world",
            "open google.com",
            "what is the weather",
            "tell me a joke",
            "write me an essay about linux",
            "check disk usage",
            # "add" verb must not trigger without a reference + file/note target
            "add a note about linux",
            "add this content here",
            "add to the queue",
            # bare-filename pattern must not match URLs/domains
            "put that into example.com",
            "put this into google.com/path",
            "put it in wikipedia.org",
        ],
    )
    def test_not_recognised(self, message: str) -> None:
        assert message_requests_referenced_content(message) is False, (
            f"Expected False for {message!r}"
        )


# ---------------------------------------------------------------------------
# 2. is_recent_assistant_content_save_request
# ---------------------------------------------------------------------------


class TestIsRecentAssistantContentSaveRequest:
    """Verify that 'add' and 'savee' are treated as save verbs."""

    @pytest.mark.parametrize(
        "message",
        [
            "add that to a note",
            "add this to a note",
            "add it to a note",
            "savee it to a note",
            "savee that to a note",
            "ok add that to a note",
            # Existing phrases must still pass
            "save that to a note",
            "put that in a note",
        ],
    )
    def test_recognised_as_save_request(self, message: str) -> None:
        assert is_recent_assistant_content_save_request(message) is True, (
            f"Expected True for {message!r}"
        )

    @pytest.mark.parametrize(
        "message",
        [
            "tell me a joke",
            "open google.com",
            # "add" alone without a file/note target and reference signal must not trigger
            "add a note about linux",
            "add to the queue",
        ],
    )
    def test_not_a_save_request(self, message: str) -> None:
        assert is_recent_assistant_content_save_request(message) is False, (
            f"Expected False for {message!r}"
        )


# ---------------------------------------------------------------------------
# 3. Trailing control-text stripping — _strip_trailing_control_text
# ---------------------------------------------------------------------------


class TestStripTrailingControlText:
    """Verify conservative stripping of trailing workflow narration."""

    def test_strips_let_me_know_if(self) -> None:
        content = "Here are 20 dad jokes:\n1. joke\nLet me know if you need any more!"
        result = _strip_trailing_control_text(content)
        assert "Let me know if" not in result
        assert "1. joke" in result

    def test_strips_let_me_know_when(self) -> None:
        content = "Here is the summary.\nLet me know when you're ready to submit."
        result = _strip_trailing_control_text(content)
        assert "Let me know when" not in result
        assert "Here is the summary" in result

    def test_strips_would_you_like_me_to(self) -> None:
        content = "Here are the results.\nWould you like me to save this?"
        result = _strip_trailing_control_text(content)
        assert "Would you like me to" not in result
        assert "Here are the results" in result

    def test_strips_is_there_anything_else(self) -> None:
        content = "Done! Here's the output.\nIs there anything else I can help with?"
        result = _strip_trailing_control_text(content)
        assert "Is there anything else" not in result
        assert "Done!" in result

    def test_strips_should_we_refine(self) -> None:
        content = "Here is the draft.\nShould we refine it further?"
        result = _strip_trailing_control_text(content)
        assert "Should we refine" not in result
        assert "Here is the draft" in result

    def test_strips_do_you_want_me_to(self) -> None:
        content = "Here are the jokes.\nDo you want me to save this as a file?"
        result = _strip_trailing_control_text(content)
        assert "Do you want me to" not in result
        assert "Here are the jokes" in result

    def test_strips_nothing_has_been_submitted(self) -> None:
        content = "Preview is ready.\nNothing has been submitted yet."
        result = _strip_trailing_control_text(content)
        assert "Nothing has been submitted" not in result

    def test_strips_trailing_blank_lines_too(self) -> None:
        content = "Main content.\n\nLet me know if you want changes.\n\n"
        result = _strip_trailing_control_text(content)
        assert result == "Main content."

    def test_preserves_mid_content_let_me_know(self) -> None:
        """Only TRAILING occurrences are stripped — mid-content is preserved."""
        content = (
            "First block.\n"
            "Let me know if you liked this.\n"
            "Second block with real content here.\n"
            "Third block final."
        )
        result = _strip_trailing_control_text(content)
        # The trailing line is "Third block final." — not a control phrase — nothing stripped
        assert "Let me know if" in result
        assert "Third block final." in result

    def test_empty_input_returns_empty(self) -> None:
        assert _strip_trailing_control_text("") == ""

    def test_no_control_text_unchanged(self) -> None:
        content = "Here are 20 dad jokes:\n1. joke\n2. joke"
        assert _strip_trailing_control_text(content) == content

    def test_dad_jokes_with_trailing_stripped(self) -> None:
        result = _strip_trailing_control_text(_DAD_JOKES_WITH_TRAILING)
        assert "Let me know if" not in result
        assert "20. Why can't you give Elsa a balloon" in result

    def test_strips_multiple_trailing_control_lines(self) -> None:
        content = (
            "Here is some content.\n"
            "Let me know if you need changes.\n"
            "Would you like me to submit this?"
        )
        result = _strip_trailing_control_text(content)
        assert "Let me know if" not in result
        assert "Would you like me to" not in result
        assert "Here is some content." in result


# ---------------------------------------------------------------------------
# 4. build_saveable_assistant_artifact — artifact content is stripped
# ---------------------------------------------------------------------------


class TestBuildSaveableAssistantArtifact:
    """Verify artifact content stripping and rejection of boilerplate."""

    def test_strips_trailing_control_text_in_artifact(self) -> None:
        artifact = build_saveable_assistant_artifact(_DAD_JOKES_WITH_TRAILING)
        assert artifact is not None
        assert "Let me know if" not in artifact["content"]
        assert "20. Why can't you give Elsa a balloon" in artifact["content"]

    def test_clean_content_stored_intact(self) -> None:
        artifact = build_saveable_assistant_artifact(_DAD_JOKES_CONTENT)
        assert artifact is not None
        assert "20. Why can't you give Elsa a balloon" in artifact["content"]

    def test_preview_boilerplate_rejected(self) -> None:
        boilerplate = (
            "I've prepared a preview of your request. "
            "This is preview-only — nothing has been submitted yet."
        )
        assert build_saveable_assistant_artifact(boilerplate) is None

    def test_submit_status_rejected(self) -> None:
        status_msg = "I submitted the job to VoxeraOS. Job ID: inbox-abc.json"
        assert build_saveable_assistant_artifact(status_msg) is None

    def test_queue_state_rejected(self) -> None:
        assert build_saveable_assistant_artifact("Queue state: running") is None

    def test_empty_input_rejected(self) -> None:
        assert build_saveable_assistant_artifact("") is None

    def test_trivial_ok_rejected(self) -> None:
        assert build_saveable_assistant_artifact("Ok!") is None


# ---------------------------------------------------------------------------
# 5. select_recent_saveable_assistant_artifact — selection with new patterns
# ---------------------------------------------------------------------------


class TestSelectRecentSaveableAssistantArtifact:
    """Verify artifact selection for new recognised phrases."""

    @pytest.mark.parametrize(
        "message",
        [
            "add that to a note",
            "add this to a note",
            "add it to a note",
            "ok add that to a note",
            "savee it to a note",
            "put this in a note",
            "save that to a note",
            "save that to dad-jokes.txt",
            "put that in a note called dad-jokes.txt",
        ],
    )
    def test_returns_most_recent_artifact(self, message: str) -> None:
        result = select_recent_saveable_assistant_artifact(
            message=message,
            assistant_artifacts=[_ARTIFACT_JOKES],
        )
        assert result is not None, f"Expected artifact for {message!r}"
        assert result["content"] == _DAD_JOKES_CONTENT

    def test_returns_none_when_no_artifacts(self) -> None:
        result = select_recent_saveable_assistant_artifact(
            message="add that to a note",
            assistant_artifacts=[],
        )
        assert result is None

    def test_returns_none_for_unrelated_message(self) -> None:
        result = select_recent_saveable_assistant_artifact(
            message="what is the weather today",
            assistant_artifacts=[_ARTIFACT_JOKES],
        )
        assert result is None


# ---------------------------------------------------------------------------
# 6. Full preview payload — _normalize_structured_file_write_payload
# ---------------------------------------------------------------------------


class TestNormalizeStructuredFileWritePayload:
    """Test that the builder produces correct write_file previews."""

    def test_add_that_to_note_with_artifact_creates_preview(self) -> None:
        result = _normalize_structured_file_write_payload(
            "add that to a note",
            assistant_artifacts=[_ARTIFACT_JOKES],
        )
        assert result is not None
        wf = result.get("write_file")
        assert isinstance(wf, dict)
        assert wf["content"] == _DAD_JOKES_CONTENT
        assert wf["path"].startswith("~/VoxeraOS/notes/")
        assert wf["mode"] == "overwrite"

    def test_add_that_to_note_without_artifact_returns_none(self) -> None:
        result = _normalize_structured_file_write_payload(
            "add that to a note",
            assistant_artifacts=[],
        )
        assert result is None

    def test_add_that_with_filename_uses_correct_path(self) -> None:
        result = _normalize_structured_file_write_payload(
            "add that to a note called dad-jokes.txt",
            assistant_artifacts=[_ARTIFACT_JOKES],
        )
        assert result is not None
        wf = result["write_file"]
        assert wf["path"] == "~/VoxeraOS/notes/dad-jokes.txt"
        assert wf["content"] == _DAD_JOKES_CONTENT

    def test_put_this_in_note_named_creates_preview(self) -> None:
        result = _normalize_structured_file_write_payload(
            "put this in a note named dad-jokes.txt",
            assistant_artifacts=[_ARTIFACT_JOKES],
        )
        assert result is not None
        wf = result["write_file"]
        assert wf["path"] == "~/VoxeraOS/notes/dad-jokes.txt"
        assert wf["content"] == _DAD_JOKES_CONTENT

    def test_save_that_to_filename_direct(self) -> None:
        result = _normalize_structured_file_write_payload(
            "save that to dad-jokes.txt",
            assistant_artifacts=[_ARTIFACT_JOKES],
        )
        assert result is not None
        wf = result["write_file"]
        assert wf["path"] == "~/VoxeraOS/notes/dad-jokes.txt"
        assert wf["content"] == _DAD_JOKES_CONTENT

    def test_save_that_to_note_called_filename(self) -> None:
        result = _normalize_structured_file_write_payload(
            "save that to a note called dad-jokes.txt",
            assistant_artifacts=[_ARTIFACT_JOKES],
        )
        assert result is not None
        wf = result["write_file"]
        assert wf["path"] == "~/VoxeraOS/notes/dad-jokes.txt"

    def test_put_that_in_note_called_filename(self) -> None:
        result = _normalize_structured_file_write_payload(
            "put that in a note called dad-jokes.txt",
            assistant_artifacts=[_ARTIFACT_JOKES],
        )
        assert result is not None
        wf = result["write_file"]
        assert wf["path"] == "~/VoxeraOS/notes/dad-jokes.txt"

    def test_savee_typo_with_filename(self) -> None:
        result = _normalize_structured_file_write_payload(
            "savee it to a note called dad-jokes.txt",
            assistant_artifacts=[_ARTIFACT_JOKES],
        )
        assert result is not None
        wf = result["write_file"]
        assert wf["path"] == "~/VoxeraOS/notes/dad-jokes.txt"
        assert wf["content"] == _DAD_JOKES_CONTENT

    def test_savee_typo_without_filename_uses_default_path(self) -> None:
        result = _normalize_structured_file_write_payload(
            "savee it to a note",
            assistant_artifacts=[_ARTIFACT_JOKES],
        )
        assert result is not None
        wf = result["write_file"]
        assert wf["path"].startswith("~/VoxeraOS/notes/note-")
        assert wf["content"] == _DAD_JOKES_CONTENT

    def test_add_that_without_filename_uses_default_path(self) -> None:
        result = _normalize_structured_file_write_payload(
            "add that to a note",
            assistant_artifacts=[_ARTIFACT_JOKES],
        )
        assert result is not None
        wf = result["write_file"]
        assert wf["path"].startswith("~/VoxeraOS/notes/note-")
        assert wf["content"] != ""

    def test_content_is_non_empty(self) -> None:
        result = _normalize_structured_file_write_payload(
            "add that to a note",
            assistant_artifacts=[_ARTIFACT_JOKES],
        )
        assert result is not None
        assert str(result["write_file"].get("content", "")).strip() != ""

    def test_no_auto_submit_field(self) -> None:
        result = _normalize_structured_file_write_payload(
            "add that to a note called dad-jokes.txt",
            assistant_artifacts=[_ARTIFACT_JOKES],
        )
        assert result is not None
        assert "submit" not in str(result).lower() or "auto_submit" not in result


# ---------------------------------------------------------------------------
# 7. maybe_draft_job_payload — end-to-end builder
# ---------------------------------------------------------------------------


class TestMaybeDraftJobPayloadReferencedSave:
    """End-to-end builder test for the referenced-save flow."""

    def test_add_that_to_note_produces_preview(self) -> None:
        result = maybe_draft_job_payload(
            "add that to a note",
            recent_assistant_artifacts=[_ARTIFACT_JOKES],
        )
        assert result is not None
        wf = result.get("write_file")
        assert isinstance(wf, dict)
        assert wf["content"] == _DAD_JOKES_CONTENT
        assert wf["path"].startswith("~/VoxeraOS/notes/")

    def test_add_that_to_note_called_filename(self) -> None:
        result = maybe_draft_job_payload(
            "add that to a note called dad-jokes.txt",
            recent_assistant_artifacts=[_ARTIFACT_JOKES],
        )
        assert result is not None
        wf = result["write_file"]
        assert wf["path"] == "~/VoxeraOS/notes/dad-jokes.txt"
        assert wf["content"] == _DAD_JOKES_CONTENT

    def test_ok_add_that_to_note_produces_preview(self) -> None:
        result = maybe_draft_job_payload(
            "ok add that to a note",
            recent_assistant_artifacts=[_ARTIFACT_JOKES],
        )
        assert result is not None
        assert "write_file" in result

    def test_savee_it_to_note_produces_preview(self) -> None:
        result = maybe_draft_job_payload(
            "savee it to a note",
            recent_assistant_artifacts=[_ARTIFACT_JOKES],
        )
        assert result is not None
        wf = result.get("write_file")
        assert isinstance(wf, dict)
        assert wf["content"] == _DAD_JOKES_CONTENT

    def test_put_this_in_note_named_produces_preview(self) -> None:
        result = maybe_draft_job_payload(
            "put this in a note named dad-jokes.txt",
            recent_assistant_artifacts=[_ARTIFACT_JOKES],
        )
        assert result is not None
        wf = result["write_file"]
        assert wf["path"] == "~/VoxeraOS/notes/dad-jokes.txt"

    def test_no_artifact_returns_none_not_empty_preview(self) -> None:
        result = maybe_draft_job_payload(
            "save that to a note",
            recent_assistant_artifacts=[],
        )
        assert result is None, "Expected fail-closed None when no saveable artifact exists"

    def test_add_that_no_artifact_returns_none(self) -> None:
        result = maybe_draft_job_payload(
            "add that to a note",
            recent_assistant_artifacts=[],
        )
        assert result is None, "Expected fail-closed None when no saveable artifact exists"

    def test_preview_has_correct_mode(self) -> None:
        result = maybe_draft_job_payload(
            "add that to a note called dad-jokes.txt",
            recent_assistant_artifacts=[_ARTIFACT_JOKES],
        )
        assert result is not None
        assert result["write_file"]["mode"] == "overwrite"

    def test_recent_assistant_messages_path_produces_preview(self) -> None:
        """Verifies the collect_recent_saveable_assistant_artifacts pipeline is wired."""
        result = maybe_draft_job_payload(
            "add that to a note called dad-jokes.txt",
            recent_assistant_artifacts=None,
            recent_assistant_messages=[_DAD_JOKES_CONTENT],
        )
        assert result is not None
        wf = result["write_file"]
        assert wf["path"] == "~/VoxeraOS/notes/dad-jokes.txt"
        assert "bicycle" in wf["content"]

    def test_recent_assistant_messages_boilerplate_not_saved(self) -> None:
        """Boilerplate in recent_assistant_messages must not become preview content."""
        boilerplate = (
            "I've prepared a preview. This is preview-only — nothing has been submitted yet."
        )
        result = maybe_draft_job_payload(
            "add that to a note",
            recent_assistant_artifacts=None,
            recent_assistant_messages=[boilerplate],
        )
        assert result is None, "Boilerplate-only message must not produce a preview"


# ---------------------------------------------------------------------------
# 8. Wrapper stripping — content from dad-jokes saved correctly
# ---------------------------------------------------------------------------


class TestWrapperStrippingOnSave:
    """Verify that trailing control phrases are stripped from saved content."""

    def test_saved_content_excludes_let_me_know_trailing(self) -> None:
        artifact = build_saveable_assistant_artifact(_DAD_JOKES_WITH_TRAILING)
        assert artifact is not None
        result = _normalize_structured_file_write_payload(
            "save that to a note called dad-jokes.txt",
            assistant_artifacts=[artifact],
        )
        assert result is not None
        wf = result["write_file"]
        assert "Let me know if" not in wf["content"]
        assert "20. Why can't you give Elsa a balloon" in wf["content"]

    def test_saved_content_includes_jokes(self) -> None:
        artifact = build_saveable_assistant_artifact(_DAD_JOKES_WITH_TRAILING)
        assert artifact is not None
        result = _normalize_structured_file_write_payload(
            "add that to a note called dad-jokes.txt",
            assistant_artifacts=[artifact],
        )
        assert result is not None
        wf = result["write_file"]
        assert "1. Why did the bicycle fall over" in wf["content"]
        assert "20. Why can't you give Elsa a balloon" in wf["content"]


# ---------------------------------------------------------------------------
# 9. Wrapper/status exclusion — boilerplate not saved
# ---------------------------------------------------------------------------


class TestBoilerplateExclusion:
    """Boilerplate/status assistant messages must not become saveable artifacts."""

    @pytest.mark.parametrize(
        "boilerplate",
        [
            "I've prepared a preview of your request. This is preview-only — nothing has been submitted yet.",
            "I prepared a governed save-to-note preview. Nothing has been submitted.",
            "Your linked write_file job completed successfully.",
            "I submitted the job to VoxeraOS. Job ID: inbox-abc.json. The request is now in the queue.",
            "Prepared preview — this is preview-only.",
            "Nothing has been submitted yet. I still have the current request ready.",
        ],
    )
    def test_boilerplate_not_stored_as_artifact(self, boilerplate: str) -> None:
        artifact = build_saveable_assistant_artifact(boilerplate)
        assert artifact is None, f"Expected None for boilerplate: {boilerplate[:60]!r}"

    def test_meaningful_content_stored_when_boilerplate_follows(self) -> None:
        """Meaningful content before boilerplate: meaningful content is the artifact."""
        meaningful = build_saveable_assistant_artifact(_DAD_JOKES_CONTENT)
        boilerplate_artifact = build_saveable_assistant_artifact(
            "I've prepared a preview. This is preview-only."
        )
        assert meaningful is not None
        assert boilerplate_artifact is None

        # Referenced save uses meaningful content (not the excluded boilerplate)
        result = select_recent_saveable_assistant_artifact(
            message="save that to wrapper-test.txt",
            assistant_artifacts=[meaningful],
        )
        assert result is not None
        assert result["content"] == meaningful["content"]
        assert "preview-only" not in result["content"]
        assert "I've prepared" not in result["content"]


# ---------------------------------------------------------------------------
# 10. Submit smoke — preview payload shape is submit-ready
# ---------------------------------------------------------------------------


class TestSubmitSmoke:
    """Verify the preview payload produced by referenced-save is submit-ready."""

    def test_preview_payload_has_required_fields(self) -> None:
        result = maybe_draft_job_payload(
            "add that to a note called dad-jokes.txt",
            recent_assistant_artifacts=[_ARTIFACT_JOKES],
        )
        assert result is not None
        assert "goal" in result
        assert "write_file" in result
        wf = result["write_file"]
        assert "path" in wf
        assert "content" in wf
        assert "mode" in wf

    def test_no_auto_submit_in_payload(self) -> None:
        result = maybe_draft_job_payload(
            "add that to a note called dad-jokes.txt",
            recent_assistant_artifacts=[_ARTIFACT_JOKES],
        )
        assert result is not None
        # Payload must not contain auto-submit or enqueue fields
        assert result.get("auto_submit") is not True
        assert "enqueue" not in result or result.get("enqueue") is not True

    def test_preview_content_matches_referenced_answer(self) -> None:
        result = maybe_draft_job_payload(
            "save that to dad-jokes.txt",
            recent_assistant_artifacts=[_ARTIFACT_JOKES],
        )
        assert result is not None
        wf = result["write_file"]
        assert wf["content"] == _DAD_JOKES_CONTENT
