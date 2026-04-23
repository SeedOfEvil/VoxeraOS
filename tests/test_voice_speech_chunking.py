"""Tests for :mod:`voxera.voice.speech_chunking`.

The chunker balances two product goals:

* **Early first audio** — the first chunk must ship fast so TTS
  playback can begin while later chunks are still synthesizing.
* **Natural cadence** — subsequent chunks must be large enough for
  the synthesizer to produce continuous-voice prosody, instead of
  the audibly fragmented "one sentence per TTS request" feel that an
  earlier sentence-per-chunk implementation produced.

The chunker therefore uses a two-tier head/body coalescing strategy:

* The head chunk is small (minimum ~40 chars; capped ~180) so it
  ships quickly.
* Body chunks coalesce consecutive sentences until they reach a
  natural-prosody target (~220 chars).

The tests pin:

1. Sentence boundaries are respected (``.`` / ``!`` / ``?``) without
   splitting on common abbreviations (``Dr.``, ``e.g.``, ``U.S.``).
2. Short replies (single sentence, or a few short sentences) stay as
   one chunk so the synthesizer speaks them as one natural utterance.
3. Multi-sentence replies produce a head chunk + body chunks; body
   chunks are materially larger than a single sentence so they do
   not sound like a line-by-line reader.
4. An oversized single sentence is split on bounded clause
   boundaries (``;`` / ``, and`` / ``, but``) instead of being
   shipped as one huge TTS request.
5. Empty / whitespace-only input returns ``[]``.
6. The concatenation of all chunks is a faithful restatement of the
   input -- no content is lost and order is preserved.
"""

from __future__ import annotations

from voxera.voice.speech_chunking import (
    _BODY_CHUNK_TARGET_CHARS,
    _HEAD_CHUNK_MIN_CHARS,
    split_speakable_chunks,
)


def _normalize_join(chunks: list[str]) -> str:
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


class TestSentenceBoundaryDetection:
    def test_abbreviation_does_not_split(self) -> None:
        text = "Dr. Rivera reviewed the logs. She found one anomaly."
        chunks = split_speakable_chunks(text)
        # Under the two-tier coalescer these two sentences combine
        # into a single chunk (both short); the point of this test
        # is that "Dr." is not treated as a sentence boundary.
        assert len(chunks) == 1
        assert "Dr. Rivera reviewed the logs." in chunks[0]
        assert "She found one anomaly." in chunks[0]

    def test_initialism_does_not_split(self) -> None:
        text = "The U.S. report is long. It covers three years."
        chunks = split_speakable_chunks(text)
        assert len(chunks) == 1
        assert "U.S. report" in chunks[0]
        assert "three years." in chunks[0]

    def test_eg_does_not_split(self) -> None:
        text = (
            "The queue handles many shapes, e.g. write_file and mission. "
            "Each one submits through the gateway."
        )
        chunks = split_speakable_chunks(text)
        joined = _normalize_join(chunks)
        # "e.g." survives intact (no false split) even though the
        # surrounding chunk layout is governed by the coalescer.
        assert "e.g. write_file and mission" in joined


class TestContentFaithfulness:
    def test_all_content_words_survive(self) -> None:
        text = (
            "I found three bottlenecks. Queue throughput is low. "
            "Panel requests queue frequently. Logs show a retry loop."
        )
        chunks = split_speakable_chunks(text)
        joined = _normalize_join(chunks)
        for word in ["bottlenecks", "throughput", "Panel", "retry"]:
            assert word in joined

    def test_paragraph_reconstructs_faithfully(self) -> None:
        text = (
            "Yes, I can help! Let me think for a moment. "
            "Do you want the short version? Or the long one."
        )
        chunks = split_speakable_chunks(text)
        joined = _normalize_join(chunks)
        assert joined == text.strip()

    def test_joined_chunks_reproduce_multi_sentence_reply(self) -> None:
        text = (
            "The queue looks healthy this morning. "
            "I checked the panel metrics and nothing stands out. "
            "Let me know if you want me to inspect a specific service."
        )
        chunks = split_speakable_chunks(text)
        joined = _normalize_join(chunks)
        assert joined == text.strip()


class TestHeadBodyCoalescing:
    def test_short_reply_is_single_chunk(self) -> None:
        # Three terse sentences together still stay below the head
        # minimum; the whole reply ships as one natural utterance
        # rather than three stuttered TTS calls.
        text = "Yes. OK. Let us look at the queue health in detail."
        chunks = split_speakable_chunks(text)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_multi_sentence_reply_produces_head_plus_body(self) -> None:
        # Three sentences where the first alone is below the head
        # minimum (38 chars < 40).  The coalescer merges s1 + s2
        # into the head chunk so the first TTS call has enough
        # prosody context; the remaining sentence flushes as body.
        text = (
            "The queue looks healthy this morning. "
            "I checked the panel metrics and nothing stands out. "
            "Let me know if you want me to inspect a specific service."
        )
        chunks = split_speakable_chunks(text)
        assert len(chunks) == 2
        # Head chunk contains the first two sentences coalesced so
        # the first TTS call is not a too-short stutter.
        assert chunks[0].startswith("The queue looks healthy")
        assert "panel metrics" in chunks[0]
        # Body chunk carries the remaining sentence.
        assert "inspect a specific" in chunks[1]

    def test_head_chunk_meets_minimum_size(self) -> None:
        # If the first sentence is short, the head chunk merges the
        # next sentence in so it still meets the natural-size floor.
        text = "Sure. I can help with that today and tomorrow."
        chunks = split_speakable_chunks(text)
        assert len(chunks) == 1
        # Combined chunk meets the head minimum — the terse "Sure."
        # alone would otherwise sound like a stuttered standalone
        # utterance.
        assert len(chunks[0]) >= _HEAD_CHUNK_MIN_CHARS
        assert chunks[0].startswith("Sure.")

    def test_body_chunks_are_materially_larger_than_one_sentence(self) -> None:
        # A longer reply — body chunks should coalesce so each body
        # chunk is larger than a single sentence, giving the
        # synthesizer enough prosody context to sound continuous.
        text = (
            "Here is one approach for the investigation. "
            "First, inspect the queue health snapshot for bottlenecks. "
            "Next, review the panel latency metrics over the last hour. "
            "Then compare that against recent audit entries for context. "
            "Finally, bundle the findings so the operator can review them."
        )
        chunks = split_speakable_chunks(text)
        assert len(chunks) >= 2
        # The last chunk may fall short of target if content ran out,
        # but intermediate body chunks should be at or near the body
        # target so spoken cadence feels continuous.
        intermediate_body_chunks = chunks[1:-1] if len(chunks) > 2 else [chunks[1]]
        for body_chunk in intermediate_body_chunks:
            # Each body chunk carries multiple sentences' worth of
            # text (materially larger than a single ~60-char sentence).
            assert len(body_chunk) >= _BODY_CHUNK_TARGET_CHARS * 0.5, (
                f"body chunk too small for natural prosody: {len(body_chunk)} chars: {body_chunk!r}"
            )

    def test_body_chunks_never_exceed_absolute_max(self) -> None:
        # Ten long sentences in a row — the coalescer must stop
        # merging at the body target so no chunk grows unbounded.
        sentence = "The daemon processed the pending queue items without any errors."
        text = " ".join([sentence] * 10)
        chunks = split_speakable_chunks(text)
        assert len(chunks) >= 2
        for chunk in chunks:
            # 400 is the absolute hard cap for non-oversized-sentence
            # body chunks after coalescing.
            assert len(chunk) <= 400 + len(sentence), f"chunk exceeded body cap: {len(chunk)} chars"


class TestLongSentenceClauseSplit:
    def test_oversized_sentence_with_semicolons_splits_on_clauses(self) -> None:
        long_body = (
            "I reviewed the queue throughput numbers carefully; "
            "the panel request lane is the dominant contributor; "
            "the daemon retry loop adds a smaller but measurable cost; "
            "and the Vera preview builder is consistently sub-second"
        )
        padded = long_body + ", which tells us the bottleneck is upstream of Vera" * 5 + "."
        assert len(padded) > 400
        chunks = split_speakable_chunks(padded)
        assert len(chunks) >= 2
        joined = _normalize_join(chunks)
        for word in ["queue", "panel", "daemon", "preview"]:
            assert word in joined

    def test_short_sentence_with_semicolons_is_not_split(self) -> None:
        text = "Queue; panel; daemon."
        chunks = split_speakable_chunks(text)
        assert chunks == ["Queue; panel; daemon."]


class TestRegressionAgainstLineByLineFeel:
    """Regression fences for the "sounds line-by-line" bug fixed in
    the naturalness pass.

    The previous chunker shipped one sentence per chunk, which
    produced a staccato spoken cadence with a perceptible pause
    between every sentence.  These tests pin that a multi-sentence
    reply no longer produces one-chunk-per-sentence output.
    """

    def test_three_medium_sentences_produce_fewer_than_three_chunks(self) -> None:
        text = (
            "The queue looks healthy this morning. "
            "I checked the panel metrics and nothing stands out. "
            "Let me know if you want me to inspect a specific service."
        )
        chunks = split_speakable_chunks(text)
        # The former "sentence per chunk" behaviour produced 3.  The
        # coalescer must produce fewer so the listener hears fewer
        # synthesis boundaries.
        assert len(chunks) < 3

    def test_terse_three_sentence_reply_is_single_chunk(self) -> None:
        # "Yes. OK. Done." used to produce three separate TTS
        # utterances.  The coalescer must merge them into one chunk
        # because the combined text stays below the head minimum.
        text = "Yes. OK. Done."
        chunks = split_speakable_chunks(text)
        assert chunks == [text]

    def test_head_chunk_for_typical_helpful_reply_merges_first_two_sentences_if_short(
        self,
    ) -> None:
        # A short lead-in followed by a real sentence should produce
        # one combined head chunk, not a terse lead-in chunk followed
        # by a second chunk.
        text = (
            "Okay. Here is what I found when I looked at the queue. "
            "There are three entries pending review."
        )
        chunks = split_speakable_chunks(text)
        # Terse "Okay." must not stand alone; it merges forward.
        assert not any(chunk.rstrip() == "Okay." for chunk in chunks)
