"""Tests for the voice page UI/UX polish pass.

Pins the structural improvements, CSS class landmarks, result ergonomics,
and accessibility additions introduced by the voice page polish PR.
Does not test backend behavior — only presentation and template structure.
"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from voxera.panel import app as panel_module
from voxera.voice.stt_protocol import STT_STATUS_SUCCEEDED, STTResponse
from voxera.voice.tts_protocol import TTS_STATUS_SUCCEEDED, TTSResponse


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
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")


def _make_tts_response(
    *,
    audio_path: str | None = "/tmp/voxera_tts_test.wav",
    audio_duration_ms: int | None = 1200,
    inference_ms: int | None = 50,
) -> TTSResponse:
    return TTSResponse(
        request_id="test-req-001",
        status=TTS_STATUS_SUCCEEDED,
        audio_path=audio_path,
        audio_duration_ms=audio_duration_ms,
        error=None,
        error_class=None,
        backend="piper_local",
        started_at_ms=1000,
        finished_at_ms=1050,
        schema_version=1,
        inference_ms=inference_ms,
    )


def _make_stt_response(
    *,
    transcript: str | None = "Hello world from audio",
    language: str | None = "en",
    audio_duration_ms: int | None = 3500,
    inference_ms: int | None = 150,
) -> STTResponse:
    return STTResponse(
        request_id="test-stt-001",
        status=STT_STATUS_SUCCEEDED,
        transcript=transcript,
        language=language,
        audio_duration_ms=audio_duration_ms,
        error=None,
        error_class=None,
        backend="whisper_local",
        started_at_ms=1000,
        finished_at_ms=1150,
        schema_version=1,
        inference_ms=inference_ms,
    )


class TestVoicePageAccessibility:
    """Pins skip-link and main landmark presence matching other panel pages."""

    def test_skip_link_present(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert 'class="skip-link"' in res.text
        assert "#main-content" in res.text

    def test_main_landmark_present(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert 'id="main-content"' in res.text
        assert "<main" in res.text


class TestVoicePageNavigation:
    """Pins the in-page navigation strip for quick section scanning."""

    def test_page_nav_present(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert "page-nav" in res.text
        assert 'aria-label="Voice page sections"' in res.text

    def test_page_nav_links_to_sections(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert "#voice-config" in res.text
        assert "#voice-tts" in res.text
        assert "#voice-stt" in res.text
        assert "#voice-debug" in res.text

    def test_section_anchors_present(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert 'id="voice-config"' in res.text
        assert 'id="voice-tts"' in res.text
        assert 'id="voice-stt"' in res.text
        assert 'id="voice-debug"' in res.text

    def test_page_nav_hidden_on_error(
        self, _panel_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When voice status fails to load, the in-page nav should not render."""
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
        # Nav should not render when there are no sections to navigate to
        assert 'aria-label="Voice page sections"' not in res.text


class TestVoicePageHierarchy:
    """Pins the zone-based layout hierarchy."""

    def test_configuration_status_zone_label(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert "Configuration &amp; Status" in res.text

    def test_status_cards_use_grid(self, _panel_env: None) -> None:
        """STT and TTS status should be in a side-by-side grid."""
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert "voice-status-grid" in res.text
        assert "grid-2" in res.text

    def test_card_headings_present(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert "voice-card-heading" in res.text

    def test_zone_ordering(self, _panel_env: None) -> None:
        """Zones should appear in order: Config → TTS → STT → Debug."""
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        text = res.text
        idx_config = text.index('id="voice-config"')
        idx_tts = text.index('id="voice-tts"')
        idx_stt = text.index('id="voice-stt"')
        idx_debug = text.index('id="voice-debug"')
        assert idx_config < idx_tts < idx_stt < idx_debug


class TestVoicePageSectionIntros:
    """Pins the section intro descriptions."""

    def test_tts_section_intro_present(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert "voice-section-intro" in res.text
        assert "artifact-oriented" in res.text

    def test_stt_section_intro_present(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert "File-oriented only" in res.text

    def test_debug_section_intro_present(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert "Machine-readable endpoints" in res.text


class TestVoiceEmptyResultStates:
    """Pins the empty-result rendering when no action has been performed."""

    def test_tts_empty_result_shown(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert "voice-empty-result" in res.text
        assert "No TTS generation result yet" in res.text

    def test_stt_empty_result_shown(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert "No STT transcription result yet" in res.text

    def test_empty_result_not_shown_when_result_present(self, _panel_env: None) -> None:
        """When a TTS result is present, the empty-result message should not appear for TTS."""
        mock_response = _make_tts_response()
        with patch("voxera.panel.routes_voice.synthesize_text", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate",
                data={"tts_text": "Hello"},
            )
        assert res.status_code == 200
        assert "No TTS generation result yet" not in res.text


class TestVoiceTTSResultRendering:
    """Pins the polished TTS result block structure."""

    def test_result_block_has_voice_result_class(self, _panel_env: None) -> None:
        mock_response = _make_tts_response()
        with patch("voxera.panel.routes_voice.synthesize_text", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate",
                data={"tts_text": "Hello"},
            )
        assert "voice-result" in res.text

    def test_success_result_has_ok_accent(self, _panel_env: None) -> None:
        mock_response = _make_tts_response()
        with patch("voxera.panel.routes_voice.synthesize_text", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate",
                data={"tts_text": "Hello"},
            )
        assert "voice-result-ok" in res.text

    def test_failure_result_has_fail_accent(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = _authed_csrf_request(
            client,
            "post",
            "/voice/tts/generate",
            data={"tts_text": ""},
        )
        assert "voice-result-fail" in res.text

    def test_result_header_present(self, _panel_env: None) -> None:
        mock_response = _make_tts_response()
        with patch("voxera.panel.routes_voice.synthesize_text", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate",
                data={"tts_text": "Hello"},
            )
        assert "voice-result-header" in res.text
        assert "voice-result-title" in res.text
        assert "Last Result" in res.text

    def test_audio_path_uses_path_block(self, _panel_env: None) -> None:
        mock_response = _make_tts_response()
        with patch("voxera.panel.routes_voice.synthesize_text", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate",
                data={"tts_text": "Hello"},
            )
        assert "voice-path-block" in res.text

    def test_timing_group_present(self, _panel_env: None) -> None:
        mock_response = _make_tts_response()
        with patch("voxera.panel.routes_voice.synthesize_text", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate",
                data={"tts_text": "Hello"},
            )
        assert "voice-timing-group" in res.text

    def test_raw_details_class_present(self, _panel_env: None) -> None:
        mock_response = _make_tts_response()
        with patch("voxera.panel.routes_voice.synthesize_text", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/tts/generate",
                data={"tts_text": "Hello"},
            )
        assert "voice-raw-details" in res.text


class TestVoiceSTTResultRendering:
    """Pins the polished STT result block structure."""

    def test_stt_result_block_has_voice_result_class(self, _panel_env: None) -> None:
        mock_response = _make_stt_response()
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe",
                data={"stt_audio_path": "/tmp/test.wav"},
            )
        assert "voice-result" in res.text

    def test_stt_success_has_ok_accent(self, _panel_env: None) -> None:
        mock_response = _make_stt_response()
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe",
                data={"stt_audio_path": "/tmp/test.wav"},
            )
        assert "voice-result-ok" in res.text

    def test_stt_failure_has_fail_accent(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = _authed_csrf_request(
            client,
            "post",
            "/voice/stt/transcribe",
            data={"stt_audio_path": ""},
        )
        assert "voice-result-fail" in res.text

    def test_transcript_uses_transcript_block(self, _panel_env: None) -> None:
        mock_response = _make_stt_response()
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe",
                data={"stt_audio_path": "/tmp/test.wav"},
            )
        assert "voice-transcript-block" in res.text

    def test_stt_timing_group_present(self, _panel_env: None) -> None:
        mock_response = _make_stt_response()
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe",
                data={"stt_audio_path": "/tmp/test.wav"},
            )
        assert "voice-timing-group" in res.text

    def test_stt_raw_details_class_present(self, _panel_env: None) -> None:
        mock_response = _make_stt_response()
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=mock_response):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/stt/transcribe",
                data={"stt_audio_path": "/tmp/test.wav"},
            )
        assert "voice-raw-details" in res.text


class TestVoiceFormPolish:
    """Pins form structure improvements."""

    def test_tts_form_has_action_form_class(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert "voice-action-form" in res.text

    def test_tts_optional_fields_in_form_row(self, _panel_env: None) -> None:
        """Voice ID and Language should be grouped in a form-row for side-by-side layout."""
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert "form-row" in res.text

    def test_submit_buttons_use_primary_style(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert "btn-primary" in res.text


class TestVoiceDebugSection:
    """Pins the debug/JSON section treatment."""

    def test_debug_card_has_quieter_class(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert "voice-debug-card" in res.text


class TestVoiceCSSClassesExist:
    """Pins that the voice-specific CSS classes are defined in panel.css."""

    def test_voice_css_classes_in_stylesheet(self, _panel_env: None) -> None:
        css_path = (
            Path(__file__).resolve().parent.parent
            / "src"
            / "voxera"
            / "panel"
            / "static"
            / "panel.css"
        )
        css_text = css_path.read_text(encoding="utf-8")
        for cls in [
            ".voice-card-heading",
            ".voice-section-intro",
            ".voice-result",
            ".voice-result-ok",
            ".voice-result-fail",
            ".voice-result-header",
            ".voice-result-title",
            ".voice-path-block",
            ".voice-transcript-row",
            ".voice-transcript-block",
            ".voice-error-text",
            ".voice-timing-group",
            ".voice-empty-result",
            ".voice-action-form",
            ".voice-raw-details",
            ".voice-dep-hint",
            ".voice-debug-card",
            ".voice-status-grid",
        ]:
            assert cls in css_text, f"CSS class {cls} missing from panel.css"


class TestVoiceErrorTextStyling:
    """Pins error text styling in result blocks."""

    def test_tts_error_uses_error_text_class(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = _authed_csrf_request(
            client,
            "post",
            "/voice/tts/generate",
            data={"tts_text": ""},
        )
        assert "voice-error-text" in res.text

    def test_stt_error_uses_error_text_class(self, _panel_env: None) -> None:
        client = TestClient(panel_module.app)
        res = _authed_csrf_request(
            client,
            "post",
            "/voice/stt/transcribe",
            data={"stt_audio_path": ""},
        )
        assert "voice-error-text" in res.text
