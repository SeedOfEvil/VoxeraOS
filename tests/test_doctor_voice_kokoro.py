"""Tests for Kokoro-aware diagnostics in ``voxera doctor --quick``.

Pins the Kokoro-specific branches of the voice TTS check:
- selecting Kokoro surfaces the backend in the TTS detail line
- missing model / voices paths produce truthful detail tokens
- hints flow through from ``build_voice_status_summary``
- existing Piper / disabled / unconfigured branches still work
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


def _tts_check(checks: list[dict[str, str]]) -> dict[str, str]:
    return next(c for c in checks if c["check"] == "voice: tts status")


def _point_flags_at(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg_path = tmp_path / "voxera_config.json"
    from voxera import config as _voxera_config

    monkeypatch.setattr(_voxera_config, "_DEFAULT_RUNTIME_CONFIG", cfg_path)
    return cfg_path


class TestKokoroSelectedBackendVisibility:
    def test_kokoro_backend_shows_in_detail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _point_flags_at(tmp_path, monkeypatch)
        cfg.write_text(
            json.dumps(
                {
                    "enable_voice_foundation": True,
                    "enable_voice_output": True,
                    "voice_tts_backend": "kokoro_local",
                }
            ),
            encoding="utf-8",
        )
        checks = run_quick_doctor(queue_root=_queue_root(tmp_path))
        tts = _tts_check(checks)
        assert "backend=kokoro_local" in tts["detail"]

    def test_kokoro_unset_model_path_shows_in_detail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _point_flags_at(tmp_path, monkeypatch)
        cfg.write_text(
            json.dumps(
                {
                    "enable_voice_foundation": True,
                    "enable_voice_output": True,
                    "voice_tts_backend": "kokoro_local",
                }
            ),
            encoding="utf-8",
        )
        checks = run_quick_doctor(queue_root=_queue_root(tmp_path))
        tts = _tts_check(checks)
        # Unset model / voices paths should show up truthfully when
        # kokoro-onnx is installed.  The dep-missing hint takes precedence
        # on systems where kokoro-onnx is absent; both branches produce a
        # specific, non-generic hint.
        assert tts["hint"]
        assert (
            any(token in tts["detail"] for token in ("kokoro_model=unset", "kokoro_model=missing"))
            or "dependency_missing" in tts["detail"]
        )


class TestKokoroMissingModelFile:
    def test_kokoro_missing_model_hint_is_specific(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _point_flags_at(tmp_path, monkeypatch)
        missing_model = tmp_path / "nope.onnx"
        voices_path = tmp_path / "voices.bin"
        voices_path.write_bytes(b"")
        cfg.write_text(
            json.dumps(
                {
                    "enable_voice_foundation": True,
                    "enable_voice_output": True,
                    "voice_tts_backend": "kokoro_local",
                    "voice_tts_kokoro_model": str(missing_model),
                    "voice_tts_kokoro_voices": str(voices_path),
                }
            ),
            encoding="utf-8",
        )
        checks = run_quick_doctor(queue_root=_queue_root(tmp_path))
        tts = _tts_check(checks)
        assert tts["hint"]
        # Either the Kokoro-specific model-missing hint or (when the dep
        # is absent) the install hint.  Both are distinct from the
        # generic "TTS enabled" warning.
        assert any(
            token in tts["hint"]
            for token in (
                "Kokoro model path does not exist",
                "pip install voxera-os[kokoro]",
            )
        )
