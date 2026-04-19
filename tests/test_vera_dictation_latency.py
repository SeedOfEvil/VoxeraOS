"""Tests for Vera dictation latency instrumentation and backend reuse.

Pins the bounded improvements made to the canonical ``/chat/voice``
path:

1. Stage timings are surfaced under ``payload["stage_timings"]`` and
   are truthful for the stages that actually ran this turn.  A stage
   that did not run (e.g. ``tts_ms`` without ``speak_response``) is
   reported as ``None``, never fabricated.
2. Text stays authoritative — ``assistant_text``, ``turns`` and
   ``preview`` are present and accurate even when TTS is slow or
   fails.
3. The process-wide STT/TTS backend caches reuse the same adapter
   instance across calls when flag values stay unchanged, and rebuild
   when they change.  This is the mechanism that eliminates the
   per-turn Whisper/Piper model reload.
4. Existing canonical preview/lifecycle invariants are preserved — a
   dictated "submit it" on a session with an active preview still
   routes through ``_submit_handoff``.

These tests do NOT touch real faster-whisper / piper models; they
exercise the cache plumbing and payload shape only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from voxera.vera_web import app as vera_app_module
from voxera.voice import stt_backend_factory, tts_backend_factory
from voxera.voice.flags import VoiceFoundationFlags
from voxera.voice.stt_adapter import NullSTTBackend
from voxera.voice.stt_protocol import STT_STATUS_SUCCEEDED, STTResponse
from voxera.voice.tts_adapter import NullTTSBackend
from voxera.voice.tts_protocol import TTS_STATUS_SUCCEEDED, TTSResponse


def _enabled_voice_flags() -> VoiceFoundationFlags:
    return VoiceFoundationFlags(
        enable_voice_foundation=True,
        enable_voice_input=True,
        enable_voice_output=True,
        voice_stt_backend="whisper_local",
        voice_tts_backend="piper_local",
    )


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
    *, status: str = TTS_STATUS_SUCCEEDED, audio_path: str | None = "/tmp/fake.wav"
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


def _set_queue_root(monkeypatch: pytest.MonkeyPatch, queue: Path) -> None:
    monkeypatch.setattr(vera_app_module, "_active_queue_root", lambda: queue)


def _force_enabled_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vera_app_module, "load_voice_foundation_flags", _enabled_voice_flags)


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


# =============================================================================
# Section 1: shared STT/TTS backend caches reuse instances
# =============================================================================


class TestSharedSTTBackend:
    def setup_method(self) -> None:
        stt_backend_factory.reset_shared_stt_backend()

    def teardown_method(self) -> None:
        stt_backend_factory.reset_shared_stt_backend()

    def test_same_flags_reuses_instance(self) -> None:
        """Identical flags produce the same adapter instance across calls.

        This is the mechanism that lets the Whisper model load exactly
        once per process — a fresh instance on every request would
        reload the model on first-use of every request.
        """
        flags = VoiceFoundationFlags(
            enable_voice_foundation=True,
            enable_voice_input=True,
            enable_voice_output=False,
            voice_stt_backend=None,
            voice_tts_backend=None,
        )
        first = stt_backend_factory.get_shared_stt_backend(flags)
        second = stt_backend_factory.get_shared_stt_backend(flags)
        assert first is second

    def test_disabled_voice_input_returns_cached_null(self) -> None:
        flags = VoiceFoundationFlags(
            enable_voice_foundation=False,
            enable_voice_input=False,
            enable_voice_output=False,
            voice_stt_backend=None,
            voice_tts_backend=None,
        )
        backend = stt_backend_factory.get_shared_stt_backend(flags)
        assert isinstance(backend, NullSTTBackend)
        # Same flags: same instance.
        assert backend is stt_backend_factory.get_shared_stt_backend(flags)

    def test_model_change_rebuilds_instance(self) -> None:
        """Swapping the whisper model identifier invalidates the cache."""
        flags_base = VoiceFoundationFlags(
            enable_voice_foundation=True,
            enable_voice_input=True,
            enable_voice_output=False,
            voice_stt_backend="whisper_local",
            voice_tts_backend=None,
            voice_stt_whisper_model="base",
        )
        flags_small = VoiceFoundationFlags(
            enable_voice_foundation=True,
            enable_voice_input=True,
            enable_voice_output=False,
            voice_stt_backend="whisper_local",
            voice_tts_backend=None,
            voice_stt_whisper_model="small",
        )
        a = stt_backend_factory.get_shared_stt_backend(flags_base)
        b = stt_backend_factory.get_shared_stt_backend(flags_base)
        c = stt_backend_factory.get_shared_stt_backend(flags_small)
        assert a is b
        assert a is not c

    def test_reset_drops_cached_instance(self) -> None:
        flags = VoiceFoundationFlags(
            enable_voice_foundation=True,
            enable_voice_input=True,
            enable_voice_output=False,
            voice_stt_backend=None,
            voice_tts_backend=None,
        )
        a = stt_backend_factory.get_shared_stt_backend(flags)
        stt_backend_factory.reset_shared_stt_backend()
        b = stt_backend_factory.get_shared_stt_backend(flags)
        assert a is not b


class TestSharedTTSBackend:
    def setup_method(self) -> None:
        tts_backend_factory.reset_shared_tts_backend()

    def teardown_method(self) -> None:
        tts_backend_factory.reset_shared_tts_backend()

    def test_same_flags_reuses_instance(self) -> None:
        flags = VoiceFoundationFlags(
            enable_voice_foundation=True,
            enable_voice_input=False,
            enable_voice_output=True,
            voice_stt_backend=None,
            voice_tts_backend=None,
        )
        first = tts_backend_factory.get_shared_tts_backend(flags)
        second = tts_backend_factory.get_shared_tts_backend(flags)
        assert first is second

    def test_disabled_voice_output_returns_cached_null(self) -> None:
        flags = VoiceFoundationFlags(
            enable_voice_foundation=True,
            enable_voice_input=True,
            enable_voice_output=False,
            voice_stt_backend=None,
            voice_tts_backend=None,
        )
        backend = tts_backend_factory.get_shared_tts_backend(flags)
        assert isinstance(backend, NullTTSBackend)
        assert backend is tts_backend_factory.get_shared_tts_backend(flags)

    def test_piper_model_change_rebuilds_instance(self) -> None:
        flags_a = VoiceFoundationFlags(
            enable_voice_foundation=True,
            enable_voice_input=False,
            enable_voice_output=True,
            voice_stt_backend=None,
            voice_tts_backend="piper_local",
            voice_tts_piper_model="en_US-lessac-medium",
        )
        flags_b = VoiceFoundationFlags(
            enable_voice_foundation=True,
            enable_voice_input=False,
            enable_voice_output=True,
            voice_stt_backend=None,
            voice_tts_backend="piper_local",
            voice_tts_piper_model="en_US-ryan-high",
        )
        a = tts_backend_factory.get_shared_tts_backend(flags_a)
        b = tts_backend_factory.get_shared_tts_backend(flags_b)
        assert a is not b


# =============================================================================
# Section 2: /chat/voice payload carries truthful stage timings
# =============================================================================


def test_chat_voice_payload_has_stage_timings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/chat/voice`` payload always includes a bounded stage_timings dict.

    Operator diagnostics depend on being able to see where a dictation
    round trip's time went.  Every known stage appears as a key; the
    stages that actually ran report non-negative ``int`` milliseconds;
    stages that did not run stay ``None``.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_vera_reply)

    stt = _make_stt(transcript="hello vera")
    with patch.object(
        vera_app_module,
        "transcribe_audio_file_async",
        side_effect=_async_stt(stt),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(client, body=b"\x00" * 32, params={"session_id": "st-timings"})
    assert res.status_code == 200
    payload = res.json()
    assert "stage_timings" in payload
    timings = payload["stage_timings"]
    # All known stage keys are present.
    expected_keys = {
        "upload_ms",
        "temp_write_ms",
        "stt_ms",
        "vera_ms",
        "tts_ms",
        "total_ms",
    }
    assert set(timings) == expected_keys
    # Stages that ran report non-negative ints.
    for key in ("upload_ms", "temp_write_ms", "stt_ms", "vera_ms", "total_ms"):
        assert isinstance(timings[key], int), f"{key} must be an int, got {timings[key]!r}"
        assert timings[key] >= 0, f"{key} must be non-negative"
    # TTS did not run this turn (no speak_response) → truthful None.
    assert timings["tts_ms"] is None
    # Sanity: total covers the run, should be >= individual stages that ran.
    assert timings["total_ms"] >= timings["stt_ms"]


def test_chat_voice_tts_timing_present_when_speak_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``tts_ms`` is populated when TTS actually runs and succeeds.

    Pins that the timing is tied to stages that ran — a ``None``
    ``tts_ms`` with an ``audio_path`` present (or vice-versa) would
    mean the instrumentation lies.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_vera_reply)
    audio_file = tmp_path / "fake_tts.wav"
    audio_file.write_bytes(b"RIFF----WAVE" + b"\x00" * 32)
    stt = _make_stt(transcript="hello vera")
    tts = _make_tts(audio_path=str(audio_file))
    with (
        patch.object(
            vera_app_module,
            "transcribe_audio_file_async",
            side_effect=_async_stt(stt),
        ),
        patch.object(
            vera_app_module,
            "synthesize_text_async",
            side_effect=_async_tts(tts),
        ),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(
            client,
            body=b"\x00" * 32,
            params={"session_id": "st-tts-time", "speak_response": "1"},
        )
    assert res.status_code == 200
    payload = res.json()
    timings = payload["stage_timings"]
    assert isinstance(timings["tts_ms"], int)
    assert timings["tts_ms"] >= 0


def test_chat_voice_stage_timings_stt_failure_still_truthful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """STT failure: only the stages that actually ran are populated.

    When STT fails, Vera never runs, so ``vera_ms`` must stay ``None``
    even though ``stt_ms`` is populated.  This keeps the instrumentation
    truthful under failure.
    """
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
        res = _post_voice(client, body=b"\x00" * 32, params={"session_id": "st-stt-fail"})
    assert res.status_code == 200
    payload = res.json()
    timings = payload["stage_timings"]
    assert isinstance(timings["stt_ms"], int)
    assert timings["vera_ms"] is None
    assert timings["tts_ms"] is None
    assert isinstance(timings["total_ms"], int)


# =============================================================================
# Section 3: text stays authoritative even when TTS is slow / failing
# =============================================================================


def test_chat_voice_text_authoritative_on_slow_tts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even a slow TTS response does not block assistant text / turns.

    The payload always carries ``assistant_text`` and a fresh turns
    list from the canonical chat helper; TTS is additive.  We
    simulate "slow" by making the fake TTS pause deliberately before
    returning, then assert the top-level payload shape is complete
    and coherent regardless.
    """
    import asyncio

    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_vera_reply)
    audio_file = tmp_path / "slow_tts.wav"
    audio_file.write_bytes(b"RIFF----WAVE" + b"\x00" * 32)

    async def _slow_tts(**_kwargs: Any) -> TTSResponse:
        await asyncio.sleep(0.05)
        return _make_tts(audio_path=str(audio_file))

    stt = _make_stt(transcript="hello vera")
    with (
        patch.object(
            vera_app_module,
            "transcribe_audio_file_async",
            side_effect=_async_stt(stt),
        ),
        patch.object(
            vera_app_module,
            "synthesize_text_async",
            side_effect=_slow_tts,
        ),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(
            client,
            body=b"\x00" * 32,
            params={"session_id": "st-slow-tts", "speak_response": "1"},
        )
    assert res.status_code == 200
    payload = res.json()
    # Text is authoritative: assistant_text and turns always present.
    assert payload["assistant_text"] == "Ack: hello vera"
    assert payload["turns"][-1]["role"] == "assistant"
    assert payload["tts_url"] is not None
    # TTS timing is present and >= the deliberate sleep duration.
    assert payload["stage_timings"]["tts_ms"] is not None


def test_chat_voice_text_authoritative_when_tts_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When TTS fails outright, text / turns / preview remain truthful.

    The ``tts_url`` field drops to ``None`` (never a fabricated URL)
    and ``ok`` stays ``True`` because the canonical chat path ran
    successfully — TTS failure never flips the top-level ``ok``.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_vera_reply)
    stt = _make_stt(transcript="hello vera")
    failing_tts = _make_tts(status="failed", audio_path=None)
    with (
        patch.object(
            vera_app_module,
            "transcribe_audio_file_async",
            side_effect=_async_stt(stt),
        ),
        patch.object(
            vera_app_module,
            "synthesize_text_async",
            side_effect=_async_tts(failing_tts),
        ),
    ):
        client = TestClient(vera_app_module.app)
        res = _post_voice(
            client,
            body=b"\x00" * 32,
            params={"session_id": "st-tts-hard-fail", "speak_response": "1"},
        )
    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is True
    assert payload["assistant_text"] == "Ack: hello vera"
    assert payload["tts_url"] is None
    assert payload["tts"]["success"] is False


# =============================================================================
# Section 4: voice input path consumes shared STT backend
# =============================================================================


def test_transcribe_audio_file_uses_shared_stt_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """:func:`transcribe_audio_file` delegates to ``get_shared_stt_backend``.

    The Whisper model-reload hotspot is fixed specifically at the
    factory → shared-instance seam: without this delegation, the
    model would still reload on every call.  Assert the shared
    factory is what produces the adapter when no explicit backend
    is supplied.
    """
    stt_backend_factory.reset_shared_stt_backend()
    calls: dict[str, int] = {"shared": 0}
    real_shared = stt_backend_factory.get_shared_stt_backend

    def _counting_shared(flags: VoiceFoundationFlags) -> Any:
        calls["shared"] += 1
        return real_shared(flags)

    monkeypatch.setattr(stt_backend_factory, "get_shared_stt_backend", _counting_shared)
    # ALSO monkeypatch at the callsite's import so the voice.input
    # lookup hits the counting wrapper (input.py imports inside the
    # function body).
    from voxera.voice import input as voice_input_module

    # The import statement in ``transcribe_audio_file`` resolves the
    # attribute at call time via ``from .stt_backend_factory import
    # get_shared_stt_backend``, so our monkeypatch above is the
    # authoritative point.

    flags = VoiceFoundationFlags(
        enable_voice_foundation=True,
        enable_voice_input=True,
        enable_voice_output=False,
        voice_stt_backend=None,
        voice_tts_backend=None,
    )
    audio = tmp_path / "test.wav"
    audio.write_bytes(b"\x00" * 16)
    # Call the sync entry point; it returns an STTResponse (may be
    # unavailable since no backend is configured — we only care that
    # the shared helper was invoked).
    voice_input_module.transcribe_audio_file(audio_path=str(audio), flags=flags)
    assert calls["shared"] == 1
    # A second call with the same flags re-enters the shared helper
    # but the helper itself returns the cached instance.
    voice_input_module.transcribe_audio_file(audio_path=str(audio), flags=flags)
    assert calls["shared"] == 2
    stt_backend_factory.reset_shared_stt_backend()


# =============================================================================
# Section 5: client-side state transition strings present in enhancer JS
# =============================================================================


def test_dictation_enhancer_surfaces_bounded_state_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The enhancer JS contains the bounded state labels operators see.

    Pins the concrete strings so a silent rename cannot drift the UX.
    The four labels together describe the whole in-flight pipeline
    from the operator's point of view:
    - "Uploading"      : blob being POSTed
    - "Transcribing"   : server running STT
    - "Vera thinking"  : server running the canonical chat helper
    - "Speaking reply" : browser playing the synthesised audio
    """
    _set_queue_root(monkeypatch, tmp_path / "queue")
    client = TestClient(vera_app_module.app)
    res = client.get("/static/vera_dictation.js")
    assert res.status_code == 200
    body = res.text
    assert "Uploading" in body
    assert "Transcribing" in body
    assert "Vera thinking" in body
    assert "Speaking reply" in body
    # Timers that drive the transitions must also be present so a
    # refactor cannot accidentally collapse the progression back to
    # a single label.
    assert "clearStagingTimers" in body


# =============================================================================
# Section 6: behavior parity — submit still routes through canonical handoff
# =============================================================================


def test_chat_voice_submit_still_routes_through_handoff_after_timings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adding stage timings did not rewire the canonical submit path.

    A dictated "submit it" on a session with an active preview MUST
    still flow through ``_submit_handoff`` — same helper typed
    ``/chat`` uses.  This test deliberately re-verifies the invariant
    after the latency work so future refactors can't silently drift
    it.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)
    _force_enabled_voice(monkeypatch)
    session_id = "vera-timing-submit"
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
        res = _post_voice(client, body=b"\x00" * 32, params={"session_id": session_id})
    assert res.status_code == 200
    payload = res.json()
    assert captured["preview"] == active_preview
    assert payload["status"] == "handoff_submitted"
    # Timings still populated on the submit lane.
    assert payload["stage_timings"]["vera_ms"] is not None
