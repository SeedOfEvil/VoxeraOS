"""STT backend factory — runtime selection from voice foundation flags.

Provides a small, explicit factory that maps ``VoiceFoundationFlags``
to the appropriate ``STTBackend`` implementation.  This is the single
point where backend selection logic lives.

Supported backends:
- ``None`` / unrecognized  -> ``NullSTTBackend`` (truthful unavailable)
- ``"whisper_local"``      -> ``WhisperLocalBackend``
- ``"moonshine_local"``    -> ``MoonshineLocalBackend``

The factory is intentionally boring: no plugin registry, no dynamic
imports, no class scanning.  If more backends arrive, add an ``elif``.

A process-wide shared instance helper (:func:`get_shared_stt_backend`)
is also provided.  It reuses the same ``STTBackend`` across calls so
heavy per-backend state (e.g. a loaded faster-whisper model) is not
re-paid on every dictation turn.  The cache key is the tuple of
voice-flag values that affect backend construction; a change to any
of those values rebuilds the instance on the next call so operator
config changes still take effect immediately.
"""

from __future__ import annotations

import threading

from .flags import VoiceFoundationFlags
from .moonshine_backend import MoonshineLocalBackend
from .stt_adapter import NullSTTBackend, STTBackend
from .whisper_backend import WhisperLocalBackend

# -- canonical backend identifiers -------------------------------------------

STT_BACKEND_WHISPER_LOCAL = "whisper_local"
STT_BACKEND_MOONSHINE_LOCAL = "moonshine_local"

# Bounded allow-list surfaced in operator UIs (panel voice options,
# setup wizard).  Keeps the panel form a small dropdown rather than a
# free-text field the operator can typo into.  The factory still
# accepts any truthy string for backwards-compat with env-only
# deployments that pin a backend outside this list, but the panel
# UX stays curated.
STT_BACKEND_CHOICES: tuple[str, ...] = (
    STT_BACKEND_WHISPER_LOCAL,
    STT_BACKEND_MOONSHINE_LOCAL,
)


def build_stt_backend(flags: VoiceFoundationFlags) -> STTBackend:
    """Build the STT backend selected by the current voice foundation flags.

    Returns a ``NullSTTBackend`` when:
    - voice input is not enabled (foundation off or input off)
    - no backend is configured (``voice_stt_backend`` is None/empty)
    - the configured backend identifier is not recognized

    Returns ``WhisperLocalBackend`` when ``voice_stt_backend`` is
    ``"whisper_local"``.  The operator-selected whisper model id
    (``voice_stt_whisper_model``) is threaded through to the backend
    so the factory is the only place that maps config → backend
    construction.  ``None`` preserves the backend's existing default
    (``base``, or a ``VOXERA_VOICE_STT_WHISPER_MODEL`` env override).

    Returns ``MoonshineLocalBackend`` when ``voice_stt_backend`` is
    ``"moonshine_local"``.  The operator-selected moonshine model id
    (``voice_stt_moonshine_model``) is threaded through so the
    factory remains the only place that maps config → backend
    construction.  ``None`` preserves the backend default
    (``moonshine/base``, or a ``VOXERA_VOICE_STT_MOONSHINE_MODEL``
    env override).

    The returned backend is always a valid ``STTBackend`` — callers
    never need to handle ``None``.
    """
    if not flags.voice_input_enabled:
        return NullSTTBackend()

    backend_id = (flags.voice_stt_backend or "").strip().lower()

    if not backend_id:
        return NullSTTBackend()

    if backend_id == STT_BACKEND_WHISPER_LOCAL:
        model = (flags.voice_stt_whisper_model or "").strip() or None
        return WhisperLocalBackend(model_size=model)

    if backend_id == STT_BACKEND_MOONSHINE_LOCAL:
        moonshine_model = (flags.voice_stt_moonshine_model or "").strip() or None
        return MoonshineLocalBackend(model_name=moonshine_model)

    # Unrecognized backend — return NullSTTBackend with a specific reason
    # so operators can see which identifier was rejected.
    return NullSTTBackend(reason=f"STT backend {flags.voice_stt_backend!r} is not recognized")


# -- process-wide shared backend ----------------------------------------------
#
# Dictation latency is dominated by first-call model load.  The
# backend instance itself is cheap to construct, but the
# CTranslate2 / ONNX model on ``_ensure_model()`` can take multiple
# seconds on a cold start.  Reusing the instance across turns pays
# that cost once per process, not per request.
#
# Cache key: the small, immutable subset of flag values that actually
# change backend construction (voice_input_enabled plus the explicit
# backend identifier and model selections).  When any of those values
# changes, ``get_shared_stt_backend`` rebuilds the instance on the
# next call so operator reconfiguration takes effect immediately.
#
# Thread safety: accesses are guarded by a module-level lock so two
# concurrent first-turn requests cannot race each other into building
# two models in parallel.

_SharedKey = tuple[bool, str | None, str | None, str | None]
_shared_lock = threading.Lock()
_shared_backend: STTBackend | None = None
_shared_key: _SharedKey | None = None


def _shared_key_for(flags: VoiceFoundationFlags) -> _SharedKey:
    # Scope note: the key covers everything that flows through
    # ``VoiceFoundationFlags`` into backend construction.  It does NOT
    # cover env-only knobs that backends read directly
    # (``VOXERA_VOICE_STT_WHISPER_DEVICE``,
    # ``VOXERA_VOICE_STT_WHISPER_COMPUTE_TYPE``).  Those are
    # process-start config in practice; changing them at runtime
    # without an explicit ``reset_shared_stt_backend()`` call will
    # keep the stale instance alive.  If runtime env-var
    # reconfiguration ever becomes a supported flow, either extend
    # this key or reset on the reconfig boundary.
    return (
        flags.voice_input_enabled,
        (flags.voice_stt_backend or "").strip().lower() or None,
        (flags.voice_stt_whisper_model or "").strip() or None,
        (flags.voice_stt_moonshine_model or "").strip() or None,
    )


def get_shared_stt_backend(flags: VoiceFoundationFlags) -> STTBackend:
    """Return a process-wide shared STT backend for *flags*.

    The instance is reused across calls so the model loads once per
    process instead of once per dictation turn.  Construction is
    guarded by a module-level lock so concurrent first-turn requests
    cannot race each other into duplicate model loads.

    The instance is rebuilt when any flag value that affects backend
    construction changes; otherwise the previous instance is returned
    as-is.

    Fail-soft behaviour exactly matches :func:`build_stt_backend` —
    the returned backend is always a valid ``STTBackend``.
    """
    global _shared_backend, _shared_key
    key = _shared_key_for(flags)
    with _shared_lock:
        if _shared_backend is not None and _shared_key == key:
            return _shared_backend
        _shared_backend = build_stt_backend(flags)
        _shared_key = key
        return _shared_backend


def reset_shared_stt_backend() -> None:
    """Drop the process-wide shared STT backend.

    Exists for tests that want a clean slate between cases so one
    test's cached instance does not leak into the next.  Production
    callers should rely on the cache-key invalidation instead.
    """
    global _shared_backend, _shared_key
    with _shared_lock:
        _shared_backend = None
        _shared_key = None
