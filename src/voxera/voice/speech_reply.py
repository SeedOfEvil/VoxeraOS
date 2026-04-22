"""Speech-optimized reply shaping and sentence splitting.

Vera's full written reply is authoritative and appears unchanged in
the chat thread.  For spoken output, reading the entire written reply
aloud is expensive (TTS runtime grows roughly linearly with the
synthesized text) and often awkward — long bulleted elaborations or
multi-paragraph code recaps are great in the panel but verbose over
audio.

This module provides two bounded, truthful helpers:

* :func:`prepare_speech_reply` — derive a concise speech-only copy
  of the assistant reply.  Applies :func:`normalize_text_for_tts`
  first to strip formatting syntax, then keeps only the first few
  sentences so the TTS backend synthesizes a shorter reply.  The
  transform is a **prefix truncation** of the normalized text: the
  helper never paraphrases, reorders, or invents.  Truth preserved:
  whatever Vera said first remains what the speaker hears first.

* :func:`split_into_sentences` — split speech-normalized text into
  ordered sentence chunks.  Used by the sentence-first TTS path so
  audio playback can begin as soon as the first chunk is synthesized
  while the remaining chunks synthesize in parallel.

Non-negotiables:

* The full written reply stays authoritative — these helpers operate
  on a derived copy and never mutate or replace the canonical text.
* Truncation is always at a sentence (or paragraph) boundary so the
  speaker never hears a half-sentence.  If no boundary fits, the
  whole text is returned unchanged.
* Degenerate inputs (empty, formatting-only) return an empty string;
  the canonical TTS request validator catches empty text fail-soft.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .speech_normalize import normalize_text_for_tts

__all__ = [
    "DEFAULT_SPEECH_MAX_CHARS",
    "DEFAULT_SPEECH_MAX_SENTENCES",
    "SpeechReplyShape",
    "prepare_speech_reply",
    "split_into_sentences",
]


# ---------------------------------------------------------------------------
# Defaults — deliberately conservative so a full reply still feels helpful
# without reading a bullet forest aloud.
# ---------------------------------------------------------------------------

# Cap on spoken sentences.  1–3 is the product default; short enough
# to feel crisp over voice, long enough to still be useful.
DEFAULT_SPEECH_MAX_SENTENCES: int = 3

# Cap on spoken characters (post-normalization).  Acts as a hard safety
# net when the reply happens to be three very long sentences — the
# helper never produces more than this many characters of spoken text.
# Tuned so a typical reply of ~2–3 sentences fits; long single sentences
# are truncated at the first sentence boundary under the cap.
DEFAULT_SPEECH_MAX_CHARS: int = 280


# ---------------------------------------------------------------------------
# Sentence boundary regex.
# ---------------------------------------------------------------------------
#
# The splitter is intentionally simple: a sentence boundary is a run
# of ``.``, ``!`` or ``?`` followed by whitespace (or end-of-string).
# Abbreviations commonly found in assistant replies are guarded so they
# do not create a false boundary.
#
# This is not a full NLP sentence segmenter; it is a bounded, deterministic
# heuristic that works well for the kind of short English assistant replies
# Vera produces.  Inputs that don't split cleanly simply fall back to a
# single chunk — the canonical TTS path still synthesizes them correctly.

# Abbreviations that should NOT trigger a sentence split even when
# followed by a period + space.  Match case-insensitively.  The list is
# intentionally short — false negatives here mean an extra sentence
# break, which is harmless (the speaker hears a slightly longer pause).
_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        # Personal titles — treating "Dr. Smith" as a single noun
        # phrase (not "Dr." / "Smith") is the common case.
        "mr",
        "mrs",
        "ms",
        "dr",
        "jr",
        "sr",
        "st",
        "prof",
        # Comparatives that typically appear mid-sentence.
        "vs",
    }
)

# Terminator characters that close a sentence.
_SENTENCE_TERMINATORS = ".!?"

# Boundary regex: one or more terminators, followed by closing quote/
# bracket, then whitespace.  We post-check abbreviations on the segment
# before the terminator.
_SENTENCE_BOUNDARY_RE = re.compile(
    r"(?<=[.!?])[\"')\]}]*\s+",
)


# ---------------------------------------------------------------------------
# Result shape for observability.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpeechReplyShape:
    """Bounded, truthful description of how the written reply was shaped for speech.

    Returned alongside the spoken text so operator-facing timing and
    diagnostics surfaces can reflect exactly what was trimmed.  Never
    leaks the canonical assistant text — that is the caller's own
    object — and never carries per-sentence secrets.

    * ``speech_text``: the concise spoken copy (post-normalization,
      post-truncation).  May equal the normalized written text when
      no truncation was needed.
    * ``sentence_count``: how many sentences survived into the spoken
      copy.  Useful for instrumentation ("spoke 2 of 7 sentences").
    * ``truncated``: ``True`` when the helper dropped one or more
      sentences from the end; ``False`` when the written reply
      already fit inside the budget.  The operator-facing UI can
      use this to render an accurate "spoken reply is shorter"
      indicator without guessing.
    """

    speech_text: str
    sentence_count: int
    truncated: bool


# ---------------------------------------------------------------------------
# Sentence splitting.
# ---------------------------------------------------------------------------


def _ends_with_abbreviation(segment: str) -> bool:
    """Return True if *segment* ends with a known abbreviation + period.

    The segment we check is everything before the current whitespace
    boundary.  We look at the last token (split on whitespace) and
    strip the trailing period, then see if it matches our abbreviation
    set case-insensitively.  Keeps the check bounded to the tail so the
    splitter stays O(n).
    """
    if not segment:
        return False
    tail = segment.rstrip()
    if not tail or not tail.endswith("."):
        return False
    # Strip the trailing period and inspect the last whitespace-delimited
    # token.  For "e.g." we check "e.g"; for "Dr." we check "Dr".
    without_period = tail[:-1]
    if not without_period:
        return False
    last_token = without_period.rsplit(None, 1)[-1]
    return last_token.lower() in _ABBREVIATIONS


def split_into_sentences(text: str) -> list[str]:
    """Split *text* into an ordered list of sentence chunks.

    Deterministic, bounded sentence segmentation suitable for the
    sentence-first TTS pipeline.  The output preserves original order
    and together concatenates (with single spaces) back to the trimmed
    input — modulo collapsed inter-sentence whitespace.

    Behavior:

    * Empty / whitespace-only input returns an empty list.
    * Input with no terminators returns a single chunk (the stripped
      input).
    * Common abbreviations ("Dr.", "e.g.", "etc.") do not create a
      spurious split.
    * Trailing whitespace / newlines around each chunk are stripped.
    * Never raises — even on pathological input (runs of punctuation,
      malformed bracketing).
    """
    if not text:
        return []
    source = str(text).strip()
    if not source:
        return []

    chunks: list[str] = []
    start = 0
    for match in _SENTENCE_BOUNDARY_RE.finditer(source):
        boundary_start = match.start()
        segment = source[start:boundary_start]
        # Guard: do not split immediately after a known abbreviation.
        if _ends_with_abbreviation(segment):
            continue
        candidate = segment.strip()
        if candidate:
            chunks.append(candidate)
        start = match.end()

    tail = source[start:].strip()
    if tail:
        chunks.append(tail)

    # Empty input edge case (e.g. text was all punctuation): guarantee
    # at least one chunk when source was non-empty so callers can rely
    # on the "returns at least one sentence for non-empty input" shape.
    if not chunks and source:
        chunks.append(source)

    return chunks


# ---------------------------------------------------------------------------
# Speech reply shaping.
# ---------------------------------------------------------------------------


def prepare_speech_reply(
    text: str,
    *,
    max_sentences: int = DEFAULT_SPEECH_MAX_SENTENCES,
    max_chars: int = DEFAULT_SPEECH_MAX_CHARS,
) -> SpeechReplyShape:
    """Return a concise speech-only copy of *text*.

    Pipeline:

    1. Run *text* through :func:`normalize_text_for_tts` so the
       synthesizer never reads markdown control characters.
    2. Split into sentence chunks.
    3. Keep only the first *max_sentences*, stopping earlier if
       *max_chars* would be exceeded.

    The result is always a prefix of the normalized reply.  The helper
    never paraphrases, reorders, or invents — it is a pure truncation,
    so the spoken reply stays faithful to the written reply.

    Non-negotiables:

    * The caller's *text* reference is never mutated.
    * When the normalized reply already fits the budget (≤ *max_sentences*
      AND ≤ *max_chars*), it is returned as-is; ``truncated`` is
      ``False`` so instrumentation reflects the truth.
    * When the first sentence alone exceeds *max_chars*, the helper
      returns that single sentence anyway — the cap is a safety net
      for pathological long inputs, not a strict character budget.
      Speaking the first full sentence is always more faithful than
      cutting mid-sentence.
    * Degenerate inputs (empty, formatting-only) return an empty
      ``speech_text`` so the canonical TTS request validator can
      refuse fail-soft.

    *max_sentences* and *max_chars* are bounded-allowed overrides so
    tests and future operator config can tune the default; negative
    or zero values fall back to the module defaults rather than
    silently disabling truncation.
    """
    normalized = normalize_text_for_tts(text)
    if not normalized:
        return SpeechReplyShape(speech_text="", sentence_count=0, truncated=False)

    if max_sentences <= 0:
        max_sentences = DEFAULT_SPEECH_MAX_SENTENCES
    if max_chars <= 0:
        max_chars = DEFAULT_SPEECH_MAX_CHARS

    sentences = split_into_sentences(normalized)
    if not sentences:
        # Normalized text had no detectable sentences (e.g. a lone word).
        # Speak it as-is; the synthesizer can handle it.  Treat this as
        # a single-sentence reply that was not truncated.
        return SpeechReplyShape(speech_text=normalized, sentence_count=1, truncated=False)

    total_sentences = len(sentences)
    kept: list[str] = []
    running_chars = 0
    for index, sentence in enumerate(sentences):
        if index >= max_sentences:
            break
        # Always keep the first sentence regardless of char budget —
        # a half-reply is worse than a slightly-long first sentence.
        if index > 0 and running_chars + 1 + len(sentence) > max_chars:
            break
        kept.append(sentence)
        running_chars += len(sentence) + (1 if index > 0 else 0)

    # Guarantee at least one sentence makes it through.  If the first
    # sentence itself somehow got filtered (only possible on extreme
    # edge cases), fall back to the first sentence as-is so the spoken
    # reply is never empty when the written reply was not.
    if not kept:
        kept = [sentences[0]]

    speech_text = " ".join(kept).strip()
    truncated = len(kept) < total_sentences

    return SpeechReplyShape(
        speech_text=speech_text,
        sentence_count=len(kept),
        truncated=truncated,
    )
