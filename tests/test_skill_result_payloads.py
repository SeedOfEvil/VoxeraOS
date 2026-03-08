from __future__ import annotations

from unittest.mock import MagicMock, patch

from voxera.skills.result_contract import SKILL_RESULT_KEY
from voxera_builtin_skills import files_read_text, open_url, terminal_run_once


def test_open_url_invalid_scheme_returns_canonical_skill_result():
    rr = open_url.run("file:///tmp/demo.txt")
    assert rr.ok is False
    payload = rr.data[SKILL_RESULT_KEY]
    assert payload["summary"] == "Rejected non-http(s) URL"
    assert payload["error_class"] == "invalid_input"
    assert payload["retryable"] is False


def test_open_url_rejects_credentialed_url_with_structured_error():
    rr = open_url.run("https://user:pass@example.com")
    assert rr.ok is False
    payload = rr.data[SKILL_RESULT_KEY]
    assert payload["summary"] == "Rejected unsafe URL form"
    assert payload["error_class"] == "invalid_input"


def test_terminal_run_once_missing_launcher_returns_canonical_skill_result():
    with patch("shutil.which", return_value=None):
        rr = terminal_run_once.run()
    assert rr.ok is False
    payload = rr.data[SKILL_RESULT_KEY]
    assert payload["summary"] == "Terminal launcher unavailable"
    assert payload["next_action_hint"] == "install_launcher"


def test_open_url_success_includes_machine_payload_launcher():
    mock_popen = MagicMock()
    with patch("subprocess.Popen", mock_popen):
        rr = open_url.run("https://example.com")
    assert rr.ok is True
    payload = rr.data[SKILL_RESULT_KEY]
    assert payload["machine_payload"]["launcher"] == "firefox"


def test_files_read_text_missing_file_sets_retryable_not_found(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    allowed_root.mkdir(parents=True)
    monkeypatch.setattr(files_read_text, "ALLOWED_ROOT", allowed_root)
    rr = files_read_text.run(str(allowed_root / "missing.txt"))
    assert rr.ok is False
    payload = rr.data[SKILL_RESULT_KEY]
    assert payload["error_class"] == "not_found"
    assert payload["retryable"] is True
