"""TTS backend factory — runtime selection from voice foundation flags.

Provides a small, explicit factory that maps ``VoiceFoundationFlags``
to the appropriate ``TTSBackend`` implementation.  This is the single
point where backend selection logic lives.

Supported backends:
- ``None`` / unrecognized  -> ``NullTTSBackend`` (truthful unavailable)
- ``"piper_local"``        -> ``PiperLocalBackend``

The factory is intentionally boring: no plugin registry, no dynamic
imports, no class scanning.  If more backends arrive, add an ``elif``.

A process-wide shared instance helper (:func:`get_shared_tts_backend`)
is also provided.  It reuses the same ``TTSBackend`` across calls so
heavy per-backend state (e.g. a loaded Piper voice) is not re-paid on
every dictation turn.  The cache key is the tuple of voice-flag
values that affect backend construction; a change to any of those
values rebuilds the instance on the next call so operator config
changes still take effect immediately.
"""

from __future__ import annotations

import threading

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


# -- process-wide shared backend ----------------------------------------------
#
# Mirrors the STT-side sharing model: Piper voice load is paid once
# per process instead of once per dictation turn when the operator
# opts into "speak replies".  The cache key is the small, immutable
# subset of flag values that affect backend construction.

_SharedKey = tuple[bool, str | None, str | None]
_shared_lock = threading.Lock()
_shared_backend: TTSBackend | None = None
_shared_key: _SharedKey | None = None


def _shared_key_for(flags: VoiceFoundationFlags) -> _SharedKey:
    return (
        flags.voice_output_enabled,
        (flags.voice_tts_backend or "").strip().lower() or None,
        (flags.voice_tts_piper_model or "").strip() or None,
    )


def get_shared_tts_backend(flags: VoiceFoundationFlags) -> TTSBackend:
    """Return a process-wide shared TTS backend for *flags*.

    The instance is reused across calls so the Piper voice loads
    once per process instead of once per reply synthesis.  A module
    lock guards construction so concurrent first-turn requests cannot
    race each other into duplicate voice loads.

    The instance is rebuilt when any flag value that affects backend
    construction changes; otherwise the previous instance is returned
    as-is.

    Fail-soft behaviour exactly matches :func:`build_tts_backend` —
    the returned backend is always a valid ``TTSBackend``.
    """
    global _shared_backend, _shared_key
    key = _shared_key_for(flags)
    with _shared_lock:
        if _shared_backend is not None and _shared_key == key:
            return _shared_backend
        _shared_backend = build_tts_backend(flags)
        _shared_key = key
        return _shared_backend


def reset_shared_tts_backend() -> None:
    """Drop the process-wide shared TTS backend.

    Exists for tests that want a clean slate between cases so one
    test's cached instance does not leak into the next.  Production
    callers should rely on the cache-key invalidation instead.
    """
    global _shared_backend, _shared_key
    with _shared_lock:
        _shared_backend = None
        _shared_key = None
