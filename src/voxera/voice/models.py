from __future__ import annotations

import enum


class InputOrigin(str, enum.Enum):
    TYPED = "typed"
    VOICE_TRANSCRIPT = "voice_transcript"


def normalize_input_origin(raw: str | None) -> InputOrigin:
    candidate = str(raw or "").strip().lower()
    if candidate == InputOrigin.VOICE_TRANSCRIPT.value:
        return InputOrigin.VOICE_TRANSCRIPT
    return InputOrigin.TYPED
