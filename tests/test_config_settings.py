from __future__ import annotations

from pathlib import Path

import pytest

from voxera.config import VoxeraSettings, load_env_file


def test_settings_defaults(tmp_path: Path) -> None:
    settings = VoxeraSettings.from_env(environ={}, cwd=tmp_path, home=tmp_path)

    assert settings.queue_root.as_posix().endswith("VoxeraOS/notes/queue")
    assert settings.panel_host == "127.0.0.1"
    assert settings.panel_port == 8844
    assert settings.queue_lock_stale_s == 3600.0
    assert settings.panel_operator_user == "admin"


@pytest.mark.parametrize(
    ("env", "match"),
    [
        ({"VOXERA_PANEL_PORT": "abc"}, "VOXERA_PANEL_PORT"),
        ({"VOXERA_QUEUE_LOCK_STALE_S": "bad"}, "VOXERA_QUEUE_LOCK_STALE_S"),
        ({"VOXERA_PANEL_ENABLE_GET_MUTATIONS": "wat"}, "VOXERA_PANEL_ENABLE_GET_MUTATIONS"),
    ],
)
def test_settings_invalid_values_raise(env: dict[str, str], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        VoxeraSettings.from_env(environ=env, cwd=Path.cwd(), home=Path.home())


def test_settings_redacts_sensitive_fields() -> None:
    settings = VoxeraSettings.from_env(
        environ={"VOXERA_PANEL_OPERATOR_PASSWORD": "super-secret"},
        cwd=Path.cwd(),
        home=Path.home(),
    )

    payload = settings.to_safe_dict()
    assert payload["panel_operator_password"] == "***"


def test_load_env_file_parses_comments_and_blank_lines(tmp_path: Path) -> None:
    env_file = tmp_path / "voxera.env"
    env_file.write_text(
        """
# comment
VOXERA_PANEL_HOST=0.0.0.0

VOXERA_PANEL_PORT=9000
""",
        encoding="utf-8",
    )

    parsed = load_env_file(env_file)
    assert parsed == {"VOXERA_PANEL_HOST": "0.0.0.0", "VOXERA_PANEL_PORT": "9000"}


def test_load_env_file_rejects_invalid_lines(tmp_path: Path) -> None:
    env_file = tmp_path / "broken.env"
    env_file.write_text("INVALID_LINE\n", encoding="utf-8")

    with pytest.raises(ValueError, match="expected KEY=VALUE"):
        load_env_file(env_file)
