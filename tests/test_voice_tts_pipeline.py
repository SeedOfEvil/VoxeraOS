"""Tests for TTS backend selection and voice output pipeline wiring.

Pins:
- ``build_tts_backend`` factory returns correct backends from flags
- ``synthesize_text`` threads through canonical TTS path
- truthful outcomes for unconfigured, disabled, unsupported, and
  successful synthesis paths
- no fake audio output or overclaimed format support
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from voxera.voice.flags import VoiceFoundationFlags
from voxera.voice.output import synthesize_text
from voxera.voice.piper_backend import PiperLocalBackend
from voxera.voice.tts_adapter import NullTTSBackend, TTSAdapterResult
from voxera.voice.tts_backend_factory import (
    TTS_BACKEND_PIPER_LOCAL,
    build_tts_backend,
)
from voxera.voice.tts_protocol import (
    TTS_ERROR_BACKEND_MISSING,
    TTS_STATUS_FAILED,
    TTS_STATUS_SUCCEEDED,
    TTS_STATUS_UNAVAILABLE,
    TTSResponse,
)

# -- helpers -----------------------------------------------------------------


def _make_flags(
    *,
    foundation: bool = True,
    voice_input: bool = False,
    voice_output: bool = True,
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


class _CapturingBackend:
    """Stub backend that captures the request and returns a configurable result.

    Used across multiple test classes to verify request pass-through
    without repeating a local class definition each time.
    """

    def __init__(self, audio_path: str, *, name: str = "capturing") -> None:
        self._audio_path = audio_path
        self._name = name
        self.last_request: object | None = None

    @property
    def backend_name(self) -> str:
        return self._name

    def supports_voice(self, voice_id: str) -> bool:
        return True

    def synthesize(self, request):  # noqa: ANN001, ANN201
        self.last_request = request
        return TTSAdapterResult(audio_path=self._audio_path)


# =============================================================================
# Section 1: build_tts_backend factory
# =============================================================================


class TestBuildTTSBackendNullCases:
    """Factory returns NullTTSBackend when no usable backend is configured."""

    def test_no_backend_configured_returns_null(self) -> None:
        flags = _make_flags(tts_backend=None)
        backend = build_tts_backend(flags)
        assert isinstance(backend, NullTTSBackend)
        assert backend.backend_name == "null"

    def test_empty_backend_string_returns_null(self) -> None:
        flags = _make_flags(tts_backend="")
        backend = build_tts_backend(flags)
        assert isinstance(backend, NullTTSBackend)

    def test_whitespace_backend_string_returns_null(self) -> None:
        flags = _make_flags(tts_backend="   ")
        backend = build_tts_backend(flags)
        assert isinstance(backend, NullTTSBackend)

    def test_unrecognized_backend_returns_null(self) -> None:
        flags = _make_flags(tts_backend="google_cloud_tts")
        backend = build_tts_backend(flags)
        assert isinstance(backend, NullTTSBackend)

    def test_voice_foundation_disabled_returns_null(self) -> None:
        flags = _make_flags(foundation=False, tts_backend="piper_local")
        backend = build_tts_backend(flags)
        assert isinstance(backend, NullTTSBackend)

    def test_voice_output_disabled_returns_null(self) -> None:
        flags = _make_flags(voice_output=False, tts_backend="piper_local")
        backend = build_tts_backend(flags)
        assert isinstance(backend, NullTTSBackend)


class TestBuildTTSBackendPiper:
    """Factory returns PiperLocalBackend when configured."""

    def test_piper_local_returns_piper_backend(self) -> None:
        flags = _make_flags(tts_backend="piper_local")
        backend = build_tts_backend(flags)
        assert isinstance(backend, PiperLocalBackend)
        assert backend.backend_name == "piper_local"

    def test_piper_local_case_insensitive(self) -> None:
        flags = _make_flags(tts_backend="PIPER_LOCAL")
        backend = build_tts_backend(flags)
        assert isinstance(backend, PiperLocalBackend)

    def test_piper_local_strips_whitespace(self) -> None:
        flags = _make_flags(tts_backend="  piper_local  ")
        backend = build_tts_backend(flags)
        assert isinstance(backend, PiperLocalBackend)


class TestBuildTTSBackendErrorMessages:
    """Factory-produced NullTTSBackend carries truthful error messages."""

    def test_unrecognized_backend_error_mentions_name(self) -> None:
        """Operators see which backend identifier was rejected."""
        flags = _make_flags(tts_backend="google_cloud_tts")
        backend = build_tts_backend(flags)
        from voxera.voice.tts_protocol import build_tts_request

        req = build_tts_request(text="hello", request_id="err-msg")
        result = backend.synthesize(req)
        assert result.error is not None
        assert "google_cloud_tts" in result.error

    def test_unconfigured_backend_error_is_generic(self) -> None:
        """When no backend is configured, error says 'No TTS backend is configured'."""
        flags = _make_flags(tts_backend=None)
        backend = build_tts_backend(flags)
        from voxera.voice.tts_protocol import build_tts_request

        req = build_tts_request(text="hello", request_id="err-gen")
        result = backend.synthesize(req)
        assert result.error is not None
        assert "configured" in result.error.lower()
        assert "not recognized" not in result.error.lower()

    def test_null_backend_default_reason_preserved(self) -> None:
        """NullTTSBackend() with no args preserves the original message."""
        backend = NullTTSBackend()
        from voxera.voice.tts_protocol import build_tts_request

        req = build_tts_request(text="hello", request_id="default")
        result = backend.synthesize(req)
        assert result.error == "No TTS backend is configured"


class TestBuildTTSBackendConstant:
    """The canonical backend identifier constant is correct."""

    def test_piper_local_constant(self) -> None:
        assert TTS_BACKEND_PIPER_LOCAL == "piper_local"


# =============================================================================
# Section 2: synthesize_text pipeline wiring
# =============================================================================


class TestSynthesizeTextUnconfigured:
    """Unconfigured backend returns truthful unavailable response."""

    def test_no_backend_returns_unavailable(self) -> None:
        flags = _make_flags(tts_backend=None)
        resp = synthesize_text(text="hello world", flags=flags)
        assert isinstance(resp, TTSResponse)
        assert resp.status == TTS_STATUS_UNAVAILABLE
        assert resp.error_class == TTS_ERROR_BACKEND_MISSING
        assert resp.audio_path is None

    def test_disabled_foundation_returns_unavailable(self) -> None:
        flags = _make_flags(foundation=False, tts_backend="piper_local")
        resp = synthesize_text(text="hello world", flags=flags)
        assert resp.status == TTS_STATUS_UNAVAILABLE
        assert resp.error_class == TTS_ERROR_BACKEND_MISSING
        assert resp.audio_path is None

    def test_disabled_output_returns_unavailable(self) -> None:
        flags = _make_flags(voice_output=False, tts_backend="piper_local")
        resp = synthesize_text(text="hello world", flags=flags)
        assert resp.status == TTS_STATUS_UNAVAILABLE
        assert resp.audio_path is None

    def test_unknown_backend_returns_unavailable(self) -> None:
        flags = _make_flags(tts_backend="nonexistent_backend")
        resp = synthesize_text(text="hello world", flags=flags)
        assert resp.status == TTS_STATUS_UNAVAILABLE
        assert resp.error_class == TTS_ERROR_BACKEND_MISSING
        assert resp.audio_path is None


class TestSynthesizeTextSuccess:
    """Successful synthesis through the full pipeline."""

    def test_success_returns_audio_path(self, tmp_path) -> None:
        audio_file = tmp_path / "output.wav"
        audio_file.write_bytes(b"fake-audio-data")

        flags = _make_flags(tts_backend="piper_local")

        class StubPiperBackend:
            @property
            def backend_name(self) -> str:
                return "piper_local"

            def supports_voice(self, voice_id: str) -> bool:
                return True

            def synthesize(self, request):
                return TTSAdapterResult(
                    audio_path=str(audio_file),
                    audio_duration_ms=1500,
                    inference_ms=100,
                )

        with patch(
            "voxera.voice.tts_backend_factory.PiperLocalBackend",
            return_value=StubPiperBackend(),
        ):
            resp = synthesize_text(
                text="Hello world",
                flags=flags,
                voice_id="default",
                language="en",
                session_id="test-session",
            )

        assert resp.status == TTS_STATUS_SUCCEEDED
        assert resp.audio_path == str(audio_file)
        assert resp.backend == "piper_local"
        assert resp.error is None
        assert resp.error_class is None
        assert isinstance(resp, TTSResponse)

    def test_request_carries_text(self, tmp_path) -> None:
        """The TTSRequest built by synthesize_text carries the text."""
        audio_file = tmp_path / "output.wav"
        audio_file.write_bytes(b"fake-audio")

        stub = _CapturingBackend(str(audio_file), name="piper_local")

        with patch(
            "voxera.voice.tts_backend_factory.PiperLocalBackend",
            return_value=stub,
        ):
            resp = synthesize_text(text="Hello world", flags=_make_flags(tts_backend="piper_local"))

        assert resp.status == TTS_STATUS_SUCCEEDED
        assert stub.last_request is not None
        assert stub.last_request.text == "Hello world"


class TestSynthesizeTextFailurePaths:
    """Failure paths return truthful responses, never raise."""

    def test_missing_dependency_returns_unavailable(self) -> None:
        flags = _make_flags(tts_backend="piper_local")

        with patch("voxera.voice.piper_backend._PIPER_AVAILABLE", False):
            resp = synthesize_text(text="hello world", flags=flags)

        assert resp.status == TTS_STATUS_UNAVAILABLE
        assert resp.error_class == TTS_ERROR_BACKEND_MISSING

    def test_never_raises_on_backend_crash(self) -> None:
        """Pipeline never raises — backend crashes are caught fail-soft."""
        flags = _make_flags(tts_backend="piper_local")

        class CrashingBackend:
            @property
            def backend_name(self) -> str:
                return "piper_local"

            def supports_voice(self, voice_id: str) -> bool:
                return True

            def synthesize(self, request):
                raise RuntimeError("kaboom")

        with patch(
            "voxera.voice.tts_backend_factory.PiperLocalBackend",
            return_value=CrashingBackend(),
        ):
            resp = synthesize_text(text="hello world", flags=flags)

        assert resp.status == TTS_STATUS_FAILED
        assert resp.audio_path is None


# =============================================================================
# Section 3: pipeline uses canonical TTS request/adapter path
# =============================================================================


class TestPipelineUsesCanonicalPath:
    """The pipeline correctly threads through build_tts_request + synthesize_tts_request."""

    def test_response_has_schema_version(self) -> None:
        flags = _make_flags(tts_backend=None)
        resp = synthesize_text(text="hello", flags=flags)
        assert resp.schema_version == 1

    def test_response_has_request_id(self) -> None:
        flags = _make_flags(tts_backend=None)
        resp = synthesize_text(text="hello", flags=flags)
        assert isinstance(resp.request_id, str)
        assert len(resp.request_id) > 0

    def test_voice_id_passes_through(self, tmp_path) -> None:
        audio_file = tmp_path / "output.wav"
        audio_file.write_bytes(b"fake-audio")
        stub = _CapturingBackend(str(audio_file))
        flags = _make_flags(tts_backend="piper_local")

        with patch(
            "voxera.voice.tts_backend_factory.PiperLocalBackend",
            return_value=stub,
        ):
            synthesize_text(text="hello", flags=flags, voice_id="en-female-1")

        assert stub.last_request is not None
        assert stub.last_request.voice_id == "en-female-1"

    def test_language_passes_through(self, tmp_path) -> None:
        audio_file = tmp_path / "output.wav"
        audio_file.write_bytes(b"fake-audio")
        stub = _CapturingBackend(str(audio_file))
        flags = _make_flags(tts_backend="piper_local")

        with patch(
            "voxera.voice.tts_backend_factory.PiperLocalBackend",
            return_value=stub,
        ):
            synthesize_text(text="bonjour", flags=flags, language="fr")

        assert stub.last_request is not None
        assert stub.last_request.language == "fr"

    def test_session_id_passes_through(self, tmp_path) -> None:
        audio_file = tmp_path / "output.wav"
        audio_file.write_bytes(b"fake-audio")
        stub = _CapturingBackend(str(audio_file))
        flags = _make_flags(tts_backend="piper_local")

        with patch(
            "voxera.voice.tts_backend_factory.PiperLocalBackend",
            return_value=stub,
        ):
            synthesize_text(text="hello", flags=flags, session_id="sess-abc")

        assert stub.last_request is not None
        assert stub.last_request.session_id == "sess-abc"

    def test_speed_passes_through(self, tmp_path) -> None:
        audio_file = tmp_path / "output.wav"
        audio_file.write_bytes(b"fake-audio")
        stub = _CapturingBackend(str(audio_file))
        flags = _make_flags(tts_backend="piper_local")

        with patch(
            "voxera.voice.tts_backend_factory.PiperLocalBackend",
            return_value=stub,
        ):
            synthesize_text(text="hello", flags=flags, speed=1.5)

        assert stub.last_request is not None
        assert stub.last_request.speed == 1.5

    def test_output_format_passes_through(self, tmp_path) -> None:
        audio_file = tmp_path / "output.ogg"
        audio_file.write_bytes(b"fake-audio")
        stub = _CapturingBackend(str(audio_file))
        flags = _make_flags(tts_backend="piper_local")

        with patch(
            "voxera.voice.tts_backend_factory.PiperLocalBackend",
            return_value=stub,
        ):
            synthesize_text(text="hello", flags=flags, output_format="ogg")

        assert stub.last_request is not None
        assert stub.last_request.output_format == "ogg"

    def test_empty_text_raises_value_error(self) -> None:
        """Empty text is a request validation error, not a synthesis failure."""
        flags = _make_flags(tts_backend="piper_local")
        with pytest.raises(ValueError, match="non-empty"):
            synthesize_text(text="", flags=flags)


# =============================================================================
# Section 4: backend name propagation
# =============================================================================


class TestBackendNamePropagation:
    """Backend name is propagated in responses."""

    def test_success_propagates_backend_name(self, tmp_path) -> None:
        audio_file = tmp_path / "output.wav"
        audio_file.write_bytes(b"fake-audio")

        class NamedBackend:
            @property
            def backend_name(self) -> str:
                return "my_custom_backend"

            def supports_voice(self, voice_id: str) -> bool:
                return True

            def synthesize(self, request):
                return TTSAdapterResult(audio_path=str(audio_file))

        flags = _make_flags(tts_backend=None)
        resp = synthesize_text(text="hello", flags=flags, backend=NamedBackend())
        assert resp.backend == "my_custom_backend"

    def test_failure_propagates_backend_name(self) -> None:
        class FailingBackend:
            @property
            def backend_name(self) -> str:
                return "failing_backend"

            def supports_voice(self, voice_id: str) -> bool:
                return True

            def synthesize(self, request):
                raise RuntimeError("fail")

        flags = _make_flags(tts_backend=None)
        resp = synthesize_text(text="hello", flags=flags, backend=FailingBackend())
        assert resp.backend == "failing_backend"


# =============================================================================
# Section 5: export surface
# =============================================================================


class TestExportSurface:
    """New public symbols are exported from the voice package."""

    def test_build_tts_backend_exported(self) -> None:
        from voxera.voice import build_tts_backend as exported

        assert exported is build_tts_backend

    def test_synthesize_text_exported(self) -> None:
        from voxera.voice import synthesize_text as exported

        assert exported is synthesize_text

    def test_synthesize_text_async_exported(self) -> None:
        from voxera.voice import synthesize_text_async

        assert callable(synthesize_text_async)


# =============================================================================
# Section 6: config-driven integration
# =============================================================================


class TestConfigDrivenIntegration:
    """Backend selection works end-to-end through flags loaded from config."""

    def test_flags_from_config_select_piper(self, tmp_path) -> None:
        """Flags with voice_tts_backend='piper_local' produce PiperLocalBackend."""
        import json

        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "enable_voice_foundation": True,
                    "enable_voice_output": True,
                    "voice_tts_backend": "piper_local",
                }
            ),
            encoding="utf-8",
        )

        from voxera.voice.flags import load_voice_foundation_flags

        flags = load_voice_foundation_flags(config_path=config_path, environ={})
        backend = build_tts_backend(flags)
        assert isinstance(backend, PiperLocalBackend)

    def test_flags_from_config_no_backend(self, tmp_path) -> None:
        """Flags with no voice_tts_backend produce NullTTSBackend."""
        import json

        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "enable_voice_foundation": True,
                    "enable_voice_output": True,
                }
            ),
            encoding="utf-8",
        )

        from voxera.voice.flags import load_voice_foundation_flags

        flags = load_voice_foundation_flags(config_path=config_path, environ={})
        backend = build_tts_backend(flags)
        assert isinstance(backend, NullTTSBackend)

    def test_env_var_selects_piper(self, tmp_path) -> None:
        """VOXERA_VOICE_TTS_BACKEND env var selects PiperLocalBackend."""
        import json

        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({}), encoding="utf-8")

        from voxera.voice.flags import load_voice_foundation_flags

        env = {
            "VOXERA_ENABLE_VOICE_FOUNDATION": "1",
            "VOXERA_ENABLE_VOICE_OUTPUT": "1",
            "VOXERA_VOICE_TTS_BACKEND": "piper_local",
        }
        flags = load_voice_foundation_flags(config_path=config_path, environ=env)
        backend = build_tts_backend(flags)
        assert isinstance(backend, PiperLocalBackend)

    def test_env_var_overrides_config(self, tmp_path) -> None:
        """Env var takes precedence over config file for backend selection."""
        import json

        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "enable_voice_foundation": True,
                    "enable_voice_output": True,
                    "voice_tts_backend": "some_other_backend",
                }
            ),
            encoding="utf-8",
        )

        from voxera.voice.flags import load_voice_foundation_flags

        env = {"VOXERA_VOICE_TTS_BACKEND": "piper_local"}
        flags = load_voice_foundation_flags(config_path=config_path, environ=env)
        backend = build_tts_backend(flags)
        assert isinstance(backend, PiperLocalBackend)


# =============================================================================
# Section 7: response uniqueness
# =============================================================================


class TestResponseUniqueness:
    """Each pipeline call produces a distinct request_id."""

    def test_distinct_request_ids(self) -> None:
        flags = _make_flags(tts_backend=None)
        r1 = synthesize_text(text="hello", flags=flags)
        r2 = synthesize_text(text="hello", flags=flags)
        assert r1.request_id != r2.request_id


# =============================================================================
# Section 8: pre-built backend reuse (avoids model reload)
# =============================================================================


class TestPreBuiltBackendReuse:
    """Callers can pass a pre-built backend to avoid per-call reconstruction."""

    def test_pre_built_backend_is_used(self, tmp_path) -> None:
        """When backend= is supplied, the factory is not called."""
        audio_file = tmp_path / "output.wav"
        audio_file.write_bytes(b"fake-audio")

        captured_request = None

        class TrackingBackend:
            @property
            def backend_name(self) -> str:
                return "tracking"

            def supports_voice(self, voice_id: str) -> bool:
                return True

            def synthesize(self, request):
                nonlocal captured_request
                captured_request = request
                return TTSAdapterResult(audio_path=str(audio_file))

        # flags say no backend — but passing backend= directly should bypass factory
        flags = _make_flags(tts_backend=None)
        resp = synthesize_text(
            text="hello from pre-built",
            flags=flags,
            backend=TrackingBackend(),
        )
        assert resp.status == TTS_STATUS_SUCCEEDED
        assert resp.audio_path == str(audio_file)
        assert resp.backend == "tracking"
        assert captured_request is not None

    def test_pre_built_backend_reuses_model(self, tmp_path) -> None:
        """Same backend instance across calls reuses the loaded model."""
        audio_file = tmp_path / "output.wav"
        audio_file.write_bytes(b"fake-audio")

        call_count = 0

        class CountingBackend:
            @property
            def backend_name(self) -> str:
                return "counting"

            def supports_voice(self, voice_id: str) -> bool:
                return True

            def synthesize(self, request):
                nonlocal call_count
                call_count += 1
                return TTSAdapterResult(audio_path=str(audio_file))

        flags = _make_flags(tts_backend="piper_local")
        reusable = CountingBackend()

        r1 = synthesize_text(text="call one", flags=flags, backend=reusable)
        r2 = synthesize_text(text="call two", flags=flags, backend=reusable)
        assert r1.status == TTS_STATUS_SUCCEEDED
        assert r2.status == TTS_STATUS_SUCCEEDED
        assert call_count == 2
        # Same instance was used — no reconstruction
        assert r1.backend == r2.backend == "counting"

    def test_none_backend_falls_through_to_factory(self) -> None:
        """backend=None (the default) uses the factory."""
        flags = _make_flags(tts_backend=None)
        resp = synthesize_text(text="hello", flags=flags, backend=None)
        assert resp.status == TTS_STATUS_UNAVAILABLE
        assert resp.error_class == TTS_ERROR_BACKEND_MISSING


# =============================================================================
# Section 9: async entry point
# =============================================================================


class TestSynthesizeTextAsync:
    """Async entry point preserves sync semantics in a thread."""

    @pytest.mark.asyncio
    async def test_async_success(self, tmp_path) -> None:
        from voxera.voice.output import synthesize_text_async

        audio_file = tmp_path / "output.wav"
        audio_file.write_bytes(b"fake-audio")

        class StubBackend:
            @property
            def backend_name(self) -> str:
                return "async-stub"

            def supports_voice(self, voice_id: str) -> bool:
                return True

            def synthesize(self, request):
                return TTSAdapterResult(audio_path=str(audio_file))

        flags = _make_flags(tts_backend="piper_local")
        resp = await synthesize_text_async(
            text="async hello",
            flags=flags,
            backend=StubBackend(),
        )
        assert resp.status == TTS_STATUS_SUCCEEDED
        assert resp.audio_path == str(audio_file)
        assert resp.backend == "async-stub"
        assert isinstance(resp, TTSResponse)

    @pytest.mark.asyncio
    async def test_async_unavailable_when_unconfigured(self) -> None:
        from voxera.voice.output import synthesize_text_async

        flags = _make_flags(tts_backend=None)
        resp = await synthesize_text_async(text="hello", flags=flags)
        assert resp.status == TTS_STATUS_UNAVAILABLE
        assert resp.error_class == TTS_ERROR_BACKEND_MISSING
        assert resp.audio_path is None

    @pytest.mark.asyncio
    async def test_async_fail_soft_on_crash(self) -> None:
        from voxera.voice.output import synthesize_text_async

        class CrashBackend:
            @property
            def backend_name(self) -> str:
                return "crash"

            def supports_voice(self, voice_id: str) -> bool:
                return True

            def synthesize(self, request):
                raise RuntimeError("boom")

        flags = _make_flags(tts_backend="piper_local")
        resp = await synthesize_text_async(
            text="hello",
            flags=flags,
            backend=CrashBackend(),
        )
        assert resp.status == TTS_STATUS_FAILED
        assert resp.audio_path is None

    @pytest.mark.asyncio
    async def test_async_returns_tts_response(self) -> None:
        from voxera.voice.output import synthesize_text_async

        flags = _make_flags(tts_backend=None)
        resp = await synthesize_text_async(text="hello", flags=flags)
        assert isinstance(resp, TTSResponse)

    @pytest.mark.asyncio
    async def test_async_with_pre_built_backend(self, tmp_path) -> None:
        """Async variant also accepts a pre-built backend."""
        from voxera.voice.output import synthesize_text_async

        audio_file = tmp_path / "output.wav"
        audio_file.write_bytes(b"fake-audio")

        class ReusableBackend:
            @property
            def backend_name(self) -> str:
                return "reusable"

            def supports_voice(self, voice_id: str) -> bool:
                return True

            def synthesize(self, request):
                return TTSAdapterResult(audio_path=str(audio_file))

        flags = _make_flags(tts_backend=None)
        reusable = ReusableBackend()
        resp = await synthesize_text_async(
            text="reused",
            flags=flags,
            backend=reusable,
        )
        assert resp.status == TTS_STATUS_SUCCEEDED
        assert resp.audio_path == str(audio_file)

    def test_async_exported(self) -> None:
        from voxera.voice import synthesize_text_async

        assert callable(synthesize_text_async)


# =============================================================================
# Section 10: no fake synthesis — fail-soft invariants
# =============================================================================


class TestNoFakeSynthesis:
    """Pipeline never produces fake success — all invariants hold."""

    def test_no_audio_path_means_no_success(self) -> None:
        """A backend that returns no audio_path does not produce a succeeded response."""

        class EmptyBackend:
            @property
            def backend_name(self) -> str:
                return "empty"

            def supports_voice(self, voice_id: str) -> bool:
                return True

            def synthesize(self, request):
                return TTSAdapterResult(audio_path=None)

        flags = _make_flags(tts_backend=None)
        resp = synthesize_text(text="hello", flags=flags, backend=EmptyBackend())
        assert resp.status == TTS_STATUS_FAILED
        assert resp.audio_path is None

    def test_whitespace_audio_path_means_no_success(self) -> None:
        """A backend that returns whitespace-only audio_path does not produce a succeeded response."""

        class WhitespaceBackend:
            @property
            def backend_name(self) -> str:
                return "whitespace"

            def supports_voice(self, voice_id: str) -> bool:
                return True

            def synthesize(self, request):
                return TTSAdapterResult(audio_path="   ")

        flags = _make_flags(tts_backend=None)
        resp = synthesize_text(text="hello", flags=flags, backend=WhitespaceBackend())
        assert resp.status == TTS_STATUS_FAILED
        assert resp.audio_path is None


# =============================================================================
# Section 11: speech-only normalization at the canonical TTS entry point
# =============================================================================


class TestSpeechNormalizationAtEntryPoint:
    """The canonical TTS path hands the adapter speech-normalized text.

    Canonical display / storage text lives upstream of this function;
    these tests pin that the request text the backend sees is the
    speech-only copy, while the caller's own string reference is
    never mutated.
    """

    def test_request_text_is_normalized(self, tmp_path) -> None:
        audio_file = tmp_path / "output.wav"
        audio_file.write_bytes(b"fake-audio")
        stub = _CapturingBackend(str(audio_file))
        flags = _make_flags(tts_backend=None)

        source = "## Run summary\n- first\n- second\nRun `make test` now."
        resp = synthesize_text(text=source, flags=flags, backend=stub)
        assert resp.status == TTS_STATUS_SUCCEEDED
        assert stub.last_request is not None
        req_text = stub.last_request.text
        # Formatting syntax is gone from what the backend sees.
        assert "##" not in req_text
        assert "`" not in req_text
        assert "- first" not in req_text
        # Content words survive.
        assert "Run summary" in req_text
        assert "first" in req_text
        assert "second" in req_text
        assert "make test" in req_text

    def test_caller_text_is_not_mutated(self, tmp_path) -> None:
        """The caller's canonical text reference is unchanged after TTS."""
        audio_file = tmp_path / "output.wav"
        audio_file.write_bytes(b"fake-audio")
        stub = _CapturingBackend(str(audio_file))
        flags = _make_flags(tts_backend=None)

        source = "## Heading\n- bullet with **bold**"
        before = source
        synthesize_text(text=source, flags=flags, backend=stub)
        # Python strings are immutable, but we pin the boundary: the
        # caller's local reference still equals the original -- the
        # normalization never reassigned it, nor did the helper reach
        # back and rewrite it.
        assert source == before

    def test_plain_text_passes_through_unchanged(self, tmp_path) -> None:
        """Plain text with no formatting flows through byte-identical."""
        audio_file = tmp_path / "output.wav"
        audio_file.write_bytes(b"fake-audio")
        stub = _CapturingBackend(str(audio_file))
        flags = _make_flags(tts_backend=None)

        source = "Hello world, this is a plain reply."
        synthesize_text(text=source, flags=flags, backend=stub)
        assert stub.last_request is not None
        assert stub.last_request.text == source

    def test_tts_failure_does_not_affect_caller_text(self, tmp_path) -> None:
        """TTS crash is fail-soft and leaves canonical text authoritative.

        The canonical text lives in the caller's scope; this test
        confirms that a synthesis failure path does not raise out of
        ``synthesize_text`` and does not touch the caller's string.
        """
        flags = _make_flags(tts_backend=None)

        class CrashBackend:
            @property
            def backend_name(self) -> str:
                return "crash"

            def supports_voice(self, voice_id: str) -> bool:
                return True

            def synthesize(self, request):
                raise RuntimeError("boom")

        source = "## Heading\nbody text"
        before = source
        resp = synthesize_text(text=source, flags=flags, backend=CrashBackend())
        assert resp.status == TTS_STATUS_FAILED
        assert resp.audio_path is None
        # Caller's canonical text remains authoritative / unchanged.
        assert source == before

    def test_normalized_empty_still_raises_value_error(self) -> None:
        """Empty-after-strip input still raises -- honour the contract."""
        flags = _make_flags(tts_backend="piper_local")
        with pytest.raises(ValueError, match="non-empty"):
            synthesize_text(text="   \n\t  ", flags=flags)
