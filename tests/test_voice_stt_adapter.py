"""Tests for the STT backend adapter boundary and fail-soft transcription path.

Pins the adapter protocol contract, NullSTTBackend behavior, and the
``transcribe_stt_request`` entry point across all fail-soft paths:
no adapter, unavailable backend, unsupported input, backend exception,
adapter-reported error, empty transcript, and successful transcription.
"""

from __future__ import annotations

import pytest

from voxera.voice.stt_adapter import (
    NullSTTBackend,
    STTAdapterResult,
    STTBackend,
    STTBackendUnsupportedError,
    transcribe_stt_request,
)
from voxera.voice.stt_protocol import (
    STT_ERROR_BACKEND_ERROR,
    STT_ERROR_BACKEND_MISSING,
    STT_ERROR_EMPTY_AUDIO,
    STT_ERROR_UNSUPPORTED_SOURCE,
    STT_PROTOCOL_SCHEMA_VERSION,
    STT_STATUS_FAILED,
    STT_STATUS_SUCCEEDED,
    STT_STATUS_UNAVAILABLE,
    STT_STATUS_UNSUPPORTED,
    STTRequest,
    build_stt_request,
)

# -- test helpers: concrete adapters for testing -----------------------------


class StubSuccessBackend:
    """Adapter that always succeeds with a fixed transcript."""

    @property
    def backend_name(self) -> str:
        return "stub-success"

    def transcribe(self, request: STTRequest) -> STTAdapterResult:
        return STTAdapterResult(transcript="Hello world", language="en-US")


class StubUnsupportedBackend:
    """Adapter that rejects all requests as unsupported."""

    @property
    def backend_name(self) -> str:
        return "stub-unsupported"

    def transcribe(self, request: STTRequest) -> STTAdapterResult:
        raise STTBackendUnsupportedError(
            f"Source {request.input_source!r} not supported by this backend"
        )


class StubCrashingBackend:
    """Adapter that always raises an unexpected exception."""

    @property
    def backend_name(self) -> str:
        return "stub-crash"

    def transcribe(self, request: STTRequest) -> STTAdapterResult:
        raise RuntimeError("Unexpected segfault in native library")


class StubErrorResultBackend:
    """Adapter that returns a result with an error (not an exception)."""

    @property
    def backend_name(self) -> str:
        return "stub-error-result"

    def transcribe(self, request: STTRequest) -> STTAdapterResult:
        return STTAdapterResult(
            transcript=None,
            error="Backend rejected audio format",
            error_class="custom_format_error",
        )


class StubEmptyTranscriptBackend:
    """Adapter that returns an empty/whitespace-only transcript."""

    @property
    def backend_name(self) -> str:
        return "stub-empty"

    def transcribe(self, request: STTRequest) -> STTAdapterResult:
        return STTAdapterResult(transcript="   \t  \n  ")


class StubWhitespaceTranscriptBackend:
    """Adapter that returns a transcript needing normalization."""

    @property
    def backend_name(self) -> str:
        return "stub-whitespace"

    def transcribe(self, request: STTRequest) -> STTAdapterResult:
        return STTAdapterResult(transcript="  hello   world  ", language="en")


# -- STTAdapterResult -------------------------------------------------------


class TestSTTAdapterResult:
    def test_adapter_result_is_frozen(self) -> None:
        result = STTAdapterResult(transcript="hello")
        with pytest.raises(AttributeError):
            result.transcript = "bye"  # type: ignore[misc]

    def test_adapter_result_defaults(self) -> None:
        result = STTAdapterResult(transcript="hello")
        assert result.transcript == "hello"
        assert result.language is None
        assert result.error is None
        assert result.error_class is None

    def test_adapter_result_all_fields(self) -> None:
        result = STTAdapterResult(
            transcript="hello",
            language="en-US",
            error="warning",
            error_class="custom",
        )
        assert result.transcript == "hello"
        assert result.language == "en-US"
        assert result.error == "warning"
        assert result.error_class == "custom"


# -- NullSTTBackend ----------------------------------------------------------


class TestNullSTTBackend:
    def test_backend_name(self) -> None:
        backend = NullSTTBackend()
        assert backend.backend_name == "null"

    def test_transcribe_returns_unavailable_result(self) -> None:
        backend = NullSTTBackend()
        req = build_stt_request(input_source="microphone", request_id="null-test")
        result = backend.transcribe(req)
        assert result.transcript is None
        assert result.error is not None
        assert result.error_class == STT_ERROR_BACKEND_MISSING

    def test_satisfies_protocol(self) -> None:
        """NullSTTBackend structurally satisfies the STTBackend protocol."""
        backend: STTBackend = NullSTTBackend()
        assert backend.backend_name == "null"


# -- STTBackend protocol conformance -----------------------------------------


class TestSTTBackendProtocol:
    def test_stub_success_satisfies_protocol(self) -> None:
        backend: STTBackend = StubSuccessBackend()
        assert backend.backend_name == "stub-success"

    def test_stub_unsupported_satisfies_protocol(self) -> None:
        backend: STTBackend = StubUnsupportedBackend()
        assert backend.backend_name == "stub-unsupported"

    def test_stub_crashing_satisfies_protocol(self) -> None:
        backend: STTBackend = StubCrashingBackend()
        assert backend.backend_name == "stub-crash"


# -- transcribe_stt_request: no adapter -------------------------------------


class TestTranscribeNoAdapter:
    def test_none_adapter_returns_unavailable(self) -> None:
        req = build_stt_request(input_source="microphone", request_id="no-adapter")
        resp = transcribe_stt_request(req, adapter=None)
        assert resp.status == STT_STATUS_UNAVAILABLE
        assert resp.error_class == STT_ERROR_BACKEND_MISSING
        assert resp.request_id == "no-adapter"
        assert resp.transcript is None
        assert resp.schema_version == STT_PROTOCOL_SCHEMA_VERSION

    def test_default_adapter_is_none(self) -> None:
        req = build_stt_request(input_source="microphone", request_id="default-none")
        resp = transcribe_stt_request(req)
        assert resp.status == STT_STATUS_UNAVAILABLE
        assert resp.error_class == STT_ERROR_BACKEND_MISSING


# -- transcribe_stt_request: NullSTTBackend ----------------------------------


class TestTranscribeNullBackend:
    def test_null_backend_returns_failed_with_backend_missing(self) -> None:
        req = build_stt_request(input_source="microphone", request_id="null-be")
        resp = transcribe_stt_request(req, adapter=NullSTTBackend())
        assert resp.status == STT_STATUS_FAILED
        assert resp.error_class == STT_ERROR_BACKEND_MISSING
        assert resp.backend == "null"
        assert resp.transcript is None
        assert resp.request_id == "null-be"


# -- transcribe_stt_request: successful adapter ------------------------------


class TestTranscribeSuccess:
    def test_success_returns_transcript(self) -> None:
        req = build_stt_request(input_source="microphone", request_id="ok-1")
        resp = transcribe_stt_request(req, adapter=StubSuccessBackend())
        assert resp.status == STT_STATUS_SUCCEEDED
        assert resp.transcript == "Hello world"
        assert resp.language == "en-US"
        assert resp.backend == "stub-success"
        assert resp.request_id == "ok-1"
        assert resp.error is None
        assert resp.error_class is None
        assert resp.schema_version == STT_PROTOCOL_SCHEMA_VERSION

    def test_success_has_timing(self) -> None:
        req = build_stt_request(input_source="microphone", request_id="ok-timing")
        resp = transcribe_stt_request(req, adapter=StubSuccessBackend())
        assert resp.started_at_ms is not None
        assert resp.finished_at_ms is not None
        assert resp.finished_at_ms >= resp.started_at_ms

    def test_success_normalizes_whitespace(self) -> None:
        req = build_stt_request(input_source="microphone", request_id="ok-ws")
        resp = transcribe_stt_request(req, adapter=StubWhitespaceTranscriptBackend())
        assert resp.status == STT_STATUS_SUCCEEDED
        assert resp.transcript == "hello world"
        assert resp.language == "en"


# -- transcribe_stt_request: unsupported input -------------------------------


class TestTranscribeUnsupported:
    def test_unsupported_source_returns_unsupported(self) -> None:
        req = build_stt_request(input_source="stream", request_id="unsup-1")
        resp = transcribe_stt_request(req, adapter=StubUnsupportedBackend())
        assert resp.status == STT_STATUS_UNSUPPORTED
        assert resp.error_class == STT_ERROR_UNSUPPORTED_SOURCE
        assert resp.backend == "stub-unsupported"
        assert resp.transcript is None
        assert resp.request_id == "unsup-1"
        assert "not supported" in (resp.error or "")

    def test_unsupported_has_timing(self) -> None:
        req = build_stt_request(input_source="stream", request_id="unsup-time")
        resp = transcribe_stt_request(req, adapter=StubUnsupportedBackend())
        assert resp.started_at_ms is not None
        assert resp.finished_at_ms is not None


# -- transcribe_stt_request: backend exception -------------------------------


class TestTranscribeBackendException:
    def test_exception_returns_failed(self) -> None:
        req = build_stt_request(input_source="microphone", request_id="crash-1")
        resp = transcribe_stt_request(req, adapter=StubCrashingBackend())
        assert resp.status == STT_STATUS_FAILED
        assert resp.error_class == STT_ERROR_BACKEND_ERROR
        assert resp.backend == "stub-crash"
        assert resp.transcript is None
        assert "segfault" in (resp.error or "").lower()
        assert resp.request_id == "crash-1"

    def test_exception_does_not_raise(self) -> None:
        """The entry point never raises — it always returns an STTResponse."""
        req = build_stt_request(input_source="microphone", request_id="crash-safe")
        resp = transcribe_stt_request(req, adapter=StubCrashingBackend())
        assert resp.status == STT_STATUS_FAILED


# -- transcribe_stt_request: adapter-reported error --------------------------


class TestTranscribeAdapterError:
    def test_adapter_error_returns_failed(self) -> None:
        req = build_stt_request(input_source="audio_file", request_id="err-1")
        resp = transcribe_stt_request(req, adapter=StubErrorResultBackend())
        assert resp.status == STT_STATUS_FAILED
        assert resp.error == "Backend rejected audio format"
        assert resp.error_class == "custom_format_error"
        assert resp.backend == "stub-error-result"
        assert resp.transcript is None


# -- transcribe_stt_request: empty transcript --------------------------------


class TestTranscribeEmptyTranscript:
    def test_empty_transcript_returns_failed(self) -> None:
        req = build_stt_request(input_source="microphone", request_id="empty-1")
        resp = transcribe_stt_request(req, adapter=StubEmptyTranscriptBackend())
        assert resp.status == STT_STATUS_FAILED
        assert resp.error_class == STT_ERROR_EMPTY_AUDIO
        assert resp.backend == "stub-empty"
        assert resp.transcript is None

    def test_none_transcript_returns_failed(self) -> None:
        """Adapter returning None transcript is treated as empty."""

        class NoneTranscriptBackend:
            @property
            def backend_name(self) -> str:
                return "stub-none-transcript"

            def transcribe(self, request: STTRequest) -> STTAdapterResult:
                return STTAdapterResult(transcript=None)

        req = build_stt_request(input_source="microphone", request_id="none-t")
        resp = transcribe_stt_request(req, adapter=NoneTranscriptBackend())
        assert resp.status == STT_STATUS_FAILED
        assert resp.error_class == STT_ERROR_EMPTY_AUDIO


# -- transcription reuses normalize_transcript_text --------------------------


class TestTranscriptNormalization:
    def test_normalization_matches_input_module(self) -> None:
        """transcribe_stt_request uses the same normalization as input.py."""
        from voxera.voice.input import normalize_transcript_text

        raw = "  hello   beautiful   world  "
        expected = normalize_transcript_text(raw)

        class RawBackend:
            @property
            def backend_name(self) -> str:
                return "raw"

            def transcribe(self, request: STTRequest) -> STTAdapterResult:
                return STTAdapterResult(transcript=raw)

        req = build_stt_request(input_source="microphone", request_id="norm")
        resp = transcribe_stt_request(req, adapter=RawBackend())
        assert resp.transcript == expected


# -- all input sources work through the entry point --------------------------


class TestTranscribeAllSources:
    @pytest.mark.parametrize("source", ["microphone", "audio_file", "stream"])
    def test_all_sources_succeed_with_working_backend(self, source: str) -> None:
        req = build_stt_request(input_source=source, request_id=f"src-{source}")
        resp = transcribe_stt_request(req, adapter=StubSuccessBackend())
        assert resp.status == STT_STATUS_SUCCEEDED
        assert resp.transcript == "Hello world"
