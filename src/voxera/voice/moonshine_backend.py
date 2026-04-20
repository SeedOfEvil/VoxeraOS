"""Moonshine local STT backend using moonshine-onnx.

Provides a local speech-to-text backend backed by ``moonshine-onnx``
(ONNX-runtime-based Moonshine implementation from Useful Sensors).
Mirrors :class:`WhisperLocalBackend` in shape so the STT seam stays
uniform: ``audio_file`` input only, lazy model load, truthful
failure paths for missing dependency / bad model / bad audio.

Configuration is environment-driven (backend-specific knobs,
intentionally separate from ``VoiceFoundationFlags``):

- ``VOXERA_VOICE_STT_MOONSHINE_MODEL`` — model id (default: ``moonshine/base``)

The ``moonshine-onnx`` dependency is optional.  If not installed, the
backend reports a truthful ``backend_missing`` error — it never
crashes or pretends transcription is available.

Model loading is lazy: the Moonshine model is loaded on the first
``transcribe()`` call, not at construction time.  This matches the
Whisper path so the backend can be instantiated cheaply even if it
is never used.
"""

from __future__ import annotations

import os
import threading
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
#
# Two public moonshine Python packages exist: ``moonshine-onnx`` (ONNX
# runtime, CPU-friendly, small) and ``useful-moonshine`` (PyTorch).  We
# prefer ``moonshine_onnx`` because it is CPU-first and carries a
# narrower dependency footprint; if only the torch variant is present
# we still detect availability so the operator gets a truthful
# "installed" signal in status surfaces.

_MOONSHINE_AVAILABLE: bool
try:
    import moonshine_onnx  # noqa: F401

    _MOONSHINE_AVAILABLE = True
except ModuleNotFoundError:
    try:
        import moonshine  # noqa: F401

        _MOONSHINE_AVAILABLE = True
    except ModuleNotFoundError:
        _MOONSHINE_AVAILABLE = False

# -- environment defaults -----------------------------------------------------

_DEFAULT_MODEL = "moonshine/base"

# -- canonical model identifiers ---------------------------------------------
#
# These are the operator-selectable model identifiers for the local
# Moonshine STT path.  Moonshine ships two public model sizes: a small
# ``moonshine/tiny`` variant (lowest latency) and ``moonshine/base``
# (higher accuracy).  Both are resolved internally by moonshine-onnx.
# The panel UI surfaces these as a bounded dropdown; the factory will
# accept any truthy string so env-only deployments can still pin a
# model outside this list.
MOONSHINE_MODEL_TINY = "moonshine/tiny"
MOONSHINE_MODEL_BASE = "moonshine/base"

STT_MOONSHINE_MODEL_CHOICES: tuple[str, ...] = (
    MOONSHINE_MODEL_TINY,
    MOONSHINE_MODEL_BASE,
)


def _env_str(name: str, default: str) -> str:
    return str(os.environ.get(name) or "").strip() or default


# -- backend ------------------------------------------------------------------


class MoonshineLocalBackend:
    """Local Moonshine STT backend via ``moonshine-onnx``.

    Satisfies the ``STTBackend`` structural protocol.

    Supports ``audio_file`` only.  ``microphone`` and ``stream`` are
    explicitly rejected as unsupported.

    Model loading is lazy — the model is not loaded until the first
    ``transcribe()`` call.  This avoids heavy initialization at
    construction time and allows the backend to be instantiated
    cheaply even if it is never used.
    """

    def __init__(
        self,
        *,
        model_name: str | None = None,
    ) -> None:
        self._model_name = model_name or _env_str(
            "VOXERA_VOICE_STT_MOONSHINE_MODEL", _DEFAULT_MODEL
        )
        self._model: Any = None
        # Guards ``_ensure_model`` so two concurrent first-turn
        # transcribe calls on a freshly-shared backend cannot each
        # kick off a duplicate model load in parallel.  Acquired only
        # on the slow path (model not yet loaded), so steady-state
        # transcription is not serialised.
        self._model_lock = threading.Lock()

    # -- STTBackend protocol ---------------------------------------------------

    @property
    def backend_name(self) -> str:
        return "moonshine_local"

    def supports_source(self, input_source: str) -> bool:
        return input_source == STT_SOURCE_AUDIO_FILE

    def transcribe(self, request: STTRequest) -> STTAdapterResult:
        """Transcribe an audio file using local Moonshine.

        Returns an ``STTAdapterResult``.  Raises
        ``STTBackendUnsupportedError`` for non-audio_file sources.
        """
        # -- dependency check --------------------------------------------------
        if not _MOONSHINE_AVAILABLE:
            return STTAdapterResult(
                transcript=None,
                error=(
                    "moonshine-onnx is not installed. "
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
            model = self._ensure_model()
        except Exception as exc:
            return STTAdapterResult(
                transcript=None,
                error=f"Moonshine model failed to load: {exc}",
                error_class=STT_ERROR_BACKEND_ERROR,
            )

        # -- transcribe --------------------------------------------------------
        inference_start_ms = int(time.time() * 1000)
        try:
            raw = model(str(path))
            transcript = _coerce_transcript(raw)
        except Exception as exc:
            return STTAdapterResult(
                transcript=None,
                error=f"Moonshine transcription failed: {exc}",
                error_class=STT_ERROR_BACKEND_ERROR,
            )
        inference_end_ms = int(time.time() * 1000)

        # Moonshine does not expose audio duration / language detection
        # on the ONNX path, so those fields remain ``None`` rather than
        # fabricated.  The canonical STT seam treats missing timing
        # fields as "unknown", so downstream surfaces stay truthful.
        return STTAdapterResult(
            transcript=transcript or None,
            language=None,
            inference_ms=inference_end_ms - inference_start_ms,
            audio_duration_ms=None,
        )

    # -- internals -------------------------------------------------------------

    def _ensure_model(self) -> Any:
        """Lazy-load the Moonshine model on first use.

        The double-checked pattern below keeps the fast path (model
        already loaded) lock-free, while still serialising the slow
        first-turn load so concurrent transcribe calls on a freshly-
        shared backend do not each pay a duplicate model load cost.

        Returns a callable ``model(audio_path) -> transcript``.  The
        ``moonshine_onnx.transcribe(audio_path, model=...)`` top-level
        function is the canonical public API; we wrap it in a small
        closure so the ``transcribe()`` call site stays uniform
        regardless of which variant of the package is installed.

        Retry-on-failure: if the load raises, the exception propagates
        and ``self._model`` stays ``None``.  The next ``_ensure_model``
        call will retry the load — deliberately, so a transient
        failure does not poison the cached backend for the lifetime
        of the process.
        """
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is None:
                self._model = _load_moonshine_model(self._model_name)
            return self._model

    @property
    def model_loaded(self) -> bool:
        """Whether the model has been loaded (for testing/observability)."""
        return self._model is not None


def _coerce_transcript(raw: Any) -> str:
    """Normalize the moonshine transcribe return value to a plain string.

    ``moonshine_onnx.transcribe`` returns either a string or a
    single-element list of strings depending on package version.
    Collapse both shapes here so the backend contract stays stable.
    Anything unexpected stringifies fail-closed rather than raising.
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (list, tuple)):
        parts: list[str] = []
        for item in raw:
            if item is None:
                continue
            parts.append(str(item))
        return " ".join(parts)
    return str(raw)


def _load_moonshine_model(model_name: str) -> Any:
    """Return a callable that maps ``audio_path -> transcript``.

    Uses ``moonshine_onnx.transcribe`` when available (CPU-friendly
    ONNX runtime build) and falls back to ``moonshine.transcribe``
    when only the PyTorch variant is installed.  The returned
    callable takes a single ``audio_path`` arg so the backend's
    transcribe() call site is uniform across variants.
    """
    try:
        import moonshine_onnx as _moonshine_mod
    except ModuleNotFoundError:
        import moonshine as _moonshine_mod

    transcribe_fn = getattr(_moonshine_mod, "transcribe", None)
    if transcribe_fn is None:
        raise RuntimeError("Installed moonshine package does not expose a top-level transcribe()")

    def _call(audio_path: str) -> Any:
        return transcribe_fn(audio_path, model=model_name)

    return _call
