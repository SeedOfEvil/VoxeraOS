from unittest.mock import MagicMock, patch

from voxera.skills.result_contract import SKILL_RESULT_KEY
from voxera_builtin_skills import open_app


def test_open_app_launches_allowlisted_argv():
    with patch("subprocess.Popen", MagicMock()) as popen:
        rr = open_app.run("firefox")

    assert rr.ok is True
    assert rr.data[SKILL_RESULT_KEY]["machine_payload"]["argv"] == ["firefox"]
    popen.assert_called_once_with(["firefox"], stdout=-3, stderr=-3)


def test_open_app_rejects_unsafe_identifier():
    rr = open_app.run("firefox --new-window")

    assert rr.ok is False
    assert rr.data[SKILL_RESULT_KEY]["error_class"] == "invalid_input"
