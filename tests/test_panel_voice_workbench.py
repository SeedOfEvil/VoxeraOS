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
        """The form intro text must not imply the lane submits jobs.

        Two independent assertions: the page must carry the descriptive
        "conversational only" copy AND must never carry bare success
        claims like "job submitted" or "has been submitted". Splitting
        catches regressions where new wording adds a success claim
        alongside the existing guard language.
        """
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        lowered = res.text.lower()
        # 1. Descriptive copy that describes this lane as conversational only.
        assert "conversational only" in lowered
        # 2. No bare success claims that would imply a queue submission
        #    happened on this lane.
        assert "job submitted" not in lowered
        assert "has been submitted" not in lowered
        assert "executed successfully" not in lowered


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
        # No bare success-claim wording.
        lowered = res.text.lower()
        assert "job submitted" not in lowered
        assert "has been submitted" not in lowered
        assert "executed successfully" not in lowered

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
        # The descriptive copy must match the canonical template wording.
        assert "No queue preview was drafted" in res.text
        assert "no queue job was submitted" in res.text.lower()
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


class TestWorkbenchBadgeTruthfulness:
    """Pin that failure-styled blocks never carry success-looking labels.

    The adapter-reported ``status`` string can legitimately be ``succeeded``
    even on a truthful failure (e.g. ``SUCCEEDED`` with empty transcript, or
    ``SUCCEEDED`` with no audio_path). The route must translate that into a
    ``display_status`` the UI surfaces so badges never read ``succeeded`` on
    a fail-styled card.
    """

    def test_stt_succeeded_but_no_transcript_displays_no_transcript_badge(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Adapter pathology: status=succeeded but transcript is None."""
        stt = _make_stt_response(
            status=STT_STATUS_SUCCEEDED,
            transcript=None,
            error="Adapter returned empty transcript",
            error_class="empty_audio",
        )
        called = {"vera": False}

        async def _must_not_call_vera(**_kwargs: Any) -> dict[str, Any]:  # pragma: no cover
            called["vera"] = True
            return {"answer": "x", "status": "ok:test"}

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
        # The Transcript block is failure-styled AND the badge must not read
        # "succeeded".
        assert "badge-fail" in res.text
        assert "no_transcript" in res.text
        # The Vera lane must not have been called and no "answered" success
        # badge must appear.
        assert called["vera"] is False
        assert "answered" not in res.text

    def test_tts_succeeded_but_no_audio_path_displays_no_audio_artifact(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Adapter pathology: TTS status=succeeded but audio_path is None."""
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="ping")
        tts_bad = _make_tts_response(
            status=TTS_STATUS_SUCCEEDED,
            audio_path=None,
            error="Adapter reported success but produced no file",
            error_class="backend_error",
        )

        async def _fake_tts_async(**_kwargs: Any) -> TTSResponse:
            return tts_bad

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
        assert "no_audio_artifact" in res.text
        # No fake audio path claim.
        assert "/tmp/vera_reply.wav" not in res.text

    def test_vera_empty_answer_surfaces_upstream_vera_status_on_fail_card(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When Vera returns an empty answer with a concrete upstream status
        (e.g. ``degraded_unavailable``), the Vera failure card must surface
        BOTH the local display_status (``vera_empty_answer``) AND the
        upstream Vera status as a secondary ``Vera Status`` detail line so
        an operator can tell why the answer was empty."""

        async def _empty_degraded(**_kwargs: Any) -> dict[str, Any]:
            return {"answer": "   ", "status": "degraded_unavailable"}

        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _empty_degraded)
        stt = _make_stt_response(transcript="hi")
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
        # Failure-styled block with local reason.
        assert "badge-fail" in res.text
        assert "vera_empty_answer" in res.text
        # Upstream Vera status detail is rendered so the operator can tell
        # *why* the answer was empty (degraded brain provider, etc.).
        assert "Vera Status" in res.text
        assert "degraded_unavailable" in res.text
        # No fabricated answer body.
        assert "answered" not in res.text


class TestWorkbenchOperatorContracts:
    """Operator-facing behavior contracts that must not regress."""

    def test_speak_response_unchecked_does_not_call_tts(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TTS must only run when the operator explicitly opted in."""
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        tts_called = {"n": 0}

        async def _count_tts(**_kwargs: Any) -> TTSResponse:  # pragma: no cover
            tts_called["n"] += 1
            return _make_tts_response()

        stt = _make_stt_response(transcript="hello")
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
                    # speak_response deliberately unchecked
                },
            )
        assert res.status_code == 200
        assert tts_called["n"] == 0
        # The empty state explicitly surfaces "TTS was not requested".
        assert "TTS was not requested" in res.text

    def test_language_is_passed_to_stt(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Operator-supplied language must reach the canonical STT call."""
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="hola")
        captured: dict[str, Any] = {}

        def _capture_transcribe(**kwargs: Any) -> STTResponse:
            captured.update(kwargs)
            return stt

        with patch(
            "voxera.panel.routes_voice.transcribe_audio_file", side_effect=_capture_transcribe
        ):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/test.wav",
                    "workbench_language": "es",
                    "workbench_send_to_vera": "1",
                },
            )
        assert res.status_code == 200
        assert captured["audio_path"] == "/tmp/test.wav"
        assert captured["language"] == "es"

    def test_session_id_is_preserved_across_subsequent_workbench_runs(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two consecutive workbench runs with the same session_id must share
        the Vera session (so the voice turns appear in order in one thread)."""
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        queue_dir = _panel_env
        stt_one = _make_stt_response(transcript="first")
        stt_two = _make_stt_response(transcript="second")
        session_id = "vera-wb-preserved"

        client = TestClient(panel_module.app)
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt_one):
            _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/a.wav",
                    "workbench_send_to_vera": "1",
                    "workbench_session_id": session_id,
                },
            )
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt_two):
            _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/b.wav",
                    "workbench_send_to_vera": "1",
                    "workbench_session_id": session_id,
                },
            )
        turns = session_store.read_session_turns(queue_dir, session_id)
        # Expect: [user:first (voice), assistant:Ack first, user:second (voice), assistant:Ack second]
        assert len(turns) == 4
        assert turns[0]["role"] == "user" and turns[0]["input_origin"] == "voice_transcript"
        assert turns[0]["text"] == "first"
        assert turns[2]["role"] == "user" and turns[2]["input_origin"] == "voice_transcript"
        assert turns[2]["text"] == "second"

    def test_path_traversal_in_session_id_is_clamped(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Session JSON must never be written outside vera_sessions/."""
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        queue_dir = _panel_env
        stt = _make_stt_response(transcript="clamp")
        malicious = "../../etc/evil"
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/t.wav",
                    "workbench_send_to_vera": "1",
                    "workbench_session_id": malicious,
                },
            )
        assert res.status_code == 200
        # Canonical session_store writes to queue_root/artifacts/vera_sessions/<basename>.json.
        sessions_dir = queue_dir / "artifacts" / "vera_sessions"
        written = list(sessions_dir.glob("*.json"))
        # At least one file was written under the canonical sessions directory.
        assert written, "no session file was written"
        # Nothing outside vera_sessions/ was created from this request.
        for path in written:
            assert path.parent == sessions_dir
            assert path.name in {"evil.json"}, f"unexpected session file {path.name!r}"

    def test_no_turns_written_when_stt_fails(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed STT must not append a voice-origin turn to the session."""
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        queue_dir = _panel_env
        stt = _make_stt_response(
            status=STT_STATUS_UNAVAILABLE,
            transcript=None,
            error="STT disabled",
            error_class="disabled",
        )
        session_id = "vera-wb-stt-fail"
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/t.wav",
                    "workbench_send_to_vera": "1",
                    "workbench_session_id": session_id,
                },
            )
        turns = session_store.read_session_turns(queue_dir, session_id)
        assert turns == []

    def test_vera_empty_answer_does_not_trigger_tts(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If Vera returns an empty answer, TTS must not run (text is absent,
        so there is nothing truthful to synthesize)."""

        async def _empty_vera_reply(**_kwargs: Any) -> dict[str, Any]:
            return {"answer": "   ", "status": "degraded_unavailable"}

        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _empty_vera_reply)
        tts_called = {"n": 0}

        async def _count_tts(**_kwargs: Any) -> TTSResponse:  # pragma: no cover
            tts_called["n"] += 1
            return _make_tts_response()

        stt = _make_stt_response(transcript="hi")
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
                    "workbench_audio_path": "/tmp/t.wav",
                    "workbench_send_to_vera": "1",
                    "workbench_speak_response": "1",
                },
            )
        assert res.status_code == 200
        assert tts_called["n"] == 0
        # The page must truthfully surface that the TTS step did not run.
        assert "Vera did not produce a real textual response" in res.text


class TestWorkbenchHandoffAndQueueBoundary:
    """Pin the queue/preview boundary: this lane never mutates queue state."""

    def test_happy_path_does_not_write_job_files_to_queue_buckets(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        queue_dir = _panel_env
        stt = _make_stt_response(transcript="do something")
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/t.wav",
                    "workbench_send_to_vera": "1",
                    "workbench_session_id": "vera-wb-boundary",
                },
            )
        assert res.status_code == 200
        # No queue job files (*.json) must exist in any canonical bucket.
        # Subdirectories may be created by panel startup (e.g.
        # ``pending/approvals``) but must remain empty of jobs.
        for bucket in ("inbox", "pending", "running", "done", "failed", "canceled", "approvals"):
            bucket_dir = queue_dir / bucket
            if not bucket_dir.exists():
                continue
            job_files = [p for p in bucket_dir.rglob("*.json")]
            assert job_files == [], f"unexpected job file(s) in {bucket}: {job_files}"

    def test_session_does_not_claim_preview_or_handoff_state(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After a happy run, the Vera session JSON must carry no
        ``pending_job_preview`` or ``handoff`` fields — this lane never
        claims either."""
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        queue_dir = _panel_env
        stt = _make_stt_response(transcript="please do the thing")
        session_id = "vera-wb-no-preview"
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/t.wav",
                    "workbench_send_to_vera": "1",
                    "workbench_session_id": session_id,
                },
            )
        import json as _json

        path = queue_dir / "artifacts" / "vera_sessions" / f"{session_id}.json"
        assert path.exists()
        data = _json.loads(path.read_text(encoding="utf-8"))
        assert "pending_job_preview" not in data
        assert "handoff" not in data
