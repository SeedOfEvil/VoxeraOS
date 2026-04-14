"""Combined voice status summary for operator-facing surfaces.

Builds a single truthful status payload covering both STT and TTS
subsystems.  Reuses the existing ``build_stt_status`` and
``build_tts_status`` surfaces and adds dependency availability checks
for the configured backends.

This module is read-only and diagnostic -- it never triggers model
loading, synthesis, or transcription.
"""

from __future__ import annotations

from typing import Any

from .flags import VoiceFoundationFlags
from .stt_backend_factory import STT_BACKEND_WHISPER_LOCAL
from .stt_status import build_stt_status, stt_status_as_dict
from .tts_backend_factory import TTS_BACKEND_PIPER_LOCAL
from .tts_status import build_tts_status, tts_status_as_dict

# -- schema version for the combined summary --------------------------------

VOICE_STATUS_SUMMARY_SCHEMA_VERSION = 1


def _check_stt_dependency(backend: str | None) -> dict[str, Any]:
    """Check whether the configured STT backend's dependency is available."""
    if not backend:
        return {"checked": False, "reason": "no_backend_configured"}

    backend_lower = backend.strip().lower()
    if backend_lower == STT_BACKEND_WHISPER_LOCAL:
        try:
            import faster_whisper  # noqa: F401

            return {"checked": True, "available": True, "package": "faster-whisper"}
        except ModuleNotFoundError:
            return {
                "checked": True,
                "available": False,
                "package": "faster-whisper",
                "hint": "Install with: pip install voxera-os[whisper]",
            }

    return {"checked": False, "reason": f"unknown_backend:{backend}"}


def _check_tts_dependency(backend: str | None) -> dict[str, Any]:
    """Check whether the configured TTS backend's dependency is available."""
    if not backend:
        return {"checked": False, "reason": "no_backend_configured"}

    backend_lower = backend.strip().lower()
    if backend_lower == TTS_BACKEND_PIPER_LOCAL:
        try:
            import piper  # noqa: F401

            return {"checked": True, "available": True, "package": "piper-tts"}
        except ModuleNotFoundError:
            return {
                "checked": True,
                "available": False,
                "package": "piper-tts",
                "hint": "Install with: pip install voxera-os[piper]",
            }

    return {"checked": False, "reason": f"unknown_backend:{backend}"}


def build_voice_status_summary(
    flags: VoiceFoundationFlags,
    *,
    last_tts_error: str | None = None,
) -> dict[str, Any]:
    """Build a combined voice status summary from the current flags.

    Returns a plain dict suitable for JSON serialization.  The payload
    is truthful: it never implies readiness when something is disabled,
    misconfigured, or missing a dependency.
    """
    stt = build_stt_status(flags)
    tts = build_tts_status(flags, last_error=last_tts_error)

    return {
        "voice_foundation_enabled": flags.enable_voice_foundation,
        "stt": stt_status_as_dict(stt),
        "stt_dependency": _check_stt_dependency(flags.voice_stt_backend),
        "tts": tts_status_as_dict(tts),
        "tts_dependency": _check_tts_dependency(flags.voice_tts_backend),
        "schema_version": VOICE_STATUS_SUMMARY_SCHEMA_VERSION,
    }
