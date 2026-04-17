"""Tests for the panel voice status routes.

Pins the operator-facing voice status panel page and JSON endpoint:
HTML rendering for disabled/enabled/configured states, JSON shape,
truthful status display, and auth enforcement.
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
def _panel_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Set up the minimal environment for panel routes to work."""
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    monkeypatch.setattr(_voxera_config, "_DEFAULT_RUNTIME_CONFIG", tmp_path / "voxera_config.json")


class TestVoiceStatusPage:
    def test_voice_status_page_renders(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert "Voice Status" in res.text

    def test_voice_status_page_shows_foundation_state(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert "Voice Foundation" in res.text

    def test_voice_status_page_shows_stt_section(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert "Speech-to-Text" in res.text

    def test_voice_status_page_shows_tts_section(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert "Text-to-Speech" in res.text

    def test_voice_status_page_shows_disabled_when_foundation_off(self, _panel_env: None) -> None:
        """Default config has voice disabled; page should show disabled badges."""
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert "disabled" in res.text

    def test_voice_status_page_shows_json_link(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert "/voice/status.json" in res.text

    def test_voice_status_page_shows_enabled_when_configured(
        self, _panel_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_FOUNDATION", "1")
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_INPUT", "1")
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_OUTPUT", "1")
        monkeypatch.setenv("VOXERA_VOICE_STT_BACKEND", "whisper_local")
        monkeypatch.setenv("VOXERA_VOICE_TTS_BACKEND", "piper_local")

        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert "available" in res.text
        assert "whisper_local" in res.text
        assert "piper_local" in res.text

    def test_voice_status_page_no_fake_ready(self, _panel_env: None) -> None:
        """When voice is disabled, no green 'available' badge should appear."""
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        # badge-ok is the green status badge; must not appear when fully disabled
        assert "badge-ok" not in res.text

    def test_voice_status_page_shows_config_not_runtime_note(self, _panel_env: None) -> None:
        """Page must clarify that status reflects config, not runtime readiness."""
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert "config state only" in res.text.lower()

    def test_voice_status_link_in_home_nav(self, _panel_env: None) -> None:
        """Voice Status must appear in the panel home navigation."""
        client = TestClient(panel_module.app)
        res = client.get("/", headers=_operator_headers())
        assert res.status_code == 200
        assert "/voice/status" in res.text
        assert "Voice Status" in res.text


class TestVoiceStatusJSON:
    def test_json_endpoint_returns_ok(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status.json", headers=_operator_headers())
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert "voice" in data

    def test_json_endpoint_shape(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status.json", headers=_operator_headers())
        data = res.json()
        voice = data["voice"]
        assert "voice_foundation_enabled" in voice
        assert "stt" in voice
        assert "tts" in voice
        assert "stt_dependency" in voice
        assert "tts_dependency" in voice
        assert "schema_version" in voice

    def test_json_stt_fields(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status.json", headers=_operator_headers())
        stt = res.json()["voice"]["stt"]
        assert "configured" in stt
        assert "available" in stt
        assert "enabled" in stt
        assert "backend" in stt
        assert "status" in stt
        assert "reason" in stt

    def test_json_tts_fields(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status.json", headers=_operator_headers())
        tts = res.json()["voice"]["tts"]
        assert "configured" in tts
        assert "available" in tts
        assert "enabled" in tts
        assert "backend" in tts
        assert "status" in tts
        assert "reason" in tts
        assert "last_error" in tts

    def test_json_disabled_state(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status.json", headers=_operator_headers())
        voice = res.json()["voice"]
        assert voice["voice_foundation_enabled"] is False
        assert voice["stt"]["available"] is False
        assert voice["tts"]["available"] is False

    def test_json_configured_state(self, _panel_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_FOUNDATION", "1")
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_INPUT", "1")
        monkeypatch.setenv("VOXERA_ENABLE_VOICE_OUTPUT", "1")
        monkeypatch.setenv("VOXERA_VOICE_STT_BACKEND", "whisper_local")
        monkeypatch.setenv("VOXERA_VOICE_TTS_BACKEND", "piper_local")

        client = TestClient(panel_module.app)
        res = client.get("/voice/status.json", headers=_operator_headers())
        voice = res.json()["voice"]
        assert voice["voice_foundation_enabled"] is True
        assert voice["stt"]["available"] is True
        assert voice["stt"]["backend"] == "whisper_local"
        assert voice["tts"]["available"] is True
        assert voice["tts"]["backend"] == "piper_local"

    def test_json_serializable(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status.json", headers=_operator_headers())
        data = res.json()
        reserialized = json.dumps(data)
        assert isinstance(reserialized, str)


class TestVoiceStatusErrorPath:
    def test_html_renders_error_when_flags_fail(
        self, _panel_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When load_voice_foundation_flags raises, the page renders the error alert."""
        import voxera.panel.routes_voice as rv

        monkeypatch.setattr(
            rv,
            "load_voice_foundation_flags",
            lambda: (_ for _ in ()).throw(RuntimeError("bad config")),
        )
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert "Failed to load voice status" in res.text
        assert "RuntimeError" in res.text
        # Should not show any status sections
        assert "badge-ok" not in res.text

    def test_json_returns_500_when_flags_fail(
        self, _panel_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When load_voice_foundation_flags raises, the JSON endpoint returns 500."""
        import voxera.panel.routes_voice as rv

        monkeypatch.setattr(
            rv,
            "load_voice_foundation_flags",
            lambda: (_ for _ in ()).throw(RuntimeError("bad config")),
        )
        client = TestClient(panel_module.app)
        res = client.get("/voice/status.json", headers=_operator_headers())
        assert res.status_code == 500
        data = res.json()
        assert data["ok"] is False
        assert "RuntimeError" in data["error"]


class TestVoiceStatusAuth:
    def test_voice_status_page_requires_auth(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app, raise_server_exceptions=False)
        res = client.get("/voice/status")
        assert res.status_code == 401

    def test_voice_status_json_requires_auth(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app, raise_server_exceptions=False)
        res = client.get("/voice/status.json")
        assert res.status_code == 401
