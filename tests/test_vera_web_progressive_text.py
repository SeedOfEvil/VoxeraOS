"""Tests for :mod:`voxera.vera_web.progressive_text`.

Progressive UI chunking splits a finished assistant reply into small
word-group chunks so the streaming dictation endpoint can pace
``text_chunk`` events and the browser renders a visibly typing
effect.  The tests pin:

1. Empty / whitespace-only input returns ``[]``.
2. Non-empty input produces many small chunks (a multi-sentence
   reply should not collapse to 1-2 chunks the way the sentence-
   level speech chunker does).
3. Chunks are word-sized by default (~4 words each) so no chunk is
   alone responsible for a large portion of the reply.
4. The concatenation of all chunks reproduces the original text
   with whitespace normalised to single spaces -- no content loss.
5. ``words_per_chunk`` is clamped to >= 1 so a caller cannot
   produce zero-width chunks.
"""

from __future__ import annotations

from voxera.vera_web.progressive_text import (
    _DEFAULT_WORDS_PER_CHUNK,
    split_progressive_text_chunks,
)


class TestEmptyInput:
    def test_empty_string_returns_empty_list(self) -> None:
        assert split_progressive_text_chunks("") == []

    def test_whitespace_only_returns_empty_list(self) -> None:
        assert split_progressive_text_chunks("   \n\t  ") == []


class TestWordGroupChunking:
    def test_short_reply_produces_few_chunks(self) -> None:
        out = split_progressive_text_chunks("Sure done.")
        assert out == ["Sure done."]

    def test_default_chunking_is_word_granular(self) -> None:
        # 20 words => 5 chunks at the default 4-words-per-chunk.
        words = ["word" + str(i) for i in range(20)]
        text = " ".join(words)
        chunks = split_progressive_text_chunks(text)
        assert len(chunks) == 5
        for c in chunks:
            assert len(c.split()) <= _DEFAULT_WORDS_PER_CHUNK

    def test_multi_sentence_reply_produces_many_small_chunks(self) -> None:
        # A typical helpful reply (~30 words) must produce clearly
        # more than 2 chunks so the browser shows a visibly typing
        # effect.  This is the core regression fence for the
        # "reply lands all at once" bug.
        text = (
            "The queue looks healthy this morning. "
            "I checked the panel metrics and nothing stands out. "
            "Let me know if you want me to inspect a specific service."
        )
        chunks = split_progressive_text_chunks(text)
        assert len(chunks) >= 5, (
            f"progressive chunking should produce many small chunks "
            f"for a typical reply; got {len(chunks)}: {chunks!r}"
        )


class TestContentFaithfulness:
    def test_joined_chunks_reproduce_original(self) -> None:
        text = "The queue looks healthy. I checked the panel metrics."
        chunks = split_progressive_text_chunks(text)
        joined = " ".join(chunks)
        assert joined == text

    def test_whitespace_is_normalized_to_single_spaces(self) -> None:
        # Multiple spaces / newlines collapse to single-space
        # boundaries for progressive display.  Final markdown render
        # on ``done`` preserves the canonical reply separately.
        text = "first line.\n\nSecond   paragraph  here."
        chunks = split_progressive_text_chunks(text)
        joined = " ".join(chunks)
        assert joined == "first line. Second paragraph here."

    def test_no_words_are_dropped(self) -> None:
        words = [f"token{i}" for i in range(30)]
        text = " ".join(words)
        chunks = split_progressive_text_chunks(text)
        joined = " ".join(chunks)
        for w in words:
            assert w in joined


class TestWordsPerChunkParameter:
    def test_custom_words_per_chunk(self) -> None:
        text = "one two three four five six seven eight"
        chunks = split_progressive_text_chunks(text, words_per_chunk=2)
        assert chunks == ["one two", "three four", "five six", "seven eight"]

    def test_zero_is_clamped_to_one(self) -> None:
        text = "alpha beta gamma"
        chunks = split_progressive_text_chunks(text, words_per_chunk=0)
        assert chunks == ["alpha", "beta", "gamma"]

    def test_negative_is_clamped_to_one(self) -> None:
        text = "alpha beta gamma"
        chunks = split_progressive_text_chunks(text, words_per_chunk=-5)
        assert chunks == ["alpha", "beta", "gamma"]
