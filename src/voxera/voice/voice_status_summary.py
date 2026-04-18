"""Combined voice status summary for operator-facing surfaces.

Builds a single truthful status payload covering both STT and TTS
subsystems.  Reuses the existing ``build_stt_status`` and
``build_tts_status`` surfaces and adds dependency availability checks
for the configured backends.

The summary also carries concrete operator-facing ``next_step`` hints
per subsystem so UIs and ``voxera doctor`` can tell a brand-new user
what to do next without tribal knowledge.  These hints describe the
first missing precondition, not every possible improvement.

This module is read-only and diagnostic -- it never triggers model
loading, synthesis, or transcription.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .flags import VoiceFoundationFlags
from .stt_backend_factory import STT_BACKEND_WHISPER_LOCAL
from .stt_status import build_stt_status, stt_status_as_dict
from .tts_backend_factory import TTS_BACKEND_PIPER_LOCAL
from .tts_status import build_tts_status, tts_status_as_dict

# -- schema version for the combined summary --------------------------------
# Bumped to 2 in this release: adds ``next_step`` hints per subsystem and
# ``piper_model`` metadata to ``tts_dependency``.
VOICE_STATUS_SUMMARY_SCHEMA_VERSION = 2


# -- next_step hint constants ------------------------------------------------

HINT_RUN_SETUP = "Run `voxera setup` to enable voice and pick backends."
HINT_ENABLE_FOUNDATION = "Run `voxera setup` and answer yes to 'enable voice foundation'."
HINT_ENABLE_STT = "Run `voxera setup` and answer yes to 'enable speech-to-text'."
HINT_ENABLE_TTS = "Run `voxera setup` and answer yes to 'enable text-to-speech'."
HINT_PICK_STT_BACKEND = "Run `voxera setup` and pick an STT backend (e.g. whisper_local)."
HINT_PICK_TTS_BACKEND = "Run `voxera setup` and pick a TTS backend (e.g. piper_local)."
HINT_INSTALL_WHISPER = "Install the Whisper extra: pip install voxera-os[whisper]"
HINT_INSTALL_PIPER = "Install the Piper extra: pip install voxera-os[piper]"
HINT_SET_PIPER_MODEL = (
    "Set a Piper model path or name via `voxera setup` "
    "(stored in ~/.config/voxera/config.json as voice_tts_piper_model)."
)
HINT_PIPER_MODEL_FILE_MISSING = (
    "Configured Piper model path does not exist. "
    "Re-run `voxera setup` and provide a valid .onnx path, or clear it to use the default."
)
HINT_PIPER_MODEL_METADATA_MISSING = (
    "Piper model .onnx file exists but the matching .onnx.json metadata file is missing "
    "next to it.  Download the metadata sidecar or re-run `voxera setup`."
)


# -- Piper model path validation --------------------------------------------


def _looks_like_path(value: str) -> bool:
    """Rough heuristic: path-like strings contain a path separator or end with .onnx."""
    return ("/" in value) or value.endswith(".onnx")


def _check_piper_model(model: str | None) -> dict[str, Any]:
    """Inspect the configured Piper model setting.

    Returns a dict describing whether the configured value is a path
    (and whether it exists) or a model name (deferred to lazy load).
    Never raises; never touches the network; never loads the model.
    """
    if not model:
        return {"configured": False, "reason": "no_model_configured"}

    if not _looks_like_path(model):
        # Treat as a named model — Piper resolves it at load time.
        return {
            "configured": True,
            "kind": "name",
            "value": model,
        }

    model_path = Path(model).expanduser()
    metadata_path = Path(str(model_path) + ".json")
    exists = model_path.exists()
    metadata_exists = metadata_path.exists() if exists else False
    info: dict[str, Any] = {
        "configured": True,
        "kind": "path",
        "value": str(model_path),
        "exists": exists,
        "metadata_path": str(metadata_path),
        "metadata_exists": metadata_exists,
    }
    if not exists:
        info["hint"] = HINT_PIPER_MODEL_FILE_MISSING
    elif not metadata_exists:
        info["hint"] = HINT_PIPER_MODEL_METADATA_MISSING
    return info


# -- dependency checks ------------------------------------------------------


def _check_stt_dependency(backend: str | None) -> dict[str, Any]:
    """Check whether the configured STT backend's dependency is available."""
    if not backend:
        return {"checked": False, "reason": "no_backend_configured"}

    backend_lower = backend.strip().lower()
    if backend_lower == STT_BACKEND_WHISPER_LOCAL:
        try:
            import faster_whisper  # noqa: F401

            return {"checked": True, "available": True, "package": "faster-whisper"}
        except (ImportError, OSError):
            return {
                "checked": True,
                "available": False,
                "package": "faster-whisper",
                "hint": HINT_INSTALL_WHISPER,
            }

    return {"checked": False, "reason": f"unknown_backend:{backend}"}


def _check_tts_dependency(
    backend: str | None,
    *,
    piper_model: str | None = None,
) -> dict[str, Any]:
    """Check whether the configured TTS backend's dependency is available.

    For ``piper_local`` the result also carries a ``piper_model`` sub-dict
    describing the configured model path/name (if any) so operator UIs can
    show a specific next step when the dependency is installed but the
    model is missing.
    """
    if not backend:
        return {"checked": False, "reason": "no_backend_configured"}

    backend_lower = backend.strip().lower()
    if backend_lower == TTS_BACKEND_PIPER_LOCAL:
        piper_model_info = _check_piper_model(piper_model)
        try:
            import piper  # noqa: F401

            return {
                "checked": True,
                "available": True,
                "package": "piper-tts",
                "piper_model": piper_model_info,
            }
        except (ImportError, OSError):
            return {
                "checked": True,
                "available": False,
                "package": "piper-tts",
                "hint": HINT_INSTALL_PIPER,
                "piper_model": piper_model_info,
            }

    return {"checked": False, "reason": f"unknown_backend:{backend}"}


# -- next_step hint derivation ----------------------------------------------


def _stt_next_step(
    flags: VoiceFoundationFlags,
    stt_dep: dict[str, Any],
) -> str | None:
    if not flags.enable_voice_foundation:
        return HINT_ENABLE_FOUNDATION
    if not flags.enable_voice_input:
        return HINT_ENABLE_STT
    if not flags.voice_stt_backend:
        return HINT_PICK_STT_BACKEND
    if stt_dep.get("checked") and stt_dep.get("available") is False:
        hint = stt_dep.get("hint")
        return str(hint) if hint else HINT_INSTALL_WHISPER
    return None


def _tts_next_step(
    flags: VoiceFoundationFlags,
    tts_dep: dict[str, Any],
) -> str | None:
    if not flags.enable_voice_foundation:
        return HINT_ENABLE_FOUNDATION
    if not flags.enable_voice_output:
        return HINT_ENABLE_TTS
    if not flags.voice_tts_backend:
        return HINT_PICK_TTS_BACKEND
    if tts_dep.get("checked") and tts_dep.get("available") is False:
        hint = tts_dep.get("hint")
        return str(hint) if hint else HINT_INSTALL_PIPER
    # Piper dep installed but model path is invalid?
    piper_model = tts_dep.get("piper_model")
    if isinstance(piper_model, dict) and piper_model.get("kind") == "path":
        if not piper_model.get("exists"):
            return HINT_PIPER_MODEL_FILE_MISSING
        if not piper_model.get("metadata_exists"):
            return HINT_PIPER_MODEL_METADATA_MISSING
    return None


# -- public builder ---------------------------------------------------------


def build_voice_status_summary(
    flags: VoiceFoundationFlags,
    *,
    last_tts_error: str | None = None,
) -> dict[str, Any]:
    """Build a combined voice status summary from the current flags.

    Returns a plain dict suitable for JSON serialization.  The payload
    is truthful: it never implies readiness when something is disabled,
    misconfigured, or missing a dependency.  Each subsystem carries a
    ``next_step`` hint describing the first missing precondition, or
    ``None`` when the subsystem is fully configured and its dependency
    is available.
    """
    stt = build_stt_status(flags)
    tts = build_tts_status(flags, last_error=last_tts_error)

    stt_dep = _check_stt_dependency(flags.voice_stt_backend)
    tts_dep = _check_tts_dependency(
        flags.voice_tts_backend, piper_model=flags.voice_tts_piper_model
    )

    stt_payload = stt_status_as_dict(stt)
    stt_payload["next_step"] = _stt_next_step(flags, stt_dep)

    tts_payload = tts_status_as_dict(tts)
    tts_payload["next_step"] = _tts_next_step(flags, tts_dep)

    foundation_next_step: str | None = None
    if not flags.enable_voice_foundation:
        foundation_next_step = HINT_RUN_SETUP

    return {
        "voice_foundation_enabled": flags.enable_voice_foundation,
        "voice_foundation_next_step": foundation_next_step,
        "stt": stt_payload,
        "stt_dependency": stt_dep,
        "tts": tts_payload,
        "tts_dependency": tts_dep,
        "schema_version": VOICE_STATUS_SUMMARY_SCHEMA_VERSION,
    }
