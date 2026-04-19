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
    tts_piper_model: str | None = None,
    stt_whisper_model: str | None = None,
) -> VoiceFoundationFlags:
    return VoiceFoundationFlags(
        enable_voice_foundation=foundation,
        enable_voice_input=input,
        enable_voice_output=output,
        voice_stt_backend=stt_backend,
        voice_tts_backend=tts_backend,
        voice_tts_piper_model=tts_piper_model,
        voice_stt_whisper_model=stt_whisper_model,
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


# -- next_step hints -------------------------------------------------------


class TestNextStepHints:
    def test_foundation_disabled_yields_run_setup_hint(self) -> None:
        summary = build_voice_status_summary(_flags())
        assert summary["voice_foundation_next_step"] is not None
        assert "voxera setup" in summary["voice_foundation_next_step"]
        assert summary["stt"]["next_step"] is not None
        assert "voxera setup" in summary["stt"]["next_step"]
        assert summary["tts"]["next_step"] is not None
        assert "voxera setup" in summary["tts"]["next_step"]

    def test_foundation_enabled_clears_foundation_hint(self) -> None:
        summary = build_voice_status_summary(_flags(foundation=True))
        assert summary["voice_foundation_next_step"] is None

    def test_stt_enabled_but_no_backend_hint(self) -> None:
        summary = build_voice_status_summary(_flags(foundation=True, input=True))
        assert summary["stt"]["next_step"] is not None
        assert "STT backend" in summary["stt"]["next_step"]

    def test_tts_enabled_but_no_backend_hint(self) -> None:
        summary = build_voice_status_summary(_flags(foundation=True, output=True))
        assert summary["tts"]["next_step"] is not None
        assert "TTS backend" in summary["tts"]["next_step"]

    def test_input_disabled_hint_tells_operator_to_enable_stt(self) -> None:
        summary = build_voice_status_summary(_flags(foundation=True))
        assert summary["stt"]["next_step"] is not None
        assert "speech-to-text" in summary["stt"]["next_step"]

    def test_output_disabled_hint_tells_operator_to_enable_tts(self) -> None:
        summary = build_voice_status_summary(_flags(foundation=True))
        assert summary["tts"]["next_step"] is not None
        assert "text-to-speech" in summary["tts"]["next_step"]

    def test_fully_configured_no_hint_when_deps_available(self) -> None:
        summary = build_voice_status_summary(
            _flags(
                foundation=True,
                input=True,
                output=True,
                stt_backend="whisper_local",
                tts_backend="piper_local",
            )
        )
        # When deps are installed in the test env, no next_step; when deps are
        # missing, the hint must describe the dependency. Assert the invariant.
        stt_dep = summary["stt_dependency"]
        if stt_dep["checked"] and stt_dep["available"]:
            assert summary["stt"]["next_step"] is None
        else:
            assert "pip install" in (summary["stt"]["next_step"] or "")
        tts_dep = summary["tts_dependency"]
        if tts_dep["checked"] and tts_dep["available"]:
            # next_step may still be present if piper_model check fails for
            # a path-style value; the default name-style model has kind="name".
            pm = tts_dep.get("piper_model") or {}
            if pm.get("kind") != "path" or (pm.get("exists") and pm.get("metadata_exists")):
                assert summary["tts"]["next_step"] is None
        else:
            assert "pip install" in (summary["tts"]["next_step"] or "")


# -- Whisper model selection -----------------------------------------------


class TestWhisperModelSelection:
    def test_whisper_model_block_present_for_whisper_backend(self) -> None:
        summary = build_voice_status_summary(
            _flags(foundation=True, input=True, stt_backend="whisper_local")
        )
        dep = summary["stt_dependency"]
        assert "whisper_model" in dep
        wm = dep["whisper_model"]
        assert wm["selected"] is None
        assert wm["effective"] == "base"

    def test_whisper_model_reports_selected_value(self) -> None:
        summary = build_voice_status_summary(
            _flags(
                foundation=True,
                input=True,
                stt_backend="whisper_local",
                stt_whisper_model="distil-large-v3",
            )
        )
        wm = summary["stt_dependency"]["whisper_model"]
        assert wm["selected"] == "distil-large-v3"
        assert wm["effective"] == "distil-large-v3"

    def test_whisper_model_absent_for_non_whisper_backend(self) -> None:
        """Only the whisper_local backend exposes the whisper_model sub-dict."""
        summary = build_voice_status_summary(
            _flags(foundation=True, input=True, stt_backend="unknown_engine")
        )
        dep = summary["stt_dependency"]
        assert "whisper_model" not in dep


# -- Piper model path validation -------------------------------------------


class TestPiperModelCheck:
    def test_no_model_configured_reports_not_configured(self) -> None:
        summary = build_voice_status_summary(
            _flags(foundation=True, output=True, tts_backend="piper_local")
        )
        dep = summary["tts_dependency"]
        pm = dep.get("piper_model")
        assert pm is not None
        assert pm.get("configured") is False

    def test_named_model_reports_kind_name(self) -> None:
        summary = build_voice_status_summary(
            _flags(
                foundation=True,
                output=True,
                tts_backend="piper_local",
                tts_piper_model="en_US-amy-medium",
            )
        )
        pm = summary["tts_dependency"].get("piper_model") or {}
        assert pm.get("configured") is True
        assert pm.get("kind") == "name"
        assert pm.get("value") == "en_US-amy-medium"

    def test_path_model_missing_file_reports_missing(self, tmp_path: object) -> None:
        from pathlib import Path

        assert isinstance(tmp_path, Path)
        missing = tmp_path / "does_not_exist.onnx"
        summary = build_voice_status_summary(
            _flags(
                foundation=True,
                output=True,
                tts_backend="piper_local",
                tts_piper_model=str(missing),
            )
        )
        pm = summary["tts_dependency"].get("piper_model") or {}
        assert pm.get("kind") == "path"
        assert pm.get("exists") is False
        assert "hint" in pm
        # The TTS next_step must point at the missing file, not pip install.
        tts_dep = summary["tts_dependency"]
        if tts_dep.get("checked") and tts_dep.get("available"):
            assert summary["tts"]["next_step"] is not None
            assert "Piper model path" in summary["tts"]["next_step"]

    def test_path_model_metadata_missing_reports_metadata(self, tmp_path: object) -> None:
        from pathlib import Path

        assert isinstance(tmp_path, Path)
        model_file = tmp_path / "voice.onnx"
        model_file.write_bytes(b"")  # pretend the ONNX exists
        summary = build_voice_status_summary(
            _flags(
                foundation=True,
                output=True,
                tts_backend="piper_local",
                tts_piper_model=str(model_file),
            )
        )
        pm = summary["tts_dependency"].get("piper_model") or {}
        assert pm.get("kind") == "path"
        assert pm.get("exists") is True
        assert pm.get("metadata_exists") is False
        tts_dep = summary["tts_dependency"]
        if tts_dep.get("checked") and tts_dep.get("available"):
            assert summary["tts"]["next_step"] is not None
            assert "metadata" in summary["tts"]["next_step"]

    def test_path_model_with_metadata_is_present(self, tmp_path: object) -> None:
        from pathlib import Path

        assert isinstance(tmp_path, Path)
        model_file = tmp_path / "voice.onnx"
        model_file.write_bytes(b"")
        (tmp_path / "voice.onnx.json").write_text("{}", encoding="utf-8")
        summary = build_voice_status_summary(
            _flags(
                foundation=True,
                output=True,
                tts_backend="piper_local",
                tts_piper_model=str(model_file),
            )
        )
        pm = summary["tts_dependency"].get("piper_model") or {}
        assert pm.get("kind") == "path"
        assert pm.get("exists") is True
        assert pm.get("metadata_exists") is True
