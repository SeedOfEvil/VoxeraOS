from __future__ import annotations

from unittest.mock import MagicMock, patch

from voxera.skills.result_contract import SKILL_RESULT_KEY
from voxera_builtin_skills import (
    clipboard_copy,
    clipboard_paste,
    files_read_text,
    files_write_text,
    open_url,
    system_status,
    terminal_run_once,
)


def test_open_url_invalid_scheme_returns_canonical_skill_result():
    rr = open_url.run("file:///tmp/demo.txt")
    assert rr.ok is False
    payload = rr.data[SKILL_RESULT_KEY]
    assert payload["summary"] == "Rejected non-http(s) URL"
    assert payload["error_class"] == "invalid_input"
    assert payload["retryable"] is False
    assert payload["blocked"] is False
    assert payload["approval_status"] == "none"


def test_terminal_run_once_missing_launcher_returns_canonical_skill_result():
    with patch("shutil.which", return_value=None):
        rr = terminal_run_once.run()
    assert rr.ok is False
    payload = rr.data[SKILL_RESULT_KEY]
    assert payload["summary"] == "Terminal launcher unavailable"
    assert payload["next_action_hint"] == "install_launcher"
    assert payload["error_class"] == "missing_dependency"


def test_open_url_success_includes_machine_payload_launcher():
    mock_popen = MagicMock()
    with patch("subprocess.Popen", mock_popen):
        rr = open_url.run("https://example.com")
    assert rr.ok is True
    payload = rr.data[SKILL_RESULT_KEY]
    assert payload["machine_payload"]["launcher"] == "firefox"
    assert payload["retryable"] is False


def test_files_read_text_missing_file_sets_retryable_not_found(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    allowed_root.mkdir(parents=True)
    monkeypatch.setattr(files_read_text, "ALLOWED_ROOT", allowed_root)
    rr = files_read_text.run(str(allowed_root / "missing.txt"))
    assert rr.ok is False
    payload = rr.data[SKILL_RESULT_KEY]
    assert payload["error_class"] == "not_found"
    assert payload["retryable"] is True
    assert payload["error"]


def test_files_write_text_invalid_mode_has_structured_contract(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    allowed_root.mkdir(parents=True)
    monkeypatch.setattr(files_write_text, "ALLOWED_ROOT", allowed_root)
    rr = files_write_text.run(str(allowed_root / "a.txt"), text="x", mode="bad")
    assert rr.ok is False
    payload = rr.data[SKILL_RESULT_KEY]
    assert payload["error_class"] == "invalid_input"
    assert payload["next_action_hint"] == "provide_supported_mode"


def test_clipboard_copy_missing_dependency_returns_structured_error():
    with patch("subprocess.run", side_effect=FileNotFoundError):
        rr = clipboard_copy.run("hello")
    assert rr.ok is False
    payload = rr.data[SKILL_RESULT_KEY]
    assert payload["error_class"] == "missing_dependency"
    assert payload["summary"] == "No supported clipboard copy tool found"


def test_clipboard_paste_missing_dependency_returns_structured_error():
    with patch("subprocess.check_output", side_effect=FileNotFoundError):
        rr = clipboard_paste.run()
    assert rr.ok is False
    payload = rr.data[SKILL_RESULT_KEY]
    assert payload["error_class"] == "missing_dependency"


def test_system_status_returns_structured_success_payload():
    rr = system_status.run()
    assert rr.ok is True
    payload = rr.data[SKILL_RESULT_KEY]
    assert payload["summary"] == "Collected system status snapshot"
    assert isinstance(payload["machine_payload"], dict)
    assert payload["approval_status"] == "none"
