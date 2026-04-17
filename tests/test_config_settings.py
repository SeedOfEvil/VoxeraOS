from __future__ import annotations

import json
from pathlib import Path

import pytest

from voxera.config import VoxeraConfig, load_config, load_env_file, update_runtime_config


def test_config_precedence_file_env_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps(
            {
                "panel_host": "0.0.0.0",
                "panel_port": 9000,
                "queue_lock_stale_s": 111.0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("VOXERA_PANEL_PORT", "9100")

    cfg = load_config(overrides={"panel_port": 9200}, config_path=cfg_file)

    assert cfg.panel_host == "0.0.0.0"
    assert cfg.panel_port == 9200
    assert cfg.queue_lock_stale_s == 111.0
    assert cfg.sources["panel_host"].startswith("file:")
    assert cfg.sources["panel_port"] == "override"


@pytest.mark.parametrize(
    "env",
    [
        {"VOXERA_PANEL_PORT": "bad"},
        {"VOXERA_QUEUE_LOCK_STALE_S": "nope"},
        {"VOXERA_PANEL_CSRF_ENABLED": "whoops"},
    ],
)
def test_config_invalid_values_raise(env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        load_config(config_path=Path("/tmp/does-not-exist.json"))


def test_config_redacts_sensitive_fields() -> None:
    cfg = VoxeraConfig(
        queue_root=Path("/tmp/queue"),
        panel_host="127.0.0.1",
        panel_port=8844,
        panel_operator_user="admin",
        panel_operator_password="secret",
        panel_csrf_enabled=True,
        panel_enable_get_mutations=False,
        queue_lock_stale_s=3600.0,
        queue_failed_max_age_s=None,
        queue_failed_max_count=None,
        artifacts_retention_days=None,
        artifacts_retention_max_count=None,
        queue_prune_max_age_days=None,
        queue_prune_max_count=None,
        ops_bundle_dir=None,
        dev_mode=False,
        notify_enabled=False,
        config_path=Path("/tmp/config.json"),
        sources={"panel_operator_password": "env:VOXERA_PANEL_OPERATOR_PASSWORD"},
    )

    payload = cfg.to_safe_dict()
    assert payload["panel_operator_password"] == "***"


def test_load_env_file_parses_comments_and_blank_lines(tmp_path: Path) -> None:
    env_file = tmp_path / "voxera.env"
    env_file.write_text(
        "# comment\nVOXERA_PANEL_HOST=0.0.0.0\n\nVOXERA_PANEL_PORT=9000\n", encoding="utf-8"
    )

    parsed = load_env_file(env_file)
    assert parsed == {"VOXERA_PANEL_HOST": "0.0.0.0", "VOXERA_PANEL_PORT": "9000"}


def test_load_env_file_rejects_invalid_lines(tmp_path: Path) -> None:
    env_file = tmp_path / "broken.env"
    env_file.write_text("INVALID_LINE\n", encoding="utf-8")

    with pytest.raises(ValueError, match="expected KEY=VALUE"):
        load_env_file(env_file)


def test_update_runtime_config_creates_file_when_absent(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    written = update_runtime_config({"panel_port": 9100}, config_path=cfg_path)

    assert written == cfg_path
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data == {"panel_port": 9100}


def test_update_runtime_config_merges_without_clobbering(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"panel_port": 8844, "queue_lock_stale_s": 123.0}),
        encoding="utf-8",
    )
    update_runtime_config(
        {"voice_stt_backend": "whisper_local", "voice_tts_backend": "piper_local"},
        config_path=cfg_path,
    )
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data["panel_port"] == 8844
    assert data["queue_lock_stale_s"] == 123.0
    assert data["voice_stt_backend"] == "whisper_local"
    assert data["voice_tts_backend"] == "piper_local"


def test_update_runtime_config_none_value_removes_key(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"voice_stt_backend": "whisper_local", "panel_port": 8844}),
        encoding="utf-8",
    )
    update_runtime_config({"voice_stt_backend": None}, config_path=cfg_path)
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "voice_stt_backend" not in data
    assert data["panel_port"] == 8844


def test_update_runtime_config_creates_parent_directory(tmp_path: Path) -> None:
    cfg_path = tmp_path / "nested" / "config.json"
    assert not cfg_path.parent.exists()
    update_runtime_config({"panel_port": 9100}, config_path=cfg_path)
    assert cfg_path.parent.is_dir()
    assert json.loads(cfg_path.read_text(encoding="utf-8")) == {"panel_port": 9100}
