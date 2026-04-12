"""STT backend adapter boundary.

Defines the runtime adapter interface for speech-to-text backends and
the fail-soft transcription entry point that consumes ``STTRequest``
and returns ``STTResponse``.

The adapter boundary makes these states explicit and first-class:
- backend unavailable (no adapter configured)
- backend unsupported (adapter rejects the input source)
- backend failure (adapter raises during transcription)
- backend success (adapter returns a transcript)

The ``NullSTTBackend`` is the default when no real backend is configured.
It always returns an honest "unavailable" response — it never pretends
transcription occurred.

The ``transcribe_stt_request`` entry point is the canonical fail-soft
path: it handles missing adapters, adapter exceptions, and empty
transcripts without crashing, and always returns a truthful
``STTResponse``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from .input import normalize_transcript_text
from .stt_protocol import (
    STT_ERROR_BACKEND_ERROR,
    STT_ERROR_BACKEND_MISSING,
    STT_ERROR_EMPTY_AUDIO,
    STT_ERROR_UNSUPPORTED_SOURCE,
    STT_STATUS_FAILED,
    STT_STATUS_SUCCEEDED,
    STT_STATUS_UNSUPPORTED,
    STTRequest,
    STTResponse,
    build_stt_response,
    build_stt_unavailable_response,
)

# -- adapter result ---------------------------------------------------------


@dataclass(frozen=True)
class STTAdapterResult:
    """Raw result returned by an STT backend adapter.

    This is the adapter-internal shape — callers never see it directly.
    The ``transcribe_stt_request`` entry point wraps it into an
    ``STTResponse``.
    """

    transcript: str | None
    language: str | None = None
    error: str | None = None
    error_class: str | None = None


# -- adapter protocol -------------------------------------------------------


class STTBackend(Protocol):
    """Structural interface for an STT backend adapter.

    Mirrors the ``Brain`` protocol pattern in ``brain/base.py``.
    Implementations do not need to inherit — they only need to satisfy
    the structural signature.
    """

    @property
    def backend_name(self) -> str:
        """Stable identifier for this backend (used in responses/logs)."""
        ...

    def transcribe(self, request: STTRequest) -> STTAdapterResult:
        """Attempt to transcribe the given request.

        Returns an ``STTAdapterResult`` on success or partial failure.
        Raises ``STTBackendUnsupportedError`` if the input source is not
        supported by this backend.
        May raise any other exception on unexpected failure — the
        ``transcribe_stt_request`` entry point catches these fail-soft.
        """
        ...


# -- adapter exceptions -----------------------------------------------------


class STTBackendUnsupportedError(Exception):
    """Raised by an adapter when it does not support the requested input source."""


# -- null adapter (default when unconfigured) --------------------------------


class NullSTTBackend:
    """Truthful no-op backend: always reports unavailable.

    Used when no real STT backend is configured.  Never pretends
    transcription occurred.
    """

    @property
    def backend_name(self) -> str:
        return "null"

    def transcribe(self, request: STTRequest) -> STTAdapterResult:
        return STTAdapterResult(
            transcript=None,
            error="No STT backend is configured",
            error_class=STT_ERROR_BACKEND_MISSING,
        )


# -- fail-soft transcription entry point -------------------------------------


def transcribe_stt_request(
    request: STTRequest,
    adapter: STTBackend | None = None,
) -> STTResponse:
    """Fail-soft transcription entry point.

    Takes an ``STTRequest`` and an optional adapter, and always returns
    a truthful ``STTResponse`` — never raises.

    Behavior:
    - If *adapter* is ``None``: returns unavailable (backend_missing).
    - If the adapter raises ``STTBackendUnsupportedError``: returns
      unsupported with the adapter's message.
    - If the adapter raises any other exception: returns failed with
      a backend_error error class.
    - If the adapter returns an empty/whitespace-only transcript:
      returns failed with empty_audio error class.
    - If the adapter returns a result with an error: returns failed
      with the adapter's error details.
    - Otherwise: returns succeeded with the normalized transcript.
    """
    started_at_ms = int(time.time() * 1000)

    # -- no adapter configured -----------------------------------------------
    if adapter is None:
        return build_stt_unavailable_response(
            request_id=request.request_id,
            reason="No STT backend adapter provided",
            error_class=STT_ERROR_BACKEND_MISSING,
        )

    backend_name = adapter.backend_name

    # -- call adapter (fail-soft) --------------------------------------------
    try:
        result = adapter.transcribe(request)
    except STTBackendUnsupportedError as exc:
        finished_at_ms = int(time.time() * 1000)
        return build_stt_response(
            request_id=request.request_id,
            status=STT_STATUS_UNSUPPORTED,
            error=str(exc) or f"Input source {request.input_source!r} not supported",
            error_class=STT_ERROR_UNSUPPORTED_SOURCE,
            backend=backend_name,
            started_at_ms=started_at_ms,
            finished_at_ms=finished_at_ms,
        )
    except Exception as exc:
        finished_at_ms = int(time.time() * 1000)
        return build_stt_response(
            request_id=request.request_id,
            status=STT_STATUS_FAILED,
            error=f"STT backend error: {exc}",
            error_class=STT_ERROR_BACKEND_ERROR,
            backend=backend_name,
            started_at_ms=started_at_ms,
            finished_at_ms=finished_at_ms,
        )

    finished_at_ms = int(time.time() * 1000)

    # -- adapter returned an error -------------------------------------------
    if result.error is not None:
        return build_stt_response(
            request_id=request.request_id,
            status=STT_STATUS_FAILED,
            error=result.error,
            error_class=result.error_class,
            backend=backend_name,
            started_at_ms=started_at_ms,
            finished_at_ms=finished_at_ms,
        )

    # -- normalize transcript ------------------------------------------------
    normalized: str | None = None
    if result.transcript is not None:
        normalized = normalize_transcript_text(result.transcript)

    if not normalized:
        return build_stt_response(
            request_id=request.request_id,
            status=STT_STATUS_FAILED,
            error="Transcript is empty after normalization",
            error_class=STT_ERROR_EMPTY_AUDIO,
            backend=backend_name,
            started_at_ms=started_at_ms,
            finished_at_ms=finished_at_ms,
        )

    # -- success -------------------------------------------------------------
    return build_stt_response(
        request_id=request.request_id,
        status=STT_STATUS_SUCCEEDED,
        transcript=normalized,
        language=result.language,
        backend=backend_name,
        started_at_ms=started_at_ms,
        finished_at_ms=finished_at_ms,
    )
