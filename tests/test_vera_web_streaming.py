from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from voxera.models import AppConfig, BrainConfig
from voxera.vera import service as vera_service
from voxera.vera_web import app as vera_app_module
from voxera.voice.flags import VoiceFoundationFlags
from voxera.voice.stt_protocol import STT_STATUS_SUCCEEDED, STTResponse


def _set_queue_root(monkeypatch, queue: Path) -> None:
    monkeypatch.setattr(vera_app_module, "_active_queue_root", lambda: queue)


def _voice_flags() -> VoiceFoundationFlags:
    return VoiceFoundationFlags(
        enable_voice_foundation=True,
        enable_voice_input=True,
        enable_voice_output=True,
        voice_stt_backend="whisper_local",
        voice_tts_backend="piper_local",
    )


def _ndjson_events(response) -> list[dict[str, Any]]:
    lines = [line for line in response.text.splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


def test_typed_chat_stream_emits_progressive_and_done(tmp_path: Path, monkeypatch) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    monkeypatch.setattr(vera_app_module, "load_voice_foundation_flags", _voice_flags)

    async def _fake_turn(**kwargs):
        hook = kwargs.get("stream_delta_hook")
        if hook is not None:
            await hook("Hello ")
            await hook("world")
        return vera_app_module.ChatTurnResult(
            session_id="stream-typed",
            turns=[
                {"role": "user", "text": "hi", "input_origin": "typed"},
                {"role": "assistant", "text": "Hello world"},
            ],
            status="ok:test",
            assistant_text="Hello world",
            preview=None,
        )

    monkeypatch.setattr(vera_app_module, "run_vera_chat_turn", _fake_turn)
    client = TestClient(vera_app_module.app)
    res = client.post(
        "/chat/stream",
        data={"session_id": "stream-typed", "input_origin": "typed", "message": "hi"},
    )
    assert res.status_code == 200
    events = _ndjson_events(res)
    assert events[0]["type"] == "start"
    assert [e["delta"] for e in events if e["type"] == "assistant_delta"] == ["Hello ", "world"]
    assert events[-1]["type"] == "done"
    assert events[-1]["assistant_text"] == "Hello world"


def test_voice_stream_emits_transcript_then_assistant_deltas(tmp_path: Path, monkeypatch) -> None:
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    monkeypatch.setattr(vera_app_module, "load_voice_foundation_flags", _voice_flags)

    async def _fake_stt(**_kwargs):
        return STTResponse(
            request_id="stt-1",
            status=STT_STATUS_SUCCEEDED,
            transcript="voice hello",
            language="en",
            audio_duration_ms=1000,
            error=None,
            error_class=None,
            backend="whisper_local",
            started_at_ms=1,
            finished_at_ms=2,
            schema_version=1,
            inference_ms=1,
        )

    async def _fake_turn(**kwargs):
        hook = kwargs.get("stream_delta_hook")
        if hook is not None:
            await hook("voice ")
            await hook("reply")
        return vera_app_module.ChatTurnResult(
            session_id="voice-stream",
            turns=[
                {"role": "user", "text": "voice hello", "input_origin": "voice_transcript"},
                {"role": "assistant", "text": "voice reply"},
            ],
            status="ok:test",
            assistant_text="voice reply",
            preview=None,
        )

    monkeypatch.setattr(vera_app_module, "transcribe_audio_file_async", _fake_stt)
    monkeypatch.setattr(vera_app_module, "run_vera_chat_turn", _fake_turn)

    client = TestClient(vera_app_module.app)
    res = client.post(
        "/chat/voice/stream?session_id=voice-stream",
        content=b"\x00" * 32,
        headers={"content-type": "audio/webm"},
    )
    assert res.status_code == 200
    events = _ndjson_events(res)
    assert events[0]["type"] == "stt"
    assert events[0]["transcript"] == "voice hello"
    assert [e["delta"] for e in events if e["type"] == "assistant_delta"] == ["voice ", "reply"]
    assert events[-1]["type"] == "done"


def test_generate_vera_reply_stream_falls_back_to_batch_before_any_text(monkeypatch) -> None:
    provider = BrainConfig(
        type="openai_compat",
        model="test",
        base_url="https://example.com",
        api_key_ref="key",
    )
    cfg = AppConfig(brain={"primary": provider})
    monkeypatch.setattr(vera_service, "load_app_config", lambda: cfg)

    class _Brain:
        async def generate_stream(self, *_args, **_kwargs):
            if False:
                yield ""  # pragma: no cover
            raise RuntimeError("stream unavailable")

        async def generate(self, *_args, **_kwargs):
            return SimpleNamespace(text="batch answer")

    monkeypatch.setattr(vera_service, "_create_brain", lambda _provider: _Brain())

    out = asyncio.run(
        vera_service.generate_vera_reply(
            turns=[],
            user_message="hello",
            stream_delta_hook=(lambda _delta: asyncio.sleep(0)),
        )
    )
    assert out["answer"] == "batch answer"


def test_generate_vera_reply_stream_failure_after_partial_raises(monkeypatch) -> None:
    provider = BrainConfig(
        type="openai_compat",
        model="test",
        base_url="https://example.com",
        api_key_ref="key",
    )
    cfg = AppConfig(brain={"primary": provider})
    monkeypatch.setattr(vera_service, "load_app_config", lambda: cfg)

    class _Brain:
        async def generate_stream(self, *_args, **_kwargs):
            yield "partial "
            raise RuntimeError("boom")

        async def generate(self, *_args, **_kwargs):
            return SimpleNamespace(text="should not persist")

    monkeypatch.setattr(vera_service, "_create_brain", lambda _provider: _Brain())

    async def _consume() -> None:
        deltas: list[str] = []

        async def _hook(delta: str) -> None:
            deltas.append(delta)

        try:
            await vera_service.generate_vera_reply(
                turns=[],
                user_message="hello",
                stream_delta_hook=_hook,
            )
        except vera_service.StreamInterruptedAfterPartialError:
            assert deltas == ["partial "]
            return
        raise AssertionError("Expected StreamInterruptedAfterPartialError")

    asyncio.run(_consume())
