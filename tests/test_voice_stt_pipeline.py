"""Tests for STT backend selection and voice input pipeline wiring.

Pins:
- ``build_stt_backend`` factory returns correct backends from flags
- ``transcribe_audio_file`` threads through canonical STT path
- truthful outcomes for unconfigured, disabled, unsupported, and
  successful transcription paths
- no fake transcripts or overclaimed source support
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from voxera.voice.flags import VoiceFoundationFlags
from voxera.voice.input import transcribe_audio_file
from voxera.voice.stt_adapter import NullSTTBackend, STTAdapterResult
from voxera.voice.stt_backend_factory import (
    STT_BACKEND_WHISPER_LOCAL,
    build_stt_backend,
)
from voxera.voice.stt_protocol import (
    STT_ERROR_BACKEND_MISSING,
    STT_ERROR_EMPTY_AUDIO,
    STT_STATUS_FAILED,
    STT_STATUS_SUCCEEDED,
    STT_STATUS_UNAVAILABLE,
    STTResponse,
)
from voxera.voice.whisper_backend import WhisperLocalBackend

# -- helpers -----------------------------------------------------------------


def _make_flags(
    *,
    foundation: bool = True,
    voice_input: bool = True,
    voice_output: bool = False,
    stt_backend: str | None = None,
    tts_backend: str | None = None,
) -> VoiceFoundationFlags:
    return VoiceFoundationFlags(
        enable_voice_foundation=foundation,
        enable_voice_input=voice_input,
        enable_voice_output=voice_output,
        voice_stt_backend=stt_backend,
        voice_tts_backend=tts_backend,
    )


# =============================================================================
# Section 1: build_stt_backend factory
# =============================================================================


class TestBuildSTTBackendNullCases:
    """Factory returns NullSTTBackend when no usable backend is configured."""

    def test_no_backend_configured_returns_null(self) -> None:
        flags = _make_flags(stt_backend=None)
        backend = build_stt_backend(flags)
        assert isinstance(backend, NullSTTBackend)
        assert backend.backend_name == "null"

    def test_empty_backend_string_returns_null(self) -> None:
        flags = _make_flags(stt_backend="")
        backend = build_stt_backend(flags)
        assert isinstance(backend, NullSTTBackend)

    def test_whitespace_backend_string_returns_null(self) -> None:
        flags = _make_flags(stt_backend="   ")
        backend = build_stt_backend(flags)
        assert isinstance(backend, NullSTTBackend)

    def test_unrecognized_backend_returns_null(self) -> None:
        flags = _make_flags(stt_backend="google_cloud_stt")
        backend = build_stt_backend(flags)
        assert isinstance(backend, NullSTTBackend)

    def test_voice_foundation_disabled_returns_null(self) -> None:
        flags = _make_flags(foundation=False, stt_backend="whisper_local")
        backend = build_stt_backend(flags)
        assert isinstance(backend, NullSTTBackend)

    def test_voice_input_disabled_returns_null(self) -> None:
        flags = _make_flags(voice_input=False, stt_backend="whisper_local")
        backend = build_stt_backend(flags)
        assert isinstance(backend, NullSTTBackend)


class TestBuildSTTBackendWhisper:
    """Factory returns WhisperLocalBackend when configured."""

    def test_whisper_local_returns_whisper_backend(self) -> None:
        flags = _make_flags(stt_backend="whisper_local")
        backend = build_stt_backend(flags)
        assert isinstance(backend, WhisperLocalBackend)
        assert backend.backend_name == "whisper_local"

    def test_whisper_local_case_insensitive(self) -> None:
        flags = _make_flags(stt_backend="WHISPER_LOCAL")
        backend = build_stt_backend(flags)
        assert isinstance(backend, WhisperLocalBackend)

    def test_whisper_local_strips_whitespace(self) -> None:
        flags = _make_flags(stt_backend="  whisper_local  ")
        backend = build_stt_backend(flags)
        assert isinstance(backend, WhisperLocalBackend)


class TestBuildSTTBackendConstant:
    """The canonical backend identifier constant is correct."""

    def test_whisper_local_constant(self) -> None:
        assert STT_BACKEND_WHISPER_LOCAL == "whisper_local"


# =============================================================================
# Section 2: transcribe_audio_file pipeline wiring
# =============================================================================


class TestTranscribeAudioFileUnconfigured:
    """Unconfigured backend returns truthful unavailable response."""

    def test_no_backend_returns_unavailable(self) -> None:
        flags = _make_flags(stt_backend=None)
        resp = transcribe_audio_file(audio_path="/tmp/test.wav", flags=flags)
        assert isinstance(resp, STTResponse)
        assert resp.status == STT_STATUS_UNAVAILABLE
        assert resp.error_class == STT_ERROR_BACKEND_MISSING
        assert resp.transcript is None

    def test_disabled_foundation_returns_unavailable(self) -> None:
        flags = _make_flags(foundation=False, stt_backend="whisper_local")
        resp = transcribe_audio_file(audio_path="/tmp/test.wav", flags=flags)
        assert resp.status == STT_STATUS_UNAVAILABLE
        assert resp.error_class == STT_ERROR_BACKEND_MISSING
        assert resp.transcript is None

    def test_disabled_input_returns_unavailable(self) -> None:
        flags = _make_flags(voice_input=False, stt_backend="whisper_local")
        resp = transcribe_audio_file(audio_path="/tmp/test.wav", flags=flags)
        assert resp.status == STT_STATUS_UNAVAILABLE
        assert resp.transcript is None

    def test_unknown_backend_returns_unavailable(self) -> None:
        flags = _make_flags(stt_backend="nonexistent_backend")
        resp = transcribe_audio_file(audio_path="/tmp/test.wav", flags=flags)
        assert resp.status == STT_STATUS_UNAVAILABLE
        assert resp.error_class == STT_ERROR_BACKEND_MISSING
        assert resp.transcript is None


class TestTranscribeAudioFileSuccess:
    """Successful transcription through the full pipeline."""

    def test_success_returns_transcript(self, tmp_path) -> None:
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake-audio-data")

        mock_model = MagicMock()
        segment = MagicMock()
        segment.text = "Hello world"
        info = MagicMock()
        info.language = "en"
        info.duration = 3.5
        mock_model.transcribe.return_value = (iter([segment]), info)

        flags = _make_flags(stt_backend="whisper_local")

        with (
            patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", True),
            patch("voxera.voice.stt_backend_factory.WhisperLocalBackend") as MockWhisper,
        ):
            instance = WhisperLocalBackend()
            instance._model = mock_model
            MockWhisper.return_value = instance
            resp = transcribe_audio_file(
                audio_path=str(audio_file),
                flags=flags,
                language="en",
                session_id="test-session",
            )

        assert resp.status == STT_STATUS_SUCCEEDED
        assert resp.transcript == "Hello world"
        assert resp.backend == "whisper_local"
        assert resp.error is None
        assert resp.error_class is None
        assert isinstance(resp, STTResponse)

    def test_request_carries_audio_path(self, tmp_path) -> None:
        """The STTRequest built by transcribe_audio_file carries audio_path."""
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake-audio")

        mock_model = MagicMock()
        segment = MagicMock()
        segment.text = "test"
        info = MagicMock()
        info.language = "en"
        info.duration = 1.0
        mock_model.transcribe.return_value = (iter([segment]), info)

        flags = _make_flags(stt_backend="whisper_local")

        captured_request = None

        class CapturingBackend:
            @property
            def backend_name(self) -> str:
                return "whisper_local"

            def supports_source(self, input_source: str) -> bool:
                return input_source == "audio_file"

            def transcribe(self, request):
                nonlocal captured_request
                captured_request = request
                return STTAdapterResult(transcript="captured")

        with patch(
            "voxera.voice.stt_backend_factory.WhisperLocalBackend",
            return_value=CapturingBackend(),
        ):
            resp = transcribe_audio_file(audio_path=str(audio_file), flags=flags)

        assert resp.status == STT_STATUS_SUCCEEDED
        assert captured_request is not None
        assert captured_request.audio_path == str(audio_file)
        assert captured_request.input_source == "audio_file"


class TestTranscribeAudioFileFailurePaths:
    """Failure paths return truthful responses, never raise."""

    def test_missing_dependency_returns_unavailable(self, tmp_path) -> None:
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake-audio")

        flags = _make_flags(stt_backend="whisper_local")

        with patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", False):
            resp = transcribe_audio_file(audio_path=str(audio_file), flags=flags)

        assert resp.status == STT_STATUS_UNAVAILABLE
        assert resp.error_class == STT_ERROR_BACKEND_MISSING

    def test_nonexistent_file_returns_failed(self) -> None:
        flags = _make_flags(stt_backend="whisper_local")

        with patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", True):
            resp = transcribe_audio_file(audio_path="/nonexistent/path/audio.wav", flags=flags)

        assert resp.status == STT_STATUS_FAILED
        assert resp.transcript is None

    def test_empty_transcript_returns_failed(self, tmp_path) -> None:
        audio_file = tmp_path / "silence.wav"
        audio_file.write_bytes(b"fake-silence")

        mock_model = MagicMock()
        info = MagicMock()
        info.language = "en"
        info.duration = 1.0
        mock_model.transcribe.return_value = (iter([]), info)

        flags = _make_flags(stt_backend="whisper_local")

        with (
            patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", True),
            patch("voxera.voice.stt_backend_factory.WhisperLocalBackend") as MockWhisper,
        ):
            instance = WhisperLocalBackend()
            instance._model = mock_model
            MockWhisper.return_value = instance
            resp = transcribe_audio_file(audio_path=str(audio_file), flags=flags)

        assert resp.status == STT_STATUS_FAILED
        assert resp.error_class == STT_ERROR_EMPTY_AUDIO
        assert resp.transcript is None

    def test_never_raises_on_backend_crash(self, tmp_path) -> None:
        """Pipeline never raises — backend crashes are caught fail-soft."""
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake-audio")

        flags = _make_flags(stt_backend="whisper_local")

        class CrashingBackend:
            @property
            def backend_name(self) -> str:
                return "whisper_local"

            def supports_source(self, input_source: str) -> bool:
                return True

            def transcribe(self, request):
                raise RuntimeError("kaboom")

        with patch(
            "voxera.voice.stt_backend_factory.WhisperLocalBackend",
            return_value=CrashingBackend(),
        ):
            resp = transcribe_audio_file(audio_path=str(audio_file), flags=flags)

        assert resp.status == STT_STATUS_FAILED
        assert resp.transcript is None


# =============================================================================
# Section 3: pipeline uses canonical STT request/adapter path
# =============================================================================


class TestPipelineUsesCanonicalPath:
    """The pipeline correctly threads through build_stt_request + transcribe_stt_request."""

    def test_response_has_schema_version(self) -> None:
        flags = _make_flags(stt_backend=None)
        resp = transcribe_audio_file(audio_path="/tmp/test.wav", flags=flags)
        assert resp.schema_version == 1

    def test_response_has_request_id(self) -> None:
        flags = _make_flags(stt_backend=None)
        resp = transcribe_audio_file(audio_path="/tmp/test.wav", flags=flags)
        assert isinstance(resp.request_id, str)
        assert len(resp.request_id) > 0

    def test_language_passes_through(self, tmp_path) -> None:
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake-audio")

        captured_request = None

        class CapturingBackend:
            @property
            def backend_name(self) -> str:
                return "capturing"

            def supports_source(self, input_source: str) -> bool:
                return True

            def transcribe(self, request):
                nonlocal captured_request
                captured_request = request
                return STTAdapterResult(transcript="ok")

        flags = _make_flags(stt_backend="whisper_local")

        with patch(
            "voxera.voice.stt_backend_factory.WhisperLocalBackend",
            return_value=CapturingBackend(),
        ):
            transcribe_audio_file(audio_path=str(audio_file), flags=flags, language="fr")

        assert captured_request is not None
        assert captured_request.language == "fr"

    def test_session_id_passes_through(self, tmp_path) -> None:
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake-audio")

        captured_request = None

        class CapturingBackend:
            @property
            def backend_name(self) -> str:
                return "capturing"

            def supports_source(self, input_source: str) -> bool:
                return True

            def transcribe(self, request):
                nonlocal captured_request
                captured_request = request
                return STTAdapterResult(transcript="ok")

        flags = _make_flags(stt_backend="whisper_local")

        with patch(
            "voxera.voice.stt_backend_factory.WhisperLocalBackend",
            return_value=CapturingBackend(),
        ):
            transcribe_audio_file(
                audio_path=str(audio_file),
                flags=flags,
                session_id="sess-abc",
            )

        assert captured_request is not None
        assert captured_request.session_id == "sess-abc"


# =============================================================================
# Section 4: truthful source support — only audio_file
# =============================================================================


class TestSourceTruthfulness:
    """The pipeline only claims audio_file support."""

    def test_input_source_is_audio_file(self) -> None:
        """transcribe_audio_file always builds requests with audio_file source."""
        captured_request = None

        class CapturingBackend:
            @property
            def backend_name(self) -> str:
                return "capturing"

            def supports_source(self, input_source: str) -> bool:
                return True

            def transcribe(self, request):
                nonlocal captured_request
                captured_request = request
                return STTAdapterResult(transcript="ok")

        flags = _make_flags(stt_backend="whisper_local")

        with patch(
            "voxera.voice.stt_backend_factory.WhisperLocalBackend",
            return_value=CapturingBackend(),
        ):
            transcribe_audio_file(audio_path="/tmp/test.wav", flags=flags)

        assert captured_request is not None
        assert captured_request.input_source == "audio_file"


# =============================================================================
# Section 5: export surface
# =============================================================================


class TestExportSurface:
    """New public symbols are exported from the voice package."""

    def test_build_stt_backend_exported(self) -> None:
        from voxera.voice import build_stt_backend as exported

        assert exported is build_stt_backend

    def test_transcribe_audio_file_exported(self) -> None:
        from voxera.voice import transcribe_audio_file as exported

        assert exported is transcribe_audio_file
