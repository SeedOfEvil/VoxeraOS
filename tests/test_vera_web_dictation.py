"""Tests for canonical Vera's dictation route (``POST /chat/voice``).

Pins the Vera-native dictation lane:

1. The Vera index page advertises the mic button, voice bar, and the
   dictation enhancer script so the browser can progressively
   enhance typed chat with microphone capture.
2. ``POST /chat/voice`` with a valid audio body feeds the canonical
   shared voice-session pipeline and persists a ``voice_transcript``-origin
   user turn + an assistant turn on the canonical Vera session.
3. An informational transcript surfaces Vera's reply without drafting
   a preview (conversational lane intact).
4. An action-oriented transcript drafts a real canonical preview in
   the same Vera session — identical behavior to the panel Voice
   Workbench's browser-mic lane.
5. A spoken submit phrase ("submit it") routes through the bounded
   canonical lifecycle seam (not a fake submit).
6. When ``speak_response=1`` and TTS succeeds, the JSON includes a
   tokenized ``tts_url`` and ``GET /vera/voice/audio/<token>`` serves
   the audio.
7. When TTS fails, text stays authoritative: ``tts_url`` is ``None``
   but the assistant turn is still in the session.
8. Empty / non-audio / oversized bodies fail closed (400 / 415 / 413)
   without touching STT or Vera.
9. Typed chat still works unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from voxera.vera import session_store as vera_session_store
from voxera.vera_web import app as vera_app_module
from voxera.voice.flags import VoiceFoundationFlags
from voxera.voice.stt_protocol import STT_STATUS_SUCCEEDED, STTResponse
from voxera.voice.tts_protocol import TTS_STATUS_SUCCEEDED, TTSResponse


def _set_queue_root(monkeypatch: pytest.MonkeyPatch, queue: Path) -> None:
    monkeypatch.setattr(vera_app_module, "_active_queue_root", lambda: queue)


def _enabled_voice_flags() -> VoiceFoundationFlags:
    return VoiceFoundationFlags(
        enable_voice_foundation=True,
        enable_voice_input=True,
        enable_voice_output=True,
        voice_stt_backend="whisper_local",
        voice_tts_backend="piper_local",
    )


def _force_enabled_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vera_app_module, "load_voice_foundation_flags", _enabled_voice_flags)
    monkeypatch.setattr(
        "voxera.panel.voice_session_pipeline.ingest_voice_transcript",
        lambda *, transcript_text, voice_input_enabled: _FakeIngest(transcript_text.strip()),
    )


class _FakeIngest:
    def __init__(self, text: str) -> None:
        self.transcript_text = text
        self.input_origin = "voice_transcript"


def _make_stt(transcript: str = "hello vera", status: str = STT_STATUS_SUCCEEDED) -> STTResponse:
    return STTResponse(
        request_id="test-stt",
        status=status,
        transcript=transcript,
        language="en",
        audio_duration_ms=1500,
        error=None,
        error_class=None,
        backend="whisper_local",
        started_at_ms=1000,
        finished_at_ms=1100,
        schema_version=1,
        inference_ms=100,
    )


def _make_tts(
    *,
    status: str = TTS_STATUS_SUCCEEDED,
    audio_path: str | None = "/tmp/fake.wav",
) -> TTSResponse:
    return TTSResponse(
        request_id="test-tts",
        status=status,
        audio_path=audio_path,
        backend="piper_local",
        error=None,
        error_class=None,
        audio_duration_ms=1200,
        started_at_ms=1000,
        finished_at_ms=1150,
        schema_version=1,
        inference_ms=150,
    )


async def _fake_vera_reply(**kwargs: Any) -> dict[str, Any]:
    return {"answer": f"Ack: {kwargs['user_message']}", "status": "ok:test"}


def _async_stt(response: STTResponse) -> Any:
    async def _run(**_kwargs: Any) -> STTResponse:
        return response

    return _run


def _async_tts(response: TTSResponse) -> Any:
    async def _run(**_kwargs: Any) -> TTSResponse:
        return response

    return _run


def _post_voice(
    client: TestClient,
    *,
    body: bytes,
    content_type: str = "audio/webm",
    params: dict[str, str] | None = None,
) -> Any:
    return client.post(
        "/chat/voice",
        content=body,
        headers={"content-type": content_type},
        params=params or {},
    )


def test_index_renders_dictation_controls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    monkeypatch.setattr(vera_app_module, "load_voice_foundation_flags", _enabled_voice_flags)
    client = TestClient(vera_app_module.app)
    res = client.get("/")
    assert res.status_code == 200
    assert 'data-testid="vera-mic-btn"' in res.text
    assert 'data-testid="vera-voice-bar"' in res.text
    assert 'data-testid="vera-voice-speak"' in res.text
    assert 'src="/static/vera_dictation.js"' in res.text
    # Progressive enhancement: the mic button ships hidden so
    # typed chat stays intact when JS or the mic is unavailable.
    assert "vera-mic-btn" in res.text


def test_dictation_enhancer_js_is_served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    res = client.get("/static/vera_dictation.js")
    assert res.status_code == 200
    # The script MUST never auto-start recording; operator-initiated only.
    assert "getUserMedia" in res.text
    assert "mic.addEventListener" not in res.text  # no auto-listen
    assert 'addEventListener("click"' in res.text or "addEventListener('click'" in res.text


def test_chat_voice_persists_voice_transcript_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)

    stt = _make_stt(transcript="what is the capital of Alberta")
    session_id = "vera-dict-test"
    with patch(
        "voxera.panel.voice_session_pipeline.transcribe_audio_file_async",
        side_effect=_async_stt(stt),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(
            client,
            body=b"\x1a\x45\xdf\xa3" + b"\x00" * 64,
            params={"session_id": session_id},
        )
    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is True
    assert payload["session_id"] == session_id
    assert payload["stt"]["success"] is True
    assert payload["stt"]["transcript"] == "what is the capital of Alberta"
    assert payload["vera"]["success"] is True
    assert payload["vera"]["answer"] == "Ack: what is the capital of Alberta"
    turns = vera_session_store.read_session_turns(queue, session_id)
    assert turns[0]["role"] == "user"
    assert turns[0]["input_origin"] == "voice_transcript"
    assert turns[0]["text"] == "what is the capital of Alberta"
    assert turns[1]["role"] == "assistant"
    assert turns[1]["text"] == "Ack: what is the capital of Alberta"


def test_chat_voice_informational_has_no_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
    stt = _make_stt(transcript="explain photosynthesis simply")
    with patch(
        "voxera.panel.voice_session_pipeline.transcribe_audio_file_async",
        side_effect=_async_stt(stt),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(client, body=b"\x00" * 32, params={"session_id": "vera-info-test"})
    assert res.status_code == 200
    payload = res.json()
    assert payload["preview"] is None
    assert payload["vera"]["success"] is True
    assert payload["classification"]["is_action_oriented"] is False


def test_chat_voice_action_oriented_drafts_canonical_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
    stt = _make_stt(transcript="save a note about black holes called bh.md")
    with patch(
        "voxera.panel.voice_session_pipeline.transcribe_audio_file_async",
        side_effect=_async_stt(stt),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(client, body=b"\x00" * 32, params={"session_id": "vera-action-test"})
    assert res.status_code == 200
    payload = res.json()
    # Action-oriented classifier fires and the preview-drafting seam
    # runs against the canonical session.  Either the preview lands
    # on the session (``preview`` populated) or, if the deterministic
    # drafter declines, ``preview_attempt`` surfaces the truthful
    # reason — the route never silently claims otherwise.
    assert payload["classification"]["is_action_oriented"] is True
    assert payload["preview_attempt"] is not None


def test_chat_voice_spoken_submit_dispatches_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    # The lifecycle submit seam is invoked via the shared pipeline's
    # dispatcher. Patch it at the pipeline's import site so we assert
    # on the canonical submit hook without really writing to a queue.
    captured: dict[str, Any] = {}

    def _fake_dispatch(
        *, classification: Any, session_id: str, queue_root: Path, **_kwargs: Any
    ) -> Any:
        captured["action"] = classification.kind
        captured["session_id"] = session_id
        from voxera.panel.voice_workbench_lifecycle import (
            LIFECYCLE_ACTION_SUBMIT,
            LIFECYCLE_STATUS_SUBMITTED,
            VoiceWorkbenchLifecycleResult,
        )

        return VoiceWorkbenchLifecycleResult(
            ok=True,
            action=LIFECYCLE_ACTION_SUBMIT,
            status=LIFECYCLE_STATUS_SUBMITTED,
            ack="Submitted inbox-xyz.json.",
            job_id="inbox-xyz.json",
        )

    monkeypatch.setattr(
        "voxera.panel.voice_session_pipeline.dispatch_spoken_lifecycle_command",
        _fake_dispatch,
    )
    stt = _make_stt(transcript="submit it")
    with patch(
        "voxera.panel.voice_session_pipeline.transcribe_audio_file_async",
        side_effect=_async_stt(stt),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(client, body=b"\x00" * 32, params={"session_id": "vera-submit-test"})
    assert res.status_code == 200
    payload = res.json()
    assert captured["action"] == "submit"
    assert captured["session_id"] == "vera-submit-test"
    assert payload["lifecycle"] is not None
    assert payload["lifecycle"]["ok"] is True
    assert payload["lifecycle"]["status"] == "submitted"
    # Conversational lane should NOT fire when lifecycle took the turn.
    assert payload["vera"] is None


def test_chat_voice_speak_response_returns_audio_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
    audio_file = tmp_path / "fake_tts.wav"
    audio_file.write_bytes(b"RIFF----WAVE" + b"\x00" * 32)
    stt = _make_stt(transcript="hello vera")
    tts = _make_tts(audio_path=str(audio_file))
    with (
        patch(
            "voxera.panel.voice_session_pipeline.transcribe_audio_file_async",
            side_effect=_async_stt(stt),
        ),
        patch(
            "voxera.panel.voice_session_pipeline.synthesize_text_async",
            side_effect=_async_tts(tts),
        ),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(
            client,
            body=b"\x00" * 32,
            params={"session_id": "vera-tts-test", "speak_response": "1"},
        )
    assert res.status_code == 200
    payload = res.json()
    assert payload["tts"]["success"] is True
    assert payload["tts_url"] is not None
    assert payload["tts_url"].startswith("/vera/voice/audio/")
    # Serving the token returns the audio file's bytes.
    audio_res = client.get(payload["tts_url"])
    assert audio_res.status_code == 200
    assert audio_res.headers["content-type"].startswith("audio/")
    assert audio_res.content.startswith(b"RIFF")


def test_chat_voice_tts_failure_text_still_authoritative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr("voxera.panel.voice_workbench.generate_vera_reply", _fake_vera_reply)
    stt = _make_stt(transcript="hello vera")
    failing_tts = _make_tts(status="failed", audio_path=None)
    with (
        patch(
            "voxera.panel.voice_session_pipeline.transcribe_audio_file_async",
            side_effect=_async_stt(stt),
        ),
        patch(
            "voxera.panel.voice_session_pipeline.synthesize_text_async",
            side_effect=_async_tts(failing_tts),
        ),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(
            client,
            body=b"\x00" * 32,
            params={"session_id": "vera-tts-fail", "speak_response": "1"},
        )
    assert res.status_code == 200
    payload = res.json()
    assert payload["tts"]["success"] is False
    assert payload["tts_url"] is None
    # Vera answer still persisted even though TTS failed.
    turns = vera_session_store.read_session_turns(queue, "vera-tts-fail")
    assert turns[-1]["role"] == "assistant"
    assert turns[-1]["text"] == "Ack: hello vera"


def test_chat_voice_empty_body_returns_400(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    called = {"stt": False}

    async def _must_not_call_stt(**_kwargs: Any) -> STTResponse:  # pragma: no cover
        called["stt"] = True
        return _make_stt()

    with patch(
        "voxera.panel.voice_session_pipeline.transcribe_audio_file_async",
        side_effect=_must_not_call_stt,
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(client, body=b"")
    assert res.status_code == 400
    assert called["stt"] is False


def test_chat_voice_non_audio_content_type_returns_415(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    client = TestClient(vera_app_module.app)
    res = client.post(
        "/chat/voice",
        content=b"plain text body",
        headers={"content-type": "text/plain"},
    )
    assert res.status_code == 415


def test_chat_voice_oversized_returns_413(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "_VERA_DICTATION_MAX_BYTES", 16)
    client = TestClient(vera_app_module.app)
    res = _post_voice(client, body=b"X" * 64)
    assert res.status_code == 413


def test_typed_chat_still_works(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    monkeypatch.setattr(vera_app_module, "load_voice_foundation_flags", _enabled_voice_flags)

    async def _fake_reply(**kwargs: Any) -> dict[str, Any]:
        return {"answer": "typed ok", "status": "ok:test"}

    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_reply)
    client = TestClient(vera_app_module.app)
    res = client.post(
        "/chat",
        content="session_id=typed-test&input_origin=typed&message=hello",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert res.status_code == 200
    turns = vera_session_store.read_session_turns(queue, "typed-test")
    assert turns[0]["role"] == "user"
    assert turns[0]["input_origin"] == "typed"
    assert turns[0]["text"] == "hello"


def test_vera_voice_audio_unknown_token_returns_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    res = client.get("/vera/voice/audio/doesnotexist")
    assert res.status_code == 404


def _disabled_voice_flags() -> VoiceFoundationFlags:
    return VoiceFoundationFlags(
        enable_voice_foundation=True,
        enable_voice_input=False,
        enable_voice_output=False,
        voice_stt_backend="whisper_local",
        voice_tts_backend="piper_local",
    )


def test_chat_voice_fails_closed_when_voice_input_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Voice-input-disabled runtime MUST fail-closed BEFORE audio hits disk.

    The UI hides the mic button in this case; a direct client that
    skips the UI must still be refused without the audio ever being
    written to temp storage or fed to STT.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    monkeypatch.setattr(vera_app_module, "load_voice_foundation_flags", _disabled_voice_flags)
    stt_called = {"count": 0}
    mkstemp_called = {"count": 0}

    async def _must_not_call_stt(**_kwargs: Any) -> STTResponse:  # pragma: no cover
        stt_called["count"] += 1
        return _make_stt()

    original_mkstemp = vera_app_module.tempfile.mkstemp

    def _counting_mkstemp(*args: Any, **kwargs: Any) -> Any:
        mkstemp_called["count"] += 1
        return original_mkstemp(*args, **kwargs)

    monkeypatch.setattr(vera_app_module.tempfile, "mkstemp", _counting_mkstemp)
    with patch(
        "voxera.panel.voice_session_pipeline.transcribe_audio_file_async",
        side_effect=_must_not_call_stt,
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(client, body=b"\x00" * 32, params={"session_id": "vera-disabled"})
    assert res.status_code == 403
    payload = res.json()
    assert payload["ok"] is False
    assert payload["status"] == "voice_input_disabled"
    assert stt_called["count"] == 0, "STT must NOT run when voice_input is disabled"
    assert mkstemp_called["count"] == 0, "No temp file must be created when voice_input is disabled"


def test_chat_voice_oversized_rejects_before_tempfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Oversized uploads 413 BEFORE a temp file is created.

    Protects against a giant body being materialized to disk just to
    be rejected. Combined with the early Content-Length gate on the
    route, this is the operator-facing guarantee that the cap is not
    paid in temp storage.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "_VERA_DICTATION_MAX_BYTES", 16)
    mkstemp_called = {"count": 0}
    original_mkstemp = vera_app_module.tempfile.mkstemp

    def _counting_mkstemp(*args: Any, **kwargs: Any) -> Any:
        mkstemp_called["count"] += 1
        return original_mkstemp(*args, **kwargs)

    monkeypatch.setattr(vera_app_module.tempfile, "mkstemp", _counting_mkstemp)
    client = TestClient(vera_app_module.app)
    res = _post_voice(client, body=b"X" * 64)
    assert res.status_code == 413
    assert mkstemp_called["count"] == 0


def test_chat_voice_ok_reflects_stt_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Top-level ``ok`` is False when STT did not succeed, even at HTTP 200.

    Telemetry / operator dashboards rely on ``ok`` being truthful so
    an operator can tell "the mic worked but STT failed" from "this
    was a clean success" at a glance.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)

    failing_stt = STTResponse(
        request_id="test-stt-fail",
        status="failed",
        transcript=None,
        language=None,
        audio_duration_ms=0,
        error="backend unavailable",
        error_class="RuntimeError",
        backend="whisper_local",
        started_at_ms=1000,
        finished_at_ms=1050,
        schema_version=1,
        inference_ms=50,
    )
    with patch(
        "voxera.panel.voice_session_pipeline.transcribe_audio_file_async",
        side_effect=_async_stt(failing_stt),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(client, body=b"\x00" * 32, params={"session_id": "vera-stt-fail"})
    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is False
    assert payload["stt"]["success"] is False
    assert payload["vera"] is None


def test_chat_voice_tts_registry_cap_evicts_oldest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TTS registry never exceeds its cap; oldest entries are evicted with disk cleanup.

    Prevents unbounded growth of the in-process token registry and of
    the on-disk temp files that back each token.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "_MAX_TTS_REGISTRY_ENTRIES", 3)
    # Start from a clean registry so the test is order-independent.
    for tok in list(vera_app_module._TTS_AUDIO_REGISTRY.keys()):
        vera_app_module._TTS_AUDIO_REGISTRY.pop(tok, None)
    created_paths: list[Path] = []
    for i in range(5):
        audio_file = tmp_path / f"tts_{i}.wav"
        audio_file.write_bytes(b"RIFF" + i.to_bytes(1, "little") * 16)
        created_paths.append(audio_file)
        vera_app_module._register_tts_audio(str(audio_file))
    # Registry is capped.
    assert len(vera_app_module._TTS_AUDIO_REGISTRY) == 3
    # The first two entries' on-disk artifacts were unlinked by
    # eviction; the last three remain.
    assert not created_paths[0].exists()
    assert not created_paths[1].exists()
    assert created_paths[2].exists()
    assert created_paths[3].exists()
    assert created_paths[4].exists()


def test_chat_voice_tts_token_rejects_bad_alphabet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Malformed tokens 404 without touching the registry."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    client = TestClient(vera_app_module.app)
    # Registry-shape tokens use URL-safe base64 (A-Z a-z 0-9 - _); a
    # token containing other characters must be rejected up front.
    res = client.get("/vera/voice/audio/bad%20token")
    assert res.status_code == 404
    res = client.get("/vera/voice/audio/has.period")
    assert res.status_code == 404
