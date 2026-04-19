"""Tests for ``normalize_text_for_tts``.

The helper is the single bounded place where assistant reply text is
rewritten for TTS.  These tests pin the invariants that matter for
both speech quality and canonical-text preservation:

1. Formatting markers (headings, bold, italics, inline code, bullets,
   numbered lists, blockquotes, horizontal rules, fences) are stripped
   from the speech copy.
2. Underlying content words are preserved -- this is a conservative
   wrapper-stripping transform, not a paraphrase.
3. Plain text is returned unchanged (aside from cosmetic whitespace
   trimming) so normal replies are not rewritten.
4. The helper is pure and does not mutate its input -- the canonical
   reply text that lives in the session / UI is unchanged.
5. Degenerate inputs (empty, whitespace, format-only) still return a
   truthful non-empty string when any characters existed so the TTS
   request contract (non-empty text) is honoured downstream.
"""

from __future__ import annotations

import pytest

from voxera.voice.speech_normalize import normalize_text_for_tts

# ---------------------------------------------------------------------------
# 1. Headings -- '#' markers are not spoken.
# ---------------------------------------------------------------------------


class TestHeadingNormalization:
    def test_h1_marker_stripped(self) -> None:
        result = normalize_text_for_tts("# Daily status")
        assert "#" not in result
        assert "Daily status" in result

    def test_h2_marker_stripped(self) -> None:
        result = normalize_text_for_tts("## Summary of findings")
        assert "#" not in result
        assert "Summary of findings" in result

    def test_h3_marker_stripped(self) -> None:
        result = normalize_text_for_tts("### Next steps")
        assert "#" not in result
        assert "Next steps" in result

    def test_multiple_headings_retain_words(self) -> None:
        source = "# Overview\nSome body.\n## Details\nMore body."
        result = normalize_text_for_tts(source)
        assert "#" not in result
        assert "Overview" in result
        assert "Some body." in result
        assert "Details" in result
        assert "More body." in result

    def test_hashtag_inside_word_is_not_a_heading(self) -> None:
        # '#channel' in the middle of a sentence is not an ATX heading
        # (no required whitespace after the '#' at line start) -- the
        # pattern requires a line-start position, so a hashtag in the
        # middle of prose should remain unchanged.
        result = normalize_text_for_tts("Mention #channel in the chat.")
        assert "#channel" in result


# ---------------------------------------------------------------------------
# 2. Bold and italic wrappers are removed; content is preserved.
# ---------------------------------------------------------------------------


class TestEmphasisNormalization:
    def test_bold_stars_stripped(self) -> None:
        result = normalize_text_for_tts("This is **really important** today.")
        assert "**" not in result
        assert "really important" in result

    def test_bold_underscores_stripped(self) -> None:
        result = normalize_text_for_tts("This is __really important__ today.")
        assert "__" not in result
        assert "really important" in result

    def test_italic_star_stripped(self) -> None:
        result = normalize_text_for_tts("This is *a note* from earlier.")
        assert "*a note*" not in result
        assert "a note" in result

    def test_italic_underscore_stripped(self) -> None:
        result = normalize_text_for_tts("This is _a note_ from earlier.")
        assert "_a note_" not in result
        assert "a note" in result

    def test_bold_and_italic_together(self) -> None:
        result = normalize_text_for_tts("**Bold** and *italic* both kept.")
        assert "**" not in result
        assert "*" not in result
        assert "Bold" in result
        assert "italic" in result


# ---------------------------------------------------------------------------
# 3. Inline code backticks are removed, content preserved.
# ---------------------------------------------------------------------------


class TestInlineCodeNormalization:
    def test_single_backtick_removed(self) -> None:
        result = normalize_text_for_tts("Run `make test` to verify.")
        assert "`" not in result
        assert "make test" in result

    def test_multiple_backticks_removed(self) -> None:
        result = normalize_text_for_tts("Use `ls` then `pwd` then `cd`.")
        assert "`" not in result
        assert "ls" in result
        assert "pwd" in result
        assert "cd" in result

    def test_triple_backtick_fence_line_removed(self) -> None:
        source = "Example:\n```python\nprint('hi')\n```\nDone."
        result = normalize_text_for_tts(source)
        assert "```" not in result
        # The code content survives; the fence lines do not.
        assert "print('hi')" in result
        assert "Done." in result


# ---------------------------------------------------------------------------
# 4. Bullets and numbered lists become speech-friendly.
# ---------------------------------------------------------------------------


class TestBulletAndListNormalization:
    def test_dash_bullet_marker_removed(self) -> None:
        source = "- first item\n- second item\n- third item"
        result = normalize_text_for_tts(source)
        for line in result.splitlines():
            # The leading '-' followed by a space should not survive.
            assert not line.lstrip().startswith("- ")
        assert "first item" in result
        assert "second item" in result
        assert "third item" in result

    def test_star_bullet_marker_removed(self) -> None:
        source = "* alpha\n* beta"
        result = normalize_text_for_tts(source)
        for line in result.splitlines():
            assert not line.lstrip().startswith("* ")
        assert "alpha" in result
        assert "beta" in result

    def test_plus_bullet_marker_removed(self) -> None:
        source = "+ gamma\n+ delta"
        result = normalize_text_for_tts(source)
        for line in result.splitlines():
            assert not line.lstrip().startswith("+ ")
        assert "gamma" in result
        assert "delta" in result

    def test_numbered_list_prefix_removed(self) -> None:
        source = "1. first\n2. second\n3. third"
        result = normalize_text_for_tts(source)
        for line in result.splitlines():
            stripped = line.lstrip()
            # No line should still start with "N. ".
            assert not (len(stripped) >= 3 and stripped[0].isdigit() and stripped[1] == ".")
        assert "first" in result
        assert "second" in result
        assert "third" in result

    def test_parenthesis_numbered_list_prefix_removed(self) -> None:
        source = "1) first\n2) second"
        result = normalize_text_for_tts(source)
        assert "1) first" not in result
        assert "2) second" not in result
        assert "first" in result
        assert "second" in result


# ---------------------------------------------------------------------------
# 5. Plain text is preserved cleanly.
# ---------------------------------------------------------------------------


class TestPlainTextPreservation:
    def test_plain_prose_unchanged(self) -> None:
        source = "The queue is healthy and latency is within budget."
        assert normalize_text_for_tts(source) == source

    def test_punctuation_preserved(self) -> None:
        source = "Yes, the run passed! But check: one warning, one skip."
        assert normalize_text_for_tts(source) == source

    def test_numbers_and_urls_not_rewritten(self) -> None:
        source = "See https://example.com for 42 recent updates."
        assert normalize_text_for_tts(source) == source

    def test_sentence_wording_unchanged(self) -> None:
        # Paraphrase / summarization must not happen.
        source = "Vera drafted a preview. The operator approved it."
        assert normalize_text_for_tts(source) == source


# ---------------------------------------------------------------------------
# 6. Input is never mutated; canonical text stays intact.
# ---------------------------------------------------------------------------


class TestInputPreservation:
    def test_input_string_not_mutated(self) -> None:
        original = "# Heading\n- item **bold**"
        # Python strings are immutable, but we still pin the canonical
        # contract: the caller's reference is unchanged after the call.
        before = original
        _ = normalize_text_for_tts(original)
        assert original == before

    def test_returned_value_is_string(self) -> None:
        assert isinstance(normalize_text_for_tts("hello"), str)

    def test_returned_value_differs_from_input_only_when_formatting_present(self) -> None:
        plain = "Plain reply with no formatting."
        assert normalize_text_for_tts(plain) == plain
        formatted = "**bold** reply"
        assert normalize_text_for_tts(formatted) != formatted


# ---------------------------------------------------------------------------
# 7. Degenerate inputs -- empty / whitespace / format-only.
# ---------------------------------------------------------------------------


class TestDegenerateInputs:
    def test_empty_string_returns_empty(self) -> None:
        assert normalize_text_for_tts("") == ""

    def test_none_returns_empty(self) -> None:
        # Callers occasionally hand in an Optional; we accept it
        # defensively and fall through to empty.
        assert normalize_text_for_tts(None) == ""  # type: ignore[arg-type]

    def test_whitespace_only_returns_empty(self) -> None:
        assert normalize_text_for_tts("   \n  \t") == ""

    def test_formatting_only_returns_truthful_fallback(self) -> None:
        # If the entire input was formatting characters that got
        # stripped, we must return *something* non-empty so the
        # downstream build_tts_request non-empty check does not then
        # fail.  The fallback is the original text stripped.
        result = normalize_text_for_tts("###")
        assert result != ""
        # Either "###" (if nothing else matched) or an empty-after-strip
        # fallback is acceptable.  The key property is: the caller
        # still has something to synthesize or a clean empty signal.
        assert result.strip() != "" or result == ""


# ---------------------------------------------------------------------------
# 8. Combined realistic Vera reply.
# ---------------------------------------------------------------------------


class TestRealisticVeraReply:
    def test_formatted_informational_reply_reads_naturally(self) -> None:
        source = (
            "## Run summary\n"
            "\n"
            "The queue completed **3 jobs** in the last hour.\n"
            "\n"
            "Highlights:\n"
            "- first job: success\n"
            "- second job: success with 1 warning\n"
            "- third job: success\n"
            "\n"
            "Next step: run `make validation-check` to confirm."
        )
        result = normalize_text_for_tts(source)
        # No formatting syntax survives.
        for marker in ("##", "**", "`", "- "):
            assert marker not in result
        # All spoken content words survive.
        for phrase in (
            "Run summary",
            "3 jobs",
            "first job: success",
            "second job: success with 1 warning",
            "third job: success",
            "make validation-check",
        ):
            assert phrase in result

    def test_blockquote_prefix_removed(self) -> None:
        source = "> quoted line\n> another quote"
        result = normalize_text_for_tts(source)
        assert ">" not in result
        assert "quoted line" in result
        assert "another quote" in result

    def test_horizontal_rule_removed(self) -> None:
        source = "Top half.\n\n---\n\nBottom half."
        result = normalize_text_for_tts(source)
        assert "---" not in result
        assert "Top half." in result
        assert "Bottom half." in result


# ---------------------------------------------------------------------------
# 9. Determinism.
# ---------------------------------------------------------------------------


class TestDeterminism:
    @pytest.mark.parametrize(
        "source",
        [
            "# Heading",
            "- one\n- two",
            "**bold** and *italic*",
            "`code span`",
            "plain text",
        ],
    )
    def test_stable_across_calls(self, source: str) -> None:
        first = normalize_text_for_tts(source)
        second = normalize_text_for_tts(source)
        third = normalize_text_for_tts(source)
        assert first == second == third


# ---------------------------------------------------------------------------
# 10. Semantic-drift regression guards.
#
# Real-world assistant replies contain arithmetic, glob patterns, URLs,
# file paths, and code-like tokens that use '*' and '_' in ways that
# *look* like markdown emphasis but are not.  These tests pin that
# the conservative wrapper requires no whitespace inside the
# delimiters (CommonMark rule), so these expressions pass through
# untouched.  If a future change re-introduces spaced-asterisk
# stripping, these tests will catch it.
# ---------------------------------------------------------------------------


class TestSemanticDriftGuards:
    def test_multiplication_with_spaced_asterisk_preserved(self) -> None:
        source = "The answer is 2 * 3 = 6 exactly."
        assert normalize_text_for_tts(source) == source

    def test_multi_factor_multiplication_preserved(self) -> None:
        source = "Compute 2 * 3 * 4 = 24 quickly."
        assert normalize_text_for_tts(source) == source

    def test_python_exponent_operator_preserved(self) -> None:
        source = "Use 2 ** 10 = 1024 as the shift factor."
        assert normalize_text_for_tts(source) == source

    def test_glob_pattern_preserved(self) -> None:
        source = "Match files with * and ? in the pattern."
        assert normalize_text_for_tts(source) == source

    def test_url_with_underscores_preserved(self) -> None:
        source = "See https://example.com/my_file_name.html for the spec."
        assert normalize_text_for_tts(source) == source

    def test_snake_case_identifier_preserved(self) -> None:
        source = "Call helper_function_name inside some_module_name."
        assert normalize_text_for_tts(source) == source

    def test_file_path_preserved(self) -> None:
        source = "Edit /var/log/syslog on the host."
        assert normalize_text_for_tts(source) == source

    def test_angle_bracket_html_like_preserved(self) -> None:
        # Angle brackets mid-line are not blockquotes and must pass through.
        source = "Use <div> and </div> to wrap content."
        assert normalize_text_for_tts(source) == source

    def test_unicode_bullet_preserved(self) -> None:
        # A real unicode bullet ("•") is not a markdown bullet and
        # must not be mistaken for one.
        source = "The • symbol is not a markdown bullet."
        assert normalize_text_for_tts(source) == source

    def test_spaced_bold_delimiters_left_alone(self) -> None:
        # Not valid CommonMark bold -- the delimiters sit next to
        # whitespace -- so they must not be treated as bold wrappers.
        source = "Run ** foo ** as literal text."
        assert normalize_text_for_tts(source) == source

    def test_spaced_italic_delimiters_left_alone(self) -> None:
        source = "Put * here * exactly as written."
        assert normalize_text_for_tts(source) == source
