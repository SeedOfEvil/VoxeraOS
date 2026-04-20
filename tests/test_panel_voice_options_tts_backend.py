"""Tests for the panel Voice Options TTS backend selector.

Pins the operator-selectable TTS backend lane:
- the voice options save lane accepts the TTS backend field and
  persists it to runtime config
- unrecognized backends fail truthfully and do not reach config
- empty / ``default`` clears the runtime config value
- the voice status page renders the TTS backend select with the
  curated choices
- after a successful save the effective backend is shown on the page
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from voxera import config as _voxera_config
from voxera.panel import app as panel_module


def _operator_headers(user: str = "admin", password: str = "secret") -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}"}


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
    return cfg_path


def _csrf_headers(client: TestClient) -> dict[str, str]:
    """Grab a CSRF token so the save POST passes the mutation guard."""
    res = client.get("/voice/status", headers=_operator_headers())
    assert res.status_code == 200
    token = client.cookies.get("voxera_panel_csrf")
    assert token
    return {
        **_operator_headers(),
        "x-csrf-token": token,
    }


class TestVoiceOptionsTTSBackendForm:
    """The voice options form surfaces the TTS backend selector."""

    def test_form_renders_tts_backend_select(self, _panel_env: Path) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert 'name="tts_backend"' in res.text
        # Both curated choices are exposed in the dropdown.
        assert "piper_local" in res.text
        assert "kokoro_local" in res.text

    def test_form_shows_current_tts_backend_when_set(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_FOUNDATION", "1")
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_OUTPUT", "1")
        monkeypatch.setenv("VOXERA_VOICE_TTS_BACKEND", "kokoro_local")
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert 'data-testid="voice-options-current-tts-backend"' in res.text


class TestVoiceOptionsSavePersistsTTSBackend:
    """Saving through the panel persists the TTS backend truthfully."""

    def test_save_valid_kokoro_backend(self, _panel_env: Path) -> None:
        cfg_path = _panel_env
        client = TestClient(panel_module.app)
        headers = _csrf_headers(client)
        res = client.post(
            "/voice/options/save",
            headers=headers,
            data={"stt_whisper_model": "", "tts_backend": "kokoro_local"},
        )
        assert res.status_code == 200
        assert "Voice options saved" in res.text
        assert "kokoro_local" in res.text
        # Persisted truthfully in the JSON file
        saved = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert saved["voice_tts_backend"] == "kokoro_local"

    def test_save_valid_piper_backend(self, _panel_env: Path) -> None:
        cfg_path = _panel_env
        client = TestClient(panel_module.app)
        headers = _csrf_headers(client)
        res = client.post(
            "/voice/options/save",
            headers=headers,
            data={"stt_whisper_model": "", "tts_backend": "piper_local"},
        )
        assert res.status_code == 200
        assert "Voice options saved" in res.text
        saved = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert saved["voice_tts_backend"] == "piper_local"

    def test_save_empty_clears_backend(self, _panel_env: Path) -> None:
        cfg_path = _panel_env
        cfg_path.write_text(json.dumps({"voice_tts_backend": "piper_local"}), encoding="utf-8")
        client = TestClient(panel_module.app)
        headers = _csrf_headers(client)
        res = client.post(
            "/voice/options/save",
            headers=headers,
            data={"stt_whisper_model": "", "tts_backend": ""},
        )
        assert res.status_code == 200
        # ``update_runtime_config`` treats None as "remove this key"; the
        # file must not carry the key once cleared.
        saved = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert "voice_tts_backend" not in saved

    def test_save_rejects_unknown_backend(self, _panel_env: Path) -> None:
        cfg_path = _panel_env
        client = TestClient(panel_module.app)
        headers = _csrf_headers(client)
        res = client.post(
            "/voice/options/save",
            headers=headers,
            data={"stt_whisper_model": "", "tts_backend": "google_cloud_tts"},
        )
        assert res.status_code == 200
        assert "voice-options-save-fail" in res.text
        assert "google_cloud_tts" in res.text
        # Nothing was persisted for the rejected value
        if cfg_path.exists():
            saved = json.loads(cfg_path.read_text(encoding="utf-8"))
            assert saved.get("voice_tts_backend") != "google_cloud_tts"

    def test_save_survives_round_trip_through_flags(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Selection is visible to ``load_voice_foundation_flags`` after save."""
        cfg_path = _panel_env
        client = TestClient(panel_module.app)
        headers = _csrf_headers(client)
        client.post(
            "/voice/options/save",
            headers=headers,
            data={"stt_whisper_model": "", "tts_backend": "kokoro_local"},
        )
        from voxera.voice.flags import load_voice_foundation_flags

        # Ensure env does not override config for this check.
        monkeypatch.delenv("VOXERA_VOICE_TTS_BACKEND", raising=False)
        flags = load_voice_foundation_flags(config_path=cfg_path, environ={})
        assert flags.voice_tts_backend == "kokoro_local"


class TestVoiceOptionsSaveDoesNotCascadeClear:
    """Regression: a stale client must not silently clear unrelated fields.

    Pre-Kokoro the form only carried ``stt_whisper_model``.  A cached
    browser page (or an automation script) that predates the TTS
    selector and POSTs only the STT field must NOT wipe an existing
    TTS backend selection.  The hardened save lane only touches the
    fields actually submitted.
    """

    def test_stt_only_submission_preserves_existing_tts_backend(self, _panel_env: Path) -> None:
        cfg_path = _panel_env
        cfg_path.write_text(
            json.dumps({"voice_tts_backend": "kokoro_local"}),
            encoding="utf-8",
        )
        client = TestClient(panel_module.app)
        headers = _csrf_headers(client)
        # Simulate a pre-TTS-selector client: submit ONLY stt_whisper_model.
        res = client.post(
            "/voice/options/save",
            headers=headers,
            data={"stt_whisper_model": ""},
        )
        assert res.status_code == 200
        saved = json.loads(cfg_path.read_text(encoding="utf-8"))
        # Existing Kokoro selection must survive an STT-only save.
        assert saved["voice_tts_backend"] == "kokoro_local"

    def test_tts_only_submission_preserves_existing_stt_model(self, _panel_env: Path) -> None:
        cfg_path = _panel_env
        cfg_path.write_text(
            json.dumps({"voice_stt_whisper_model": "distil-large-v3"}),
            encoding="utf-8",
        )
        client = TestClient(panel_module.app)
        headers = _csrf_headers(client)
        # Submit only the TTS field.
        res = client.post(
            "/voice/options/save",
            headers=headers,
            data={"tts_backend": "kokoro_local"},
        )
        assert res.status_code == 200
        saved = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert saved["voice_tts_backend"] == "kokoro_local"
        # Existing STT model selection must survive a TTS-only save.
        assert saved["voice_stt_whisper_model"] == "distil-large-v3"

    def test_empty_submission_leaves_both_keys_untouched(self, _panel_env: Path) -> None:
        cfg_path = _panel_env
        cfg_path.write_text(
            json.dumps(
                {
                    "voice_tts_backend": "piper_local",
                    "voice_stt_whisper_model": "distil-large-v3",
                }
            ),
            encoding="utf-8",
        )
        client = TestClient(panel_module.app)
        headers = _csrf_headers(client)
        # No fields at all — treat as a no-op save, not a clear-everything.
        res = client.post("/voice/options/save", headers=headers, data={})
        assert res.status_code == 200
        saved = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert saved["voice_tts_backend"] == "piper_local"
        assert saved["voice_stt_whisper_model"] == "distil-large-v3"


class TestVoiceOptionsKokoroPathsSurfaceInStatus:
    """Regression: saving Kokoro backend should make Kokoro paths surface
    in ``/voice/status`` when the operator also provides them via env."""

    def test_kokoro_paths_from_env_flow_into_status(
        self,
        _panel_env: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        model_path = tmp_path / "kokoro.onnx"
        voices_path = tmp_path / "voices.bin"
        model_path.write_bytes(b"")
        voices_path.write_bytes(b"")
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_FOUNDATION", "1")
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_OUTPUT", "1")
        monkeypatch.setenv("VOXERA_VOICE_TTS_BACKEND", "kokoro_local")
        monkeypatch.setenv("VOXERA_VOICE_TTS_KOKORO_MODEL", str(model_path))
        monkeypatch.setenv("VOXERA_VOICE_TTS_KOKORO_VOICES", str(voices_path))
        monkeypatch.setenv("VOXERA_VOICE_TTS_KOKORO_VOICE", "am_michael")

        client = TestClient(panel_module.app)
        res = client.get("/voice/status.json", headers=_operator_headers())
        assert res.status_code == 200
        voice = res.json()["voice"]
        assert voice["tts"]["backend"] == "kokoro_local"
        km = voice["tts_dependency"]["kokoro_model"]
        assert km["configured"] is True
        assert km["model_exists"] is True
        assert km["voices_exists"] is True
        assert km["effective_voice"] == "am_michael"
