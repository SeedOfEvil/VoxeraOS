"""Progressive UI text chunking for streaming dictation.

The streaming dictation endpoint (``/chat/voice/stream``) emits a
completed assistant reply as a series of ``text_chunk`` events so
the browser can render a visibly-typing effect in the chat thread.
This module produces those chunks: small word groups, paced by a
short ``await asyncio.sleep`` between emits in the stream
endpoint.

Naming note -- "progressive", not "streaming":

* The upstream LLM call in ``run_vera_chat_turn`` is batch; it
  returns a full reply before this chunker ever runs.
* This helper paces a *completed* reply into smaller display units
  so the UI feels progressive.  It is NOT provider-token
  streaming.
* Callers and operator-facing surfaces should describe this as
  post-generation progressive rendering, not model streaming.

TTS is synthesised as a single full-reply audio file (see
``_run_voice_stream`` in ``app.py``) because chunked TTS produced a
line-by-line cadence that operators found worse than single-file
playback.  The progressive text chunker is therefore the sole place
chunking happens today.

Public surface:

* :func:`split_progressive_text_chunks` -- takes the canonical
  assistant reply text and returns an ordered list of small word-
  group chunks ready to be emitted as ``text_chunk`` events.

Non-negotiables:

* The input string is never mutated in place.
* The concatenation of all chunks with single-space separators
  reproduces the original text with whitespace normalised to single
  spaces.  No content is dropped.
* Empty / whitespace-only input returns ``[]``.
"""

from __future__ import annotations

__all__ = ["split_progressive_text_chunks"]

# ~4 words per chunk is a balance between "visibly typing" (too
# many small chunks overwhelm the DOM and feel jittery) and "not
# progressive enough" (too few chunks and the reply still lands in
# a burst).  Paired with a small inter-chunk emit delay in the
# stream endpoint, this produces a readable typing cadence.
_DEFAULT_WORDS_PER_CHUNK = 4


def split_progressive_text_chunks(
    text: str, *, words_per_chunk: int = _DEFAULT_WORDS_PER_CHUNK
) -> list[str]:
    """Return small word-group chunks for progressive UI rendering.

    Empty / whitespace-only input returns ``[]``.  For non-empty
    input, the text is split on whitespace into word tokens, then
    grouped into chunks of ``words_per_chunk`` preserving order.

    ``words_per_chunk`` must be >= 1; values below are clamped to 1
    so a caller cannot accidentally request zero-width chunks.
    """
    if not text:
        return []
    words = str(text).split()
    if not words:
        return []
    step = max(1, int(words_per_chunk))
    return [" ".join(words[i : i + step]) for i in range(0, len(words), step)]
