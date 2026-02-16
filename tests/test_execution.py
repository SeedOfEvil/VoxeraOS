from pathlib import Path
from types import SimpleNamespace

from voxera.models import AppConfig, SkillManifest
from voxera.skills import execution as execution_module
from voxera.skills.execution import PodmanSandboxRunner


def test_parse_network_setting_accepts_bool_and_string_values():
    assert PodmanSandboxRunner._parse_network_setting(True) is True
    assert PodmanSandboxRunner._parse_network_setting(False) is False
    assert PodmanSandboxRunner._parse_network_setting("true") is True
    assert PodmanSandboxRunner._parse_network_setting("TRUE") is True
    assert PodmanSandboxRunner._parse_network_setting("1") is True
    assert PodmanSandboxRunner._parse_network_setting("false") is False
    assert PodmanSandboxRunner._parse_network_setting("0") is False
    assert PodmanSandboxRunner._parse_network_setting(None) is False


def test_parse_network_setting_rejects_non_boolean_like_values():
    try:
        PodmanSandboxRunner._parse_network_setting("maybe")
    except ValueError as exc:
        assert str(exc) == "network must be a boolean value"
    else:
        raise AssertionError("expected ValueError")

    try:
        PodmanSandboxRunner._parse_network_setting(1)
    except ValueError as exc:
        assert str(exc) == "network must be a boolean value"
    else:
        raise AssertionError("expected ValueError")


def _sandbox_manifest() -> SkillManifest:
    return SkillManifest(
        id="sandbox.exec",
        name="Sandbox Execute",
        description="Execute a command in a rootless Podman sandbox.",
        entrypoint="voxera_builtin_skills.sandbox_exec:run",
        exec_mode="sandbox",
    )


def _patch_successful_run(monkeypatch):
    monkeypatch.setattr(PodmanSandboxRunner, "_assert_available", lambda self: None)

    captured = {}

    def fake_run(cmd, check, capture_output, text, timeout):
        captured["cmd"] = cmd
        return SimpleNamespace(stdout="ok\n", stderr="", returncode=0)

    monkeypatch.setattr(execution_module.subprocess, "run", fake_run)
    return captured


def test_sandbox_exec_command_list_is_accepted(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    captured = _patch_successful_run(monkeypatch)

    runner = PodmanSandboxRunner()
    result = runner.run(
        manifest=_sandbox_manifest(),
        args={"command": ["echo", "hello"]},
        fn=lambda **_kwargs: None,
        cfg=AppConfig(),
        job_id="job-list",
    )

    assert result.ok is True
    assert captured["cmd"][-2:] == ["echo", "hello"]


def test_sandbox_exec_command_string_is_converted_to_shell_argv(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    captured = _patch_successful_run(monkeypatch)

    runner = PodmanSandboxRunner()
    result = runner.run(
        manifest=_sandbox_manifest(),
        args={"command": "echo 'Checking the page title...'"},
        fn=lambda **_kwargs: None,
        cfg=AppConfig(),
        job_id="job-string",
    )

    assert result.ok is True
    assert captured["cmd"][-3:] == ["bash", "-lc", "echo 'Checking the page title...'"]
    command_text = Path(result.data["command_path"]).read_text(encoding="utf-8")
    assert "shell_command: echo 'Checking the page title...'" in command_text
    assert "argv:" in command_text


def test_sandbox_exec_empty_or_whitespace_command_string_returns_existing_error(monkeypatch):
    monkeypatch.setattr(PodmanSandboxRunner, "_assert_available", lambda self: None)
    runner = PodmanSandboxRunner()

    for command in ["", "   ", "\n\t"]:
        result = runner.run(
            manifest=_sandbox_manifest(),
            args={"command": command},
            fn=lambda **_kwargs: None,
            cfg=AppConfig(),
            job_id="job-empty",
        )
        assert result.ok is False
        assert result.error == "sandbox.exec requires command as a non-empty list of strings"


def test_sandbox_exec_invalid_command_type_returns_existing_error(monkeypatch):
    monkeypatch.setattr(PodmanSandboxRunner, "_assert_available", lambda self: None)
    runner = PodmanSandboxRunner()

    for command in [123, {"cmd": "echo hi"}, ["echo", 1]]:
        result = runner.run(
            manifest=_sandbox_manifest(),
            args={"command": command},
            fn=lambda **_kwargs: None,
            cfg=AppConfig(),
            job_id="job-bad-type",
        )
        assert result.ok is False
        assert result.error == "sandbox.exec requires command as a non-empty list of strings"
