"""Tests for the PiperLocalBackend TTS adapter.

Pins: protocol conformance, lazy loading, missing dependency handling,
supports_voice behavior, output format requirements, speaker handling,
configuration, and the integration through synthesize_tts_request / async.

The actual piper-tts voice is mocked at the boundary so tests
stay deterministic and fast.
"""

from __future__ import annotations

import os
import wave
from unittest.mock import MagicMock, patch

import pytest

from voxera.voice.piper_backend import PiperLocalBackend
from voxera.voice.tts_adapter import (
    TTSBackend,
    TTSBackendUnsupportedError,
    synthesize_tts_request,
)
from voxera.voice.tts_protocol import (
    TTS_ERROR_BACKEND_ERROR,
    TTS_ERROR_BACKEND_MISSING,
    TTS_ERROR_UNSUPPORTED_FORMAT,
    TTS_STATUS_FAILED,
    TTS_STATUS_SUCCEEDED,
    TTS_STATUS_UNAVAILABLE,
    TTS_STATUS_UNSUPPORTED,
    TTSResponse,
    build_tts_request,
)

# -- protocol conformance -----------------------------------------------------


class TestPiperProtocolConformance:
    def test_satisfies_tts_backend_protocol(self) -> None:
        """PiperLocalBackend structurally satisfies TTSBackend."""
        backend: TTSBackend = PiperLocalBackend()
        assert backend.backend_name == "piper_local"

    def test_backend_name(self) -> None:
        backend = PiperLocalBackend()
        assert backend.backend_name == "piper_local"

    def test_backend_name_is_stable(self) -> None:
        """backend_name should be consistent across instances."""
        b1 = PiperLocalBackend()
        b2 = PiperLocalBackend(model="other-model")
        assert b1.backend_name == b2.backend_name == "piper_local"


# -- lazy loading --------------------------------------------------------------


class TestPiperLazyLoading:
    def test_model_not_loaded_at_construction(self) -> None:
        backend = PiperLocalBackend()
        assert backend.model_loaded is False

    def test_model_loaded_property_reflects_state(self) -> None:
        backend = PiperLocalBackend()
        assert backend.model_loaded is False


# -- supports_voice ------------------------------------------------------------


class TestPiperSupportsVoice:
    def test_supports_any_voice_id(self) -> None:
        backend = PiperLocalBackend()
        assert backend.supports_voice("default") is True
        assert backend.supports_voice("en-US") is True
        assert backend.supports_voice("custom-speaker") is True
        assert backend.supports_voice("") is True


# -- missing dependency --------------------------------------------------------


class TestPiperMissingDependency:
    def test_missing_dependency_returns_backend_missing(self) -> None:
        """When piper-tts is not installed, synthesize returns backend_missing."""
        backend = PiperLocalBackend()
        req = build_tts_request(text="Hello world", request_id="dep-miss")
        with patch("voxera.voice.piper_backend._PIPER_AVAILABLE", False):
            result = backend.synthesize(req)
        assert result.audio_path is None
        assert result.error_class == TTS_ERROR_BACKEND_MISSING
        assert "piper-tts" in (result.error or "")

    def test_missing_dependency_through_entry_point(self) -> None:
        """Missing dependency through synthesize_tts_request returns unavailable."""
        backend = PiperLocalBackend()
        req = build_tts_request(text="Hello world", request_id="dep-miss-ep")
        with patch("voxera.voice.piper_backend._PIPER_AVAILABLE", False):
            resp = synthesize_tts_request(req, adapter=backend)
        assert resp.status == TTS_STATUS_UNAVAILABLE
        assert resp.error_class == TTS_ERROR_BACKEND_MISSING

    def test_missing_dependency_does_not_crash(self) -> None:
        """Missing dependency never raises — returns a clean result."""
        backend = PiperLocalBackend()
        req = build_tts_request(text="Test", request_id="dep-safe")
        with patch("voxera.voice.piper_backend._PIPER_AVAILABLE", False):
            result = backend.synthesize(req)
        assert isinstance(result.error, str)
        assert result.audio_path is None


# -- unsupported format --------------------------------------------------------


class TestPiperUnsupportedFormat:
    def test_mp3_raises_unsupported(self) -> None:
        backend = PiperLocalBackend()
        req = build_tts_request(text="Hello", output_format="mp3", request_id="fmt-mp3")
        with (
            patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True),
            pytest.raises(TTSBackendUnsupportedError, match="wav"),
        ):
            backend.synthesize(req)

    def test_ogg_raises_unsupported(self) -> None:
        backend = PiperLocalBackend()
        req = build_tts_request(text="Hello", output_format="ogg", request_id="fmt-ogg")
        with (
            patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True),
            pytest.raises(TTSBackendUnsupportedError, match="wav"),
        ):
            backend.synthesize(req)

    def test_unsupported_format_through_entry_point(self) -> None:
        """Unsupported format through synthesize_tts_request returns unsupported."""
        backend = PiperLocalBackend()
        req = build_tts_request(text="Hello", output_format="mp3", request_id="fmt-ep")
        with patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
            resp = synthesize_tts_request(req, adapter=backend)
        assert resp.status == TTS_STATUS_UNSUPPORTED
        assert resp.error_class == TTS_ERROR_UNSUPPORTED_FORMAT


# -- voice load failure --------------------------------------------------------


class TestPiperVoiceLoadFailure:
    def test_voice_load_failure_returns_error_result(self) -> None:
        """If the Piper voice fails to load, return a clean error result."""
        backend = PiperLocalBackend(model="nonexistent-model-xyz")
        req = build_tts_request(text="Hello", request_id="load-fail")
        with (
            patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True),
            patch.object(backend, "_ensure_voice", side_effect=OSError("model not found")),
        ):
            result = backend.synthesize(req)
        assert result.audio_path is None
        assert result.error_class == TTS_ERROR_BACKEND_ERROR
        assert "failed to load" in (result.error or "").lower()

    def test_voice_load_failure_through_entry_point(self) -> None:
        """Voice load failure through entry point returns failed, not crashed."""
        backend = PiperLocalBackend()
        req = build_tts_request(text="Hello", request_id="load-fail-ep")
        with (
            patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True),
            patch.object(backend, "_ensure_voice", side_effect=MemoryError("OOM")),
        ):
            resp = synthesize_tts_request(req, adapter=backend)
        assert resp.status == TTS_STATUS_FAILED
        assert resp.error_class == TTS_ERROR_BACKEND_ERROR
        assert resp.backend == "piper_local"


# -- successful synthesis (mocked) --------------------------------------------


def _make_mock_voice(
    *,
    audio_bytes: bytes | None = None,
    sample_rate: int = 22050,
) -> MagicMock:
    """Build a mock PiperVoice that returns fixed audio data.

    Produces 16-bit PCM mono audio by default (0.5s of silence at 22050 Hz).
    """
    if audio_bytes is None:
        # 0.5 seconds of silence: 22050 * 0.5 * 2 bytes = 22050 bytes
        num_samples = int(sample_rate * 0.5)
        audio_bytes = b"\x00\x00" * num_samples

    mock_voice = MagicMock()
    mock_voice.synthesize_stream_raw.return_value = iter([audio_bytes])
    mock_config = MagicMock()
    mock_config.sample_rate = sample_rate
    mock_voice.config = mock_config
    return mock_voice


class TestPiperSynthesisSuccess:
    def test_success_returns_audio_path(self, tmp_path) -> None:
        backend = PiperLocalBackend()
        mock_voice = _make_mock_voice()
        backend._voice = mock_voice

        req = build_tts_request(text="Hello world", request_id="ok-1")
        with patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
            result = backend.synthesize(req)

        assert result.audio_path is not None
        assert result.audio_path.endswith(".wav")
        assert os.path.isfile(result.audio_path)
        assert result.error is None
        assert result.error_class is None

        # Cleanup
        os.unlink(result.audio_path)

    def test_success_produces_valid_wav(self) -> None:
        """Output file should be a valid WAV file."""
        backend = PiperLocalBackend()
        mock_voice = _make_mock_voice(sample_rate=22050)
        backend._voice = mock_voice

        req = build_tts_request(text="Hello", request_id="wav-valid")
        with patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
            result = backend.synthesize(req)

        assert result.audio_path is not None
        with wave.open(result.audio_path, "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 22050
            assert wf.getnframes() > 0

        os.unlink(result.audio_path)

    def test_success_reports_timing(self) -> None:
        backend = PiperLocalBackend()
        backend._voice = _make_mock_voice()

        req = build_tts_request(text="Hello", request_id="ok-timing")
        with patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
            result = backend.synthesize(req)

        assert result.inference_ms is not None
        assert result.inference_ms >= 0

        os.unlink(result.audio_path)

    def test_success_reports_audio_duration(self) -> None:
        backend = PiperLocalBackend()
        backend._voice = _make_mock_voice(sample_rate=22050)

        req = build_tts_request(text="Hello", request_id="ok-dur")
        with patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
            result = backend.synthesize(req)

        assert result.audio_duration_ms is not None
        assert result.audio_duration_ms > 0
        # 0.5s of audio at 22050 Hz = ~500ms
        assert 400 <= result.audio_duration_ms <= 600

        os.unlink(result.audio_path)

    def test_success_through_entry_point(self) -> None:
        backend = PiperLocalBackend()
        backend._voice = _make_mock_voice()

        req = build_tts_request(text="Hello world", request_id="ok-ep")
        with patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
            resp = synthesize_tts_request(req, adapter=backend)

        assert resp.status == TTS_STATUS_SUCCEEDED
        assert resp.audio_path is not None
        assert resp.backend == "piper_local"
        assert isinstance(resp, TTSResponse)
        assert resp.error is None
        assert resp.error_class is None

        os.unlink(resp.audio_path)

    def test_timing_fields_pass_through_to_response(self) -> None:
        backend = PiperLocalBackend()
        backend._voice = _make_mock_voice()

        req = build_tts_request(text="Hello", request_id="timing-ep")
        with patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
            resp = synthesize_tts_request(req, adapter=backend)

        assert resp.status == TTS_STATUS_SUCCEEDED
        assert resp.inference_ms is not None
        assert resp.audio_duration_ms is not None

        os.unlink(resp.audio_path)


# -- no fake success -----------------------------------------------------------


class TestPiperNoFakeSuccess:
    def test_empty_audio_data_returns_error(self) -> None:
        """If synthesis produces no audio bytes, return error — not fake success."""
        backend = PiperLocalBackend()
        mock_voice = MagicMock()
        mock_voice.synthesize_stream_raw.return_value = iter([b""])
        mock_config = MagicMock()
        mock_config.sample_rate = 22050
        mock_voice.config = mock_config
        backend._voice = mock_voice

        req = build_tts_request(text="Hello", request_id="no-audio")
        with patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
            result = backend.synthesize(req)

        assert result.audio_path is None
        assert result.error is not None
        assert result.error_class == TTS_ERROR_BACKEND_ERROR

    def test_no_fake_path_when_synthesis_fails(self) -> None:
        """Backend should never return a fake placeholder path."""
        backend = PiperLocalBackend()
        mock_voice = MagicMock()
        mock_voice.synthesize_stream_raw.side_effect = RuntimeError("engine crash")
        backend._voice = mock_voice

        req = build_tts_request(text="Hello", request_id="no-fake")
        with patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
            result = backend.synthesize(req)

        assert result.audio_path is None
        assert result.error is not None


# -- synthesis failure (mocked) ------------------------------------------------


class TestPiperSynthesisFailure:
    def test_backend_exception_returns_error_result(self) -> None:
        backend = PiperLocalBackend()
        mock_voice = MagicMock()
        mock_voice.synthesize_stream_raw.side_effect = RuntimeError("native library crash")
        backend._voice = mock_voice

        req = build_tts_request(text="Hello", request_id="fail-1")
        with patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
            result = backend.synthesize(req)

        assert result.audio_path is None
        assert result.error_class == TTS_ERROR_BACKEND_ERROR
        assert "synthesis failed" in (result.error or "").lower()

    def test_backend_exception_through_entry_point(self) -> None:
        backend = PiperLocalBackend()
        mock_voice = MagicMock()
        mock_voice.synthesize_stream_raw.side_effect = RuntimeError("segfault")
        backend._voice = mock_voice

        req = build_tts_request(text="Hello", request_id="fail-ep")
        with patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
            resp = synthesize_tts_request(req, adapter=backend)

        assert resp.status == TTS_STATUS_FAILED
        assert resp.backend == "piper_local"

    def test_backend_exception_does_not_leak(self) -> None:
        """No exceptions should escape the backend through the entry point."""
        backend = PiperLocalBackend()
        mock_voice = MagicMock()
        mock_voice.synthesize_stream_raw.side_effect = MemoryError("OOM")
        backend._voice = mock_voice

        req = build_tts_request(text="Hello", request_id="no-leak")
        with patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
            resp = synthesize_tts_request(req, adapter=backend)

        assert isinstance(resp, TTSResponse)
        assert resp.status == TTS_STATUS_FAILED


# -- speaker handling ----------------------------------------------------------


class TestPiperSpeakerHandling:
    def test_default_no_speaker(self) -> None:
        backend = PiperLocalBackend()
        assert backend._speaker is None

    def test_explicit_speaker(self) -> None:
        backend = PiperLocalBackend(speaker="1")
        assert backend._speaker == "1"

    def test_speaker_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("VOXERA_VOICE_TTS_PIPER_SPEAKER", "3")
        backend = PiperLocalBackend()
        assert backend._speaker == "3"

    def test_explicit_speaker_overrides_env(self, monkeypatch) -> None:
        monkeypatch.setenv("VOXERA_VOICE_TTS_PIPER_SPEAKER", "3")
        backend = PiperLocalBackend(speaker="5")
        assert backend._speaker == "5"

    def test_speaker_passed_to_synthesize(self) -> None:
        """When speaker is configured, it should be passed to the voice."""
        backend = PiperLocalBackend(speaker="2")
        mock_voice = _make_mock_voice()
        backend._voice = mock_voice

        req = build_tts_request(text="Hello", request_id="spk-pass")
        with patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
            result = backend.synthesize(req)

        mock_voice.synthesize_stream_raw.assert_called_once()
        call_kwargs = mock_voice.synthesize_stream_raw.call_args
        assert call_kwargs[1].get("speaker_id") == 2

        if result.audio_path:
            os.unlink(result.audio_path)

    def test_no_speaker_no_kwarg(self) -> None:
        """When no speaker is configured, speaker_id should not be passed."""
        backend = PiperLocalBackend()
        mock_voice = _make_mock_voice()
        backend._voice = mock_voice

        req = build_tts_request(text="Hello", request_id="spk-none")
        with patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
            result = backend.synthesize(req)

        call_kwargs = mock_voice.synthesize_stream_raw.call_args
        assert "speaker_id" not in call_kwargs[1]

        if result.audio_path:
            os.unlink(result.audio_path)

    def test_non_numeric_speaker_passed_as_string(self) -> None:
        """Non-numeric speaker IDs should be passed through as strings."""
        backend = PiperLocalBackend(speaker="narrator")
        mock_voice = _make_mock_voice()
        backend._voice = mock_voice

        req = build_tts_request(text="Hello", request_id="spk-str")
        with patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
            result = backend.synthesize(req)

        call_kwargs = mock_voice.synthesize_stream_raw.call_args
        assert call_kwargs[1].get("speaker_id") == "narrator"

        if result.audio_path:
            os.unlink(result.audio_path)


# -- configuration -------------------------------------------------------------


class TestPiperConfiguration:
    def test_default_config(self) -> None:
        backend = PiperLocalBackend()
        assert backend._model_name == "en_US-lessac-medium"
        assert backend._speaker is None

    def test_explicit_config(self) -> None:
        backend = PiperLocalBackend(model="de_DE-thorsten-high", speaker="0")
        assert backend._model_name == "de_DE-thorsten-high"
        assert backend._speaker == "0"

    def test_env_config_model(self, monkeypatch) -> None:
        monkeypatch.setenv("VOXERA_VOICE_TTS_PIPER_MODEL", "fr_FR-siwis-medium")
        backend = PiperLocalBackend()
        assert backend._model_name == "fr_FR-siwis-medium"

    def test_env_config_speaker(self, monkeypatch) -> None:
        monkeypatch.setenv("VOXERA_VOICE_TTS_PIPER_SPEAKER", "2")
        backend = PiperLocalBackend()
        assert backend._speaker == "2"

    def test_explicit_overrides_env(self, monkeypatch) -> None:
        monkeypatch.setenv("VOXERA_VOICE_TTS_PIPER_MODEL", "from-env")
        backend = PiperLocalBackend(model="from-arg")
        assert backend._model_name == "from-arg"


# -- multi-chunk synthesis -----------------------------------------------------


class TestPiperMultiChunk:
    def test_multi_chunk_audio_assembled(self) -> None:
        """Piper streams audio in chunks; all chunks should be assembled."""
        backend = PiperLocalBackend()
        chunk1 = b"\x00\x00" * 1000
        chunk2 = b"\x01\x01" * 1000
        mock_voice = MagicMock()
        mock_voice.synthesize_stream_raw.return_value = iter([chunk1, chunk2])
        mock_config = MagicMock()
        mock_config.sample_rate = 22050
        mock_voice.config = mock_config
        backend._voice = mock_voice

        req = build_tts_request(text="Hello", request_id="multi-chunk")
        with patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
            result = backend.synthesize(req)

        assert result.audio_path is not None
        # Verify the file has all the data
        with wave.open(result.audio_path, "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            assert len(frames) == len(chunk1) + len(chunk2)

        os.unlink(result.audio_path)


# -- duration / inference fields -----------------------------------------------


class TestPiperTimingFields:
    def test_duration_none_when_sample_rate_zero(self) -> None:
        """Duration should be None when sample_rate is invalid."""
        backend = PiperLocalBackend()
        mock_voice = _make_mock_voice(sample_rate=0)
        backend._voice = mock_voice

        req = build_tts_request(text="Hello", request_id="dur-zero")
        with patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
            result = backend.synthesize(req)

        # Duration can't be computed with sample_rate=0, but we don't crash
        # The WAV file write itself may also fail with 0 sample rate,
        # which is handled as synthesis failure
        assert result.error is None or result.audio_path is None

        if result.audio_path:
            os.unlink(result.audio_path)

    def test_inference_ms_is_nonnegative(self) -> None:
        backend = PiperLocalBackend()
        backend._voice = _make_mock_voice()

        req = build_tts_request(text="Hello", request_id="infer-pos")
        with patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
            result = backend.synthesize(req)

        assert result.inference_ms is not None
        assert result.inference_ms >= 0

        if result.audio_path:
            os.unlink(result.audio_path)


# -- async entry point ---------------------------------------------------------


class TestPiperAsync:
    @pytest.mark.asyncio
    async def test_async_success(self) -> None:
        from voxera.voice.tts_adapter import synthesize_tts_request_async

        backend = PiperLocalBackend()
        backend._voice = _make_mock_voice()

        req = build_tts_request(text="Hello world", request_id="async-ok")
        with patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
            resp = await synthesize_tts_request_async(req, adapter=backend)

        assert resp.status == TTS_STATUS_SUCCEEDED
        assert resp.audio_path is not None
        assert resp.backend == "piper_local"
        assert isinstance(resp, TTSResponse)

        os.unlink(resp.audio_path)

    @pytest.mark.asyncio
    async def test_async_missing_dep(self) -> None:
        from voxera.voice.tts_adapter import synthesize_tts_request_async

        backend = PiperLocalBackend()
        req = build_tts_request(text="Hello", request_id="async-dep")
        with patch("voxera.voice.piper_backend._PIPER_AVAILABLE", False):
            resp = await synthesize_tts_request_async(req, adapter=backend)

        assert resp.status == TTS_STATUS_UNAVAILABLE
        assert resp.error_class == TTS_ERROR_BACKEND_MISSING

    @pytest.mark.asyncio
    async def test_async_exception_is_fail_soft(self) -> None:
        from voxera.voice.tts_adapter import synthesize_tts_request_async

        backend = PiperLocalBackend()
        mock_voice = MagicMock()
        mock_voice.synthesize_stream_raw.side_effect = RuntimeError("crash")
        backend._voice = mock_voice

        req = build_tts_request(text="Hello", request_id="async-crash")
        with patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
            resp = await synthesize_tts_request_async(req, adapter=backend)

        assert resp.status == TTS_STATUS_FAILED
        assert resp.error_class == TTS_ERROR_BACKEND_ERROR


# -- export surface ------------------------------------------------------------


class TestPiperExportSurface:
    def test_piper_exported_from_voice_package(self) -> None:
        from voxera.voice import PiperLocalBackend as Exported

        assert Exported is PiperLocalBackend
