"""Focused tests for PiperLocalBackend — piper-tts 1.4.2 API compatibility.

Root cause: piper-tts 1.4.2 removed synthesize_stream_raw; the supported
API is PiperVoice.synthesize_wav(text, wav_file, ...) which writes directly
to a wave.Wave_write object.

These tests mock the Piper dependency so they run without piper-tts installed.
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Any
from unittest import mock

from voxera.voice.piper_backend import PiperLocalBackend
from voxera.voice.tts_adapter import synthesize_tts_request
from voxera.voice.tts_protocol import (
    TTS_ERROR_BACKEND_ERROR,
    TTS_ERROR_BACKEND_MISSING,
    TTS_STATUS_FAILED,
    TTS_STATUS_SUCCEEDED,
    TTS_STATUS_UNAVAILABLE,
    TTS_STATUS_UNSUPPORTED,
    build_tts_request,
)


def _make_request(text: str = "hello", output_format: str = "wav") -> Any:
    return build_tts_request(text=text, output_format=output_format)


def _fake_synthesize_wav(text: str, wav_file: wave.Wave_write, **kwargs: Any) -> None:
    """Minimal valid WAV writer — simulates piper-tts 1.4.2 synthesize_wav."""
    wav_file.setnchannels(1)
    wav_file.setsampwidth(2)
    wav_file.setframerate(22050)
    wav_file.writeframes(b"\x00\x00" * 220)  # ~10 ms of silence


def _make_backend_with_mock_voice(voice_mock: Any) -> PiperLocalBackend:
    """Build a PiperLocalBackend with a pre-injected voice (skips lazy load)."""
    backend = PiperLocalBackend(model="test-model")
    backend._voice = voice_mock
    return backend


# ---------------------------------------------------------------------------
# 1. Successful synthesis via synthesize_wav (piper-tts 1.4.2 path)
# ---------------------------------------------------------------------------


def test_piper_synthesize_wav_success(tmp_path: Path) -> None:
    voice_mock = mock.MagicMock()
    voice_mock.synthesize_wav.side_effect = _fake_synthesize_wav

    backend = _make_backend_with_mock_voice(voice_mock)
    request = _make_request()

    with mock.patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
        result = synthesize_tts_request(request, adapter=backend)

    assert result.status == TTS_STATUS_SUCCEEDED
    assert result.backend == "piper_local"
    assert result.audio_path is not None
    assert Path(result.audio_path).exists()
    assert Path(result.audio_path).stat().st_size > 0
    assert result.error is None
    assert result.error_class is None
    assert result.audio_duration_ms is not None
    assert result.audio_duration_ms > 0
    # Cleanup
    Path(result.audio_path).unlink(missing_ok=True)


def test_piper_synthesize_wav_is_called_not_stream_raw() -> None:
    """synthesize_wav must be called; synthesize_stream_raw must NOT be called."""
    voice_mock = mock.MagicMock()
    voice_mock.synthesize_wav.side_effect = _fake_synthesize_wav

    backend = _make_backend_with_mock_voice(voice_mock)
    request = _make_request()

    with mock.patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
        result = synthesize_tts_request(request, adapter=backend)

    voice_mock.synthesize_wav.assert_called_once()
    voice_mock.synthesize_stream_raw.assert_not_called()
    assert result.status == TTS_STATUS_SUCCEEDED
    if result.audio_path:
        Path(result.audio_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 2. Missing / unsupported synthesis method => failed / backend_error
# ---------------------------------------------------------------------------


def test_piper_missing_synthesize_wav_returns_backend_error() -> None:
    """If synthesize_wav raises AttributeError (method absent), report backend_error."""
    voice_mock = mock.MagicMock(spec=[])  # no attributes at all
    # Accessing synthesize_wav on a spec=[] mock raises AttributeError
    type(voice_mock).synthesize_wav = mock.PropertyMock(
        side_effect=AttributeError("'PiperVoice' object has no attribute 'synthesize_wav'")
    )

    backend = _make_backend_with_mock_voice(voice_mock)
    request = _make_request()

    with mock.patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
        result = synthesize_tts_request(request, adapter=backend)

    assert result.status == TTS_STATUS_FAILED
    assert result.error_class == TTS_ERROR_BACKEND_ERROR
    assert result.audio_path is None
    assert "Piper synthesis failed" in (result.error or "")


def test_piper_synthesize_wav_raises_returns_backend_error() -> None:
    """A runtime exception from synthesize_wav maps to failed / backend_error."""
    voice_mock = mock.MagicMock()
    voice_mock.synthesize_wav.side_effect = RuntimeError("ONNX inference failed")

    backend = _make_backend_with_mock_voice(voice_mock)
    request = _make_request()

    with mock.patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
        result = synthesize_tts_request(request, adapter=backend)

    assert result.status == TTS_STATUS_FAILED
    assert result.error_class == TTS_ERROR_BACKEND_ERROR
    assert result.audio_path is None
    assert "Piper synthesis failed" in (result.error or "")


# ---------------------------------------------------------------------------
# 3. Empty output artifact => failure, not success
# ---------------------------------------------------------------------------


def test_piper_zero_frame_artifact_returns_backend_error() -> None:
    """A WAV file with zero audio frames must not be returned as success."""
    voice_mock = mock.MagicMock()

    def write_zero_frames(text: str, wav_file: wave.Wave_write, **kwargs: object) -> None:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22050)
        # no writeframes() — zero-frame WAV

    voice_mock.synthesize_wav.side_effect = write_zero_frames

    backend = _make_backend_with_mock_voice(voice_mock)
    request = _make_request()

    with mock.patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
        result = synthesize_tts_request(request, adapter=backend)

    assert result.status == TTS_STATUS_FAILED
    assert result.error_class == TTS_ERROR_BACKEND_ERROR
    assert result.audio_path is None
    assert "no audio data" in (result.error or "")


# ---------------------------------------------------------------------------
# 4. Canonical TTS response contract
# ---------------------------------------------------------------------------


def test_piper_response_contract_on_success(tmp_path: Path) -> None:
    """Verify all required contract fields are present and correct on success."""
    voice_mock = mock.MagicMock()
    voice_mock.synthesize_wav.side_effect = _fake_synthesize_wav

    backend = _make_backend_with_mock_voice(voice_mock)
    request = _make_request(text="contract check")

    with mock.patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
        resp = synthesize_tts_request(request, adapter=backend)

    assert resp.backend == "piper_local"
    assert resp.status == TTS_STATUS_SUCCEEDED
    assert resp.audio_path is not None and resp.audio_path.endswith(".wav")
    assert resp.error is None
    assert resp.error_class is None
    assert resp.request_id == request.request_id
    if resp.audio_path:
        Path(resp.audio_path).unlink(missing_ok=True)


def test_piper_response_contract_on_failure() -> None:
    """On failure, audio_path must be None and error fields must be set."""
    voice_mock = mock.MagicMock()
    voice_mock.synthesize_wav.side_effect = RuntimeError("boom")

    backend = _make_backend_with_mock_voice(voice_mock)
    request = _make_request(text="contract failure check")

    with mock.patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
        resp = synthesize_tts_request(request, adapter=backend)

    assert resp.backend == "piper_local"
    assert resp.status == TTS_STATUS_FAILED
    assert resp.audio_path is None
    assert resp.error is not None
    assert resp.error_class == TTS_ERROR_BACKEND_ERROR


# ---------------------------------------------------------------------------
# 5. Piper not installed => unavailable / backend_missing (no regression)
# ---------------------------------------------------------------------------


def test_piper_not_installed_returns_unavailable() -> None:
    backend = PiperLocalBackend(model="test-model")
    request = _make_request()

    with mock.patch("voxera.voice.piper_backend._PIPER_AVAILABLE", False):
        result = synthesize_tts_request(request, adapter=backend)

    assert result.status == TTS_STATUS_UNAVAILABLE
    assert result.error_class == TTS_ERROR_BACKEND_MISSING
    assert result.audio_path is None


# ---------------------------------------------------------------------------
# 6. Non-WAV format => unsupported
# ---------------------------------------------------------------------------


def test_piper_non_wav_format_returns_unsupported() -> None:
    voice_mock = mock.MagicMock()
    backend = _make_backend_with_mock_voice(voice_mock)
    request = _make_request(output_format="mp3")

    with mock.patch("voxera.voice.piper_backend._PIPER_AVAILABLE", True):
        result = synthesize_tts_request(request, adapter=backend)

    assert result.status == TTS_STATUS_UNSUPPORTED
    assert result.audio_path is None
