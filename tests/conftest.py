from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def _sanitize_voxera_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("VOXERA_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("VOXERA_LOAD_DOTENV", "0")


@pytest.fixture(autouse=True)
def _provide_default_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _sanitize_voxera_env: None,
) -> None:
    """Ensure a config.yml exists so the first-run config guard passes.

    Depends on ``_sanitize_voxera_env`` (via the parameter) to guarantee
    env vars are cleaned before we set the config path — otherwise the
    sanitizer could clear state we depend on.

    Tests that explicitly verify the missing-config behavior override
    ``default_config_path`` themselves via monkeypatch.
    """
    cfg = tmp_path / "config.yml"
    cfg.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr("voxera.config.default_config_path", lambda: cfg)


@pytest.fixture(autouse=True)
def _reset_shared_voice_backends() -> None:
    """Drop any process-wide shared STT/TTS backend between tests.

    The shared-backend caches (``voxera.voice.stt_backend_factory``
    and ``voxera.voice.tts_backend_factory``) keep a single adapter
    instance alive per process so the Whisper / Piper model load cost
    is paid once per dictation session rather than once per turn.
    Between tests this caching must be reset — otherwise a patched
    backend from one test would leak into the next as a cached
    instance.
    """
    from voxera.voice.stt_backend_factory import reset_shared_stt_backend
    from voxera.voice.tts_backend_factory import reset_shared_tts_backend

    reset_shared_stt_backend()
    reset_shared_tts_backend()
    yield
    reset_shared_stt_backend()
    reset_shared_tts_backend()


@pytest.fixture(autouse=True)
def _isolate_health_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _sanitize_voxera_env: None,
) -> None:
    """Redirect all health snapshot writes to a per-test temp file.

    Sets ``VOXERA_HEALTH_PATH`` so that ``read_health_snapshot`` /
    ``write_health_snapshot`` (and every helper that calls them) target a
    throwaway file inside ``tmp_path`` instead of the real operator snapshot
    at ``notes/queue/health.json``.  The file is seeded with a normalised
    empty snapshot so tests that only read get sensible defaults.

    Depends on ``_sanitize_voxera_env`` to guarantee that fixture runs first
    so ``VOXERA_HEALTH_PATH`` is not cleared after being set here.
    """
    health_file = tmp_path / "health.json"
    monkeypatch.setenv("VOXERA_HEALTH_PATH", str(health_file))
    from voxera.health import write_health_snapshot

    write_health_snapshot(tmp_path, {})
