"""Tests for operator-selectable local STT backend (whisper/moonshine).

Pins the end-to-end contract for choosing the local STT backend at
runtime:

1. Runtime config persistence of ``voice_stt_backend`` (+ moonshine
   model) through the canonical runtime JSON
2. Panel POST ``/voice/options/save`` accepts valid choices, rejects
   unknown values truthfully, and clears on blank submission
3. ``/voice/status`` HTML + JSON surface the effective STT backend
4. ``voxera doctor --quick`` voice check shows the effective backend
5. ``build_stt_backend`` routes to ``WhisperLocalBackend`` vs
   ``MoonshineLocalBackend`` based on the selector
6. Invalid/missing selection fails truthfully (unknown backend yields
   the NullSTTBackend with a specific reason; no silent fallback)
7. Voice foundation flags accept the new kwargs without breaking
   existing callers

The tests never install moonshine-voice / faster-whisper at runtime —
dependency probes are patched at the seam where necessary.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from voxera import config as _voxera_config
from voxera.config import update_runtime_config
from voxera.doctor import run_quick_doctor
from voxera.panel import app as panel_module
from voxera.voice.flags import VoiceFoundationFlags, load_voice_foundation_flags
from voxera.voice.moonshine_backend import (
    MOONSHINE_MODEL_BASE,
    MOONSHINE_MODEL_TINY,
    STT_MOONSHINE_MODEL_CHOICES,
    MoonshineLocalBackend,
)
from voxera.voice.stt_adapter import NullSTTBackend
from voxera.voice.stt_backend_factory import (
    STT_BACKEND_CHOICES,
    STT_BACKEND_MOONSHINE_LOCAL,
    STT_BACKEND_WHISPER_LOCAL,
    build_stt_backend,
    reset_shared_stt_backend,
)
from voxera.voice.voice_status_summary import (
    VOICE_STATUS_SUMMARY_SCHEMA_VERSION,
    build_voice_status_summary,
)
from voxera.voice.whisper_backend import WhisperLocalBackend

# -- helpers ---------------------------------------------------------------


def _flags(
    *,
    foundation: bool = True,
    input: bool = True,
    output: bool = False,
    stt_backend: str | None = "whisper_local",
    tts_backend: str | None = None,
    stt_whisper_model: str | None = None,
    stt_moonshine_model: str | None = None,
) -> VoiceFoundationFlags:
    return VoiceFoundationFlags(
        enable_voice_foundation=foundation,
        enable_voice_input=input,
        enable_voice_output=output,
        voice_stt_backend=stt_backend,
        voice_tts_backend=tts_backend,
        voice_stt_whisper_model=stt_whisper_model,
        voice_stt_moonshine_model=stt_moonshine_model,
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
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    cfg_path = tmp_path / "voxera_config.json"
    monkeypatch.setattr(_voxera_config, "_DEFAULT_RUNTIME_CONFIG", cfg_path)
    # Always clear the STT shared cache so previous tests' cached
    # Null/Whisper/Moonshine instances can't leak across cases.
    reset_shared_stt_backend()
    return cfg_path


# -- 1. runtime config persistence -----------------------------------------


class TestConfigPersistence:
    def test_flags_load_moonshine_backend_from_runtime_config(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.json"
        cfg.write_text(
            json.dumps(
                {
                    "enable_voice_foundation": True,
                    "enable_voice_input": True,
                    "voice_stt_backend": STT_BACKEND_MOONSHINE_LOCAL,
                    "voice_stt_moonshine_model": MOONSHINE_MODEL_TINY,
                }
            ),
            encoding="utf-8",
        )
        flags = load_voice_foundation_flags(config_path=cfg, environ={})
        assert flags.voice_stt_backend == STT_BACKEND_MOONSHINE_LOCAL
        assert flags.voice_stt_moonshine_model == MOONSHINE_MODEL_TINY

    def test_env_override_wins_over_file_value(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.json"
        cfg.write_text(
            json.dumps(
                {
                    "voice_stt_backend": STT_BACKEND_WHISPER_LOCAL,
                    "voice_stt_moonshine_model": "moonshine/base",
                }
            ),
            encoding="utf-8",
        )
        flags = load_voice_foundation_flags(
            config_path=cfg,
            environ={
                "VOXERA_VOICE_STT_BACKEND": STT_BACKEND_MOONSHINE_LOCAL,
                "VOXERA_VOICE_STT_MOONSHINE_MODEL": MOONSHINE_MODEL_TINY,
            },
        )
        assert flags.voice_stt_backend == STT_BACKEND_MOONSHINE_LOCAL
        assert flags.voice_stt_moonshine_model == MOONSHINE_MODEL_TINY

    def test_missing_keys_default_to_none(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({}), encoding="utf-8")
        flags = load_voice_foundation_flags(config_path=cfg, environ={})
        assert flags.voice_stt_backend is None
        assert flags.voice_stt_moonshine_model is None

    def test_update_runtime_config_writes_and_clears_backend(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.json"
        update_runtime_config({"voice_stt_backend": STT_BACKEND_MOONSHINE_LOCAL}, config_path=cfg)
        payload = json.loads(cfg.read_text(encoding="utf-8"))
        assert payload["voice_stt_backend"] == STT_BACKEND_MOONSHINE_LOCAL

        update_runtime_config({"voice_stt_backend": None}, config_path=cfg)
        cleared = json.loads(cfg.read_text(encoding="utf-8"))
        assert "voice_stt_backend" not in cleared

    def test_backwards_compat_kwargs(self) -> None:
        """Old callers that do not pass the moonshine model still work."""
        flags = VoiceFoundationFlags(
            enable_voice_foundation=True,
            enable_voice_input=True,
            enable_voice_output=False,
            voice_stt_backend=STT_BACKEND_WHISPER_LOCAL,
            voice_tts_backend=None,
        )
        assert flags.voice_stt_moonshine_model is None


# -- 2. panel save endpoint ------------------------------------------------


class TestPanelVoiceOptionsSaveSTTBackend:
    def test_save_accepts_moonshine_backend(self, _panel_env: Path) -> None:
        client = TestClient(panel_module.app)
        res = _authed_csrf_request(
            client,
            "post",
            "/voice/options/save",
            data={"stt_backend": STT_BACKEND_MOONSHINE_LOCAL},
        )
        assert res.status_code == 200
        assert "voice-options-save-ok" in res.text
        payload = json.loads(_panel_env.read_text(encoding="utf-8"))
        assert payload["voice_stt_backend"] == STT_BACKEND_MOONSHINE_LOCAL

    def test_save_accepts_moonshine_model(self, _panel_env: Path) -> None:
        client = TestClient(panel_module.app)
        res = _authed_csrf_request(
            client,
            "post",
            "/voice/options/save",
            data={"stt_moonshine_model": MOONSHINE_MODEL_TINY},
        )
        assert res.status_code == 200
        assert "voice-options-save-ok" in res.text
        payload = json.loads(_panel_env.read_text(encoding="utf-8"))
        assert payload["voice_stt_moonshine_model"] == MOONSHINE_MODEL_TINY

    def test_save_blank_backend_clears_selection(self, _panel_env: Path) -> None:
        client = TestClient(panel_module.app)
        _authed_csrf_request(
            client,
            "post",
            "/voice/options/save",
            data={"stt_backend": STT_BACKEND_MOONSHINE_LOCAL},
        )
        res = _authed_csrf_request(client, "post", "/voice/options/save", data={"stt_backend": ""})
        assert res.status_code == 200
        assert "voice-options-save-ok" in res.text
        payload = json.loads(_panel_env.read_text(encoding="utf-8"))
        assert "voice_stt_backend" not in payload

    def test_save_rejects_unknown_backend(self, _panel_env: Path) -> None:
        client = TestClient(panel_module.app)
        res = _authed_csrf_request(
            client,
            "post",
            "/voice/options/save",
            data={"stt_backend": "cloud_whisper_api"},
        )
        assert res.status_code == 200
        assert "voice-options-save-fail" in res.text
        assert "cloud_whisper_api" in res.text
        assert not _panel_env.exists() or (
            "voice_stt_backend" not in json.loads(_panel_env.read_text(encoding="utf-8"))
        )

    def test_save_rejects_unknown_moonshine_model(self, _panel_env: Path) -> None:
        client = TestClient(panel_module.app)
        res = _authed_csrf_request(
            client,
            "post",
            "/voice/options/save",
            data={"stt_moonshine_model": "moonshine/enormous-v99"},
        )
        assert res.status_code == 200
        assert "voice-options-save-fail" in res.text
        assert "moonshine/enormous-v99" in res.text

    def test_save_stale_client_does_not_clear_untouched_fields(self, _panel_env: Path) -> None:
        """A POST that submits only TTS backend must leave STT backend alone."""
        client = TestClient(panel_module.app)
        # Prime the runtime config with an existing STT backend choice.
        update_runtime_config(
            {"voice_stt_backend": STT_BACKEND_MOONSHINE_LOCAL}, config_path=_panel_env
        )
        res = _authed_csrf_request(
            client,
            "post",
            "/voice/options/save",
            data={"tts_backend": ""},
        )
        assert res.status_code == 200
        payload = json.loads(_panel_env.read_text(encoding="utf-8"))
        # STT backend is preserved because the stale client did not send it.
        assert payload["voice_stt_backend"] == STT_BACKEND_MOONSHINE_LOCAL

    def test_save_accepts_whisper_backend(self, _panel_env: Path) -> None:
        client = TestClient(panel_module.app)
        res = _authed_csrf_request(
            client,
            "post",
            "/voice/options/save",
            data={"stt_backend": STT_BACKEND_WHISPER_LOCAL},
        )
        assert res.status_code == 200
        assert "voice-options-save-ok" in res.text
        payload = json.loads(_panel_env.read_text(encoding="utf-8"))
        assert payload["voice_stt_backend"] == STT_BACKEND_WHISPER_LOCAL


# -- 3. status page shows selected STT backend ------------------------------


class TestVoiceStatusShowsBackend:
    def test_html_status_shows_moonshine_backend(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_FOUNDATION", "1")
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_INPUT", "1")
        monkeypatch.setenv("VOXERA_VOICE_STT_BACKEND", STT_BACKEND_MOONSHINE_LOCAL)
        monkeypatch.setenv("VOXERA_VOICE_STT_MOONSHINE_MODEL", MOONSHINE_MODEL_TINY)
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert "moonshine_local" in res.text
        assert MOONSHINE_MODEL_TINY in res.text
        assert "Moonshine Model" in res.text

    def test_json_status_carries_moonshine_block(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_FOUNDATION", "1")
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_INPUT", "1")
        monkeypatch.setenv("VOXERA_VOICE_STT_BACKEND", STT_BACKEND_MOONSHINE_LOCAL)
        client = TestClient(panel_module.app)
        res = client.get("/voice/status.json", headers=_operator_headers())
        assert res.status_code == 200
        body = res.json()["voice"]
        assert body["stt"]["backend"] == STT_BACKEND_MOONSHINE_LOCAL
        stt_dep = body["stt_dependency"]
        assert "moonshine_model" in stt_dep
        assert stt_dep["moonshine_model"]["selected"] is None
        assert stt_dep["moonshine_model"]["effective"] == MOONSHINE_MODEL_BASE

    def test_json_status_moonshine_selected_model_roundtrip(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_FOUNDATION", "1")
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_INPUT", "1")
        monkeypatch.setenv("VOXERA_VOICE_STT_BACKEND", STT_BACKEND_MOONSHINE_LOCAL)
        monkeypatch.setenv("VOXERA_VOICE_STT_MOONSHINE_MODEL", MOONSHINE_MODEL_TINY)
        client = TestClient(panel_module.app)
        res = client.get("/voice/status.json", headers=_operator_headers())
        mm = res.json()["voice"]["stt_dependency"]["moonshine_model"]
        assert mm["selected"] == MOONSHINE_MODEL_TINY
        assert mm["effective"] == MOONSHINE_MODEL_TINY

    def test_form_lists_both_backend_choices(self, _panel_env: Path) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert 'name="stt_backend"' in res.text
        assert STT_BACKEND_WHISPER_LOCAL in res.text
        assert STT_BACKEND_MOONSHINE_LOCAL in res.text

    def test_schema_version_bumped_for_moonshine_block(self) -> None:
        summary = build_voice_status_summary(_flags(stt_backend=STT_BACKEND_MOONSHINE_LOCAL))
        assert summary["schema_version"] == VOICE_STATUS_SUMMARY_SCHEMA_VERSION
        assert VOICE_STATUS_SUMMARY_SCHEMA_VERSION >= 5

    def test_summary_reports_moonshine_block_only_for_moonshine_backend(self) -> None:
        moonshine_summary = build_voice_status_summary(
            _flags(stt_backend=STT_BACKEND_MOONSHINE_LOCAL)
        )
        assert "moonshine_model" in moonshine_summary["stt_dependency"]

        whisper_summary = build_voice_status_summary(_flags(stt_backend=STT_BACKEND_WHISPER_LOCAL))
        assert "moonshine_model" not in whisper_summary["stt_dependency"]


# -- 4. doctor diagnostics ------------------------------------------------


class TestDoctorSurfacesBackend:
    def test_doctor_stt_detail_shows_moonshine_backend_and_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "voxera_config.json"
        cfg.write_text(
            json.dumps(
                {
                    "enable_voice_foundation": True,
                    "enable_voice_input": True,
                    "voice_stt_backend": STT_BACKEND_MOONSHINE_LOCAL,
                    "voice_stt_moonshine_model": MOONSHINE_MODEL_TINY,
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
        assert f"backend={STT_BACKEND_MOONSHINE_LOCAL}" in stt["detail"]
        assert f"model={MOONSHINE_MODEL_TINY}" in stt["detail"]

    def test_doctor_stt_detail_reports_effective_base_when_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "voxera_config.json"
        cfg.write_text(
            json.dumps(
                {
                    "enable_voice_foundation": True,
                    "enable_voice_input": True,
                    "voice_stt_backend": STT_BACKEND_MOONSHINE_LOCAL,
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
        assert f"model={MOONSHINE_MODEL_BASE}" in stt["detail"]


# -- 5. factory routes to the right backend -------------------------------


class TestFactoryRouting:
    def test_factory_builds_whisper_backend(self) -> None:
        flags = _flags(stt_backend=STT_BACKEND_WHISPER_LOCAL)
        backend = build_stt_backend(flags)
        assert isinstance(backend, WhisperLocalBackend)

    def test_factory_builds_moonshine_backend(self) -> None:
        flags = _flags(stt_backend=STT_BACKEND_MOONSHINE_LOCAL)
        backend = build_stt_backend(flags)
        assert isinstance(backend, MoonshineLocalBackend)

    def test_factory_threads_moonshine_model_into_backend(self) -> None:
        flags = _flags(
            stt_backend=STT_BACKEND_MOONSHINE_LOCAL,
            stt_moonshine_model=MOONSHINE_MODEL_TINY,
        )
        backend = build_stt_backend(flags)
        assert isinstance(backend, MoonshineLocalBackend)
        assert backend._model_name == MOONSHINE_MODEL_TINY

    def test_factory_moonshine_default_model_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VOXERA_VOICE_STT_MOONSHINE_MODEL", raising=False)
        flags = _flags(stt_backend=STT_BACKEND_MOONSHINE_LOCAL)
        backend = build_stt_backend(flags)
        assert isinstance(backend, MoonshineLocalBackend)
        assert backend._model_name == MOONSHINE_MODEL_BASE

    def test_factory_null_when_foundation_disabled(self) -> None:
        flags = _flags(foundation=False, stt_backend=STT_BACKEND_MOONSHINE_LOCAL)
        backend = build_stt_backend(flags)
        assert isinstance(backend, NullSTTBackend)

    def test_unknown_backend_returns_null_with_specific_reason(self) -> None:
        flags = _flags(stt_backend="cloud_engine_v99")
        backend = build_stt_backend(flags)
        assert isinstance(backend, NullSTTBackend)

    def test_backend_choices_list_contains_both(self) -> None:
        assert STT_BACKEND_WHISPER_LOCAL in STT_BACKEND_CHOICES
        assert STT_BACKEND_MOONSHINE_LOCAL in STT_BACKEND_CHOICES

    def test_moonshine_choice_list_contains_known_ids(self) -> None:
        assert MOONSHINE_MODEL_TINY in STT_MOONSHINE_MODEL_CHOICES
        assert MOONSHINE_MODEL_BASE in STT_MOONSHINE_MODEL_CHOICES


# -- 6. truthful dependency reporting -------------------------------------


class TestDependencyReporting:
    def test_missing_moonshine_dep_reports_hint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Patch the probe so we get a deterministic 'missing' state."""
        monkeypatch.setattr(
            "voxera.voice.voice_status_summary._probe_moonshine_package",
            lambda: (False, "moonshine-voice"),
        )
        summary = build_voice_status_summary(_flags(stt_backend=STT_BACKEND_MOONSHINE_LOCAL))
        dep = summary["stt_dependency"]
        assert dep["checked"] is True
        assert dep["available"] is False
        assert dep["package"] == "moonshine-voice"
        assert "moonshine" in (dep.get("hint") or "").lower()
        assert "pip install" in (summary["stt"]["next_step"] or "")

    def test_installed_moonshine_dep_reports_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "voxera.voice.voice_status_summary._probe_moonshine_package",
            lambda: (True, "moonshine-voice"),
        )
        summary = build_voice_status_summary(_flags(stt_backend=STT_BACKEND_MOONSHINE_LOCAL))
        dep = summary["stt_dependency"]
        assert dep["checked"] is True
        assert dep["available"] is True
        assert dep["package"] == "moonshine-voice"
        assert "hint" not in dep
        assert summary["stt"]["next_step"] is None


# -- 7. existing flow unchanged --------------------------------------------


class TestWhisperFlowUnchanged:
    def test_empty_config_still_whisper_compatible(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.json"
        flags = load_voice_foundation_flags(config_path=cfg, environ={})
        assert flags.voice_stt_backend is None
        assert flags.voice_stt_moonshine_model is None
        assert flags.voice_stt_whisper_model is None

    def test_whisper_still_selected_with_legacy_config(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.json"
        cfg.write_text(
            json.dumps(
                {
                    "enable_voice_foundation": True,
                    "enable_voice_input": True,
                    "voice_stt_backend": STT_BACKEND_WHISPER_LOCAL,
                    "voice_stt_whisper_model": "small",
                }
            ),
            encoding="utf-8",
        )
        flags = load_voice_foundation_flags(config_path=cfg, environ={})
        backend = build_stt_backend(flags)
        assert isinstance(backend, WhisperLocalBackend)
        assert backend._model_size == "small"
