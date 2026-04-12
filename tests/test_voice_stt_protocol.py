"""Tests for the STT request/response protocol contract shapes.

Pins the canonical STT protocol surfaces: request construction, success
response, failure response, unavailable response, and fail-closed
normalization of unknown status values.
"""

from __future__ import annotations

import json

import pytest

from voxera.voice.stt_protocol import (
    STT_ERROR_BACKEND_ERROR,
    STT_ERROR_BACKEND_MISSING,
    STT_ERROR_DISABLED,
    STT_PROTOCOL_SCHEMA_VERSION,
    STT_SOURCE_AUDIO_FILE,
    STT_SOURCE_MICROPHONE,
    STT_SOURCE_STREAM,
    STT_STATUS_FAILED,
    STT_STATUS_SUCCEEDED,
    STT_STATUS_UNAVAILABLE,
    STT_STATUS_UNSUPPORTED,
    STTRequest,
    STTResponse,
    build_stt_request,
    build_stt_response,
    build_stt_unavailable_response,
    stt_request_as_dict,
    stt_response_as_dict,
)

# -- request shape ----------------------------------------------------------


class TestSTTRequestShape:
    def test_build_request_has_all_fields(self) -> None:
        req = build_stt_request(input_source="microphone", language="en-US", session_id="sess-1")
        assert isinstance(req, STTRequest)
        assert req.input_source == STT_SOURCE_MICROPHONE
        assert req.language == "en-US"
        assert req.session_id == "sess-1"
        assert isinstance(req.request_id, str)
        assert len(req.request_id) > 0
        assert isinstance(req.created_at_ms, int)
        assert req.created_at_ms > 0
        assert req.schema_version == STT_PROTOCOL_SCHEMA_VERSION

    def test_build_request_accepts_all_valid_sources(self) -> None:
        for source in (STT_SOURCE_MICROPHONE, STT_SOURCE_AUDIO_FILE, STT_SOURCE_STREAM):
            req = build_stt_request(input_source=source)
            assert req.input_source == source

    def test_build_request_normalizes_source_case(self) -> None:
        req = build_stt_request(input_source="MICROPHONE")
        assert req.input_source == STT_SOURCE_MICROPHONE

    def test_build_request_rejects_unknown_source(self) -> None:
        with pytest.raises(ValueError, match="Invalid STT input_source"):
            build_stt_request(input_source="telepathy")

    def test_build_request_rejects_empty_source(self) -> None:
        with pytest.raises(ValueError, match="Invalid STT input_source"):
            build_stt_request(input_source="")

    def test_build_request_generates_uuid_when_no_id_given(self) -> None:
        r1 = build_stt_request(input_source="microphone")
        r2 = build_stt_request(input_source="microphone")
        assert r1.request_id != r2.request_id

    def test_build_request_accepts_explicit_id(self) -> None:
        req = build_stt_request(input_source="microphone", request_id="my-id-123")
        assert req.request_id == "my-id-123"

    def test_build_request_accepts_explicit_timestamp(self) -> None:
        req = build_stt_request(input_source="microphone", created_at_ms=1000)
        assert req.created_at_ms == 1000

    def test_request_is_frozen(self) -> None:
        req = build_stt_request(input_source="microphone")
        with pytest.raises(AttributeError):
            req.input_source = "stream"  # type: ignore[misc]

    def test_build_request_optional_fields_default_none(self) -> None:
        req = build_stt_request(input_source="microphone")
        assert req.language is None
        assert req.session_id is None
        assert req.audio_path is None


# -- success response shape -------------------------------------------------


class TestSTTSuccessResponse:
    def test_success_response_has_all_fields(self) -> None:
        resp = build_stt_response(
            request_id="req-1",
            status="succeeded",
            transcript="Hello world",
            language="en-US",
            backend="stub-transcriber",
            started_at_ms=1000,
            finished_at_ms=2000,
        )
        assert isinstance(resp, STTResponse)
        assert resp.request_id == "req-1"
        assert resp.status == STT_STATUS_SUCCEEDED
        assert resp.transcript == "Hello world"
        assert resp.language == "en-US"
        assert resp.error is None
        assert resp.error_class is None
        assert resp.backend == "stub-transcriber"
        assert resp.started_at_ms == 1000
        assert resp.finished_at_ms == 2000
        assert resp.schema_version == STT_PROTOCOL_SCHEMA_VERSION
        assert resp.inference_ms is None
        assert resp.audio_duration_ms is None

    def test_success_response_with_timing_fields(self) -> None:
        resp = build_stt_response(
            request_id="req-timing",
            status="succeeded",
            transcript="hello",
            inference_ms=150,
            audio_duration_ms=3500,
        )
        assert resp.inference_ms == 150
        assert resp.audio_duration_ms == 3500

    def test_success_response_normalizes_transcript_whitespace(self) -> None:
        resp = build_stt_response(
            request_id="req-2",
            status="succeeded",
            transcript="  hello   world  ",
        )
        assert resp.transcript == "hello world"

    def test_success_response_empty_transcript_becomes_none(self) -> None:
        resp = build_stt_response(
            request_id="req-3",
            status="succeeded",
            transcript="   ",
        )
        assert resp.transcript is None

    def test_response_is_frozen(self) -> None:
        resp = build_stt_response(request_id="req-4", status="succeeded")
        with pytest.raises(AttributeError):
            resp.status = "failed"  # type: ignore[misc]


# -- failure response shape -------------------------------------------------


class TestSTTFailureResponse:
    def test_failed_response_carries_error(self) -> None:
        resp = build_stt_response(
            request_id="req-5",
            status="failed",
            error="Backend timeout after 30s",
            error_class=STT_ERROR_BACKEND_ERROR,
            backend="slow-provider",
        )
        assert resp.status == STT_STATUS_FAILED
        assert resp.transcript is None
        assert resp.error == "Backend timeout after 30s"
        assert resp.error_class == STT_ERROR_BACKEND_ERROR
        assert resp.backend == "slow-provider"

    def test_unsupported_response(self) -> None:
        resp = build_stt_response(
            request_id="req-6",
            status="unsupported",
            error="Audio format not supported",
        )
        assert resp.status == STT_STATUS_UNSUPPORTED
        assert resp.error == "Audio format not supported"


# -- unavailable response shape ---------------------------------------------


class TestSTTUnavailableResponse:
    def test_unavailable_response_convenience_builder(self) -> None:
        resp = build_stt_unavailable_response(
            request_id="req-7",
            reason="Voice foundation is disabled",
            error_class=STT_ERROR_DISABLED,
        )
        assert resp.status == STT_STATUS_UNAVAILABLE
        assert resp.error == "Voice foundation is disabled"
        assert resp.error_class == STT_ERROR_DISABLED
        assert resp.transcript is None
        assert resp.request_id == "req-7"

    def test_unavailable_response_requires_error_class(self) -> None:
        resp = build_stt_unavailable_response(
            request_id="req-8",
            reason="No backend",
            error_class=STT_ERROR_BACKEND_MISSING,
        )
        assert resp.error_class == STT_ERROR_BACKEND_MISSING

    def test_unavailable_response_with_backend(self) -> None:
        resp = build_stt_unavailable_response(
            request_id="req-9",
            reason="Backend unreachable",
            error_class=STT_ERROR_BACKEND_MISSING,
            backend="dead-provider",
        )
        assert resp.backend == "dead-provider"
        assert resp.error_class == STT_ERROR_BACKEND_MISSING


# -- fail-closed status normalization ---------------------------------------


class TestSTTStatusNormalization:
    def test_unknown_status_normalizes_to_unavailable(self) -> None:
        resp = build_stt_response(request_id="req-10", status="invented_status")
        assert resp.status == STT_STATUS_UNAVAILABLE

    def test_empty_status_normalizes_to_unavailable(self) -> None:
        resp = build_stt_response(request_id="req-11", status="")
        assert resp.status == STT_STATUS_UNAVAILABLE

    def test_none_status_normalizes_to_unavailable(self) -> None:
        resp = build_stt_response(request_id="req-12", status=None)  # type: ignore[arg-type]
        assert resp.status == STT_STATUS_UNAVAILABLE

    def test_all_valid_statuses_pass_through(self) -> None:
        for valid in (
            STT_STATUS_SUCCEEDED,
            STT_STATUS_FAILED,
            STT_STATUS_UNAVAILABLE,
            STT_STATUS_UNSUPPORTED,
        ):
            resp = build_stt_response(request_id="req-v", status=valid)
            assert resp.status == valid


# -- error_class passthrough ------------------------------------------------


class TestSTTErrorClassPassthrough:
    def test_arbitrary_error_class_passes_through(self) -> None:
        """error_class is intentionally not validated — backends may define their own."""
        resp = build_stt_response(
            request_id="req-ec",
            status="failed",
            error_class="custom_backend_specific_error",
        )
        assert resp.error_class == "custom_backend_specific_error"

    def test_none_error_class_passes_through(self) -> None:
        resp = build_stt_response(request_id="req-ec2", status="succeeded")
        assert resp.error_class is None


# -- serialization ----------------------------------------------------------


class TestSTTSerialization:
    def test_request_as_dict_roundtrip(self) -> None:
        req = build_stt_request(
            input_source="microphone",
            language="en-US",
            session_id="sess-1",
            request_id="rid-1",
            created_at_ms=5000,
        )
        d = stt_request_as_dict(req)
        assert isinstance(d, dict)
        assert d["request_id"] == "rid-1"
        assert d["input_source"] == "microphone"
        assert d["language"] == "en-US"
        assert d["session_id"] == "sess-1"
        assert d["created_at_ms"] == 5000
        assert d["schema_version"] == STT_PROTOCOL_SCHEMA_VERSION
        # field-count guard: catches drift if a field is added to STTRequest
        # but forgotten in stt_request_as_dict
        assert len(d) == len(STTRequest.__dataclass_fields__)

    def test_request_as_dict_json_serializable(self) -> None:
        req = build_stt_request(input_source="stream")
        assert isinstance(json.dumps(stt_request_as_dict(req)), str)

    def test_response_as_dict_roundtrip(self) -> None:
        resp = build_stt_response(
            request_id="rid-2",
            status="succeeded",
            transcript="hello",
            language="en",
            backend="stub",
            started_at_ms=1000,
            finished_at_ms=2000,
            inference_ms=150,
            audio_duration_ms=3500,
        )
        d = stt_response_as_dict(resp)
        assert isinstance(d, dict)
        assert d["request_id"] == "rid-2"
        assert d["status"] == "succeeded"
        assert d["transcript"] == "hello"
        assert d["backend"] == "stub"
        assert d["schema_version"] == STT_PROTOCOL_SCHEMA_VERSION
        assert d["inference_ms"] == 150
        assert d["audio_duration_ms"] == 3500
        # field-count guard
        assert len(d) == len(STTResponse.__dataclass_fields__)

    def test_response_as_dict_json_serializable(self) -> None:
        resp = build_stt_response(request_id="rid-3", status="failed", error="boom")
        assert isinstance(json.dumps(stt_response_as_dict(resp)), str)
