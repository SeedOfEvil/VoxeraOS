from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..config import resolve_config_path


@dataclass(frozen=True)
class VoiceFoundationFlags:
    enable_voice_foundation: bool
    enable_voice_input: bool
    enable_voice_output: bool
    voice_stt_backend: str | None
    voice_tts_backend: str | None
    voice_tts_piper_model: str | None = None
    # Optional operator-selected model identifier for the local
    # faster-whisper STT path.  ``None`` preserves the existing
    # default selection (the backend's own ``base`` default, or a
    # ``VOXERA_VOICE_STT_WHISPER_MODEL`` env override).  When set,
    # this is passed through to ``WhisperLocalBackend(model_size=...)``
    # so the operator's choice overrides env defaults deterministically.
    voice_stt_whisper_model: str | None = None
    # Optional operator-selected model identifier for the local
    # Moonshine STT path.  ``None`` preserves the existing default
    # selection (the backend's own ``moonshine/base`` default, or a
    # ``VOXERA_VOICE_STT_MOONSHINE_MODEL`` env override).  When set,
    # this is passed through to ``MoonshineLocalBackend(model_name=...)``
    # so the operator's choice overrides env defaults deterministically.
    voice_stt_moonshine_model: str | None = None
    # Optional operator-configured paths / voice id for the local
    # Kokoro TTS path.  ``None`` preserves the env-only fallback
    # (``VOXERA_VOICE_TTS_KOKORO_*``) so existing deployments do not
    # change behaviour.  When set, these flow through
    # ``KokoroLocalBackend(model_path=..., voices_path=..., voice=...)``
    # so the operator's choice overrides env defaults deterministically.
    voice_tts_kokoro_model: str | None = None
    voice_tts_kokoro_voices: str | None = None
    voice_tts_kokoro_voice: str | None = None

    @property
    def voice_input_enabled(self) -> bool:
        return self.enable_voice_foundation and self.enable_voice_input

    @property
    def voice_output_enabled(self) -> bool:
        return self.enable_voice_foundation and self.enable_voice_output


def _parse_bool(name: str, value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value for {name}: {value!r}")


def _load_runtime_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def load_voice_foundation_flags(
    *,
    config_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> VoiceFoundationFlags:
    env = environ or os.environ
    path = resolve_config_path(config_path)
    file_values = _load_runtime_json(path)

    base_enable_voice_foundation = _parse_bool(
        "enable_voice_foundation", file_values.get("enable_voice_foundation"), default=False
    )
    base_enable_voice_input = _parse_bool(
        "enable_voice_input", file_values.get("enable_voice_input"), default=False
    )
    base_enable_voice_output = _parse_bool(
        "enable_voice_output", file_values.get("enable_voice_output"), default=False
    )

    env_enable_voice_foundation = env.get("VOXERA_ENABLE_VOICE_FOUNDATION")
    env_enable_voice_input = env.get("VOXERA_ENABLE_VOICE_INPUT")
    env_enable_voice_output = env.get("VOXERA_ENABLE_VOICE_OUTPUT")
    env_voice_stt_backend = env.get("VOXERA_VOICE_STT_BACKEND")
    env_voice_tts_backend = env.get("VOXERA_VOICE_TTS_BACKEND")
    env_voice_tts_piper_model = env.get("VOXERA_VOICE_TTS_PIPER_MODEL")
    env_voice_stt_whisper_model = env.get("VOXERA_VOICE_STT_WHISPER_MODEL")
    env_voice_stt_moonshine_model = env.get("VOXERA_VOICE_STT_MOONSHINE_MODEL")
    env_voice_tts_kokoro_model = env.get("VOXERA_VOICE_TTS_KOKORO_MODEL")
    env_voice_tts_kokoro_voices = env.get("VOXERA_VOICE_TTS_KOKORO_VOICES")
    env_voice_tts_kokoro_voice = env.get("VOXERA_VOICE_TTS_KOKORO_VOICE")

    enable_voice_foundation = _parse_bool(
        "VOXERA_ENABLE_VOICE_FOUNDATION",
        env_enable_voice_foundation,
        default=base_enable_voice_foundation,
    )
    enable_voice_input = _parse_bool(
        "VOXERA_ENABLE_VOICE_INPUT",
        env_enable_voice_input,
        default=base_enable_voice_input,
    )
    enable_voice_output = _parse_bool(
        "VOXERA_ENABLE_VOICE_OUTPUT",
        env_enable_voice_output,
        default=base_enable_voice_output,
    )

    file_stt_backend = str(file_values.get("voice_stt_backend") or "").strip() or None
    file_tts_backend = str(file_values.get("voice_tts_backend") or "").strip() or None
    file_tts_piper_model = str(file_values.get("voice_tts_piper_model") or "").strip() or None
    file_stt_whisper_model = str(file_values.get("voice_stt_whisper_model") or "").strip() or None
    file_stt_moonshine_model = (
        str(file_values.get("voice_stt_moonshine_model") or "").strip() or None
    )
    file_tts_kokoro_model = str(file_values.get("voice_tts_kokoro_model") or "").strip() or None
    file_tts_kokoro_voices = str(file_values.get("voice_tts_kokoro_voices") or "").strip() or None
    file_tts_kokoro_voice = str(file_values.get("voice_tts_kokoro_voice") or "").strip() or None

    voice_stt_backend = str(env_voice_stt_backend or "").strip() or file_stt_backend
    voice_tts_backend = str(env_voice_tts_backend or "").strip() or file_tts_backend
    voice_tts_piper_model = str(env_voice_tts_piper_model or "").strip() or file_tts_piper_model
    voice_stt_whisper_model = (
        str(env_voice_stt_whisper_model or "").strip() or file_stt_whisper_model
    )
    voice_stt_moonshine_model = (
        str(env_voice_stt_moonshine_model or "").strip() or file_stt_moonshine_model
    )
    voice_tts_kokoro_model = str(env_voice_tts_kokoro_model or "").strip() or file_tts_kokoro_model
    voice_tts_kokoro_voices = (
        str(env_voice_tts_kokoro_voices or "").strip() or file_tts_kokoro_voices
    )
    voice_tts_kokoro_voice = str(env_voice_tts_kokoro_voice or "").strip() or file_tts_kokoro_voice

    return VoiceFoundationFlags(
        enable_voice_foundation=enable_voice_foundation,
        enable_voice_input=enable_voice_input,
        enable_voice_output=enable_voice_output,
        voice_stt_backend=voice_stt_backend,
        voice_tts_backend=voice_tts_backend,
        voice_tts_piper_model=voice_tts_piper_model,
        voice_stt_whisper_model=voice_stt_whisper_model,
        voice_stt_moonshine_model=voice_stt_moonshine_model,
        voice_tts_kokoro_model=voice_tts_kokoro_model,
        voice_tts_kokoro_voices=voice_tts_kokoro_voices,
        voice_tts_kokoro_voice=voice_tts_kokoro_voice,
    )
