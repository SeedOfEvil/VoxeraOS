from __future__ import annotations

import json
from pathlib import Path

import pytest

from voxera.voice.flags import load_voice_foundation_flags
from voxera.voice.input import VoiceInputDisabledError, ingest_voice_transcript
from voxera.voice.output import voice_output_status


def test_voice_flags_load_from_runtime_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "enable_voice_foundation": True,
                "enable_voice_input": True,
                "enable_voice_output": True,
                "voice_stt_backend": "stub-transcriber",
                "voice_tts_backend": "stub-speaker",
            }
        ),
        encoding="utf-8",
    )

    flags = load_voice_foundation_flags(config_path=config_path, environ={})

    assert flags.voice_input_enabled is True
    assert flags.voice_output_enabled is True
    assert flags.voice_stt_backend == "stub-transcriber"
    assert flags.voice_tts_backend == "stub-speaker"


def test_voice_input_requires_enabled_flag() -> None:
    with pytest.raises(VoiceInputDisabledError):
        ingest_voice_transcript(transcript_text="hello", voice_input_enabled=False)


def test_voice_output_status_reports_placeholder_when_backend_missing() -> None:
    flags = load_voice_foundation_flags(
        environ={
            "VOXERA_ENABLE_VOICE_FOUNDATION": "1",
            "VOXERA_ENABLE_VOICE_OUTPUT": "1",
        }
    )

    payload = voice_output_status(flags)
    assert payload["voice_output_attempted"] is True
    assert payload["voice_output_reason"] == "voice_output_backend_missing"
