"""Tests for the panel voice TTS generation flow.

Pins the operator-facing TTS generation surface: form rendering, canonical
``synthesize_text(...)`` invocation, truthful success/failure rendering,
artifact path display, auth and CSRF enforcement, and JSON endpoint.
"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from voxera.panel import app as panel_module
from voxera.voice.tts_protocol import (
    TTS_STATUS_FAILED,
    TTS_STATUS_SUCCEEDED,
    TTS_STATUS_UNAVAILABLE,
    TTSResponse,
)


def _operator_headers(user: str = "admin", password: str = "secret") -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _authed_csrf_request(client: TestClient, method: str, url: str, *, data: dict[str, str]):
    auth = _operator_headers()
    home = client.get("/", headers=auth)
    assert home.status_code == 200
    csrf = client.cookies.get("voxera_panel_csrf")
    payload = dict(data)
    payload["csrf_token"] = csrf or ""
    return getattr(client, method)(url, data=payload, headers=auth, follow_redirects=False)


@pytest.fixture()
def _panel_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Set up the minimal environment for panel routes to work."""
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")


def _make_tts_response(
    *,
    status: str = TTS_STATUS_SUCCEEDED,
    audio_path: str | None = "/tmp/voxera_tts_test.wav",
    audio_duration_ms: int | None = 1200,
    inference_ms: int | None = 50,
    error: str | None = None,
    error_class: str | None = None,
    backend: str | None = "piper_local",
    request_id: str = "test-req-001",
) -> TTSResponse:
    return TTSResponse(
        request_id=request_id,
        status=status,
        audio_path=audio_path,
        audio_duration_ms=audio_duration_ms,
        error=error,
        error_class=error_class,
        backend=backend,
        started_at_ms=1000,
        finished_at_ms=1050,
        schema_version=1,
        inference_ms=inference_ms,
    )


class TestTTSGenerationFormRendering:
    def test_voice_status_page_renders_tts_generation_form(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert "TTS Generation" in res.text
        assert "Generate Speech" in res.text
        assert 'name="tts_text"' in res.text

    def test_voice_status_page_renders_form_fields(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert 'name="tts_voice_id"' in res.text
        assert 'name="tts_language"' in res.text
        assert 'name="csrf_token"' in res.text

    def test_voice_status_page_has_csrf_cookie(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        client.get("/voice/status", headers=_operator_headers())
        assert client.cookies.get("voxera_panel_csrf")

    def test_form_action_points_to_generate(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert 'action="/voice/tts/generate"' in res.text


class TestTTSGenerationSuccess:
    def test_successful_synthesis_shows_succeeded_badge(self, _panel_env: None) -> None:
        mock_response = _make_tts_response()
        with patch("voxera.panel.routes_voice.synthesize_text", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate",
                data={"tts_text": "Hello world"},
            )
        assert res.status_code == 200
        assert "badge-ok" in res.text
        assert "succeeded" in res.text

    def test_successful_synthesis_shows_audio_path(self, _panel_env: None) -> None:
        mock_response = _make_tts_response(audio_path="/tmp/voxera_tts_test.wav")
        with patch("voxera.panel.routes_voice.synthesize_text", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate",
                data={"tts_text": "Hello world"},
            )
        assert res.status_code == 200
        assert "/tmp/voxera_tts_test.wav" in res.text
        assert "Audio Path" in res.text

    def test_successful_synthesis_shows_backend(self, _panel_env: None) -> None:
        mock_response = _make_tts_response(backend="piper_local")
        with patch("voxera.panel.routes_voice.synthesize_text", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate",
                data={"tts_text": "Hello world"},
            )
        assert res.status_code == 200
        assert "piper_local" in res.text

    def test_successful_synthesis_shows_timing(self, _panel_env: None) -> None:
        mock_response = _make_tts_response(audio_duration_ms=1200, inference_ms=50)
        with patch("voxera.panel.routes_voice.synthesize_text", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate",
                data={"tts_text": "Hello world"},
            )
        assert res.status_code == 200
        assert "1200 ms" in res.text
        assert "50 ms" in res.text

    def test_successful_synthesis_shows_request_id(self, _panel_env: None) -> None:
        mock_response = _make_tts_response(request_id="test-req-001")
        with patch("voxera.panel.routes_voice.synthesize_text", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate",
                data={"tts_text": "Hello world"},
            )
        assert res.status_code == 200
        assert "test-req-001" in res.text

    def test_successful_synthesis_preserves_input_text(self, _panel_env: None) -> None:
        mock_response = _make_tts_response()
        with patch("voxera.panel.routes_voice.synthesize_text", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate",
                data={"tts_text": "Hello world"},
            )
        assert res.status_code == 200
        assert "Hello world" in res.text

    def test_successful_synthesis_includes_raw_response(self, _panel_env: None) -> None:
        mock_response = _make_tts_response()
        with patch("voxera.panel.routes_voice.synthesize_text", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate",
                data={"tts_text": "Hello world"},
            )
        assert res.status_code == 200
        assert "Raw TTS response" in res.text


class TestTTSGenerationFailure:
    def test_unavailable_shows_failure_badge(self, _panel_env: None) -> None:
        mock_response = _make_tts_response(
            status=TTS_STATUS_UNAVAILABLE,
            audio_path=None,
            audio_duration_ms=None,
            inference_ms=None,
            error="No TTS backend is configured",
            error_class="backend_missing",
            backend="null",
        )
        with patch("voxera.panel.routes_voice.synthesize_text", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate",
                data={"tts_text": "Hello world"},
            )
        assert res.status_code == 200
        assert "badge-fail" in res.text
        assert "badge-ok" not in res.text

    def test_unavailable_shows_error_message(self, _panel_env: None) -> None:
        mock_response = _make_tts_response(
            status=TTS_STATUS_UNAVAILABLE,
            audio_path=None,
            audio_duration_ms=None,
            inference_ms=None,
            error="No TTS backend is configured",
            error_class="backend_missing",
            backend="null",
        )
        with patch("voxera.panel.routes_voice.synthesize_text", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate",
                data={"tts_text": "Hello world"},
            )
        assert res.status_code == 200
        assert "No TTS backend is configured" in res.text
        assert "backend_missing" in res.text

    def test_unavailable_does_not_show_audio_path(self, _panel_env: None) -> None:
        mock_response = _make_tts_response(
            status=TTS_STATUS_UNAVAILABLE,
            audio_path=None,
            audio_duration_ms=None,
            inference_ms=None,
            error="Voice output disabled",
            error_class="backend_missing",
        )
        with patch("voxera.panel.routes_voice.synthesize_text", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate",
                data={"tts_text": "Hello world"},
            )
        assert res.status_code == 200
        assert "Audio Path" not in res.text

    def test_failed_status_shows_failure(self, _panel_env: None) -> None:
        mock_response = _make_tts_response(
            status=TTS_STATUS_FAILED,
            audio_path=None,
            audio_duration_ms=None,
            inference_ms=None,
            error="Piper synthesis failed: model not found",
            error_class="backend_error",
            backend="piper_local",
        )
        with patch("voxera.panel.routes_voice.synthesize_text", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate",
                data={"tts_text": "Hello world"},
            )
        assert res.status_code == 200
        assert "badge-fail" in res.text
        assert "Piper synthesis failed" in res.text

    def test_empty_text_shows_error(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = _authed_csrf_request(
            client,
            "post",
            "/voice/tts/generate",
            data={"tts_text": ""},
        )
        assert res.status_code == 200
        assert "badge-fail" in res.text
        assert "Text input is required" in res.text

    def test_whitespace_only_text_shows_error(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = _authed_csrf_request(
            client,
            "post",
            "/voice/tts/generate",
            data={"tts_text": "   "},
        )
        assert res.status_code == 200
        assert "badge-fail" in res.text
        assert "Text input is required" in res.text


class TestTTSGenerationNoFakeSuccess:
    def test_no_fake_success_when_audio_path_missing(self, _panel_env: None) -> None:
        """A response with succeeded status but no audio_path must NOT show success."""
        mock_response = _make_tts_response(
            status=TTS_STATUS_SUCCEEDED,
            audio_path=None,
            audio_duration_ms=None,
            inference_ms=None,
        )
        with patch("voxera.panel.routes_voice.synthesize_text", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate",
                data={"tts_text": "Hello world"},
            )
        assert res.status_code == 200
        # Must NOT show success badge when audio_path is missing
        assert "badge-ok" not in res.text
        assert "Audio Path" not in res.text

    def test_no_fake_success_when_status_not_succeeded(self, _panel_env: None) -> None:
        mock_response = _make_tts_response(
            status=TTS_STATUS_FAILED,
            audio_path=None,
            audio_duration_ms=None,
            inference_ms=None,
            error="synthesis error",
            error_class="backend_error",
        )
        with patch("voxera.panel.routes_voice.synthesize_text", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate",
                data={"tts_text": "Hello world"},
            )
        assert res.status_code == 200
        assert "badge-ok" not in res.text


class TestTTSGenerationCallsCanonicalPath:
    def test_form_calls_synthesize_text(self, _panel_env: None) -> None:
        mock_response = _make_tts_response()
        with patch(
            "voxera.panel.routes_voice.synthesize_text", return_value=mock_response
        ) as mock_synth:
            client = TestClient(panel_module.app)
            _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate",
                data={"tts_text": "Test speech"},
            )
        mock_synth.assert_called_once()
        call_kwargs = mock_synth.call_args
        assert call_kwargs.kwargs["text"] == "Test speech"

    def test_voice_id_passed_through(self, _panel_env: None) -> None:
        mock_response = _make_tts_response()
        with patch(
            "voxera.panel.routes_voice.synthesize_text", return_value=mock_response
        ) as mock_synth:
            client = TestClient(panel_module.app)
            _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate",
                data={"tts_text": "Test speech", "tts_voice_id": "custom_voice"},
            )
        assert mock_synth.call_args.kwargs["voice_id"] == "custom_voice"

    def test_language_passed_through(self, _panel_env: None) -> None:
        mock_response = _make_tts_response()
        with patch(
            "voxera.panel.routes_voice.synthesize_text", return_value=mock_response
        ) as mock_synth:
            client = TestClient(panel_module.app)
            _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate",
                data={"tts_text": "Test speech", "tts_language": "de"},
            )
        assert mock_synth.call_args.kwargs["language"] == "de"

    def test_empty_voice_id_passed_as_none(self, _panel_env: None) -> None:
        mock_response = _make_tts_response()
        with patch(
            "voxera.panel.routes_voice.synthesize_text", return_value=mock_response
        ) as mock_synth:
            client = TestClient(panel_module.app)
            _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate",
                data={"tts_text": "Test speech", "tts_voice_id": ""},
            )
        assert mock_synth.call_args.kwargs["voice_id"] is None

    def test_empty_language_passed_as_none(self, _panel_env: None) -> None:
        mock_response = _make_tts_response()
        with patch(
            "voxera.panel.routes_voice.synthesize_text", return_value=mock_response
        ) as mock_synth:
            client = TestClient(panel_module.app)
            _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate",
                data={"tts_text": "Test speech", "tts_language": ""},
            )
        assert mock_synth.call_args.kwargs["language"] is None


class TestTTSGenerationAuth:
    def test_generate_requires_auth(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app, raise_server_exceptions=False)
        res = client.post("/voice/tts/generate", data={"tts_text": "Hello"})
        assert res.status_code == 401

    def test_generate_requires_csrf(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app, raise_server_exceptions=False)
        res = client.post(
            "/voice/tts/generate",
            headers=_operator_headers(),
            data={"tts_text": "Hello"},
            follow_redirects=False,
        )
        assert res.status_code == 403

    def test_json_generate_requires_auth(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app, raise_server_exceptions=False)
        res = client.post("/voice/tts/generate.json", data={"tts_text": "Hello"})
        assert res.status_code == 401

    def test_json_generate_requires_csrf(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app, raise_server_exceptions=False)
        res = client.post(
            "/voice/tts/generate.json",
            headers=_operator_headers(),
            data={"tts_text": "Hello"},
            follow_redirects=False,
        )
        assert res.status_code == 403


class TestTTSGenerationJSON:
    def test_json_endpoint_success(self, _panel_env: None) -> None:
        mock_response = _make_tts_response()
        with patch("voxera.panel.routes_voice.synthesize_text", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate.json",
                data={"tts_text": "Hello world"},
            )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert "tts" in data
        assert data["tts"]["status"] == "succeeded"
        assert data["tts"]["audio_path"] == "/tmp/voxera_tts_test.wav"

    def test_json_endpoint_failure(self, _panel_env: None) -> None:
        mock_response = _make_tts_response(
            status=TTS_STATUS_UNAVAILABLE,
            audio_path=None,
            audio_duration_ms=None,
            inference_ms=None,
            error="No TTS backend is configured",
            error_class="backend_missing",
            backend="null",
        )
        with patch("voxera.panel.routes_voice.synthesize_text", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate.json",
                data={"tts_text": "Hello world"},
            )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is False
        assert data["tts"]["status"] == "unavailable"
        assert data["tts"]["error"] == "No TTS backend is configured"

    def test_json_endpoint_empty_text(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = _authed_csrf_request(
            client,
            "post",
            "/voice/tts/generate.json",
            data={"tts_text": ""},
        )
        assert res.status_code == 400
        data = res.json()
        assert data["ok"] is False
        assert "required" in data["error"].lower()

    def test_json_endpoint_serializable(self, _panel_env: None) -> None:
        mock_response = _make_tts_response()
        with patch("voxera.panel.routes_voice.synthesize_text", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate.json",
                data={"tts_text": "Hello world"},
            )
        import json

        data = res.json()
        reserialized = json.dumps(data)
        assert isinstance(reserialized, str)


class TestExistingVoiceStatusPreserved:
    """Verify that existing voice status routes still work after adding TTS generation."""

    def test_voice_status_page_still_renders(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert "Voice Status" in res.text
        assert "Voice Foundation" in res.text
        assert "Speech-to-Text" in res.text
        assert "Text-to-Speech" in res.text

    def test_voice_status_json_still_works(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status.json", headers=_operator_headers())
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert "voice" in data

    def test_voice_status_page_auth_still_enforced(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app, raise_server_exceptions=False)
        res = client.get("/voice/status")
        assert res.status_code == 401

    def test_voice_status_json_auth_still_enforced(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app, raise_server_exceptions=False)
        res = client.get("/voice/status.json")
        assert res.status_code == 401

    def test_json_link_still_present(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert "/voice/status.json" in res.text

    def test_no_fake_ready_when_disabled(self, _panel_env: None) -> None:
        """When voice is disabled, no green 'available' badge should appear."""
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert "badge-ok" not in res.text

    def test_config_not_runtime_note(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert "config state only" in res.text.lower()
