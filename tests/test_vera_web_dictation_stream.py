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
    """Fake Vera reply long enough that the naturalness-first
    coalescer still produces multiple chunks.

    The naturalness-first strategy merges consecutive sentences into
    body chunks of ~220 chars, so a short 3-sentence reply now
    collapses to 1-2 chunks.  For tests that need multi-chunk
    behaviour we supply a reply of ~5 sentences and enough total
    length that the body target is crossed at least once.
    """
    return {
        "answer": (
            "The queue looks healthy this morning. "
            "I checked the panel metrics and nothing stands out at the "
            "moment. "
            "Let me know if you want me to inspect a specific service "
            "more carefully. "
            "I can also bundle the recent audit entries for you. "
            "Either path stays read-only until you explicitly ask to "
            "submit something through VoxeraOS."
        ),
        "status": "ok:test",
    }


async def _fake_vera_reply_multi(**kwargs: Any) -> dict[str, Any]:
    return _multi_sentence_reply(**kwargs)


async def _fake_vera_reply_single(**kwargs: Any) -> dict[str, Any]:
    return {"answer": "Sure — done.", "status": "ok:test"}


def _expected_multi_chunk_count() -> int:
    """Number of UI text chunks the multi-sentence fixture produces.

    Progressive UI chunks are word-group granular (see
    ``voxera.vera_web.progressive_text.split_progressive_text_chunks``)
    so a multi-sentence fixture produces many small chunks for the
    visibly-typing effect.  Computed via the real chunker so the
    fixture and the chunk expectation cannot drift out of sync when
    the word-group tuning changes.
    """
    from voxera.vera_web.progressive_text import split_progressive_text_chunks

    return len(split_progressive_text_chunks(_multi_sentence_reply()["answer"]))


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
    # Text chunks immediately after reply_start.  The multi-sentence
    # fixture is tuned to produce at least 2 chunks under the
    # naturalness-first coalescer (head + body).
    text_events = [e for e in events if e["event"] == "text_chunk"]
    assert len(text_events) >= 2
    assert len(text_events) == _expected_multi_chunk_count()
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


def test_stream_with_tts_emits_single_audio_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``speak_response=1``: TTS is now single-file.

    Chunked TTS previously produced a line-by-line cadence; we
    reverted to one coherent full-reply synthesis.  No matter how
    many progressive ``text_chunk`` events fire, exactly one
    ``audio_chunk`` event is emitted with the full-reply audio URL,
    and it carries the first/only chunk timings.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_vera_reply_multi)

    audio_path = tmp_path / "full_reply.wav"
    audio_path.write_bytes(b"RIFF----WAVE" + b"\x00" * 32)
    call_count = {"n": 0}

    async def _fake_tts(**_kwargs: Any) -> TTSResponse:
        call_count["n"] += 1
        return _make_tts(audio_path=str(audio_path))

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
    # Single-file TTS: exactly one TTS call, exactly one audio_chunk
    # event, regardless of how many progressive text chunks fired.
    assert call_count["n"] == 1
    assert len(audio_events) == 1
    # Progressive text rendering is word-group granular -- several
    # chunks for a typical reply so the UI visibly types.
    assert len(text_events) > len(audio_events)
    assert audio_events[0]["index"] == 0
    # Single chunk carries the first-chunk timings; there are no
    # later chunks to compare against.
    assert "tts_first_chunk_ms" in audio_events[0]
    assert "first_stable_speech_chunk_ms" in audio_events[0]
    assert audio_events[0]["audio_url"].startswith("/vera/voice/audio/")
    # Done event: tts_url matches the one audio chunk and tts dict
    # reports chunk_count=1 (single-file).
    done = events[-1]
    assert done["event"] == "done"
    assert done["ok"] is True
    assert done["tts_url"] == audio_events[0]["audio_url"]
    assert done["tts"]["chunk_count"] == 1
    assert done["tts"]["audio_chunk_failures"] == 0
    timings = done["stage_timings"]
    assert isinstance(timings["tts_first_chunk_ms"], int)
    assert isinstance(timings["first_stable_speech_chunk_ms"], int)
    assert isinstance(timings["total_tts_ms"], int)
    assert timings["tts_first_chunk_ms"] <= timings["total_ms"]


def test_stream_tts_chunk_failure_is_truthful_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full-reply TTS failure emits exactly one ``audio_chunk_failed``.

    Since TTS is now single-file, a failure produces one failed
    event (no surviving audio chunks, no fabricated URL).  Text
    chunks are unaffected because they emit before TTS completes,
    so the operator still sees the full written reply and ``ok``
    stays ``True`` (text authority is preserved).
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_vera_reply_multi)

    call_count = {"n": 0}

    async def _fake_tts(**_kwargs: Any) -> TTSResponse:
        call_count["n"] += 1
        return _make_tts(status="failed", audio_path=None)

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
    # Single-file TTS: one synthesize call, one failed event, no
    # successful audio_chunk events.
    assert call_count["n"] == 1
    assert len(audio_events) == 0
    assert len(failed_events) == 1
    assert failed_events[0]["index"] == 0
    # Text chunks still flowed (progressive render is independent of
    # TTS success) — text authority is preserved even when speech fails.
    assert len(text_events) >= 1
    done = events[-1]
    assert done["ok"] is True
    assert done["tts"]["audio_chunk_failures"] == 1
    assert done["tts"]["success"] is False
    assert done["tts"]["chunk_count"] == 0
    assert done["tts_url"] is None
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
# Hardening fences — catch regressions that would silently reintroduce
# batch feel or break chunk ordering
# =============================================================================


def test_stream_sets_proxy_safe_headers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Streaming response advertises ``X-Accel-Buffering: no`` + ``no-cache``.

    Without these, a reverse proxy (nginx in particular) may buffer the
    entire NDJSON response, which silently regresses the product back
    to batch feel even though the server is genuinely streaming.  The
    headers are cheap defensive hints; this test prevents them from
    being dropped in a future refactor.
    """
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
        res = _post_stream(client, params={"session_id": "stream-headers"})
    assert res.status_code == 200
    assert res.headers.get("x-accel-buffering") == "no"
    cache_control = res.headers.get("cache-control", "").lower()
    assert "no-cache" in cache_control or "no-transform" in cache_control


def test_stream_text_chunks_join_matches_assistant_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Joining text_chunk payloads faithfully reproduces ``assistant_text``.

    Text authority: the streamed chunks are the same content the
    canonical chat helper produced.  If a future chunker regression
    drops a sentence, the join-vs-assistant_text comparison catches
    it at the endpoint boundary rather than leaving the operator
    with silent text loss.
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
        res = _post_stream(client, params={"session_id": "stream-faithful"})
    events = _parse_ndjson(res.text)
    text_events = [e for e in events if e["event"] == "text_chunk"]
    done = events[-1]
    joined = " ".join(str(e["text"]) for e in text_events).strip()
    # The reply used in the fake has single-space separators between
    # sentences; the joined chunks reproduce that exactly.
    assert joined == done["assistant_text"].strip()
    # The ``final`` flag is on the last chunk (and only the last).
    assert text_events[-1]["final"] is True
    assert all(not e["final"] for e in text_events[:-1])


def test_stream_text_chunks_flow_before_audio_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``text_chunk`` events all precede the terminal ``audio_chunk``.

    The progressive UI render depends on this ordering: the browser
    must see every text chunk (and update the assistant bubble) BEFORE
    the final single-file audio chunk can arrive.  Pinning this
    guards against a future refactor that awaits the TTS task before
    pacing text chunks -- which would re-introduce the "all at once"
    batch feel.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_vera_reply_multi)

    audio_path = tmp_path / "paced.wav"
    audio_path.write_bytes(b"RIFF----WAVE" + b"\x00" * 32)

    async def _fake_tts(**_kwargs: Any) -> TTSResponse:
        return _make_tts(audio_path=str(audio_path))

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
            params={"session_id": "stream-order", "speak_response": "1"},
        )
    events = _parse_ndjson(res.text)
    # Walk the event stream in arrival order.  Every ``text_chunk``
    # must appear before the single ``audio_chunk`` (or
    # ``audio_chunk_failed``) event.  This is the concrete ordering
    # invariant behind "progressive text renders before audio
    # arrives".
    audio_seen_at: int | None = None
    text_after_audio: list[dict[str, Any]] = []
    text_before_audio_count = 0
    for idx, evt in enumerate(events):
        if evt["event"] in ("audio_chunk", "audio_chunk_failed"):
            audio_seen_at = idx
        elif evt["event"] == "text_chunk":
            if audio_seen_at is None:
                text_before_audio_count += 1
            else:
                text_after_audio.append(evt)
    assert audio_seen_at is not None, "single-file TTS should emit one audio event"
    assert text_after_audio == [], "text_chunk must never arrive after audio_chunk"
    assert text_before_audio_count >= 1


def test_stream_ready_event_carries_pre_stt_timings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``ready`` handshake reports the stages that have already run.

    ``upload_ms`` and ``temp_write_ms`` are measured *before* the
    generator begins, so they should always be present as
    non-negative ints on the handshake — that is how operators can
    see pre-STT cost on a slow network.  STT and later stages stay
    ``None`` on ``ready`` because they have not happened yet.
    """
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
        res = _post_stream(client, params={"session_id": "stream-ready"})
    events = _parse_ndjson(res.text)
    assert events[0]["event"] == "ready"
    timings = events[0]["stage_timings"]
    assert isinstance(timings["upload_ms"], int)
    assert timings["upload_ms"] >= 0
    assert isinstance(timings["temp_write_ms"], int)
    assert timings["temp_write_ms"] >= 0
    # Stages that have not run are absent (None) — truthful handshake.
    assert timings["stt_ms"] is None
    assert timings["vera_ms"] is None
    assert timings["tts_first_chunk_ms"] is None


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


def test_dictation_enhancer_renders_user_transcript_before_progressive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression fence for the "assistant appears before user" bug.

    Manual validation of the original PR found that the streaming
    enhancer created the progressive assistant bubble immediately on
    fetch, before the user's voice transcript was visible.  That
    produced the wrong conversation order on screen ("Vera replying
    to a transcript the operator cannot see").

    The fix is in the client: the progressive bubble is
    lazy-initialised via ``ensureProgressiveBubble`` and only
    constructed when the first ``text_chunk`` event arrives -- AFTER
    the ``stt`` event's ``appendUserTranscriptBubble`` call has
    placed the user turn into the thread.  These string assertions
    pin that contract so a future refactor that eagerly creates the
    progressive bubble (before stt) would fail this test.
    """
    _set_queue_root(monkeypatch, tmp_path / "queue")
    client = TestClient(vera_app_module.app)
    res = client.get("/static/vera_dictation.js")
    assert res.status_code == 200
    body = res.text
    # User transcript rendering helper is present and wired in the
    # stt-event handler branch (not in a post-reply handler).
    assert "appendUserTranscriptBubble" in body
    # Progressive bubble is created lazily, not eagerly.  The lazy
    # initialiser is the single call site that constructs the
    # bubble; a caller that bypasses it would eager-create it again.
    assert "ensureProgressiveBubble" in body
    # Structural guarantee: the progressive-bubble factory is only
    # invoked from inside ensureProgressiveBubble (and not at the
    # top of streamDictation) so the ordering constraint cannot be
    # violated by an eager call at fetch time.
    begin_count = body.count("beginProgressiveAssistantBubble(")
    # One definition call-site + one call from ensureProgressiveBubble = 2.
    # Any additional call would indicate an eager pre-stt creation.
    assert begin_count == 2, (
        f"beginProgressiveAssistantBubble should be invoked exactly once "
        f"(from ensureProgressiveBubble) and defined once; got {begin_count} "
        f"occurrences, which suggests an eager pre-stt call was added back"
    )


def test_stream_emits_text_chunks_with_async_pacing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inter-chunk ``await asyncio.sleep`` produces measurable wall-clock
    spacing between paced ``text_chunk`` emits.

    Without a small async pause between yields, the ASGI server
    flushes consecutive ``text_chunk`` events in the same tick and
    the browser perceives the reply "all at once".  This test
    patches ``asyncio.sleep`` inside the app module so each sleep
    call is counted, then confirms the stream called ``asyncio.sleep``
    at least once per inter-chunk gap in a multi-chunk reply.
    """
    import asyncio as _asyncio

    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_vera_reply_multi)

    real_sleep = _asyncio.sleep
    sleep_calls: list[float] = []

    async def _counting_sleep(delay: float) -> None:
        sleep_calls.append(delay)
        # Keep the real (tiny) delay so the generator yields to
        # the event loop as it would in production.
        await real_sleep(0)

    # Patch the asyncio module reference used inside vera_web.app so
    # the stream generator's ``asyncio.sleep(...)`` calls route to
    # our counter.  A top-level patch on ``_asyncio.sleep`` would
    # also trip other async machinery, so we patch via the app
    # module's attribute lookup.
    monkeypatch.setattr(vera_app_module.asyncio, "sleep", _counting_sleep)
    stt = _make_stt(transcript="tell me about the queue")
    with patch.object(
        vera_app_module,
        "transcribe_audio_file_async",
        side_effect=_async_stt(stt),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_stream(client, params={"session_id": "stream-paced"})
    events = _parse_ndjson(res.text)
    text_events = [e for e in events if e["event"] == "text_chunk"]
    assert len(text_events) >= 2
    # One sleep per inter-chunk gap: N text chunks -> N-1 paced gaps.
    # Every observed sleep must be the non-zero pacing delay so a
    # future refactor that passes ``delay=0`` would fail here.
    paced_gaps = [
        d for d in sleep_calls if abs(d - vera_app_module._TEXT_CHUNK_EMIT_DELAY_S) < 1e-6
    ]
    assert len(paced_gaps) >= len(text_events) - 1, (
        f"expected >= {len(text_events) - 1} paced sleeps, got "
        f"{len(paced_gaps)} (all sleep calls: {sleep_calls!r})"
    )
    # Delay constant is non-zero so the pacing is real, not a no-op.
    assert vera_app_module._TEXT_CHUNK_EMIT_DELAY_S > 0


def test_stream_produces_many_progressive_text_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression fence for the "reply arrives all at once" bug.

    Progressive UI chunking must produce materially more than 2
    chunks for a typical multi-sentence reply so the browser
    actually shows a visibly typing effect.  The previous sentence-
    level chunker produced 1-2 chunks for the same fixture, which
    flushed back-to-back and felt like the full reply landed in one
    tick.  Word-group chunking keeps chunks small and the stream
    endpoint paces them with ``await asyncio.sleep`` between emits.
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
        res = _post_stream(client, params={"session_id": "stream-many-chunks"})
    events = _parse_ndjson(res.text)
    text_events = [e for e in events if e["event"] == "text_chunk"]
    # Tight lower bound: a multi-sentence fixture (~30 words) should
    # produce well over 2 chunks under the word-group chunker.  If
    # this assertion fires, something regressed back toward
    # sentence-level emission and the reply will feel "all at once"
    # again.
    assert len(text_events) >= 5, (
        f"progressive text chunking regressed: got {len(text_events)} "
        f"chunks for a multi-sentence reply"
    )
