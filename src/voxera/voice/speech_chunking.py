"""Speakable chunk splitting for progressive TTS synthesis.

Canonical Vera produces a single authoritative assistant reply text
per turn.  For voice turns, synthesizing that whole reply as one TTS
request delays the first spoken word by the full end-to-end TTS
duration, even when local backends (Kokoro / Piper) are fast.  The
fix is to synthesize speakable chunks one at a time, starting with
the first stable sentence, so playback can begin while later chunks
are still being produced.

This module provides a single bounded helper,
:func:`split_speakable_chunks`, that returns an ordered list of
speakable chunks for a reply text.  The helper is deterministic,
conservative, and purely a string transform -- it never paraphrases,
summarizes, or deletes meaningful content.  The concatenation of all
returned chunks, with a single space between them, is always a
faithful restatement of the normalized input (no content loss).

Non-negotiables enforced by this module:

* The input string is never mutated in place.
* The ordered list of returned chunks covers the entire input --
  callers that speak the chunks in order and in full get the
  complete reply.
* Empty / whitespace-only input returns ``[]``.
* A reply that contains no clear sentence boundary is returned as a
  single chunk -- callers fall back to whole-reply TTS cleanly.

Chunk boundary strategy:

1. **Sentence boundary (preferred).**  ``.``, ``!``, ``?`` (including
   repeats and compounds like ``?!``) followed by whitespace or EOL
   end a sentence, provided the preceding token is not a common
   abbreviation (``Dr.``, ``Mr.``, ``e.g.``, ``i.e.``, ``U.S.``,
   ``vs.``, ``etc.`` ...).  A trailing ``.`` at the very end of the
   input also closes a chunk.
2. **Clause boundary (bounded fallback).**  If a single sentence
   exceeds :data:`_MAX_CHUNK_CHARS` (~400 chars), split on ``;`` or
   ``,`` followed by a coordinator (``and``, ``but``, ``or``, ``so``,
   ``because``).  This is only used to tame runaway sentences so the
   first chunk still ships to TTS quickly; we never split short
   sentences mid-clause.
3. **Minimum chunk size.**  Chunks shorter than
   :data:`_MIN_CHUNK_CHARS` (~24 chars) are merged with the next
   chunk so the synthesizer does not wake up for tiny "Yes.",
   "Right." pulses that would add more overhead than they save.  The
   last chunk is kept as-is regardless of size so a short final
   sentence ("Done.") is still spoken.

The returned chunks are speech-ready in the sense that they are
already suitable for :func:`voxera.voice.speech_normalize.
normalize_text_for_tts`; this helper does not normalize markdown
itself (callers should run each chunk through the normalizer before
handing it to the TTS backend -- see ``vera_web.app`` for the
canonical call site).
"""

from __future__ import annotations

import re

__all__ = ["split_speakable_chunks"]

# Bounded chunk size envelope.  The max keeps one sentence from being
# shipped as one giant TTS request; the min is kept small on purpose
# because the product goal is "first spoken word ASAP" -- a terse
# "Sure." or "Wait!" is a fully stable sentence and should reach TTS
# immediately rather than being coalesced with whatever comes after.
# ``_MIN_CHUNK_CHARS`` is the floor below which we *would* merge a
# chunk with its neighbour; a value of ``1`` effectively disables the
# coalescing pass while still filtering out empty strings produced by
# a purely-formatting terminator run.
_MAX_CHUNK_CHARS = 400
_MIN_CHUNK_CHARS = 1

# Common abbreviations whose trailing "." is NOT a sentence boundary.
# Matching is case-insensitive on word boundaries.  Kept small and
# conservative -- the cost of missing one is a slightly early chunk
# split (still speakable); the cost of adding a bogus one is under-
# chunking a long reply (slightly slower first speech).
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
    """Return True when ``terminator`` at ``match_start`` ends a sentence.

    Filters out abbreviation-style ``.`` endings like ``Dr.`` /
    ``e.g.`` / ``U.S.`` where the next character is a space but the
    preceding token is a known abbreviation or an initialism.
    Compound terminators (``?!``, ``!!``) are always sentence ends
    because no abbreviation uses them.
    """
    if terminator and any(ch in terminator for ch in "!?"):
        return True
    # Look back at the word preceding the ``.`` to check for
    # abbreviations / initialisms.  We collect trailing letters and
    # remember whether we skipped any interior periods -- a period
    # embedded in the preceding token (``e.g.`` / ``U.S.``) is a
    # strong signal that the ``.`` at ``match_start`` is part of the
    # same abbreviation, not a real sentence end.
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
    # If clause splitting yielded nothing, fall back to the whole
    # sentence (better to speak a long chunk than to lose content).
    if not pieces:
        return [sentence]
    # If any resulting piece is still too long, we accept it rather
    # than chopping mid-phrase; the synthesizer will handle it.
    return pieces


def _coalesce_short_chunks(chunks: list[str]) -> list[str]:
    """Merge chunks below the minimum size with their neighbours.

    Rules:
    * A short chunk in the middle is merged with the next chunk.
    * The last chunk is kept as-is regardless of size -- a short final
      sentence ("Done.") must still be spoken.
    * The merge inserts a single space so cadence is preserved.
    """
    if not chunks:
        return []
    merged: list[str] = []
    i = 0
    while i < len(chunks):
        current = chunks[i]
        # If short and not the last, merge forward.
        while len(current) < _MIN_CHUNK_CHARS and i + 1 < len(chunks):
            nxt = chunks[i + 1]
            current = f"{current} {nxt}".strip()
            i += 1
        merged.append(current)
        i += 1
    return merged


def split_speakable_chunks(text: str) -> list[str]:
    """Return an ordered list of bounded speakable chunks for ``text``.

    See module docstring for the full chunk boundary strategy.  Empty
    or whitespace-only input returns ``[]`` (caller should fall back
    to "no TTS this turn").  A reply with no clear sentence boundary
    is returned as a single chunk so the caller can still synthesize
    the whole reply rather than falsely claiming "no speakable
    content".
    """
    if not text:
        return []
    trimmed = str(text).strip()
    if not trimmed:
        return []

    sentences = _raw_sentence_split(trimmed)
    if not sentences:
        # No terminators: single chunk (bounded fallback).  We do not
        # clause-split here because the absence of any terminator in a
        # short reply is the norm ("Sure.", "hi there") rather than an
        # oversized runaway paragraph.
        return [trimmed]

    expanded: list[str] = []
    for sentence in sentences:
        expanded.extend(_split_long_sentence(sentence))
    coalesced = _coalesce_short_chunks(expanded)
    return [chunk for chunk in coalesced if chunk]
