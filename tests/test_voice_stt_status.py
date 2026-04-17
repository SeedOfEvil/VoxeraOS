"""Tests for the STT status surface contract.

Symmetric with ``test_voice_tts_status.py``.  Pins the observable STT status
surface: configuration states, availability semantics, truthful
unavailable/unconfigured handling, dict serialization, and doctor integration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voxera import config as _voxera_config
from voxera.voice.flags import VoiceFoundationFlags, load_voice_foundation_flags
from voxera.voice.stt_status import (
    STT_STATUS_LABEL_AVAILABLE,
    STT_STATUS_LABEL_DISABLED,
    STT_STATUS_LABEL_UNCONFIGURED,
    STT_STATUS_SCHEMA_VERSION,
    STTStatus,
    build_stt_status,
    stt_status_as_dict,
)


def _flags(
    *,
    foundation: bool = False,
    input: bool = False,
    stt_backend: str | None = None,
) -> VoiceFoundationFlags:
    return VoiceFoundationFlags(
        enable_voice_foundation=foundation,
        enable_voice_input=input,
        enable_voice_output=False,
        voice_stt_backend=stt_backend,
        voice_tts_backend=None,
    )


# -- status surface shape ---------------------------------------------------


class TestSTTStatusShape:
    def test_available_when_fully_configured(self) -> None:
        status = build_stt_status(_flags(foundation=True, input=True, stt_backend="test-stt"))
        assert isinstance(status, STTStatus)
        assert status.configured is True
        assert status.available is True
        assert status.enabled is True
        assert status.backend == "test-stt"
        assert status.status == STT_STATUS_LABEL_AVAILABLE
        assert status.reason is None
        assert status.schema_version == STT_STATUS_SCHEMA_VERSION

    def test_disabled_when_foundation_off(self) -> None:
        status = build_stt_status(_flags(foundation=False, input=True, stt_backend="test-stt"))
        assert status.available is False
        assert status.enabled is False
        assert status.configured is True
        assert status.status == STT_STATUS_LABEL_DISABLED
        assert status.reason == "voice_foundation_disabled"

    def test_disabled_when_input_off(self) -> None:
        status = build_stt_status(_flags(foundation=True, input=False, stt_backend="test-stt"))
        assert status.available is False
        assert status.enabled is False
        assert status.configured is True
        assert status.status == STT_STATUS_LABEL_DISABLED
        assert status.reason == "voice_input_disabled"

    def test_unconfigured_when_no_backend(self) -> None:
        status = build_stt_status(_flags(foundation=True, input=True, stt_backend=None))
        assert status.available is False
        assert status.enabled is True
        assert status.configured is False
        assert status.status == STT_STATUS_LABEL_UNCONFIGURED
        assert status.reason == "voice_stt_backend_not_configured"

    def test_fully_disabled_defaults(self) -> None:
        status = build_stt_status(_flags())
        assert status.available is False
        assert status.enabled is False
        assert status.configured is False
        assert status.status == STT_STATUS_LABEL_DISABLED
        assert status.reason == "voice_foundation_disabled"

    def test_status_is_frozen(self) -> None:
        status = build_stt_status(_flags(foundation=True, input=True, stt_backend="x"))
        with pytest.raises(AttributeError):
            status.available = False  # type: ignore[misc]


# -- truthful unavailable handling ------------------------------------------


class TestSTTUnavailableHandling:
    def test_available_does_not_imply_transcription_works(self) -> None:
        """available=True means configured + enabled, NOT proven transcription."""
        status = build_stt_status(_flags(foundation=True, input=True, stt_backend="stub"))
        assert status.available is True
        assert status.reason is None


# -- dict serialization -----------------------------------------------------


class TestSTTStatusSerialization:
    def test_as_dict_roundtrip(self) -> None:
        status = build_stt_status(_flags(foundation=True, input=True, stt_backend="test-stt"))
        d = stt_status_as_dict(status)
        assert isinstance(d, dict)
        assert d["configured"] is True
        assert d["available"] is True
        assert d["enabled"] is True
        assert d["backend"] == "test-stt"
        assert d["status"] == STT_STATUS_LABEL_AVAILABLE
        assert d["reason"] is None
        assert d["schema_version"] == STT_STATUS_SCHEMA_VERSION
        # field-count guard
        assert len(d) == len(STTStatus.__dataclass_fields__)

    def test_as_dict_json_serializable(self) -> None:
        status = build_stt_status(_flags(foundation=True, input=True, stt_backend="stub"))
        assert isinstance(json.dumps(stt_status_as_dict(status)), str)


# -- integration with flags loader ------------------------------------------


class TestSTTStatusWithFlagsLoader:
    def test_from_config_file(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "enable_voice_foundation": True,
                    "enable_voice_input": True,
                    "voice_stt_backend": "stub-transcriber",
                }
            ),
            encoding="utf-8",
        )
        flags = load_voice_foundation_flags(config_path=config_path, environ={})
        status = build_stt_status(flags)
        assert status.available is True
        assert status.backend == "stub-transcriber"

    def test_from_empty_config(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.json"
        config_path.write_text("{}", encoding="utf-8")
        flags = load_voice_foundation_flags(config_path=config_path, environ={})
        status = build_stt_status(flags)
        assert status.available is False
        assert status.status == STT_STATUS_LABEL_DISABLED


# -- doctor integration -----------------------------------------------------


class TestSTTStatusInDoctor:
    """Verify the STT status check appears in ``run_quick_doctor`` output."""

    @staticmethod
    def _make_queue(tmp_path: Path) -> Path:
        queue_root = tmp_path / "queue"
        (queue_root / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
        for bucket in ("inbox", "done", "failed", "canceled"):
            (queue_root / bucket).mkdir(parents=True, exist_ok=True)
        (queue_root / "health.json").write_text("{}", encoding="utf-8")
        return queue_root

    def test_stt_check_present_in_quick_doctor(self, tmp_path: Path) -> None:
        from voxera.doctor import run_quick_doctor

        queue_root = self._make_queue(tmp_path)
        checks = run_quick_doctor(queue_root=queue_root)
        stt_checks = [c for c in checks if c["check"] == "voice: stt status"]
        assert len(stt_checks) == 1

    def test_stt_check_ok_when_disabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from voxera.doctor import run_quick_doctor

        monkeypatch.setattr(
            _voxera_config, "_DEFAULT_RUNTIME_CONFIG", tmp_path / "voxera_config.json"
        )
        queue_root = self._make_queue(tmp_path)
        checks = run_quick_doctor(queue_root=queue_root)
        stt_check = next(c for c in checks if c["check"] == "voice: stt status")
        assert stt_check["status"] == "ok"
        assert "disabled" in stt_check["detail"]
        assert stt_check["hint"] == ""

    def test_stt_check_warn_when_enabled_but_unconfigured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from voxera.doctor import run_quick_doctor

        monkeypatch.setattr(
            _voxera_config, "_DEFAULT_RUNTIME_CONFIG", tmp_path / "voxera_config.json"
        )
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_FOUNDATION", "1")
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_INPUT", "1")

        queue_root = self._make_queue(tmp_path)
        checks = run_quick_doctor(queue_root=queue_root)
        stt_check = next(c for c in checks if c["check"] == "voice: stt status")
        assert stt_check["status"] == "warn"
        assert "unconfigured" in stt_check["detail"]
        assert "VOXERA_VOICE_STT_BACKEND" in stt_check["hint"]
