"""Kokoro local TTS backend.

Provides a local text-to-speech backend backed by ``kokoro-onnx`` — an
ONNX-runtime implementation of the `Kokoro
<https://huggingface.co/hexgrad/Kokoro-82M>`_ speech synthesis model.
This is the second real ``TTSBackend`` implementation, sitting
alongside :mod:`voxera.voice.piper_backend` behind the canonical TTS
seam.

Piper remains the default / recommended backend.  Kokoro is offered
as an operator-selectable alternative for deployments that prefer
its voice set.  Both backends synthesize WAV artifacts through the
same ``TTSBackend`` protocol, so the artifact-oriented rest of the
pipeline (``synthesize_text`` / ``synthesize_tts_request``) does not
change.

Configuration is environment-driven (backend-specific knobs,
intentionally separate from ``VoiceFoundationFlags``; mirrors the
Piper / Whisper backend pattern):

- ``VOXERA_VOICE_TTS_KOKORO_MODEL`` — path to ``kokoro-*.onnx``
  (required; no sane default path exists across deployments)
- ``VOXERA_VOICE_TTS_KOKORO_VOICES`` — path to ``voices-*.bin``
  (required; ships alongside the model)
- ``VOXERA_VOICE_TTS_KOKORO_VOICE`` — voice id to synthesize with
  (default: ``af_sarah``; a stable, widely-tested Kokoro voice)
- ``VOXERA_VOICE_TTS_KOKORO_LANG`` — language code (default: ``en-us``)

The ``kokoro-onnx`` dependency is **optional**.  If not installed,
the backend reports a truthful ``backend_missing`` error — it never
crashes or pretends synthesis is available.  Missing or
misconfigured model / voices paths are surfaced the same way, with
operator-facing error strings that state exactly which file is
missing.

Model loading is lazy: the Kokoro session is loaded on the first
``synthesize()`` call, not at construction time.  This mirrors Piper
so the shared-backend cache pays the load cost once per process.
CPU-first: the backend does not import or require torch / CUDA; it
runs on ``onnxruntime`` on CPU.
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

_KOKORO_AVAILABLE: bool
try:
    import kokoro_onnx  # noqa: F401

    _KOKORO_AVAILABLE = True
except ModuleNotFoundError:
    _KOKORO_AVAILABLE = False

# -- environment / defaults ---------------------------------------------------

_DEFAULT_VOICE = "af_sarah"
_DEFAULT_LANG = "en-us"


def _env_str(name: str, default: str) -> str:
    return str(os.environ.get(name) or "").strip() or default


def _env_str_optional(name: str) -> str | None:
    val = str(os.environ.get(name) or "").strip()
    return val if val else None


# -- backend ------------------------------------------------------------------


class KokoroLocalBackend:
    """Local Kokoro TTS backend via ``kokoro-onnx``.

    Satisfies the ``TTSBackend`` structural protocol.

    Supports WAV output only.  Other formats raise
    ``TTSBackendUnsupportedError`` — the canonical entry point catches
    that and returns a truthful ``unsupported`` response.

    Model loading is lazy: the Kokoro session is not loaded until the
    first ``synthesize()`` call.  This mirrors the Piper backend and
    keeps construction cheap even if the backend is never exercised
    for a given process.
    """

    def __init__(
        self,
        *,
        model_path: str | None = None,
        voices_path: str | None = None,
        voice: str | None = None,
        lang: str | None = None,
    ) -> None:
        # Model / voices paths: explicit arg > env var > None.  A
        # ``None`` here is an operator-facing misconfiguration (no sane
        # cross-deployment default) that ``synthesize()`` surfaces as
        # a truthful backend_missing error on the slow path, not a
        # crash.  The backend stays cheap to construct so the shared
        # cache and factory can hand out an instance even when the
        # operator has not finished wiring the model files.
        self._model_path: str | None = (
            model_path.strip() if isinstance(model_path, str) and model_path.strip() else None
        ) or _env_str_optional("VOXERA_VOICE_TTS_KOKORO_MODEL")
        self._voices_path: str | None = (
            voices_path.strip() if isinstance(voices_path, str) and voices_path.strip() else None
        ) or _env_str_optional("VOXERA_VOICE_TTS_KOKORO_VOICES")
        self._voice: str = (
            voice.strip() if isinstance(voice, str) and voice.strip() else None
        ) or _env_str("VOXERA_VOICE_TTS_KOKORO_VOICE", _DEFAULT_VOICE)
        self._lang: str = (
            lang.strip() if isinstance(lang, str) and lang.strip() else None
        ) or _env_str("VOXERA_VOICE_TTS_KOKORO_LANG", _DEFAULT_LANG)
        self._session: Any = None
        # Guards ``_ensure_session`` so two concurrent first-turn
        # synthesize calls on a freshly-shared backend cannot each
        # kick off a duplicate Kokoro session load in parallel.
        # Mirrors the Piper voice-load lock semantics.
        self._session_lock = threading.Lock()

    # -- TTSBackend protocol --------------------------------------------------

    @property
    def backend_name(self) -> str:
        return "kokoro_local"

    def supports_voice(self, voice_id: str) -> bool:
        """Kokoro accepts any voice id — voice selection is via config, not voice_id.

        The request-level ``voice_id`` is not a Kokoro voice name; the
        Kokoro voice is the operator-selected one passed to
        ``__init__`` / env.  Returning ``True`` here matches Piper's
        stance and keeps the request-level voice_id decoupled from the
        backend's model-specific voice pool.
        """
        return True

    def synthesize(self, request: TTSRequest) -> TTSAdapterResult:
        """Synthesize text to a WAV file using local Kokoro.

        Returns a ``TTSAdapterResult``.  Raises
        ``TTSBackendUnsupportedError`` for non-WAV output formats —
        the canonical entry point converts that into a truthful
        ``unsupported`` response.  Every other failure path returns a
        clean error result; no exceptions escape.
        """
        # -- dependency check ------------------------------------------------
        if not _KOKORO_AVAILABLE:
            return TTSAdapterResult(
                audio_path=None,
                error=("kokoro-onnx is not installed. Install with: pip install voxera-os[kokoro]"),
                error_class=TTS_ERROR_BACKEND_MISSING,
            )

        # -- format check ----------------------------------------------------
        if request.output_format != TTS_FORMAT_WAV:
            raise TTSBackendUnsupportedError(
                f"KokoroLocalBackend supports 'wav' only, got {request.output_format!r}"
            )

        # -- model / voices path check --------------------------------------
        # Kokoro has no cross-deployment default path, so a missing
        # model or voices file is surfaced here as backend_missing —
        # not as a generic backend_error.  The operator-facing error
        # string states which path is missing so doctor/status can
        # render a concrete next step.
        missing_path_error = self._missing_path_error()
        if missing_path_error is not None:
            return TTSAdapterResult(
                audio_path=None,
                error=missing_path_error,
                error_class=TTS_ERROR_BACKEND_MISSING,
            )

        # -- lazy session load ----------------------------------------------
        try:
            session = self._ensure_session()
        except Exception as exc:
            return TTSAdapterResult(
                audio_path=None,
                error=f"Kokoro session failed to load: {exc}",
                error_class=TTS_ERROR_BACKEND_ERROR,
            )

        # -- synthesize ------------------------------------------------------
        inference_start_ms = int(time.time() * 1000)
        tmp_path: str | None = None
        audio_duration_ms: int | None = None
        try:
            samples, sample_rate = session.create(
                request.text,
                voice=self._voice,
                speed=float(request.speed),
                lang=self._lang,
            )

            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".wav", prefix="voxera_tts_kokoro_")
            os.close(tmp_fd)

            # Kokoro returns float32 samples in [-1.0, 1.0].  Convert to
            # 16-bit PCM for the canonical WAV artifact — the same shape
            # the rest of the pipeline already consumes for Piper output.
            pcm_bytes = _float_samples_to_pcm16(samples)
            n_frames = len(pcm_bytes) // 2

            with wave.open(tmp_path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(int(sample_rate))
                wav_file.writeframes(pcm_bytes)

            if n_frames == 0:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
                tmp_path = None
                return TTSAdapterResult(
                    audio_path=None,
                    error="Kokoro synthesis produced no audio data",
                    error_class=TTS_ERROR_BACKEND_ERROR,
                )

            if int(sample_rate) > 0:
                audio_duration_ms = int((n_frames / float(sample_rate)) * 1000)

        except Exception as exc:
            # Clean up orphaned temp file on failure so a mid-write
            # exception does not leave artifact garbage on disk.
            if tmp_path is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
            return TTSAdapterResult(
                audio_path=None,
                error=f"Kokoro synthesis failed: {exc}",
                error_class=TTS_ERROR_BACKEND_ERROR,
            )

        inference_end_ms = int(time.time() * 1000)

        return TTSAdapterResult(
            audio_path=tmp_path,
            audio_duration_ms=audio_duration_ms,
            inference_ms=inference_end_ms - inference_start_ms,
        )

    # -- internals ------------------------------------------------------------

    def _missing_path_error(self) -> str | None:
        """Return a truthful error string when model/voices paths are bad.

        Returns ``None`` when both paths are configured and exist on
        disk.  Returns a concrete operator-facing string when something
        is missing so status / doctor surfaces show exactly which file
        the operator needs to fix, not a generic "backend error".
        """
        if not self._model_path:
            return (
                "Kokoro model path is not configured "
                "(set VOXERA_VOICE_TTS_KOKORO_MODEL to the kokoro-*.onnx path)."
            )
        if not self._voices_path:
            return (
                "Kokoro voices path is not configured "
                "(set VOXERA_VOICE_TTS_KOKORO_VOICES to the voices-*.bin path)."
            )
        if not os.path.exists(self._model_path):
            return f"Kokoro model file does not exist: {self._model_path}"
        if not os.path.exists(self._voices_path):
            return f"Kokoro voices file does not exist: {self._voices_path}"
        return None

    def _ensure_session(self) -> Any:
        """Lazy-load the Kokoro session on first use.

        Double-checked locking keeps the fast path (session already
        loaded) lock-free, while still serialising the slow first-turn
        load so concurrent synthesize calls on a freshly-shared
        backend do not each pay a duplicate Kokoro construction cost.

        Retry-on-failure: if construction raises (missing voices file,
        corrupt ONNX, OOM) the exception propagates and ``self._session``
        stays ``None``.  A subsequent call will retry — deliberately,
        so a transient failure does not poison the cached backend for
        the lifetime of the process.
        """
        if self._session is not None:
            return self._session
        with self._session_lock:
            if self._session is None:
                from kokoro_onnx import Kokoro

                # mypy: _model_path / _voices_path are guaranteed non-None
                # here because synthesize() gated on _missing_path_error()
                # before ever reaching _ensure_session().
                assert self._model_path is not None  # noqa: S101
                assert self._voices_path is not None  # noqa: S101
                self._session = Kokoro(self._model_path, self._voices_path)
            return self._session

    @property
    def session_loaded(self) -> bool:
        """Whether the Kokoro session has been loaded (for tests / observability)."""
        return self._session is not None

    @property
    def model_path(self) -> str | None:
        """Configured Kokoro model path (for tests / observability)."""
        return self._model_path

    @property
    def voices_path(self) -> str | None:
        """Configured Kokoro voices path (for tests / observability)."""
        return self._voices_path

    @property
    def voice(self) -> str:
        """Configured Kokoro voice id (for tests / observability)."""
        return self._voice

    @property
    def lang(self) -> str:
        """Configured Kokoro language code (for tests / observability)."""
        return self._lang


# -- helpers ------------------------------------------------------------------


def _float_samples_to_pcm16(samples: Any) -> bytes:
    """Convert Kokoro's float32 samples (-1..1) to 16-bit PCM bytes.

    Accepts any sequence/ndarray-like yielding floats; returns bytes
    suitable for ``wave.Wave_write.writeframes``.  Values outside
    [-1.0, 1.0] are clipped so an out-of-range sample can never
    produce wrapped PCM noise in the artifact.

    Implementation note: Kokoro-onnx returns a numpy ``ndarray`` in
    practice, but we do not require numpy as a hard dependency — a
    pure-Python fallback path handles the rare case where the caller
    passes a plain list.  The fast path uses numpy when it is already
    importable (kokoro-onnx transitively depends on it, so in every
    real deployment this path is taken).
    """
    try:
        import numpy as np

        arr = np.asarray(samples, dtype=np.float32)
        clipped = np.clip(arr, -1.0, 1.0)
        pcm = (clipped * 32767.0).astype(np.int16)
        return pcm.tobytes()
    except ModuleNotFoundError:
        # Pure-Python fallback — only reached if numpy is missing
        # entirely, which in practice means kokoro-onnx is too.
        import struct

        out = bytearray()
        for s in samples:
            v = float(s)
            if v > 1.0:
                v = 1.0
            elif v < -1.0:
                v = -1.0
            out.extend(struct.pack("<h", int(v * 32767.0)))
        return bytes(out)
