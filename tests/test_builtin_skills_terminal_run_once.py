from __future__ import annotations

from unittest.mock import MagicMock, patch

from voxera_builtin_skills.terminal_run_once import run


def test_run_opens_plain_gnome_terminal_without_demo_script():
    mock_popen = MagicMock()

    with (
        patch("shutil.which", return_value="/usr/bin/gnome-terminal"),
        patch("subprocess.Popen", mock_popen),
    ):
        result = run()

    assert result.ok is True
    assert "opened terminal" in result.output.lower()
    mock_popen.assert_called_once()
    call_args = mock_popen.call_args[0][0]
    assert call_args == ["gnome-terminal"]


def test_keep_open_flag_does_not_inject_commands():
    mock_popen = MagicMock()

    with (
        patch("shutil.which", return_value="/usr/bin/gnome-terminal"),
        patch("subprocess.Popen", mock_popen),
    ):
        result = run(keep_open=False)

    assert result.ok is True
    call_args = mock_popen.call_args[0][0]
    assert call_args == ["gnome-terminal"]


def test_run_fails_if_gnome_terminal_not_found():
    with patch("shutil.which", return_value=None):
        result = run()

    assert result.ok is False
    assert "gnome-terminal" in result.error
