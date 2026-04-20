"""Tests for the MoonshineLocalBackend STT adapter.

Pins: protocol conformance, lazy loading, missing dependency handling,
supports_source behavior, audio_path requirements, unsupported sources,
and the integration through transcribe_stt_request / async.

The real moonshine package is mocked at the seam so tests stay
deterministic and fast regardless of whether ``moonshine-onnx`` is
installed in the host environment.
"""

from __future__ import annotations

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
        backend._model = MagicMock()
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
        assert "moonshine" in (result.error or "").lower()

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


# -- successful transcription (mocked) ----------------------------------------


class TestMoonshineTranscriptionSuccess:
    def test_string_result_returns_transcript(self, tmp_path) -> None:
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake-audio-data")

        backend = MoonshineLocalBackend()
        backend._model = MagicMock(return_value="Hello from moonshine")

        req = build_stt_request(
            input_source="audio_file",
            request_id="ok-string",
            audio_path=str(audio_file),
        )
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True):
            result = backend.transcribe(req)

        assert result.transcript == "Hello from moonshine"
        assert result.error is None
        assert result.error_class is None

    def test_list_result_joined(self, tmp_path) -> None:
        """Moonshine variants can return a list of strings — we join them."""
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake-audio-data")

        backend = MoonshineLocalBackend()
        backend._model = MagicMock(return_value=["Hello", "world"])

        req = build_stt_request(
            input_source="audio_file",
            request_id="ok-list",
            audio_path=str(audio_file),
        )
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True):
            result = backend.transcribe(req)

        assert result.transcript == "Hello world"

    def test_success_through_entry_point(self, tmp_path) -> None:
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake-audio-data")

        backend = MoonshineLocalBackend()
        backend._model = MagicMock(return_value="Hello entry point")

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

    def test_empty_transcript_maps_to_empty_audio(self, tmp_path) -> None:
        """Whitespace-only moonshine output collapses to empty_audio failure."""
        audio_file = tmp_path / "silence.wav"
        audio_file.write_bytes(b"fake-silence")

        backend = MoonshineLocalBackend()
        backend._model = MagicMock(return_value="   ")

        req = build_stt_request(
            input_source="audio_file",
            request_id="empty",
            audio_path=str(audio_file),
        )
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True):
            resp = transcribe_stt_request(req, adapter=backend)

        assert resp.status == STT_STATUS_FAILED
        assert resp.error_class == STT_ERROR_EMPTY_AUDIO


# -- transcription failure (mocked) -------------------------------------------


class TestMoonshineTranscriptionFailure:
    def test_backend_exception_returns_error(self, tmp_path) -> None:
        audio_file = tmp_path / "bad.wav"
        audio_file.write_bytes(b"corrupt-audio")

        backend = MoonshineLocalBackend()
        backend._model = MagicMock(side_effect=RuntimeError("native crash"))

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
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake-audio-data")

        backend = MoonshineLocalBackend(model_name="totally-bogus-model")
        req = build_stt_request(
            input_source="audio_file",
            request_id="load-fail",
            audio_path=str(audio_file),
        )
        with (
            patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True),
            patch.object(backend, "_ensure_model", side_effect=OSError("model not found")),
        ):
            result = backend.transcribe(req)

        assert result.transcript is None
        assert result.error_class == STT_ERROR_BACKEND_ERROR
        assert "failed to load" in (result.error or "").lower()


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
    async def test_async_success(self, tmp_path) -> None:
        from voxera.voice.stt_adapter import transcribe_stt_request_async

        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake-audio-data")

        backend = MoonshineLocalBackend()
        backend._model = MagicMock(return_value="Async hello")

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
