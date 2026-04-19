"""Tests for the Voice Workbench browser microphone upload route.

Pins the browser-capture lane for the Voice Workbench:

1. ``/voice/workbench/mic-upload`` accepts a bounded binary audio body
   and feeds it into the same canonical STT -> Vera -> optional TTS
   pipeline as the file-path form.
2. Empty / missing audio bodies fail closed with a truthful ``400``
   without invoking STT or Vera.
3. The rendered page truthfully labels the run as microphone-origin
   and names the temp file as ``Temp Audio File`` (not ``Audio Path``),
   without claiming a queue submission happened.
4. The captured transcript is persisted as a ``voice_transcript``-origin
   turn on the same Vera session the operator is already on (preserving
   session continuity with the canonical Vera chat surface).
5. Action-oriented mic-origin transcripts draft a real canonical
   preview in the same session — identical behavior to the file-path
   lane.
6. Spoken lifecycle commands ("submit it") dispatched from a mic-origin
   transcript route into the canonical submit seam (not a fake submit).
7. Oversized uploads fail closed with a ``413`` and leave no temp file
   behind.
8. CSRF enforcement: a mic upload without a valid CSRF token is
   rejected with ``403`` and never touches the pipeline.
9. The voice status page advertises the mic capture block + the
   enhancer script so the browser can progressively enhance the form.
10. The file-path lane still renders a truthful ``file path`` source
    badge — no regression in the typed-path workflow.
"""

from __future__ import annotations

import base64
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from voxera.panel import app as panel_module
from voxera.panel import routes_voice
from voxera.panel.voice_workbench_lifecycle import (
    LIFECYCLE_ACTION_SUBMIT,
    LIFECYCLE_STATUS_SUBMITTED,
    VoiceWorkbenchLifecycleResult,
)
from voxera.vera import session_store
from voxera.voice.stt_protocol import STT_STATUS_SUCCEEDED, STTResponse


def _operator_headers(user: str = "admin", password: str = "secret") -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _prime_csrf(client: TestClient) -> str:
    home = client.get("/", headers=_operator_headers())
    assert home.status_code == 200
    return client.cookies.get("voxera_panel_csrf") or ""


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
) -> STTResponse:
    return STTResponse(
        request_id="test-stt-mic",
        status=status,
        transcript=transcript,
        language=language,
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


def _mic_post(
    client: TestClient,
    *,
    body: bytes,
    csrf: str,
    query: dict[str, str] | None = None,
    content_type: str = "audio/webm",
) -> Any:
    headers = {
        **_operator_headers(),
        "content-type": content_type,
        "x-csrf-token": csrf,
    }
    url = "/voice/workbench/mic-upload"
    return client.post(url, content=body, headers=headers, params=query or {})


def _iter_tmp_mic_files() -> Iterator[Path]:
    """Yield any mic-upload temp files that may have been left on disk.

    Used to assert that the route cleans up after itself even on the
    error paths.  The prefix is the canonical one defined by the route.
    The root is resolved through :func:`tempfile.gettempdir` — the same
    function ``tempfile.mkstemp`` resolves against — so this stays
    correct on macOS / CI setups that override ``TMPDIR`` / ``TMP`` /
    ``TEMP`` instead of using ``/tmp``.
    """
    yield from Path(tempfile.gettempdir()).glob(f"{routes_voice._MIC_UPLOAD_PREFIX}*")


class TestMicUploadHappyPath:
    def test_mic_upload_feeds_workbench_pipeline(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A mic POST with valid audio flows through the canonical STT -> Vera path."""
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="please check system health")

        seen: dict[str, Any] = {}

        def _fake_transcribe(**kwargs: Any) -> STTResponse:
            seen["audio_path"] = kwargs.get("audio_path")
            seen["session_id"] = kwargs.get("session_id")
            return stt

        client = TestClient(panel_module.app)
        csrf = _prime_csrf(client)
        with patch("voxera.panel.routes_voice.transcribe_audio_file", side_effect=_fake_transcribe):
            res = _mic_post(
                client,
                body=b"\x1a\x45\xdf\xa3" + b"\x00" * 64,
                csrf=csrf,
                query={"workbench_send_to_vera": "1"},
            )
        assert res.status_code == 200
        assert "please check system health" in res.text
        assert "Ack: please check system health" in res.text
        assert "browser microphone" in res.text
        assert 'data-testid="voice-workbench-source-mic"' in res.text
        audio_path = seen["audio_path"]
        assert isinstance(audio_path, str)
        assert audio_path.endswith(".webm")
        assert routes_voice._MIC_UPLOAD_PREFIX in audio_path
        # Temp file cleaned up after the run.
        assert list(_iter_tmp_mic_files()) == []

    def test_voice_transcript_origin_persisted_from_mic_upload(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The mic-origin turn is persisted with the canonical voice_transcript origin."""
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        queue_dir = _panel_env
        stt = _make_stt_response(transcript="schedule uptime check")
        session_id = "vera-mic-route-test"
        client = TestClient(panel_module.app)
        csrf = _prime_csrf(client)
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            res = _mic_post(
                client,
                body=b"RIFF----WAVE" + b"\x00" * 128,
                csrf=csrf,
                query={"workbench_send_to_vera": "1", "workbench_session_id": session_id},
                content_type="audio/wav",
            )
        assert res.status_code == 200
        turns = session_store.read_session_turns(queue_dir, session_id)
        assert turns[0]["role"] == "user"
        assert turns[0]["input_origin"] == "voice_transcript"
        assert turns[0]["text"] == "schedule uptime check"
        assert turns[1]["role"] == "assistant"
        assert turns[1]["text"] == "Ack: schedule uptime check"

    def test_content_type_drives_temp_suffix(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The temp suffix reflects the browser's captured container hint."""
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="hello")
        observed: dict[str, str] = {}

        def _fake_transcribe(**kwargs: Any) -> STTResponse:
            observed["audio_path"] = str(kwargs.get("audio_path"))
            return stt

        client = TestClient(panel_module.app)
        csrf = _prime_csrf(client)
        with patch("voxera.panel.routes_voice.transcribe_audio_file", side_effect=_fake_transcribe):
            res = _mic_post(
                client,
                body=b"OggS" + b"\x00" * 64,
                csrf=csrf,
                query={"workbench_send_to_vera": "1"},
                content_type="audio/ogg;codecs=opus",
            )
        assert res.status_code == 200
        assert observed["audio_path"].endswith(".ogg")

    def test_unknown_audio_subtype_falls_back_to_webm(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An ``audio/*`` subtype the suffix map doesn't recognize still runs —
        it just falls back to the default ``.webm`` temp suffix.  Non-audio
        content types are rejected by the allowlist (see
        ``test_non_audio_content_type_rejected``); this only exercises the
        soft-unknown case inside the allowed ``audio/*`` space.
        """
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="hello")
        observed: dict[str, str] = {}

        def _fake_transcribe(**kwargs: Any) -> STTResponse:
            observed["audio_path"] = str(kwargs.get("audio_path"))
            return stt

        client = TestClient(panel_module.app)
        csrf = _prime_csrf(client)
        with patch("voxera.panel.routes_voice.transcribe_audio_file", side_effect=_fake_transcribe):
            res = _mic_post(
                client,
                body=b"\x00" * 64,
                csrf=csrf,
                query={"workbench_send_to_vera": "1"},
                content_type="audio/x-future-codec",
            )
        assert res.status_code == 200
        assert observed["audio_path"].endswith(".webm")


class TestMicUploadFailClosed:
    def test_empty_body_rejected(self, _panel_env: Path) -> None:
        called = {"stt": False}

        def _must_not_call_stt(**_kwargs: Any) -> STTResponse:  # pragma: no cover
            called["stt"] = True
            return _make_stt_response()

        client = TestClient(panel_module.app)
        csrf = _prime_csrf(client)
        with patch(
            "voxera.panel.routes_voice.transcribe_audio_file", side_effect=_must_not_call_stt
        ):
            res = _mic_post(client, body=b"", csrf=csrf)
        assert res.status_code == 400
        assert called["stt"] is False
        assert list(_iter_tmp_mic_files()) == []

    def test_oversized_body_rejected(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(routes_voice, "_MIC_UPLOAD_MAX_BYTES", 16)
        called = {"stt": False}

        def _must_not_call_stt(**_kwargs: Any) -> STTResponse:  # pragma: no cover
            called["stt"] = True
            return _make_stt_response()

        client = TestClient(panel_module.app)
        csrf = _prime_csrf(client)
        with patch(
            "voxera.panel.routes_voice.transcribe_audio_file", side_effect=_must_not_call_stt
        ):
            res = _mic_post(client, body=b"X" * 64, csrf=csrf)
        assert res.status_code == 413
        assert called["stt"] is False
        assert list(_iter_tmp_mic_files()) == []

    def test_csrf_missing_rejected(self, _panel_env: Path) -> None:
        client = TestClient(panel_module.app)
        _prime_csrf(client)  # ensure cookie exists, but send no header
        res = client.post(
            "/voice/workbench/mic-upload",
            content=b"\x00" * 32,
            headers={**_operator_headers(), "content-type": "audio/webm"},
        )
        assert res.status_code == 403
        assert list(_iter_tmp_mic_files()) == []

    def test_non_audio_content_type_rejected(self, _panel_env: Path) -> None:
        """Mic uploads with a non-``audio/*`` Content-Type fail closed with 415.

        Only audio bytes make sense on this route — non-audio bodies would
        be written to a temp file and then error deep inside STT with a
        misleading diagnostic.  Reject them up front and never create a
        temp file or invoke STT/Vera.
        """
        called = {"stt": False}

        def _must_not_call_stt(**_kwargs: Any) -> STTResponse:  # pragma: no cover
            called["stt"] = True
            return _make_stt_response()

        client = TestClient(panel_module.app)
        csrf = _prime_csrf(client)
        with patch(
            "voxera.panel.routes_voice.transcribe_audio_file", side_effect=_must_not_call_stt
        ):
            res = _mic_post(
                client,
                body=b"hello world",
                csrf=csrf,
                content_type="text/plain",
            )
        assert res.status_code == 415
        assert called["stt"] is False
        assert list(_iter_tmp_mic_files()) == []

    def test_missing_content_type_rejected(self, _panel_env: Path) -> None:
        """A mic upload without any Content-Type header also fails closed."""
        called = {"stt": False}

        def _must_not_call_stt(**_kwargs: Any) -> STTResponse:  # pragma: no cover
            called["stt"] = True
            return _make_stt_response()

        client = TestClient(panel_module.app)
        csrf = _prime_csrf(client)
        with patch(
            "voxera.panel.routes_voice.transcribe_audio_file", side_effect=_must_not_call_stt
        ):
            res = client.post(
                "/voice/workbench/mic-upload",
                content=b"\x00" * 32,
                headers={**_operator_headers(), "x-csrf-token": csrf},
            )
        # TestClient may default to application/octet-stream when content is
        # supplied without content-type; either way, both are non-audio.
        assert res.status_code == 415
        assert called["stt"] is False
        assert list(_iter_tmp_mic_files()) == []

    def test_temp_file_cleaned_up_when_stt_raises(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression guard: the mic route must unlink the temp file even
        when the STT backend itself raises a surprise exception — the
        ``finally`` block is the only thing standing between a crashed
        STT backend and a disk full of leaked recordings.
        """

        async def _must_not_call_vera(**_kwargs: Any) -> dict[str, Any]:  # pragma: no cover
            raise AssertionError("Vera must not be called on STT exception")

        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _must_not_call_vera)

        def _blow_up(**_kwargs: Any) -> STTResponse:
            raise RuntimeError("simulated STT backend crash")

        client = TestClient(panel_module.app)
        csrf = _prime_csrf(client)
        with patch("voxera.panel.routes_voice.transcribe_audio_file", side_effect=_blow_up):
            res = _mic_post(
                client,
                body=b"\x00" * 32,
                csrf=csrf,
                query={"workbench_send_to_vera": "1"},
            )
        # Route surfaces the failure truthfully (200 + failure card), not 500.
        assert res.status_code == 200
        assert "simulated STT backend crash" in res.text
        # And the temp file is gone — privacy invariant.
        assert list(_iter_tmp_mic_files()) == []

    def test_informational_mic_run_does_not_call_vera(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the operator records from the mic but leaves "Send to Vera"
        off, STT still runs (they can see what was heard) but the Vera
        lane stays dark.  The mic route must obey the same opt-in gate as
        the file-path lane.
        """
        called = {"vera": False}

        async def _must_not_call_vera(**_kwargs: Any) -> dict[str, Any]:  # pragma: no cover
            called["vera"] = True
            return {"answer": "should not happen", "status": "ok:test"}

        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _must_not_call_vera)
        stt = _make_stt_response(transcript="quiet informational run")
        client = TestClient(panel_module.app)
        csrf = _prime_csrf(client)
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            res = _mic_post(client, body=b"\x00" * 32, csrf=csrf)  # no send_to_vera
        assert res.status_code == 200
        assert "quiet informational run" in res.text
        assert called["vera"] is False
        # UI truthfully explains why Vera was not called.
        assert "Send to Vera" in res.text
        assert list(_iter_tmp_mic_files()) == []

    def test_stt_failure_does_not_call_vera(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called = {"vera": False}

        async def _must_not_call_vera(**_kwargs: Any) -> dict[str, Any]:  # pragma: no cover
            called["vera"] = True
            return {"answer": "should not happen", "status": "ok:test"}

        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _must_not_call_vera)
        from voxera.voice.stt_protocol import STT_STATUS_FAILED

        stt = _make_stt_response(status=STT_STATUS_FAILED, transcript=None)
        stt_with_error = STTResponse(
            request_id=stt.request_id,
            status=stt.status,
            transcript=None,
            language=None,
            audio_duration_ms=stt.audio_duration_ms,
            error="Decode failure",
            error_class="backend_error",
            backend=stt.backend,
            started_at_ms=stt.started_at_ms,
            finished_at_ms=stt.finished_at_ms,
            schema_version=stt.schema_version,
            inference_ms=stt.inference_ms,
        )
        client = TestClient(panel_module.app)
        csrf = _prime_csrf(client)
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt_with_error):
            res = _mic_post(
                client,
                body=b"\x00" * 32,
                csrf=csrf,
                query={"workbench_send_to_vera": "1"},
            )
        assert res.status_code == 200
        assert "Decode failure" in res.text
        assert called["vera"] is False


class TestMicUploadSurfaceTruth:
    def test_source_badge_labels_microphone_origin(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="hello")
        client = TestClient(panel_module.app)
        csrf = _prime_csrf(client)
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            res = _mic_post(
                client,
                body=b"\x00" * 32,
                csrf=csrf,
                query={"workbench_send_to_vera": "1"},
            )
        assert res.status_code == 200
        # Truthful surface framing.
        assert 'data-testid="voice-workbench-source-mic"' in res.text
        assert "browser microphone" in res.text
        assert "Temp Audio File" in res.text
        lowered = res.text.lower()
        # Truth invariants.
        assert "job submitted" not in lowered
        assert "has been submitted" not in lowered
        assert "executed successfully" not in lowered

    def test_file_path_run_still_labels_file_source(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression guard: the typed-path lane still renders a truthful file badge."""
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="hello from a path")
        client = TestClient(panel_module.app)
        csrf = _prime_csrf(client)
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            res = client.post(
                "/voice/workbench/run",
                data={
                    "workbench_audio_path": "/tmp/test.wav",
                    "workbench_send_to_vera": "1",
                    "csrf_token": csrf,
                },
                headers=_operator_headers(),
                follow_redirects=False,
            )
        assert res.status_code == 200
        assert 'data-testid="voice-workbench-source-file"' in res.text
        assert "/tmp/test.wav" in res.text


class TestMicUploadActionLanes:
    def test_action_oriented_mic_upload_drafts_preview(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mic-origin action-oriented transcripts reach the same drafting seam."""
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="write a note called hello.txt")
        session_id = "vera-mic-preview-session"
        client = TestClient(panel_module.app)
        csrf = _prime_csrf(client)
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            res = _mic_post(
                client,
                body=b"\x00" * 32,
                csrf=csrf,
                query={"workbench_send_to_vera": "1", "workbench_session_id": session_id},
            )
        assert res.status_code == 200
        assert 'data-testid="voice-workbench-preview-drafted"' in res.text
        preview = session_store.read_session_preview(_panel_env, session_id)
        assert isinstance(preview, dict)
        assert "goal" in preview
        assert isinstance(preview.get("write_file"), dict)

    def test_spoken_submit_from_mic_dispatches_lifecycle(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``submit it`` said into the mic routes through the canonical submit seam."""
        stt = _make_stt_response(transcript="submit it")

        submit_calls: dict[str, Any] = {}

        def _fake_dispatch(
            *,
            classification: Any,
            session_id: str,
            queue_root: Path,
            **_kwargs: Any,
        ) -> VoiceWorkbenchLifecycleResult:
            submit_calls["action"] = classification.kind
            submit_calls["session_id"] = session_id
            return VoiceWorkbenchLifecycleResult(
                ok=True,
                action=LIFECYCLE_ACTION_SUBMIT,
                status=LIFECYCLE_STATUS_SUBMITTED,
                ack="Submitted preview as job inbox-abc.",
                job_id="inbox-abc",
            )

        monkeypatch.setattr(
            "voxera.panel.routes_voice.dispatch_spoken_lifecycle_command",
            _fake_dispatch,
        )
        client = TestClient(panel_module.app)
        csrf = _prime_csrf(client)
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            res = _mic_post(
                client,
                body=b"\x00" * 32,
                csrf=csrf,
                query={"workbench_send_to_vera": "1"},
            )
        assert res.status_code == 200
        assert submit_calls.get("action") == LIFECYCLE_ACTION_SUBMIT
        assert 'data-testid="voice-workbench-lifecycle"' in res.text
        assert "inbox-abc" in res.text


class TestMicUIRendering:
    def test_voice_status_page_renders_mic_capture_block(self, _panel_env: Path) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        # Mic capture block + controls.
        assert 'data-testid="voice-workbench-mic-capture"' in res.text
        assert 'data-testid="voice-workbench-mic-start"' in res.text
        assert 'data-testid="voice-workbench-mic-stop"' in res.text
        assert 'data-testid="voice-workbench-mic-state"' in res.text
        # Enhancer script reference.
        assert "voice_mic_capture.js" in res.text
        # Noscript fallback so the file-path lane still works.
        assert 'data-testid="voice-workbench-mic-noscript"' in res.text

    def test_mic_capture_block_hidden_by_default(self, _panel_env: Path) -> None:
        """The mic block must ship hidden so browsers without MediaRecorder never
        see a dead UI, and browsers with JS disabled fall back to the file-path
        form.  Only the enhancer script reveals the block after feature-detect.
        """
        client = TestClient(panel_module.app)
        res = client.get("/voice/status", headers=_operator_headers())
        assert res.status_code == 200
        # Find the block tag and assert it has the ``hidden`` attribute.
        marker = 'data-testid="voice-workbench-mic-capture"'
        idx = res.text.find(marker)
        assert idx != -1
        # Look at the opening tag slice — conservative window that cannot
        # accidentally match a later element's attribute.
        tag_start = res.text.rfind("<", 0, idx)
        tag_end = res.text.find(">", idx)
        assert tag_start != -1 and tag_end != -1
        tag = res.text[tag_start : tag_end + 1]
        assert " hidden" in tag or tag.endswith("hidden>") or tag.rstrip(">").endswith(" hidden")

    def test_mic_capture_script_asset_served(self, _panel_env: Path) -> None:
        client = TestClient(panel_module.app)
        res = client.get("/static/voice_mic_capture.js", headers=_operator_headers())
        assert res.status_code == 200
        body = res.text
        # Sanity guards: script is an operator-initiated-only capture,
        # uses MediaRecorder, POSTs to the canonical mic-upload route.
        assert "MediaRecorder" in body
        assert "/voice/workbench/mic-upload" in body
        assert "getUserMedia" in body

    def test_mic_script_never_auto_starts_capture(self, _panel_env: Path) -> None:
        """Privacy red-line: the enhancer must only request the mic inside the
        explicit Start-click handler.  A simple structural assertion catches
        the regression where ``getUserMedia`` migrates to module top-level or
        runs on load / visibilitychange / any non-click signal.
        """
        client = TestClient(panel_module.app)
        res = client.get("/static/voice_mic_capture.js", headers=_operator_headers())
        assert res.status_code == 200
        body = res.text
        # The only actual *call* to getUserMedia(...) must live inside the
        # Start-click handler.  Other mentions (the comment header, the
        # feature-detect ``typeof`` check, the unsupported-browser error
        # string) are fine; what is never OK is a second invocation site.
        assert body.count(".getUserMedia(") == 1
        start_handler_idx = body.find('startBtn.addEventListener("click"')
        assert start_handler_idx != -1
        mic_call_idx = body.find(".getUserMedia(")
        assert mic_call_idx > start_handler_idx, (
            "getUserMedia must be called only from the Start-click handler — "
            "never on load, visibility, or any auto-start signal."
        )
        # Explicit red-line: no streaming, no always-on listening, no
        # auto-start signals of any kind.  Checks the *code* the script
        # runs, not its comment header — the header is allowed (and in
        # fact required) to say "no autoplay", "no always-on listening".
        for forbidden in (
            "setInterval(",
            "requestAnimationFrame(",
            "new WebSocket(",
            "new EventSource(",
            'addEventListener("visibilitychange"',
            'addEventListener("DOMContentLoaded"',
            "window.onload",
            'autoplay"',
        ):
            assert forbidden not in body, f"mic enhancer must not use {forbidden}"

    def test_mic_framing_blurb_does_not_over_promise_locality(
        self, _panel_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The result-page framing for a mic-origin run must not claim the
        audio stays on this machine.  The STT backend can be local or remote
        (whisper-local, a cloud whisper endpoint, etc.); any phrasing that
        implies otherwise is a privacy overclaim.
        """
        monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
        stt = _make_stt_response(transcript="hello")
        client = TestClient(panel_module.app)
        csrf = _prime_csrf(client)
        with patch("voxera.panel.routes_voice.transcribe_audio_file", return_value=stt):
            res = _mic_post(
                client,
                body=b"\x00" * 32,
                csrf=csrf,
                query={"workbench_send_to_vera": "1"},
            )
        assert res.status_code == 200
        # The blurb explicitly frames the pipeline as the same as the
        # file-path lane — no "local-only" claim, no "stays on this
        # machine" claim.
        lowered = res.text.lower()
        assert "same voice pipeline" in lowered
        assert "local voice pipeline" not in lowered
        assert "stays on this machine" not in lowered
        assert "never leaves" not in lowered
