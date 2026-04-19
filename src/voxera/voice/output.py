from __future__ import annotations

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
    from .tts_adapter import synthesize_tts_request
    from .tts_backend_factory import get_shared_tts_backend
    from .tts_protocol import build_tts_request

    # Prefer the process-wide shared backend so heavy state (e.g. the
    # Piper voice) is loaded once per process rather than once per
    # reply synthesis.  A caller-supplied *backend* still wins so
    # tests and specialised callers can inject bespoke adapters.
    selected_backend = backend if backend is not None else get_shared_tts_backend(flags)
    request = build_tts_request(
        text=text,
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
    import asyncio

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
