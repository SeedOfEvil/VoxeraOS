"""TTS backend adapter boundary.

Defines the runtime adapter interface for text-to-speech backends and
the fail-soft synthesis entry point that consumes ``TTSRequest``
and returns ``TTSResponse``.

The adapter boundary makes these states explicit and first-class:
- backend unavailable (no adapter configured)
- backend unsupported (adapter rejects the voice/format combination)
- backend failure (adapter raises during synthesis)
- backend success (adapter returns an audio artifact path)

The ``NullTTSBackend`` is the default when no real backend is configured.
It always returns an honest "unavailable" result — it never pretends
synthesis occurred.

The ``synthesize_tts_request`` entry point is the canonical fail-soft
path: it handles missing adapters, adapter exceptions, and missing
audio artifacts without crashing, and always returns a truthful
``TTSResponse``.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Protocol

from .tts_protocol import (
    TTS_ERROR_BACKEND_ERROR,
    TTS_ERROR_BACKEND_MISSING,
    TTS_ERROR_DISABLED,
    TTS_ERROR_UNSUPPORTED_FORMAT,
    TTS_STATUS_FAILED,
    TTS_STATUS_SUCCEEDED,
    TTS_STATUS_UNAVAILABLE,
    TTS_STATUS_UNSUPPORTED,
    TTSRequest,
    TTSResponse,
    build_tts_response,
    build_tts_unavailable_response,
)

# Error classes that signal availability problems (subsystem cannot service
# the request at all), as opposed to runtime failures (subsystem tried but
# encountered an error).  Used by ``synthesize_tts_request`` to choose
# ``unavailable`` vs ``failed`` status truthfully.
_UNAVAILABLE_ERROR_CLASSES: frozenset[str] = frozenset(
    {TTS_ERROR_DISABLED, TTS_ERROR_BACKEND_MISSING}
)

# -- adapter result ---------------------------------------------------------


@dataclass(frozen=True)
class TTSAdapterResult:
    """Raw result returned by a TTS backend adapter.

    This is the adapter-internal shape — callers never see it directly.
    The ``synthesize_tts_request`` entry point wraps it into a
    ``TTSResponse``.

    Optional timing fields are adapter-reported observability data:
    - ``inference_ms``: wall-clock time spent in the synthesis call.
    - ``audio_duration_ms``: duration of the output audio, if known.
    These are best-effort — backends that cannot measure them leave
    them as ``None``.
    """

    audio_path: str | None
    audio_duration_ms: int | None = None
    inference_ms: int | None = None
    error: str | None = None
    error_class: str | None = None


# -- adapter protocol -------------------------------------------------------


class TTSBackend(Protocol):
    """Structural interface for a TTS backend adapter.

    Mirrors the ``STTBackend`` protocol pattern in ``stt_adapter.py``.
    Implementations do not need to inherit — they only need to satisfy
    the structural signature.
    """

    @property
    def backend_name(self) -> str:
        """Stable identifier for this backend (used in responses/logs)."""
        ...

    def supports_voice(self, voice_id: str) -> bool:
        """Return whether this backend supports the given voice id.

        Allows callers to check voice support upfront (e.g. for UI
        gating) without triggering a full synthesis attempt.
        """
        ...

    def synthesize(self, request: TTSRequest) -> TTSAdapterResult:
        """Attempt to synthesize the given request.

        Returns a ``TTSAdapterResult`` on success or partial failure.
        Raises ``TTSBackendUnsupportedError`` if the voice or format is
        not supported by this backend.
        May raise any other exception on unexpected failure — the
        ``synthesize_tts_request`` entry point catches these fail-soft.
        """
        ...


# -- adapter exceptions -----------------------------------------------------


class TTSBackendUnsupportedError(Exception):
    """Raised by an adapter when it does not support the requested voice or format."""


# -- null adapter (default when unconfigured) --------------------------------


class NullTTSBackend:
    """Truthful no-op backend: always reports unavailable.

    Used when no real TTS backend is configured.  Never pretends
    synthesis occurred.

    An optional *reason* can be passed at construction time to
    distinguish "not configured" from "unrecognized backend" in
    error messages.  The default covers the common unconfigured case.
    """

    def __init__(self, *, reason: str = "No TTS backend is configured") -> None:
        self._reason = reason

    @property
    def backend_name(self) -> str:
        return "null"

    def supports_voice(self, voice_id: str) -> bool:
        return False

    def synthesize(self, request: TTSRequest) -> TTSAdapterResult:
        return TTSAdapterResult(
            audio_path=None,
            error=self._reason,
            error_class=TTS_ERROR_BACKEND_MISSING,
        )


# -- fail-soft synthesis entry point -----------------------------------------


def synthesize_tts_request(
    request: TTSRequest,
    adapter: TTSBackend | None = None,
) -> TTSResponse:
    """Fail-soft synthesis entry point.

    Takes a ``TTSRequest`` and an optional adapter, and always returns
    a truthful ``TTSResponse`` — never raises.

    Behavior:
    - If *adapter* is ``None``: returns unavailable (backend_missing).
    - If the adapter raises ``TTSBackendUnsupportedError``: returns
      unsupported with the adapter's message.
    - If the adapter raises any other exception: returns failed with
      a backend_error error class.
    - If the adapter returns a result with an availability-class error
      (``disabled``, ``backend_missing``): returns unavailable.
    - If the adapter returns a result with any other error: returns
      failed with the adapter's error details.
    - If the adapter returns a result with no ``audio_path``: returns
      failed — does not fake success.
    - Otherwise: returns succeeded with the audio artifact path.
    """
    started_at_ms = int(time.time() * 1000)

    # -- no adapter configured -----------------------------------------------
    if adapter is None:
        return build_tts_unavailable_response(
            request_id=request.request_id,
            reason="No TTS backend adapter provided",
            error_class=TTS_ERROR_BACKEND_MISSING,
        )

    backend_name = adapter.backend_name

    # -- call adapter (fail-soft) --------------------------------------------
    try:
        result = adapter.synthesize(request)
    except TTSBackendUnsupportedError as exc:
        finished_at_ms = int(time.time() * 1000)
        return build_tts_response(
            request_id=request.request_id,
            status=TTS_STATUS_UNSUPPORTED,
            error=str(exc) or f"Voice {request.voice_id!r} not supported",
            error_class=TTS_ERROR_UNSUPPORTED_FORMAT,
            backend=backend_name,
            started_at_ms=started_at_ms,
            finished_at_ms=finished_at_ms,
        )
    except Exception as exc:
        finished_at_ms = int(time.time() * 1000)
        return build_tts_response(
            request_id=request.request_id,
            status=TTS_STATUS_FAILED,
            error=f"TTS backend error: {exc}",
            error_class=TTS_ERROR_BACKEND_ERROR,
            backend=backend_name,
            started_at_ms=started_at_ms,
            finished_at_ms=finished_at_ms,
        )

    finished_at_ms = int(time.time() * 1000)

    # -- adapter returned an error -------------------------------------------
    if result.error is not None:
        error_status = (
            TTS_STATUS_UNAVAILABLE
            if result.error_class in _UNAVAILABLE_ERROR_CLASSES
            else TTS_STATUS_FAILED
        )
        return build_tts_response(
            request_id=request.request_id,
            status=error_status,
            error=result.error,
            error_class=result.error_class,
            backend=backend_name,
            started_at_ms=started_at_ms,
            finished_at_ms=finished_at_ms,
        )

    # -- verify audio artifact -----------------------------------------------
    # Strip whitespace from audio_path (matches build_tts_response behavior).
    # Backends returning "  /tmp/out.wav  " will see "/tmp/out.wav" in the
    # response — the response may differ from the raw adapter result.
    cleaned_audio_path: str | None = None
    if result.audio_path is not None:
        cleaned_audio_path = str(result.audio_path).strip() or None

    if not cleaned_audio_path:
        return build_tts_response(
            request_id=request.request_id,
            status=TTS_STATUS_FAILED,
            error="Synthesis produced no audio artifact",
            error_class=TTS_ERROR_BACKEND_ERROR,
            backend=backend_name,
            started_at_ms=started_at_ms,
            finished_at_ms=finished_at_ms,
        )

    # -- success -------------------------------------------------------------
    return build_tts_response(
        request_id=request.request_id,
        status=TTS_STATUS_SUCCEEDED,
        audio_path=cleaned_audio_path,
        audio_duration_ms=result.audio_duration_ms,
        backend=backend_name,
        started_at_ms=started_at_ms,
        finished_at_ms=finished_at_ms,
        inference_ms=result.inference_ms,
    )


# -- async entry point ------------------------------------------------------


async def synthesize_tts_request_async(
    request: TTSRequest,
    adapter: TTSBackend | None = None,
) -> TTSResponse:
    """Async wrapper around ``synthesize_tts_request``.

    Runs the synchronous synthesis path in a thread via
    ``asyncio.to_thread()`` so it does not block the event loop.
    Preserves all fail-soft semantics of the sync entry point.
    """
    return await asyncio.to_thread(synthesize_tts_request, request, adapter)
