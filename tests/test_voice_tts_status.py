"""Tests for the TTS status surface contract.

Pins the observable TTS status surface: configuration states, availability
semantics, truthful unavailable/unconfigured handling, and dict serialization.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voxera.voice.flags import VoiceFoundationFlags, load_voice_foundation_flags
from voxera.voice.tts_status import (
    TTS_STATUS_AVAILABLE,
    TTS_STATUS_DISABLED,
    TTS_STATUS_SCHEMA_VERSION,
    TTS_STATUS_UNCONFIGURED,
    TTSStatus,
    build_tts_status,
    tts_status_as_dict,
)


def _flags(
    *,
    foundation: bool = False,
    output: bool = False,
    tts_backend: str | None = None,
) -> VoiceFoundationFlags:
    return VoiceFoundationFlags(
        enable_voice_foundation=foundation,
        enable_voice_input=False,
        enable_voice_output=output,
        voice_stt_backend=None,
        voice_tts_backend=tts_backend,
    )


# -- status surface shape ---------------------------------------------------


class TestTTSStatusShape:
    def test_available_when_fully_configured(self) -> None:
        status = build_tts_status(_flags(foundation=True, output=True, tts_backend="test-speaker"))
        assert isinstance(status, TTSStatus)
        assert status.configured is True
        assert status.available is True
        assert status.enabled is True
        assert status.backend == "test-speaker"
        assert status.status == TTS_STATUS_AVAILABLE
        assert status.reason is None
        assert status.last_error is None
        assert status.schema_version == TTS_STATUS_SCHEMA_VERSION

    def test_disabled_when_foundation_off(self) -> None:
        status = build_tts_status(_flags(foundation=False, output=True, tts_backend="test-speaker"))
        assert status.available is False
        assert status.enabled is False
        assert status.configured is True
        assert status.status == TTS_STATUS_DISABLED
        assert status.reason == "voice_foundation_disabled"

    def test_disabled_when_output_off(self) -> None:
        status = build_tts_status(_flags(foundation=True, output=False, tts_backend="test-speaker"))
        assert status.available is False
        assert status.enabled is False
        assert status.configured is True
        assert status.status == TTS_STATUS_DISABLED
        assert status.reason == "voice_output_disabled"

    def test_unconfigured_when_no_backend(self) -> None:
        status = build_tts_status(_flags(foundation=True, output=True, tts_backend=None))
        assert status.available is False
        assert status.enabled is True
        assert status.configured is False
        assert status.status == TTS_STATUS_UNCONFIGURED
        assert status.reason == "voice_tts_backend_not_configured"

    def test_fully_disabled_defaults(self) -> None:
        status = build_tts_status(_flags())
        assert status.available is False
        assert status.enabled is False
        assert status.configured is False
        assert status.status == TTS_STATUS_DISABLED
        assert status.reason == "voice_foundation_disabled"

    def test_status_is_frozen(self) -> None:
        status = build_tts_status(_flags(foundation=True, output=True, tts_backend="x"))
        with pytest.raises(AttributeError):
            status.available = False  # type: ignore[misc]


# -- truthful unavailable handling ------------------------------------------


class TestTTSUnavailableHandling:
    def test_available_does_not_imply_synthesis_works(self) -> None:
        """available=True means configured + enabled, NOT proven synthesis."""
        status = build_tts_status(_flags(foundation=True, output=True, tts_backend="stub"))
        assert status.available is True
        # No synthesis was attempted -- status is a configuration truth surface,
        # not a proof of successful speech output.
        assert status.reason is None

    def test_last_error_passthrough(self) -> None:
        status = build_tts_status(
            _flags(foundation=True, output=True, tts_backend="stub"),
            last_error="provider returned 503",
        )
        assert status.last_error == "provider returned 503"

    def test_last_error_none_when_not_provided(self) -> None:
        status = build_tts_status(_flags(foundation=True, output=True, tts_backend="stub"))
        assert status.last_error is None

    def test_last_error_strips_whitespace(self) -> None:
        status = build_tts_status(
            _flags(foundation=True, output=True, tts_backend="stub"),
            last_error="  timeout  ",
        )
        assert status.last_error == "timeout"


# -- dict serialization -----------------------------------------------------


class TestTTSStatusSerialization:
    def test_as_dict_roundtrip(self) -> None:
        status = build_tts_status(_flags(foundation=True, output=True, tts_backend="test-tts"))
        d = tts_status_as_dict(status)
        assert isinstance(d, dict)
        assert d["configured"] is True
        assert d["available"] is True
        assert d["enabled"] is True
        assert d["backend"] == "test-tts"
        assert d["status"] == TTS_STATUS_AVAILABLE
        assert d["reason"] is None
        assert d["last_error"] is None
        assert d["schema_version"] == TTS_STATUS_SCHEMA_VERSION

    def test_as_dict_json_serializable(self) -> None:
        status = build_tts_status(_flags(foundation=True, output=True, tts_backend="stub"))
        d = tts_status_as_dict(status)
        serialized = json.dumps(d)
        assert isinstance(serialized, str)

    def test_as_dict_disabled_state(self) -> None:
        status = build_tts_status(_flags())
        d = tts_status_as_dict(status)
        assert d["available"] is False
        assert d["status"] == TTS_STATUS_DISABLED
        assert d["reason"] == "voice_foundation_disabled"


# -- integration with flags loader ------------------------------------------


class TestTTSStatusWithFlagsLoader:
    def test_from_config_file(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "enable_voice_foundation": True,
                    "enable_voice_output": True,
                    "voice_tts_backend": "stub-speaker",
                }
            ),
            encoding="utf-8",
        )
        flags = load_voice_foundation_flags(config_path=config_path, environ={})
        status = build_tts_status(flags)
        assert status.available is True
        assert status.backend == "stub-speaker"

    def test_from_empty_config(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.json"
        config_path.write_text("{}", encoding="utf-8")
        flags = load_voice_foundation_flags(config_path=config_path, environ={})
        status = build_tts_status(flags)
        assert status.available is False
        assert status.status == TTS_STATUS_DISABLED

    def test_from_env_vars(self) -> None:
        flags = load_voice_foundation_flags(
            environ={
                "VOXERA_ENABLE_VOICE_FOUNDATION": "1",
                "VOXERA_ENABLE_VOICE_OUTPUT": "1",
                "VOXERA_VOICE_TTS_BACKEND": "env-speaker",
            }
        )
        status = build_tts_status(flags)
        assert status.available is True
        assert status.backend == "env-speaker"


# -- doctor integration -----------------------------------------------------


class TestTTSStatusInDoctor:
    """Verify the TTS status check appears in ``run_quick_doctor`` output."""

    @staticmethod
    def _make_queue(tmp_path: Path) -> Path:
        queue_root = tmp_path / "queue"
        (queue_root / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
        for bucket in ("inbox", "done", "failed", "canceled"):
            (queue_root / bucket).mkdir(parents=True, exist_ok=True)
        (queue_root / "health.json").write_text("{}", encoding="utf-8")
        return queue_root

    def test_tts_check_present_in_quick_doctor(self, tmp_path: Path) -> None:
        from voxera.doctor import run_quick_doctor

        queue_root = self._make_queue(tmp_path)
        checks = run_quick_doctor(queue_root=queue_root)
        tts_checks = [c for c in checks if c["check"] == "voice: tts status"]
        assert len(tts_checks) == 1

    def test_tts_check_ok_when_disabled(self, tmp_path: Path) -> None:
        """Disabled-by-config is intentional; should be ok, not warn."""
        from voxera.doctor import run_quick_doctor

        queue_root = self._make_queue(tmp_path)
        checks = run_quick_doctor(queue_root=queue_root)
        tts_check = next(c for c in checks if c["check"] == "voice: tts status")
        assert tts_check["status"] == "ok"
        assert "disabled" in tts_check["detail"]
        assert tts_check["hint"] == ""

    def test_tts_check_warn_when_enabled_but_unconfigured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from voxera.doctor import run_quick_doctor

        monkeypatch.setenv("VOXERA_ENABLE_VOICE_FOUNDATION", "1")
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_OUTPUT", "1")
        # no VOXERA_VOICE_TTS_BACKEND set

        queue_root = self._make_queue(tmp_path)
        checks = run_quick_doctor(queue_root=queue_root)
        tts_check = next(c for c in checks if c["check"] == "voice: tts status")
        assert tts_check["status"] == "warn"
        assert "unconfigured" in tts_check["detail"]
        assert "VOXERA_VOICE_TTS_BACKEND" in tts_check["hint"]
