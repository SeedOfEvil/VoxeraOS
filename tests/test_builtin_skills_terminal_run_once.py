from __future__ import annotations

from unittest.mock import MagicMock, patch

from voxera_builtin_skills.terminal_run_once import run


def test_run_opens_gnome_terminal():
    mock_popen = MagicMock()

    with patch("shutil.which", return_value="/usr/bin/gnome-terminal"), patch(
        "subprocess.Popen", mock_popen
    ):
        result = run()

    assert result.ok is True
    assert "terminal" in result.output.lower() or "hello" in result.output.lower()
    mock_popen.assert_called_once()
    call_args = mock_popen.call_args[0][0]
    assert "gnome-terminal" in call_args
    assert "--" in call_args
    assert "bash" in call_args
    assert "-lc" in call_args


def test_run_script_contains_hello_world_and_press_enter():
    mock_popen = MagicMock()

    with patch("shutil.which", return_value="/usr/bin/gnome-terminal"), patch(
        "subprocess.Popen", mock_popen
    ):
        result = run(keep_open=True)

    assert result.ok is True
    call_args = mock_popen.call_args[0][0]
    script = call_args[-1]
    assert "Hello, world!" in script
    assert "Press Enter" in script


def test_run_without_keep_open_has_no_press_enter():
    mock_popen = MagicMock()

    with patch("shutil.which", return_value="/usr/bin/gnome-terminal"), patch(
        "subprocess.Popen", mock_popen
    ):
        result = run(keep_open=False)

    assert result.ok is True
    call_args = mock_popen.call_args[0][0]
    script = call_args[-1]
    assert "Hello, world!" in script
    assert "Press Enter" not in script


def test_run_fails_if_gnome_terminal_not_found():
    with patch("shutil.which", return_value=None):
        result = run()

    assert result.ok is False
    assert "gnome-terminal" in result.error
