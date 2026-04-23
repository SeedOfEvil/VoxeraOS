"""Speakable chunk splitting for progressive TTS synthesis.

Canonical Vera produces a single authoritative assistant reply text
per turn.  For voice turns, synthesizing the whole reply as one TTS
request delays the first spoken word by the full end-to-end TTS
duration even when local backends (Kokoro / Piper) are fast.  But the
naive opposite -- one TTS request per sentence -- produces a
"line-by-line" cadence: each sentence gets its own utterance envelope
with a perceptible pause between synthesis boundaries, so the reply
no longer sounds like a single natural assistant voice.

This module's strategy is therefore a **two-tier head/body split**:

* The **head chunk** is small (typically one short sentence, or two
  very short ones coalesced) so the first audio chunk ships quickly
  and playback can begin while body chunks are still being produced.
* The **body chunks** coalesce consecutive sentences until they reach
  a natural-prosody target (~220 chars).  That gives the synthesizer
  enough context for smoother intonation and fewer audible chunk
  boundaries than the sentence-per-chunk approach.

This preserves the latency benefit of early-chunk TTS (the first
chunk still ships after roughly one sentence) while removing the
"each sentence is its own utterance" feel for the rest of the reply.

Public surface:

* :func:`split_speakable_chunks` -- takes the canonical assistant
  reply text and returns an ordered list of speakable chunks ready
  for :func:`voxera.voice.speech_normalize.normalize_text_for_tts`.

Non-negotiables enforced by this module:

* The input string is never mutated in place.
* The ordered list of returned chunks covers the entire input --
  callers that speak the chunks in order get the complete reply.
* Empty / whitespace-only input returns ``[]``.
* A reply with no sentence terminator is returned as a single chunk
  so the caller falls back to whole-reply TTS cleanly.

Chunk boundary strategy:

1. **Sentence boundary (preferred).**  ``.``, ``!``, ``?`` (including
   repeats and compounds like ``?!``) followed by whitespace or EOL
   end a sentence, provided the preceding token is not a common
   abbreviation (``Dr.``, ``Mr.``, ``e.g.``, ``i.e.``, ``U.S.``,
   ``vs.``, ``etc.`` ...).  A trailing ``.`` at the very end of the
   input also closes a sentence.
2. **Head/body coalescing.**  Consecutive sentences merge into one
   chunk until the size target is reached (``_HEAD_CHUNK_MIN_CHARS``
   for the first chunk, ``_BODY_CHUNK_TARGET_CHARS`` for the rest).
   This is the core naturalness-preserving step.
3. **Clause boundary (bounded fallback).**  If a single sentence
   exceeds :data:`_MAX_CHUNK_CHARS` (~400 chars), split on ``;`` or
   ``,`` followed by a coordinator (``and``, ``but``, ``or``, ``so``,
   ``because``) so one runaway sentence does not dominate TTS.  Short
   sentences are never clause-split.
"""

from __future__ import annotations

import re

__all__ = ["split_speakable_chunks"]

# Head chunk ships early so playback can begin while body chunks are
# still synthesizing.  ``_HEAD_CHUNK_MIN_CHARS`` is the minimum size
# the head chunk must reach before we flush it; below this we coalesce
# the next sentence in so terse heads ("Sure.", "OK.") do not ship as
# their own TTS request with a jarring boundary.  ``_HEAD_CHUNK_MAX_CHARS``
# is the upper guardrail -- we stop merging into the head once we hit
# this size so it stays a "first breath" chunk, not a whole paragraph.
_HEAD_CHUNK_MIN_CHARS = 40
_HEAD_CHUNK_MAX_CHARS = 180

# Body chunks coalesce sentences until they reach this length, which
# gives the synthesizer enough prosody context to sound like a
# continuous assistant voice rather than a staccato line-by-line
# reader.  The final body chunk may fall short of this target if the
# remaining content is small -- the last chunk is always emitted
# regardless of size so no content is dropped.
_BODY_CHUNK_TARGET_CHARS = 220

# Absolute upper bound for any single chunk.  Oversized sentences are
# clause-split to keep synthesis bounded; body chunks never exceed
# this after coalescing because the coalescer stops merging once the
# chunk is at or over the body target.
_MAX_CHUNK_CHARS = 400

# Common abbreviations whose trailing "." is NOT a sentence boundary.
_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "dr",
        "mr",
        "mrs",
        "ms",
        "prof",
        "sr",
        "jr",
        "st",
        "vs",
        "etc",
        "e.g",
        "i.e",
        "no",
        "fig",
        "vol",
        "approx",
    }
)

# Sentence-ender pattern: one or more terminators followed by whitespace
# or end-of-string.  Captures the terminator run so we can decide about
# abbreviations in ``_is_real_sentence_end``.
_SENTENCE_END_RE = re.compile(r"([.!?]+)(?=\s|$)")

# Clause-boundary pattern used only to tame oversized sentences.  We
# prefer semicolons over commas so ordinary prose is not chopped mid-
# breath; commas only count when followed by a small set of bounded
# coordinators that clearly introduce an independent thought.
_CLAUSE_SPLIT_RE = re.compile(
    r"(;\s+)|(,\s+(?:and|but|or|so|because)\b)",
    re.IGNORECASE,
)


def _is_real_sentence_end(text: str, match_start: int, terminator: str) -> bool:
    """Return True when ``terminator`` at ``match_start`` ends a sentence."""
    if terminator and any(ch in terminator for ch in "!?"):
        return True
    i = match_start - 1
    trailing: list[str] = []
    saw_interior_period = False
    while i >= 0:
        ch = text[i]
        if ch.isalpha():
            trailing.append(ch)
            i -= 1
        elif ch == "." and trailing:
            saw_interior_period = True
            i -= 1
        else:
            break
    if saw_interior_period:
        return False
    word = "".join(reversed(trailing)).lower()
    if not word:
        return True
    return word not in _ABBREVIATIONS


def _raw_sentence_split(text: str) -> list[str]:
    """Split ``text`` on real sentence boundaries; keep terminators."""
    if not text:
        return []
    results: list[str] = []
    last_end = 0
    for match in _SENTENCE_END_RE.finditer(text):
        terminator = match.group(1)
        if not _is_real_sentence_end(text, match.start(), terminator):
            continue
        end = match.end()
        segment = text[last_end:end].strip()
        if segment:
            results.append(segment)
        last_end = end
    tail = text[last_end:].strip()
    if tail:
        results.append(tail)
    return results


def _split_long_sentence(sentence: str) -> list[str]:
    """Split an oversized sentence on bounded clause boundaries."""
    if len(sentence) <= _MAX_CHUNK_CHARS:
        return [sentence]
    pieces: list[str] = []
    cursor = 0
    for match in _CLAUSE_SPLIT_RE.finditer(sentence):
        end = match.end()
        piece = sentence[cursor:end].strip()
        if piece:
            pieces.append(piece)
        cursor = end
    tail = sentence[cursor:].strip()
    if tail:
        pieces.append(tail)
    if not pieces:
        return [sentence]
    return pieces


def _coalesce_for_naturalness(sentences: list[str]) -> list[str]:
    """Coalesce sentences into a head chunk + natural-size body chunks.

    * Head chunk (first emitted): ships once it reaches
      ``_HEAD_CHUNK_MIN_CHARS``, capped at ``_HEAD_CHUNK_MAX_CHARS``
      to keep time-to-first-audio small.
    * Body chunks: coalesce subsequent sentences until the combined
      text reaches ``_BODY_CHUNK_TARGET_CHARS``, then flush.  This
      gives the synthesizer enough prosody context to sound like a
      continuous voice rather than a one-sentence-per-utterance
      reader.
    * Trailing buffer: flushed as the final chunk regardless of size
      so a terse closing sentence ("Done.") is still spoken.
    """
    if not sentences:
        return []
    chunks: list[str] = []
    buffer = ""
    head_emitted = False

    for sentence in sentences:
        buffer = sentence if not buffer else f"{buffer} {sentence}"
        target = _BODY_CHUNK_TARGET_CHARS if head_emitted else _HEAD_CHUNK_MIN_CHARS
        hard_max = _MAX_CHUNK_CHARS if head_emitted else _HEAD_CHUNK_MAX_CHARS
        if len(buffer) >= target or len(buffer) >= hard_max:
            chunks.append(buffer)
            buffer = ""
            head_emitted = True
    if buffer:
        chunks.append(buffer)
    return chunks


def split_speakable_chunks(text: str) -> list[str]:
    """Return an ordered list of bounded speakable chunks for ``text``.

    See module docstring for the full strategy.  Empty / whitespace-
    only input returns ``[]``.  A reply with no clear sentence
    boundary returns a single chunk so the caller can still
    synthesize the whole reply rather than falsely claiming "no
    speakable content".
    """
    if not text:
        return []
    trimmed = str(text).strip()
    if not trimmed:
        return []

    sentences = _raw_sentence_split(trimmed)
    if not sentences:
        return [trimmed]

    expanded: list[str] = []
    for sentence in sentences:
        expanded.extend(_split_long_sentence(sentence))
    return [chunk for chunk in _coalesce_for_naturalness(expanded) if chunk]
