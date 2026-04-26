"""Tests for active write_file preview content edits being applied truthfully.

Covers:
1.  Additive edits mutate preview content directly
2.  Phrase variants for additive intent are routed correctly
3.  No false success when content generation fails
4.  Pending suggestion apply ("add them please") with seeded suggestion
5.  Count truth — explicit wrong-count claims are prevented
6.  Wrapper/control text is excluded from updated content
7.  Non-interference with submit, rename, and inspect commands
"""

from __future__ import annotations

import pytest

from voxera.vera.draft_revision import (
    _generate_additional_items,
    _parse_additive_count,
    interpret_active_preview_draft_revision,
    is_active_preview_additive_edit_request,
    is_apply_pending_suggestion_request,
    refined_content_from_active_preview,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FIVE_JOKES = (
    "Why did the scarecrow win an award? Because he was outstanding in his field.\n\n"
    "I told my wife she was drawing her eyebrows too high. She looked surprised.\n\n"
    "Why don't scientists trust atoms? Because they make up everything.\n\n"
    "What do you call a fish without eyes? A fsh.\n\n"
    "I used to hate facial hair, but then it grew on me."
)

_JOKE_PREVIEW = {
    "goal": "write a file called jokarakira.txt with provided content",
    "write_file": {
        "path": "~/VoxeraOS/notes/jokarakira.txt",
        "content": _FIVE_JOKES,
        "mode": "overwrite",
    },
}

_EMPTY_PREVIEW = {
    "goal": "write a file called notes.txt with provided content",
    "write_file": {
        "path": "~/VoxeraOS/notes/notes.txt",
        "content": "",
        "mode": "overwrite",
    },
}


# ---------------------------------------------------------------------------
# 1. Active preview additive edit applies directly
# ---------------------------------------------------------------------------


def test_additive_edit_appends_to_preview():
    """'add 5 more jokes' produces merged content (original + new jokes)."""
    result = interpret_active_preview_draft_revision(
        "add 5 more jokes",
        _JOKE_PREVIEW,
    )
    assert result is not None
    wf = result.get("write_file", {})
    new_content = wf.get("content", "")
    # Path preserved
    assert wf.get("path") == "~/VoxeraOS/notes/jokarakira.txt"
    # Original content still present
    assert "outstanding in his field" in new_content
    # Content is longer than original (additional jokes added)
    assert len(new_content) > len(_FIVE_JOKES)


def test_additive_edit_can_you_prefix():
    """'can you add 5 more jokes' (with polite prefix) is also handled."""
    result = interpret_active_preview_draft_revision(
        "can you add 5 more jokes",
        _JOKE_PREVIEW,
    )
    assert result is not None
    wf = result.get("write_file", {})
    assert len(str(wf.get("content", ""))) > len(_FIVE_JOKES)


def test_additive_edit_preserves_path():
    """Path must not change after additive content edit."""
    result = interpret_active_preview_draft_revision(
        "add 5 more jokes",
        _JOKE_PREVIEW,
    )
    assert result is not None
    assert result["write_file"]["path"] == "~/VoxeraOS/notes/jokarakira.txt"


def test_additive_edit_content_has_no_wrapper_text():
    """Updated write_file.content must not include wrapper/status narration."""
    result = interpret_active_preview_draft_revision(
        "add 5 more jokes",
        _JOKE_PREVIEW,
    )
    assert result is not None
    content = result["write_file"]["content"]
    bad_phrases = [
        "i've updated",
        "the draft is still",
        "nothing has been submitted",
        "let me know",
        "would you like to submit",
        "take a look at the preview",
        "added jokes:",
    ]
    content_lower = content.lower()
    for phrase in bad_phrases:
        assert phrase not in content_lower, f"Wrapper phrase found: {phrase!r}"


# ---------------------------------------------------------------------------
# 2. Additive phrase variants route as content edits
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase",
    [
        "add 5 more jokes to the content",
        "please add 5 more dad jokes to the list",
        "please add 5 more dad jokes to the note content",
        "append 3 more bullets",
        "continue the list",
        "add more jokes",
        "append more items",
        "make it longer",
    ],
)
def test_additive_intent_detection(phrase):
    """Additive phrases are detected by is_active_preview_additive_edit_request."""
    assert is_active_preview_additive_edit_request(phrase), f"Not detected: {phrase!r}"


@pytest.mark.parametrize(
    "phrase",
    [
        "add 5 more jokes to the content",
        "please add 5 more dad jokes to the list",
        "append 3 more bullets",
        "continue the list",
        "make it longer",
    ],
)
def test_additive_phrase_variants_produce_edit(phrase):
    """Additive phrase variants produce a preview update, not a stale status."""
    result = interpret_active_preview_draft_revision(phrase, _JOKE_PREVIEW)
    assert result is not None, f"No revision for phrase: {phrase!r}"
    wf = result.get("write_file", {})
    assert len(str(wf.get("content", ""))) > len(_FIVE_JOKES)


# ---------------------------------------------------------------------------
# 3. No false success when generation fails
# ---------------------------------------------------------------------------


def test_no_false_success_on_exhausted_pool():
    """Even if the pool is exhausted, result is None or content unchanged — not a false update."""
    # Build a preview that already contains all known jokes
    from voxera.vera.draft_revision import _JOKE_POOL

    all_jokes_content = "\n\n".join(_JOKE_POOL)
    full_preview = {
        "goal": "write a file called alljokes.txt with provided content",
        "write_file": {
            "path": "~/VoxeraOS/notes/alljokes.txt",
            "content": all_jokes_content,
            "mode": "overwrite",
        },
    }
    result = interpret_active_preview_draft_revision("add 5 more jokes", full_preview)
    # When pool is exhausted, result may be None or the content may be unchanged/recycled.
    # Either is acceptable; what is NOT acceptable is returning empty or shorter content.
    if result is not None:
        wf = result.get("write_file", {})
        new_content = str(wf.get("content", ""))
        # Must not be shorter than original
        assert len(new_content) >= len(all_jokes_content)


# ---------------------------------------------------------------------------
# 4. Pending suggestion apply ("add them please")
# ---------------------------------------------------------------------------


def test_apply_pending_suggestion_detection():
    """Phrases that should trigger pending suggestion application."""
    apply_phrases = [
        "add them please",
        "yes add those",
        "apply those",
        "apply them",
        "apply that",
        "put those in the note",
        "update the preview with those",
        "add those to the content",
        "use those",
    ]
    for phrase in apply_phrases:
        assert is_apply_pending_suggestion_request(phrase), f"Not detected: {phrase!r}"


def test_not_apply_pending_suggestion():
    """Normal edit phrases should NOT trigger pending suggestion application."""
    non_apply_phrases = [
        "add 5 more jokes",
        "submit it",
        "save that to a note",
        "rename it to foo.txt",
        "where is the content?",
    ]
    for phrase in non_apply_phrases:
        assert not is_apply_pending_suggestion_request(phrase), f"False positive: {phrase!r}"


# ---------------------------------------------------------------------------
# 5. Count truth — no explicit wrong-count claims
# ---------------------------------------------------------------------------


def test_parse_additive_count_digit():
    assert _parse_additive_count("add 5 more jokes") == 5


def test_parse_additive_count_word():
    assert _parse_additive_count("add five more jokes") == 5


def test_parse_additive_count_three():
    assert _parse_additive_count("append three more bullets") == 3


def test_parse_additive_count_default():
    assert _parse_additive_count("continue the list") == 3  # default


def test_additive_count_not_doubled():
    """Requesting 5 should not produce 10 or 20 items."""
    additional = _generate_additional_items("joke", "", 5)
    assert additional is not None
    # Count distinct items (split on double newline for jokes)
    items = [i.strip() for i in additional.split("\n\n") if i.strip()]
    # Should be at most 5 + a few (never 10 or 20 unless pool forces recycling)
    assert len(items) <= 5


def test_additive_count_ten():
    """Requesting 10 should not claim 20."""
    additional = _generate_additional_items("joke", "", 10)
    if additional is not None:
        items = [i.strip() for i in additional.split("\n\n") if i.strip()]
        assert len(items) <= 10


# ---------------------------------------------------------------------------
# 6. Wrapper/control text exclusion
# ---------------------------------------------------------------------------


def test_wrapper_text_not_in_updated_content():
    """Updated preview content must not contain wrapper/status narration phrases."""
    result = interpret_active_preview_draft_revision(
        "add 5 more jokes",
        _JOKE_PREVIEW,
    )
    assert result is not None
    content = result["write_file"]["content"].lower()
    forbidden = [
        "i've updated",
        "the draft is still in the preview",
        "nothing has been submitted",
        "let me know when",
        "would you like to submit",
        "take a look at the preview pane",
        "added jokes:",
    ]
    for phrase in forbidden:
        assert phrase not in content, f"Wrapper phrase in content: {phrase!r}"


def test_empty_preview_additive_edit():
    """Additive edit on an empty preview creates content from scratch."""
    result = interpret_active_preview_draft_revision("add 5 more jokes", _EMPTY_PREVIEW)
    assert result is not None
    wf = result.get("write_file", {})
    content = str(wf.get("content", ""))
    assert content.strip()
    assert "outstanding in his field" in content or len(content.split()) >= 4


# ---------------------------------------------------------------------------
# 7. Non-interference with existing commands
# ---------------------------------------------------------------------------


def test_submit_intent_not_treated_as_additive():
    """'submit it' must not be detected as an additive edit."""
    assert not is_active_preview_additive_edit_request("submit it")


def test_rename_not_treated_as_additive():
    """'rename it to foo.txt' must not be detected as additive."""
    assert not is_active_preview_additive_edit_request("rename it to foo.txt")


def test_save_as_not_treated_as_additive():
    """'save it as foo.txt' must not be detected as additive."""
    assert not is_active_preview_additive_edit_request("save it as foo.txt")


def test_inspect_not_treated_as_additive():
    """'where is the content?' must not be detected as additive."""
    assert not is_active_preview_additive_edit_request("where is the content?")


def test_save_to_note_not_treated_as_additive():
    """'save that to a note called x.txt' must not be detected as additive."""
    assert not is_active_preview_additive_edit_request("save that to a note called x.txt")


def test_rename_still_works_after_additive_support():
    """Rename command still produces a path change after additive support added."""
    result = interpret_active_preview_draft_revision(
        "rename it to newname.txt",
        _JOKE_PREVIEW,
    )
    assert result is not None
    wf = result.get("write_file", {})
    assert "newname.txt" in str(wf.get("path", ""))
    # Content preserved during rename
    assert "outstanding in his field" in str(wf.get("content", ""))


def test_make_it_shorter_still_works():
    """'make it shorter' transform still applies correctly."""
    result = interpret_active_preview_draft_revision(
        "make it shorter",
        _JOKE_PREVIEW,
    )
    assert result is not None
    wf = result.get("write_file", {})
    new_content = str(wf.get("content", ""))
    assert len(new_content) < len(_FIVE_JOKES)


def test_transform_to_checklist_still_works():
    """'turn it into a checklist' transform still works."""
    preview = {
        "goal": "write a file called steps.txt with provided content",
        "write_file": {
            "path": "~/VoxeraOS/notes/steps.txt",
            "content": "Step one. Step two. Step three.",
            "mode": "overwrite",
        },
    }
    result = interpret_active_preview_draft_revision(
        "turn it into a checklist",
        preview,
    )
    assert result is not None
    content = result["write_file"]["content"]
    assert "- " in content


# ---------------------------------------------------------------------------
# 8. Refined content from active preview — additive path directly
# ---------------------------------------------------------------------------


def test_refined_content_from_active_preview_additive():
    """refined_content_from_active_preview appends additional content for additive requests."""
    existing = "Why did the scarecrow win an award? Because he was outstanding in his field."
    result = refined_content_from_active_preview(
        text="add 5 more jokes",
        lowered="add 5 more jokes",
        existing_content=existing,
    )
    assert result is not None
    assert existing in result
    assert len(result) > len(existing)


def test_refined_content_additive_fact():
    """Additive fact requests produce additional fact content."""
    existing = "Honey never spoils."
    result = refined_content_from_active_preview(
        text="add 3 more facts",
        lowered="add 3 more facts",
        existing_content=existing,
    )
    assert result is not None
    assert existing in result


def test_refined_content_additive_appends_not_replaces():
    """Additive edits append to existing content, not replace it."""
    existing = "Original joke content here."
    result = refined_content_from_active_preview(
        text="add more jokes",
        lowered="add more jokes",
        existing_content=existing,
    )
    assert result is not None
    assert existing in result, "Original content was replaced instead of appended to"


# ---------------------------------------------------------------------------
# 9. No hijack when no active preview exists
# ---------------------------------------------------------------------------


def test_additive_edit_no_active_preview_returns_none():
    """interpret_active_preview_draft_revision returns None when active_preview is None."""
    result = interpret_active_preview_draft_revision("add 5 more jokes", None)
    assert result is None, "Additive edit must not create a preview from nothing"


def test_apply_pending_no_active_preview_returns_none():
    """is_apply_pending_suggestion_request does not create state by itself — no side effects."""
    # Just detection — the actual guard is in app.py (isinstance(pending_preview, dict))
    assert is_apply_pending_suggestion_request("add them please")
    # With None preview, interpret_active_preview_draft_revision also returns None
    result = interpret_active_preview_draft_revision("add them please", None)
    assert result is None


# ---------------------------------------------------------------------------
# 10. Content-type detection handles plural forms
# ---------------------------------------------------------------------------


def test_detect_content_type_jokes_plural():
    """'add 3 more facts' must detect content type 'fact', not fall back to 'item'."""
    from voxera.vera.draft_revision import _detect_additive_content_type

    assert _detect_additive_content_type("add 3 more facts") == "fact"
    assert _detect_additive_content_type("add 3 more fact") == "fact"


def test_detect_content_type_poems_plural():
    """'add more poems' must detect content type 'poem'."""
    from voxera.vera.draft_revision import _detect_additive_content_type

    assert _detect_additive_content_type("add more poems") == "poem"
    assert _detect_additive_content_type("add more poem") == "poem"


def test_detect_content_type_jokes_plural_generates_jokes():
    """'add 3 more facts' routes to fact pool, not generic item pool."""
    from voxera.vera.draft_revision import _FACT_POOL, _generate_additional_items

    result = _generate_additional_items("fact", "", 2)
    assert result is not None
    # Fact pool items use sentence form, not bullet "- " prefix
    assert result[0] != "-", "Fact items should not be bullet-prefixed"
    # Verify result contains items from the fact pool
    assert any(fact in result for fact in _FACT_POOL)
