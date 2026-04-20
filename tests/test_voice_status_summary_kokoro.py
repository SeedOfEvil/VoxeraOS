"""Tests for Kokoro awareness in ``build_voice_status_summary``.

Pins:
- ``tts_dependency.kokoro_model`` sub-block appears when Kokoro is the
  configured backend
- model / voices path existence flags are truthful
- hint is a specific, non-generic string when paths are missing
- schema version bump is reflected in the payload
"""

from __future__ import annotations

from pathlib import Path

from voxera.voice.flags import VoiceFoundationFlags
from voxera.voice.voice_status_summary import (
    VOICE_STATUS_SUMMARY_SCHEMA_VERSION,
    build_voice_status_summary,
)


def _flags(
    *,
    kokoro_model: str | None = None,
    kokoro_voices: str | None = None,
    kokoro_voice: str | None = None,
) -> VoiceFoundationFlags:
    return VoiceFoundationFlags(
        enable_voice_foundation=True,
        enable_voice_input=False,
        enable_voice_output=True,
        voice_stt_backend=None,
        voice_tts_backend="kokoro_local",
        voice_tts_kokoro_model=kokoro_model,
        voice_tts_kokoro_voices=kokoro_voices,
        voice_tts_kokoro_voice=kokoro_voice,
    )


class TestKokoroStatusSummary:
    def test_schema_version_bumped(self) -> None:
        assert VOICE_STATUS_SUMMARY_SCHEMA_VERSION == 4

    def test_kokoro_model_sub_block_present(self) -> None:
        payload = build_voice_status_summary(_flags())
        assert "kokoro_model" in payload["tts_dependency"]

    def test_kokoro_model_unconfigured(self) -> None:
        payload = build_voice_status_summary(_flags())
        km = payload["tts_dependency"]["kokoro_model"]
        assert km["configured"] is False
        assert km["model_exists"] is False
        assert km["voices_exists"] is False
        assert km["effective_voice"] == "af_sarah"
        assert km["hint"]

    def test_kokoro_model_with_paths(self, tmp_path: Path) -> None:
        model_path = tmp_path / "kokoro.onnx"
        voices_path = tmp_path / "voices.bin"
        model_path.write_bytes(b"")
        voices_path.write_bytes(b"")
        payload = build_voice_status_summary(
            _flags(
                kokoro_model=str(model_path),
                kokoro_voices=str(voices_path),
                kokoro_voice="am_michael",
            )
        )
        km = payload["tts_dependency"]["kokoro_model"]
        assert km["configured"] is True
        assert km["model_exists"] is True
        assert km["voices_exists"] is True
        assert km["effective_voice"] == "am_michael"
        assert km["voice"] == "am_michael"
        assert "hint" not in km

    def test_kokoro_missing_model_file_yields_hint(self, tmp_path: Path) -> None:
        voices_path = tmp_path / "voices.bin"
        voices_path.write_bytes(b"")
        payload = build_voice_status_summary(
            _flags(
                kokoro_model=str(tmp_path / "missing.onnx"),
                kokoro_voices=str(voices_path),
            )
        )
        km = payload["tts_dependency"]["kokoro_model"]
        assert km["model_exists"] is False
        assert "does not exist" in km["hint"].lower()

    def test_piper_path_unaffected(self) -> None:
        """Selecting Kokoro does not break the Piper path in the same payload.

        The ``piper_model`` sub-block only appears when piper_local is
        the selected backend; Kokoro selection must not inject it.
        """
        payload = build_voice_status_summary(_flags())
        assert "piper_model" not in payload["tts_dependency"]

    def test_tts_backend_truthful_in_payload(self) -> None:
        payload = build_voice_status_summary(_flags())
        assert payload["tts"]["backend"] == "kokoro_local"
