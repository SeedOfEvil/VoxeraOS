"""Route-level tests for the Voice Workbench action-oriented guidance block.

Pins operator-facing rendering behavior:

1. Clearly conversational transcripts do NOT render the action-oriented
   guidance block (keeping informational runs clean).
2. Clearly action-oriented transcripts DO render the guidance block,
   distinct from the always-present "Governed Handoff: not attempted"
   row.
3. The guidance copy stays truthful: it never claims a preview exists,
   never claims a job was created, never says "ready to submit".
4. The "Continue in Vera" affordance remains present and links back to
   the same canonical session.
5. No queue/preview state is fabricated for the workbench session.
6. Existing workbench success/failure rendering is preserved.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from voxera.config import DEFAULT_VERA_WEB_BASE_URL as _DEFAULT_VERA_WEB_BASE_URL
from voxera.panel import app as panel_module
from voxera.vera import session_store
from voxera.voice.stt_protocol import STT_STATUS_SUCCEEDED, STTResponse


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


def _make_stt_response(*, transcript: str | None = "hello") -> STTResponse:
    return STTResponse(
        request_id="test-stt-action",
        status=STT_STATUS_SUCCEEDED,
        transcript=transcript,
        language="en",
        audio_duration_ms=2000,
        error=None,
        error_class=None,
        backend="whisper_local",
        started_at_ms=1000,
        finished_at_ms=1100,
        schema_version=1,
        inference_ms=100,
    )


async def _fake_vera_reply(**kwargs: Any) -> dict[str, Any]:
    return {"answer": f"Ack: {kwargs['user_message']}", "status": "ok:test"}


_ACTION_GUIDANCE_TESTID = 'data-testid="voice-workbench-action-guidance"'
_ACTION_CONTINUE_TESTID = 'data-testid="voice-workbench-action-continue"'


class TestInformationalRunStaysClean:
    """Conversational / informational transcripts must not render the block."""

    def test_question_form_does_not_render_action_guidance(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="what time is it?")
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/t.wav",
                    "workbench_send_to_vera": "1",
                },
            )
        assert res.status_code == 200
        assert _ACTION_GUIDANCE_TESTID not in res.text
        assert "Looks like governed work" not in res.text
        # The existing governed-handoff row still renders its truthful
        # "not attempted" copy.
        assert "Governed Handoff" in res.text
        assert "not attempted" in res.text

    def test_plain_conversational_does_not_render_action_guidance(
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
                    "workbench_audio_path": "/tmp/t.wav",
                    "workbench_send_to_vera": "1",
                },
            )
        assert res.status_code == 200
        assert _ACTION_GUIDANCE_TESTID not in res.text


class TestActionOrientedRunRendersGuidance:
    """Clearly action-oriented transcripts render the guidance block."""

    def test_action_oriented_transcript_renders_guidance_block(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="delete the report file from notes")
        session_id = "vera-wb-action-delete"
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/t.wav",
                    "workbench_send_to_vera": "1",
                    "workbench_session_id": session_id,
                },
            )
        assert res.status_code == 200
        assert _ACTION_GUIDANCE_TESTID in res.text
        assert "Looks like governed work" in res.text
        assert "action-oriented" in res.text
        # The guidance block is visually distinct: badge-warn styling,
        # not the badge-paused ("not attempted") styling of the
        # governed-handoff row.
        guidance_start = res.text.index(_ACTION_GUIDANCE_TESTID)
        handoff_at = res.text.index("Governed Handoff", guidance_start)
        guidance_block = res.text[guidance_start:handoff_at]
        assert "badge-warn" in guidance_block
        assert "not attempted" not in guidance_block

    def test_action_guidance_block_precedes_governed_handoff_row(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Guidance block is rendered first so the operator sees the
        stronger signal before the always-present handoff row."""
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="move the uploads folder to archive")
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/t.wav",
                    "workbench_send_to_vera": "1",
                },
            )
        assert res.status_code == 200
        guidance_at = res.text.index(_ACTION_GUIDANCE_TESTID)
        handoff_at = res.text.index("Governed Handoff")
        assert guidance_at < handoff_at


class TestGuidanceWordingIsTruthful:
    """The guidance copy must not imply preview/job/submit state."""

    def test_guidance_does_not_claim_preview_or_submission(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="restart the voxera-daemon service")
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/t.wav",
                    "workbench_send_to_vera": "1",
                },
            )
        assert res.status_code == 200
        assert _ACTION_GUIDANCE_TESTID in res.text
        guidance_start = res.text.index(_ACTION_GUIDANCE_TESTID)
        handoff_at = res.text.index("Governed Handoff", guidance_start)
        guidance_block = res.text[guidance_start:handoff_at].lower()
        # No false claims of preview drafting.
        assert "preview has been drafted" not in guidance_block
        assert "preview is ready" not in guidance_block
        assert "ready to submit" not in guidance_block
        # No false claims of submission / execution.
        assert "job submitted" not in guidance_block
        assert "has been submitted" not in guidance_block
        assert "has been queued" not in guidance_block
        assert "executed successfully" not in guidance_block
        # The block does carry the conservative, guidance-only phrasing.
        assert "nothing has been drafted" in guidance_block

    def test_continue_in_vera_affordance_is_present_and_correct(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="create a new automation for log rotation")
        session_id = "vera-wb-action-continue"
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/t.wav",
                    "workbench_send_to_vera": "1",
                    "workbench_session_id": session_id,
                },
            )
        assert res.status_code == 200
        assert _ACTION_CONTINUE_TESTID in res.text
        assert f"{_DEFAULT_VERA_WEB_BASE_URL}/?session_id={session_id}" in res.text


class TestNoFabricatedQueueOrPreviewState:
    """The workbench must never fabricate preview or queue state."""

    def test_action_guidance_run_writes_no_queue_jobs(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        queue_dir = _panel_env
        stt = _make_stt_response(transcript="install the latest voxera package")
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/t.wav",
                    "workbench_send_to_vera": "1",
                    "workbench_session_id": "vera-wb-action-noqueue",
                },
            )
        assert res.status_code == 200
        assert _ACTION_GUIDANCE_TESTID in res.text
        for bucket in ("inbox", "pending", "running", "done", "failed", "canceled", "approvals"):
            bucket_dir = queue_dir / bucket
            if not bucket_dir.exists():
                continue
            job_files = [p for p in bucket_dir.rglob("*.json")]
            assert job_files == [], f"unexpected job file(s) in {bucket}: {job_files}"

    def test_action_guidance_run_writes_no_preview_in_session(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        queue_dir = _panel_env
        stt = _make_stt_response(transcript="delete the stale artifact folder")
        session_id = "vera-wb-action-nopreview"
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


class TestNoRegressionOnExistingRenderings:
    """The action-guidance block must not break existing surfaces."""

    def test_stt_failed_run_does_not_render_action_guidance(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When STT fails there is no real transcript — classifier sees
        nothing and we must not render a guidance block keyed off a
        fabricated transcript."""
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript=None)
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/t.wav",
                    "workbench_send_to_vera": "1",
                },
            )
        assert res.status_code == 200
        assert _ACTION_GUIDANCE_TESTID not in res.text
        # The existing failure rendering is preserved.
        assert "upstream transcription did not produce a real transcript" in res.text

    def test_action_oriented_run_still_renders_vera_answer(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="restart the panel service")
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            client = TestClient(panel_module.app)
            res = _authed_csrf_request(
                client,
                "post",
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/t.wav",
                    "workbench_send_to_vera": "1",
                },
            )
        assert res.status_code == 200
        # Vera answer still renders, and the action-guidance block renders
        # alongside it.
        assert "Ack: restart the panel service" in res.text
        assert "Vera Response" in res.text
        assert "answered" in res.text
        assert _ACTION_GUIDANCE_TESTID in res.text

    def test_informational_run_does_not_change_session_persistence(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Classification is a read-only scan — it must not alter how the
        canonical Vera session is persisted."""
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        queue_dir = _panel_env
        stt = _make_stt_response(transcript="tell me about the architecture")
        session_id = "vera-wb-action-sessionpersist"
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
        assert turns[0]["role"] == "user"
        assert turns[0]["input_origin"] == "voice_transcript"
        assert turns[1]["role"] == "assistant"
