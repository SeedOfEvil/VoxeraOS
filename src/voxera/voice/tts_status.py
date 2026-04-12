"""TTS (text-to-speech) status surface.

Provides an observable, truthful status surface for the TTS subsystem.
This reports *configuration and availability*, not synthesis capability --
actual speech synthesis is not yet implemented.  Status values are designed
to be consumed by doctor checks, health payloads, and (later) panel UI.

Design rules:
- ``available`` is True only when foundation + output are enabled AND a
  backend is configured.  It does NOT imply successful synthesis.
- ``status`` is a human-readable summary label, not a boolean gate.
- ``reason`` explains why the subsystem is not available when it isn't.
- All fields are present in every payload (no optional-as-missing).
"""

from __future__ import annotations

from dataclasses import dataclass

from .flags import VoiceFoundationFlags

# -- canonical status labels ------------------------------------------------

TTS_STATUS_AVAILABLE = "available"
TTS_STATUS_UNCONFIGURED = "unconfigured"
TTS_STATUS_DISABLED = "disabled"
TTS_STATUS_UNAVAILABLE = "unavailable"

# -- schema version ---------------------------------------------------------

TTS_STATUS_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class TTSStatus:
    """Observable TTS status surface.

    Frozen after construction.  Created via :func:`build_tts_status`.
    """

    configured: bool
    available: bool
    enabled: bool
    backend: str | None
    status: str
    reason: str | None
    last_error: str | None
    schema_version: int


def build_tts_status(
    flags: VoiceFoundationFlags,
    *,
    last_error: str | None = None,
) -> TTSStatus:
    """Build a truthful TTS status from the current voice foundation flags.

    The ``available`` field is True only when the voice foundation is enabled,
    voice output is enabled, and a TTS backend is configured.  It does NOT
    imply that synthesis has been tested or will succeed.

    ``last_error`` is an optional passthrough for the most recent TTS-related
    error string (e.g. from health counters).  It is surfaced as-is.
    """
    enabled = flags.enable_voice_foundation and flags.enable_voice_output
    configured = bool(flags.voice_tts_backend)
    available = enabled and configured

    if not flags.enable_voice_foundation:
        status = TTS_STATUS_DISABLED
        reason = "voice_foundation_disabled"
    elif not flags.enable_voice_output:
        status = TTS_STATUS_DISABLED
        reason = "voice_output_disabled"
    elif not configured:
        status = TTS_STATUS_UNCONFIGURED
        reason = "voice_tts_backend_not_configured"
    else:
        status = TTS_STATUS_AVAILABLE
        reason = None

    cleaned_error = str(last_error).strip() if last_error else None

    return TTSStatus(
        configured=configured,
        available=available,
        enabled=enabled,
        backend=flags.voice_tts_backend,
        status=status,
        reason=reason,
        last_error=cleaned_error,
        schema_version=TTS_STATUS_SCHEMA_VERSION,
    )


def tts_status_as_dict(status: TTSStatus) -> dict[str, object]:
    """Serialize a TTSStatus to a plain dict for JSON / health payloads."""
    return {
        "configured": status.configured,
        "available": status.available,
        "enabled": status.enabled,
        "backend": status.backend,
        "status": status.status,
        "reason": status.reason,
        "last_error": status.last_error,
        "schema_version": status.schema_version,
    }
