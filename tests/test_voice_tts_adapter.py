"""Tests for the TTS backend adapter boundary and fail-soft synthesis path.

Pins the adapter protocol contract, NullTTSBackend behavior, and the
``synthesize_tts_request`` entry point across all fail-soft paths:
no adapter, unavailable backend, unsupported voice/format, backend exception,
adapter-reported error, missing audio artifact, and successful synthesis.
"""

from __future__ import annotations

import pytest

from voxera.voice.tts_adapter import (
    NullTTSBackend,
    TTSAdapterResult,
    TTSBackend,
    TTSBackendUnsupportedError,
    synthesize_tts_request,
)
from voxera.voice.tts_protocol import (
    TTS_ERROR_BACKEND_ERROR,
    TTS_ERROR_BACKEND_MISSING,
    TTS_ERROR_DISABLED,
    TTS_ERROR_UNSUPPORTED_FORMAT,
    TTS_PROTOCOL_SCHEMA_VERSION,
    TTS_STATUS_FAILED,
    TTS_STATUS_SUCCEEDED,
    TTS_STATUS_UNAVAILABLE,
    TTS_STATUS_UNSUPPORTED,
    TTSRequest,
    TTSResponse,
    build_tts_request,
)

# -- test helpers: concrete adapters for testing -----------------------------


class StubSuccessBackend:
    """Adapter that always succeeds with a fixed audio path."""

    @property
    def backend_name(self) -> str:
        return "stub-success"

    def supports_voice(self, voice_id: str) -> bool:
        return True

    def synthesize(self, request: TTSRequest) -> TTSAdapterResult:
        return TTSAdapterResult(
            audio_path="/tmp/output.wav",
            audio_duration_ms=3500,
            inference_ms=150,
        )


class StubUnsupportedBackend:
    """Adapter that rejects all requests as unsupported."""

    @property
    def backend_name(self) -> str:
        return "stub-unsupported"

    def supports_voice(self, voice_id: str) -> bool:
        return False

    def synthesize(self, request: TTSRequest) -> TTSAdapterResult:
        raise TTSBackendUnsupportedError(
            f"Voice {request.voice_id!r} not supported by this backend"
        )


class StubCrashingBackend:
    """Adapter that always raises an unexpected exception."""

    @property
    def backend_name(self) -> str:
        return "stub-crash"

    def supports_voice(self, voice_id: str) -> bool:
        return True

    def synthesize(self, request: TTSRequest) -> TTSAdapterResult:
        raise RuntimeError("Unexpected segfault in native library")


class StubErrorResultBackend:
    """Adapter that returns a result with an error (not an exception)."""

    @property
    def backend_name(self) -> str:
        return "stub-error-result"

    def supports_voice(self, voice_id: str) -> bool:
        return True

    def synthesize(self, request: TTSRequest) -> TTSAdapterResult:
        return TTSAdapterResult(
            audio_path=None,
            error="Backend rejected output format",
            error_class="custom_format_error",
        )


class StubNoAudioPathBackend:
    """Adapter that returns a result with no audio_path (None)."""

    @property
    def backend_name(self) -> str:
        return "stub-no-audio"

    def supports_voice(self, voice_id: str) -> bool:
        return True

    def synthesize(self, request: TTSRequest) -> TTSAdapterResult:
        return TTSAdapterResult(audio_path=None)


class StubEmptyAudioPathBackend:
    """Adapter that returns a result with whitespace-only audio_path."""

    @property
    def backend_name(self) -> str:
        return "stub-empty-audio"

    def supports_voice(self, voice_id: str) -> bool:
        return True

    def synthesize(self, request: TTSRequest) -> TTSAdapterResult:
        return TTSAdapterResult(audio_path="   \t  ")


# -- TTSAdapterResult -------------------------------------------------------


class TestTTSAdapterResult:
    def test_adapter_result_is_frozen(self) -> None:
        result = TTSAdapterResult(audio_path="/tmp/out.wav")
        with pytest.raises(AttributeError):
            result.audio_path = "/other"  # type: ignore[misc]

    def test_adapter_result_defaults(self) -> None:
        result = TTSAdapterResult(audio_path="/tmp/out.wav")
        assert result.audio_path == "/tmp/out.wav"
        assert result.audio_duration_ms is None
        assert result.inference_ms is None
        assert result.error is None
        assert result.error_class is None

    def test_adapter_result_all_fields(self) -> None:
        result = TTSAdapterResult(
            audio_path="/tmp/out.wav",
            audio_duration_ms=3500,
            inference_ms=150,
            error="warning",
            error_class="custom",
        )
        assert result.audio_path == "/tmp/out.wav"
        assert result.audio_duration_ms == 3500
        assert result.inference_ms == 150
        assert result.error == "warning"
        assert result.error_class == "custom"


# -- NullTTSBackend ----------------------------------------------------------


class TestNullTTSBackend:
    def test_backend_name(self) -> None:
        backend = NullTTSBackend()
        assert backend.backend_name == "null"

    def test_synthesize_returns_unavailable_result(self) -> None:
        backend = NullTTSBackend()
        req = build_tts_request(text="Hello world", request_id="null-test")
        result = backend.synthesize(req)
        assert result.audio_path is None
        assert result.error is not None
        assert result.error_class == TTS_ERROR_BACKEND_MISSING

    def test_custom_reason(self) -> None:
        backend = NullTTSBackend(reason="TTS backend 'unknown' is not recognized")
        req = build_tts_request(text="Hello", request_id="custom-reason")
        result = backend.synthesize(req)
        assert "not recognized" in (result.error or "")
        assert result.error_class == TTS_ERROR_BACKEND_MISSING

    def test_satisfies_protocol(self) -> None:
        """NullTTSBackend structurally satisfies the TTSBackend protocol."""
        backend: TTSBackend = NullTTSBackend()
        assert backend.backend_name == "null"


# -- TTSBackend protocol conformance -----------------------------------------


class TestTTSBackendProtocol:
    def test_stub_success_satisfies_protocol(self) -> None:
        backend: TTSBackend = StubSuccessBackend()
        assert backend.backend_name == "stub-success"

    def test_stub_unsupported_satisfies_protocol(self) -> None:
        backend: TTSBackend = StubUnsupportedBackend()
        assert backend.backend_name == "stub-unsupported"

    def test_stub_crashing_satisfies_protocol(self) -> None:
        backend: TTSBackend = StubCrashingBackend()
        assert backend.backend_name == "stub-crash"


# -- synthesize_tts_request: no adapter -------------------------------------


class TestSynthesizeNoAdapter:
    def test_none_adapter_returns_unavailable(self) -> None:
        req = build_tts_request(text="Hello world", request_id="no-adapter")
        resp = synthesize_tts_request(req, adapter=None)
        assert resp.status == TTS_STATUS_UNAVAILABLE
        assert resp.error_class == TTS_ERROR_BACKEND_MISSING
        assert resp.request_id == "no-adapter"
        assert resp.audio_path is None
        assert resp.schema_version == TTS_PROTOCOL_SCHEMA_VERSION

    def test_default_adapter_is_none(self) -> None:
        req = build_tts_request(text="Hello world", request_id="default-none")
        resp = synthesize_tts_request(req)
        assert resp.status == TTS_STATUS_UNAVAILABLE
        assert resp.error_class == TTS_ERROR_BACKEND_MISSING


# -- synthesize_tts_request: NullTTSBackend ----------------------------------


class TestSynthesizeNullBackend:
    def test_null_backend_returns_unavailable(self) -> None:
        """NullTTSBackend signals an availability problem, not a runtime failure."""
        req = build_tts_request(text="Hello world", request_id="null-be")
        resp = synthesize_tts_request(req, adapter=NullTTSBackend())
        assert resp.status == TTS_STATUS_UNAVAILABLE
        assert resp.error_class == TTS_ERROR_BACKEND_MISSING
        assert resp.backend == "null"
        assert resp.audio_path is None
        assert resp.request_id == "null-be"


# -- synthesize_tts_request: successful adapter ------------------------------


class TestSynthesizeSuccess:
    def test_success_returns_audio_path(self) -> None:
        req = build_tts_request(text="Hello world", request_id="ok-1")
        resp = synthesize_tts_request(req, adapter=StubSuccessBackend())
        assert resp.status == TTS_STATUS_SUCCEEDED
        assert resp.audio_path == "/tmp/output.wav"
        assert resp.audio_duration_ms == 3500
        assert resp.inference_ms == 150
        assert resp.backend == "stub-success"
        assert resp.request_id == "ok-1"
        assert resp.error is None
        assert resp.error_class is None
        assert resp.schema_version == TTS_PROTOCOL_SCHEMA_VERSION

    def test_success_has_timing(self) -> None:
        req = build_tts_request(text="Hello world", request_id="ok-timing")
        resp = synthesize_tts_request(req, adapter=StubSuccessBackend())
        assert resp.started_at_ms is not None
        assert resp.finished_at_ms is not None
        assert resp.finished_at_ms >= resp.started_at_ms


# -- synthesize_tts_request: missing audio_path ------------------------------


class TestSynthesizeMissingAudioPath:
    def test_none_audio_path_does_not_fake_success(self) -> None:
        """Backend result with no audio_path is failed, not succeeded."""
        req = build_tts_request(text="Hello world", request_id="no-audio-1")
        resp = synthesize_tts_request(req, adapter=StubNoAudioPathBackend())
        assert resp.status == TTS_STATUS_FAILED
        assert resp.audio_path is None
        assert resp.backend == "stub-no-audio"
        assert resp.error is not None
        assert resp.error_class == TTS_ERROR_BACKEND_ERROR

    def test_empty_audio_path_does_not_fake_success(self) -> None:
        """Backend result with whitespace-only audio_path is failed."""
        req = build_tts_request(text="Hello world", request_id="no-audio-2")
        resp = synthesize_tts_request(req, adapter=StubEmptyAudioPathBackend())
        assert resp.status == TTS_STATUS_FAILED
        assert resp.audio_path is None
        assert resp.backend == "stub-empty-audio"
        assert resp.error is not None
        assert resp.error_class == TTS_ERROR_BACKEND_ERROR


# -- synthesize_tts_request: unsupported voice/format -----------------------


class TestSynthesizeUnsupported:
    def test_unsupported_returns_unsupported(self) -> None:
        req = build_tts_request(text="Hello", voice_id="unknown-voice", request_id="unsup-1")
        resp = synthesize_tts_request(req, adapter=StubUnsupportedBackend())
        assert resp.status == TTS_STATUS_UNSUPPORTED
        assert resp.error_class == TTS_ERROR_UNSUPPORTED_FORMAT
        assert resp.backend == "stub-unsupported"
        assert resp.audio_path is None
        assert resp.request_id == "unsup-1"
        assert "not supported" in (resp.error or "")

    def test_unsupported_has_timing(self) -> None:
        req = build_tts_request(text="Hello", request_id="unsup-time")
        resp = synthesize_tts_request(req, adapter=StubUnsupportedBackend())
        assert resp.started_at_ms is not None
        assert resp.finished_at_ms is not None


# -- synthesize_tts_request: backend exception -------------------------------


class TestSynthesizeBackendException:
    def test_exception_returns_failed(self) -> None:
        req = build_tts_request(text="Hello", request_id="crash-1")
        resp = synthesize_tts_request(req, adapter=StubCrashingBackend())
        assert resp.status == TTS_STATUS_FAILED
        assert resp.error_class == TTS_ERROR_BACKEND_ERROR
        assert resp.backend == "stub-crash"
        assert resp.audio_path is None
        assert "segfault" in (resp.error or "").lower()
        assert resp.request_id == "crash-1"

    def test_exception_does_not_raise(self) -> None:
        """The entry point never raises — it always returns a TTSResponse."""
        req = build_tts_request(text="Hello", request_id="crash-safe")
        resp = synthesize_tts_request(req, adapter=StubCrashingBackend())
        assert resp.status == TTS_STATUS_FAILED


# -- synthesize_tts_request: adapter-reported error --------------------------


class TestSynthesizeAdapterError:
    def test_runtime_error_returns_failed(self) -> None:
        """An adapter-reported error with a runtime error_class maps to 'failed'."""
        req = build_tts_request(text="Hello", request_id="err-1")
        resp = synthesize_tts_request(req, adapter=StubErrorResultBackend())
        assert resp.status == TTS_STATUS_FAILED
        assert resp.error == "Backend rejected output format"
        assert resp.error_class == "custom_format_error"
        assert resp.backend == "stub-error-result"
        assert resp.audio_path is None

    def test_availability_error_returns_unavailable(self) -> None:
        """An adapter-reported error with an availability error_class maps to 'unavailable'."""

        class DisabledBackend:
            @property
            def backend_name(self) -> str:
                return "stub-disabled"

            def supports_voice(self, voice_id: str) -> bool:
                return False

            def synthesize(self, request: TTSRequest) -> TTSAdapterResult:
                return TTSAdapterResult(
                    audio_path=None,
                    error="TTS is disabled by policy",
                    error_class=TTS_ERROR_DISABLED,
                )

        req = build_tts_request(text="Hello", request_id="avail-1")
        resp = synthesize_tts_request(req, adapter=DisabledBackend())
        assert resp.status == TTS_STATUS_UNAVAILABLE
        assert resp.error_class == TTS_ERROR_DISABLED
        assert resp.backend == "stub-disabled"
        assert resp.audio_path is None

    def test_backend_missing_error_returns_unavailable(self) -> None:
        """backend_missing error_class is an availability problem, not a runtime failure."""
        req = build_tts_request(text="Hello", request_id="avail-2")
        resp = synthesize_tts_request(req, adapter=NullTTSBackend())
        assert resp.status == TTS_STATUS_UNAVAILABLE
        assert resp.error_class == TTS_ERROR_BACKEND_MISSING

    def test_unknown_error_class_returns_failed(self) -> None:
        """Unknown/custom error_class defaults to 'failed' — not availability."""

        class CustomErrorBackend:
            @property
            def backend_name(self) -> str:
                return "stub-custom-err"

            def supports_voice(self, voice_id: str) -> bool:
                return True

            def synthesize(self, request: TTSRequest) -> TTSAdapterResult:
                return TTSAdapterResult(
                    audio_path=None,
                    error="Something vendor-specific broke",
                    error_class="vendor_specific_error",
                )

        req = build_tts_request(text="Hello", request_id="custom-err")
        resp = synthesize_tts_request(req, adapter=CustomErrorBackend())
        assert resp.status == TTS_STATUS_FAILED
        assert resp.error_class == "vendor_specific_error"

    def test_none_error_class_returns_failed(self) -> None:
        """Adapter error with no error_class is a runtime failure."""

        class BareErrorBackend:
            @property
            def backend_name(self) -> str:
                return "stub-bare-err"

            def supports_voice(self, voice_id: str) -> bool:
                return True

            def synthesize(self, request: TTSRequest) -> TTSAdapterResult:
                return TTSAdapterResult(
                    audio_path=None,
                    error="Something went wrong",
                )

        req = build_tts_request(text="Hello", request_id="bare-err")
        resp = synthesize_tts_request(req, adapter=BareErrorBackend())
        assert resp.status == TTS_STATUS_FAILED
        assert resp.error_class is None


# -- synthesize_tts_request: unsupported with empty message ------------------


class TestSynthesizeUnsupportedEmptyMessage:
    def test_empty_unsupported_error_uses_fallback_message(self) -> None:
        """TTSBackendUnsupportedError('') gets a meaningful fallback error string."""

        class EmptyMessageUnsupportedBackend:
            @property
            def backend_name(self) -> str:
                return "stub-empty-msg"

            def supports_voice(self, voice_id: str) -> bool:
                return False

            def synthesize(self, request: TTSRequest) -> TTSAdapterResult:
                raise TTSBackendUnsupportedError("")

        req = build_tts_request(text="Hello", voice_id="test-voice", request_id="empty-msg")
        resp = synthesize_tts_request(req, adapter=EmptyMessageUnsupportedBackend())
        assert resp.status == TTS_STATUS_UNSUPPORTED
        assert resp.error_class == TTS_ERROR_UNSUPPORTED_FORMAT
        # Should have a meaningful error string, not empty
        assert resp.error is not None
        assert len(resp.error) > 0
        assert "test-voice" in resp.error


# -- backend name propagation -----------------------------------------------


class TestBackendNamePropagation:
    def test_backend_name_on_success(self) -> None:
        req = build_tts_request(text="Hello", request_id="bn-ok")
        resp = synthesize_tts_request(req, adapter=StubSuccessBackend())
        assert resp.backend == "stub-success"

    def test_backend_name_on_failure(self) -> None:
        req = build_tts_request(text="Hello", request_id="bn-fail")
        resp = synthesize_tts_request(req, adapter=StubCrashingBackend())
        assert resp.backend == "stub-crash"

    def test_backend_name_on_unsupported(self) -> None:
        req = build_tts_request(text="Hello", request_id="bn-unsup")
        resp = synthesize_tts_request(req, adapter=StubUnsupportedBackend())
        assert resp.backend == "stub-unsupported"

    def test_backend_name_on_null(self) -> None:
        req = build_tts_request(text="Hello", request_id="bn-null")
        resp = synthesize_tts_request(req, adapter=NullTTSBackend())
        assert resp.backend == "null"

    def test_no_backend_name_when_no_adapter(self) -> None:
        req = build_tts_request(text="Hello", request_id="bn-none")
        resp = synthesize_tts_request(req, adapter=None)
        assert resp.backend is None


# -- error_class passthrough ------------------------------------------------


class TestErrorClassPassthrough:
    def test_error_class_preserved_on_adapter_error(self) -> None:
        req = build_tts_request(text="Hello", request_id="ec-1")
        resp = synthesize_tts_request(req, adapter=StubErrorResultBackend())
        assert resp.error_class == "custom_format_error"

    def test_error_class_none_on_success(self) -> None:
        req = build_tts_request(text="Hello", request_id="ec-ok")
        resp = synthesize_tts_request(req, adapter=StubSuccessBackend())
        assert resp.error_class is None


# -- supports_voice() --------------------------------------------------------


class TestSupportsVoice:
    def test_null_backend_supports_nothing(self) -> None:
        backend = NullTTSBackend()
        assert backend.supports_voice("default") is False
        assert backend.supports_voice("en-US") is False
        assert backend.supports_voice("custom-speaker") is False

    def test_stub_success_supports_all(self) -> None:
        backend = StubSuccessBackend()
        assert backend.supports_voice("default") is True
        assert backend.supports_voice("custom") is True

    def test_stub_unsupported_supports_none(self) -> None:
        backend = StubUnsupportedBackend()
        assert backend.supports_voice("default") is False


# -- timing fields on TTSAdapterResult ----------------------------------------


class TestAdapterResultTimingFields:
    def test_timing_fields_default_none(self) -> None:
        result = TTSAdapterResult(audio_path="/tmp/out.wav")
        assert result.inference_ms is None
        assert result.audio_duration_ms is None

    def test_timing_fields_set(self) -> None:
        result = TTSAdapterResult(
            audio_path="/tmp/out.wav",
            inference_ms=500,
            audio_duration_ms=3000,
        )
        assert result.inference_ms == 500
        assert result.audio_duration_ms == 3000

    def test_timing_fields_pass_through_to_response(self) -> None:
        """Adapter-reported timing is carried into the TTSResponse."""

        class TimedBackend:
            @property
            def backend_name(self) -> str:
                return "stub-timed"

            def supports_voice(self, voice_id: str) -> bool:
                return True

            def synthesize(self, request: TTSRequest) -> TTSAdapterResult:
                return TTSAdapterResult(
                    audio_path="/tmp/timed.wav",
                    inference_ms=150,
                    audio_duration_ms=2500,
                )

        req = build_tts_request(text="Hello", request_id="timed-1")
        resp = synthesize_tts_request(req, adapter=TimedBackend())
        assert resp.status == TTS_STATUS_SUCCEEDED
        assert resp.inference_ms == 150
        assert resp.audio_duration_ms == 2500

    def test_timing_fields_none_when_not_reported(self) -> None:
        """Timing is None when the backend doesn't report it."""

        class NoTimingBackend:
            @property
            def backend_name(self) -> str:
                return "stub-no-timing"

            def supports_voice(self, voice_id: str) -> bool:
                return True

            def synthesize(self, request: TTSRequest) -> TTSAdapterResult:
                return TTSAdapterResult(audio_path="/tmp/out.wav")

        req = build_tts_request(text="Hello", request_id="no-timing")
        resp = synthesize_tts_request(req, adapter=NoTimingBackend())
        assert resp.status == TTS_STATUS_SUCCEEDED
        assert resp.inference_ms is None
        assert resp.audio_duration_ms is None


# -- no synthesis function raises to caller ----------------------------------


class TestNeverRaises:
    def test_sync_never_raises_on_crash(self) -> None:
        req = build_tts_request(text="Hello", request_id="nr-crash")
        resp = synthesize_tts_request(req, adapter=StubCrashingBackend())
        assert isinstance(resp, TTSResponse)
        assert resp.status == TTS_STATUS_FAILED

    def test_sync_never_raises_on_none_adapter(self) -> None:
        req = build_tts_request(text="Hello", request_id="nr-none")
        resp = synthesize_tts_request(req, adapter=None)
        assert isinstance(resp, TTSResponse)
        assert resp.status == TTS_STATUS_UNAVAILABLE

    def test_sync_never_raises_on_unsupported(self) -> None:
        req = build_tts_request(text="Hello", request_id="nr-unsup")
        resp = synthesize_tts_request(req, adapter=StubUnsupportedBackend())
        assert isinstance(resp, TTSResponse)
        assert resp.status == TTS_STATUS_UNSUPPORTED


# -- async entry point --------------------------------------------------------


class TestSynthesizeAsync:
    @pytest.mark.asyncio
    async def test_async_returns_same_as_sync(self) -> None:
        from voxera.voice.tts_adapter import synthesize_tts_request_async

        req = build_tts_request(text="Hello world", request_id="async-ok")
        resp = await synthesize_tts_request_async(req, adapter=StubSuccessBackend())
        assert resp.status == TTS_STATUS_SUCCEEDED
        assert resp.audio_path == "/tmp/output.wav"
        assert resp.backend == "stub-success"

    @pytest.mark.asyncio
    async def test_async_preserves_fail_soft(self) -> None:
        from voxera.voice.tts_adapter import synthesize_tts_request_async

        req = build_tts_request(text="Hello world", request_id="async-fail")
        resp = await synthesize_tts_request_async(req, adapter=None)
        assert resp.status == TTS_STATUS_UNAVAILABLE
        assert resp.error_class == TTS_ERROR_BACKEND_MISSING

    @pytest.mark.asyncio
    async def test_async_exception_is_fail_soft(self) -> None:
        from voxera.voice.tts_adapter import synthesize_tts_request_async

        req = build_tts_request(text="Hello world", request_id="async-crash")
        resp = await synthesize_tts_request_async(req, adapter=StubCrashingBackend())
        assert resp.status == TTS_STATUS_FAILED
        assert resp.error_class == TTS_ERROR_BACKEND_ERROR

    @pytest.mark.asyncio
    async def test_async_returns_tts_response(self) -> None:
        from voxera.voice.tts_adapter import synthesize_tts_request_async

        req = build_tts_request(text="Hello world", request_id="async-shape")
        resp = await synthesize_tts_request_async(req, adapter=StubSuccessBackend())
        assert isinstance(resp, TTSResponse)
