"""Piper local TTS backend.

Provides a local text-to-speech backend backed by ``piper-tts``
(ONNX-based Piper speech synthesis).  This is the first real
``TTSBackend`` implementation — it synthesizes text to a WAV file
on disk.

Configuration is environment-driven (backend-specific knobs, intentionally
separate from ``VoiceFoundationFlags``; mirrors the Whisper backend pattern):

- ``VOXERA_VOICE_TTS_PIPER_MODEL`` — model name or path (default: ``en_US-lessac-medium``)
- ``VOXERA_VOICE_TTS_PIPER_SPEAKER`` — speaker id for multi-speaker models (default: ``None``)

The ``piper-tts`` dependency is optional.  If not installed, the backend
reports a truthful ``backend_missing`` error — it never crashes or
pretends synthesis is available.

Model loading is lazy: the Piper voice is loaded on the first
``synthesize()`` call, not at construction time.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import threading
import time
import wave
from typing import Any

from .tts_adapter import TTSAdapterResult, TTSBackendUnsupportedError
from .tts_protocol import (
    TTS_ERROR_BACKEND_ERROR,
    TTS_ERROR_BACKEND_MISSING,
    TTS_FORMAT_WAV,
    TTSRequest,
)

# -- optional dependency guard ------------------------------------------------

_PIPER_AVAILABLE: bool
try:
    import piper  # noqa: F401

    _PIPER_AVAILABLE = True
except ModuleNotFoundError:
    _PIPER_AVAILABLE = False

# -- environment defaults -----------------------------------------------------

_DEFAULT_MODEL = "en_US-lessac-medium"


def _env_str(name: str, default: str) -> str:
    return str(os.environ.get(name) or "").strip() or default


def _env_str_optional(name: str) -> str | None:
    val = str(os.environ.get(name) or "").strip()
    return val if val else None


# -- backend ------------------------------------------------------------------


class PiperLocalBackend:
    """Local Piper TTS backend via ``piper-tts``.

    Satisfies the ``TTSBackend`` structural protocol.

    Supports WAV output only.  Other formats raise
    ``TTSBackendUnsupportedError``.

    Model loading is lazy — the voice is not loaded until the first
    ``synthesize()`` call.  This avoids heavy initialization at
    construction time and allows the backend to be instantiated cheaply
    even if it is never used.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        speaker: str | None = None,
    ) -> None:
        self._model_name = model or _env_str("VOXERA_VOICE_TTS_PIPER_MODEL", _DEFAULT_MODEL)
        # Speaker: explicit arg > env var > None (use model default)
        self._speaker: str | None = (
            speaker if speaker is not None else _env_str_optional("VOXERA_VOICE_TTS_PIPER_SPEAKER")
        )
        self._voice: Any = None
        # Guards ``_ensure_voice`` so two concurrent first-turn
        # synthesize calls on a freshly-shared backend cannot each
        # kick off a duplicate Piper voice load in parallel.  Without
        # this lock the process-wide shared-instance cache would
        # still save every subsequent turn but the very first
        # concurrent pair would pay the load cost twice.  Acquired
        # only on the slow path (voice not yet loaded), so steady-
        # state synthesis is not serialised.
        self._voice_lock = threading.Lock()

    # -- TTSBackend protocol ---------------------------------------------------

    @property
    def backend_name(self) -> str:
        return "piper_local"

    def supports_voice(self, voice_id: str) -> bool:
        """Piper supports any voice_id — model selection is via config, not voice_id."""
        return True

    def synthesize(self, request: TTSRequest) -> TTSAdapterResult:
        """Synthesize text to a WAV file using local Piper.

        Returns a ``TTSAdapterResult``.  Raises
        ``TTSBackendUnsupportedError`` for non-WAV output formats.
        """
        # -- dependency check --------------------------------------------------
        if not _PIPER_AVAILABLE:
            return TTSAdapterResult(
                audio_path=None,
                error=("piper-tts is not installed. Install with: pip install voxera-os[piper]"),
                error_class=TTS_ERROR_BACKEND_MISSING,
            )

        # -- format check ------------------------------------------------------
        if request.output_format != TTS_FORMAT_WAV:
            raise TTSBackendUnsupportedError(
                f"PiperLocalBackend supports 'wav' only, got {request.output_format!r}"
            )

        # -- lazy voice load ---------------------------------------------------
        try:
            voice = self._ensure_voice()
        except Exception as exc:
            return TTSAdapterResult(
                audio_path=None,
                error=f"Piper voice failed to load: {exc}",
                error_class=TTS_ERROR_BACKEND_ERROR,
            )

        # -- synthesize --------------------------------------------------------
        inference_start_ms = int(time.time() * 1000)
        tmp_path: str | None = None
        audio_duration_ms: int | None = None
        try:
            # Build speaker_id kwarg if configured
            synth_kwargs: dict[str, Any] = {}
            if self._speaker is not None:
                try:
                    synth_kwargs["speaker_id"] = int(self._speaker)
                except ValueError:
                    synth_kwargs["speaker_id"] = self._speaker

            # synthesize_wav writes WAV directly to the provided wave.Wave_write
            # object (piper-tts 1.4.2 API — no synthesize_stream_raw)
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".wav", prefix="voxera_tts_piper_")
            os.close(tmp_fd)

            with wave.open(tmp_path, "wb") as wav_file:
                voice.synthesize_wav(request.text, wav_file, **synth_kwargs)

            # Read back WAV metadata to verify frames were written and compute duration
            n_frames: int = 0
            sample_rate: int = 0
            try:
                with wave.open(tmp_path, "rb") as wf:
                    n_frames = wf.getnframes()
                    sample_rate = wf.getframerate()
            except Exception:
                pass

            if n_frames == 0:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
                tmp_path = None
                return TTSAdapterResult(
                    audio_path=None,
                    error="Piper synthesis produced no audio data",
                    error_class=TTS_ERROR_BACKEND_ERROR,
                )

            if sample_rate > 0:
                audio_duration_ms = int((n_frames / sample_rate) * 1000)

        except Exception as exc:
            # Clean up orphaned temp file on failure
            if tmp_path is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
            return TTSAdapterResult(
                audio_path=None,
                error=f"Piper synthesis failed: {exc}",
                error_class=TTS_ERROR_BACKEND_ERROR,
            )

        inference_end_ms = int(time.time() * 1000)

        return TTSAdapterResult(
            audio_path=tmp_path,
            audio_duration_ms=audio_duration_ms,
            inference_ms=inference_end_ms - inference_start_ms,
        )

    # -- internals -------------------------------------------------------------

    def _ensure_voice(self) -> Any:
        """Lazy-load the Piper voice on first use.

        The double-checked pattern below keeps the fast path (voice
        already loaded) lock-free, while still serialising the slow
        first-turn load so concurrent synthesize calls on a freshly-
        shared backend do not each pay a duplicate
        ``PiperVoice.load(...)`` cost.
        """
        if self._voice is not None:
            return self._voice
        with self._voice_lock:
            if self._voice is None:
                from piper import PiperVoice

                self._voice = PiperVoice.load(self._model_name)
            return self._voice

    @property
    def model_loaded(self) -> bool:
        """Whether the voice has been loaded (for testing/observability)."""
        return self._voice is not None
