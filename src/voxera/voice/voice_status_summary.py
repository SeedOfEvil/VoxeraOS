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
from .moonshine_backend import MOONSHINE_MODEL_BASE
from .stt_backend_factory import STT_BACKEND_MOONSHINE_LOCAL, STT_BACKEND_WHISPER_LOCAL
from .stt_status import build_stt_status, stt_status_as_dict
from .tts_backend_factory import TTS_BACKEND_KOKORO_LOCAL, TTS_BACKEND_PIPER_LOCAL
from .tts_status import build_tts_status, tts_status_as_dict
from .whisper_backend import WHISPER_MODEL_BASE

# -- schema version for the combined summary --------------------------------
# Bumped to 5 in this release: adds ``moonshine_model`` metadata to
# ``stt_dependency`` when the operator selects the Moonshine backend,
# mirroring the existing ``whisper_model`` block so panel + doctor
# can report the operator-selected and effective Moonshine model id
# truthfully alongside the dependency state.
VOICE_STATUS_SUMMARY_SCHEMA_VERSION = 5


# -- next_step hint constants ------------------------------------------------

HINT_RUN_SETUP = "Run `voxera setup` to enable voice and pick backends."
HINT_ENABLE_FOUNDATION = "Run `voxera setup` and answer yes to 'enable voice foundation'."
HINT_ENABLE_STT = "Run `voxera setup` and answer yes to 'enable speech-to-text'."
HINT_ENABLE_TTS = "Run `voxera setup` and answer yes to 'enable text-to-speech'."
HINT_PICK_STT_BACKEND = "Run `voxera setup` and pick an STT backend (e.g. whisper_local)."
HINT_PICK_TTS_BACKEND = "Run `voxera setup` and pick a TTS backend (e.g. piper_local)."
HINT_INSTALL_WHISPER = "Install the Whisper extra: pip install voxera-os[whisper]"
HINT_INSTALL_MOONSHINE = "Install the Moonshine extra: pip install voxera-os[moonshine]"
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
HINT_INSTALL_KOKORO = "Install the Kokoro extra: pip install voxera-os[kokoro]"
HINT_SET_KOKORO_MODEL = (
    "Set the Kokoro model path in panel Voice Options or via "
    "VOXERA_VOICE_TTS_KOKORO_MODEL (absolute path to kokoro-*.onnx)."
)
HINT_SET_KOKORO_VOICES = (
    "Set the Kokoro voices path in panel Voice Options or via "
    "VOXERA_VOICE_TTS_KOKORO_VOICES (absolute path to voices-*.bin)."
)
HINT_KOKORO_MODEL_FILE_MISSING = (
    "Configured Kokoro model path does not exist. "
    "Point voice_tts_kokoro_model at a valid kokoro-*.onnx file."
)
HINT_KOKORO_VOICES_FILE_MISSING = (
    "Configured Kokoro voices path does not exist. "
    "Point voice_tts_kokoro_voices at a valid voices-*.bin file."
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


def _describe_whisper_model(model: str | None) -> dict[str, Any]:
    """Describe the operator-selected whisper model for status surfaces.

    Returns a small dict that truthfully reports whether the operator
    pinned a specific whisper model via runtime config / env (``selected``)
    and the effective model id the backend will use (``effective``).
    When no explicit selection is made, the default backend model id
    (:data:`voxera.voice.whisper_backend.WHISPER_MODEL_BASE`) is
    reported as the effective value and ``selected`` is ``None`` so
    operators can see at a glance that the default is in play.
    """
    value = (model or "").strip() or None
    return {
        "selected": value,
        "effective": value or WHISPER_MODEL_BASE,
    }


def _describe_moonshine_model(model: str | None) -> dict[str, Any]:
    """Describe the operator-selected Moonshine model for status surfaces.

    Mirrors :func:`_describe_whisper_model` in shape so operator UIs
    can render the block the same way: ``selected`` reports whether
    the operator pinned a specific id and ``effective`` reports what
    the backend will actually load (defaulting to
    ``moonshine/base``).
    """
    value = (model or "").strip() or None
    return {
        "selected": value,
        "effective": value or MOONSHINE_MODEL_BASE,
    }


def _check_stt_dependency(
    backend: str | None,
    *,
    whisper_model: str | None = None,
    moonshine_model: str | None = None,
) -> dict[str, Any]:
    """Check whether the configured STT backend's dependency is available.

    For ``whisper_local`` the result also carries a ``whisper_model``
    sub-dict describing the operator-selected model id (if any) and
    the effective model id the backend will load.  This lets operator
    UIs and doctor output show the chosen model alongside the dependency
    state without reaching past the flags.

    For ``moonshine_local`` the result similarly carries a
    ``moonshine_model`` sub-dict and reports the ``moonshine-onnx``
    package as the dependency probe.  The fallback ``moonshine``
    (PyTorch) package is considered equivalent for "installed"
    reporting so an operator who picked one variant sees a truthful
    "installed" signal.
    """
    if not backend:
        return {"checked": False, "reason": "no_backend_configured"}

    backend_lower = backend.strip().lower()
    if backend_lower == STT_BACKEND_WHISPER_LOCAL:
        whisper_model_info = _describe_whisper_model(whisper_model)
        try:
            import faster_whisper  # noqa: F401

            return {
                "checked": True,
                "available": True,
                "package": "faster-whisper",
                "whisper_model": whisper_model_info,
            }
        except (ImportError, OSError):
            return {
                "checked": True,
                "available": False,
                "package": "faster-whisper",
                "hint": HINT_INSTALL_WHISPER,
                "whisper_model": whisper_model_info,
            }

    if backend_lower == STT_BACKEND_MOONSHINE_LOCAL:
        moonshine_model_info = _describe_moonshine_model(moonshine_model)
        available, package = _probe_moonshine_package()
        if available:
            return {
                "checked": True,
                "available": True,
                "package": package,
                "moonshine_model": moonshine_model_info,
            }
        return {
            "checked": True,
            "available": False,
            "package": package,
            "hint": HINT_INSTALL_MOONSHINE,
            "moonshine_model": moonshine_model_info,
        }

    return {"checked": False, "reason": f"unknown_backend:{backend}"}


def _probe_moonshine_package() -> tuple[bool, str]:
    """Return (available, package_label) for the Moonshine dependency.

    Prefers the ONNX variant (``moonshine_onnx``) because it is the
    CPU-first option VoxeraOS recommends.  Falls back to the PyTorch
    variant (``moonshine``) purely for availability reporting so an
    operator who already installed that variant does not see a
    misleading "missing" signal.  The dependency-missing hint always
    points at the ``[moonshine]`` extra, which pulls ``moonshine-onnx``.
    """
    try:
        import moonshine_onnx  # noqa: F401

        return True, "moonshine-onnx"
    except (ImportError, OSError):
        pass
    try:
        import moonshine  # noqa: F401

        return True, "moonshine"
    except (ImportError, OSError):
        return False, "moonshine-onnx"


def _check_kokoro_model(
    model_path: str | None,
    voices_path: str | None,
    voice: str | None,
) -> dict[str, Any]:
    """Inspect the configured Kokoro model / voices / voice settings.

    Returns a dict describing whether the operator configured paths,
    whether each path exists on disk, and the effective voice id
    (operator-selected or Kokoro's default ``af_sarah``).  Never
    raises; never touches the network; never loads the model.

    The shape mirrors ``_check_piper_model`` in spirit so operator
    UIs can render the block the same way.
    """
    model_raw = (model_path or "").strip() or None
    voices_raw = (voices_path or "").strip() or None
    voice_raw = (voice or "").strip() or None

    info: dict[str, Any] = {
        "configured": bool(model_raw and voices_raw),
        "model_path": str(Path(model_raw).expanduser()) if model_raw else None,
        "voices_path": str(Path(voices_raw).expanduser()) if voices_raw else None,
        "model_exists": (Path(model_raw).expanduser().exists() if model_raw else False),
        "voices_exists": (Path(voices_raw).expanduser().exists() if voices_raw else False),
        "voice": voice_raw,
        "effective_voice": voice_raw or "af_sarah",
    }
    if not model_raw:
        info["hint"] = HINT_SET_KOKORO_MODEL
    elif not voices_raw:
        info["hint"] = HINT_SET_KOKORO_VOICES
    elif not info["model_exists"]:
        info["hint"] = HINT_KOKORO_MODEL_FILE_MISSING
    elif not info["voices_exists"]:
        info["hint"] = HINT_KOKORO_VOICES_FILE_MISSING
    return info


def _check_tts_dependency(
    backend: str | None,
    *,
    piper_model: str | None = None,
    kokoro_model: str | None = None,
    kokoro_voices: str | None = None,
    kokoro_voice: str | None = None,
) -> dict[str, Any]:
    """Check whether the configured TTS backend's dependency is available.

    For ``piper_local`` the result also carries a ``piper_model`` sub-dict
    describing the configured model path/name (if any) so operator UIs can
    show a specific next step when the dependency is installed but the
    model is missing.

    For ``kokoro_local`` the result carries a ``kokoro_model`` sub-dict
    describing the configured model / voices paths and the effective
    voice id.  The dependency package is ``kokoro-onnx``.
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

    if backend_lower == TTS_BACKEND_KOKORO_LOCAL:
        kokoro_model_info = _check_kokoro_model(kokoro_model, kokoro_voices, kokoro_voice)
        try:
            import kokoro_onnx  # noqa: F401

            return {
                "checked": True,
                "available": True,
                "package": "kokoro-onnx",
                "kokoro_model": kokoro_model_info,
            }
        except (ImportError, OSError):
            return {
                "checked": True,
                "available": False,
                "package": "kokoro-onnx",
                "hint": HINT_INSTALL_KOKORO,
                "kokoro_model": kokoro_model_info,
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
        if hint:
            return str(hint)
        # Fallback default — pick the hint that matches the selected
        # backend rather than always pointing at the Whisper extra,
        # so operators who chose Moonshine see the right install
        # command.
        backend_lower = (flags.voice_stt_backend or "").strip().lower()
        if backend_lower == STT_BACKEND_MOONSHINE_LOCAL:
            return HINT_INSTALL_MOONSHINE
        return HINT_INSTALL_WHISPER
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
    # Kokoro dep installed but model / voices paths are missing or
    # point at non-existent files -- the helper already composed the
    # specific hint string, so just pass it through.
    kokoro_model = tts_dep.get("kokoro_model")
    if isinstance(kokoro_model, dict):
        kokoro_hint = kokoro_model.get("hint")
        if kokoro_hint:
            return str(kokoro_hint)
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

    stt_dep = _check_stt_dependency(
        flags.voice_stt_backend,
        whisper_model=flags.voice_stt_whisper_model,
        moonshine_model=flags.voice_stt_moonshine_model,
    )
    tts_dep = _check_tts_dependency(
        flags.voice_tts_backend,
        piper_model=flags.voice_tts_piper_model,
        kokoro_model=flags.voice_tts_kokoro_model,
        kokoro_voices=flags.voice_tts_kokoro_voices,
        kokoro_voice=flags.voice_tts_kokoro_voice,
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
