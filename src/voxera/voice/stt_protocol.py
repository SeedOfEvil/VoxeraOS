"""STT (speech-to-text) request/response protocol.

Defines the canonical contract shapes for speech-to-text interactions.
This is the protocol definition layer only -- it does not perform actual
transcription.  Runtime backends will consume/produce these shapes.

Status values follow the repo-wide convention: known terminal states are
enumerated explicitly; unknown values normalize fail-closed to "unavailable".

``error_class`` is intentionally **not** validated.  Backends may define
their own error classes beyond the canonical constants exported here.
This matches the ``CanonicalSkillResult.error_class`` passthrough policy
in ``skills/result_contract.py``.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

# -- canonical status values ------------------------------------------------

STT_STATUS_SUCCEEDED = "succeeded"
STT_STATUS_FAILED = "failed"
STT_STATUS_UNAVAILABLE = "unavailable"
STT_STATUS_UNSUPPORTED = "unsupported"
_STT_VALID_STATUSES = frozenset(
    {STT_STATUS_SUCCEEDED, STT_STATUS_FAILED, STT_STATUS_UNAVAILABLE, STT_STATUS_UNSUPPORTED}
)

# -- canonical input source values ------------------------------------------

STT_SOURCE_MICROPHONE = "microphone"
STT_SOURCE_AUDIO_FILE = "audio_file"
STT_SOURCE_STREAM = "stream"
_STT_VALID_SOURCES = frozenset({STT_SOURCE_MICROPHONE, STT_SOURCE_AUDIO_FILE, STT_SOURCE_STREAM})

# -- canonical error class values -------------------------------------------

STT_ERROR_DISABLED = "disabled"
STT_ERROR_BACKEND_MISSING = "backend_missing"
STT_ERROR_BACKEND_ERROR = "backend_error"
STT_ERROR_TIMEOUT = "timeout"
STT_ERROR_UNSUPPORTED_SOURCE = "unsupported_source"
STT_ERROR_EMPTY_AUDIO = "empty_audio"

# -- schema version ---------------------------------------------------------

STT_PROTOCOL_SCHEMA_VERSION = 1


# -- request ----------------------------------------------------------------


@dataclass(frozen=True)
class STTRequest:
    """Canonical STT request shape.

    Immutable after construction.  Created via :func:`build_stt_request`.
    """

    request_id: str
    input_source: str
    language: str | None
    session_id: str | None
    created_at_ms: int
    schema_version: int


def build_stt_request(
    *,
    input_source: str,
    language: str | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
    created_at_ms: int | None = None,
) -> STTRequest:
    """Build a validated STT request.

    Normalizes *input_source* fail-closed: unknown values raise ``ValueError``.
    """
    normalized_source = str(input_source or "").strip().lower()
    if normalized_source not in _STT_VALID_SOURCES:
        raise ValueError(
            f"Invalid STT input_source: {input_source!r}. "
            f"Must be one of {sorted(_STT_VALID_SOURCES)}."
        )

    rid = str(request_id or "").strip() or str(uuid.uuid4())
    ts = int(created_at_ms) if created_at_ms is not None else int(time.time() * 1000)

    return STTRequest(
        request_id=rid,
        input_source=normalized_source,
        language=str(language).strip() if language else None,
        session_id=str(session_id).strip() if session_id else None,
        created_at_ms=ts,
        schema_version=STT_PROTOCOL_SCHEMA_VERSION,
    )


# -- response ---------------------------------------------------------------


@dataclass(frozen=True)
class STTResponse:
    """Canonical STT response shape.

    Immutable after construction.  Created via :func:`build_stt_response`
    or :func:`build_stt_unavailable_response`.
    """

    request_id: str
    status: str
    transcript: str | None
    language: str | None
    error: str | None
    error_class: str | None
    backend: str | None
    started_at_ms: int | None
    finished_at_ms: int | None
    schema_version: int


def _normalize_status(raw: str | None) -> str:
    """Normalize status fail-closed to ``"unavailable"``."""
    candidate = str(raw or "").strip().lower()
    if candidate in _STT_VALID_STATUSES:
        return candidate
    return STT_STATUS_UNAVAILABLE


def build_stt_response(
    *,
    request_id: str,
    status: str,
    transcript: str | None = None,
    language: str | None = None,
    error: str | None = None,
    error_class: str | None = None,
    backend: str | None = None,
    started_at_ms: int | None = None,
    finished_at_ms: int | None = None,
) -> STTResponse:
    """Build a validated STT response.

    Unknown *status* values normalize fail-closed to ``"unavailable"``.
    """
    normalized_status = _normalize_status(status)
    cleaned_transcript: str | None = None
    if transcript is not None:
        cleaned_transcript = " ".join(str(transcript).strip().split()) or None

    return STTResponse(
        request_id=str(request_id).strip(),
        status=normalized_status,
        transcript=cleaned_transcript,
        language=str(language).strip() if language else None,
        error=str(error).strip() if error else None,
        error_class=str(error_class).strip() if error_class else None,
        backend=str(backend).strip() if backend else None,
        started_at_ms=int(started_at_ms) if started_at_ms is not None else None,
        finished_at_ms=int(finished_at_ms) if finished_at_ms is not None else None,
        schema_version=STT_PROTOCOL_SCHEMA_VERSION,
    )


def build_stt_unavailable_response(
    *,
    request_id: str,
    reason: str,
    error_class: str,
    backend: str | None = None,
) -> STTResponse:
    """Convenience builder for an unavailable/failed STT response.

    Use this when the STT subsystem cannot service the request at all
    (disabled, unconfigured, backend missing, etc.).

    *error_class* is required — callers must state why the request is
    unavailable (e.g. ``STT_ERROR_DISABLED``, ``STT_ERROR_BACKEND_MISSING``).
    """
    return build_stt_response(
        request_id=request_id,
        status=STT_STATUS_UNAVAILABLE,
        error=reason,
        error_class=error_class,
        backend=backend,
    )


# -- serialization ----------------------------------------------------------


def stt_request_as_dict(request: STTRequest) -> dict[str, object]:
    """Serialize an STTRequest to a plain dict for JSON / logging / audit."""
    return {
        "request_id": request.request_id,
        "input_source": request.input_source,
        "language": request.language,
        "session_id": request.session_id,
        "created_at_ms": request.created_at_ms,
        "schema_version": request.schema_version,
    }


def stt_response_as_dict(response: STTResponse) -> dict[str, object]:
    """Serialize an STTResponse to a plain dict for JSON / logging / audit."""
    return {
        "request_id": response.request_id,
        "status": response.status,
        "transcript": response.transcript,
        "language": response.language,
        "error": response.error,
        "error_class": response.error_class,
        "backend": response.backend,
        "started_at_ms": response.started_at_ms,
        "finished_at_ms": response.finished_at_ms,
        "schema_version": response.schema_version,
    }
