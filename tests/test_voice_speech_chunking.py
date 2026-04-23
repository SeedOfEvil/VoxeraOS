"""Tests for :mod:`voxera.voice.speech_chunking`.

The chunker is the only place in the codebase that splits Vera's
authoritative assistant reply into speakable pieces for early-chunk
TTS on ``/chat/voice/stream``.  The tests pin:

1. Sentence boundaries are respected (``.`` / ``!`` / ``?``) without
   splitting on common abbreviations (``Dr.``, ``e.g.``, ``U.S.``).
2. The concatenation of all chunks is a faithful restatement of the
   input — no content is lost and the order is preserved.
3. Short adjacent fragments coalesce to meet the minimum chunk size,
   but the last chunk may stay short so a terse final sentence
   ("Done.") is still spoken.
4. An oversized single sentence is split on bounded clause
   boundaries (``;`` / ``, and`` / ``, but``) instead of being
   shipped as one huge TTS request; short sentences never split.
5. Empty / whitespace-only input returns ``[]``; text without a
   terminator returns a single chunk (fall-back to whole-reply
   TTS).
"""

from __future__ import annotations

from voxera.voice.speech_chunking import split_speakable_chunks


def _normalize_join(chunks: list[str]) -> str:
    """Join chunks with a single space for content-faithfulness checks."""
    return " ".join(chunk.strip() for chunk in chunks if chunk.strip())


class TestEmptyOrTrivialInput:
    def test_empty_string_returns_empty_list(self) -> None:
        assert split_speakable_chunks("") == []

    def test_whitespace_only_returns_empty_list(self) -> None:
        assert split_speakable_chunks("   \n\t  ") == []

    def test_single_short_reply_returns_single_chunk(self) -> None:
        out = split_speakable_chunks("Sure.")
        assert out == ["Sure."]

    def test_no_terminator_returns_single_chunk(self) -> None:
        out = split_speakable_chunks("hello there operator")
        assert out == ["hello there operator"]


class TestSentenceBoundaries:
    def test_splits_on_period_plus_space(self) -> None:
        text = (
            "The lab is quiet this morning. I checked the queue. Everything looks healthy so far."
        )
        chunks = split_speakable_chunks(text)
        assert len(chunks) == 3
        assert chunks[0].startswith("The lab")
        assert chunks[1].startswith("I checked")
        assert chunks[2].startswith("Everything")
        # All chunks end with their sentence terminator.
        assert all(chunk.rstrip()[-1] in ".!?" for chunk in chunks)

    def test_splits_on_exclamation_and_question(self) -> None:
        text = "Wait! Did you see that? I think so."
        chunks = split_speakable_chunks(text)
        assert len(chunks) == 3
        assert chunks[0] == "Wait!"
        assert chunks[1] == "Did you see that?"
        assert chunks[2] == "I think so."

    def test_compound_terminator_is_sentence_end(self) -> None:
        text = "Really?! That is surprising. Let us continue."
        chunks = split_speakable_chunks(text)
        assert chunks[0] == "Really?!"
        assert chunks[1] == "That is surprising."
        assert chunks[2] == "Let us continue."

    def test_abbreviation_does_not_split(self) -> None:
        text = "Dr. Rivera reviewed the logs. She found one anomaly."
        chunks = split_speakable_chunks(text)
        assert len(chunks) == 2
        assert chunks[0].startswith("Dr. Rivera")
        assert "She found" in chunks[1]

    def test_initialism_does_not_split(self) -> None:
        text = "The U.S. report is long. It covers three years."
        chunks = split_speakable_chunks(text)
        assert len(chunks) == 2
        assert "U.S. report" in chunks[0]

    def test_eg_does_not_split(self) -> None:
        text = (
            "The queue handles many shapes, e.g. write_file and mission. "
            "Each one submits through the gateway."
        )
        chunks = split_speakable_chunks(text)
        assert len(chunks) == 2
        assert "e.g." in chunks[0]


class TestContentFaithfulness:
    def test_reordering_of_text_is_stable(self) -> None:
        text = "First. Second. Third. Fourth."
        chunks = split_speakable_chunks(text)
        # Order is preserved strictly.
        joined = _normalize_join(chunks)
        assert joined == "First. Second. Third. Fourth."

    def test_no_character_loss_in_content_words(self) -> None:
        text = (
            "I found three bottlenecks. Queue throughput is low. "
            "Panel requests queue frequently. Logs show a retry loop."
        )
        chunks = split_speakable_chunks(text)
        joined = _normalize_join(chunks)
        # All content words survive.
        for word in ["bottlenecks", "throughput", "Panel", "retry"]:
            assert word in joined

    def test_paragraph_with_mixed_punctuation_reconstructs_faithfully(self) -> None:
        text = (
            "Yes, I can help! Let me think for a moment. "
            "Do you want the short version? Or the long one."
        )
        chunks = split_speakable_chunks(text)
        joined = _normalize_join(chunks)
        assert joined == text.strip()


class TestShortSentencesShipImmediately:
    def test_terse_sentences_are_separate_chunks(self) -> None:
        # A terse "Yes." or "OK." alone is a fully stable sentence and
        # should reach TTS immediately -- coalescing short chunks
        # would work against the "first spoken word ASAP" product
        # goal.  Each sentence ships on its own.
        text = "Yes. OK. Let us look at the queue health in detail."
        chunks = split_speakable_chunks(text)
        assert len(chunks) == 3
        assert chunks[0] == "Yes."
        assert chunks[1] == "OK."
        assert "Let us look" in chunks[-1]

    def test_terse_final_sentence_is_preserved(self) -> None:
        text = "I ran the inspection and everything passed cleanly. Done."
        chunks = split_speakable_chunks(text)
        assert chunks[-1].rstrip() == "Done."


class TestLongSentenceClauseSplit:
    def test_oversized_sentence_with_semicolons_splits_on_clauses(self) -> None:
        # One very long sentence with multiple clause separators.  The
        # chunker should split on the clause boundaries rather than
        # shipping the whole 400+ character sentence as a single TTS
        # request.  A short sentence with the same wording is NOT
        # eligible for clause splitting — we only split on clause
        # boundaries once the single-sentence chars exceed the max.
        long_body = (
            "I reviewed the queue throughput numbers carefully; "
            "the panel request lane is the dominant contributor; "
            "the daemon retry loop adds a smaller but measurable cost; "
            "and the Vera preview builder is consistently sub-second"
        )
        # Pad so the single sentence is well above the 400-char cap.
        padded = long_body + ", which tells us the bottleneck is upstream of Vera" * 5 + "."
        assert len(padded) > 400
        chunks = split_speakable_chunks(padded)
        assert len(chunks) >= 2
        # Content fidelity: every content keyword shows up in order.
        joined = _normalize_join(chunks)
        for word in ["queue", "panel", "daemon", "preview"]:
            assert word in joined

    def test_short_sentence_with_semicolons_is_not_split(self) -> None:
        # A short sentence should be returned as a single chunk even if
        # it contains semicolons; clause splitting only fires when the
        # sentence itself is oversized.
        text = "Queue; panel; daemon."
        chunks = split_speakable_chunks(text)
        assert chunks == ["Queue; panel; daemon."]


class TestTypicalVeraReplyShapes:
    def test_quick_confirmation_reply_is_one_chunk(self) -> None:
        text = "Sure — done."
        chunks = split_speakable_chunks(text)
        assert len(chunks) == 1

    def test_conversational_planning_reply_splits_by_sentence(self) -> None:
        text = (
            "Here is one approach. First, inspect the queue health. "
            "Next, review the panel latency metrics. "
            "Finally, compare that against recent audit entries."
        )
        chunks = split_speakable_chunks(text)
        assert len(chunks) == 4
        assert chunks[0].startswith("Here is")
        assert chunks[-1].startswith("Finally")
