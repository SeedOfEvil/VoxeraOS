from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .models import InputOrigin

if TYPE_CHECKING:
    from .flags import VoiceFoundationFlags
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
) -> STTResponse:
    """Transcribe an audio file through the canonical STT pipeline.

    Builds an ``STTRequest``, selects the appropriate backend from
    *flags* via the backend factory, and runs the request through
    ``transcribe_stt_request``.  Always returns a truthful
    ``STTResponse`` — never raises on transcription failure.

    This is the recommended entry point for audio-file transcription.
    Only ``audio_file`` is supported as an input source.  Microphone
    and stream sources are not supported by this function — they
    remain future work.

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
    from .stt_backend_factory import build_stt_backend
    from .stt_protocol import STT_SOURCE_AUDIO_FILE, build_stt_request

    backend = build_stt_backend(flags)
    request = build_stt_request(
        input_source=STT_SOURCE_AUDIO_FILE,
        audio_path=audio_path,
        language=language,
        session_id=session_id,
    )
    return transcribe_stt_request(request, adapter=backend)
