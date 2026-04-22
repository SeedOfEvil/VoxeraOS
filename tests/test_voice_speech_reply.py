"""Tests for the speech-optimized reply shaper and sentence splitter.

The helpers in :mod:`voxera.voice.speech_reply` produce a **derived**
spoken copy of the assistant text.  These tests pin the invariants
that matter for trust and for the sentence-first TTS pipeline:

1. The full written reply is never mutated — the helper returns a
   new string derived from a prefix of the normalized text.
2. Concise replies pass through without truncation; long replies are
   truncated at a sentence boundary so the speaker never hears a
   half-sentence.
3. Splitting is deterministic, preserves order, and behaves sensibly
   on abbreviations, whitespace-only input, and formatting-only
   input.
4. The shape metadata (``sentence_count``, ``truncated``) is
   truthful — callers depend on these for instrumentation and UI
   hints.
"""

from __future__ import annotations

from voxera.voice.speech_reply import (
    DEFAULT_SPEECH_MAX_CHARS,
    DEFAULT_SPEECH_MAX_SENTENCES,
    SpeechReplyShape,
    prepare_speech_reply,
    split_into_sentences,
)

# =============================================================================
# Section 1: split_into_sentences
# =============================================================================


class TestSplitIntoSentencesBasic:
    """Basic splitting behaviour — order preserved, terminators respected."""

    def test_empty_input_returns_empty_list(self) -> None:
        assert split_into_sentences("") == []

    def test_whitespace_only_returns_empty_list(self) -> None:
        assert split_into_sentences("   \n\t  ") == []

    def test_none_safe_on_falsy_input(self) -> None:
        # The helper accepts any stringifiable input; a None-ish call
        # is treated like empty by the early guard.
        assert split_into_sentences(None) == []  # type: ignore[arg-type]

    def test_single_sentence_no_terminator(self) -> None:
        assert split_into_sentences("hello world") == ["hello world"]

    def test_three_periods(self) -> None:
        assert split_into_sentences("One. Two. Three.") == [
            "One.",
            "Two.",
            "Three.",
        ]

    def test_mixed_terminators(self) -> None:
        assert split_into_sentences("Hi! How are you? I am fine.") == [
            "Hi!",
            "How are you?",
            "I am fine.",
        ]

    def test_preserves_order(self) -> None:
        source = "First. Second. Third. Fourth."
        assert split_into_sentences(source) == [
            "First.",
            "Second.",
            "Third.",
            "Fourth.",
        ]

    def test_single_chunk_for_unterminated_input(self) -> None:
        # When no terminator is present we still return a chunk — the
        # sentence-first TTS path must not crash on "just one short
        # answer" replies.
        assert split_into_sentences("ok") == ["ok"]


class TestSplitIntoSentencesAbbreviations:
    """Common abbreviations must not create spurious sentence boundaries."""

    def test_dr_abbreviation_preserved(self) -> None:
        chunks = split_into_sentences("Ask Dr. Smith about it. Then wait.")
        assert chunks == ["Ask Dr. Smith about it.", "Then wait."]

    def test_prof_abbreviation_preserved(self) -> None:
        chunks = split_into_sentences("Ask Prof. Jones about it. Then wait.")
        assert chunks == ["Ask Prof. Jones about it.", "Then wait."]

    def test_st_abbreviation_preserved(self) -> None:
        chunks = split_into_sentences("Meet on Main St. today. Done.")
        assert chunks == ["Meet on Main St. today.", "Done."]


class TestSplitIntoSentencesEdgeCases:
    """Edge-case inputs produce predictable, truthful output."""

    def test_trailing_whitespace_stripped(self) -> None:
        chunks = split_into_sentences("  Hello.   World.  ")
        assert chunks == ["Hello.", "World."]

    def test_newlines_between_sentences(self) -> None:
        chunks = split_into_sentences("First.\nSecond.\nThird.")
        assert chunks == ["First.", "Second.", "Third."]

    def test_multiple_terminators_collapse(self) -> None:
        # A run of "!!!" or "..." is still one boundary — the splitter
        # does not invent empty sentences between terminators.
        chunks = split_into_sentences("Wow!!! Amazing. Really!")
        # The exact split may vary by terminator count, but we get
        # three non-empty chunks and none is just punctuation.
        assert len(chunks) == 3
        for c in chunks:
            assert c.strip("!?. ")

    def test_returns_list_of_strings(self) -> None:
        chunks = split_into_sentences("One. Two.")
        assert all(isinstance(c, str) for c in chunks)


# =============================================================================
# Section 2: prepare_speech_reply — shape and truncation
# =============================================================================


class TestPrepareSpeechReplyShortReplies:
    """Replies that fit in the budget pass through unchanged."""

    def test_empty_input_returns_empty_shape(self) -> None:
        shape = prepare_speech_reply("")
        assert isinstance(shape, SpeechReplyShape)
        assert shape.speech_text == ""
        assert shape.sentence_count == 0
        assert shape.truncated is False

    def test_formatting_only_input_returns_empty_shape(self) -> None:
        # "###" normalizes to empty; canonical TTS caller treats empty
        # as "nothing to speak" fail-soft.
        shape = prepare_speech_reply("###")
        assert shape.speech_text == ""
        assert shape.sentence_count == 0
        assert shape.truncated is False

    def test_single_sentence_passes_through(self) -> None:
        shape = prepare_speech_reply("Hello world.")
        assert shape.speech_text == "Hello world."
        assert shape.sentence_count == 1
        assert shape.truncated is False

    def test_three_sentence_reply_not_truncated(self) -> None:
        source = "One. Two. Three."
        shape = prepare_speech_reply(source)
        assert shape.speech_text == source
        assert shape.sentence_count == 3
        assert shape.truncated is False


class TestPrepareSpeechReplyLongReplies:
    """Long replies are truncated at a sentence boundary — never mid-sentence."""

    def test_four_sentence_reply_truncated_to_three(self) -> None:
        source = "One. Two. Three. Four."
        shape = prepare_speech_reply(source)
        assert shape.sentence_count == DEFAULT_SPEECH_MAX_SENTENCES
        assert shape.truncated is True
        # Prefix of original, preserving sentence order.
        assert shape.speech_text.startswith("One.")
        assert "Four." not in shape.speech_text

    def test_long_bullet_forest_collapses_to_budget(self) -> None:
        # A reply with lots of bullets: the speech shaper normalizes
        # the bullets out (speech_normalize) and then takes only the
        # first few sentences.  The full written reply still has all
        # the bullets — the helper does not mutate its input.
        source = (
            "Here are the steps.\n"
            "- First step to run.\n"
            "- Second step to run.\n"
            "- Third step to run.\n"
            "- Fourth step to run.\n"
            "- Fifth step to run.\n"
            "That is the plan."
        )
        before = source
        shape = prepare_speech_reply(source)
        # Caller's string is unchanged (Python strings are immutable
        # but we pin that the helper did not rebind their reference
        # or rewrite it in-place).
        assert source == before
        # Spoken copy is shorter than the written copy.
        assert len(shape.speech_text) < len(source)
        # Formatting is stripped (normalize_text_for_tts is in the
        # pipeline).
        assert "-" not in shape.speech_text
        # Sentence count is bounded by the default max.
        assert shape.sentence_count <= DEFAULT_SPEECH_MAX_SENTENCES

    def test_max_sentences_override(self) -> None:
        source = "One. Two. Three. Four. Five."
        shape = prepare_speech_reply(source, max_sentences=2)
        assert shape.sentence_count == 2
        assert shape.truncated is True
        assert "Three." not in shape.speech_text

    def test_max_chars_stops_before_sentence_limit(self) -> None:
        # Three long sentences, each 100+ chars.  With a 150-char
        # budget and a 3-sentence sentence cap, we should stop after
        # the first sentence because adding the second would blow
        # the character budget.
        s = "A" * 100 + "."
        source = f"{s} {s} {s}"
        shape = prepare_speech_reply(source, max_chars=150)
        assert shape.sentence_count == 1
        assert shape.truncated is True

    def test_first_sentence_longer_than_budget_still_kept(self) -> None:
        # A single very long sentence must still be spoken in full —
        # a half-sentence is worse than a too-long sentence.
        source = "A" * 500 + "."
        shape = prepare_speech_reply(source, max_chars=100)
        assert shape.sentence_count == 1
        assert shape.truncated is False
        assert shape.speech_text.endswith(".")


class TestPrepareSpeechReplyFaithfulness:
    """The spoken copy is a faithful prefix of the normalized written reply."""

    def test_spoken_copy_never_reorders_sentences(self) -> None:
        source = "Alpha. Beta. Gamma. Delta."
        shape = prepare_speech_reply(source, max_sentences=2)
        # The first two sentences of the source, in order.
        assert shape.speech_text.startswith("Alpha.")
        assert "Beta." in shape.speech_text
        # Dropped sentences do not reappear.
        assert "Gamma." not in shape.speech_text

    def test_spoken_copy_is_a_prefix_of_normalized(self) -> None:
        # Normalized prose + bounded truncation means the spoken text
        # must appear verbatim at the start of the normalized reply
        # (modulo sentence-join whitespace).
        source = "First sentence. Second sentence. Third sentence. Fourth sentence."
        shape = prepare_speech_reply(source, max_sentences=2)
        assert source.startswith(shape.speech_text[:15])

    def test_caller_text_reference_unchanged(self) -> None:
        source = "# Heading\n- bullet\n- bullet\nSome body text."
        before = source
        prepare_speech_reply(source)
        assert source == before

    def test_negative_budget_falls_back_to_defaults(self) -> None:
        # Zero / negative overrides are treated as "use the default
        # budget" rather than silently disabling truncation.
        source = "One. " * 10
        shape = prepare_speech_reply(source, max_sentences=0, max_chars=-1)
        assert shape.sentence_count <= DEFAULT_SPEECH_MAX_SENTENCES
        assert len(shape.speech_text) <= DEFAULT_SPEECH_MAX_CHARS + 20


class TestPrepareSpeechReplyMarkdownStripping:
    """The shaper runs speech normalization first, so markdown never survives."""

    def test_headings_stripped(self) -> None:
        shape = prepare_speech_reply("## Summary\nAll good.")
        assert "##" not in shape.speech_text

    def test_bold_markers_stripped(self) -> None:
        shape = prepare_speech_reply("That is **very important** to note.")
        assert "**" not in shape.speech_text
        assert "very important" in shape.speech_text

    def test_inline_code_backticks_stripped(self) -> None:
        shape = prepare_speech_reply("Run `make test` first. Done.")
        assert "`" not in shape.speech_text
        assert "make test" in shape.speech_text
