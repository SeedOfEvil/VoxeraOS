"""TTS backend factory — runtime selection from voice foundation flags.

Provides a small, explicit factory that maps ``VoiceFoundationFlags``
to the appropriate ``TTSBackend`` implementation.  This is the single
point where backend selection logic lives.

Supported backends:
- ``None`` / unrecognized  -> ``NullTTSBackend`` (truthful unavailable)
- ``"piper_local"``        -> ``PiperLocalBackend``

The factory is intentionally boring: no plugin registry, no dynamic
imports, no class scanning.  If more backends arrive, add an ``elif``.
"""

from __future__ import annotations

from .flags import VoiceFoundationFlags
from .piper_backend import PiperLocalBackend
from .tts_adapter import NullTTSBackend, TTSBackend

# -- canonical backend identifiers -------------------------------------------

TTS_BACKEND_PIPER_LOCAL = "piper_local"


def build_tts_backend(flags: VoiceFoundationFlags) -> TTSBackend:
    """Build the TTS backend selected by the current voice foundation flags.

    Returns a ``NullTTSBackend`` when:
    - voice output is not enabled (foundation off or output off)
    - no backend is configured (``voice_tts_backend`` is None/empty)
    - the configured backend identifier is not recognized

    Returns ``PiperLocalBackend`` when ``voice_tts_backend`` is
    ``"piper_local"``.

    The returned backend is always a valid ``TTSBackend`` — callers
    never need to handle ``None``.
    """
    if not flags.voice_output_enabled:
        return NullTTSBackend()

    backend_id = (flags.voice_tts_backend or "").strip().lower()

    if not backend_id:
        return NullTTSBackend()

    if backend_id == TTS_BACKEND_PIPER_LOCAL:
        return PiperLocalBackend(model=flags.voice_tts_piper_model)

    # Unrecognized backend — return NullTTSBackend with a specific reason
    # so operators can see which identifier was rejected.
    return NullTTSBackend(reason=f"TTS backend {flags.voice_tts_backend!r} is not recognized")
