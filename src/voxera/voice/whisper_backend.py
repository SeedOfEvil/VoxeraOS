"""Whisper local STT backend using faster-whisper.

Provides a local speech-to-text backend backed by ``faster-whisper``
(CTranslate2-based Whisper implementation).  This is the first real
``STTBackend`` implementation — it supports ``audio_file`` transcription
only.  ``microphone`` and ``stream`` are explicitly unsupported for now.

Configuration is environment-driven:

- ``VOXERA_VOICE_STT_WHISPER_MODEL`` — model size (default: ``base``)
- ``VOXERA_VOICE_STT_WHISPER_DEVICE`` — compute device (default: ``auto``)
- ``VOXERA_VOICE_STT_WHISPER_COMPUTE_TYPE`` — quantization (default: ``int8``)

The ``faster-whisper`` dependency is optional.  If not installed, the
backend reports a truthful ``backend_missing`` error — it never crashes
or pretends transcription is available.

Model loading is lazy: the Whisper model is loaded on the first
``transcribe()`` call, not at construction time.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from .stt_adapter import STTAdapterResult, STTBackendUnsupportedError
from .stt_protocol import (
    STT_ERROR_BACKEND_ERROR,
    STT_ERROR_BACKEND_MISSING,
    STT_SOURCE_AUDIO_FILE,
    STTRequest,
)

# -- optional dependency guard ------------------------------------------------

_FASTER_WHISPER_AVAILABLE: bool
try:
    import faster_whisper  # noqa: F401

    _FASTER_WHISPER_AVAILABLE = True
except ModuleNotFoundError:
    _FASTER_WHISPER_AVAILABLE = False

# -- environment defaults -----------------------------------------------------

_DEFAULT_MODEL = "base"
_DEFAULT_DEVICE = "auto"
_DEFAULT_COMPUTE_TYPE = "int8"


def _env_str(name: str, default: str) -> str:
    return str(os.environ.get(name) or "").strip() or default


# -- backend ------------------------------------------------------------------


class WhisperLocalBackend:
    """Local Whisper STT backend via ``faster-whisper``.

    Satisfies the ``STTBackend`` structural protocol.

    Supports ``audio_file`` only.  ``microphone`` and ``stream`` are
    explicitly rejected as unsupported.

    Model loading is lazy — the model is not loaded until the first
    ``transcribe()`` call.  This avoids heavy initialization at
    construction time and allows the backend to be instantiated cheaply
    even if it is never used.
    """

    def __init__(
        self,
        *,
        model_size: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
    ) -> None:
        self._model_size = model_size or _env_str("VOXERA_VOICE_STT_WHISPER_MODEL", _DEFAULT_MODEL)
        self._device = device or _env_str("VOXERA_VOICE_STT_WHISPER_DEVICE", _DEFAULT_DEVICE)
        self._compute_type = compute_type or _env_str(
            "VOXERA_VOICE_STT_WHISPER_COMPUTE_TYPE", _DEFAULT_COMPUTE_TYPE
        )
        self._model: Any = None

    # -- STTBackend protocol ---------------------------------------------------

    @property
    def backend_name(self) -> str:
        return "whisper_local"

    def supports_source(self, input_source: str) -> bool:
        return input_source == STT_SOURCE_AUDIO_FILE

    def transcribe(self, request: STTRequest) -> STTAdapterResult:
        """Transcribe an audio file using local Whisper.

        Returns an ``STTAdapterResult``.  Raises
        ``STTBackendUnsupportedError`` for non-audio_file sources.
        """
        # -- dependency check --------------------------------------------------
        if not _FASTER_WHISPER_AVAILABLE:
            return STTAdapterResult(
                transcript=None,
                error=(
                    "faster-whisper is not installed. Install with: pip install voxera-os[whisper]"
                ),
                error_class=STT_ERROR_BACKEND_MISSING,
            )

        # -- source check ------------------------------------------------------
        if request.input_source != STT_SOURCE_AUDIO_FILE:
            raise STTBackendUnsupportedError(
                f"WhisperLocalBackend supports 'audio_file' only, got {request.input_source!r}"
            )

        # -- audio_path check --------------------------------------------------
        audio_path = getattr(request, "audio_path", None)
        if not audio_path:
            return STTAdapterResult(
                transcript=None,
                error="audio_path is required for audio_file transcription",
                error_class=STT_ERROR_BACKEND_ERROR,
            )

        path = Path(audio_path)
        if not path.is_file():
            return STTAdapterResult(
                transcript=None,
                error=f"Audio file not found: {audio_path}",
                error_class=STT_ERROR_BACKEND_ERROR,
            )

        # -- lazy model load ---------------------------------------------------
        model = self._ensure_model()

        # -- transcribe --------------------------------------------------------
        inference_start_ms = int(time.time() * 1000)
        try:
            segments, info = model.transcribe(
                str(path),
                language=request.language if request.language else None,
            )
            text_parts: list[str] = []
            for segment in segments:
                text_parts.append(segment.text)
            transcript = " ".join(text_parts)
        except Exception as exc:
            return STTAdapterResult(
                transcript=None,
                error=f"Whisper transcription failed: {exc}",
                error_class=STT_ERROR_BACKEND_ERROR,
            )
        inference_end_ms = int(time.time() * 1000)

        # -- compute audio duration if available -------------------------------
        audio_duration_ms: int | None = None
        if hasattr(info, "duration") and info.duration is not None:
            audio_duration_ms = int(info.duration * 1000)

        detected_language = info.language if hasattr(info, "language") and info.language else None

        return STTAdapterResult(
            transcript=transcript or None,
            language=detected_language,
            inference_ms=inference_end_ms - inference_start_ms,
            audio_duration_ms=audio_duration_ms,
        )

    # -- internals -------------------------------------------------------------

    def _ensure_model(self) -> Any:
        """Lazy-load the Whisper model on first use."""
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=self._compute_type,
            )
        return self._model

    @property
    def model_loaded(self) -> bool:
        """Whether the model has been loaded (for testing/observability)."""
        return self._model is not None
