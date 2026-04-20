"""Tests for the KokoroLocalBackend TTS adapter.

Pins: protocol conformance, lazy loading, missing dependency handling,
missing-path handling, supports_voice behavior, output format
requirements, configuration, and the integration through
synthesize_tts_request / async.

The actual ``kokoro_onnx`` session is mocked at the boundary so tests
stay deterministic and fast and do not require the extra to be
installed.  Text-first / artifact-oriented semantics match Piper: a
successful synthesis writes a real WAV file on disk; every failure
path returns a truthful non-success result with no fake audio
artifact.
"""

from __future__ import annotations

import os
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from voxera.voice.kokoro_backend import KokoroLocalBackend
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

# -- helpers ------------------------------------------------------------------


def _write_dummy_files(tmp_path: Path) -> tuple[str, str]:
    """Create dummy model + voices files so path-existence checks pass."""
    model_path = tmp_path / "kokoro-v1.0.onnx"
    voices_path = tmp_path / "voices-v1.0.bin"
    model_path.write_bytes(b"")
    voices_path.write_bytes(b"")
    return str(model_path), str(voices_path)


def _make_mock_session(
    *,
    num_samples: int = 11025,
    sample_rate: int = 22050,
) -> MagicMock:
    """Build a mock Kokoro session whose ``create`` returns fixed float32 samples.

    Defaults to ~0.5s of silence at 22050 Hz so the WAV artifact has
    a stable, verifiable duration.
    """
    # Lightweight stand-in for a numpy array; the pure-Python fallback
    # in _float_samples_to_pcm16 accepts any iterable of floats.
    samples = [0.0] * num_samples
    mock_session = MagicMock()
    mock_session.create.return_value = (samples, sample_rate)
    return mock_session


# -- protocol conformance -----------------------------------------------------


class TestKokoroProtocolConformance:
    def test_satisfies_tts_backend_protocol(self) -> None:
        backend: TTSBackend = KokoroLocalBackend()
        assert backend.backend_name == "kokoro_local"

    def test_backend_name_is_stable(self) -> None:
        b1 = KokoroLocalBackend()
        b2 = KokoroLocalBackend(model_path="/tmp/model.onnx")
        assert b1.backend_name == b2.backend_name == "kokoro_local"


# -- lazy loading --------------------------------------------------------------


class TestKokoroLazyLoading:
    def test_session_not_loaded_at_construction(self) -> None:
        backend = KokoroLocalBackend()
        assert backend.session_loaded is False

    def test_lazy_load_preserves_cheap_construction(self, tmp_path: Path) -> None:
        model_path, voices_path = _write_dummy_files(tmp_path)
        # Construction must not import kokoro_onnx or load the model,
        # so a missing dependency should not fail here.
        backend = KokoroLocalBackend(model_path=model_path, voices_path=voices_path)
        assert backend.session_loaded is False


# -- supports_voice ------------------------------------------------------------


class TestKokoroSupportsVoice:
    def test_supports_any_voice_id(self) -> None:
        backend = KokoroLocalBackend()
        assert backend.supports_voice("default") is True
        assert backend.supports_voice("af_sarah") is True
        assert backend.supports_voice("") is True


# -- configuration -------------------------------------------------------------


class TestKokoroConfiguration:
    def test_default_config(self) -> None:
        backend = KokoroLocalBackend()
        assert backend.voice == "af_sarah"
        assert backend.lang == "en-us"

    def test_explicit_config(self, tmp_path: Path) -> None:
        model_path, voices_path = _write_dummy_files(tmp_path)
        backend = KokoroLocalBackend(
            model_path=model_path,
            voices_path=voices_path,
            voice="am_michael",
            lang="en-gb",
        )
        assert backend.model_path == model_path
        assert backend.voices_path == voices_path
        assert backend.voice == "am_michael"
        assert backend.lang == "en-gb"

    def test_env_config_model(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        model_path, voices_path = _write_dummy_files(tmp_path)
        monkeypatch.setenv("VOXERA_VOICE_TTS_KOKORO_MODEL", model_path)
        monkeypatch.setenv("VOXERA_VOICE_TTS_KOKORO_VOICES", voices_path)
        monkeypatch.setenv("VOXERA_VOICE_TTS_KOKORO_VOICE", "am_adam")
        backend = KokoroLocalBackend()
        assert backend.model_path == model_path
        assert backend.voices_path == voices_path
        assert backend.voice == "am_adam"

    def test_explicit_overrides_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env_model = str(tmp_path / "env.onnx")
        arg_model = str(tmp_path / "arg.onnx")
        monkeypatch.setenv("VOXERA_VOICE_TTS_KOKORO_MODEL", env_model)
        backend = KokoroLocalBackend(model_path=arg_model)
        assert backend.model_path == arg_model


# -- missing dependency --------------------------------------------------------


class TestKokoroMissingDependency:
    def test_missing_dependency_returns_backend_missing(self, tmp_path: Path) -> None:
        """When kokoro-onnx is not installed, synthesize returns backend_missing."""
        model_path, voices_path = _write_dummy_files(tmp_path)
        backend = KokoroLocalBackend(model_path=model_path, voices_path=voices_path)
        req = build_tts_request(text="Hello world", request_id="dep-miss")
        with patch("voxera.voice.kokoro_backend._KOKORO_AVAILABLE", False):
            result = backend.synthesize(req)
        assert result.audio_path is None
        assert result.error_class == TTS_ERROR_BACKEND_MISSING
        assert "kokoro-onnx" in (result.error or "")

    def test_missing_dependency_through_entry_point(self, tmp_path: Path) -> None:
        model_path, voices_path = _write_dummy_files(tmp_path)
        backend = KokoroLocalBackend(model_path=model_path, voices_path=voices_path)
        req = build_tts_request(text="Hello world", request_id="dep-miss-ep")
        with patch("voxera.voice.kokoro_backend._KOKORO_AVAILABLE", False):
            resp = synthesize_tts_request(req, adapter=backend)
        assert resp.status == TTS_STATUS_UNAVAILABLE
        assert resp.error_class == TTS_ERROR_BACKEND_MISSING

    def test_missing_dependency_does_not_crash(self) -> None:
        backend = KokoroLocalBackend()
        req = build_tts_request(text="Test", request_id="dep-safe")
        with patch("voxera.voice.kokoro_backend._KOKORO_AVAILABLE", False):
            result = backend.synthesize(req)
        assert isinstance(result.error, str)
        assert result.audio_path is None


# -- missing path --------------------------------------------------------------


class TestKokoroMissingPath:
    def test_missing_model_path_returns_backend_missing(self) -> None:
        """No model path configured -> truthful backend_missing result."""
        backend = KokoroLocalBackend(voices_path="/tmp/voices.bin")
        req = build_tts_request(text="Hello", request_id="no-model")
        with patch("voxera.voice.kokoro_backend._KOKORO_AVAILABLE", True):
            result = backend.synthesize(req)
        assert result.audio_path is None
        assert result.error_class == TTS_ERROR_BACKEND_MISSING
        assert "model path" in (result.error or "").lower()

    def test_missing_voices_path_returns_backend_missing(self, tmp_path: Path) -> None:
        model_path = tmp_path / "kokoro.onnx"
        model_path.write_bytes(b"")
        backend = KokoroLocalBackend(model_path=str(model_path))
        req = build_tts_request(text="Hello", request_id="no-voices")
        with patch("voxera.voice.kokoro_backend._KOKORO_AVAILABLE", True):
            result = backend.synthesize(req)
        assert result.audio_path is None
        assert result.error_class == TTS_ERROR_BACKEND_MISSING
        assert "voices path" in (result.error or "").lower()

    def test_nonexistent_model_file_returns_backend_missing(self, tmp_path: Path) -> None:
        voices_path = tmp_path / "voices.bin"
        voices_path.write_bytes(b"")
        backend = KokoroLocalBackend(
            model_path=str(tmp_path / "missing.onnx"),
            voices_path=str(voices_path),
        )
        req = build_tts_request(text="Hello", request_id="model-missing")
        with patch("voxera.voice.kokoro_backend._KOKORO_AVAILABLE", True):
            result = backend.synthesize(req)
        assert result.audio_path is None
        assert result.error_class == TTS_ERROR_BACKEND_MISSING
        assert "does not exist" in (result.error or "")

    def test_nonexistent_voices_file_returns_backend_missing(self, tmp_path: Path) -> None:
        model_path = tmp_path / "model.onnx"
        model_path.write_bytes(b"")
        backend = KokoroLocalBackend(
            model_path=str(model_path),
            voices_path=str(tmp_path / "missing.bin"),
        )
        req = build_tts_request(text="Hello", request_id="voices-missing")
        with patch("voxera.voice.kokoro_backend._KOKORO_AVAILABLE", True):
            result = backend.synthesize(req)
        assert result.audio_path is None
        assert result.error_class == TTS_ERROR_BACKEND_MISSING
        assert "does not exist" in (result.error or "")


# -- unsupported format --------------------------------------------------------


class TestKokoroUnsupportedFormat:
    def test_mp3_raises_unsupported(self, tmp_path: Path) -> None:
        model_path, voices_path = _write_dummy_files(tmp_path)
        backend = KokoroLocalBackend(model_path=model_path, voices_path=voices_path)
        req = build_tts_request(text="Hello", output_format="mp3", request_id="fmt-mp3")
        with (
            patch("voxera.voice.kokoro_backend._KOKORO_AVAILABLE", True),
            pytest.raises(TTSBackendUnsupportedError, match="wav"),
        ):
            backend.synthesize(req)

    def test_unsupported_format_through_entry_point(self, tmp_path: Path) -> None:
        model_path, voices_path = _write_dummy_files(tmp_path)
        backend = KokoroLocalBackend(model_path=model_path, voices_path=voices_path)
        req = build_tts_request(text="Hello", output_format="mp3", request_id="fmt-ep")
        with patch("voxera.voice.kokoro_backend._KOKORO_AVAILABLE", True):
            resp = synthesize_tts_request(req, adapter=backend)
        assert resp.status == TTS_STATUS_UNSUPPORTED
        assert resp.error_class == TTS_ERROR_UNSUPPORTED_FORMAT


# -- session load failure ------------------------------------------------------


class TestKokoroSessionLoadFailure:
    def test_session_load_failure_returns_error_result(self, tmp_path: Path) -> None:
        model_path, voices_path = _write_dummy_files(tmp_path)
        backend = KokoroLocalBackend(model_path=model_path, voices_path=voices_path)
        req = build_tts_request(text="Hello", request_id="load-fail")
        with (
            patch("voxera.voice.kokoro_backend._KOKORO_AVAILABLE", True),
            patch.object(backend, "_ensure_session", side_effect=OSError("corrupt onnx")),
        ):
            result = backend.synthesize(req)
        assert result.audio_path is None
        assert result.error_class == TTS_ERROR_BACKEND_ERROR
        assert "failed to load" in (result.error or "").lower()

    def test_session_load_failure_through_entry_point(self, tmp_path: Path) -> None:
        """Load failure through entry point returns failed, not crashed."""
        model_path, voices_path = _write_dummy_files(tmp_path)
        backend = KokoroLocalBackend(model_path=model_path, voices_path=voices_path)
        req = build_tts_request(text="Hello", request_id="load-fail-ep")
        with (
            patch("voxera.voice.kokoro_backend._KOKORO_AVAILABLE", True),
            patch.object(backend, "_ensure_session", side_effect=MemoryError("OOM")),
        ):
            resp = synthesize_tts_request(req, adapter=backend)
        assert resp.status == TTS_STATUS_FAILED
        assert resp.error_class == TTS_ERROR_BACKEND_ERROR
        assert resp.backend == "kokoro_local"


# -- successful synthesis (mocked) --------------------------------------------


class TestKokoroSynthesisSuccess:
    def test_success_returns_audio_path(self, tmp_path: Path) -> None:
        model_path, voices_path = _write_dummy_files(tmp_path)
        backend = KokoroLocalBackend(model_path=model_path, voices_path=voices_path)
        backend._session = _make_mock_session()

        req = build_tts_request(text="Hello world", request_id="ok-1")
        with patch("voxera.voice.kokoro_backend._KOKORO_AVAILABLE", True):
            result = backend.synthesize(req)

        assert result.audio_path is not None
        assert result.audio_path.endswith(".wav")
        assert os.path.isfile(result.audio_path)
        assert result.error is None
        assert result.error_class is None

        os.unlink(result.audio_path)

    def test_success_produces_valid_wav(self, tmp_path: Path) -> None:
        model_path, voices_path = _write_dummy_files(tmp_path)
        backend = KokoroLocalBackend(model_path=model_path, voices_path=voices_path)
        backend._session = _make_mock_session(sample_rate=22050, num_samples=11025)

        req = build_tts_request(text="Hello", request_id="wav-valid")
        with patch("voxera.voice.kokoro_backend._KOKORO_AVAILABLE", True):
            result = backend.synthesize(req)

        assert result.audio_path is not None
        with wave.open(result.audio_path, "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 22050
            assert wf.getnframes() > 0

        os.unlink(result.audio_path)

    def test_success_reports_timing(self, tmp_path: Path) -> None:
        model_path, voices_path = _write_dummy_files(tmp_path)
        backend = KokoroLocalBackend(model_path=model_path, voices_path=voices_path)
        backend._session = _make_mock_session()

        req = build_tts_request(text="Hello", request_id="ok-timing")
        with patch("voxera.voice.kokoro_backend._KOKORO_AVAILABLE", True):
            result = backend.synthesize(req)

        assert result.inference_ms is not None
        assert result.inference_ms >= 0

        if result.audio_path:
            os.unlink(result.audio_path)

    def test_success_reports_audio_duration(self, tmp_path: Path) -> None:
        model_path, voices_path = _write_dummy_files(tmp_path)
        backend = KokoroLocalBackend(model_path=model_path, voices_path=voices_path)
        # 0.5s at 22050 Hz
        backend._session = _make_mock_session(sample_rate=22050, num_samples=11025)

        req = build_tts_request(text="Hello", request_id="ok-dur")
        with patch("voxera.voice.kokoro_backend._KOKORO_AVAILABLE", True):
            result = backend.synthesize(req)

        assert result.audio_duration_ms is not None
        assert 400 <= result.audio_duration_ms <= 600

        if result.audio_path:
            os.unlink(result.audio_path)

    def test_success_through_entry_point(self, tmp_path: Path) -> None:
        model_path, voices_path = _write_dummy_files(tmp_path)
        backend = KokoroLocalBackend(model_path=model_path, voices_path=voices_path)
        backend._session = _make_mock_session()

        req = build_tts_request(text="Hello world", request_id="ok-ep")
        with patch("voxera.voice.kokoro_backend._KOKORO_AVAILABLE", True):
            resp = synthesize_tts_request(req, adapter=backend)

        assert resp.status == TTS_STATUS_SUCCEEDED
        assert resp.audio_path is not None
        assert resp.backend == "kokoro_local"
        assert isinstance(resp, TTSResponse)
        assert resp.error is None
        assert resp.error_class is None

        os.unlink(resp.audio_path)

    def test_voice_and_lang_passed_to_session(self, tmp_path: Path) -> None:
        """Configured voice / lang flow into Kokoro's ``create`` call."""
        model_path, voices_path = _write_dummy_files(tmp_path)
        backend = KokoroLocalBackend(
            model_path=model_path,
            voices_path=voices_path,
            voice="am_michael",
            lang="en-gb",
        )
        session = _make_mock_session()
        backend._session = session

        req = build_tts_request(text="Hello", request_id="voice-pass")
        with patch("voxera.voice.kokoro_backend._KOKORO_AVAILABLE", True):
            result = backend.synthesize(req)

        session.create.assert_called_once()
        _, kwargs = session.create.call_args
        assert kwargs["voice"] == "am_michael"
        assert kwargs["lang"] == "en-gb"

        if result.audio_path:
            os.unlink(result.audio_path)


# -- no fake success -----------------------------------------------------------


class TestKokoroNoFakeSuccess:
    def test_empty_audio_data_returns_error(self, tmp_path: Path) -> None:
        """If ``create`` returns no samples, return error — not fake success."""
        model_path, voices_path = _write_dummy_files(tmp_path)
        backend = KokoroLocalBackend(model_path=model_path, voices_path=voices_path)
        session = MagicMock()
        session.create.return_value = ([], 22050)
        backend._session = session

        req = build_tts_request(text="Hello", request_id="no-audio")
        with patch("voxera.voice.kokoro_backend._KOKORO_AVAILABLE", True):
            result = backend.synthesize(req)

        assert result.audio_path is None
        assert result.error is not None
        assert result.error_class == TTS_ERROR_BACKEND_ERROR

    def test_no_fake_path_when_synthesis_fails(self, tmp_path: Path) -> None:
        model_path, voices_path = _write_dummy_files(tmp_path)
        backend = KokoroLocalBackend(model_path=model_path, voices_path=voices_path)
        session = MagicMock()
        session.create.side_effect = RuntimeError("engine crash")
        backend._session = session

        req = build_tts_request(text="Hello", request_id="no-fake")
        with patch("voxera.voice.kokoro_backend._KOKORO_AVAILABLE", True):
            result = backend.synthesize(req)

        assert result.audio_path is None
        assert result.error is not None


# -- synthesis failure (mocked) ------------------------------------------------


class TestKokoroSynthesisFailure:
    def test_backend_exception_returns_error_result(self, tmp_path: Path) -> None:
        model_path, voices_path = _write_dummy_files(tmp_path)
        backend = KokoroLocalBackend(model_path=model_path, voices_path=voices_path)
        session = MagicMock()
        session.create.side_effect = RuntimeError("native library crash")
        backend._session = session

        req = build_tts_request(text="Hello", request_id="fail-1")
        with patch("voxera.voice.kokoro_backend._KOKORO_AVAILABLE", True):
            result = backend.synthesize(req)

        assert result.audio_path is None
        assert result.error_class == TTS_ERROR_BACKEND_ERROR
        assert "synthesis failed" in (result.error or "").lower()

    def test_backend_exception_does_not_leak(self, tmp_path: Path) -> None:
        model_path, voices_path = _write_dummy_files(tmp_path)
        backend = KokoroLocalBackend(model_path=model_path, voices_path=voices_path)
        session = MagicMock()
        session.create.side_effect = MemoryError("OOM")
        backend._session = session

        req = build_tts_request(text="Hello", request_id="no-leak")
        with patch("voxera.voice.kokoro_backend._KOKORO_AVAILABLE", True):
            resp = synthesize_tts_request(req, adapter=backend)

        assert isinstance(resp, TTSResponse)
        assert resp.status == TTS_STATUS_FAILED


# -- async entry point ---------------------------------------------------------


class TestKokoroAsync:
    @pytest.mark.asyncio
    async def test_async_success(self, tmp_path: Path) -> None:
        from voxera.voice.tts_adapter import synthesize_tts_request_async

        model_path, voices_path = _write_dummy_files(tmp_path)
        backend = KokoroLocalBackend(model_path=model_path, voices_path=voices_path)
        backend._session = _make_mock_session()

        req = build_tts_request(text="Hello world", request_id="async-ok")
        with patch("voxera.voice.kokoro_backend._KOKORO_AVAILABLE", True):
            resp = await synthesize_tts_request_async(req, adapter=backend)

        assert resp.status == TTS_STATUS_SUCCEEDED
        assert resp.audio_path is not None
        assert resp.backend == "kokoro_local"

        os.unlink(resp.audio_path)

    @pytest.mark.asyncio
    async def test_async_missing_dep(self) -> None:
        from voxera.voice.tts_adapter import synthesize_tts_request_async

        backend = KokoroLocalBackend()
        req = build_tts_request(text="Hello", request_id="async-dep")
        with patch("voxera.voice.kokoro_backend._KOKORO_AVAILABLE", False):
            resp = await synthesize_tts_request_async(req, adapter=backend)

        assert resp.status == TTS_STATUS_UNAVAILABLE
        assert resp.error_class == TTS_ERROR_BACKEND_MISSING


# -- export surface ------------------------------------------------------------


class TestKokoroExportSurface:
    def test_kokoro_exported_from_voice_package(self) -> None:
        from voxera.voice import KokoroLocalBackend as Exported

        assert Exported is KokoroLocalBackend


# -- factory integration -------------------------------------------------------


class TestKokoroFactoryIntegration:
    def test_factory_returns_kokoro_backend_when_configured(self, tmp_path: Path) -> None:
        from voxera.voice.flags import VoiceFoundationFlags
        from voxera.voice.tts_backend_factory import build_tts_backend

        model_path, voices_path = _write_dummy_files(tmp_path)
        flags = VoiceFoundationFlags(
            enable_voice_foundation=True,
            enable_voice_input=False,
            enable_voice_output=True,
            voice_stt_backend=None,
            voice_tts_backend="kokoro_local",
            voice_tts_kokoro_model=model_path,
            voice_tts_kokoro_voices=voices_path,
            voice_tts_kokoro_voice="am_michael",
        )
        backend = build_tts_backend(flags)
        assert isinstance(backend, KokoroLocalBackend)
        assert backend.model_path == model_path
        assert backend.voices_path == voices_path
        assert backend.voice == "am_michael"

    def test_factory_case_insensitive_kokoro(self, tmp_path: Path) -> None:
        from voxera.voice.flags import VoiceFoundationFlags
        from voxera.voice.tts_backend_factory import build_tts_backend

        flags = VoiceFoundationFlags(
            enable_voice_foundation=True,
            enable_voice_input=False,
            enable_voice_output=True,
            voice_stt_backend=None,
            voice_tts_backend="KOKORO_LOCAL",
        )
        backend = build_tts_backend(flags)
        assert isinstance(backend, KokoroLocalBackend)

    def test_factory_kokoro_choice_exposed(self) -> None:
        from voxera.voice.tts_backend_factory import (
            TTS_BACKEND_CHOICES,
            TTS_BACKEND_KOKORO_LOCAL,
            TTS_BACKEND_PIPER_LOCAL,
        )

        assert TTS_BACKEND_KOKORO_LOCAL == "kokoro_local"
        # Piper remains first so the default ordering does not flip.
        assert TTS_BACKEND_CHOICES == (TTS_BACKEND_PIPER_LOCAL, TTS_BACKEND_KOKORO_LOCAL)

    def test_shared_backend_distinct_for_piper_vs_kokoro(self, tmp_path: Path) -> None:
        """Switching the backend flag rebuilds the shared instance cleanly."""
        from voxera.voice.flags import VoiceFoundationFlags
        from voxera.voice.piper_backend import PiperLocalBackend
        from voxera.voice.tts_backend_factory import (
            get_shared_tts_backend,
            reset_shared_tts_backend,
        )

        reset_shared_tts_backend()
        try:
            piper_flags = VoiceFoundationFlags(
                enable_voice_foundation=True,
                enable_voice_input=False,
                enable_voice_output=True,
                voice_stt_backend=None,
                voice_tts_backend="piper_local",
            )
            piper_backend = get_shared_tts_backend(piper_flags)
            assert isinstance(piper_backend, PiperLocalBackend)

            kokoro_flags = VoiceFoundationFlags(
                enable_voice_foundation=True,
                enable_voice_input=False,
                enable_voice_output=True,
                voice_stt_backend=None,
                voice_tts_backend="kokoro_local",
            )
            kokoro_backend = get_shared_tts_backend(kokoro_flags)
            assert isinstance(kokoro_backend, KokoroLocalBackend)
            assert kokoro_backend is not piper_backend
        finally:
            reset_shared_tts_backend()

    def test_shared_backend_rebuilds_when_kokoro_paths_change(self, tmp_path: Path) -> None:
        """The shared-instance cache key covers Kokoro paths / voice.

        If the operator updates the configured model/voices/voice via
        runtime config or env, the cached backend must be rebuilt so
        the next ``synthesize_text`` call picks up the new paths.
        """
        from voxera.voice.flags import VoiceFoundationFlags
        from voxera.voice.tts_backend_factory import (
            get_shared_tts_backend,
            reset_shared_tts_backend,
        )

        reset_shared_tts_backend()
        try:
            model_a = tmp_path / "a.onnx"
            voices_a = tmp_path / "va.bin"
            model_b = tmp_path / "b.onnx"
            voices_b = tmp_path / "vb.bin"
            for p in (model_a, voices_a, model_b, voices_b):
                p.write_bytes(b"")

            flags_a = VoiceFoundationFlags(
                enable_voice_foundation=True,
                enable_voice_input=False,
                enable_voice_output=True,
                voice_stt_backend=None,
                voice_tts_backend="kokoro_local",
                voice_tts_kokoro_model=str(model_a),
                voice_tts_kokoro_voices=str(voices_a),
                voice_tts_kokoro_voice="af_sarah",
            )
            backend_a = get_shared_tts_backend(flags_a)
            # Idempotent call -- same flags -> same instance.
            assert get_shared_tts_backend(flags_a) is backend_a

            # Flip the model path.
            flags_b_model = VoiceFoundationFlags(
                enable_voice_foundation=True,
                enable_voice_input=False,
                enable_voice_output=True,
                voice_stt_backend=None,
                voice_tts_backend="kokoro_local",
                voice_tts_kokoro_model=str(model_b),
                voice_tts_kokoro_voices=str(voices_a),
                voice_tts_kokoro_voice="af_sarah",
            )
            backend_b = get_shared_tts_backend(flags_b_model)
            assert backend_b is not backend_a
            assert isinstance(backend_b, KokoroLocalBackend)
            assert backend_b.model_path == str(model_b)

            # Flip only the voice.
            flags_c_voice = VoiceFoundationFlags(
                enable_voice_foundation=True,
                enable_voice_input=False,
                enable_voice_output=True,
                voice_stt_backend=None,
                voice_tts_backend="kokoro_local",
                voice_tts_kokoro_model=str(model_b),
                voice_tts_kokoro_voices=str(voices_a),
                voice_tts_kokoro_voice="am_michael",
            )
            backend_c = get_shared_tts_backend(flags_c_voice)
            assert backend_c is not backend_b
            assert isinstance(backend_c, KokoroLocalBackend)
            assert backend_c.voice == "am_michael"
        finally:
            reset_shared_tts_backend()


# -- text-first preservation ---------------------------------------------------


class TestKokoroTextFirst:
    """TTS failure must not corrupt the caller's text path.

    The ``synthesize_text`` entry point is artifact-oriented and
    already fail-soft; these tests pin that the Kokoro adapter
    participates in that contract rather than raising out of it.
    """

    def test_kokoro_unavailable_does_not_raise_through_synthesize_text(self) -> None:
        from voxera.voice.flags import VoiceFoundationFlags
        from voxera.voice.output import synthesize_text

        flags = VoiceFoundationFlags(
            enable_voice_foundation=True,
            enable_voice_input=False,
            enable_voice_output=True,
            voice_stt_backend=None,
            voice_tts_backend="kokoro_local",
            # No paths configured — kokoro should return backend_missing
            # which becomes a truthful ``unavailable`` response with no
            # audio artifact.
        )
        with patch("voxera.voice.kokoro_backend._KOKORO_AVAILABLE", True):
            resp = synthesize_text(
                text="Text must stay authoritative.",
                flags=flags,
                session_id="text-first",
            )
        assert resp.status == TTS_STATUS_UNAVAILABLE
        assert resp.audio_path is None
        assert resp.backend == "kokoro_local"
