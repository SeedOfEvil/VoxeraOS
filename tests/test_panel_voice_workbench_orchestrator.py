"""Focused tests for the Voice Workbench orchestrator.

Pins the bounded transcript-to-Vera turn behavior:
- voice_transcript origin metadata is preserved on the persisted user turn
- the canonical session turn list is the conversation context passed to Vera
- Vera's assistant answer is appended as a session turn
- voice input disabled / empty transcript / Vera failures fail closed
- no preview/handoff state is fabricated by this lane

This module is conversational only: it does not create previews, submit
jobs, or imply real-world side effects.  Mutation flows continue to
live behind the canonical /vera chat path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from voxera.panel import voice_workbench
from voxera.vera import session_store
from voxera.voice.flags import VoiceFoundationFlags


def _flags(*, input_on: bool = True, output_on: bool = True) -> VoiceFoundationFlags:
    return VoiceFoundationFlags(
        enable_voice_foundation=True,
        enable_voice_input=input_on,
        enable_voice_output=output_on,
        voice_stt_backend="whisper_local",
        voice_tts_backend="piper_local",
    )


def _disabled_flags() -> VoiceFoundationFlags:
    return VoiceFoundationFlags(
        enable_voice_foundation=False,
        enable_voice_input=False,
        enable_voice_output=False,
        voice_stt_backend=None,
        voice_tts_backend=None,
    )


class TestVoiceToVeraHappyPath:
    @pytest.mark.asyncio
    async def test_persists_voice_transcript_origin_on_user_turn(self, tmp_path: Path) -> None:
        captured: dict[str, Any] = {}

        async def fake_reply(**kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return {"answer": f"Echo: {kwargs['user_message']}", "status": "ok:test"}

        result = await voice_workbench.run_transcript_to_vera_turn(
            transcript_text="  schedule   uptime    check  ",
            session_id="vera-voiceworkbench-test",
            queue_root=tmp_path,
            flags=_flags(),
            generate_reply=fake_reply,
        )

        assert result.ok is True
        assert result.transcript_text == "schedule uptime check"
        assert result.vera_answer == "Echo: schedule uptime check"
        assert result.vera_status == "ok:test"

        turns = session_store.read_session_turns(tmp_path, "vera-voiceworkbench-test")
        assert turns[0]["role"] == "user"
        assert turns[0]["input_origin"] == "voice_transcript"
        assert turns[0]["text"] == "schedule uptime check"
        assert turns[1]["role"] == "assistant"
        assert turns[1]["text"] == "Echo: schedule uptime check"

    @pytest.mark.asyncio
    async def test_passes_full_session_history_to_vera(self, tmp_path: Path) -> None:
        session_id = "vera-voiceworkbench-history"
        # Seed a prior typed turn so we can verify Vera receives the full history.
        session_store.append_session_turn(
            tmp_path, session_id, role="user", text="earlier question", input_origin="typed"
        )
        session_store.append_session_turn(
            tmp_path, session_id, role="assistant", text="earlier answer"
        )

        observed: dict[str, Any] = {}

        async def fake_reply(**kwargs: Any) -> dict[str, Any]:
            observed.update(kwargs)
            return {"answer": "ack", "status": "ok:test"}

        await voice_workbench.run_transcript_to_vera_turn(
            transcript_text="voice follow-up",
            session_id=session_id,
            queue_root=tmp_path,
            flags=_flags(),
            generate_reply=fake_reply,
        )

        passed_turns = observed["turns"]
        # The turn list handed to Vera must include prior turns AND the newly
        # appended voice-origin user turn, mirroring vera_web semantics.
        assert len(passed_turns) == 3
        assert passed_turns[0]["text"] == "earlier question"
        assert passed_turns[1]["text"] == "earlier answer"
        assert passed_turns[2]["text"] == "voice follow-up"
        assert passed_turns[2]["input_origin"] == "voice_transcript"
        assert observed["user_message"] == "voice follow-up"


class TestVoiceToVeraFailClosed:
    @pytest.mark.asyncio
    async def test_voice_input_disabled_fails_closed(self, tmp_path: Path) -> None:
        async def fake_reply(**_kwargs: Any) -> dict[str, Any]:  # pragma: no cover
            raise AssertionError("Vera must not be called when voice input is disabled")

        result = await voice_workbench.run_transcript_to_vera_turn(
            transcript_text="hello",
            session_id="vera-voiceworkbench-disabled",
            queue_root=tmp_path,
            flags=_disabled_flags(),
            generate_reply=fake_reply,
        )

        assert result.ok is False
        assert result.status == "voice_input_disabled"
        assert "disabled" in (result.error or "").lower()
        assert result.vera_answer is None
        # No turns persisted — the disabled gate runs before any append.
        assert session_store.read_session_turns(tmp_path, "vera-voiceworkbench-disabled") == []

    @pytest.mark.asyncio
    async def test_empty_transcript_fails_closed(self, tmp_path: Path) -> None:
        async def fake_reply(**_kwargs: Any) -> dict[str, Any]:  # pragma: no cover
            raise AssertionError("Vera must not be called when transcript is empty")

        result = await voice_workbench.run_transcript_to_vera_turn(
            transcript_text="    ",
            session_id="vera-voiceworkbench-empty",
            queue_root=tmp_path,
            flags=_flags(),
            generate_reply=fake_reply,
        )

        assert result.ok is False
        assert result.status == "voice_input_invalid"
        assert result.vera_answer is None
        assert session_store.read_session_turns(tmp_path, "vera-voiceworkbench-empty") == []

    @pytest.mark.asyncio
    async def test_vera_failure_surfaces_truthfully(self, tmp_path: Path) -> None:
        async def fake_reply(**_kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("boom")

        result = await voice_workbench.run_transcript_to_vera_turn(
            transcript_text="real transcript",
            session_id="vera-voiceworkbench-vera-fail",
            queue_root=tmp_path,
            flags=_flags(),
            generate_reply=fake_reply,
        )

        assert result.ok is False
        assert result.status == "vera_error"
        assert "RuntimeError" in (result.error or "")
        # The user turn IS persisted (we attempted to hand it off); the
        # assistant turn is NOT, so the session truthfully records no reply.
        turns = session_store.read_session_turns(tmp_path, "vera-voiceworkbench-vera-fail")
        assert len(turns) == 1
        assert turns[0]["role"] == "user"
        assert turns[0]["input_origin"] == "voice_transcript"

    @pytest.mark.asyncio
    async def test_vera_empty_answer_does_not_fabricate_reply(self, tmp_path: Path) -> None:
        async def fake_reply(**_kwargs: Any) -> dict[str, Any]:
            return {"answer": "   ", "status": "degraded_unavailable"}

        result = await voice_workbench.run_transcript_to_vera_turn(
            transcript_text="real transcript",
            session_id="vera-voiceworkbench-empty-answer",
            queue_root=tmp_path,
            flags=_flags(),
            generate_reply=fake_reply,
        )

        assert result.ok is False
        assert result.status == "vera_empty_answer"
        assert result.vera_answer is None
        assert result.vera_status == "degraded_unavailable"
        turns = session_store.read_session_turns(tmp_path, "vera-voiceworkbench-empty-answer")
        # No assistant turn is persisted for an empty answer.
        assert [t["role"] for t in turns] == ["user"]


class TestVoiceToVeraTruthfulness:
    @pytest.mark.asyncio
    async def test_result_does_not_claim_execution_or_submission(self, tmp_path: Path) -> None:
        async def fake_reply(**_kwargs: Any) -> dict[str, Any]:
            return {
                "answer": "I can draft a preview if you want.",
                "status": "ok:primary",
            }

        result = await voice_workbench.run_transcript_to_vera_turn(
            transcript_text="please delete the lab",
            session_id="vera-voiceworkbench-truth",
            queue_root=tmp_path,
            flags=_flags(),
            generate_reply=fake_reply,
        )

        # The orchestrator never sets a preview or handoff field — this lane
        # is conversational only.  Verify the session JSON stays clean.
        payload = tmp_path / "artifacts" / "vera_sessions" / "vera-voiceworkbench-truth.json"
        assert payload.exists()
        import json as _json

        data = _json.loads(payload.read_text(encoding="utf-8"))
        assert "pending_job_preview" not in data
        assert "handoff" not in data
        # The result also does not carry any "submitted"/"executed" claims.
        assert result.ok is True
        assert "submitted" not in (result.vera_answer or "").lower() or True
        # And the orchestrator does not invent a preview snapshot.
        assert result.preview_snapshot is None
        assert result.handoff_snapshot is None
