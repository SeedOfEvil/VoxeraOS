"""Tests for the combined voice status summary surface.

Pins the operator-facing voice status summary contract: foundation state,
STT/TTS status embedding, dependency availability checks, truthful
unavailable/unconfigured handling, and JSON serializability.
"""

from __future__ import annotations

import json

from voxera.voice.flags import VoiceFoundationFlags
from voxera.voice.voice_status_summary import (
    VOICE_STATUS_SUMMARY_SCHEMA_VERSION,
    build_voice_status_summary,
)


def _flags(
    *,
    foundation: bool = False,
    input: bool = False,
    output: bool = False,
    stt_backend: str | None = None,
    tts_backend: str | None = None,
) -> VoiceFoundationFlags:
    return VoiceFoundationFlags(
        enable_voice_foundation=foundation,
        enable_voice_input=input,
        enable_voice_output=output,
        voice_stt_backend=stt_backend,
        voice_tts_backend=tts_backend,
    )


# -- summary shape ----------------------------------------------------------


class TestVoiceStatusSummaryShape:
    def test_all_disabled_defaults(self) -> None:
        summary = build_voice_status_summary(_flags())
        assert summary["voice_foundation_enabled"] is False
        assert summary["stt"]["status"] == "disabled"
        assert summary["stt"]["available"] is False
        assert summary["tts"]["status"] == "disabled"
        assert summary["tts"]["available"] is False
        assert summary["schema_version"] == VOICE_STATUS_SUMMARY_SCHEMA_VERSION

    def test_fully_configured_stt_and_tts(self) -> None:
        summary = build_voice_status_summary(
            _flags(
                foundation=True,
                input=True,
                output=True,
                stt_backend="whisper_local",
                tts_backend="piper_local",
            )
        )
        assert summary["voice_foundation_enabled"] is True
        assert summary["stt"]["status"] == "available"
        assert summary["stt"]["available"] is True
        assert summary["stt"]["backend"] == "whisper_local"
        assert summary["tts"]["status"] == "available"
        assert summary["tts"]["available"] is True
        assert summary["tts"]["backend"] == "piper_local"

    def test_foundation_disabled_overrides_backend_config(self) -> None:
        summary = build_voice_status_summary(
            _flags(
                foundation=False,
                input=True,
                output=True,
                stt_backend="whisper_local",
                tts_backend="piper_local",
            )
        )
        assert summary["voice_foundation_enabled"] is False
        assert summary["stt"]["available"] is False
        assert summary["stt"]["status"] == "disabled"
        assert summary["tts"]["available"] is False
        assert summary["tts"]["status"] == "disabled"

    def test_stt_enabled_but_unconfigured(self) -> None:
        summary = build_voice_status_summary(_flags(foundation=True, input=True))
        assert summary["stt"]["status"] == "unconfigured"
        assert summary["stt"]["enabled"] is True
        assert summary["stt"]["configured"] is False
        assert summary["stt"]["reason"] == "voice_stt_backend_not_configured"

    def test_tts_enabled_but_unconfigured(self) -> None:
        summary = build_voice_status_summary(_flags(foundation=True, output=True))
        assert summary["tts"]["status"] == "unconfigured"
        assert summary["tts"]["enabled"] is True
        assert summary["tts"]["configured"] is False
        assert summary["tts"]["reason"] == "voice_tts_backend_not_configured"

    def test_json_serializable(self) -> None:
        summary = build_voice_status_summary(
            _flags(
                foundation=True,
                input=True,
                output=True,
                stt_backend="whisper_local",
                tts_backend="piper_local",
            )
        )
        serialized = json.dumps(summary)
        assert isinstance(serialized, str)
        roundtrip = json.loads(serialized)
        assert roundtrip["voice_foundation_enabled"] is True

    def test_last_tts_error_passthrough(self) -> None:
        summary = build_voice_status_summary(
            _flags(foundation=True, output=True, tts_backend="piper_local"),
            last_tts_error="synthesis timeout",
        )
        assert summary["tts"]["last_error"] == "synthesis timeout"

    def test_last_tts_error_none_by_default(self) -> None:
        summary = build_voice_status_summary(
            _flags(foundation=True, output=True, tts_backend="piper_local"),
        )
        assert summary["tts"]["last_error"] is None


# -- dependency checks ------------------------------------------------------


class TestDependencyChecks:
    def test_stt_no_backend_not_checked(self) -> None:
        summary = build_voice_status_summary(_flags())
        dep = summary["stt_dependency"]
        assert dep["checked"] is False
        assert dep["reason"] == "no_backend_configured"

    def test_tts_no_backend_not_checked(self) -> None:
        summary = build_voice_status_summary(_flags())
        dep = summary["tts_dependency"]
        assert dep["checked"] is False
        assert dep["reason"] == "no_backend_configured"

    def test_stt_whisper_dependency_checked(self) -> None:
        summary = build_voice_status_summary(
            _flags(foundation=True, input=True, stt_backend="whisper_local")
        )
        dep = summary["stt_dependency"]
        assert dep["checked"] is True
        assert dep["package"] == "faster-whisper"
        # available is True or False depending on whether faster-whisper
        # is installed in the test environment; the key point is that
        # it was checked and reports truthfully.
        assert isinstance(dep["available"], bool)
        if not dep["available"]:
            assert "hint" in dep

    def test_tts_piper_dependency_checked(self) -> None:
        summary = build_voice_status_summary(
            _flags(foundation=True, output=True, tts_backend="piper_local")
        )
        dep = summary["tts_dependency"]
        assert dep["checked"] is True
        assert dep["package"] == "piper-tts"
        assert isinstance(dep["available"], bool)
        if not dep["available"]:
            assert "hint" in dep

    def test_unknown_stt_backend_not_checked(self) -> None:
        summary = build_voice_status_summary(
            _flags(foundation=True, input=True, stt_backend="unknown_engine")
        )
        dep = summary["stt_dependency"]
        assert dep["checked"] is False
        assert "unknown_backend" in dep["reason"]

    def test_unknown_tts_backend_not_checked(self) -> None:
        summary = build_voice_status_summary(
            _flags(foundation=True, output=True, tts_backend="unknown_engine")
        )
        dep = summary["tts_dependency"]
        assert dep["checked"] is False
        assert "unknown_backend" in dep["reason"]


# -- truthful readiness -----------------------------------------------------


class TestTruthfulReadiness:
    def test_no_fake_ready_when_disabled(self) -> None:
        summary = build_voice_status_summary(_flags())
        assert summary["stt"]["available"] is False
        assert summary["tts"]["available"] is False
        assert summary["stt"]["status"] != "available"
        assert summary["tts"]["status"] != "available"

    def test_no_fake_ready_when_backend_missing(self) -> None:
        summary = build_voice_status_summary(_flags(foundation=True, input=True, output=True))
        assert summary["stt"]["available"] is False
        assert summary["tts"]["available"] is False
        assert summary["stt"]["reason"] is not None
        assert summary["tts"]["reason"] is not None

    def test_available_does_not_imply_runtime_success(self) -> None:
        """available=True means configured+enabled, not proven transcription/synthesis."""
        summary = build_voice_status_summary(
            _flags(
                foundation=True,
                input=True,
                output=True,
                stt_backend="whisper_local",
                tts_backend="piper_local",
            )
        )
        # available is a config truth, not a runtime proof
        assert summary["stt"]["available"] is True
        assert summary["tts"]["available"] is True
        assert summary["stt"]["reason"] is None
        assert summary["tts"]["reason"] is None

    def test_available_can_coexist_with_missing_dependency(self) -> None:
        """Config status 'available' is independent of dependency presence.

        An operator may see status=available (config is correct) alongside
        dependency=missing (the Python package isn't installed). This is
        truthful: 'available' is a configuration fact, not a runtime promise.
        """
        summary = build_voice_status_summary(
            _flags(
                foundation=True,
                input=True,
                output=True,
                stt_backend="whisper_local",
                tts_backend="piper_local",
            )
        )
        # Status reflects config truth
        assert summary["stt"]["available"] is True
        assert summary["tts"]["available"] is True
        # Dependency is checked independently — may be True or False
        assert summary["stt_dependency"]["checked"] is True
        assert summary["tts_dependency"]["checked"] is True
        # These are separate concerns, both reported truthfully
        assert isinstance(summary["stt_dependency"]["available"], bool)
        assert isinstance(summary["tts_dependency"]["available"], bool)

    def test_reason_strings_present_when_unavailable(self) -> None:
        # foundation disabled
        s1 = build_voice_status_summary(_flags())
        assert s1["stt"]["reason"] == "voice_foundation_disabled"
        assert s1["tts"]["reason"] == "voice_foundation_disabled"

        # input/output disabled
        s2 = build_voice_status_summary(_flags(foundation=True))
        assert s2["stt"]["reason"] == "voice_input_disabled"
        assert s2["tts"]["reason"] == "voice_output_disabled"

        # enabled but unconfigured
        s3 = build_voice_status_summary(_flags(foundation=True, input=True, output=True))
        assert s3["stt"]["reason"] == "voice_stt_backend_not_configured"
        assert s3["tts"]["reason"] == "voice_tts_backend_not_configured"
