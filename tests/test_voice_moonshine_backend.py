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

from pathlib import Path
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


# -- normalization seam (non-WAV → PCM WAV transcode) -------------------------


class TestMoonshineNormalization:
    """The Moonshine backend runs audio through
    :func:`voxera.voice.audio_normalize.ensure_pcm_wav` before handing
    it to ``load_wav_file``.  Already-PCM-WAV inputs pay zero cost;
    non-WAV inputs (e.g. browser-captured ``audio/webm``) are
    transcoded to a temp PCM WAV file and cleaned up after use.
    """

    def test_non_wav_input_triggers_normalization(self, tmp_path, monkeypatch) -> None:
        not_a_wav = tmp_path / "clip.webm"
        not_a_wav.write_bytes(b"\x1a\x45\xdf\xa3")  # EBML header, not RIFF

        fake_wav = tmp_path / "norm.wav"
        fake_wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVEnorm")

        calls: list[str] = []

        def fake_ensure(source):
            calls.append(str(source))
            return fake_wav, fake_wav  # caller must cleanup fake_wav

        monkeypatch.setattr("voxera.voice.moonshine_backend.ensure_pcm_wav", fake_ensure)

        backend = MoonshineLocalBackend()
        _prime_backend(backend, _fake_transcript("hello post normalize"))
        monkeypatch.setattr(
            "voxera.voice.moonshine_backend.load_wav_file",
            MagicMock(return_value=([0.0] * 16000, 16000)),
            raising=False,
        )

        req = build_stt_request(
            input_source="audio_file",
            request_id="norm",
            audio_path=str(not_a_wav),
        )
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True):
            result = backend.transcribe(req)

        assert result.transcript == "hello post normalize"
        assert calls == [str(not_a_wav)]
        # Temp file cleanup must have run — the backend's ``finally``
        # block unlinks any cleanup_path returned from ensure_pcm_wav.
        assert not fake_wav.exists()

    def test_already_wav_input_skips_normalization(self, tmp_path, monkeypatch) -> None:
        """When the input already passes ``is_pcm_wav``, the backend
        must hand it directly to ``load_wav_file`` without paying the
        transcode cost.  Regression pin: a naive 'always transcode'
        design would waste CPU on every turn for file-path WAV
        uploads."""
        wav_path = tmp_path / "real.wav"
        wav_path.write_bytes(b"RIFF\x00\x00\x00\x00WAVEdata")

        observed: list[tuple[Path, Path | None]] = []
        real_ensure = __import__(
            "voxera.voice.audio_normalize", fromlist=["ensure_pcm_wav"]
        ).ensure_pcm_wav

        def tracking_ensure(source):
            result = real_ensure(source)
            observed.append(result)
            return result

        monkeypatch.setattr("voxera.voice.moonshine_backend.ensure_pcm_wav", tracking_ensure)

        backend = MoonshineLocalBackend()
        _prime_backend(backend, _fake_transcript("no transcode"))
        monkeypatch.setattr(
            "voxera.voice.moonshine_backend.load_wav_file",
            MagicMock(return_value=([0.0] * 16000, 16000)),
            raising=False,
        )

        req = build_stt_request(
            input_source="audio_file",
            request_id="already-wav",
            audio_path=str(wav_path),
        )
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True):
            result = backend.transcribe(req)

        assert result.transcript == "no transcode"
        assert len(observed) == 1
        path_used, cleanup = observed[0]
        assert path_used == wav_path
        assert cleanup is None  # no temp file — no transcode happened

    def test_normalize_failure_yields_truthful_backend_error(self, tmp_path, monkeypatch) -> None:
        not_a_wav = tmp_path / "bogus.webm"
        not_a_wav.write_bytes(b"\x1a\x45\xdf\xa3")

        def fake_ensure(_source):
            raise RuntimeError("PyAV failed to decode: no audio stream found in bogus.webm")

        monkeypatch.setattr("voxera.voice.moonshine_backend.ensure_pcm_wav", fake_ensure)

        backend = MoonshineLocalBackend()
        # prime transcriber so the normalize error is the one that
        # surfaces, not a model-load error.
        _prime_backend(backend, _fake_transcript("unused"))

        req = build_stt_request(
            input_source="audio_file",
            request_id="norm-fail",
            audio_path=str(not_a_wav),
        )
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True):
            result = backend.transcribe(req)
        assert result.transcript is None
        assert result.error_class == STT_ERROR_BACKEND_ERROR
        assert "normalize audio" in (result.error or "")
        assert "no audio stream" in (result.error or "")

    def test_temp_cleanup_runs_even_when_transcription_fails(self, tmp_path, monkeypatch) -> None:
        """If transcription raises after a successful transcode, the
        temp PCM WAV must still be unlinked so the mic-upload lane
        does not leak temp files on persistent failures."""
        not_a_wav = tmp_path / "clip.webm"
        not_a_wav.write_bytes(b"\x1a\x45\xdf\xa3")

        tmp_wav = tmp_path / "leaked.wav"
        tmp_wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVEtmp")

        monkeypatch.setattr(
            "voxera.voice.moonshine_backend.ensure_pcm_wav",
            lambda source: (tmp_wav, tmp_wav),
        )
        monkeypatch.setattr(
            "voxera.voice.moonshine_backend.load_wav_file",
            MagicMock(side_effect=RuntimeError("boom")),
            raising=False,
        )

        backend = MoonshineLocalBackend()
        _prime_backend(backend, _fake_transcript("unused"))

        req = build_stt_request(
            input_source="audio_file",
            request_id="leak-test",
            audio_path=str(not_a_wav),
        )
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True):
            result = backend.transcribe(req)

        assert result.transcript is None
        assert result.error_class == STT_ERROR_BACKEND_ERROR
        assert not tmp_wav.exists()


# -- successful transcription (mocked) ----------------------------------------


class TestMoonshineTranscriptionSuccess:
    def _fake_load(self, audio=(0.0,) * 16000, sr: int = 16000):
        return MagicMock(return_value=(list(audio), sr))

    def test_single_line_transcript_passes_through(self, tmp_path, monkeypatch) -> None:
        audio_file = tmp_path / "clip.wav"
        audio_file.write_bytes(b"RIFF\x00\x00\x00\x00WAVEstub")

        backend = MoonshineLocalBackend()
        _prime_backend(backend, _fake_transcript("Hello from moonshine"))
        monkeypatch.setattr(
            "voxera.voice.moonshine_backend.load_wav_file",
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
        audio_file.write_bytes(b"RIFF\x00\x00\x00\x00WAVEstub")

        backend = MoonshineLocalBackend()
        _prime_backend(backend, _fake_transcript("Hello", "beautiful", "world"))
        monkeypatch.setattr(
            "voxera.voice.moonshine_backend.load_wav_file",
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
        audio_file.write_bytes(b"RIFF\x00\x00\x00\x00WAVEstub")

        backend = MoonshineLocalBackend()
        _prime_backend(backend, _fake_transcript("one two three"))
        # 32000 samples @ 16 kHz = 2000 ms
        monkeypatch.setattr(
            "voxera.voice.moonshine_backend.load_wav_file",
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
        audio_file.write_bytes(b"RIFF\x00\x00\x00\x00WAVEstub")

        backend = MoonshineLocalBackend()
        _prime_backend(backend, _fake_transcript("Hello entry point"))
        monkeypatch.setattr(
            "voxera.voice.moonshine_backend.load_wav_file",
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
        """Zero-line Moonshine return → ``empty_audio`` failure with a
        truthful diagnostic error that distinguishes it from the
        whitespace-only case."""
        audio_file = tmp_path / "silence.wav"
        audio_file.write_bytes(b"RIFF\x00\x00\x00\x00WAVEstub")

        backend = MoonshineLocalBackend()
        _prime_backend(backend, _fake_transcript())  # zero lines
        monkeypatch.setattr(
            "voxera.voice.moonshine_backend.load_wav_file",
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
        # Diagnostic must name the zero-line path, the audio duration
        # and the sample rate so operators can triage without guessing.
        assert "no transcript lines" in (resp.error or "")
        assert "1000 ms" in (resp.error or "")
        assert "16000 Hz" in (resp.error or "")

    def test_short_audio_hint_appears_under_800ms(self, tmp_path, monkeypatch) -> None:
        """When the decoded audio is under ~800 ms the empty-line
        diagnostic must include the 'too short' hint so the operator
        can see their clip was below Moonshine's reliable floor."""
        audio_file = tmp_path / "short.wav"
        audio_file.write_bytes(b"RIFF\x00\x00\x00\x00WAVEstub")

        backend = MoonshineLocalBackend()
        _prime_backend(backend, _fake_transcript())
        # 4000 samples @ 16 kHz = 250 ms
        monkeypatch.setattr(
            "voxera.voice.moonshine_backend.load_wav_file",
            MagicMock(return_value=([0.0] * 4000, 16000)),
            raising=False,
        )

        req = build_stt_request(
            input_source="audio_file",
            request_id="tiny",
            audio_path=str(audio_file),
        )
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True):
            resp = transcribe_stt_request(req, adapter=backend)
        assert resp.error_class == STT_ERROR_EMPTY_AUDIO
        assert "under 800 ms" in (resp.error or "")
        assert "hold" in (resp.error or "").lower() or "longer" in (resp.error or "").lower()

    def test_whitespace_only_lines_map_to_empty_audio(self, tmp_path, monkeypatch) -> None:
        """Moonshine emitting line objects with only-whitespace text
        → ``empty_audio`` with a diagnostic that calls out the line
        count so operators can see Moonshine *did* emit lines but all
        were blank."""
        audio_file = tmp_path / "ws.wav"
        audio_file.write_bytes(b"RIFF\x00\x00\x00\x00WAVEstub")

        backend = MoonshineLocalBackend()
        _prime_backend(backend, _fake_transcript("   ", "\t\n"))
        monkeypatch.setattr(
            "voxera.voice.moonshine_backend.load_wav_file",
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
        assert "2 transcript line(s) with no text" in (resp.error or "")
        # Whitespace-only branch must NOT mention "no transcript lines"
        # (that is the zero-line branch).
        assert "no transcript lines" not in (resp.error or "")

    def test_valid_transcript_bypasses_diagnostic_path(self, tmp_path, monkeypatch) -> None:
        """Regression pin: a non-empty transcript must not accidentally
        reach the empty-line diagnostic branch.  Joined text comes
        through unchanged and protocol-layer normalization folds
        whitespace."""
        audio_file = tmp_path / "ok.wav"
        audio_file.write_bytes(b"RIFF\x00\x00\x00\x00WAVEstub")

        backend = MoonshineLocalBackend()
        _prime_backend(backend, _fake_transcript("hello there"))
        monkeypatch.setattr(
            "voxera.voice.moonshine_backend.load_wav_file",
            self._fake_load(),
            raising=False,
        )

        req = build_stt_request(
            input_source="audio_file",
            request_id="ok",
            audio_path=str(audio_file),
        )
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True):
            resp = transcribe_stt_request(req, adapter=backend)
        assert resp.status == STT_STATUS_SUCCEEDED
        assert resp.transcript == "hello there"
        assert resp.error is None
        assert resp.error_class is None

    def test_keep_failed_audio_env_var_preserves_temp_wav(self, tmp_path, monkeypatch) -> None:
        """With ``VOXERA_VOICE_STT_MOONSHINE_KEEP_FAILED_AUDIO=1`` set,
        an empty-transcript failure must leave the transcoded temp WAV
        on disk and include its path in the error so operators can
        inspect the audio Moonshine actually saw.  Without the env
        var, the temp is cleaned up (previous behaviour)."""
        not_a_wav = tmp_path / "clip.webm"
        not_a_wav.write_bytes(b"\x1a\x45\xdf\xa3")

        tmp_wav = tmp_path / "preserved.wav"
        tmp_wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVEpres")

        monkeypatch.setattr(
            "voxera.voice.moonshine_backend.ensure_pcm_wav",
            lambda source: (tmp_wav, tmp_wav),
        )
        monkeypatch.setattr(
            "voxera.voice.moonshine_backend.load_wav_file",
            MagicMock(return_value=([0.0] * 16000, 16000)),
            raising=False,
        )
        monkeypatch.setenv("VOXERA_VOICE_STT_MOONSHINE_KEEP_FAILED_AUDIO", "1")

        backend = MoonshineLocalBackend()
        _prime_backend(backend, _fake_transcript())  # zero lines

        req = build_stt_request(
            input_source="audio_file",
            request_id="preserve",
            audio_path=str(not_a_wav),
        )
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True):
            resp = transcribe_stt_request(req, adapter=backend)
        assert resp.error_class == STT_ERROR_EMPTY_AUDIO
        assert str(tmp_wav) in (resp.error or "")
        # Preserved: the temp WAV must still exist after the call.
        assert tmp_wav.exists()


# -- transcription failure (mocked) -------------------------------------------


class TestMoonshineTranscriptionFailure:
    def test_transcriber_exception_returns_error(self, tmp_path, monkeypatch) -> None:
        audio_file = tmp_path / "bad.wav"
        audio_file.write_bytes(b"RIFF\x00\x00\x00\x00WAVEstub")

        backend = MoonshineLocalBackend()
        backend._transcriber = SimpleNamespace(
            transcribe_without_streaming=MagicMock(side_effect=RuntimeError("native crash"))
        )
        monkeypatch.setattr(
            "voxera.voice.moonshine_backend.load_wav_file",
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
        audio_file.write_bytes(b"RIFF\x00\x00\x00\x00WAVEstub")

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
        audio_file.write_bytes(b"RIFF\x00\x00\x00\x00WAVEstub")

        backend = MoonshineLocalBackend()
        _prime_backend(backend, _fake_transcript("Async hello"))
        monkeypatch.setattr(
            "voxera.voice.moonshine_backend.load_wav_file",
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
