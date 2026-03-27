from __future__ import annotations

from dataclasses import dataclass

from .models import InputOrigin


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
