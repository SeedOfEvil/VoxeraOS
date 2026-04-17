"""Tests for the panel voice STT transcription flow.

Pins the operator-facing STT transcription surface: form rendering, canonical
``transcribe_audio_file(...)`` invocation, truthful success/failure rendering,
transcript display, auth and CSRF enforcement, and JSON endpoint.
"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from voxera import config as _voxera_config
from voxera.panel import app as panel_module
from voxera.voice.stt_protocol import (
    STT_STATUS_FAILED,
    STT_STATUS_SUCCEEDED,
    STT_STATUS_UNAVAILABLE,
    STTResponse,
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
    monkeypatch.setattr(_voxera_config, "_DEFAULT_RUNTIME_CONFIG", tmp_path / "voxera_config.json")


def _make_stt_response(
    *,
    status: str = STT_STATUS_SUCCEEDED,
    transcript: str | None = "Hello world from audio",
    language: str | None = "en",
    audio_duration_ms: int | None = 3500,
    inference_ms: int | None = 150,
    error: str | None = None,
    error_class: str | None = None,
    backend: str | None = "whisper_local",
    request_id: str = "test-stt-001",
) -> STTResponse:
    return STTResponse(
        request_id=request_id,
        status=status,
        transcript=transcript,
        language=language,
        audio_duration_ms=audio_duration_ms,
        error=error,
        error_class=error_class,
        backend=backend,
        started_at_ms=1000,
        finished_at_ms=1150,
        schema_version=1,
        inference_ms=inference_ms,
    )


class TestSTTTranscriptionFormRendering:
    def test_voice_status_page_renders_stt_transcription_form(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert "STT Transcription" in res.text
        assert "Transcribe Audio" in res.text
        assert 'name="stt_audio_path"' in res.text

    def test_voice_status_page_renders_stt_form_fields(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert 'name="stt_language"' in res.text
        assert 'name="csrf_token"' in res.text

    def test_stt_form_action_points_to_transcribe(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert 'action="/voice/stt/transcribe"' in res.text

    def test_stt_form_is_file_oriented(self, _panel_env: None) -> None:
        """Form should accept a file path, not microphone or stream."""
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert "Audio File Path" in res.text
        assert "no microphone" in res.text.lower()


class TestSTTTranscriptionSuccess:
    def test_successful_transcription_shows_succeeded_badge(self, _panel_env: None) -> None:
        mock_response = _make_stt_response()
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe",
                data={"stt_audio_path": "/tmp/test.wav"},
            )
        assert res.status_code == 200
        assert "badge-ok" in res.text
        assert "succeeded" in res.text

    def test_successful_transcription_shows_transcript(self, _panel_env: None) -> None:
        mock_response = _make_stt_response(transcript="Hello world from audio")
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe",
                data={"stt_audio_path": "/tmp/test.wav"},
            )
        assert res.status_code == 200
        assert "Hello world from audio" in res.text
        assert "Transcript" in res.text

    def test_successful_transcription_shows_detected_language(self, _panel_env: None) -> None:
        mock_response = _make_stt_response(language="en")
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe",
                data={"stt_audio_path": "/tmp/test.wav"},
            )
        assert res.status_code == 200
        assert "Detected Language" in res.text

    def test_successful_transcription_shows_backend(self, _panel_env: None) -> None:
        mock_response = _make_stt_response(backend="whisper_local")
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe",
                data={"stt_audio_path": "/tmp/test.wav"},
            )
        assert res.status_code == 200
        assert "whisper_local" in res.text

    def test_successful_transcription_shows_timing(self, _panel_env: None) -> None:
        mock_response = _make_stt_response(audio_duration_ms=3500, inference_ms=150)
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe",
                data={"stt_audio_path": "/tmp/test.wav"},
            )
        assert res.status_code == 200
        assert "3500 ms" in res.text
        assert "150 ms" in res.text

    def test_successful_transcription_shows_request_id(self, _panel_env: None) -> None:
        mock_response = _make_stt_response(request_id="test-stt-001")
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe",
                data={"stt_audio_path": "/tmp/test.wav"},
            )
        assert res.status_code == 200
        assert "test-stt-001" in res.text

    def test_successful_transcription_preserves_input_path(self, _panel_env: None) -> None:
        mock_response = _make_stt_response()
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe",
                data={"stt_audio_path": "/tmp/test.wav"},
            )
        assert res.status_code == 200
        assert "/tmp/test.wav" in res.text

    def test_successful_transcription_includes_raw_response(self, _panel_env: None) -> None:
        mock_response = _make_stt_response()
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe",
                data={"stt_audio_path": "/tmp/test.wav"},
            )
        assert res.status_code == 200
        assert "Raw STT response" in res.text


class TestSTTTranscriptionFailure:
    def test_unavailable_shows_failure_badge(self, _panel_env: None) -> None:
        mock_response = _make_stt_response(
            status=STT_STATUS_UNAVAILABLE,
            transcript=None,
            language=None,
            audio_duration_ms=None,
            inference_ms=None,
            error="No STT backend is configured",
            error_class="backend_missing",
            backend="null",
        )
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe",
                data={"stt_audio_path": "/tmp/test.wav"},
            )
        assert res.status_code == 200
        assert "badge-fail" in res.text
        assert "badge-ok" not in res.text

    def test_unavailable_shows_error_message(self, _panel_env: None) -> None:
        mock_response = _make_stt_response(
            status=STT_STATUS_UNAVAILABLE,
            transcript=None,
            language=None,
            audio_duration_ms=None,
            inference_ms=None,
            error="No STT backend is configured",
            error_class="backend_missing",
            backend="null",
        )
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe",
                data={"stt_audio_path": "/tmp/test.wav"},
            )
        assert res.status_code == 200
        assert "No STT backend is configured" in res.text
        assert "backend_missing" in res.text

    def test_unavailable_does_not_show_transcript(self, _panel_env: None) -> None:
        mock_response = _make_stt_response(
            status=STT_STATUS_UNAVAILABLE,
            transcript=None,
            language=None,
            audio_duration_ms=None,
            inference_ms=None,
            error="Voice input disabled",
            error_class="backend_missing",
        )
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe",
                data={"stt_audio_path": "/tmp/test.wav"},
            )
        assert res.status_code == 200
        # No "Transcript" label should appear on failure
        assert ">Transcript<" not in res.text

    def test_failed_status_shows_failure(self, _panel_env: None) -> None:
        mock_response = _make_stt_response(
            status=STT_STATUS_FAILED,
            transcript=None,
            language=None,
            audio_duration_ms=None,
            inference_ms=None,
            error="Audio file not found: /tmp/missing.wav",
            error_class="backend_error",
            backend="whisper_local",
        )
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe",
                data={"stt_audio_path": "/tmp/missing.wav"},
            )
        assert res.status_code == 200
        assert "badge-fail" in res.text
        assert "Audio file not found" in res.text

    def test_empty_path_shows_error(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = _authed_csrf_request(
            client,
            "post",
            "/voice/stt/transcribe",
            data={"stt_audio_path": ""},
        )
        assert res.status_code == 200
        assert "badge-fail" in res.text
        assert "Audio file path is required" in res.text

    def test_whitespace_only_path_shows_error(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = _authed_csrf_request(
            client,
            "post",
            "/voice/stt/transcribe",
            data={"stt_audio_path": "   "},
        )
        assert res.status_code == 200
        assert "badge-fail" in res.text
        assert "Audio file path is required" in res.text

    def test_unexpected_exception_shows_error_not_crash(self, _panel_env: None) -> None:
        """When transcribe_audio_file raises unexpectedly, page renders error, not 500."""
        with patch(
            "voxera.panel.routes_voice.transcribe_audio_file",
            side_effect=RuntimeError("unexpected segfault"),
        ):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe",
                data={"stt_audio_path": "/tmp/test.wav"},
            )
        assert res.status_code == 200
        assert "badge-fail" in res.text
        assert "Unexpected error" in res.text
        assert "segfault" in res.text
        assert "badge-ok" not in res.text


class TestSTTTranscriptionConfigFailure:
    def test_flags_load_failure_shows_error_not_crash(
        self, _panel_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When load_voice_foundation_flags raises during POST, page renders error, not 500."""
        import voxera.panel.routes_voice as rv

        monkeypatch.setattr(
            rv,
            "load_voice_foundation_flags",
            lambda: (_ for _ in ()).throw(RuntimeError("bad config")),
        )
        client = TestClient(panel_module.app)
        res = _authed_csrf_request(
            client,
            "post",
            "/voice/stt/transcribe",
            data={"stt_audio_path": "/tmp/test.wav"},
        )
        assert res.status_code == 200
        assert "Failed to load voice status" in res.text
        assert "RuntimeError" in res.text
        assert "badge-ok" not in res.text

    def test_flags_load_failure_no_transcribe_call(
        self, _panel_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When flags fail to load, transcribe_audio_file must NOT be called."""
        import voxera.panel.routes_voice as rv

        monkeypatch.setattr(
            rv,
            "load_voice_foundation_flags",
            lambda: (_ for _ in ()).throw(RuntimeError("bad config")),
        )
        with patch("voxera.panel.routes_voice.transcribe_audio_file") as mock_transcribe:
            client = TestClient(panel_module.app)
            _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe",
                data={"stt_audio_path": "/tmp/test.wav"},
            )
        mock_transcribe.assert_not_called()

    def test_flags_load_failure_json_returns_500(
        self, _panel_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """JSON endpoint returns 500 when flags fail to load."""
        import voxera.panel.routes_voice as rv

        monkeypatch.setattr(
            rv,
            "load_voice_foundation_flags",
            lambda: (_ for _ in ()).throw(RuntimeError("bad config")),
        )
        client = TestClient(panel_module.app)
        res = _authed_csrf_request(
            client,
            "post",
            "/voice/stt/transcribe.json",
            data={"stt_audio_path": "/tmp/test.wav"},
        )
        assert res.status_code == 500
        data = res.json()
        assert data["ok"] is False
        assert "RuntimeError" in data["error"]


class TestSTTTranscriptionNoFakeSuccess:
    def test_no_fake_success_when_transcript_missing(self, _panel_env: None) -> None:
        """A response with succeeded status but no transcript must NOT show success."""
        mock_response = _make_stt_response(
            status=STT_STATUS_SUCCEEDED,
            transcript=None,
            language=None,
            audio_duration_ms=None,
            inference_ms=None,
        )
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe",
                data={"stt_audio_path": "/tmp/test.wav"},
            )
        assert res.status_code == 200
        assert "badge-ok" not in res.text
        assert ">Transcript<" not in res.text

    def test_no_fake_success_when_status_not_succeeded(self, _panel_env: None) -> None:
        mock_response = _make_stt_response(
            status=STT_STATUS_FAILED,
            transcript=None,
            language=None,
            audio_duration_ms=None,
            inference_ms=None,
            error="transcription error",
            error_class="backend_error",
        )
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe",
                data={"stt_audio_path": "/tmp/test.wav"},
            )
        assert res.status_code == 200
        assert "badge-ok" not in res.text


class TestSTTTranscriptionCallsCanonicalPath:
    def test_form_calls_transcribe_audio_file(self, _panel_env: None) -> None:
        mock_response = _make_stt_response()
        with patch(
            "voxera.panel.routes_voice.transcribe_audio_file", return_value=mock_response
        ) as mock_transcribe:
            client = TestClient(panel_module.app)
            _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe",
                data={"stt_audio_path": "/tmp/test.wav"},
            )
        mock_transcribe.assert_called_once()
        call_kwargs = mock_transcribe.call_args
        assert call_kwargs.kwargs["audio_path"] == "/tmp/test.wav"

    def test_language_passed_through(self, _panel_env: None) -> None:
        mock_response = _make_stt_response()
        with patch(
            "voxera.panel.routes_voice.transcribe_audio_file", return_value=mock_response
        ) as mock_transcribe:
            client = TestClient(panel_module.app)
            _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe",
                data={"stt_audio_path": "/tmp/test.wav", "stt_language": "de"},
            )
        assert mock_transcribe.call_args.kwargs["language"] == "de"

    def test_empty_language_passed_as_none(self, _panel_env: None) -> None:
        mock_response = _make_stt_response()
        with patch(
            "voxera.panel.routes_voice.transcribe_audio_file", return_value=mock_response
        ) as mock_transcribe:
            client = TestClient(panel_module.app)
            _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe",
                data={"stt_audio_path": "/tmp/test.wav", "stt_language": ""},
            )
        assert mock_transcribe.call_args.kwargs["language"] is None


class TestSTTTranscriptionAuth:
    def test_transcribe_requires_auth(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app, raise_server_exceptions=False)
        res = client.post("/voice/stt/transcribe", data={"stt_audio_path": "/tmp/test.wav"})
        assert res.status_code == 401

    def test_transcribe_requires_csrf(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app, raise_server_exceptions=False)
        res = client.post(
            "/voice/stt/transcribe",
            headers=_operator_headers(),
            data={"stt_audio_path": "/tmp/test.wav"},
            follow_redirects=False,
        )
        assert res.status_code == 403

    def test_json_transcribe_requires_auth(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app, raise_server_exceptions=False)
        res = client.post("/voice/stt/transcribe.json", data={"stt_audio_path": "/tmp/test.wav"})
        assert res.status_code == 401

    def test_json_transcribe_requires_csrf(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app, raise_server_exceptions=False)
        res = client.post(
            "/voice/stt/transcribe.json",
            headers=_operator_headers(),
            data={"stt_audio_path": "/tmp/test.wav"},
            follow_redirects=False,
        )
        assert res.status_code == 403


class TestSTTTranscriptionJSON:
    def test_json_endpoint_success(self, _panel_env: None) -> None:
        mock_response = _make_stt_response()
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe.json",
                data={"stt_audio_path": "/tmp/test.wav"},
            )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert "stt" in data
        assert data["stt"]["status"] == "succeeded"
        assert data["stt"]["transcript"] == "Hello world from audio"

    def test_json_endpoint_failure(self, _panel_env: None) -> None:
        mock_response = _make_stt_response(
            status=STT_STATUS_UNAVAILABLE,
            transcript=None,
            language=None,
            audio_duration_ms=None,
            inference_ms=None,
            error="No STT backend is configured",
            error_class="backend_missing",
            backend="null",
        )
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe.json",
                data={"stt_audio_path": "/tmp/test.wav"},
            )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is False
        assert data["stt"]["status"] == "unavailable"
        assert data["stt"]["error"] == "No STT backend is configured"

    def test_json_endpoint_empty_path(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = _authed_csrf_request(
            client,
            "post",
            "/voice/stt/transcribe.json",
            data={"stt_audio_path": ""},
        )
        assert res.status_code == 400
        data = res.json()
        assert data["ok"] is False
        assert "required" in data["error"].lower()

    def test_json_endpoint_serializable(self, _panel_env: None) -> None:
        mock_response = _make_stt_response()
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe.json",
                data={"stt_audio_path": "/tmp/test.wav"},
            )
        import json

        data = res.json()
        reserialized = json.dumps(data)
        assert isinstance(reserialized, str)

    def test_json_endpoint_unexpected_exception(self, _panel_env: None) -> None:
        """JSON endpoint returns 500 on unexpected pipeline exception."""
        with patch(
            "voxera.panel.routes_voice.transcribe_audio_file",
            side_effect=RuntimeError("unexpected crash"),
        ):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe.json",
                data={"stt_audio_path": "/tmp/test.wav"},
            )
        assert res.status_code == 500
        data = res.json()
        assert data["ok"] is False
        assert "RuntimeError" in data["error"]


class TestExistingVoiceSurfacesPreserved:
    """Verify that existing voice status and TTS routes still work after adding STT transcription."""

    def test_voice_status_page_still_renders(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert "Voice Status" in res.text
        assert "Voice Foundation" in res.text
        assert "Speech-to-Text" in res.text
        assert "Text-to-Speech" in res.text
        assert "TTS Generation" in res.text
        assert "STT Transcription" in res.text

    def test_voice_status_json_still_works(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status.json", headers=_operator_headers())
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert "voice" in data

    def test_tts_form_still_present(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert 'name="tts_text"' in res.text
        assert "Generate Speech" in res.text

    def test_json_section_mentions_stt_endpoint(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert "/voice/stt/transcribe.json" in res.text

    def test_no_fake_ready_when_disabled(self, _panel_env: None) -> None:
        """When voice is disabled, no green 'available' badge should appear."""
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert "badge-ok" not in res.text
