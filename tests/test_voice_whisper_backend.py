"""Tests for the WhisperLocalBackend STT adapter.

Pins: protocol conformance, lazy loading, missing dependency handling,
supports_source behavior, audio_path requirements, unsupported sources,
and the integration through transcribe_stt_request / async.

The actual faster-whisper model is mocked at the boundary so tests
stay deterministic and fast.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

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
from voxera.voice.whisper_backend import WhisperLocalBackend

# -- protocol conformance -----------------------------------------------------


class TestWhisperProtocolConformance:
    def test_satisfies_stt_backend_protocol(self) -> None:
        """WhisperLocalBackend structurally satisfies STTBackend."""
        backend: STTBackend = WhisperLocalBackend()
        assert backend.backend_name == "whisper_local"

    def test_backend_name(self) -> None:
        backend = WhisperLocalBackend()
        assert backend.backend_name == "whisper_local"


# -- lazy loading --------------------------------------------------------------


class TestWhisperLazyLoading:
    def test_model_not_loaded_at_construction(self) -> None:
        backend = WhisperLocalBackend()
        assert backend.model_loaded is False

    def test_model_loaded_property_reflects_state(self) -> None:
        backend = WhisperLocalBackend()
        assert backend.model_loaded is False
        # We don't trigger loading here — just pin the property exists.


# -- supports_source -----------------------------------------------------------


class TestWhisperSupportsSource:
    def test_supports_audio_file(self) -> None:
        backend = WhisperLocalBackend()
        assert backend.supports_source("audio_file") is True

    def test_does_not_support_microphone(self) -> None:
        backend = WhisperLocalBackend()
        assert backend.supports_source("microphone") is False

    def test_does_not_support_stream(self) -> None:
        backend = WhisperLocalBackend()
        assert backend.supports_source("stream") is False

    def test_does_not_support_unknown(self) -> None:
        backend = WhisperLocalBackend()
        assert backend.supports_source("telepathy") is False


# -- missing dependency --------------------------------------------------------


class TestWhisperMissingDependency:
    def test_missing_dependency_returns_backend_missing(self) -> None:
        """When faster-whisper is not installed, transcribe returns backend_missing."""
        backend = WhisperLocalBackend()
        req = build_stt_request(
            input_source="audio_file",
            request_id="dep-miss",
            audio_path="/tmp/test.wav",
        )
        with patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", False):
            result = backend.transcribe(req)
        assert result.transcript is None
        assert result.error_class == STT_ERROR_BACKEND_MISSING
        assert "faster-whisper" in (result.error or "")

    def test_missing_dependency_through_entry_point(self) -> None:
        """Missing dependency through transcribe_stt_request returns unavailable."""
        backend = WhisperLocalBackend()
        req = build_stt_request(
            input_source="audio_file",
            request_id="dep-miss-ep",
            audio_path="/tmp/test.wav",
        )
        with patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", False):
            resp = transcribe_stt_request(req, adapter=backend)
        assert resp.status == STT_STATUS_UNAVAILABLE
        assert resp.error_class == STT_ERROR_BACKEND_MISSING


# -- unsupported sources -------------------------------------------------------


class TestWhisperUnsupportedSources:
    def test_microphone_raises_unsupported(self) -> None:
        backend = WhisperLocalBackend()
        req = build_stt_request(input_source="microphone", request_id="mic-unsup")
        with (
            patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", True),
            pytest.raises(STTBackendUnsupportedError, match="audio_file"),
        ):
            backend.transcribe(req)

    def test_stream_raises_unsupported(self) -> None:
        backend = WhisperLocalBackend()
        req = build_stt_request(input_source="stream", request_id="stream-unsup")
        with (
            patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", True),
            pytest.raises(STTBackendUnsupportedError, match="audio_file"),
        ):
            backend.transcribe(req)

    def test_microphone_through_entry_point(self) -> None:
        """Unsupported source through transcribe_stt_request returns unsupported."""
        backend = WhisperLocalBackend()
        req = build_stt_request(input_source="microphone", request_id="mic-ep")
        with patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", True):
            resp = transcribe_stt_request(req, adapter=backend)
        assert resp.status == STT_STATUS_UNSUPPORTED
        assert resp.error_class == STT_ERROR_UNSUPPORTED_SOURCE

    def test_stream_through_entry_point(self) -> None:
        backend = WhisperLocalBackend()
        req = build_stt_request(input_source="stream", request_id="stream-ep")
        with patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", True):
            resp = transcribe_stt_request(req, adapter=backend)
        assert resp.status == STT_STATUS_UNSUPPORTED


# -- audio_path requirements ---------------------------------------------------


class TestWhisperAudioPath:
    def test_missing_audio_path_returns_error(self) -> None:
        backend = WhisperLocalBackend()
        req = build_stt_request(input_source="audio_file", request_id="no-path")
        with patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", True):
            result = backend.transcribe(req)
        assert result.transcript is None
        assert result.error_class == STT_ERROR_BACKEND_ERROR
        assert "audio_path" in (result.error or "")

    def test_nonexistent_file_returns_error(self) -> None:
        backend = WhisperLocalBackend()
        req = build_stt_request(
            input_source="audio_file",
            request_id="bad-path",
            audio_path="/nonexistent/audio.wav",
        )
        with patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", True):
            result = backend.transcribe(req)
        assert result.transcript is None
        assert result.error_class == STT_ERROR_BACKEND_ERROR
        assert "not found" in (result.error or "").lower()

    def test_audio_path_required_through_entry_point(self) -> None:
        backend = WhisperLocalBackend()
        req = build_stt_request(input_source="audio_file", request_id="no-path-ep")
        with patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", True):
            resp = transcribe_stt_request(req, adapter=backend)
        assert resp.status == STT_STATUS_FAILED
        assert resp.error_class == STT_ERROR_BACKEND_ERROR


# -- successful transcription (mocked) ----------------------------------------


def _make_mock_model(
    transcript_text: str = "Hello world", language: str = "en", duration: float = 3.5
):
    """Build a mock WhisperModel that returns a fixed transcript.

    Real faster-whisper returns (generator, TranscriptionInfo).  We use
    iter() over a list so the mock matches the real iteration contract.
    """
    mock_model = MagicMock()
    segment = MagicMock()
    segment.text = transcript_text
    info = MagicMock()
    info.language = language
    info.duration = duration
    mock_model.transcribe.return_value = (iter([segment]), info)
    return mock_model


class TestWhisperMultiSegment:
    def test_multi_segment_transcript_joined(self, tmp_path) -> None:
        """Whisper typically returns multiple segments; they should be joined."""
        audio_file = tmp_path / "multi.wav"
        audio_file.write_bytes(b"fake-audio-data")

        mock_model = MagicMock()
        seg1, seg2, seg3 = MagicMock(), MagicMock(), MagicMock()
        seg1.text = " Hello"
        seg2.text = " beautiful"
        seg3.text = " world"
        info = MagicMock()
        info.language = "en"
        info.duration = 4.0
        mock_model.transcribe.return_value = (iter([seg1, seg2, seg3]), info)

        backend = WhisperLocalBackend()
        backend._model = mock_model

        req = build_stt_request(
            input_source="audio_file",
            request_id="multi-seg",
            audio_path=str(audio_file),
        )
        with patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", True):
            resp = transcribe_stt_request(req, adapter=backend)

        assert resp.status == STT_STATUS_SUCCEEDED
        # Entry point normalizes whitespace via normalize_transcript_text
        assert resp.transcript == "Hello beautiful world"

    def test_empty_segments_list(self, tmp_path) -> None:
        """No segments → empty transcript → truthful empty_audio failure."""
        audio_file = tmp_path / "silence.wav"
        audio_file.write_bytes(b"fake-silence")

        mock_model = MagicMock()
        info = MagicMock()
        info.language = "en"
        info.duration = 1.0
        mock_model.transcribe.return_value = (iter([]), info)

        backend = WhisperLocalBackend()
        backend._model = mock_model

        req = build_stt_request(
            input_source="audio_file",
            request_id="no-seg",
            audio_path=str(audio_file),
        )
        with patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", True):
            resp = transcribe_stt_request(req, adapter=backend)

        assert resp.status == STT_STATUS_FAILED
        assert resp.error_class == STT_ERROR_EMPTY_AUDIO


class TestWhisperModelLoadFailure:
    def test_model_load_failure_returns_error_result(self, tmp_path) -> None:
        """If the Whisper model fails to load, return a clean error result."""
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake-audio-data")

        backend = WhisperLocalBackend(model_size="nonexistent-model-xyz")

        req = build_stt_request(
            input_source="audio_file",
            request_id="load-fail",
            audio_path=str(audio_file),
        )
        with (
            patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", True),
            patch.object(backend, "_ensure_model", side_effect=OSError("model not found")),
        ):
            result = backend.transcribe(req)

        assert result.transcript is None
        assert result.error_class == STT_ERROR_BACKEND_ERROR
        assert "failed to load" in (result.error or "").lower()

    def test_model_load_failure_through_entry_point(self, tmp_path) -> None:
        """Model load failure through entry point returns failed, not crashed."""
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake-audio-data")

        backend = WhisperLocalBackend()

        req = build_stt_request(
            input_source="audio_file",
            request_id="load-fail-ep",
            audio_path=str(audio_file),
        )
        with (
            patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", True),
            patch.object(backend, "_ensure_model", side_effect=MemoryError("OOM")),
        ):
            resp = transcribe_stt_request(req, adapter=backend)

        assert resp.status == STT_STATUS_FAILED
        assert resp.error_class == STT_ERROR_BACKEND_ERROR
        assert resp.backend == "whisper_local"


class TestWhisperTranscriptionSuccess:
    def test_success_returns_transcript(self, tmp_path) -> None:
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake-audio-data")

        backend = WhisperLocalBackend()
        mock_model = _make_mock_model()
        backend._model = mock_model

        req = build_stt_request(
            input_source="audio_file",
            request_id="ok-1",
            audio_path=str(audio_file),
        )
        with patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", True):
            result = backend.transcribe(req)

        assert result.transcript == "Hello world"
        assert result.language == "en"
        assert result.error is None
        assert result.error_class is None

    def test_success_reports_timing(self, tmp_path) -> None:
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake-audio-data")

        backend = WhisperLocalBackend()
        backend._model = _make_mock_model(duration=5.0)

        req = build_stt_request(
            input_source="audio_file",
            request_id="ok-timing",
            audio_path=str(audio_file),
        )
        with patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", True):
            result = backend.transcribe(req)

        assert result.inference_ms is not None
        assert result.inference_ms >= 0
        assert result.audio_duration_ms == 5000

    def test_success_through_entry_point(self, tmp_path) -> None:
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake-audio-data")

        backend = WhisperLocalBackend()
        backend._model = _make_mock_model()

        req = build_stt_request(
            input_source="audio_file",
            request_id="ok-ep",
            audio_path=str(audio_file),
        )
        with patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", True):
            resp = transcribe_stt_request(req, adapter=backend)

        assert resp.status == STT_STATUS_SUCCEEDED
        assert resp.transcript == "Hello world"
        assert resp.backend == "whisper_local"
        assert isinstance(resp, STTResponse)

    def test_empty_transcript_after_normalization(self, tmp_path) -> None:
        """Whisper returning whitespace-only text is truthfully empty_audio."""
        audio_file = tmp_path / "silence.wav"
        audio_file.write_bytes(b"fake-silence")

        backend = WhisperLocalBackend()
        backend._model = _make_mock_model(transcript_text="   ")

        req = build_stt_request(
            input_source="audio_file",
            request_id="empty-norm",
            audio_path=str(audio_file),
        )
        with patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", True):
            resp = transcribe_stt_request(req, adapter=backend)

        assert resp.status == STT_STATUS_FAILED
        assert resp.error_class == STT_ERROR_EMPTY_AUDIO

    def test_timing_fields_pass_through_to_response(self, tmp_path) -> None:
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake-audio-data")

        backend = WhisperLocalBackend()
        backend._model = _make_mock_model(duration=2.5)

        req = build_stt_request(
            input_source="audio_file",
            request_id="timing-ep",
            audio_path=str(audio_file),
        )
        with patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", True):
            resp = transcribe_stt_request(req, adapter=backend)

        assert resp.status == STT_STATUS_SUCCEEDED
        assert resp.audio_duration_ms == 2500
        assert resp.inference_ms is not None


# -- transcription failure (mocked) -------------------------------------------


class TestWhisperTranscriptionFailure:
    def test_backend_exception_returns_error_result(self, tmp_path) -> None:
        audio_file = tmp_path / "bad.wav"
        audio_file.write_bytes(b"corrupt-audio")

        backend = WhisperLocalBackend()
        mock_model = MagicMock()
        mock_model.transcribe.side_effect = RuntimeError("native library crash")
        backend._model = mock_model

        req = build_stt_request(
            input_source="audio_file",
            request_id="fail-1",
            audio_path=str(audio_file),
        )
        with patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", True):
            result = backend.transcribe(req)

        assert result.transcript is None
        assert result.error_class == STT_ERROR_BACKEND_ERROR
        assert "crash" in (result.error or "").lower()

    def test_backend_exception_through_entry_point(self, tmp_path) -> None:
        audio_file = tmp_path / "bad.wav"
        audio_file.write_bytes(b"corrupt-audio")

        backend = WhisperLocalBackend()
        mock_model = MagicMock()
        mock_model.transcribe.side_effect = RuntimeError("segfault")
        backend._model = mock_model

        req = build_stt_request(
            input_source="audio_file",
            request_id="fail-ep",
            audio_path=str(audio_file),
        )
        with patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", True):
            resp = transcribe_stt_request(req, adapter=backend)

        assert resp.status == STT_STATUS_FAILED
        assert resp.backend == "whisper_local"


# -- configuration -------------------------------------------------------------


class TestWhisperConfiguration:
    def test_default_config(self) -> None:
        backend = WhisperLocalBackend()
        assert backend._model_size == "base"
        assert backend._device == "auto"
        assert backend._compute_type == "int8"

    def test_explicit_config(self) -> None:
        backend = WhisperLocalBackend(
            model_size="large-v3",
            device="cuda",
            compute_type="float16",
        )
        assert backend._model_size == "large-v3"
        assert backend._device == "cuda"
        assert backend._compute_type == "float16"

    def test_env_config(self, monkeypatch) -> None:
        monkeypatch.setenv("VOXERA_VOICE_STT_WHISPER_MODEL", "tiny")
        monkeypatch.setenv("VOXERA_VOICE_STT_WHISPER_DEVICE", "cpu")
        monkeypatch.setenv("VOXERA_VOICE_STT_WHISPER_COMPUTE_TYPE", "float32")
        backend = WhisperLocalBackend()
        assert backend._model_size == "tiny"
        assert backend._device == "cpu"
        assert backend._compute_type == "float32"


# -- canonical model identifier pinning ---------------------------------------


class TestWhisperCanonicalIdentifiers:
    """Pin the exact identifier passed into faster-whisper's WhisperModel.

    Regression guard for the bug where WHISPER_MODEL_DISTIL_LARGE_V3 was
    set to the Hugging Face PyTorch repo id `distil-whisper/distil-large-v3`.
    That identifier caused faster-whisper to download PyTorch weights that
    lack the CT2 `model.bin`, and the load failed at runtime with
    `Unable to open file 'model.bin' in model '...'`.  The canonical
    faster-whisper identifier is `distil-large-v3`, which resolves to
    `Systran/faster-distil-whisper-large-v3` internally.
    """

    def test_distil_large_v3_constant_is_canonical(self) -> None:
        from voxera.voice.whisper_backend import WHISPER_MODEL_DISTIL_LARGE_V3

        assert WHISPER_MODEL_DISTIL_LARGE_V3 == "distil-large-v3"

    def test_ensure_model_passes_distil_large_v3_to_whisper_model(self) -> None:
        """When operator selects Distil-Whisper, WhisperModel(...) gets
        `distil-large-v3` as its first positional arg — not the raw HF
        PyTorch repo id."""
        from voxera.voice.whisper_backend import WHISPER_MODEL_DISTIL_LARGE_V3

        backend = WhisperLocalBackend(model_size=WHISPER_MODEL_DISTIL_LARGE_V3)

        import sys
        import types

        fake_module = types.ModuleType("faster_whisper")
        captured: dict[str, object] = {}

        def _fake_whisper_model(model_size, *args, **kwargs):
            captured["model_size"] = model_size
            captured["args"] = args
            captured["kwargs"] = kwargs
            return MagicMock()

        fake_module.WhisperModel = _fake_whisper_model  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"faster_whisper": fake_module}):
            backend._ensure_model()

        assert captured["model_size"] == "distil-large-v3"
        # Critical regression pin: the broken HF repo id must NEVER be
        # what reaches faster-whisper.
        assert captured["model_size"] != "distil-whisper/distil-large-v3"

    def test_all_panel_choices_pass_through_unchanged(self) -> None:
        """The factory → backend → WhisperModel chain must not mutate the
        operator's selection.  Every panel-exposed choice reaches
        faster-whisper verbatim."""
        import sys
        import types

        from voxera.voice.whisper_backend import STT_WHISPER_MODEL_CHOICES

        def _make_fake_whisper_model(sink: dict[str, object]):
            def _fake_whisper_model(model_size, *args, **kwargs):
                sink["model_size"] = model_size
                return MagicMock()

            return _fake_whisper_model

        for choice in STT_WHISPER_MODEL_CHOICES:
            backend = WhisperLocalBackend(model_size=choice)
            fake_module = types.ModuleType("faster_whisper")
            captured: dict[str, object] = {}
            fake_module.WhisperModel = _make_fake_whisper_model(captured)  # type: ignore[attr-defined]

            with patch.dict(sys.modules, {"faster_whisper": fake_module}):
                backend._ensure_model()

            assert captured["model_size"] == choice, (
                f"Panel choice {choice!r} was mutated before reaching WhisperModel"
            )


# -- async entry point ---------------------------------------------------------


class TestWhisperAsync:
    @pytest.mark.asyncio
    async def test_async_success(self, tmp_path) -> None:
        from voxera.voice.stt_adapter import transcribe_stt_request_async

        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake-audio-data")

        backend = WhisperLocalBackend()
        backend._model = _make_mock_model()

        req = build_stt_request(
            input_source="audio_file",
            request_id="async-ok",
            audio_path=str(audio_file),
        )
        with patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", True):
            resp = await transcribe_stt_request_async(req, adapter=backend)

        assert resp.status == STT_STATUS_SUCCEEDED
        assert resp.transcript == "Hello world"
        assert isinstance(resp, STTResponse)

    @pytest.mark.asyncio
    async def test_async_missing_dep(self) -> None:
        from voxera.voice.stt_adapter import transcribe_stt_request_async

        backend = WhisperLocalBackend()
        req = build_stt_request(
            input_source="audio_file",
            request_id="async-dep",
            audio_path="/tmp/test.wav",
        )
        with patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", False):
            resp = await transcribe_stt_request_async(req, adapter=backend)

        assert resp.status == STT_STATUS_UNAVAILABLE
        assert resp.error_class == STT_ERROR_BACKEND_MISSING

    @pytest.mark.asyncio
    async def test_async_unsupported_source(self) -> None:
        from voxera.voice.stt_adapter import transcribe_stt_request_async

        backend = WhisperLocalBackend()
        req = build_stt_request(input_source="microphone", request_id="async-unsup")
        with patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", True):
            resp = await transcribe_stt_request_async(req, adapter=backend)

        assert resp.status == STT_STATUS_UNSUPPORTED
        assert resp.error_class == STT_ERROR_UNSUPPORTED_SOURCE


# -- STTRequest audio_path field -----------------------------------------------


class TestSTTRequestAudioPath:
    def test_audio_path_default_none(self) -> None:
        req = build_stt_request(input_source="microphone", request_id="path-none")
        assert req.audio_path is None

    def test_audio_path_set(self) -> None:
        req = build_stt_request(
            input_source="audio_file",
            request_id="path-set",
            audio_path="/tmp/audio.wav",
        )
        assert req.audio_path == "/tmp/audio.wav"

    def test_audio_path_strips_whitespace(self) -> None:
        req = build_stt_request(
            input_source="audio_file",
            request_id="path-ws",
            audio_path="  /tmp/audio.wav  ",
        )
        assert req.audio_path == "/tmp/audio.wav"

    def test_audio_path_in_serialization(self) -> None:
        from voxera.voice.stt_protocol import stt_request_as_dict

        req = build_stt_request(
            input_source="audio_file",
            request_id="path-dict",
            audio_path="/tmp/test.wav",
        )
        d = stt_request_as_dict(req)
        assert d["audio_path"] == "/tmp/test.wav"

    def test_audio_path_none_in_serialization(self) -> None:
        from voxera.voice.stt_protocol import stt_request_as_dict

        req = build_stt_request(input_source="microphone", request_id="path-none-dict")
        d = stt_request_as_dict(req)
        assert d["audio_path"] is None
