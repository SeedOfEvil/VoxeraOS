"""Tests for voice-aware diagnostics in ``voxera doctor --quick``.

Pins the brand-new-user friendly messaging: each voice failure mode
(foundation disabled, STT/TTS disabled, backend unconfigured, backend
dependency missing, Piper model file missing, Piper model metadata
missing) produces a *distinct*, actionable hint rather than a generic
"warn".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voxera.doctor import run_quick_doctor


def _queue_root(tmp_path: Path) -> Path:
    queue_root = tmp_path / "queue"
    (queue_root / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
    (queue_root / "health.json").write_text(
        json.dumps({"last_ok_event": "daemon_tick", "last_ok_ts_ms": 100000}),
        encoding="utf-8",
    )
    return queue_root


def _stt_check(checks: list[dict[str, str]]) -> dict[str, str]:
    return next(c for c in checks if c["check"] == "voice: stt status")


def _tts_check(checks: list[dict[str, str]]) -> dict[str, str]:
    return next(c for c in checks if c["check"] == "voice: tts status")


def _point_flags_at(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``load_voice_foundation_flags`` to read from a fresh tmp config."""
    cfg_path = tmp_path / "voxera_config.json"
    from voxera import config as _voxera_config

    monkeypatch.setattr(_voxera_config, "_DEFAULT_RUNTIME_CONFIG", cfg_path)
    return cfg_path


class TestFoundationDisabled:
    def test_foundation_disabled_yields_setup_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _point_flags_at(tmp_path, monkeypatch)
        checks = run_quick_doctor(queue_root=_queue_root(tmp_path))
        stt = _stt_check(checks)
        tts = _tts_check(checks)
        # Disabled-by-config is an intentional state, not a warning.
        assert stt["status"] == "ok"
        assert tts["status"] == "ok"
        # Hints still guide the brand-new operator to run setup.
        assert "voxera setup" in stt["hint"]
        assert "voxera setup" in tts["hint"]


class TestEnabledButUnconfigured:
    def test_stt_enabled_no_backend_hints_at_backend_choice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _point_flags_at(tmp_path, monkeypatch)
        cfg.write_text(
            json.dumps(
                {
                    "enable_voice_foundation": True,
                    "enable_voice_input": True,
                }
            ),
            encoding="utf-8",
        )
        checks = run_quick_doctor(queue_root=_queue_root(tmp_path))
        stt = _stt_check(checks)
        assert stt["status"] == "warn"
        assert "STT backend" in stt["hint"]

    def test_tts_enabled_no_backend_hints_at_backend_choice(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _point_flags_at(tmp_path, monkeypatch)
        cfg.write_text(
            json.dumps(
                {
                    "enable_voice_foundation": True,
                    "enable_voice_output": True,
                }
            ),
            encoding="utf-8",
        )
        checks = run_quick_doctor(queue_root=_queue_root(tmp_path))
        tts = _tts_check(checks)
        assert tts["status"] == "warn"
        assert "TTS backend" in tts["hint"]


class TestPiperModelDiagnostics:
    def test_piper_missing_file_hint_is_specific(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _point_flags_at(tmp_path, monkeypatch)
        missing_model = tmp_path / "nonexistent_voice.onnx"
        cfg.write_text(
            json.dumps(
                {
                    "enable_voice_foundation": True,
                    "enable_voice_output": True,
                    "voice_tts_backend": "piper_local",
                    "voice_tts_piper_model": str(missing_model),
                }
            ),
            encoding="utf-8",
        )
        checks = run_quick_doctor(queue_root=_queue_root(tmp_path))
        tts = _tts_check(checks)
        # If piper is installed, the hint must point at the missing model.
        # If piper is not installed, the dep-missing hint takes precedence.
        # Both paths are acceptable; the key point is the hint is specific,
        # not a generic "TTS enabled but foo".
        assert tts["hint"]
        assert any(
            token in tts["hint"]
            for token in ("Piper model path does not exist", "pip install voxera-os[piper]")
        )

    def test_piper_metadata_missing_hint_is_specific(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _point_flags_at(tmp_path, monkeypatch)
        model = tmp_path / "voice.onnx"
        model.write_bytes(b"")
        # intentionally no voice.onnx.json sidecar
        cfg.write_text(
            json.dumps(
                {
                    "enable_voice_foundation": True,
                    "enable_voice_output": True,
                    "voice_tts_backend": "piper_local",
                    "voice_tts_piper_model": str(model),
                }
            ),
            encoding="utf-8",
        )
        checks = run_quick_doctor(queue_root=_queue_root(tmp_path))
        tts = _tts_check(checks)
        assert tts["hint"]
        # Either metadata-missing (piper installed) or dep-missing (not installed).
        assert any(
            token in tts["hint"]
            for token in ("metadata file is missing", "pip install voxera-os[piper]")
        )


class TestDoctorVoiceChecksDistinctHints:
    """Regression: disabled, unconfigured, and dep-missing must NOT collapse to the same hint."""

    def test_disabled_and_unconfigured_hints_differ(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _point_flags_at(tmp_path, monkeypatch)
        # disabled state
        cfg.write_text(json.dumps({}), encoding="utf-8")
        disabled_checks = run_quick_doctor(queue_root=_queue_root(tmp_path))
        disabled_hint = _tts_check(disabled_checks)["hint"]

        # unconfigured-but-enabled state
        cfg.write_text(
            json.dumps({"enable_voice_foundation": True, "enable_voice_output": True}),
            encoding="utf-8",
        )
        unconf_checks = run_quick_doctor(queue_root=_queue_root(tmp_path))
        unconf_hint = _tts_check(unconf_checks)["hint"]

        assert disabled_hint != unconf_hint
        assert disabled_hint  # both are non-empty — both direct the operator somewhere
        assert unconf_hint
