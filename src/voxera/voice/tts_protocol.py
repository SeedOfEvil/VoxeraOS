"""TTS (text-to-speech) request/response protocol.

Defines the canonical contract shapes for text-to-speech interactions.
This is the protocol definition layer only -- it does not perform actual
synthesis.  Runtime backends will consume/produce these shapes.

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

TTS_STATUS_SUCCEEDED = "succeeded"
TTS_STATUS_FAILED = "failed"
TTS_STATUS_UNAVAILABLE = "unavailable"
TTS_STATUS_UNSUPPORTED = "unsupported"
_TTS_VALID_STATUSES = frozenset(
    {TTS_STATUS_SUCCEEDED, TTS_STATUS_FAILED, TTS_STATUS_UNAVAILABLE, TTS_STATUS_UNSUPPORTED}
)

# -- canonical output format values -----------------------------------------

TTS_FORMAT_WAV = "wav"
TTS_FORMAT_MP3 = "mp3"
TTS_FORMAT_OGG = "ogg"
TTS_FORMAT_RAW = "raw"
_TTS_VALID_FORMATS = frozenset({TTS_FORMAT_WAV, TTS_FORMAT_MP3, TTS_FORMAT_OGG, TTS_FORMAT_RAW})

# -- canonical error class values -------------------------------------------

TTS_ERROR_DISABLED = "disabled"
TTS_ERROR_BACKEND_MISSING = "backend_missing"
TTS_ERROR_BACKEND_ERROR = "backend_error"
TTS_ERROR_TIMEOUT = "timeout"
TTS_ERROR_UNSUPPORTED_FORMAT = "unsupported_format"
TTS_ERROR_EMPTY_TEXT = "empty_text"

# -- schema version ---------------------------------------------------------

TTS_PROTOCOL_SCHEMA_VERSION = 1


# -- request ----------------------------------------------------------------


@dataclass(frozen=True)
class TTSRequest:
    """Canonical TTS request shape.

    Immutable after construction.  Created via :func:`build_tts_request`.
    """

    request_id: str
    text: str
    voice_id: str | None
    language: str | None
    speed: float
    output_format: str
    session_id: str | None
    created_at_ms: int
    schema_version: int


def build_tts_request(
    *,
    text: str,
    voice_id: str | None = None,
    language: str | None = None,
    speed: float = 1.0,
    output_format: str = "wav",
    session_id: str | None = None,
    request_id: str | None = None,
    created_at_ms: int | None = None,
) -> TTSRequest:
    """Build a validated TTS request.

    *text* is required and must be non-empty after stripping.

    *output_format* normalizes fail-closed: unknown values raise ``ValueError``.

    *speed* is silently clamped to the [0.1, 10.0] range — out-of-range values
    are corrected without raising.  Callers that need to detect clamping should
    compare the returned ``speed`` against their input.
    """
    cleaned_text = str(text or "").strip()
    if not cleaned_text:
        raise ValueError("TTS text must be non-empty after stripping.")

    normalized_format = str(output_format or "").strip().lower()
    if normalized_format not in _TTS_VALID_FORMATS:
        raise ValueError(
            f"Invalid TTS output_format: {output_format!r}. "
            f"Must be one of {sorted(_TTS_VALID_FORMATS)}."
        )

    clamped_speed = max(0.1, min(10.0, float(speed)))

    rid = str(request_id or "").strip() or str(uuid.uuid4())
    ts = int(created_at_ms) if created_at_ms is not None else int(time.time() * 1000)

    return TTSRequest(
        request_id=rid,
        text=cleaned_text,
        voice_id=str(voice_id).strip() if voice_id else None,
        language=str(language).strip() if language else None,
        speed=clamped_speed,
        output_format=normalized_format,
        session_id=str(session_id).strip() if session_id else None,
        created_at_ms=ts,
        schema_version=TTS_PROTOCOL_SCHEMA_VERSION,
    )


# -- response ---------------------------------------------------------------


@dataclass(frozen=True)
class TTSResponse:
    """Canonical TTS response shape.

    Immutable after construction.  Created via :func:`build_tts_response`
    or :func:`build_tts_unavailable_response`.

    The key distinction from STTResponse: the output artifact is a file path
    (``audio_path``), not a transcript string.

    Optional adapter-reported timing fields (observability only):
    - ``inference_ms``: wall-clock time the backend spent in synthesis.
    - ``audio_duration_ms``: duration of the output audio, if known.
    """

    request_id: str
    status: str
    audio_path: str | None
    audio_duration_ms: int | None
    error: str | None
    error_class: str | None
    backend: str | None
    started_at_ms: int | None
    finished_at_ms: int | None
    schema_version: int
    inference_ms: int | None = None


def _normalize_status(raw: str | None) -> str:
    """Normalize status fail-closed to ``"unavailable"``."""
    candidate = str(raw or "").strip().lower()
    if candidate in _TTS_VALID_STATUSES:
        return candidate
    return TTS_STATUS_UNAVAILABLE


def build_tts_response(
    *,
    request_id: str,
    status: str,
    audio_path: str | None = None,
    audio_duration_ms: int | None = None,
    error: str | None = None,
    error_class: str | None = None,
    backend: str | None = None,
    started_at_ms: int | None = None,
    finished_at_ms: int | None = None,
    inference_ms: int | None = None,
) -> TTSResponse:
    """Build a validated TTS response.

    Unknown *status* values normalize fail-closed to ``"unavailable"``.
    """
    normalized_status = _normalize_status(status)
    cleaned_audio_path: str | None = None
    if audio_path is not None:
        cleaned_audio_path = str(audio_path).strip() or None

    return TTSResponse(
        request_id=str(request_id).strip(),
        status=normalized_status,
        audio_path=cleaned_audio_path,
        audio_duration_ms=int(audio_duration_ms) if audio_duration_ms is not None else None,
        error=str(error).strip() if error else None,
        error_class=str(error_class).strip() if error_class else None,
        backend=str(backend).strip() if backend else None,
        started_at_ms=int(started_at_ms) if started_at_ms is not None else None,
        finished_at_ms=int(finished_at_ms) if finished_at_ms is not None else None,
        schema_version=TTS_PROTOCOL_SCHEMA_VERSION,
        inference_ms=int(inference_ms) if inference_ms is not None else None,
    )


def build_tts_unavailable_response(
    *,
    request_id: str,
    reason: str,
    error_class: str,
    backend: str | None = None,
) -> TTSResponse:
    """Convenience builder for an unavailable/failed TTS response.

    Use this when the TTS subsystem cannot service the request at all
    (disabled, unconfigured, backend missing, etc.).

    *error_class* is required -- callers must state why the request is
    unavailable (e.g. ``TTS_ERROR_DISABLED``, ``TTS_ERROR_BACKEND_MISSING``).
    """
    return build_tts_response(
        request_id=request_id,
        status=TTS_STATUS_UNAVAILABLE,
        error=reason,
        error_class=error_class,
        backend=backend,
    )


# -- serialization ----------------------------------------------------------


def tts_request_as_dict(request: TTSRequest) -> dict[str, object]:
    """Serialize a TTSRequest to a plain dict for JSON / logging / audit."""
    return {
        "request_id": request.request_id,
        "text": request.text,
        "voice_id": request.voice_id,
        "language": request.language,
        "speed": request.speed,
        "output_format": request.output_format,
        "session_id": request.session_id,
        "created_at_ms": request.created_at_ms,
        "schema_version": request.schema_version,
    }


def tts_response_as_dict(response: TTSResponse) -> dict[str, object]:
    """Serialize a TTSResponse to a plain dict for JSON / logging / audit."""
    return {
        "request_id": response.request_id,
        "status": response.status,
        "audio_path": response.audio_path,
        "audio_duration_ms": response.audio_duration_ms,
        "error": response.error,
        "error_class": response.error_class,
        "backend": response.backend,
        "started_at_ms": response.started_at_ms,
        "finished_at_ms": response.finished_at_ms,
        "schema_version": response.schema_version,
        "inference_ms": response.inference_ms,
    }
