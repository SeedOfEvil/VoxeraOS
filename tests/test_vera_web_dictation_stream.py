"""Tests for canonical Vera's streaming dictation route (``POST /chat/voice/stream``).

Pins the incremental-reply + early-chunk TTS seam that makes voice
turns feel alive:

1. The endpoint emits an NDJSON stream (``application/x-ndjson``)
   whose events land in the documented order: ``ready`` -> ``stt`` ->
   ``reply_start`` -> ``text_chunk`` (one per chunk) -> ``audio_chunk``
   or ``audio_chunk_failed`` (one per chunk when speak_response=1) ->
   ``done``.
2. Text chunk ordering is strictly increasing; the concatenation of
   chunk texts plus the ``done`` event's ``assistant_text`` carry the
   full canonical reply so text remains authoritative.
3. When ``speak_response=1``, audio chunk URLs are emitted in order
   (index 0, 1, ...), the first chunk carries
   ``tts_first_chunk_ms`` + ``first_stable_speech_chunk_ms``, and
   ``tts_first_chunk_ms`` < ``total_ms`` (the first audio is ready
   before the full turn finishes).
4. When a chunk's TTS fails, the endpoint emits ``audio_chunk_failed``
   for that chunk and continues synthesizing the remaining chunks —
   the rest of the reply is not silenced.  Text stays authoritative
   either way.
5. STT failure ends the stream with a ``done`` event carrying
   ``ok=false`` and no text/audio chunks; preview truth is preserved
   via the canonical ``read_session_preview``.
6. With ``speak_response=0`` (text-only), ``text_chunk`` events still
   flow but no TTS happens and ``total_tts_ms`` stays absent.
7. The enhancer script served from ``/static/vera_dictation.js``
   references the streaming endpoint + progressive bubble strings so
   a silent rename cannot drift the surface.

These tests do NOT exercise real STT/TTS models.  ``transcribe_audio_file_async``
and ``synthesize_text_async`` are patched so the stream assertions run
deterministically.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from voxera.vera_web import app as vera_app_module
from voxera.voice.flags import VoiceFoundationFlags
from voxera.voice.stt_protocol import STT_STATUS_SUCCEEDED, STTResponse
from voxera.voice.tts_protocol import TTS_STATUS_SUCCEEDED, TTSResponse


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


def _set_queue_root(monkeypatch: pytest.MonkeyPatch, queue: Path) -> None:
    monkeypatch.setattr(vera_app_module, "_active_queue_root", lambda: queue)


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


def _async_stt(response: STTResponse) -> Any:
    async def _run(**_kwargs: Any) -> STTResponse:
        return response

    return _run


def _post_stream(
    client: TestClient,
    *,
    body: bytes = b"\x00" * 32,
    content_type: str = "audio/webm",
    params: dict[str, str] | None = None,
) -> Any:
    return client.post(
        "/chat/voice/stream",
        content=body,
        headers={"content-type": content_type},
        params=params or {},
    )


def _parse_ndjson(body: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in body.split("\n"):
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


def _multi_sentence_reply(**kwargs: Any) -> dict[str, Any]:
    """Fake Vera reply with three sentences so chunking produces 3 chunks."""
    return {
        "answer": (
            "The queue looks healthy this morning. "
            "I checked the panel metrics and nothing stands out. "
            "Let me know if you want me to inspect a specific service."
        ),
        "status": "ok:test",
    }


async def _fake_vera_reply_multi(**kwargs: Any) -> dict[str, Any]:
    return _multi_sentence_reply(**kwargs)


async def _fake_vera_reply_single(**kwargs: Any) -> dict[str, Any]:
    return {"answer": "Sure — done.", "status": "ok:test"}


# =============================================================================
# Happy-path: event schema + ordering + streaming semantics
# =============================================================================


def test_stream_emits_events_in_documented_order_text_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Text-only dictation (``speak_response=0``) still streams text chunks.

    No TTS events are emitted.  The event order must still be
    ``ready`` → ``stt`` → ``reply_start`` → ``text_chunk``... → ``done``.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_vera_reply_multi)

    stt = _make_stt(transcript="tell me about the queue")
    with patch.object(
        vera_app_module,
        "transcribe_audio_file_async",
        side_effect=_async_stt(stt),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_stream(client, params={"session_id": "stream-text"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/x-ndjson")
    events = _parse_ndjson(res.text)
    event_names = [e["event"] for e in events]
    assert event_names[0] == "ready"
    assert event_names[1] == "stt"
    assert event_names[2] == "reply_start"
    # Text chunks immediately after reply_start.
    text_events = [e for e in events if e["event"] == "text_chunk"]
    assert len(text_events) >= 3
    # No TTS events on text-only.
    assert not any(e["event"] == "audio_chunk" for e in events)
    assert not any(e["event"] == "audio_chunk_failed" for e in events)
    # Terminal event is done with ok=true.
    assert events[-1]["event"] == "done"
    assert events[-1]["ok"] is True
    assert events[-1]["assistant_text"]
    assert events[-1]["chunk_count"] == len(text_events)
    # total_tts_ms stays absent (None) on text-only turns.
    assert events[-1]["stage_timings"]["total_tts_ms"] is None


def test_stream_text_chunks_are_ordered_and_faithful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Emitted chunk indices are 0, 1, 2, ... and their text joins to the reply.

    This pins the "full written reply remains authoritative" invariant
    at the streaming boundary: concatenating the ``text_chunk`` payloads
    in order produces the same content as the ``done`` event's
    ``assistant_text`` (modulo inter-chunk whitespace).
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_vera_reply_multi)

    stt = _make_stt(transcript="tell me about the queue")
    with patch.object(
        vera_app_module,
        "transcribe_audio_file_async",
        side_effect=_async_stt(stt),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_stream(client, params={"session_id": "stream-ord"})
    events = _parse_ndjson(res.text)
    text_events = [e for e in events if e["event"] == "text_chunk"]
    indices = [e["index"] for e in text_events]
    assert indices == list(range(len(text_events)))
    # Final chunk is marked as final.
    assert text_events[-1]["final"] is True
    assert all(not e["final"] for e in text_events[:-1])
    joined = " ".join(e["text"] for e in text_events)
    done = events[-1]
    # The joined chunks cover all of the canonical assistant text.
    for keyword in ["queue looks healthy", "panel metrics", "inspect a specific"]:
        assert keyword in joined
        assert keyword in done["assistant_text"]


def test_stream_with_tts_emits_audio_chunks_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``speak_response=1``: one ``audio_chunk`` per text chunk, in order.

    The first audio chunk carries ``tts_first_chunk_ms`` and
    ``first_stable_speech_chunk_ms``; later chunks do not.  The
    ``done`` event reports ``total_tts_ms`` as the sum of per-chunk
    synthesis.  ``tts_url`` on ``done`` points at the first chunk's
    registered artifact so legacy consumers see the same handle the
    batch endpoint produces.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_vera_reply_multi)

    # Three distinct audio files so the registry resolves distinct tokens.
    audio_paths = []
    for i in range(3):
        p = tmp_path / f"chunk_{i}.wav"
        p.write_bytes(b"RIFF----WAVE" + b"\x00" * 32)
        audio_paths.append(str(p))

    call_index = {"n": 0}

    async def _fake_tts(**_kwargs: Any) -> TTSResponse:
        idx = call_index["n"]
        call_index["n"] += 1
        return _make_tts(audio_path=audio_paths[idx % len(audio_paths)])

    stt = _make_stt(transcript="tell me about the queue")
    with (
        patch.object(
            vera_app_module,
            "transcribe_audio_file_async",
            side_effect=_async_stt(stt),
        ),
        patch.object(
            vera_app_module,
            "synthesize_text_async",
            side_effect=_fake_tts,
        ),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_stream(
            client,
            params={"session_id": "stream-tts", "speak_response": "1"},
        )
    assert res.status_code == 200
    events = _parse_ndjson(res.text)
    audio_events = [e for e in events if e["event"] == "audio_chunk"]
    text_events = [e for e in events if e["event"] == "text_chunk"]
    assert len(audio_events) == len(text_events)
    # Ordering: indices strictly increasing, matching text chunks.
    assert [e["index"] for e in audio_events] == list(range(len(audio_events)))
    # First chunk carries the first-chunk timings.
    assert "tts_first_chunk_ms" in audio_events[0]
    assert "first_stable_speech_chunk_ms" in audio_events[0]
    assert audio_events[0]["tts_first_chunk_ms"] >= 0
    # Later chunks do not re-carry the first-chunk timings (avoid
    # misleading "first chunk ready" claims on every event).
    for later in audio_events[1:]:
        assert "tts_first_chunk_ms" not in later
    # Each audio event has a URL pointing at /vera/voice/audio/<token>.
    for e in audio_events:
        assert e["audio_url"].startswith("/vera/voice/audio/")
    # Done event carries first chunk url as tts_url and truthful timings.
    done = events[-1]
    assert done["event"] == "done"
    assert done["ok"] is True
    assert done["tts_url"] == audio_events[0]["audio_url"]
    timings = done["stage_timings"]
    assert isinstance(timings["tts_first_chunk_ms"], int)
    assert isinstance(timings["first_stable_speech_chunk_ms"], int)
    assert isinstance(timings["total_tts_ms"], int)
    # First chunk ready must be <= total wall-clock (and typically
    # much less, because only one chunk's TTS has completed).
    assert timings["tts_first_chunk_ms"] <= timings["total_ms"]


def test_stream_tts_chunk_failure_is_truthful_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed chunk produces ``audio_chunk_failed``; later chunks still try.

    This pins the fallback-safety rule: one broken chunk must not
    silence the rest of the reply or fabricate a URL.  Text is
    already visible, so the operator still gets the full answer
    even if one chunk's audio was lost.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_vera_reply_multi)

    good_audio = tmp_path / "good.wav"
    good_audio.write_bytes(b"RIFF----WAVE" + b"\x00" * 32)

    call_index = {"n": 0}

    async def _fake_tts(**_kwargs: Any) -> TTSResponse:
        idx = call_index["n"]
        call_index["n"] += 1
        # Fail the middle chunk only.
        if idx == 1:
            return _make_tts(status="failed", audio_path=None)
        return _make_tts(audio_path=str(good_audio))

    stt = _make_stt(transcript="tell me about the queue")
    with (
        patch.object(
            vera_app_module,
            "transcribe_audio_file_async",
            side_effect=_async_stt(stt),
        ),
        patch.object(
            vera_app_module,
            "synthesize_text_async",
            side_effect=_fake_tts,
        ),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_stream(
            client,
            params={"session_id": "stream-tts-fail", "speak_response": "1"},
        )
    events = _parse_ndjson(res.text)
    audio_events = [e for e in events if e["event"] == "audio_chunk"]
    failed_events = [e for e in events if e["event"] == "audio_chunk_failed"]
    text_events = [e for e in events if e["event"] == "text_chunk"]
    # We got 2 successes + 1 failure across 3 chunks; indices cover 0..2.
    assert len(text_events) == 3
    assert len(audio_events) == 2
    assert len(failed_events) == 1
    failed_index = failed_events[0]["index"]
    assert failed_index == 1
    # Done event still reports ok=true because the canonical chat
    # helper ran successfully; TTS failures never flip ok off.
    done = events[-1]
    assert done["ok"] is True
    assert done["tts"]["audio_chunk_failures"] == 1
    # Failed chunk event carries truthful error + status (no fabrication).
    assert failed_events[0]["error"]
    assert failed_events[0]["status"]


def test_stream_stt_failure_ends_with_done_ok_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """STT failure: no reply_start / text_chunk / audio events; done ok=false."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    failing_stt = STTResponse(
        request_id="fail",
        status="failed",
        transcript=None,
        language=None,
        audio_duration_ms=0,
        error="backend unavailable",
        error_class="RuntimeError",
        backend="whisper_local",
        started_at_ms=0,
        finished_at_ms=50,
        schema_version=1,
        inference_ms=50,
    )
    with patch.object(
        vera_app_module,
        "transcribe_audio_file_async",
        side_effect=_async_stt(failing_stt),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_stream(client, params={"session_id": "stream-stt-fail"})
    events = _parse_ndjson(res.text)
    names = [e["event"] for e in events]
    assert names == ["ready", "stt", "done"]
    done = events[-1]
    assert done["ok"] is False
    assert done["status"] == "stt_failed"
    assert done["tts_url"] is None
    assert done["chunk_count"] == 0


def test_stream_text_only_total_tts_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Skipped TTS stage is reported as absent (None), never as 0."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_vera_reply_single)
    stt = _make_stt(transcript="hi")
    with patch.object(
        vera_app_module,
        "transcribe_audio_file_async",
        side_effect=_async_stt(stt),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_stream(client, params={"session_id": "stream-absent"})
    events = _parse_ndjson(res.text)
    done = events[-1]
    timings = done["stage_timings"]
    assert timings["total_tts_ms"] is None
    assert timings["tts_first_chunk_ms"] is None
    assert timings["tts_ms"] is None


# =============================================================================
# Fail-closed and fallback safety
# =============================================================================


def test_stream_rejects_non_audio_content_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_queue_root(monkeypatch, tmp_path / "queue")
    client = TestClient(vera_app_module.app)
    res = client.post(
        "/chat/voice/stream",
        content=b"hello",
        headers={"content-type": "text/plain"},
    )
    assert res.status_code == 415


def test_stream_rejects_empty_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_queue_root(monkeypatch, tmp_path / "queue")
    _force_enabled_voice(monkeypatch)
    client = TestClient(vera_app_module.app)
    res = client.post(
        "/chat/voice/stream",
        content=b"",
        headers={"content-type": "audio/webm"},
    )
    assert res.status_code == 400


def test_stream_fails_closed_when_voice_input_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_queue_root(monkeypatch, tmp_path / "queue")

    def _disabled() -> VoiceFoundationFlags:
        return VoiceFoundationFlags(
            enable_voice_foundation=False,
            enable_voice_input=False,
            enable_voice_output=False,
            voice_stt_backend=None,
            voice_tts_backend=None,
        )

    monkeypatch.setattr(vera_app_module, "load_voice_foundation_flags", _disabled)
    client = TestClient(vera_app_module.app)
    res = _post_stream(client, params={"session_id": "stream-disabled"})
    assert res.status_code == 403
    events = _parse_ndjson(res.text)
    assert events[-1]["event"] == "done"
    assert events[-1]["ok"] is False
    assert events[-1]["status"] == "voice_input_disabled"


# =============================================================================
# Preview / lifecycle / session parity with the batch endpoint
# =============================================================================


def test_stream_persists_voice_transcript_turn_same_as_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The streaming path writes turns through the canonical helper.

    After the stream completes, the session file carries a
    ``voice_transcript`` user turn + an assistant turn — identical to
    the ``/chat/voice`` behavior.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_vera_reply_multi)
    from voxera.vera.session_store import read_session_turns

    stt = _make_stt(transcript="tell me about the queue")
    with patch.object(
        vera_app_module,
        "transcribe_audio_file_async",
        side_effect=_async_stt(stt),
    ):
        client = TestClient(vera_app_module.app)
        _post_stream(client, params={"session_id": "stream-turn-parity"})
    turns = read_session_turns(queue, "stream-turn-parity")
    roles = [t["role"] for t in turns]
    assert "user" in roles
    assert "assistant" in roles
    # The user turn is a voice_transcript turn.
    user_turns = [t for t in turns if t["role"] == "user"]
    assert user_turns[-1]["input_origin"] == "voice_transcript"
    assert user_turns[-1]["text"] == "tell me about the queue"


def test_stream_submit_still_routes_through_canonical_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dictated "submit it" via streaming still flows through _submit_handoff."""
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    session_id = "stream-submit"
    active_preview: dict[str, object] = {
        "write_file": {"path": "notes/demo.md", "content": "hello"}
    }
    from voxera.vera.preview_ownership import reset_active_preview

    reset_active_preview(queue, session_id, active_preview)
    captured: dict[str, Any] = {}

    def _fake_submit(
        *, root: Path, session_id: str, preview: dict[str, object] | None
    ) -> tuple[str, str]:
        captured["preview"] = preview
        return ("Submitted demo.md.", "handoff_submitted")

    monkeypatch.setattr(vera_app_module, "_submit_handoff", _fake_submit)
    stt = _make_stt(transcript="submit it")
    with patch.object(
        vera_app_module,
        "transcribe_audio_file_async",
        side_effect=_async_stt(stt),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_stream(client, params={"session_id": session_id})
    events = _parse_ndjson(res.text)
    done = events[-1]
    assert done["status"] == "handoff_submitted"
    assert captured["preview"] == active_preview


# =============================================================================
# Enhancer JS references the streaming seam
# =============================================================================


def test_dictation_enhancer_references_streaming_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pin the streaming URL + progressive-bubble strings in the enhancer.

    A silent rename of ``/chat/voice/stream`` or the progressive-bubble
    class name would quietly regress the incremental UX; this test
    makes the rename visible.
    """
    _set_queue_root(monkeypatch, tmp_path / "queue")
    client = TestClient(vera_app_module.app)
    res = client.get("/static/vera_dictation.js")
    assert res.status_code == 200
    body = res.text
    assert "/chat/voice/stream" in body
    assert "is-streaming" in body
    assert "streamDictation" in body
    # The progressive bubble and fallback batch path must both
    # remain wired so a fetch-stream failure still resolves the
    # operator's turn cleanly.
    assert "postBatchDictation" in body
    assert "beginProgressiveAssistantBubble" in body
