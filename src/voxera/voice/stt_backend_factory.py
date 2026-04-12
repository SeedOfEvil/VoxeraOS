"""STT backend factory — runtime selection from voice foundation flags.

Provides a small, explicit factory that maps ``VoiceFoundationFlags``
to the appropriate ``STTBackend`` implementation.  This is the single
point where backend selection logic lives.

Supported backends:
- ``None`` / unrecognized  -> ``NullSTTBackend`` (truthful unavailable)
- ``"whisper_local"``      -> ``WhisperLocalBackend``

The factory is intentionally boring: no plugin registry, no dynamic
imports, no class scanning.  If more backends arrive, add an ``elif``.
"""

from __future__ import annotations

from .flags import VoiceFoundationFlags
from .stt_adapter import NullSTTBackend, STTBackend
from .whisper_backend import WhisperLocalBackend

# -- canonical backend identifiers -------------------------------------------

STT_BACKEND_WHISPER_LOCAL = "whisper_local"


def build_stt_backend(flags: VoiceFoundationFlags) -> STTBackend:
    """Build the STT backend selected by the current voice foundation flags.

    Returns a ``NullSTTBackend`` when:
    - voice input is not enabled (foundation off or input off)
    - no backend is configured (``voice_stt_backend`` is None/empty)
    - the configured backend identifier is not recognized

    Returns ``WhisperLocalBackend`` when ``voice_stt_backend`` is
    ``"whisper_local"``.

    The returned backend is always a valid ``STTBackend`` — callers
    never need to handle ``None``.
    """
    if not flags.voice_input_enabled:
        return NullSTTBackend()

    backend_id = (flags.voice_stt_backend or "").strip().lower()

    if not backend_id:
        return NullSTTBackend()

    if backend_id == STT_BACKEND_WHISPER_LOCAL:
        return WhisperLocalBackend()

    # Unrecognized backend — return NullSTTBackend rather than crashing.
    return NullSTTBackend()
