from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .models import InputOrigin

if TYPE_CHECKING:
    from .flags import VoiceFoundationFlags
    from .stt_adapter import STTBackend
    from .stt_protocol import STTResponse


class VoiceInputDisabledError(RuntimeError):
    pass


@dataclass(frozen=True)
class VoiceTranscriptIngest:
    transcript_text: str
    input_origin: str


def normalize_transcript_text(raw_transcript: str) -> str:
    return " ".join(raw_transcript.strip().split())


def ingest_voice_transcript(
    *, transcript_text: str, voice_input_enabled: bool
) -> VoiceTranscriptIngest:
    if not voice_input_enabled:
        raise VoiceInputDisabledError("Voice transcript input is disabled by runtime flags.")
    normalized = normalize_transcript_text(transcript_text)
    if not normalized:
        raise ValueError("Voice transcript text is required.")
    return VoiceTranscriptIngest(
        transcript_text=normalized,
        input_origin=InputOrigin.VOICE_TRANSCRIPT.value,
    )


def transcribe_audio_file(
    *,
    audio_path: str,
    flags: VoiceFoundationFlags,
    language: str | None = None,
    session_id: str | None = None,
    backend: STTBackend | None = None,
) -> STTResponse:
    """Transcribe an audio file through the canonical STT pipeline.

    Builds an ``STTRequest``, selects the appropriate backend from
    *flags* via the backend factory (or uses a caller-supplied
    *backend*), and runs the request through ``transcribe_stt_request``.
    Always returns a truthful ``STTResponse`` — never raises on
    transcription failure.

    Pass a pre-built *backend* to override the default instance
    entirely — useful for tests and specialised callers.  When
    *backend* is ``None`` (the default), the call resolves the
    process-wide shared instance via
    :func:`voxera.voice.stt_backend_factory.get_shared_stt_backend`,
    so heavy per-backend state (e.g. a loaded faster-whisper model)
    is paid once per process rather than once per call.  The shared
    instance is invalidated automatically when any *flags* value that
    affects backend construction changes.

    This is the recommended entry point for audio-file transcription.
    Only ``audio_file`` is supported as an input source.  Microphone
    and stream sources are not supported by this function — they
    remain future work.

    For async contexts (Vera chat, FastAPI routes), use
    :func:`transcribe_audio_file_async` instead — it runs the
    synchronous backend in a thread so it does not block the event
    loop.

    Fail-soft behavior:
    - Voice input disabled -> unavailable (via NullSTTBackend)
    - No backend configured -> unavailable (via NullSTTBackend)
    - Unknown backend -> unavailable (via NullSTTBackend)
    - Backend dependency missing -> unavailable (backend reports it)
    - File not found -> failed (backend reports it)
    - Empty transcript -> failed (adapter reports it)
    - Success -> succeeded with normalized transcript
    """
    from .stt_adapter import transcribe_stt_request
    from .stt_backend_factory import get_shared_stt_backend
    from .stt_protocol import STT_SOURCE_AUDIO_FILE, build_stt_request

    # Prefer the process-wide shared backend so heavy state (e.g. the
    # faster-whisper model) is loaded once per process rather than once
    # per dictation turn.  A caller-supplied *backend* still wins so
    # tests and specialised callers can inject bespoke adapters.
    selected_backend = backend if backend is not None else get_shared_stt_backend(flags)
    request = build_stt_request(
        input_source=STT_SOURCE_AUDIO_FILE,
        audio_path=audio_path,
        language=language,
        session_id=session_id,
    )
    return transcribe_stt_request(request, adapter=selected_backend)


async def transcribe_audio_file_async(
    *,
    audio_path: str,
    flags: VoiceFoundationFlags,
    language: str | None = None,
    session_id: str | None = None,
    backend: STTBackend | None = None,
) -> STTResponse:
    """Async variant of :func:`transcribe_audio_file`.

    Runs the synchronous transcription path in a thread via
    ``asyncio.to_thread()`` so it does not block the event loop.
    Preserves all fail-soft semantics of the sync entry point.

    Use this from async contexts (Vera chat, FastAPI routes) instead
    of the sync :func:`transcribe_audio_file`.
    """
    import asyncio

    return await asyncio.to_thread(
        _transcribe_audio_file_sync,
        audio_path=audio_path,
        flags=flags,
        language=language,
        session_id=session_id,
        backend=backend,
    )


def _transcribe_audio_file_sync(
    *,
    audio_path: str,
    flags: VoiceFoundationFlags,
    language: str | None = None,
    session_id: str | None = None,
    backend: STTBackend | None = None,
) -> STTResponse:
    """Internal sync implementation for the async wrapper.

    Identical to ``transcribe_audio_file`` — exists only so
    ``asyncio.to_thread`` can call it with keyword arguments.
    """
    return transcribe_audio_file(
        audio_path=audio_path,
        flags=flags,
        language=language,
        session_id=session_id,
        backend=backend,
    )
