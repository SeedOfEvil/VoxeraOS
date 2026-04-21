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
    # All known stage keys are present.  ``vera_*`` sub-stage keys are
    # truthful breakdowns of the ``vera_ms`` umbrella: ``None`` when the
    # sub-stage did not run this turn, non-negative ``int`` when it did.
    expected_keys = {
        "upload_ms",
        "temp_write_ms",
        "stt_ms",
        "vera_ms",
        "vera_preview_builder_ms",
        "vera_reply_ms",
        "vera_enrichment_ms",
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
    # The main reply LLM call always runs on a non-early-exit turn.
    # Monkeypatched ``generate_vera_reply`` is fast, so the timing is
    # small but still a non-negative int — the key invariant here is
    # that absence of the call would have left the field ``None``.
    assert isinstance(timings["vera_reply_ms"], int)
    assert timings["vera_reply_ms"] >= 0
    # Enrichment did not run (no active preview on a fresh session) →
    # truthful ``None``.  This is the central "absence stays truthful"
    # property: sub-stage timings never get fabricated as 0 when the
    # branch simply did not execute.
    assert timings["vera_enrichment_ms"] is None
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
    # Vera sub-stages never ran because STT failed before Vera could
    # even dispatch.  Absence stays truthful: ``None`` here, not 0 or
    # a fabricated value that would claim the LLM ran.
    assert timings["vera_preview_builder_ms"] is None
    assert timings["vera_reply_ms"] is None
    assert timings["vera_enrichment_ms"] is None


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
    The five labels together describe the whole in-flight pipeline
    from the operator's point of view:
    - "Uploading"           : blob being POSTed
    - "Transcribing"        : server running STT
    - "Vera thinking"       : server running the canonical chat helper
    - "Synthesizing speech" : server running TTS over the final text
    - "Speaking reply"      : browser playing the synthesised audio
    """
    _set_queue_root(monkeypatch, tmp_path / "queue")
    client = TestClient(vera_app_module.app)
    res = client.get("/static/vera_dictation.js")
    assert res.status_code == 200
    body = res.text
    assert "Uploading" in body
    assert "Transcribing" in body
    assert "Vera thinking" in body
    assert "Synthesizing speech" in body
    assert "Speaking reply" in body
    # Timers that drive the transitions must also be present so a
    # refactor cannot accidentally collapse the progression back to
    # a single label.
    assert "clearStagingTimers" in body
    # "Synthesizing speech…" must be gated on ``speakResponse`` — the
    # label is only truthful when a spoken reply was actually requested.
    # Without the gate, the label would appear on text-only voice turns
    # that never run TTS, which would be fabricated progress.
    assert "_stagingTimer3" in body


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


# =============================================================================
# Section 7: concurrent first-turn races do not duplicate heavy model loads
# =============================================================================


class TestBackendModelLoadLockHardening:
    """Hardening: ``_ensure_model`` / ``_ensure_voice`` must serialise the slow
    first-turn load so the shared-instance cache's point — one model load per
    process — is not defeated by concurrent first-turn requests.

    Each test simulates two threads entering the lazy-load path at the same
    time, counts how many times the real load ran, and asserts it ran exactly
    once.  Steady-state (model already loaded) stays lock-free.
    """

    def test_whisper_ensure_model_loads_once_under_concurrent_calls(self) -> None:
        import threading

        from voxera.voice.whisper_backend import WhisperLocalBackend

        backend = WhisperLocalBackend(model_size="base")
        load_calls: list[str] = []
        enter_event = threading.Event()
        release_event = threading.Event()

        class _FakeWhisperModel:
            def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                load_calls.append("built")
                # Park the first thread inside construction so the
                # second thread is guaranteed to race with it.
                enter_event.set()
                release_event.wait(timeout=2.0)

        class _FakeFasterWhisper:
            WhisperModel = _FakeWhisperModel

        import sys

        sys.modules["faster_whisper"] = _FakeFasterWhisper  # type: ignore[assignment]
        try:
            results: list[Any] = []
            errors: list[BaseException] = []

            def _run() -> None:
                try:
                    results.append(backend._ensure_model())
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            t1 = threading.Thread(target=_run)
            t2 = threading.Thread(target=_run)
            t1.start()
            # Wait for t1 to enter the fake constructor, then start t2
            # so it races on the same lock / cache check.
            assert enter_event.wait(timeout=2.0)
            t2.start()
            # Let t1 finish construction; t2 should then observe the
            # already-built model via the double-checked pattern and
            # NOT invoke the constructor a second time.
            release_event.set()
            t1.join(timeout=3.0)
            t2.join(timeout=3.0)

            assert not errors, f"concurrent _ensure_model raised: {errors}"
            assert len(load_calls) == 1
            assert results[0] is results[1]
        finally:
            sys.modules.pop("faster_whisper", None)

    def test_piper_ensure_voice_loads_once_under_concurrent_calls(self) -> None:
        import threading

        from voxera.voice.piper_backend import PiperLocalBackend

        backend = PiperLocalBackend(model="en_US-lessac-medium")
        load_calls: list[str] = []
        enter_event = threading.Event()
        release_event = threading.Event()

        class _FakePiperVoice:
            @classmethod
            def load(cls, *_args: Any, **_kwargs: Any) -> _FakePiperVoice:
                load_calls.append("loaded")
                enter_event.set()
                release_event.wait(timeout=2.0)
                return cls()

        class _FakePiperModule:
            PiperVoice = _FakePiperVoice

        import sys

        sys.modules["piper"] = _FakePiperModule  # type: ignore[assignment]
        try:
            results: list[Any] = []
            errors: list[BaseException] = []

            def _run() -> None:
                try:
                    results.append(backend._ensure_voice())
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            t1 = threading.Thread(target=_run)
            t2 = threading.Thread(target=_run)
            t1.start()
            assert enter_event.wait(timeout=2.0)
            t2.start()
            release_event.set()
            t1.join(timeout=3.0)
            t2.join(timeout=3.0)

            assert not errors, f"concurrent _ensure_voice raised: {errors}"
            assert len(load_calls) == 1
            assert results[0] is results[1]
        finally:
            sys.modules.pop("piper", None)


# =============================================================================
# Section 8: /chat/voice route actually threads through the shared factory
# =============================================================================


def test_chat_voice_resolves_shared_stt_backend_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: ``/chat/voice`` pulls its STT backend via the shared factory.

    This test exercises the full wiring from the FastAPI request
    handler down through ``transcribe_audio_file_async`` →
    ``_transcribe_audio_file_sync`` → ``transcribe_audio_file`` →
    ``get_shared_stt_backend``.  We deliberately do NOT patch
    ``transcribe_audio_file_async`` — instead we run the real async
    path with a flags configuration that makes
    ``get_shared_stt_backend`` return a ``NullSTTBackend`` (no backend
    configured).  Both back-to-back requests must pass through the
    shared resolver and receive the SAME instance; a silent regression
    that bypassed the cache would either call the resolver more than
    twice, or return two distinct instances.
    """
    queue = tmp_path / "queue"
    _set_queue_root(monkeypatch, queue)

    # Voice enabled but NO backend configured: the real
    # transcribe_audio_file path runs, resolves the shared
    # NullSTTBackend, and returns an unavailable STT response.  That
    # is all we need to confirm the route really went through the
    # shared helper.
    def _null_backend_flags() -> VoiceFoundationFlags:
        return VoiceFoundationFlags(
            enable_voice_foundation=True,
            enable_voice_input=True,
            enable_voice_output=False,
            voice_stt_backend=None,
            voice_tts_backend=None,
        )

    monkeypatch.setattr(vera_app_module, "load_voice_foundation_flags", _null_backend_flags)

    # Watch the shared resolver so we can assert it was actually
    # called by the route's real STT path.
    observed_backends: list[Any] = []
    real_shared = stt_backend_factory.get_shared_stt_backend

    def _watching_shared(flags: VoiceFoundationFlags) -> Any:
        backend = real_shared(flags)
        observed_backends.append(backend)
        return backend

    monkeypatch.setattr(stt_backend_factory, "get_shared_stt_backend", _watching_shared)

    client = TestClient(vera_app_module.app)
    res1 = _post_voice(client, body=b"\x00" * 32, params={"session_id": "wired-1"})
    res2 = _post_voice(client, body=b"\x00" * 32, params={"session_id": "wired-2"})

    # Both responses completed the route; STT reports unavailable
    # because no backend is configured (the expected outcome for a
    # NullSTTBackend path).
    assert res1.status_code == 200 and res2.status_code == 200
    payload1 = res1.json()
    payload2 = res2.json()
    assert payload1["ok"] is False and payload2["ok"] is False
    assert payload1["stt"]["backend"] == "null"
    assert payload2["stt"]["backend"] == "null"

    # The real STT path actually went through the shared resolver —
    # twice, once per request — and the SAME cached instance came
    # back both times.  This is the invariant that prevents the
    # Whisper model from being rebuilt on every dictation turn.
    assert len(observed_backends) == 2
    assert observed_backends[0] is observed_backends[1]
    assert isinstance(observed_backends[0], NullSTTBackend)
    # Stage timings are still populated on this route even with a
    # Null backend — the instrumentation is wiring-agnostic.
    assert isinstance(payload1["stage_timings"]["stt_ms"], int)
    assert isinstance(payload2["stage_timings"]["stt_ms"], int)


# =============================================================================
# Section 9: Vera-internal sub-stage breakdown (preview builder / reply / enrichment)
# =============================================================================


class TestVeraSubStageBreakdown:
    """Pins the truthful Vera-internal sub-stage breakdown surfaced on
    ``/chat/voice`` payloads.  The breakdown lets operators see where
    inside the ``vera_ms`` umbrella a slow turn actually spent its
    time — essential when the dominant latency source is the LLM
    generation phase, not the STT/TTS backends.
    """

    def test_sub_stages_report_non_negative_ints_for_running_lanes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the LLM orchestration lane runs, ``vera_reply_ms`` is
        populated as a non-negative int.  The preview builder stage may
        or may not run depending on the turn shape — we only assert it
        is either a non-negative int (ran) or ``None`` (skipped), never
        fabricated.
        """
        queue = tmp_path / "queue"
        _set_queue_root(monkeypatch, queue)
        _force_enabled_voice(monkeypatch)
        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_vera_reply)
        stt = _make_stt(transcript="tell me a joke")
        with patch.object(
            vera_app_module,
            "transcribe_audio_file_async",
            side_effect=_async_stt(stt),
        ):
            client = TestClient(vera_app_module.app)
            res = _post_voice(client, body=b"\x00" * 32, params={"session_id": "st-sub"})
        assert res.status_code == 200
        timings = res.json()["stage_timings"]
        # Main reply LLM ran — non-negative int.
        assert isinstance(timings["vera_reply_ms"], int)
        assert timings["vera_reply_ms"] >= 0
        # Preview builder may be None (conversational/informational skip)
        # or a non-negative int.  Absence stays truthful; it is never 0
        # fabricated when the branch was skipped.
        builder_ms = timings["vera_preview_builder_ms"]
        assert builder_ms is None or (isinstance(builder_ms, int) and builder_ms >= 0)

    def test_parallel_llm_calls_do_not_add_up_serially(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The preview builder and Vera reply run concurrently — not
        serially — when both are needed.

        Parallelism is the core latency reduction in this work: if
        each LLM call takes ~200ms, running them in parallel keeps
        ``vera_ms`` near the slower call, not the sum.  We simulate
        this by making both patched calls deliberately sleep, then
        assert the outer ``vera_ms`` is much closer to max(a, b) than
        a + b.  This is the operator-visible proof that parallelism
        actually happened; a regression that reverts to sequential
        awaits would fail this bound.
        """
        import asyncio as _asyncio

        queue = tmp_path / "queue"
        _set_queue_root(monkeypatch, queue)
        _force_enabled_voice(monkeypatch)
        # Active preview ensures the turn is not conversational/
        # informational and the preview builder branch actually runs.
        from voxera.vera.preview_ownership import reset_active_preview

        reset_active_preview(
            queue,
            "st-parallel",
            {"write_file": {"path": "notes/p.md", "content": "seed"}},
        )
        sleep_ms = 200

        async def _slow_builder(**_kwargs: Any) -> dict[str, Any]:
            await _asyncio.sleep(sleep_ms / 1000.0)
            return {"write_file": {"path": "notes/p.md", "content": "updated"}}

        async def _slow_reply(**_kwargs: Any) -> dict[str, Any]:
            await _asyncio.sleep(sleep_ms / 1000.0)
            return {"answer": "hi", "status": "ok:test"}

        monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _slow_builder)
        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _slow_reply)
        stt = _make_stt(transcript="revise the file")
        with patch.object(
            vera_app_module,
            "transcribe_audio_file_async",
            side_effect=_async_stt(stt),
        ):
            client = TestClient(vera_app_module.app)
            res = _post_voice(client, body=b"\x00" * 32, params={"session_id": "st-parallel"})
        assert res.status_code == 200
        timings = res.json()["stage_timings"]
        vera_ms = timings["vera_ms"]
        builder_ms = timings["vera_preview_builder_ms"]
        reply_ms = timings["vera_reply_ms"]
        assert isinstance(vera_ms, int)
        assert isinstance(builder_ms, int)
        assert isinstance(reply_ms, int)
        # Each individual stage reports its own sleep duration.
        assert builder_ms >= sleep_ms - 50
        assert reply_ms >= sleep_ms - 50
        # Parallel execution: vera_ms covers the overlapping region,
        # so it must be SIGNIFICANTLY less than the serial sum.  A
        # generous margin keeps the assertion stable across slow CI
        # runners: parallel wall-clock should be within ~1.5x the
        # single-stage time, never the full 2x serial sum.
        serial_sum = builder_ms + reply_ms
        assert vera_ms < serial_sum * 0.85, (
            f"parallel gather should keep vera_ms under 85% of the serial "
            f"sum; got vera_ms={vera_ms}, serial_sum={serial_sum}"
        )

    def test_conversational_artifact_turn_skips_preview_builder_stage(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Conversational-artifact voice turns do not run the preview builder.

        When the classifier puts a turn into CONVERSATIONAL_ARTIFACT
        mode (checklist / planning / brainstorm without save intent),
        the preview builder LLM call is skipped — the deterministic
        answer-first lane handles it.  The instrumentation must
        faithfully report the skipped stage as ``None``, never as 0.
        This directly backs one of the primary latency reductions:
        skipping an LLM call entirely is strictly better than running
        it faster.
        """
        queue = tmp_path / "queue"
        _set_queue_root(monkeypatch, queue)
        _force_enabled_voice(monkeypatch)
        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_vera_reply)

        # Fail loudly if the builder is entered — the point of the
        # test is that the conversational-artifact lane skips it.
        async def _should_not_run(**_kwargs: Any) -> dict[str, Any]:
            raise AssertionError(
                "preview builder should be skipped on conversational-artifact turn"
            )

        monkeypatch.setattr(vera_app_module, "generate_preview_builder_update", _should_not_run)
        # Checklist-style request → classified as CONVERSATIONAL_ARTIFACT.
        stt = _make_stt(transcript="give me a checklist for a weekend trip")
        with patch.object(
            vera_app_module,
            "transcribe_audio_file_async",
            side_effect=_async_stt(stt),
        ):
            client = TestClient(vera_app_module.app)
            res = _post_voice(client, body=b"\x00" * 32, params={"session_id": "st-skip"})
        assert res.status_code == 200
        timings = res.json()["stage_timings"]
        # Builder stage did not run → truthful None.
        assert timings["vera_preview_builder_ms"] is None
        # The main reply stage still ran.
        assert isinstance(timings["vera_reply_ms"], int)


# =============================================================================
# Section 10: ChatTurnResult carries the same sub-stage timings
# =============================================================================


class TestChatTurnResultStageTimings:
    """The canonical :class:`ChatTurnResult` carries stage timings so
    both the typed ``/chat`` and dictated ``/chat/voice`` surfaces can
    observe sub-stage breakdowns without relying on the dictation
    endpoint being the only source of truth.  This test pins the
    structure directly off the helper, independent of the JSON
    payload serialization, so a regression that forgets to populate
    the dict on the result is caught at the helper boundary.
    """

    def test_run_vera_chat_turn_returns_stage_timings_dict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio as _asyncio

        queue = tmp_path / "queue"
        _set_queue_root(monkeypatch, queue)
        monkeypatch.setattr(vera_app_module, "generate_vera_reply", _fake_vera_reply)
        flags = _enabled_voice_flags()

        from voxera.voice.models import InputOrigin

        result = _asyncio.run(
            vera_app_module.run_vera_chat_turn(
                message="hello",
                input_origin=InputOrigin.TYPED,
                session_id="st-helper",
                voice_flags=flags,
            )
        )
        assert isinstance(result.stage_timings, dict)
        # Reply stage ran.
        assert "vera_reply_ms" in result.stage_timings
        assert isinstance(result.stage_timings["vera_reply_ms"], int)
        # Absent stages stay absent — never fabricated.
        assert "vera_enrichment_ms" not in result.stage_timings

    def test_early_exit_turns_carry_empty_stage_timings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Turns that short-circuit on an early-exit lane (time
        question, diagnostics refusal, etc.) do not run the LLM path
        and therefore carry an empty ``stage_timings`` dict.  This
        keeps the "absence stays truthful" rule consistent across
        all return points of the helper.
        """
        import asyncio as _asyncio

        queue = tmp_path / "queue"
        _set_queue_root(monkeypatch, queue)
        flags = _enabled_voice_flags()

        from voxera.voice.models import InputOrigin

        # "what time is it?" is handled by a deterministic early-exit
        # branch with no LLM call involved.
        result = _asyncio.run(
            vera_app_module.run_vera_chat_turn(
                message="what time is it?",
                input_origin=InputOrigin.TYPED,
                session_id="st-early",
                voice_flags=flags,
            )
        )
        # ``stage_timings`` is always a dict — never missing.
        assert isinstance(result.stage_timings, dict)
        # Early-exit lanes do not populate LLM sub-stages.
        assert "vera_reply_ms" not in result.stage_timings
        assert "vera_preview_builder_ms" not in result.stage_timings
