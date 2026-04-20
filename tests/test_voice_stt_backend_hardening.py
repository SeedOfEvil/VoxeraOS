"""Hardening tests for the operator-selectable STT backend.

Sibling to ``test_voice_stt_backend_selection.py`` but focused on
boundary / invariant pins that a skeptical reviewer would want held:

1. Shared-backend cache key invalidates when the operator switches
   backends (no stale Moonshine instance returned after flip to
   Whisper, and vice versa)
2. End-to-end transcription through the canonical
   ``transcribe_audio_file`` entry point flows Moonshine correctly
   (not just the raw backend) so dictation / workbench / panel
   surfaces inherit the same behaviour
3. Missing-dependency failure is truthful through the high-level
   entry point too (``unavailable`` with ``backend_missing``)
4. Panel GET ``/voice/status`` after a save pre-selects the
   persisted backend in the dropdown so the operator can see what
   will apply on the next run
5. Doctor ``elif`` branch: when Whisper is selected, the STT
   detail row carries the Whisper model (not accidentally also a
   Moonshine model if both keys were ever present)

These tests never install the real Moonshine / faster-whisper
packages; seams are patched at the module boundary so behaviour is
deterministic regardless of host env.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from voxera import config as _voxera_config
from voxera.doctor import run_quick_doctor
from voxera.panel import app as panel_module
from voxera.voice.flags import VoiceFoundationFlags
from voxera.voice.input import transcribe_audio_file
from voxera.voice.moonshine_backend import (
    MOONSHINE_MODEL_BASE,
    MOONSHINE_MODEL_TINY,
    MoonshineLocalBackend,
)
from voxera.voice.stt_backend_factory import (
    STT_BACKEND_MOONSHINE_LOCAL,
    STT_BACKEND_WHISPER_LOCAL,
    get_shared_stt_backend,
    reset_shared_stt_backend,
)
from voxera.voice.stt_protocol import (
    STT_ERROR_BACKEND_MISSING,
    STT_STATUS_SUCCEEDED,
    STT_STATUS_UNAVAILABLE,
)
from voxera.voice.whisper_backend import (
    WHISPER_MODEL_DISTIL_LARGE_V3,
    WhisperLocalBackend,
)


def _flags(
    *,
    stt_backend: str | None,
    moonshine_model: str | None = None,
    whisper_model: str | None = None,
) -> VoiceFoundationFlags:
    return VoiceFoundationFlags(
        enable_voice_foundation=True,
        enable_voice_input=True,
        enable_voice_output=False,
        voice_stt_backend=stt_backend,
        voice_tts_backend=None,
        voice_stt_whisper_model=whisper_model,
        voice_stt_moonshine_model=moonshine_model,
    )


def _operator_headers(user: str = "admin", password: str = "secret") -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _authed_csrf_request(
    client: TestClient,
    method: str,
    url: str,
    *,
    data: dict[str, str],
):
    auth = _operator_headers()
    home = client.get("/", headers=auth)
    assert home.status_code == 200
    csrf = client.cookies.get("voxera_panel_csrf")
    payload = dict(data)
    payload["csrf_token"] = csrf or ""
    return getattr(client, method)(url, data=payload, headers=auth, follow_redirects=False)


@pytest.fixture()
def _panel_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake_home = tmp_path / "home"
    queue_dir = fake_home / "VoxeraOS" / "notes" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(panel_module.Path, "home", lambda: fake_home)
    monkeypatch.setenv("VOXERA_PANEL_OPERATOR_PASSWORD", "secret")
    cfg_path = tmp_path / "voxera_config.json"
    monkeypatch.setattr(_voxera_config, "_DEFAULT_RUNTIME_CONFIG", cfg_path)
    reset_shared_stt_backend()
    return cfg_path


# -- 1. shared-backend cache invalidation ----------------------------------


class TestSharedBackendCacheInvalidation:
    def setup_method(self) -> None:
        reset_shared_stt_backend()

    def teardown_method(self) -> None:
        reset_shared_stt_backend()

    def test_flip_whisper_to_moonshine_rebuilds(self) -> None:
        w = _flags(stt_backend=STT_BACKEND_WHISPER_LOCAL)
        m = _flags(stt_backend=STT_BACKEND_MOONSHINE_LOCAL)

        a = get_shared_stt_backend(w)
        assert isinstance(a, WhisperLocalBackend)
        b = get_shared_stt_backend(m)
        assert isinstance(b, MoonshineLocalBackend)
        assert a is not b

    def test_flip_moonshine_to_whisper_rebuilds(self) -> None:
        m = _flags(stt_backend=STT_BACKEND_MOONSHINE_LOCAL)
        w = _flags(stt_backend=STT_BACKEND_WHISPER_LOCAL)

        a = get_shared_stt_backend(m)
        assert isinstance(a, MoonshineLocalBackend)
        b = get_shared_stt_backend(w)
        assert isinstance(b, WhisperLocalBackend)
        assert a is not b

    def test_same_flags_returns_cached(self) -> None:
        m = _flags(stt_backend=STT_BACKEND_MOONSHINE_LOCAL)
        a = get_shared_stt_backend(m)
        b = get_shared_stt_backend(m)
        assert a is b

    def test_moonshine_model_change_invalidates(self) -> None:
        base = _flags(stt_backend=STT_BACKEND_MOONSHINE_LOCAL, moonshine_model=MOONSHINE_MODEL_BASE)
        tiny = _flags(stt_backend=STT_BACKEND_MOONSHINE_LOCAL, moonshine_model=MOONSHINE_MODEL_TINY)
        a = get_shared_stt_backend(base)
        b = get_shared_stt_backend(tiny)
        assert a is not b
        assert isinstance(a, MoonshineLocalBackend)
        assert a._model_name == MOONSHINE_MODEL_BASE
        assert isinstance(b, MoonshineLocalBackend)
        assert b._model_name == MOONSHINE_MODEL_TINY

    def test_whisper_model_change_does_not_leak_into_moonshine_cache(self) -> None:
        """Whisper-model change must NOT make a subsequent Moonshine
        request reuse a Whisper instance."""
        w_small = _flags(stt_backend=STT_BACKEND_WHISPER_LOCAL, whisper_model="small")
        m = _flags(stt_backend=STT_BACKEND_MOONSHINE_LOCAL)

        a = get_shared_stt_backend(w_small)
        assert isinstance(a, WhisperLocalBackend)
        b = get_shared_stt_backend(m)
        assert isinstance(b, MoonshineLocalBackend)

    def test_foundation_disabled_returns_null_and_separate_cache(self) -> None:
        """Disabling the foundation invalidates the cache truthfully."""
        from voxera.voice.stt_adapter import NullSTTBackend

        m = _flags(stt_backend=STT_BACKEND_MOONSHINE_LOCAL)
        off = VoiceFoundationFlags(
            enable_voice_foundation=False,
            enable_voice_input=False,
            enable_voice_output=False,
            voice_stt_backend=STT_BACKEND_MOONSHINE_LOCAL,
            voice_tts_backend=None,
        )
        a = get_shared_stt_backend(m)
        b = get_shared_stt_backend(off)
        assert isinstance(a, MoonshineLocalBackend)
        assert isinstance(b, NullSTTBackend)
        assert a is not b


# -- 2. end-to-end entry point with moonshine flags ------------------------


class TestTranscribeAudioFileEntryPoint:
    def setup_method(self) -> None:
        reset_shared_stt_backend()

    def teardown_method(self) -> None:
        reset_shared_stt_backend()

    def test_moonshine_success_through_transcribe_audio_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF....stub")

        backend = MoonshineLocalBackend()
        # Inject a fake ``Transcriber`` (non-streaming API) so we don't
        # touch the real model.  ``load_wav_file`` is patched on the
        # upstream module so the backend's deferred-import resolves to
        # a deterministic (audio_data, sample_rate) tuple.
        backend._transcriber = SimpleNamespace(
            transcribe_without_streaming=MagicMock(
                return_value=SimpleNamespace(lines=[SimpleNamespace(text="hello from entry point")])
            )
        )
        monkeypatch.setattr(
            "moonshine_voice.transcriber.load_wav_file",
            MagicMock(return_value=([0.0] * 16000, 16000)),
            raising=False,
        )

        flags = _flags(stt_backend=STT_BACKEND_MOONSHINE_LOCAL)

        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True):
            resp = transcribe_audio_file(audio_path=str(audio), flags=flags, backend=backend)

        assert resp.status == STT_STATUS_SUCCEEDED
        assert resp.transcript == "hello from entry point"
        assert resp.backend == "moonshine_local"

    def test_moonshine_missing_dependency_through_entry_point(self, tmp_path: Path) -> None:
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"fake")

        backend = MoonshineLocalBackend()
        flags = _flags(stt_backend=STT_BACKEND_MOONSHINE_LOCAL)

        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", False):
            resp = transcribe_audio_file(audio_path=str(audio), flags=flags, backend=backend)

        # Unavailable (subsystem cannot service the request), not failed —
        # this is the same truthful mapping the whisper backend uses, and
        # downstream surfaces (panel, dictation, workbench) rely on the
        # unavailable/failed distinction for their banners.
        assert resp.status == STT_STATUS_UNAVAILABLE
        assert resp.error_class == STT_ERROR_BACKEND_MISSING
        assert resp.backend == "moonshine_local"

    def test_moonshine_shared_backend_used_when_none_passed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without an explicit backend arg, the entry point must use the
        shared moonshine instance — not Whisper, not Null."""
        from types import SimpleNamespace

        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF....stub")

        flags = _flags(stt_backend=STT_BACKEND_MOONSHINE_LOCAL)

        captured: dict[str, object] = {}

        def fake_ensure(self):
            captured["called_on"] = type(self).__name__
            return SimpleNamespace(
                transcribe_without_streaming=MagicMock(
                    return_value=SimpleNamespace(lines=[SimpleNamespace(text="wired up")])
                )
            )

        monkeypatch.setattr(MoonshineLocalBackend, "_ensure_transcriber", fake_ensure)
        monkeypatch.setattr(
            "moonshine_voice.transcriber.load_wav_file",
            MagicMock(return_value=([0.0] * 16000, 16000)),
            raising=False,
        )
        with patch("voxera.voice.moonshine_backend._MOONSHINE_AVAILABLE", True):
            resp = transcribe_audio_file(audio_path=str(audio), flags=flags)

        assert resp.status == STT_STATUS_SUCCEEDED
        assert resp.backend == "moonshine_local"
        assert captured["called_on"] == "MoonshineLocalBackend"


# -- 3. panel pre-selects the persisted backend in the dropdown ------------


class TestPanelPreSelectsSavedBackend:
    def test_saved_moonshine_backend_is_pre_selected(self, _panel_env: Path) -> None:
        client = TestClient(panel_module.app)
        save = _authed_csrf_request(
            client,
            "post",
            "/voice/options/save",
            data={"stt_backend": STT_BACKEND_MOONSHINE_LOCAL},
        )
        assert save.status_code == 200

        # Re-open /voice/status and confirm the <option> for moonshine_local
        # carries ``selected`` — i.e. the dropdown reflects the saved state.
        page = client.get("/voice/status", headers=_operator_headers())
        assert page.status_code == 200
        # The panel renders: <option value="moonshine_local" selected>moonshine_local</option>
        assert f'value="{STT_BACKEND_MOONSHINE_LOCAL}" selected' in page.text
        # And whisper_local is NOT the selected row.
        assert f'value="{STT_BACKEND_WHISPER_LOCAL}" selected' not in page.text

    def test_saved_moonshine_model_is_pre_selected(self, _panel_env: Path) -> None:
        client = TestClient(panel_module.app)
        _authed_csrf_request(
            client,
            "post",
            "/voice/options/save",
            data={
                "stt_backend": STT_BACKEND_MOONSHINE_LOCAL,
                "stt_moonshine_model": MOONSHINE_MODEL_TINY,
            },
        )
        page = client.get("/voice/status", headers=_operator_headers())
        assert page.status_code == 200
        assert f'value="{MOONSHINE_MODEL_TINY}" selected' in page.text

    def test_save_then_reload_persists_through_fresh_client(self, _panel_env: Path) -> None:
        """A second TestClient reading the same config path must see the
        saved selection — i.e. persistence is operator-file, not in-memory."""
        c1 = TestClient(panel_module.app)
        _authed_csrf_request(
            c1,
            "post",
            "/voice/options/save",
            data={"stt_backend": STT_BACKEND_MOONSHINE_LOCAL},
        )

        c2 = TestClient(panel_module.app)
        page = c2.get("/voice/status", headers=_operator_headers())
        assert page.status_code == 200
        assert f'value="{STT_BACKEND_MOONSHINE_LOCAL}" selected' in page.text

        payload = json.loads(_panel_env.read_text(encoding="utf-8"))
        assert payload["voice_stt_backend"] == STT_BACKEND_MOONSHINE_LOCAL


# -- 4. doctor elif regression: whisper still shows its model alone ---------


class TestDoctorBackendRegression:
    def test_doctor_whisper_detail_shows_whisper_model_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After the hardening pass the doctor detail builder uses elif
        between the whisper_model and moonshine_model blocks.  Whisper-
        configured operators must still see their model in the line, and
        must NOT see a spurious Moonshine model string."""
        cfg = tmp_path / "voxera_config.json"
        cfg.write_text(
            json.dumps(
                {
                    "enable_voice_foundation": True,
                    "enable_voice_input": True,
                    "voice_stt_backend": STT_BACKEND_WHISPER_LOCAL,
                    "voice_stt_whisper_model": WHISPER_MODEL_DISTIL_LARGE_V3,
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(_voxera_config, "_DEFAULT_RUNTIME_CONFIG", cfg)
        queue_root = tmp_path / "queue"
        (queue_root / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
        (queue_root / "health.json").write_text(
            json.dumps({"last_ok_event": "daemon_tick", "last_ok_ts_ms": 100_000}),
            encoding="utf-8",
        )
        checks = run_quick_doctor(queue_root=queue_root)
        stt = next(c for c in checks if c["check"] == "voice: stt status")
        assert f"backend={STT_BACKEND_WHISPER_LOCAL}" in stt["detail"]
        assert f"model={WHISPER_MODEL_DISTIL_LARGE_V3}" in stt["detail"]
        # Only one model= token — no accidental Moonshine leakage.
        assert stt["detail"].count("model=") == 1
