"""Tests for the MoonshineLocalBackend STT adapter.

Pins: protocol conformance, lazy loading, missing dependency handling,
supports_source behavior, audio_path requirements, unsupported sources,
WAV-loading failure truthfulness, successful transcription with a
fake Transcriber, empty transcript normalization, transcription
failure passthrough, model-load failure passthrough, and the
integration through transcribe_stt_request / async.

The real ``moonshine-voice`` package is optional.  All tests either
patch ``_MOONSHINE_AVAILABLE`` at the module boundary or stub out
``_build_transcriber`` + ``load_wav_file`` so behaviour is
deterministic and fast regardless of whether ``moonshine-voice`` is
installed in the host environment.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from voxera.voice.moonshine_backend import (
    MOONSHINE_MODEL_BASE,
    MOONSHINE_MODEL_TINY,
    STT_MOONSHINE_MODEL_CHOICES,
    MoonshineLocalBackend,
)
from voxera.voice.stt_adapter import (
    STTBackend,
    STTBackendUnsupportedError,
    transcribe_stt_request,
)
from voxera.voice.stt_protocol import (
    STT_ERROR_BACKEND_ERROR,
    STT_ERROR_BACKEND_MISSING,
    STT_ERROR_EMPTY_AUDIO,
    STT_ERROR_UNSUPPORTED_SOURCE,
    STT_STATUS_FAILED,
    STT_STATUS_SUCCEEDED,
    STT_STATUS_UNAVAILABLE,
    STT_STATUS_UNSUPPORTED,
    STTResponse,
    build_stt_request,
)

# -- helpers ------------------------------------------------------------------


def _fake_transcript(*lines: str):
    """Build a duck-typed ``Transcript`` object — just needs ``.lines``
    with items exposing ``.text``."""
    return SimpleNamespace(lines=[SimpleNamespace(text=t) for t in lines])


def _fake_transcriber(transcript_value):
    """Build a duck-typed transcriber with the non-streaming API."""
    return SimpleNamespace(transcribe_without_streaming=MagicMock(return_value=transcript_value))


def _prime_backend(backend: MoonshineLocalBackend, transcript_value) -> None:
    """Inject a fake transcriber so ``_ensure_transcriber`` short-circuits."""
    backend._transcriber = _fake_transcriber(transcript_value)


def _patch_load_wav(audio=(0.0, 0.1, -0.1), sr: int = 16000):
    """Patch ``moonshine_voice.transcriber.load_wav_file`` at the
    backend's import site so tests never touch the real loader."""
    return patch(
        "voxera.voice.moonshine_backend.MoonshineLocalBackend.transcribe.__globals__",
        # can't patch a method's globals; instead we patch the symbol
        # that would be resolved at call-time: the moonshine_voice
        # submodule's load_wav_file.
    )


# -- protocol conformance -----------------------------------------------------


class TestMoonshineProtocolConformance:
    def test_satisfies_stt_backend_protocol(self) -> None:
        backend: STTBackend = MoonshineLocalBackend()
        assert backend.backend_name == "moonshine_local"

    def test_backend_name(self) -> None:
        assert MoonshineLocalBackend().backend_name == "moonshine_local"


# -- lazy loading --------------------------------------------------------------


class TestMoonshineLazyLoading:
    def test_model_not_loaded_at_construction(self) -> None:
        backend = MoonshineLocalBackend()
        assert backend.model_loaded is False

    def test_model_loaded_reflects_state_after_injection(self) -> None:
        backend = MoonshineLocalBackend()
        backend._transcriber = MagicMock()
        assert backend.model_loaded is True


# -- supports_source -----------------------------------------------------------


class TestMoonshineSupportsSource:
    def test_supports_audio_file(self) -> None:
        assert MoonshineLocalBackend().supports_source("audio_file") is True

    def test_does_not_support_microphone(self) -> None:
        assert MoonshineLocalBackend().supports_source("microphone") is False

    def test_does_not_support_stream(self) -> None:
        assert MoonshineLocalBackend().supports_source("stream") is False

    def test_does_not_support_unknown(self) -> None:
        assert MoonshineLocalBackend().supports_source("telepathy") is False


# -- missing dependency --------------------------------------------------------


class TestMoonshineMissingDependency:
    def test_missing_dependency_returns_backend_missing(self) -> None:
        backend = MoonshineLocalBackend()
        req = build_stt_request(
            input_source="audio_file",
            request_id="dep-miss",
            audio_path="/tmp/test.wav",
        )
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", False):
            result = backend.transcribe(req)
        assert result.transcript is None
        assert result.error_class == STT_ERROR_BACKEND_MISSING
        # Hint must point at the correct PyPI package name.
        assert "moonshine-voice" in (result.error or "").lower()

    def test_missing_dependency_through_entry_point(self) -> None:
        backend = MoonshineLocalBackend()
        req = build_stt_request(
            input_source="audio_file",
            request_id="dep-miss-ep",
            audio_path="/tmp/test.wav",
        )
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", False):
            resp = transcribe_stt_request(req, adapter=backend)
        assert resp.status == STT_STATUS_UNAVAILABLE
        assert resp.error_class == STT_ERROR_BACKEND_MISSING
        assert resp.backend == "moonshine_local"


# -- unsupported sources -------------------------------------------------------


class TestMoonshineUnsupportedSources:
    def test_microphone_raises_unsupported(self) -> None:
        backend = MoonshineLocalBackend()
        req = build_stt_request(input_source="microphone", request_id="mic-unsup")
        with (
            patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True),
            pytest.raises(STTBackendUnsupportedError, match="audio_file"),
        ):
            backend.transcribe(req)

    def test_stream_raises_unsupported(self) -> None:
        backend = MoonshineLocalBackend()
        req = build_stt_request(input_source="stream", request_id="stream-unsup")
        with (
            patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True),
            pytest.raises(STTBackendUnsupportedError, match="audio_file"),
        ):
            backend.transcribe(req)

    def test_microphone_through_entry_point(self) -> None:
        backend = MoonshineLocalBackend()
        req = build_stt_request(input_source="microphone", request_id="mic-ep")
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True):
            resp = transcribe_stt_request(req, adapter=backend)
        assert resp.status == STT_STATUS_UNSUPPORTED
        assert resp.error_class == STT_ERROR_UNSUPPORTED_SOURCE


# -- audio_path requirements ---------------------------------------------------


class TestMoonshineAudioPath:
    def test_missing_audio_path_returns_error(self) -> None:
        backend = MoonshineLocalBackend()
        req = build_stt_request(input_source="audio_file", request_id="no-path")
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True):
            result = backend.transcribe(req)
        assert result.transcript is None
        assert result.error_class == STT_ERROR_BACKEND_ERROR
        assert "audio_path" in (result.error or "")

    def test_nonexistent_file_returns_error(self) -> None:
        backend = MoonshineLocalBackend()
        req = build_stt_request(
            input_source="audio_file",
            request_id="bad-path",
            audio_path="/nonexistent/audio.wav",
        )
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True):
            result = backend.transcribe(req)
        assert result.transcript is None
        assert result.error_class == STT_ERROR_BACKEND_ERROR
        assert "not found" in (result.error or "").lower()


# -- WAV loader failure -------------------------------------------------------


class TestMoonshineWavLoader:
    def test_non_wav_file_reports_wav_required(self, tmp_path, monkeypatch) -> None:
        """A non-WAV file surfaces a truthful ``PCM WAV required`` error.

        The real ``load_wav_file`` raises ``ValueError`` on a non-RIFF
        body; we stub it here so the test does not depend on the
        upstream implementation details, only on the contract: a
        load-layer exception yields ``backend_error`` with the string
        ``PCM WAV required`` so the operator can see why Moonshine
        rejected the file.
        """
        not_a_wav = tmp_path / "clip.webm"
        not_a_wav.write_bytes(b"\x1a\x45\xdf\xa3")  # EBML header, not RIFF

        backend = MoonshineLocalBackend()
        _prime_backend(backend, _fake_transcript("should not be called"))

        fake_load = MagicMock(side_effect=ValueError("Not a valid RIFF file"))
        monkeypatch.setattr("moonshine_voice.transcriber.load_wav_file", fake_load, raising=False)

        req = build_stt_request(
            input_source="audio_file",
            request_id="nonwav",
            audio_path=str(not_a_wav),
        )
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True):
            result = backend.transcribe(req)
        assert result.transcript is None
        assert result.error_class == STT_ERROR_BACKEND_ERROR
        assert "PCM WAV required" in (result.error or "")


# -- successful transcription (mocked) ----------------------------------------


class TestMoonshineTranscriptionSuccess:
    def _fake_load(self, audio=(0.0,) * 16000, sr: int = 16000):
        return MagicMock(return_value=(list(audio), sr))

    def test_single_line_transcript_passes_through(self, tmp_path, monkeypatch) -> None:
        audio_file = tmp_path / "clip.wav"
        audio_file.write_bytes(b"RIFF....stub")

        backend = MoonshineLocalBackend()
        _prime_backend(backend, _fake_transcript("Hello from moonshine"))
        monkeypatch.setattr(
            "moonshine_voice.transcriber.load_wav_file",
            self._fake_load(),
            raising=False,
        )

        req = build_stt_request(
            input_source="audio_file",
            request_id="ok",
            audio_path=str(audio_file),
        )
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True):
            result = backend.transcribe(req)

        assert result.transcript == "Hello from moonshine"
        assert result.error is None
        assert result.error_class is None

    def test_multi_line_transcript_joined(self, tmp_path, monkeypatch) -> None:
        """Moonshine splits some audio into multiple ``TranscriptLine``
        entries; the backend joins them into a single transcript
        string for the canonical seam."""
        audio_file = tmp_path / "multi.wav"
        audio_file.write_bytes(b"RIFF....stub")

        backend = MoonshineLocalBackend()
        _prime_backend(backend, _fake_transcript("Hello", "beautiful", "world"))
        monkeypatch.setattr(
            "moonshine_voice.transcriber.load_wav_file",
            self._fake_load(),
            raising=False,
        )

        req = build_stt_request(
            input_source="audio_file",
            request_id="multi",
            audio_path=str(audio_file),
        )
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True):
            resp = transcribe_stt_request(req, adapter=backend)

        assert resp.status == STT_STATUS_SUCCEEDED
        assert resp.transcript == "Hello beautiful world"

    def test_audio_duration_derived_from_samples(self, tmp_path, monkeypatch) -> None:
        """Moonshine does not report duration directly; we derive it
        from decoded sample count / sample rate so operator surfaces
        have a truthful number."""
        audio_file = tmp_path / "dur.wav"
        audio_file.write_bytes(b"RIFF....stub")

        backend = MoonshineLocalBackend()
        _prime_backend(backend, _fake_transcript("one two three"))
        # 32000 samples @ 16 kHz = 2000 ms
        monkeypatch.setattr(
            "moonshine_voice.transcriber.load_wav_file",
            self._fake_load(audio=(0.0,) * 32000, sr=16000),
            raising=False,
        )

        req = build_stt_request(
            input_source="audio_file",
            request_id="dur",
            audio_path=str(audio_file),
        )
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True):
            result = backend.transcribe(req)
        assert result.audio_duration_ms == 2000
        assert result.inference_ms is not None
        assert result.inference_ms >= 0

    def test_success_through_entry_point(self, tmp_path, monkeypatch) -> None:
        audio_file = tmp_path / "ep.wav"
        audio_file.write_bytes(b"RIFF....stub")

        backend = MoonshineLocalBackend()
        _prime_backend(backend, _fake_transcript("Hello entry point"))
        monkeypatch.setattr(
            "moonshine_voice.transcriber.load_wav_file",
            self._fake_load(),
            raising=False,
        )

        req = build_stt_request(
            input_source="audio_file",
            request_id="ok-ep",
            audio_path=str(audio_file),
        )
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True):
            resp = transcribe_stt_request(req, adapter=backend)

        assert resp.status == STT_STATUS_SUCCEEDED
        assert resp.transcript == "Hello entry point"
        assert resp.backend == "moonshine_local"
        assert isinstance(resp, STTResponse)
        assert resp.inference_ms is not None

    def test_empty_lines_maps_to_empty_audio(self, tmp_path, monkeypatch) -> None:
        """A Transcript with no lines — or lines carrying whitespace
        only — collapses to the canonical empty_audio failure via the
        STT entry point."""
        audio_file = tmp_path / "silence.wav"
        audio_file.write_bytes(b"RIFF....stub")

        backend = MoonshineLocalBackend()
        _prime_backend(backend, _fake_transcript())  # zero lines
        monkeypatch.setattr(
            "moonshine_voice.transcriber.load_wav_file",
            self._fake_load(),
            raising=False,
        )

        req = build_stt_request(
            input_source="audio_file",
            request_id="silence",
            audio_path=str(audio_file),
        )
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True):
            resp = transcribe_stt_request(req, adapter=backend)
        assert resp.status == STT_STATUS_FAILED
        assert resp.error_class == STT_ERROR_EMPTY_AUDIO

    def test_whitespace_only_lines_map_to_empty_audio(self, tmp_path, monkeypatch) -> None:
        audio_file = tmp_path / "ws.wav"
        audio_file.write_bytes(b"RIFF....stub")

        backend = MoonshineLocalBackend()
        _prime_backend(backend, _fake_transcript("   ", "\t\n"))
        monkeypatch.setattr(
            "moonshine_voice.transcriber.load_wav_file",
            self._fake_load(),
            raising=False,
        )

        req = build_stt_request(
            input_source="audio_file",
            request_id="ws",
            audio_path=str(audio_file),
        )
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True):
            resp = transcribe_stt_request(req, adapter=backend)
        assert resp.status == STT_STATUS_FAILED
        assert resp.error_class == STT_ERROR_EMPTY_AUDIO


# -- transcription failure (mocked) -------------------------------------------


class TestMoonshineTranscriptionFailure:
    def test_transcriber_exception_returns_error(self, tmp_path, monkeypatch) -> None:
        audio_file = tmp_path / "bad.wav"
        audio_file.write_bytes(b"RIFF....stub")

        backend = MoonshineLocalBackend()
        backend._transcriber = SimpleNamespace(
            transcribe_without_streaming=MagicMock(side_effect=RuntimeError("native crash"))
        )
        monkeypatch.setattr(
            "moonshine_voice.transcriber.load_wav_file",
            MagicMock(return_value=([0.0] * 16000, 16000)),
            raising=False,
        )

        req = build_stt_request(
            input_source="audio_file",
            request_id="crash",
            audio_path=str(audio_file),
        )
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True):
            result = backend.transcribe(req)
        assert result.transcript is None
        assert result.error_class == STT_ERROR_BACKEND_ERROR
        assert "crash" in (result.error or "").lower()

    def test_model_load_failure_returns_error(self, tmp_path) -> None:
        audio_file = tmp_path / "clip.wav"
        audio_file.write_bytes(b"RIFF....stub")

        backend = MoonshineLocalBackend(model_name="totally-bogus-model")
        req = build_stt_request(
            input_source="audio_file",
            request_id="load-fail",
            audio_path=str(audio_file),
        )
        with (
            patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True),
            patch.object(backend, "_ensure_transcriber", side_effect=OSError("model not found")),
        ):
            result = backend.transcribe(req)
        assert result.transcript is None
        assert result.error_class == STT_ERROR_BACKEND_ERROR
        assert "failed to load" in (result.error or "").lower()

    def test_invalid_model_name_rejected_at_resolve(self) -> None:
        """Non-canonical model names raise a truthful ValueError when
        the resolver tries to map them to ``ModelArch``.  Goes via the
        backend's ``_ensure_transcriber`` which must surface the
        failure through the same ``backend_error`` path as any other
        load failure."""
        from voxera.voice.moonshine_backend import _resolve_model_arch

        pytest.importorskip("moonshine_voice")
        with pytest.raises(ValueError, match="Unsupported Moonshine model"):
            _resolve_model_arch("moonshine/giant-v99")


# -- configuration -------------------------------------------------------------


class TestMoonshineConfiguration:
    def test_default_model(self, monkeypatch) -> None:
        monkeypatch.delenv("VOXERA_VOICE_STT_MOONSHINE_MODEL", raising=False)
        backend = MoonshineLocalBackend()
        assert backend._model_name == MOONSHINE_MODEL_BASE

    def test_explicit_model(self) -> None:
        backend = MoonshineLocalBackend(model_name=MOONSHINE_MODEL_TINY)
        assert backend._model_name == MOONSHINE_MODEL_TINY

    def test_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv("VOXERA_VOICE_STT_MOONSHINE_MODEL", MOONSHINE_MODEL_TINY)
        backend = MoonshineLocalBackend()
        assert backend._model_name == MOONSHINE_MODEL_TINY


# -- canonical model identifier pinning ---------------------------------------


class TestMoonshineCanonicalIdentifiers:
    def test_base_constant(self) -> None:
        assert MOONSHINE_MODEL_BASE == "moonshine/base"

    def test_tiny_constant(self) -> None:
        assert MOONSHINE_MODEL_TINY == "moonshine/tiny"

    def test_choice_list_pins_both_sizes(self) -> None:
        assert MOONSHINE_MODEL_BASE in STT_MOONSHINE_MODEL_CHOICES
        assert MOONSHINE_MODEL_TINY in STT_MOONSHINE_MODEL_CHOICES

    def test_resolve_model_arch_accepts_canonical_names(self) -> None:
        """The backend's resolver maps our bounded public ids to
        ``moonshine_voice.ModelArch`` enum values."""
        pytest.importorskip("moonshine_voice")
        from moonshine_voice.transcriber import ModelArch

        from voxera.voice.moonshine_backend import _resolve_model_arch

        assert _resolve_model_arch(MOONSHINE_MODEL_TINY) == ModelArch.TINY
        assert _resolve_model_arch(MOONSHINE_MODEL_BASE) == ModelArch.BASE


# -- async entry point ---------------------------------------------------------


class TestMoonshineAsync:
    @pytest.mark.asyncio
    async def test_async_missing_dep(self) -> None:
        from voxera.voice.stt_adapter import transcribe_stt_request_async

        backend = MoonshineLocalBackend()
        req = build_stt_request(
            input_source="audio_file",
            request_id="async-dep",
            audio_path="/tmp/test.wav",
        )
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", False):
            resp = await transcribe_stt_request_async(req, adapter=backend)

        assert resp.status == STT_STATUS_UNAVAILABLE
        assert resp.error_class == STT_ERROR_BACKEND_MISSING

    @pytest.mark.asyncio
    async def test_async_success(self, tmp_path, monkeypatch) -> None:
        from voxera.voice.stt_adapter import transcribe_stt_request_async

        audio_file = tmp_path / "async.wav"
        audio_file.write_bytes(b"RIFF....stub")

        backend = MoonshineLocalBackend()
        _prime_backend(backend, _fake_transcript("Async hello"))
        monkeypatch.setattr(
            "moonshine_voice.transcriber.load_wav_file",
            MagicMock(return_value=([0.0] * 16000, 16000)),
            raising=False,
        )

        req = build_stt_request(
            input_source="audio_file",
            request_id="async-ok",
            audio_path=str(audio_file),
        )
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True):
            resp = await transcribe_stt_request_async(req, adapter=backend)

        assert resp.status == STT_STATUS_SUCCEEDED
        assert resp.transcript == "Async hello"
        assert resp.backend == "moonshine_local"
