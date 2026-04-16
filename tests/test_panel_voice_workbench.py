"""Tests for the panel Voice Workbench route (/voice/workbench/run).

Pins the operator-facing voice -> Vera workflow:
- form rendering on the existing panel voice page
- canonical STT invocation through transcribe_audio_file
- voice_transcript-origin turn persistence via the canonical Vera
  session store
- Vera reply rendering (real text only)
- optional TTS on the reply (artifact-oriented, truthful failure)
- fail-closed behavior on missing audio, STT unavailability, empty
  transcript, Vera failure, TTS failure
- no fake "submitted"/"executed"/queue-handoff wording
- auth + CSRF enforcement
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from voxera.panel import app as panel_module
from voxera.vera import session_store
from voxera.voice.stt_protocol import (
    STT_STATUS_FAILED,
    STT_STATUS_SUCCEEDED,
    STT_STATUS_UNAVAILABLE,
    STTResponse,
)
from voxera.voice.tts_protocol import (
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
def _panel_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    monkeypatch.setenv("VOXERA_ENABLE_VOICE_FOUNDATION", "1")
    monkeypatch.setenv("VOXERA_ENABLE_VOICE_INPUT", "1")
    monkeypatch.setenv("VOXERA_ENABLE_VOICE_OUTPUT", "1")
    monkeypatch.setenv("VOXERA_VOICE_STT_BACKEND", "whisper_local")
    monkeypatch.setenv("VOXERA_VOICE_TTS_BACKEND", "piper_local")
    return queue_dir


def _make_stt_response(
    *,
    status: str = STT_STATUS_SUCCEEDED,
    transcript: str | None = "please check system health",
    language: str | None = "en",
    error: str | None = None,
    error_class: str | None = None,
    backend: str | None = "whisper_local",
    request_id: str = "test-stt-wb",
) -> STTResponse:
    return STTResponse(
        request_id=request_id,
        status=status,
        transcript=transcript,
        language=language,
        audio_duration_ms=2000,
        error=error,
        error_class=error_class,
        backend=backend,
        started_at_ms=1000,
        finished_at_ms=1100,
        schema_version=1,
        inference_ms=100,
    )


def _make_tts_response(
    *,
    status: str = TTS_STATUS_SUCCEEDED,
    audio_path: str | None = "/tmp/vera_reply.wav",
    error: str | None = None,
    error_class: str | None = None,
    backend: str | None = "piper_local",
    request_id: str = "test-tts-wb",
) -> TTSResponse:
    return TTSResponse(
        request_id=request_id,
        status=status,
        audio_path=audio_path,
        audio_duration_ms=1500,
        error=error,
        error_class=error_class,
        backend=backend,
        started_at_ms=2000,
        finished_at_ms=2200,
        schema_version=1,
        inference_ms=200,
    )


async def _fake_vera_reply(**kwargs: Any) -> dict[str, Any]:
    return {"answer": f"Ack: {kwargs['user_message']}", "status": "ok:test"}


async def _failing_vera_reply(**_kwargs: Any) -> dict[str, Any]:
    raise RuntimeError("brain offline")


class TestWorkbenchFormRendering:
    def test_voice_status_page_renders_workbench_form(self, _panel_env: Path) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        assert "Voice Workbench" in res.text
        assert 'action="/voice/workbench/run"' in res.text
        assert 'name="workbench_audio_path"' in res.text
        assert 'name="workbench_send_to_vera"' in res.text
        assert 'name="workbench_speak_response"' in res.text
        assert 'name="workbench_session_id"' in res.text

    def test_workbench_form_advertises_conversational_only_scope(self, _panel_env: Path) -> None:
        """The form intro text must not imply the lane submits jobs."""
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        lowered = res.text.lower()
        # The lane must describe itself as conversational only.
        assert "conversational only" in lowered
        # And must not claim it submits or executes anything.
        assert "submit" not in lowered or "never" in lowered or "not submit" in lowered


class TestWorkbenchHappyPath:
    def test_transcript_to_vera_renders_real_answer(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="please check system health")
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/test.wav",
                    "workbench_send_to_vera": "1",
                },
            )
        assert res.status_code == 200
        assert "please check system health" in res.text
        assert "Ack: please check system health" in res.text
        assert "Vera Response" in res.text
        assert "answered" in res.text
        assert "transcribed" in res.text

    def test_voice_transcript_origin_is_persisted_on_turn(
        self,
        _panel_env: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        queue_dir = _panel_env
        stt = _make_stt_response(transcript="schedule uptime check")
        session_id = "vera-workbench-route-test"
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/test.wav",
                    "workbench_send_to_vera": "1",
                    "workbench_session_id": session_id,
                },
            )
        assert res.status_code == 200
        turns = session_store.read_session_turns(queue_dir, session_id)
        assert turns[0]["role"] == "user"
        assert turns[0]["input_origin"] == "voice_transcript"
        assert turns[0]["text"] == "schedule uptime check"
        assert turns[1]["role"] == "assistant"
        assert turns[1]["text"] == "Ack: schedule uptime check"

    def test_tts_runs_on_vera_reply_when_requested(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="ping")
        tts = _make_tts_response(audio_path="/tmp/vera_out.wav")

        async def _fake_tts_async(**_kwargs: Any) -> TTSResponse:
            return tts

        with (
            patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt),
            patch("voxera.panel.routes_voice.synthesize_text_async", side_effect=_fake_tts_async),
        ):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/test.wav",
                    "workbench_send_to_vera": "1",
                    "workbench_speak_response": "1",
                },
            )
        assert res.status_code == 200
        assert "Spoken Response (TTS)" in res.text
        assert "/tmp/vera_out.wav" in res.text
        assert "synthesized" in res.text


class TestWorkbenchFailClosed:
    def test_missing_audio_path_renders_error(self, _panel_env: Path) -> None:
        client = TestClient(panel_module.app)
        res = _authed_csrf_request(
            client,
            "post",
            "/voice/workbench/run",
            data={"workbench_audio_path": "", "workbench_send_to_vera": "1"},
        )
        assert res.status_code == 200
        assert "Audio file path is required" in res.text
        # Vera must not have been called, so no "Vera Response" success badge.
        assert "answered" not in res.text
        # No "submitted" or "executed" wording.
        lowered = res.text.lower()
        assert "submitted" not in lowered or "no queue job was submitted" in lowered

    def test_stt_unavailable_fails_closed(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stt = _make_stt_response(
            status=STT_STATUS_UNAVAILABLE,
            transcript=None,
            language=None,
            error="No STT backend is configured",
            error_class="backend_missing",
            backend="null",
        )

        called = {"vera": False}

        async def _must_not_call_vera(**_kwargs: Any) -> dict[str, Any]:  # pragma: no cover
            called["vera"] = True
            return {"answer": "should not happen", "status": "ok:test"}

        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _must_not_call_vera)
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/test.wav",
                    "workbench_send_to_vera": "1",
                },
            )
        assert res.status_code == 200
        assert "No STT backend is configured" in res.text
        assert called["vera"] is False
        # No assistant answer badge when STT failed.
        assert "answered" not in res.text

    def test_stt_succeeds_but_empty_transcript_does_not_call_vera(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """STT response with status=succeeded but no transcript must not call Vera."""
        stt = _make_stt_response(
            status=STT_STATUS_SUCCEEDED,
            transcript=None,
            error="Empty audio",
            error_class="empty_audio",
        )
        called = {"vera": False}

        async def _must_not_call_vera(**_kwargs: Any) -> dict[str, Any]:  # pragma: no cover
            called["vera"] = True
            return {"answer": "should not happen", "status": "ok:test"}

        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _must_not_call_vera)
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/test.wav",
                    "workbench_send_to_vera": "1",
                },
            )
        assert res.status_code == 200
        assert called["vera"] is False
        assert "answered" not in res.text

    def test_vera_failure_is_surfaced_truthfully(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _failing_vera_reply)
        stt = _make_stt_response(transcript="hello world")
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/test.wav",
                    "workbench_send_to_vera": "1",
                    "workbench_speak_response": "1",
                },
            )
        assert res.status_code == 200
        assert "vera_error" in res.text
        assert "RuntimeError" in res.text
        # Vera failure must NOT trigger TTS.
        assert "Spoken Response (TTS)" not in res.text or "synthesized" not in res.text

    def test_tts_unavailable_keeps_text_authoritative(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="hello")
        tts_unavailable = _make_tts_response(
            status=TTS_STATUS_UNAVAILABLE,
            audio_path=None,
            error="No TTS backend configured",
            error_class="backend_missing",
            backend="null",
        )

        async def _fake_tts_async(**_kwargs: Any) -> TTSResponse:
            return tts_unavailable

        with (
            patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt),
            patch("voxera.panel.routes_voice.synthesize_text_async", side_effect=_fake_tts_async),
        ):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/test.wav",
                    "workbench_send_to_vera": "1",
                    "workbench_speak_response": "1",
                },
            )
        assert res.status_code == 200
        # Text response IS authoritative: Vera answer is rendered.
        assert "Ack: hello" in res.text
        # TTS failure is surfaced, not hidden.
        assert "No TTS backend configured" in res.text
        # No audio path is claimed.
        assert "/tmp/vera_reply.wav" not in res.text
        # Text-is-authoritative wording present.
        assert "authoritative" in res.text.lower()

    def test_stt_failed_status_does_not_call_vera(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stt = _make_stt_response(
            status=STT_STATUS_FAILED,
            transcript=None,
            error="Audio file not found: /tmp/nope.wav",
            error_class="backend_error",
        )
        called = {"vera": False}

        async def _must_not_call_vera(**_kwargs: Any) -> dict[str, Any]:  # pragma: no cover
            called["vera"] = True
            return {"answer": "should not happen", "status": "ok:test"}

        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _must_not_call_vera)
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/nope.wav",
                    "workbench_send_to_vera": "1",
                },
            )
        assert res.status_code == 200
        assert called["vera"] is False
        assert "Audio file not found" in res.text


class TestWorkbenchTrustModel:
    def test_no_fake_queue_handoff_wording_in_successful_flow(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="do the thing")
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/test.wav",
                    "workbench_send_to_vera": "1",
                },
            )
        assert res.status_code == 200
        # The page must truthfully surface the handoff section as "not attempted"
        # on the conversational-only lane.
        assert "Governed Handoff" in res.text
        assert "not attempted" in res.text
        assert (
            "No queue preview was drafted" in res.text
            or "no queue job was submitted" in res.text.lower()
        )
        # No "submitted" or "executed" success claims.
        lowered = res.text.lower()
        assert "job submitted" not in lowered
        assert "has been submitted" not in lowered
        assert "executed successfully" not in lowered

    def test_send_to_vera_unchecked_does_not_call_vera(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called = {"vera": False}

        async def _must_not_call_vera(**_kwargs: Any) -> dict[str, Any]:  # pragma: no cover
            called["vera"] = True
            return {"answer": "nope", "status": "ok:test"}

        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _must_not_call_vera)
        stt = _make_stt_response(transcript="hi")
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/test.wav",
                    # workbench_send_to_vera intentionally omitted
                },
            )
        assert res.status_code == 200
        assert called["vera"] is False
        # Transcript is shown (STT succeeded).
        assert "hi" in res.text
        # But no Vera "answered" success badge.
        assert "answered" not in res.text

    def test_speak_response_requires_real_vera_answer(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If Vera fails, TTS must not be invoked even if speak_response is checked."""
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _failing_vera_reply)
        tts_called = {"n": 0}

        async def _count_tts(**_kwargs: Any) -> TTSResponse:  # pragma: no cover
            tts_called["n"] += 1
            return _make_tts_response()

        stt = _make_stt_response(transcript="hi there")
        with (
            patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt),
            patch("voxera.panel.routes_voice.synthesize_text_async", side_effect=_count_tts),
        ):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/test.wav",
                    "workbench_send_to_vera": "1",
                    "workbench_speak_response": "1",
                },
            )
        assert res.status_code == 200
        assert tts_called["n"] == 0


class TestWorkbenchSecurityEnforcement:
    def test_requires_csrf_token(self, _panel_env: Path) -> None:
        client = TestClient(panel_module.app)
        # Get a CSRF cookie but send a blank token in the body.
        client.get("/", headers=_operator_headers())
        res = client.post(
            "/voice/workbench/run",
            data={
                "workbench_audio_path": "/tmp/test.wav",
                "workbench_send_to_vera": "1",
                "csrf_token": "",
            },
            headers=_operator_headers(),
            follow_redirects=False,
        )
        assert res.status_code in (400, 401, 403)

    def test_requires_operator_auth(self, _panel_env: Path) -> None:
        client = TestClient(panel_module.app)
        res = client.post(
            "/voice/workbench/run",
            data={"workbench_audio_path": "/tmp/test.wav"},
            follow_redirects=False,
        )
        assert res.status_code in (401, 403)
