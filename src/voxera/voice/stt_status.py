"""STT (speech-to-text) status surface.

Provides an observable, truthful status surface for the STT subsystem.
Symmetric with ``tts_status.py``.  Reports configuration and availability
only -- no runtime transcription backend is wired yet.

Design rules mirror TTS:
- ``available`` is True only when foundation + input are enabled AND a
  backend is configured.  It does NOT imply successful transcription.
- ``status`` is a human-readable summary label, not a boolean gate.
- ``reason`` explains why the subsystem is not available when it isn't.
"""

from __future__ import annotations

from dataclasses import dataclass

from .flags import VoiceFoundationFlags

# -- canonical status labels ------------------------------------------------

STT_STATUS_LABEL_AVAILABLE = "available"
STT_STATUS_LABEL_UNCONFIGURED = "unconfigured"
STT_STATUS_LABEL_DISABLED = "disabled"

# -- schema version ---------------------------------------------------------

STT_STATUS_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class STTStatus:
    """Observable STT status surface.

    Frozen after construction.  Created via :func:`build_stt_status`.
    """

    configured: bool
    available: bool
    enabled: bool
    backend: str | None
    status: str
    reason: str | None
    schema_version: int


def build_stt_status(flags: VoiceFoundationFlags) -> STTStatus:
    """Build a truthful STT status from the current voice foundation flags.

    The ``available`` field is True only when the voice foundation is enabled,
    voice input is enabled, and an STT backend is configured.  It does NOT
    imply that transcription has been tested or will succeed.
    """
    enabled = flags.enable_voice_foundation and flags.enable_voice_input
    configured = bool(flags.voice_stt_backend)
    available = enabled and configured

    if not flags.enable_voice_foundation:
        status = STT_STATUS_LABEL_DISABLED
        reason = "voice_foundation_disabled"
    elif not flags.enable_voice_input:
        status = STT_STATUS_LABEL_DISABLED
        reason = "voice_input_disabled"
    elif not configured:
        status = STT_STATUS_LABEL_UNCONFIGURED
        reason = "voice_stt_backend_not_configured"
    else:
        status = STT_STATUS_LABEL_AVAILABLE
        reason = None

    return STTStatus(
        configured=configured,
        available=available,
        enabled=enabled,
        backend=flags.voice_stt_backend,
        status=status,
        reason=reason,
        schema_version=STT_STATUS_SCHEMA_VERSION,
    )


def stt_status_as_dict(status: STTStatus) -> dict[str, object]:
    """Serialize an STTStatus to a plain dict for JSON / health payloads."""
    return {
        "configured": status.configured,
        "available": status.available,
        "enabled": status.enabled,
        "backend": status.backend,
        "status": status.status,
        "reason": status.reason,
        "schema_version": status.schema_version,
    }
