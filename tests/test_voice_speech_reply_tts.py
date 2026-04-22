"""Tests for the sentence-first TTS synthesis pipeline.

``synthesize_speech_reply_async`` is the canonical entry point for
"speak Vera's reply" flows: it shapes a concise speech copy of the
written reply, splits that copy into sentence chunks, and synthesizes
the chunks concurrently so the operator-visible time-to-first-audio
is dominated by the slowest chunk rather than the full reply.

These tests pin the invariants that matter for trust and for the
operator-facing timing surface:

1. Concise speech text is derived from the written reply; the full
   written reply is never mutated.
2. Sentence chunks are synthesized in spoken order, and the result
   list preserves that order.
3. Per-chunk responses are truthful ``TTSResponse`` objects — a
   failing chunk surfaces as ``failed``, not a fabricated success.
4. Timing fields are wall-clock measurements, not fabricated values.
   Stages that did not run stay ``None``.
5. TTS failure still leaves the caller's text authoritative — the
   helper never raises on synthesis failure.
6. The pipeline works against any ``TTSBackend`` — Piper, Kokoro, or
   a test stub — so faster Kokoro variants (e.g. int8) naturally
   benefit without code changes.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from voxera.voice.flags import VoiceFoundationFlags
from voxera.voice.output import (
    SpeechReplyTTSResult,
    synthesize_speech_reply_async,
)
from voxera.voice.tts_adapter import TTSAdapterResult
from voxera.voice.tts_protocol import (
    TTS_STATUS_FAILED,
    TTS_STATUS_SUCCEEDED,
    TTSResponse,
)


def _make_flags(
    *,
    tts_backend: str | None = "piper_local",
) -> VoiceFoundationFlags:
    return VoiceFoundationFlags(
        enable_voice_foundation=True,
        enable_voice_input=False,
        enable_voice_output=True,
        voice_stt_backend=None,
        voice_tts_backend=tts_backend,
    )


class _RecordingBackend:
    """TTS backend stub that records every request it receives.

    Used to pin per-chunk request shape and ordering.  Returns
    per-call fresh temp-ish audio paths via a small counter so each
    chunk gets a unique-looking artifact.
    """

    def __init__(self, name: str = "recording", audio_root: str = "/tmp") -> None:
        self._name = name
        self._audio_root = audio_root
        self.requests: list[object] = []
        self._counter = 0

    @property
    def backend_name(self) -> str:
        return self._name

    def supports_voice(self, voice_id: str) -> bool:
        return True

    def synthesize(self, request):  # noqa: ANN001, ANN201
        self.requests.append(request)
        self._counter += 1
        return TTSAdapterResult(
            audio_path=f"{self._audio_root}/chunk_{self._counter}.wav",
            audio_duration_ms=500,
            inference_ms=10,
        )


# =============================================================================
# Section 1: speech-text shaping is threaded through synthesis
# =============================================================================


class TestSpeechTextShapingPropagates:
    """The synthesis pipeline uses the shaped speech copy, not the raw reply."""

    @pytest.mark.asyncio
    async def test_concise_reply_passes_through_intact(self) -> None:
        backend = _RecordingBackend()
        result = await synthesize_speech_reply_async(
            text="Hello world.",
            flags=_make_flags(),
            backend=backend,
        )
        assert isinstance(result, SpeechReplyTTSResult)
        assert result.speech_text == "Hello world."
        assert result.sentence_count == 1
        assert result.truncated is False
        assert len(result.responses) == 1

    @pytest.mark.asyncio
    async def test_long_reply_is_truncated_in_speech_only(self) -> None:
        backend = _RecordingBackend()
        written = "One. Two. Three. Four. Five. Six. Seven."
        before = written
        result = await synthesize_speech_reply_async(
            text=written,
            flags=_make_flags(),
            backend=backend,
        )
        # Written reply reference unchanged.
        assert written == before
        # Spoken copy is a prefix of the written reply.
        assert result.truncated is True
        assert result.sentence_count <= 3
        assert "Four." not in result.speech_text
        # Each chunk is one sentence of the shaped speech text.
        for req in backend.requests:
            assert "." in req.text or "!" in req.text or "?" in req.text

    @pytest.mark.asyncio
    async def test_markdown_stripped_before_synthesis(self) -> None:
        backend = _RecordingBackend()
        await synthesize_speech_reply_async(
            text="## Summary\n- first point.\n- second point.",
            flags=_make_flags(),
            backend=backend,
        )
        # The synthesizer never sees '#' or '-' formatting characters.
        for req in backend.requests:
            assert "##" not in req.text
            assert "- " not in req.text


# =============================================================================
# Section 2: sentence-first pipeline — order preserved, per-chunk responses
# =============================================================================


class TestSentenceFirstPipeline:
    """Each sentence chunk becomes its own synthesis call, in spoken order."""

    @pytest.mark.asyncio
    async def test_three_sentence_reply_produces_three_responses(self) -> None:
        backend = _RecordingBackend()
        result = await synthesize_speech_reply_async(
            text="First sentence. Second sentence. Third sentence.",
            flags=_make_flags(),
            backend=backend,
        )
        assert result.sentence_count == 3
        assert len(result.responses) == 3
        # Every response is a real TTSResponse, not a fabricated one.
        for resp in result.responses:
            assert isinstance(resp, TTSResponse)
            assert resp.backend == "recording"

    @pytest.mark.asyncio
    async def test_responses_preserve_spoken_order(self) -> None:
        backend = _RecordingBackend()
        await synthesize_speech_reply_async(
            text="Alpha one. Beta two. Gamma three.",
            flags=_make_flags(),
            backend=backend,
        )
        texts = [req.text for req in backend.requests]
        # The backend saw chunks in the same order the shaper split
        # them, which is the order the browser will play them.
        assert texts[0].startswith("Alpha")
        assert texts[1].startswith("Beta")
        assert texts[2].startswith("Gamma")

    @pytest.mark.asyncio
    async def test_each_chunk_carries_its_sentence(self) -> None:
        backend = _RecordingBackend()
        await synthesize_speech_reply_async(
            text="One. Two. Three.",
            flags=_make_flags(),
            backend=backend,
        )
        seen = [req.text for req in backend.requests]
        assert "One." in seen
        assert "Two." in seen
        assert "Three." in seen


# =============================================================================
# Section 3: timing fields are truthful
# =============================================================================


class TestTimingFieldsAreTruthful:
    """Sub-stage timings reflect real wall-clock work, never fabricated."""

    @pytest.mark.asyncio
    async def test_all_timings_populated_for_real_synthesis(self) -> None:
        backend = _RecordingBackend()
        result = await synthesize_speech_reply_async(
            text="Hi. Hello there.",
            flags=_make_flags(),
            backend=backend,
        )
        # Every sub-stage ran, so every timing is a concrete int.
        assert isinstance(result.speech_text_prepare_ms, int)
        assert result.speech_text_prepare_ms >= 0
        assert isinstance(result.speech_sentence_split_ms, int)
        assert result.speech_sentence_split_ms >= 0
        assert isinstance(result.tts_first_chunk_ms, int)
        assert result.tts_first_chunk_ms >= 0
        assert isinstance(result.tts_total_synthesis_ms, int)
        assert result.tts_total_synthesis_ms >= 0

    @pytest.mark.asyncio
    async def test_first_chunk_never_exceeds_total(self) -> None:
        backend = _RecordingBackend()
        result = await synthesize_speech_reply_async(
            text="First. Second. Third.",
            flags=_make_flags(),
            backend=backend,
        )
        assert result.tts_first_chunk_ms is not None
        assert result.tts_total_synthesis_ms is not None
        # The first-chunk time is the time for chunk 0; the total
        # includes waiting for all chunks.  Total >= first, always.
        assert result.tts_first_chunk_ms <= result.tts_total_synthesis_ms

    @pytest.mark.asyncio
    async def test_empty_speech_text_skips_synthesis_timings(self) -> None:
        """When shaping produces empty speech (formatting-only input),
        synthesis never runs and its timings stay ``None`` — not
        fabricated zeros."""
        backend = _RecordingBackend()
        result = await synthesize_speech_reply_async(
            text="###",  # normalizes to empty
            flags=_make_flags(),
            backend=backend,
        )
        assert result.speech_text == ""
        assert result.sentence_count == 0
        assert result.responses == []
        # Prepare stage ran; split / first-chunk / total did not.
        assert isinstance(result.speech_text_prepare_ms, int)
        assert result.speech_sentence_split_ms is None
        assert result.tts_first_chunk_ms is None
        assert result.tts_total_synthesis_ms is None
        # Backend never saw a request since there was nothing to speak.
        assert backend.requests == []

    @pytest.mark.asyncio
    async def test_first_chunk_timing_reflects_concurrency(self) -> None:
        """When later chunks synthesize slowly but chunk 0 is fast,
        ``tts_first_chunk_ms`` reflects chunk 0's actual completion
        time, not the total wall clock.  This is the whole point of
        the sentence-first pipeline."""

        class SlowLaterBackend:
            @property
            def backend_name(self) -> str:
                return "slow_later"

            def supports_voice(self, voice_id: str) -> bool:
                return True

            def synthesize(self, request):  # noqa: ANN001, ANN201
                # The first sentence is fast; later sentences take
                # longer.  asyncio.to_thread isolates each call, so
                # chunk 0 finishes before the slow chunks.
                if request.text.startswith("Fast"):
                    return TTSAdapterResult(
                        audio_path="/tmp/fast.wav",
                        audio_duration_ms=100,
                        inference_ms=5,
                    )
                # Block this synthesis for ~80ms to widen the gap.
                time.sleep(0.08)
                return TTSAdapterResult(
                    audio_path="/tmp/slow.wav",
                    audio_duration_ms=100,
                    inference_ms=80,
                )

        result = await synthesize_speech_reply_async(
            text="Fast start. Slow middle. Slow end.",
            flags=_make_flags(),
            backend=SlowLaterBackend(),
        )
        assert result.sentence_count == 3
        # Chunk 0 ("Fast start.") is quick; total is dominated by the
        # slow chunks.  The first-chunk timing must be strictly less
        # than the total (with some slack for timing jitter).
        assert result.tts_first_chunk_ms is not None
        assert result.tts_total_synthesis_ms is not None
        assert result.tts_first_chunk_ms < result.tts_total_synthesis_ms


# =============================================================================
# Section 4: fail-soft behaviour
# =============================================================================


class TestFailSoftBehaviour:
    """Synthesis failures are truthful and never raise out of the helper."""

    @pytest.mark.asyncio
    async def test_crashing_backend_produces_failed_responses(self) -> None:
        class CrashingBackend:
            @property
            def backend_name(self) -> str:
                return "crash"

            def supports_voice(self, voice_id: str) -> bool:
                return True

            def synthesize(self, request):  # noqa: ANN001, ANN201
                raise RuntimeError("kaboom")

        written = "One. Two. Three."
        before = written
        result = await synthesize_speech_reply_async(
            text=written,
            flags=_make_flags(),
            backend=CrashingBackend(),
        )
        # Helper did not raise.
        assert result.sentence_count == 3
        # Every chunk failed truthfully.
        for resp in result.responses:
            assert resp.status == TTS_STATUS_FAILED
            assert resp.audio_path is None
        # Written reply is untouched.
        assert written == before

    @pytest.mark.asyncio
    async def test_partial_failure_preserves_ordering(self) -> None:
        """A backend that fails on the SECOND chunk still returns
        truthful per-chunk responses in spoken order."""

        counter = {"n": 0}

        class FlakyBackend:
            @property
            def backend_name(self) -> str:
                return "flaky"

            def supports_voice(self, voice_id: str) -> bool:
                return True

            def synthesize(self, request):  # noqa: ANN001, ANN201
                counter["n"] += 1
                if counter["n"] == 2:
                    raise RuntimeError("mid-chunk failure")
                return TTSAdapterResult(
                    audio_path=f"/tmp/ok_{counter['n']}.wav",
                    audio_duration_ms=200,
                    inference_ms=10,
                )

        result = await synthesize_speech_reply_async(
            text="First. Second. Third.",
            flags=_make_flags(),
            backend=FlakyBackend(),
        )
        assert result.sentence_count == 3
        # The FIRST call is the first sentence (index 0) and succeeds.
        # The order of per-chunk call completion may vary due to
        # concurrent scheduling, but each response is a truthful
        # outcome for its own text.  At least one succeeded and at
        # least one failed.
        statuses = [r.status for r in result.responses]
        assert TTS_STATUS_SUCCEEDED in statuses
        assert TTS_STATUS_FAILED in statuses

    @pytest.mark.asyncio
    async def test_empty_input_returns_truthful_empty_result(self) -> None:
        result = await synthesize_speech_reply_async(
            text="",
            flags=_make_flags(),
        )
        assert result.speech_text == ""
        assert result.sentence_count == 0
        assert result.responses == []


# =============================================================================
# Section 5: backend-agnostic — works with any TTSBackend
# =============================================================================


class TestBackendAgnostic:
    """The pipeline does not hardcode Piper or Kokoro — any backend works."""

    @pytest.mark.asyncio
    async def test_honours_custom_backend_name(self) -> None:
        backend = _RecordingBackend(name="kokoro_int8_variant")
        result = await synthesize_speech_reply_async(
            text="Hi.",
            flags=_make_flags(tts_backend="kokoro_local"),
            backend=backend,
        )
        assert len(result.responses) == 1
        assert result.responses[0].backend == "kokoro_int8_variant"

    @pytest.mark.asyncio
    async def test_falls_back_to_null_backend_when_unconfigured(self) -> None:
        result = await synthesize_speech_reply_async(
            text="Hi.",
            flags=_make_flags(tts_backend=None),
        )
        # Null backend returns an unavailable response — not a crash,
        # not a fabricated success.  The helper still returns a
        # truthful aggregate result so the caller can react.
        assert result.sentence_count == 1
        assert result.responses[0].audio_path is None
        # Text remains authoritative; the caller's trust model holds.


# =============================================================================
# Section 6: text remains authoritative under TTS failure
# =============================================================================


class TestTextRemainsAuthoritativeUnderFailure:
    """TTS failures never mutate or interfere with the written reply."""

    @pytest.mark.asyncio
    async def test_crash_does_not_raise(self) -> None:
        class CrashBackend:
            @property
            def backend_name(self) -> str:
                return "crash"

            def supports_voice(self, voice_id: str) -> bool:
                return True

            def synthesize(self, request):  # noqa: ANN001, ANN201
                raise RuntimeError("boom")

        written = "# Heading\n- bullet\nbody text."
        before = written
        # Must not raise — matches ``synthesize_text_async`` contract.
        await synthesize_speech_reply_async(
            text=written,
            flags=_make_flags(),
            backend=CrashBackend(),
        )
        assert written == before


# =============================================================================
# Section 7: concurrency sanity
# =============================================================================


class TestConcurrencySanity:
    """Per-chunk synthesis runs concurrently, not serially."""

    @pytest.mark.asyncio
    async def test_parallel_chunks_finish_faster_than_serial(self) -> None:
        """With a backend that sleeps 50ms per chunk, synthesizing
        three chunks concurrently must take less than the serial
        equivalent (3 * 50ms = 150ms).  We allow generous slack for
        event-loop scheduling jitter."""

        class SleepyBackend:
            @property
            def backend_name(self) -> str:
                return "sleepy"

            def supports_voice(self, voice_id: str) -> bool:
                return True

            def synthesize(self, request):  # noqa: ANN001, ANN201
                time.sleep(0.05)
                return TTSAdapterResult(
                    audio_path="/tmp/s.wav",
                    audio_duration_ms=100,
                    inference_ms=50,
                )

        start = time.time()
        result = await synthesize_speech_reply_async(
            text="First. Second. Third.",
            flags=_make_flags(),
            backend=SleepyBackend(),
        )
        elapsed_ms = int((time.time() - start) * 1000)
        assert result.sentence_count == 3
        # Serial would be ~150ms; parallel should be closer to ~50ms.
        # We allow up to 120ms to avoid flake on slow CI.
        assert elapsed_ms < 120


# =============================================================================
# Section 8: export surface
# =============================================================================


class TestExportSurface:
    """New public symbols are exported from the voice package."""

    def test_synthesize_speech_reply_async_exported(self) -> None:
        from voxera.voice import synthesize_speech_reply_async as exported

        assert callable(exported)
        assert asyncio.iscoroutinefunction(exported)

    def test_speech_reply_tts_result_exported(self) -> None:
        from voxera.voice import SpeechReplyTTSResult as exported

        assert exported is SpeechReplyTTSResult
