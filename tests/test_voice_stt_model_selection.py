"""Tests for operator-selectable local STT (Distil-Whisper) model selection.

Pins the end-to-end contract for choosing the local faster-whisper model
id at runtime:

1. Runtime config persistence of ``voice_stt_whisper_model``
2. Panel POST ``/voice/options/save`` accepts valid choices, rejects
   unknown values truthfully, and clears on blank/``default`` input
3. ``/voice/status`` HTML surfaces the selected/effective model
4. ``voxera doctor --quick`` STT detail row shows the effective model
5. ``build_stt_backend`` threads the operator's selection into the
   ``WhisperLocalBackend`` constructor
6. Invalid/missing configuration fails truthfully (no silent fallback
   that hides operator-selected values)
7. Existing voice flows are unchanged when no operator selection is
   persisted (default model id ``base`` stays effective)

The tests never touch the network, never load real models, and never
install ``faster-whisper`` at runtime; the dependency guard is patched
where the surface needs it so the tests run deterministically regardless
of the host environment.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from voxera import config as _voxera_config
from voxera.config import update_runtime_config
from voxera.doctor import run_quick_doctor
from voxera.panel import app as panel_module
from voxera.voice.flags import VoiceFoundationFlags, load_voice_foundation_flags
from voxera.voice.stt_adapter import NullSTTBackend
from voxera.voice.stt_backend_factory import build_stt_backend
from voxera.voice.voice_status_summary import (
    VOICE_STATUS_SUMMARY_SCHEMA_VERSION,
    build_voice_status_summary,
)
from voxera.voice.whisper_backend import (
    STT_WHISPER_MODEL_CHOICES,
    WHISPER_MODEL_BASE,
    WHISPER_MODEL_DISTIL_LARGE_V3,
    WhisperLocalBackend,
)

# -- helpers ---------------------------------------------------------------


def _flags(
    *,
    foundation: bool = True,
    input: bool = True,
    output: bool = False,
    stt_backend: str | None = "whisper_local",
    tts_backend: str | None = None,
    stt_whisper_model: str | None = None,
) -> VoiceFoundationFlags:
    return VoiceFoundationFlags(
        enable_voice_foundation=foundation,
        enable_voice_input=input,
        enable_voice_output=output,
        voice_stt_backend=stt_backend,
        voice_tts_backend=tts_backend,
        voice_stt_whisper_model=stt_whisper_model,
    )


def _operator_headers(user: str = "admin", password: str = "secret") -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _authed_csrf_request(
    client: TestClient,
    method: str,
    url: str,
    *,
    data: dict[str, str],
):
    auth = _operator_headers()
    home = client.get("/", headers=auth)
    assert home.status_code == 200
    csrf = client.cookies.get("voxera_panel_csrf")
    payload = dict(data)
    payload["csrf_token"] = csrf or ""
    return getattr(client, method)(url, data=payload, headers=auth, follow_redirects=False)


@pytest.fixture()
def _panel_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Minimal panel environment; returns the runtime config path."""
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    cfg_path = tmp_path / "voxera_config.json"
    monkeypatch.setattr(_voxera_config, "_DEFAULT_RUNTIME_CONFIG", cfg_path)
    return cfg_path


# -- 1. runtime config persistence -----------------------------------------


class TestConfigPersistence:
    def test_flags_load_distil_whisper_from_runtime_config(self, tmp_path: Path) -> None:
        """Runtime config JSON ``voice_stt_whisper_model`` round-trips into flags."""
        cfg = tmp_path / "config.json"
        cfg.write_text(
            json.dumps(
                {
                    "enable_voice_foundation": True,
                    "enable_voice_input": True,
                    "voice_stt_backend": "whisper_local",
                    "voice_stt_whisper_model": WHISPER_MODEL_DISTIL_LARGE_V3,
                }
            ),
            encoding="utf-8",
        )
        flags = load_voice_foundation_flags(config_path=cfg, environ={})
        assert flags.voice_stt_whisper_model == WHISPER_MODEL_DISTIL_LARGE_V3

    def test_env_override_wins_over_file_value(self, tmp_path: Path) -> None:
        """``VOXERA_VOICE_STT_WHISPER_MODEL`` env overrides the file value."""
        cfg = tmp_path / "config.json"
        cfg.write_text(
            json.dumps({"voice_stt_whisper_model": "small"}),
            encoding="utf-8",
        )
        flags = load_voice_foundation_flags(
            config_path=cfg,
            environ={"VOXERA_VOICE_STT_WHISPER_MODEL": WHISPER_MODEL_DISTIL_LARGE_V3},
        )
        assert flags.voice_stt_whisper_model == WHISPER_MODEL_DISTIL_LARGE_V3

    def test_missing_key_defaults_to_none(self, tmp_path: Path) -> None:
        """A config without the key leaves ``voice_stt_whisper_model`` as None."""
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({}), encoding="utf-8")
        flags = load_voice_foundation_flags(config_path=cfg, environ={})
        assert flags.voice_stt_whisper_model is None

    def test_update_runtime_config_writes_and_clears_selection(self, tmp_path: Path) -> None:
        """``update_runtime_config`` persists the model and clears on None."""
        cfg = tmp_path / "config.json"
        update_runtime_config(
            {"voice_stt_whisper_model": WHISPER_MODEL_DISTIL_LARGE_V3},
            config_path=cfg,
        )
        payload = json.loads(cfg.read_text(encoding="utf-8"))
        assert payload["voice_stt_whisper_model"] == WHISPER_MODEL_DISTIL_LARGE_V3

        update_runtime_config({"voice_stt_whisper_model": None}, config_path=cfg)
        cleared = json.loads(cfg.read_text(encoding="utf-8"))
        assert "voice_stt_whisper_model" not in cleared


# -- 2. panel save endpoint -----------------------------------------------


class TestPanelVoiceOptionsSave:
    def test_save_accepts_distil_whisper(self, _panel_env: Path) -> None:
        client = TestClient(panel_module.app)
        res = _authed_csrf_request(
            client,
            "post",
            "/voice/options/save",
            data={"stt_whisper_model": WHISPER_MODEL_DISTIL_LARGE_V3},
        )
        assert res.status_code == 200
        assert "voice-options-save-ok" in res.text
        assert WHISPER_MODEL_DISTIL_LARGE_V3 in res.text
        # Persisted on disk
        payload = json.loads(_panel_env.read_text(encoding="utf-8"))
        assert payload["voice_stt_whisper_model"] == WHISPER_MODEL_DISTIL_LARGE_V3

    def test_save_blank_clears_selection(self, _panel_env: Path) -> None:
        client = TestClient(panel_module.app)
        # First set a value
        _authed_csrf_request(
            client,
            "post",
            "/voice/options/save",
            data={"stt_whisper_model": WHISPER_MODEL_DISTIL_LARGE_V3},
        )
        # Then clear it with a blank submission
        res = _authed_csrf_request(
            client,
            "post",
            "/voice/options/save",
            data={"stt_whisper_model": ""},
        )
        assert res.status_code == 200
        assert "voice-options-save-ok" in res.text
        payload = json.loads(_panel_env.read_text(encoding="utf-8"))
        assert "voice_stt_whisper_model" not in payload

    def test_save_rejects_unknown_model_truthfully(self, _panel_env: Path) -> None:
        client = TestClient(panel_module.app)
        res = _authed_csrf_request(
            client,
            "post",
            "/voice/options/save",
            data={"stt_whisper_model": "definitely-not-a-real-model"},
        )
        assert res.status_code == 200
        assert "voice-options-save-fail" in res.text
        assert "definitely-not-a-real-model" in res.text
        # Nothing persisted
        assert not _panel_env.exists() or (
            "voice_stt_whisper_model" not in json.loads(_panel_env.read_text(encoding="utf-8"))
        )

    def test_save_requires_csrf(self, _panel_env: Path) -> None:
        client = TestClient(panel_module.app, raise_server_exceptions=False)
        res = client.post(
            "/voice/options/save",
            data={"stt_whisper_model": WHISPER_MODEL_DISTIL_LARGE_V3},
            headers=_operator_headers(),
            follow_redirects=False,
        )
        # Missing CSRF is rejected (403 from mutation guard)
        assert res.status_code in (400, 403)

    def test_save_requires_auth(self, _panel_env: Path) -> None:
        client = TestClient(panel_module.app, raise_server_exceptions=False)
        res = client.post(
            "/voice/options/save",
            data={"stt_whisper_model": WHISPER_MODEL_DISTIL_LARGE_V3},
            follow_redirects=False,
        )
        assert res.status_code == 401


# -- 3. status page shows selected STT model -------------------------------


class TestVoiceStatusShowsModel:
    def test_html_status_shows_effective_model(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_FOUNDATION", "1")
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_INPUT", "1")
        monkeypatch.setenv("VOXERA_VOICE_STT_BACKEND", "whisper_local")
        monkeypatch.setenv("VOXERA_VOICE_STT_WHISPER_MODEL", WHISPER_MODEL_DISTIL_LARGE_V3)
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert "Whisper Model" in res.text
        assert WHISPER_MODEL_DISTIL_LARGE_V3 in res.text

    def test_html_status_shows_default_when_unselected(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_FOUNDATION", "1")
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_INPUT", "1")
        monkeypatch.setenv("VOXERA_VOICE_STT_BACKEND", "whisper_local")
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert WHISPER_MODEL_BASE in res.text
        assert "(default)" in res.text

    def test_json_status_carries_whisper_model_block(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_FOUNDATION", "1")
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_INPUT", "1")
        monkeypatch.setenv("VOXERA_VOICE_STT_BACKEND", "whisper_local")
        monkeypatch.setenv("VOXERA_VOICE_STT_WHISPER_MODEL", WHISPER_MODEL_DISTIL_LARGE_V3)
        client = TestClient(panel_module.app)
        res = client.get("/voice/status.json", headers=_operator_headers())
        assert res.status_code == 200
        stt_dep = res.json()["voice"]["stt_dependency"]
        assert stt_dep["whisper_model"]["selected"] == WHISPER_MODEL_DISTIL_LARGE_V3
        assert stt_dep["whisper_model"]["effective"] == WHISPER_MODEL_DISTIL_LARGE_V3

    def test_json_status_whisper_model_effective_defaults_to_base(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_FOUNDATION", "1")
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_INPUT", "1")
        monkeypatch.setenv("VOXERA_VOICE_STT_BACKEND", "whisper_local")
        client = TestClient(panel_module.app)
        res = client.get("/voice/status.json", headers=_operator_headers())
        assert res.status_code == 200
        stt_dep = res.json()["voice"]["stt_dependency"]
        assert stt_dep["whisper_model"]["selected"] is None
        assert stt_dep["whisper_model"]["effective"] == WHISPER_MODEL_BASE

    def test_form_lists_distil_whisper_choice(self, _panel_env: Path) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert 'name="stt_whisper_model"' in res.text
        assert WHISPER_MODEL_DISTIL_LARGE_V3 in res.text

    def test_summary_schema_version_pins_whisper_model_block(self) -> None:
        """Schema version must be >= 3 for the whisper_model sub-dict contract."""
        summary = build_voice_status_summary(
            _flags(stt_whisper_model=WHISPER_MODEL_DISTIL_LARGE_V3)
        )
        assert summary["schema_version"] == VOICE_STATUS_SUMMARY_SCHEMA_VERSION
        assert VOICE_STATUS_SUMMARY_SCHEMA_VERSION >= 3


# -- 4. doctor diagnostics -------------------------------------------------


class TestDoctorSurfacesModel:
    def test_doctor_stt_detail_shows_selected_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "voxera_config.json"
        cfg.write_text(
            json.dumps(
                {
                    "enable_voice_foundation": True,
                    "enable_voice_input": True,
                    "voice_stt_backend": "whisper_local",
                    "voice_stt_whisper_model": WHISPER_MODEL_DISTIL_LARGE_V3,
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(_voxera_config, "_DEFAULT_RUNTIME_CONFIG", cfg)
        queue_root = tmp_path / "queue"
        (queue_root / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
        (queue_root / "health.json").write_text(
            json.dumps({"last_ok_event": "daemon_tick", "last_ok_ts_ms": 100_000}),
            encoding="utf-8",
        )
        checks = run_quick_doctor(queue_root=queue_root)
        stt = next(c for c in checks if c["check"] == "voice: stt status")
        assert f"model={WHISPER_MODEL_DISTIL_LARGE_V3}" in stt["detail"]

    def test_doctor_stt_detail_shows_effective_base_by_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "voxera_config.json"
        cfg.write_text(
            json.dumps(
                {
                    "enable_voice_foundation": True,
                    "enable_voice_input": True,
                    "voice_stt_backend": "whisper_local",
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(_voxera_config, "_DEFAULT_RUNTIME_CONFIG", cfg)
        queue_root = tmp_path / "queue"
        (queue_root / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
        (queue_root / "health.json").write_text(
            json.dumps({"last_ok_event": "daemon_tick", "last_ok_ts_ms": 100_000}),
            encoding="utf-8",
        )
        checks = run_quick_doctor(queue_root=queue_root)
        stt = next(c for c in checks if c["check"] == "voice: stt status")
        assert f"model={WHISPER_MODEL_BASE}" in stt["detail"]


# -- 5. factory threads selection into backend -----------------------------


class TestFactoryThreadsSelection:
    def test_factory_passes_distil_whisper_to_backend(self) -> None:
        """build_stt_backend threads the operator selection into the ctor."""
        flags = _flags(stt_whisper_model=WHISPER_MODEL_DISTIL_LARGE_V3)
        backend = build_stt_backend(flags)
        assert isinstance(backend, WhisperLocalBackend)
        assert backend._model_size == WHISPER_MODEL_DISTIL_LARGE_V3

    def test_factory_uses_env_default_when_unselected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no operator selection, backend picks up its own default."""
        monkeypatch.delenv("VOXERA_VOICE_STT_WHISPER_MODEL", raising=False)
        flags = _flags(stt_whisper_model=None)
        backend = build_stt_backend(flags)
        assert isinstance(backend, WhisperLocalBackend)
        assert backend._model_size == WHISPER_MODEL_BASE

    def test_factory_selection_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Operator selection wins over a stray env default."""
        monkeypatch.setenv("VOXERA_VOICE_STT_WHISPER_MODEL", "tiny")
        flags = _flags(stt_whisper_model=WHISPER_MODEL_DISTIL_LARGE_V3)
        backend = build_stt_backend(flags)
        assert isinstance(backend, WhisperLocalBackend)
        assert backend._model_size == WHISPER_MODEL_DISTIL_LARGE_V3

    def test_factory_whitespace_only_selection_treated_as_none(self) -> None:
        """Whitespace-only selection falls back to the backend default."""
        flags = _flags(stt_whisper_model="   ")
        backend = build_stt_backend(flags)
        assert isinstance(backend, WhisperLocalBackend)
        assert backend._model_size == WHISPER_MODEL_BASE

    def test_factory_null_backend_when_input_disabled(self) -> None:
        """Even with a model id set, a disabled foundation yields the null backend."""
        flags = _flags(foundation=False, stt_whisper_model=WHISPER_MODEL_DISTIL_LARGE_V3)
        backend = build_stt_backend(flags)
        assert isinstance(backend, NullSTTBackend)


# -- 6. invalid/missing model fails truthfully -----------------------------


class TestInvalidConfigFailsTruthfully:
    def test_backend_reports_load_failure_when_model_id_invalid(self, tmp_path: Path) -> None:
        """A bogus model id propagates the loader error as backend_error."""
        from voxera.voice.stt_protocol import (
            STT_ERROR_BACKEND_ERROR,
            build_stt_request,
        )

        audio_file = tmp_path / "sample.wav"
        audio_file.write_bytes(b"fake-audio-data")

        backend = WhisperLocalBackend(model_size="totally-not-a-model")
        req = build_stt_request(
            input_source="audio_file",
            request_id="invalid-model",
            audio_path=str(audio_file),
        )
        with (
            patch("voxera.voice.whisper_backend._FASTER_WHISPER_AVAILABLE", True),
            patch.object(
                backend,
                "_ensure_model",
                side_effect=OSError("model repo not found"),
            ),
        ):
            result = backend.transcribe(req)
        assert result.transcript is None
        assert result.error_class == STT_ERROR_BACKEND_ERROR
        assert "failed to load" in (result.error or "").lower()

    def test_panel_save_rejects_model_not_in_choices(self, _panel_env: Path) -> None:
        client = TestClient(panel_module.app)
        res = _authed_csrf_request(
            client,
            "post",
            "/voice/options/save",
            data={"stt_whisper_model": "whisper-enormous-v42"},
        )
        assert res.status_code == 200
        assert "voice-options-save-fail" in res.text
        assert "Unrecognized" in res.text or "whisper-enormous-v42" in res.text

    def test_distil_whisper_is_in_the_public_choice_list(self) -> None:
        assert WHISPER_MODEL_DISTIL_LARGE_V3 in STT_WHISPER_MODEL_CHOICES


# -- 7. existing flow unchanged when config not touched --------------------


class TestDefaultFlowUnchanged:
    def test_default_flags_have_no_model_selection(self, tmp_path: Path) -> None:
        """A brand-new install (empty config) carries no model selection."""
        cfg = tmp_path / "config.json"
        flags = load_voice_foundation_flags(config_path=cfg, environ={})
        assert flags.voice_stt_whisper_model is None

    def test_default_backend_still_selects_base(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without any override, the whisper backend defaults to 'base'."""
        monkeypatch.delenv("VOXERA_VOICE_STT_WHISPER_MODEL", raising=False)
        flags = _flags(stt_whisper_model=None)
        backend = build_stt_backend(flags)
        assert isinstance(backend, WhisperLocalBackend)
        assert backend._model_size == WHISPER_MODEL_BASE

    def test_status_summary_effective_model_is_base_by_default(self) -> None:
        summary = build_voice_status_summary(_flags(stt_whisper_model=None))
        stt_dep = summary["stt_dependency"]
        assert stt_dep["whisper_model"]["selected"] is None
        assert stt_dep["whisper_model"]["effective"] == WHISPER_MODEL_BASE

    def test_voice_foundation_flags_accept_old_kwargs_without_model(self) -> None:
        """Backwards-compat: old callers that don't pass the new field still work."""
        flags = VoiceFoundationFlags(
            enable_voice_foundation=True,
            enable_voice_input=True,
            enable_voice_output=False,
            voice_stt_backend="whisper_local",
            voice_tts_backend=None,
        )
        assert flags.voice_stt_whisper_model is None
