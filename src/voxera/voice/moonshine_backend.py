"""Moonshine local STT backend using moonshine-voice.

Provides a local speech-to-text backend backed by the official
``moonshine-voice`` package (Useful Sensors Moonshine, ONNX-runtime-
based).  Mirrors :class:`WhisperLocalBackend` in shape so the STT
seam stays uniform: ``audio_file`` input only, lazy model load,
truthful failure paths for missing dependency / bad model / bad
audio.

Install: ``pip install voxera-os[moonshine]`` (pulls
``moonshine-voice`` from PyPI).  The dependency is optional — if
the package is not installed, the backend honestly reports
``backend_missing`` rather than pretending.

Configuration is environment-driven (backend-specific knobs,
intentionally separate from ``VoiceFoundationFlags``):

- ``VOXERA_VOICE_STT_MOONSHINE_MODEL`` — operator-selectable model
  id.  Canonical values: ``moonshine/tiny`` (lowest latency),
  ``moonshine/base`` (higher accuracy, default).

Model loading is lazy: the Moonshine transcriber is constructed
(and model assets are downloaded via the official CDN) on the
first ``transcribe()`` call, not at construction time.  This
matches the Whisper backend and lets the STT seam be instantiated
cheaply even if it is never used.

Format: ``moonshine-voice`` loads PCM WAV files directly (16-bit /
24-bit / 32-bit PCM).  Non-WAV inputs surface a truthful
``backend_error`` so operators can see what went wrong; for
broader codec support (webm, ogg, mp3, etc.) pick ``whisper_local``
which runs through faster-whisper's FFmpeg decoding path.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from .audio_normalize import ensure_pcm_wav
from .stt_adapter import STTAdapterResult, STTBackendUnsupportedError
from .stt_protocol import (
    STT_ERROR_BACKEND_ERROR,
    STT_ERROR_BACKEND_MISSING,
    STT_SOURCE_AUDIO_FILE,
    STTRequest,
)

# -- optional dependency guard ------------------------------------------------
#
# ``moonshine-voice`` is the canonical upstream PyPI package (semver,
# e.g. ``0.0.56``).  It installs as the Python module ``moonshine_voice``
# and exposes the ``Transcriber`` class plus a pure-Python WAV loader.
# Older community packages (``useful-moonshine-onnx``, ``useful-moonshine``)
# existed with date-style versioning but used a different top-level
# ``transcribe()`` API that has diverged; we do not fall back to them
# because their semver-incompatible versions also cannot be pinned
# cleanly in ``pyproject.toml``.

# ``load_wav_file`` is exposed at module scope (not function-local)
# so test monkey-patches can target
# ``voxera.voice.moonshine_backend.load_wav_file`` without requiring
# the real ``moonshine_voice`` package to be installed.  The runtime
# dep guard above already ensures we never call it when
# ``_MOONSHINE_AVAILABLE`` is False.
load_wav_file: Any
_MOONSHINE_AVAILABLE: bool
try:
    import moonshine_voice  # noqa: F401
    from moonshine_voice.transcriber import load_wav_file as _real_load_wav_file

    load_wav_file = _real_load_wav_file
    _MOONSHINE_AVAILABLE = True
except ModuleNotFoundError:
    load_wav_file = None
    _MOONSHINE_AVAILABLE = False

# -- environment defaults -----------------------------------------------------

_DEFAULT_MODEL = "moonshine/base"
_DEFAULT_LANGUAGE = "en"

# -- canonical model identifiers ---------------------------------------------
#
# These are the operator-selectable model identifiers for the local
# Moonshine STT path.  Moonshine ships two public non-streaming model
# sizes: ``tiny`` (lowest latency) and ``base`` (higher accuracy).
# The panel UI surfaces these as a bounded dropdown; the factory
# accepts any string in ``_SUPPORTED_MODELS`` so env-only deployments
# can pin either variant.
MOONSHINE_MODEL_TINY = "moonshine/tiny"
MOONSHINE_MODEL_BASE = "moonshine/base"

STT_MOONSHINE_MODEL_CHOICES: tuple[str, ...] = (
    MOONSHINE_MODEL_TINY,
    MOONSHINE_MODEL_BASE,
)


def _env_str(name: str, default: str) -> str:
    return str(os.environ.get(name) or "").strip() or default


def _resolve_model_arch(model_name: str) -> Any:
    """Map a VoxeraOS model id (``moonshine/tiny``, ``moonshine/base``)
    to a ``moonshine_voice.ModelArch`` enum.

    Called inside ``_ensure_transcriber`` on the slow path — never at
    import time — so the ``moonshine_voice`` import stays optional.
    Unknown names raise ``ValueError`` so the backend's load-failure
    branch surfaces the rejected id truthfully.
    """
    from moonshine_voice.transcriber import ModelArch

    norm = (model_name or "").strip().lower()
    if norm in (MOONSHINE_MODEL_TINY, "tiny", "tiny-en"):
        return ModelArch.TINY
    if norm in (MOONSHINE_MODEL_BASE, "base", "base-en"):
        return ModelArch.BASE
    raise ValueError(
        f"Unsupported Moonshine model {model_name!r}; expected one of {STT_MOONSHINE_MODEL_CHOICES}"
    )


# -- backend ------------------------------------------------------------------


class MoonshineLocalBackend:
    """Local Moonshine STT backend via ``moonshine-voice``.

    Satisfies the ``STTBackend`` structural protocol.

    Supports ``audio_file`` only.  ``microphone`` and ``stream`` are
    explicitly rejected as unsupported.

    Model loading is lazy — the transcriber and underlying ONNX
    models are not loaded until the first ``transcribe()`` call.
    This avoids heavy initialization at construction time and allows
    the backend to be instantiated cheaply even if it is never used.
    """

    def __init__(
        self,
        *,
        model_name: str | None = None,
    ) -> None:
        self._model_name = model_name or _env_str(
            "VOXERA_VOICE_STT_MOONSHINE_MODEL", _DEFAULT_MODEL
        )
        self._transcriber: Any = None
        # Guards ``_ensure_transcriber`` so two concurrent first-turn
        # transcribe calls on a freshly-shared backend cannot each kick
        # off a duplicate model download / ONNX-session build in
        # parallel.  Acquired only on the slow path; steady-state
        # transcription is not serialised.
        self._model_lock = threading.Lock()

    # -- STTBackend protocol ---------------------------------------------------

    @property
    def backend_name(self) -> str:
        return "moonshine_local"

    def supports_source(self, input_source: str) -> bool:
        return input_source == STT_SOURCE_AUDIO_FILE

    def transcribe(self, request: STTRequest) -> STTAdapterResult:
        """Transcribe a WAV file using local Moonshine.

        Returns an ``STTAdapterResult``.  Raises
        ``STTBackendUnsupportedError`` for non-audio_file sources.
        """
        # -- dependency check --------------------------------------------------
        if not _MOONSHINE_AVAILABLE:
            return STTAdapterResult(
                transcript=None,
                error=(
                    "moonshine-voice is not installed. "
                    "Install with: pip install voxera-os[moonshine]"
                ),
                error_class=STT_ERROR_BACKEND_MISSING,
            )

        # -- source check ------------------------------------------------------
        if request.input_source != STT_SOURCE_AUDIO_FILE:
            raise STTBackendUnsupportedError(
                f"MoonshineLocalBackend supports 'audio_file' only, got {request.input_source!r}"
            )

        # -- audio_path check --------------------------------------------------
        # Trust boundary: audio_path is currently only set by internal
        # code (build_stt_request).  If it ever comes from operator /
        # Vera input, it will need path boundary enforcement (see
        # skills/path_boundaries.py).
        if not request.audio_path:
            return STTAdapterResult(
                transcript=None,
                error="audio_path is required for audio_file transcription",
                error_class=STT_ERROR_BACKEND_ERROR,
            )

        path = Path(request.audio_path)
        if not path.is_file():
            return STTAdapterResult(
                transcript=None,
                error=f"Audio file not found: {request.audio_path}",
                error_class=STT_ERROR_BACKEND_ERROR,
            )

        # -- lazy model load ---------------------------------------------------
        try:
            transcriber = self._ensure_transcriber()
        except Exception as exc:
            return STTAdapterResult(
                transcript=None,
                error=f"Moonshine model failed to load: {exc}",
                error_class=STT_ERROR_BACKEND_ERROR,
            )

        # -- normalize to PCM WAV ---------------------------------------------
        # ``moonshine_voice.load_wav_file`` is PCM-WAV-only.  Operators
        # routinely feed us browser-captured ``audio/webm`` from the
        # Voice Workbench mic-upload lane, so we transcode non-WAV
        # inputs transparently via the audio_normalize helper (PyAV
        # under the hood, pulled by the ``[moonshine]`` extra).  The
        # helper is a no-op when the input is already PCM WAV, so
        # already-normalized files pay zero conversion cost.  A temp
        # file is cleaned up in a ``finally`` block below regardless of
        # success / failure.
        decode_path: Path
        cleanup_path: Path | None = None
        try:
            decode_path, cleanup_path = ensure_pcm_wav(path)
        except Exception as exc:
            return STTAdapterResult(
                transcript=None,
                error=(f"Moonshine could not normalize audio to PCM WAV: {exc}"),
                error_class=STT_ERROR_BACKEND_ERROR,
            )

        # -- load WAV ----------------------------------------------------------
        try:
            try:
                audio_data, sample_rate = load_wav_file(str(decode_path))
            except Exception as exc:
                return STTAdapterResult(
                    transcript=None,
                    error=(f"Moonshine could not read audio file (PCM WAV required): {exc}"),
                    error_class=STT_ERROR_BACKEND_ERROR,
                )

            # -- transcribe ----------------------------------------------------
            inference_start_ms = int(time.time() * 1000)
            try:
                transcript_obj = transcriber.transcribe_without_streaming(audio_data, sample_rate)
                transcript_text = _extract_transcript_text(transcript_obj)
            except Exception as exc:
                return STTAdapterResult(
                    transcript=None,
                    error=f"Moonshine transcription failed: {exc}",
                    error_class=STT_ERROR_BACKEND_ERROR,
                )
            inference_end_ms = int(time.time() * 1000)

            # Audio duration is derivable from the decoded sample count
            # and rate.  Moonshine's transcriber does not report
            # per-call language detection on the non-streaming path;
            # leave ``language`` as None rather than fabricate one.
            audio_duration_ms: int | None = None
            if sample_rate:
                audio_duration_ms = int(len(audio_data) / sample_rate * 1000)

            return STTAdapterResult(
                transcript=transcript_text or None,
                language=None,
                inference_ms=inference_end_ms - inference_start_ms,
                audio_duration_ms=audio_duration_ms,
            )
        finally:
            if cleanup_path is not None:
                import contextlib as _contextlib

                with _contextlib.suppress(OSError):
                    os.unlink(cleanup_path)

    # -- internals -------------------------------------------------------------

    def _ensure_transcriber(self) -> Any:
        """Lazy-load the Moonshine transcriber on first use.

        Resolves the operator-selected model name to a
        ``ModelArch`` enum, downloads the ONNX model assets via
        the official ``moonshine_voice.download`` helpers (cached
        under ``~/.cache/moonshine_voice``), and constructs a
        ``Transcriber`` ready for non-streaming file transcription.

        The double-checked pattern keeps the fast path lock-free
        while still serialising the slow first-turn download /
        construction so concurrent requests do not pay duplicate
        cost.

        Retry-on-failure: if construction raises (bad model id,
        network failure, disk full), ``self._transcriber`` stays
        ``None`` so the next ``_ensure_transcriber`` call retries —
        deliberately, so a transient failure does not poison the
        cached backend for the lifetime of the process.
        """
        if self._transcriber is not None:
            return self._transcriber
        with self._model_lock:
            if self._transcriber is None:
                self._transcriber = _build_transcriber(self._model_name)
            return self._transcriber

    @property
    def model_loaded(self) -> bool:
        """Whether the transcriber has been loaded (for testing /
        observability).  Matches the Whisper backend's property name
        so status / doctor code paths can probe both uniformly."""
        return self._transcriber is not None


def _extract_transcript_text(transcript_obj: Any) -> str:
    """Normalize a ``moonshine_voice.Transcript`` to a plain string.

    ``transcribe_without_streaming`` returns a ``Transcript`` dataclass
    holding a list of ``TranscriptLine`` entries, each with a ``text``
    field.  We join them on spaces to produce a single transcript
    string; downstream ``normalize_transcript_text`` folds the
    whitespace.  Fail-closed: anything unexpected stringifies rather
    than raising, so the canonical STT seam stays in charge of
    empty-transcript semantics.
    """
    lines = getattr(transcript_obj, "lines", None)
    if lines is None:
        return ""
    parts: list[str] = []
    for line in lines:
        text = getattr(line, "text", None)
        if text:
            parts.append(str(text))
    return " ".join(parts)


def _build_transcriber(model_name: str) -> Any:
    """Build a configured ``Transcriber`` for *model_name*.

    Isolated at module scope so tests can patch the slow path
    without stubbing out the backend's whole ``_ensure_transcriber``
    method.  Imports are deferred so module import cost stays zero
    when the operator has not enabled Moonshine.
    """
    from moonshine_voice.download import download_model_from_info, find_model_info
    from moonshine_voice.transcriber import Transcriber

    arch = _resolve_model_arch(model_name)
    info = find_model_info(language=_DEFAULT_LANGUAGE, model_arch=arch)
    model_path, resolved_arch = download_model_from_info(info)
    return Transcriber(model_path=str(model_path), model_arch=resolved_arch)
