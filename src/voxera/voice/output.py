from __future__ import annotations

from .flags import VoiceFoundationFlags


def voice_output_status(flags: VoiceFoundationFlags) -> dict[str, object]:
    attempted = flags.voice_output_enabled
    configured = bool(flags.voice_tts_backend)
    if not flags.enable_voice_foundation:
        reason = "voice_foundation_disabled"
    elif not flags.enable_voice_output:
        reason = "voice_output_disabled"
    elif not configured:
        reason = "voice_output_backend_missing"
    else:
        reason = "voice_output_placeholder_only"
    return {
        "voice_output_attempted": attempted,
        "voice_output_backend": flags.voice_tts_backend,
        "voice_output_reason": reason,
    }
