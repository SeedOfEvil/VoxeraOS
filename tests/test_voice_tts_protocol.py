"""Tests for the TTS request/response protocol contract shapes.

Pins the canonical TTS protocol surfaces: request construction, success
response, failure response, unavailable response, fail-closed
normalization of unknown status values, audio_path semantics, and
error_class passthrough behavior.
"""

from __future__ import annotations

import json

import pytest

from voxera.voice.tts_protocol import (
    TTS_ERROR_BACKEND_ERROR,
    TTS_ERROR_BACKEND_MISSING,
    TTS_ERROR_DISABLED,
    TTS_ERROR_EMPTY_TEXT,
    TTS_ERROR_UNSUPPORTED_FORMAT,
    TTS_FORMAT_MP3,
    TTS_FORMAT_OGG,
    TTS_FORMAT_RAW,
    TTS_FORMAT_WAV,
    TTS_PROTOCOL_SCHEMA_VERSION,
    TTS_STATUS_FAILED,
    TTS_STATUS_SUCCEEDED,
    TTS_STATUS_UNAVAILABLE,
    TTS_STATUS_UNSUPPORTED,
    TTSRequest,
    TTSResponse,
    build_tts_request,
    build_tts_response,
    build_tts_unavailable_response,
    tts_request_as_dict,
    tts_response_as_dict,
)

# -- request shape ----------------------------------------------------------


class TestTTSRequestShape:
    def test_build_request_has_all_fields(self) -> None:
        req = build_tts_request(
            text="Hello world",
            voice_id="default",
            language="en-US",
            session_id="sess-1",
        )
        assert isinstance(req, TTSRequest)
        assert req.text == "Hello world"
        assert req.voice_id == "default"
        assert req.language == "en-US"
        assert req.session_id == "sess-1"
        assert req.speed == 1.0
        assert req.output_format == TTS_FORMAT_WAV
        assert isinstance(req.request_id, str)
        assert len(req.request_id) > 0
        assert isinstance(req.created_at_ms, int)
        assert req.created_at_ms > 0
        assert req.schema_version == TTS_PROTOCOL_SCHEMA_VERSION

    def test_build_request_accepts_all_valid_formats(self) -> None:
        for fmt in (TTS_FORMAT_WAV, TTS_FORMAT_MP3, TTS_FORMAT_OGG, TTS_FORMAT_RAW):
            req = build_tts_request(text="hello", output_format=fmt)
            assert req.output_format == fmt

    def test_build_request_normalizes_format_case(self) -> None:
        req = build_tts_request(text="hello", output_format="WAV")
        assert req.output_format == TTS_FORMAT_WAV

    def test_build_request_rejects_unknown_format(self) -> None:
        with pytest.raises(ValueError, match="Invalid TTS output_format"):
            build_tts_request(text="hello", output_format="flac")

    def test_build_request_rejects_empty_format(self) -> None:
        with pytest.raises(ValueError, match="Invalid TTS output_format"):
            build_tts_request(text="hello", output_format="")

    def test_build_request_rejects_empty_text(self) -> None:
        with pytest.raises(ValueError, match="TTS text must be non-empty"):
            build_tts_request(text="")

    def test_build_request_rejects_whitespace_only_text(self) -> None:
        with pytest.raises(ValueError, match="TTS text must be non-empty"):
            build_tts_request(text="   ")

    def test_build_request_strips_text(self) -> None:
        req = build_tts_request(text="  hello world  ")
        assert req.text == "hello world"

    def test_build_request_generates_uuid_when_no_id_given(self) -> None:
        r1 = build_tts_request(text="hello")
        r2 = build_tts_request(text="hello")
        assert r1.request_id != r2.request_id

    def test_build_request_accepts_explicit_id(self) -> None:
        req = build_tts_request(text="hello", request_id="my-id-123")
        assert req.request_id == "my-id-123"

    def test_build_request_accepts_explicit_timestamp(self) -> None:
        req = build_tts_request(text="hello", created_at_ms=1000)
        assert req.created_at_ms == 1000

    def test_request_is_frozen(self) -> None:
        req = build_tts_request(text="hello")
        with pytest.raises(AttributeError):
            req.text = "changed"  # type: ignore[misc]

    def test_build_request_optional_fields_default_none(self) -> None:
        req = build_tts_request(text="hello")
        assert req.voice_id is None
        assert req.language is None
        assert req.session_id is None

    def test_build_request_speed_default(self) -> None:
        req = build_tts_request(text="hello")
        assert req.speed == 1.0

    def test_build_request_speed_clamped_low(self) -> None:
        req = build_tts_request(text="hello", speed=0.01)
        assert req.speed == 0.1

    def test_build_request_speed_clamped_high(self) -> None:
        req = build_tts_request(text="hello", speed=99.0)
        assert req.speed == 10.0

    def test_build_request_speed_within_range(self) -> None:
        req = build_tts_request(text="hello", speed=1.5)
        assert req.speed == 1.5


# -- success response shape -------------------------------------------------


class TestTTSSuccessResponse:
    def test_success_response_has_all_fields(self) -> None:
        resp = build_tts_response(
            request_id="req-1",
            status="succeeded",
            audio_path="/tmp/output.wav",
            audio_duration_ms=3500,
            backend="stub-speaker",
            started_at_ms=1000,
            finished_at_ms=2000,
        )
        assert isinstance(resp, TTSResponse)
        assert resp.request_id == "req-1"
        assert resp.status == TTS_STATUS_SUCCEEDED
        assert resp.audio_path == "/tmp/output.wav"
        assert resp.audio_duration_ms == 3500
        assert resp.error is None
        assert resp.error_class is None
        assert resp.backend == "stub-speaker"
        assert resp.started_at_ms == 1000
        assert resp.finished_at_ms == 2000
        assert resp.schema_version == TTS_PROTOCOL_SCHEMA_VERSION
        assert resp.inference_ms is None

    def test_success_response_with_timing_fields(self) -> None:
        resp = build_tts_response(
            request_id="req-timing",
            status="succeeded",
            audio_path="/tmp/out.wav",
            inference_ms=150,
            audio_duration_ms=3500,
        )
        assert resp.inference_ms == 150
        assert resp.audio_duration_ms == 3500

    def test_success_response_audio_path_stripped(self) -> None:
        resp = build_tts_response(
            request_id="req-2",
            status="succeeded",
            audio_path="  /tmp/output.wav  ",
        )
        assert resp.audio_path == "/tmp/output.wav"

    def test_success_response_empty_audio_path_becomes_none(self) -> None:
        resp = build_tts_response(
            request_id="req-3",
            status="succeeded",
            audio_path="   ",
        )
        assert resp.audio_path is None

    def test_response_is_frozen(self) -> None:
        resp = build_tts_response(request_id="req-4", status="succeeded")
        with pytest.raises(AttributeError):
            resp.status = "failed"  # type: ignore[misc]


# -- audio_path semantics ---------------------------------------------------


class TestTTSAudioPathSemantics:
    def test_audio_path_present_on_success(self) -> None:
        """Successful synthesis should carry an audio_path."""
        resp = build_tts_response(
            request_id="req-ap-1",
            status="succeeded",
            audio_path="/output/speech.wav",
            audio_duration_ms=2000,
        )
        assert resp.audio_path == "/output/speech.wav"
        assert resp.audio_duration_ms == 2000

    def test_audio_path_none_on_failure(self) -> None:
        resp = build_tts_response(
            request_id="req-ap-2",
            status="failed",
            error="Synthesis failed",
            error_class=TTS_ERROR_BACKEND_ERROR,
        )
        assert resp.audio_path is None

    def test_audio_path_none_on_unavailable(self) -> None:
        resp = build_tts_unavailable_response(
            request_id="req-ap-3",
            reason="No backend",
            error_class=TTS_ERROR_BACKEND_MISSING,
        )
        assert resp.audio_path is None

    def test_audio_duration_ms_none_on_failure(self) -> None:
        resp = build_tts_response(
            request_id="req-ap-4",
            status="failed",
            error="boom",
        )
        assert resp.audio_duration_ms is None


# -- failure response shape -------------------------------------------------


class TestTTSFailureResponse:
    def test_failed_response_carries_error(self) -> None:
        resp = build_tts_response(
            request_id="req-5",
            status="failed",
            error="Backend timeout after 30s",
            error_class=TTS_ERROR_BACKEND_ERROR,
            backend="slow-provider",
        )
        assert resp.status == TTS_STATUS_FAILED
        assert resp.audio_path is None
        assert resp.error == "Backend timeout after 30s"
        assert resp.error_class == TTS_ERROR_BACKEND_ERROR
        assert resp.backend == "slow-provider"

    def test_unsupported_response(self) -> None:
        resp = build_tts_response(
            request_id="req-6",
            status="unsupported",
            error="Output format not supported",
        )
        assert resp.status == TTS_STATUS_UNSUPPORTED
        assert resp.error == "Output format not supported"


# -- unavailable response shape ---------------------------------------------


class TestTTSUnavailableResponse:
    def test_unavailable_response_convenience_builder(self) -> None:
        resp = build_tts_unavailable_response(
            request_id="req-7",
            reason="Voice foundation is disabled",
            error_class=TTS_ERROR_DISABLED,
        )
        assert resp.status == TTS_STATUS_UNAVAILABLE
        assert resp.error == "Voice foundation is disabled"
        assert resp.error_class == TTS_ERROR_DISABLED
        assert resp.audio_path is None
        assert resp.request_id == "req-7"

    def test_unavailable_response_requires_error_class(self) -> None:
        resp = build_tts_unavailable_response(
            request_id="req-8",
            reason="No backend",
            error_class=TTS_ERROR_BACKEND_MISSING,
        )
        assert resp.error_class == TTS_ERROR_BACKEND_MISSING

    def test_unavailable_response_with_backend(self) -> None:
        resp = build_tts_unavailable_response(
            request_id="req-9",
            reason="Backend unreachable",
            error_class=TTS_ERROR_BACKEND_MISSING,
            backend="dead-provider",
        )
        assert resp.backend == "dead-provider"
        assert resp.error_class == TTS_ERROR_BACKEND_MISSING


# -- nullable fields on failure/unavailable ---------------------------------


class TestTTSNullableFieldsOnNonSuccess:
    def test_failed_response_nullable_fields(self) -> None:
        resp = build_tts_response(
            request_id="req-nf-1",
            status="failed",
            error="boom",
        )
        assert resp.audio_path is None
        assert resp.audio_duration_ms is None
        assert resp.backend is None
        assert resp.started_at_ms is None
        assert resp.finished_at_ms is None
        assert resp.inference_ms is None

    def test_unavailable_response_nullable_fields(self) -> None:
        resp = build_tts_unavailable_response(
            request_id="req-nf-2",
            reason="disabled",
            error_class=TTS_ERROR_DISABLED,
        )
        assert resp.audio_path is None
        assert resp.audio_duration_ms is None
        assert resp.started_at_ms is None
        assert resp.finished_at_ms is None
        assert resp.inference_ms is None


# -- fail-closed status normalization ---------------------------------------


class TestTTSStatusNormalization:
    def test_unknown_status_normalizes_to_unavailable(self) -> None:
        resp = build_tts_response(request_id="req-10", status="invented_status")
        assert resp.status == TTS_STATUS_UNAVAILABLE

    def test_empty_status_normalizes_to_unavailable(self) -> None:
        resp = build_tts_response(request_id="req-11", status="")
        assert resp.status == TTS_STATUS_UNAVAILABLE

    def test_none_status_normalizes_to_unavailable(self) -> None:
        resp = build_tts_response(request_id="req-12", status=None)  # type: ignore[arg-type]
        assert resp.status == TTS_STATUS_UNAVAILABLE

    def test_all_valid_statuses_pass_through(self) -> None:
        for valid in (
            TTS_STATUS_SUCCEEDED,
            TTS_STATUS_FAILED,
            TTS_STATUS_UNAVAILABLE,
            TTS_STATUS_UNSUPPORTED,
        ):
            resp = build_tts_response(request_id="req-v", status=valid)
            assert resp.status == valid


# -- error_class passthrough ------------------------------------------------


class TestTTSErrorClassPassthrough:
    def test_arbitrary_error_class_passes_through(self) -> None:
        """error_class is intentionally not validated -- backends may define their own."""
        resp = build_tts_response(
            request_id="req-ec",
            status="failed",
            error_class="custom_backend_specific_error",
        )
        assert resp.error_class == "custom_backend_specific_error"

    def test_none_error_class_passes_through(self) -> None:
        resp = build_tts_response(request_id="req-ec2", status="succeeded")
        assert resp.error_class is None

    def test_canonical_error_classes_pass_through(self) -> None:
        for ec in (
            TTS_ERROR_DISABLED,
            TTS_ERROR_BACKEND_MISSING,
            TTS_ERROR_BACKEND_ERROR,
            TTS_ERROR_EMPTY_TEXT,
            TTS_ERROR_UNSUPPORTED_FORMAT,
        ):
            resp = build_tts_response(
                request_id="req-ec3",
                status="failed",
                error_class=ec,
            )
            assert resp.error_class == ec


# -- serialization ----------------------------------------------------------


class TestTTSSerialization:
    def test_request_as_dict_roundtrip(self) -> None:
        req = build_tts_request(
            text="Hello world",
            voice_id="default",
            language="en-US",
            session_id="sess-1",
            request_id="rid-1",
            created_at_ms=5000,
        )
        d = tts_request_as_dict(req)
        assert isinstance(d, dict)
        assert d["request_id"] == "rid-1"
        assert d["text"] == "Hello world"
        assert d["voice_id"] == "default"
        assert d["language"] == "en-US"
        assert d["session_id"] == "sess-1"
        assert d["speed"] == 1.0
        assert d["output_format"] == "wav"
        assert d["created_at_ms"] == 5000
        assert d["schema_version"] == TTS_PROTOCOL_SCHEMA_VERSION
        # field-count guard: catches drift if a field is added to TTSRequest
        # but forgotten in tts_request_as_dict
        assert len(d) == len(TTSRequest.__dataclass_fields__)

    def test_request_as_dict_json_serializable(self) -> None:
        req = build_tts_request(text="hello")
        assert isinstance(json.dumps(tts_request_as_dict(req)), str)

    def test_response_as_dict_roundtrip(self) -> None:
        resp = build_tts_response(
            request_id="rid-2",
            status="succeeded",
            audio_path="/tmp/out.wav",
            audio_duration_ms=3500,
            backend="stub",
            started_at_ms=1000,
            finished_at_ms=2000,
            inference_ms=150,
        )
        d = tts_response_as_dict(resp)
        assert isinstance(d, dict)
        assert d["request_id"] == "rid-2"
        assert d["status"] == "succeeded"
        assert d["audio_path"] == "/tmp/out.wav"
        assert d["audio_duration_ms"] == 3500
        assert d["backend"] == "stub"
        assert d["schema_version"] == TTS_PROTOCOL_SCHEMA_VERSION
        assert d["inference_ms"] == 150
        # field-count guard
        assert len(d) == len(TTSResponse.__dataclass_fields__)

    def test_response_as_dict_json_serializable(self) -> None:
        resp = build_tts_response(request_id="rid-3", status="failed", error="boom")
        assert isinstance(json.dumps(tts_response_as_dict(resp)), str)


# -- schema version ---------------------------------------------------------


class TestTTSSchemaVersion:
    def test_schema_version_is_one(self) -> None:
        assert TTS_PROTOCOL_SCHEMA_VERSION == 1

    def test_request_carries_schema_version(self) -> None:
        req = build_tts_request(text="hello")
        assert req.schema_version == TTS_PROTOCOL_SCHEMA_VERSION

    def test_response_carries_schema_version(self) -> None:
        resp = build_tts_response(request_id="req-sv", status="succeeded")
        assert resp.schema_version == TTS_PROTOCOL_SCHEMA_VERSION

    def test_unavailable_response_carries_schema_version(self) -> None:
        resp = build_tts_unavailable_response(
            request_id="req-sv2",
            reason="disabled",
            error_class=TTS_ERROR_DISABLED,
        )
        assert resp.schema_version == TTS_PROTOCOL_SCHEMA_VERSION
