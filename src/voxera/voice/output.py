from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .flags import VoiceFoundationFlags
    from .tts_adapter import TTSBackend
    from .tts_protocol import TTSResponse


def voice_output_status(flags: VoiceFoundationFlags) -> dict[str, object]:
    attempted = flags.voice_output_enabled
    configured = bool(flags.voice_tts_backend)
    if not flags.enable_voice_foundation:
        reason = "voice_foundation_disabled"
    elif not flags.enable_voice_output:
        reason = "voice_output_disabled"
    elif not configured:
        reason = "voice_output_backend_missing"
    else:
        reason = "voice_output_ready"
    return {
        "voice_output_attempted": attempted,
        "voice_output_backend": flags.voice_tts_backend,
        "voice_output_reason": reason,
    }


def synthesize_text(
    *,
    text: str,
    flags: VoiceFoundationFlags,
    voice_id: str | None = None,
    language: str | None = None,
    speed: float = 1.0,
    output_format: str = "wav",
    session_id: str | None = None,
    backend: TTSBackend | None = None,
) -> TTSResponse:
    """Synthesize text through the canonical TTS pipeline.

    Builds a ``TTSRequest``, selects the appropriate backend from
    *flags* via the backend factory (or uses a caller-supplied
    *backend*), and runs the request through ``synthesize_tts_request``.
    Always returns a truthful ``TTSResponse`` — never raises on
    synthesis failure.

    Pass a pre-built *backend* to override the default instance
    entirely — useful for tests and specialised callers.  When
    *backend* is ``None`` (the default), the call resolves the
    process-wide shared instance via
    :func:`voxera.voice.tts_backend_factory.get_shared_tts_backend`,
    so heavy per-backend state (e.g. a loaded Piper voice) is paid
    once per process rather than once per call.  The shared instance
    is invalidated automatically when any *flags* value that affects
    backend construction changes.

    This is the recommended entry point for text-to-speech synthesis.
    Output is artifact-oriented (``audio_path``), not playback-oriented.
    No playback or browser audio integration is provided.

    For async contexts (Vera chat, FastAPI routes), use
    :func:`synthesize_text_async` instead — it runs the synchronous
    backend in a thread so it does not block the event loop.

    Fail-soft behavior:
    - Voice output disabled -> unavailable (via NullTTSBackend)
    - No backend configured -> unavailable (via NullTTSBackend)
    - Unknown backend -> unavailable (via NullTTSBackend)
    - Backend dependency missing -> unavailable (backend reports it)
    - Unsupported format -> unsupported (backend reports it)
    - Empty text -> raises ValueError (request validation)
    - Success -> succeeded with real audio_path
    """
    from .speech_normalize import normalize_text_for_tts
    from .tts_adapter import synthesize_tts_request
    from .tts_backend_factory import get_shared_tts_backend
    from .tts_protocol import build_tts_request

    # Prefer the process-wide shared backend so heavy state (e.g. the
    # Piper voice) is loaded once per process rather than once per
    # reply synthesis.  A caller-supplied *backend* still wins so
    # tests and specialised callers can inject bespoke adapters.
    selected_backend = backend if backend is not None else get_shared_tts_backend(flags)

    # Speech-only normalization: strip markdown/control syntax so the
    # synthesizer does not read formatting characters literally.  This
    # never mutates the assistant text stored in the session or shown
    # in the UI -- only the TTS request text is normalized.  The
    # helper is conservative and returns empty only when input was
    # empty (it falls back to the stripped original if normalization
    # would remove every character), so ``build_tts_request`` still
    # enforces the non-empty invariant downstream.
    speech_text = normalize_text_for_tts(text)

    request = build_tts_request(
        text=speech_text,
        voice_id=voice_id,
        language=language,
        speed=speed,
        output_format=output_format,
        session_id=session_id,
    )
    return synthesize_tts_request(request, adapter=selected_backend)


async def synthesize_text_async(
    *,
    text: str,
    flags: VoiceFoundationFlags,
    voice_id: str | None = None,
    language: str | None = None,
    speed: float = 1.0,
    output_format: str = "wav",
    session_id: str | None = None,
    backend: TTSBackend | None = None,
) -> TTSResponse:
    """Async variant of :func:`synthesize_text`.

    Runs the synchronous synthesis path in a thread via
    ``asyncio.to_thread()`` so it does not block the event loop.
    Preserves all fail-soft semantics of the sync entry point.

    Use this from async contexts (Vera chat, FastAPI routes) instead
    of the sync :func:`synthesize_text`.
    """
    return await asyncio.to_thread(
        synthesize_text,
        text=text,
        flags=flags,
        voice_id=voice_id,
        language=language,
        speed=speed,
        output_format=output_format,
        session_id=session_id,
        backend=backend,
    )


# ---------------------------------------------------------------------------
# Sentence-first TTS synthesis
# ---------------------------------------------------------------------------
#
# The ``synthesize_speech_reply_async`` helper is the canonical entry point
# for "speak Vera's reply over voice" flows.  It reduces time-to-first-audio
# by (1) shaping a concise speech-only copy of the reply so the synthesizer
# has less to do, and (2) splitting that copy into ordered sentence chunks
# that are synthesized concurrently so the total wall time is dominated by
# the slowest chunk rather than the sum of all chunks.
#
# Operator trust model:
# - The canonical written reply (``text``) is never mutated — this helper
#   only reads it to derive a spoken copy.
# - Every chunk's synthesis result is a full truthful ``TTSResponse``;
#   failures surface as ``failed`` / ``unavailable`` per the adapter
#   contract.  The aggregate result carries them all so the caller can
#   decide whether to treat a partial failure as success (first chunk
#   succeeded) or full failure (no chunks succeeded).
# - Timing fields are wall-clock measurements only.  Stages that did not
#   run stay ``None`` — never fabricated zero values.


@dataclass(frozen=True)
class SpeechReplyTTSResult:
    """Aggregate result of a sentence-first TTS synthesis run.

    * ``speech_text`` — the concise speech-only copy that was split into
      chunks and synthesized.  May be shorter than the caller's written
      reply; the full written reply stays authoritative in chat.
    * ``sentence_count`` — number of sentence chunks spoken (equals
      ``len(responses)``).
    * ``truncated`` — ``True`` when the speech shaper dropped one or
      more sentences from the end of the written reply.  Operator UIs
      can render a truthful "spoken reply is shorter" hint without
      inferring from other fields.
    * ``responses`` — per-chunk ``TTSResponse`` in spoken order.  Empty
      when the written reply was empty after shaping (e.g. formatting-
      only text); callers must handle that as "nothing to speak".
    * ``speech_text_prepare_ms`` — wall-clock time spent deriving the
      concise speech text from the written reply.
    * ``speech_sentence_split_ms`` — wall-clock time spent splitting
      the speech text into sentence chunks.
    * ``tts_first_chunk_ms`` — wall-clock time from synthesis start
      until the FIRST chunk's synthesis completed.  This is the
      operator-visible time-to-first-audio for the sentence-first
      playback path.  ``None`` when no chunks were synthesized.
    * ``tts_total_synthesis_ms`` — wall-clock time from synthesis
      start until ALL chunks completed (including the time the
      event loop spent scheduling concurrent chunks).  ``None``
      when no chunks were synthesized.
    """

    speech_text: str
    sentence_count: int
    truncated: bool
    responses: list[TTSResponse] = field(default_factory=list)
    speech_text_prepare_ms: int | None = None
    speech_sentence_split_ms: int | None = None
    tts_first_chunk_ms: int | None = None
    tts_total_synthesis_ms: int | None = None


async def synthesize_speech_reply_async(
    *,
    text: str,
    flags: VoiceFoundationFlags,
    voice_id: str | None = None,
    language: str | None = None,
    speed: float = 1.0,
    output_format: str = "wav",
    session_id: str | None = None,
    backend: TTSBackend | None = None,
    max_sentences: int | None = None,
    max_chars: int | None = None,
) -> SpeechReplyTTSResult:
    """Synthesize a concise spoken reply for *text*, one sentence per chunk.

    Pipeline:

    1. Derive a concise speech-only copy of *text* via
       :func:`prepare_speech_reply`.  The written reply itself is
       never mutated — this is a derived string.
    2. Split that copy into sentence chunks.
    3. Dispatch each chunk through :func:`synthesize_text_async`
       concurrently (``asyncio.gather``).  The first-completed chunk's
       wall-clock time is captured as ``tts_first_chunk_ms`` so
       operators can see the true time-to-first-audio.
    4. Return a :class:`SpeechReplyTTSResult` carrying per-chunk
       responses (in spoken order) and truthful sub-stage timings.

    Fail-soft: per-chunk synthesis failures surface as ``failed`` /
    ``unavailable`` per the canonical TTS contract and the aggregate
    result still returns — the caller decides how to present partial
    failures.  This helper itself never raises on synthesis failure,
    matching :func:`synthesize_text_async`.

    Empty / formatting-only input returns a result with
    ``speech_text=""`` and ``responses=[]``; the caller should treat
    this as "nothing to speak" without invoking the backend.  Per
    the trust model, the canonical text reply still appears in chat
    regardless of what this helper returns.

    *max_sentences* / *max_chars*: optional overrides for the bounded
    speech reply budget.  ``None`` uses the module defaults in
    :mod:`voxera.voice.speech_reply`; passing explicit values lets
    tests and future operator config tune the cap without editing
    the defaults globally.
    """
    from .speech_reply import (
        DEFAULT_SPEECH_MAX_CHARS,
        DEFAULT_SPEECH_MAX_SENTENCES,
        prepare_speech_reply,
        split_into_sentences,
    )

    # -- step 1: derive concise speech text -----------------------------------
    prepare_started_ms = int(time.time() * 1000)
    shape = prepare_speech_reply(
        text,
        max_sentences=(
            max_sentences if max_sentences is not None else DEFAULT_SPEECH_MAX_SENTENCES
        ),
        max_chars=(max_chars if max_chars is not None else DEFAULT_SPEECH_MAX_CHARS),
    )
    prepare_ms = max(0, int(time.time() * 1000) - prepare_started_ms)

    if not shape.speech_text:
        # Nothing to speak.  Report truthful stage timings so operator
        # diagnostics show where time went (all in the prepare stage)
        # without fabricating synthesis timings for a stage that did
        # not run.
        return SpeechReplyTTSResult(
            speech_text="",
            sentence_count=0,
            truncated=shape.truncated,
            responses=[],
            speech_text_prepare_ms=prepare_ms,
            speech_sentence_split_ms=None,
            tts_first_chunk_ms=None,
            tts_total_synthesis_ms=None,
        )

    # -- step 2: split into sentence chunks -----------------------------------
    split_started_ms = int(time.time() * 1000)
    sentences = split_into_sentences(shape.speech_text)
    split_ms = max(0, int(time.time() * 1000) - split_started_ms)

    if not sentences:
        # Defensive fallback: treat the whole speech_text as one chunk.
        # split_into_sentences is documented to return at least one chunk
        # for non-empty input, so this branch is belt-and-braces.
        sentences = [shape.speech_text]

    # -- step 3: synthesize chunks concurrently -------------------------------
    #
    # Each chunk runs through the canonical ``synthesize_text_async`` path
    # so every chunk gets its own truthful ``TTSResponse`` with backend
    # name, error class, and ``inference_ms``.  Chunks run as parallel
    # asyncio tasks so the total wall time is dominated by the slowest
    # chunk rather than the sum of all chunks — the core time-to-first-
    # audio win over "synthesize the whole reply in one pass".
    #
    # Ordering guarantee: ``responses[i]`` is always the synthesis of
    # ``sentences[i]``.  We await chunk 0 explicitly to capture the
    # time-to-first-audio metric operators care about (the browser
    # plays chunk 0 first regardless of which chunk's synthesis
    # finished first wall-clock), then await the remainder so the
    # aggregate result is fully materialized before we return.

    synth_started_ms = int(time.time() * 1000)

    tasks: list[asyncio.Task[TTSResponse]] = [
        asyncio.create_task(
            synthesize_text_async(
                text=sentence,
                flags=flags,
                voice_id=voice_id,
                language=language,
                speed=speed,
                output_format=output_format,
                session_id=session_id,
                backend=backend,
            )
        )
        for sentence in sentences
    ]

    # Await chunk 0 first — this is the sentence the browser plays
    # earliest, so its completion time is the operator-visible time-
    # to-first-audio.  Remaining chunks keep running concurrently in
    # the background while we take this measurement.
    await tasks[0]
    first_chunk_ms = max(0, int(time.time() * 1000) - synth_started_ms)

    # Await the rest so the aggregate result is fully materialized.
    # ``gather`` preserves order; we read task results directly so a
    # single-chunk reply is handled without a second await.
    if len(tasks) > 1:
        await asyncio.gather(*tasks[1:])

    total_ms = max(0, int(time.time() * 1000) - synth_started_ms)
    responses = [t.result() for t in tasks]

    return SpeechReplyTTSResult(
        speech_text=shape.speech_text,
        sentence_count=len(responses),
        truncated=shape.truncated,
        responses=responses,
        speech_text_prepare_ms=prepare_ms,
        speech_sentence_split_ms=split_ms,
        tts_first_chunk_ms=first_chunk_ms,
        tts_total_synthesis_ms=total_ms,
    )
