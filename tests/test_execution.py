from pathlib import Path
from types import SimpleNamespace

from voxera.models import AppConfig, SkillManifest
from voxera.skills import execution as execution_module
from voxera.skills.execution import PodmanSandboxRunner
from voxera.skills.result_contract import SKILL_RESULT_KEY


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


def test_sandbox_exec_command_string_is_converted_via_shlex_split(monkeypatch, tmp_path: Path):
    """String command is tokenised with shlex.split (not wrapped in bash -lc)."""
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
    # shlex.split("echo 'Checking the page title...'") → ["echo", "Checking the page title..."]
    assert captured["cmd"][-2:] == ["echo", "Checking the page title..."]


def test_sandbox_exec_empty_or_whitespace_command_string_returns_error(monkeypatch):
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
        assert "non-empty list of strings" in result.error


def test_sandbox_exec_invalid_command_type_returns_error(monkeypatch):
    monkeypatch.setattr(PodmanSandboxRunner, "_assert_available", lambda self: None)
    runner = PodmanSandboxRunner()

    # 123 → int value; {"cmd": "echo hi"} → dict value (not the cmd alias); ["echo", 1] → non-str token
    for command in [123, {"cmd": "echo hi"}, ["echo", 1]]:
        result = runner.run(
            manifest=_sandbox_manifest(),
            args={"command": command},
            fn=lambda **_kwargs: None,
            cfg=AppConfig(),
            job_id="job-bad-type",
        )
        assert result.ok is False
        assert "non-empty list of strings" in result.error


def test_sandbox_exec_argv_alias_is_accepted(monkeypatch, tmp_path: Path):
    """'argv' key is resolved to 'command' before execution."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    captured = _patch_successful_run(monkeypatch)

    runner = PodmanSandboxRunner()
    result = runner.run(
        manifest=_sandbox_manifest(),
        args={"argv": ["echo", "hello"]},
        fn=lambda **_kwargs: None,
        cfg=AppConfig(),
        job_id="job-argv-alias",
    )

    assert result.ok is True
    assert captured["cmd"][-2:] == ["echo", "hello"]


def test_sandbox_exec_cmd_alias_is_accepted(monkeypatch, tmp_path: Path):
    """'cmd' key is resolved to 'command' before execution."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    captured = _patch_successful_run(monkeypatch)

    runner = PodmanSandboxRunner()
    result = runner.run(
        manifest=_sandbox_manifest(),
        args={"cmd": ["ls", "-la"]},
        fn=lambda **_kwargs: None,
        cfg=AppConfig(),
        job_id="job-cmd-alias",
    )

    assert result.ok is True
    assert captured["cmd"][-2:] == ["ls", "-la"]


def test_sandbox_exec_empty_tokens_in_list_are_rejected(monkeypatch):
    monkeypatch.setattr(PodmanSandboxRunner, "_assert_available", lambda self: None)
    runner = PodmanSandboxRunner()
    result = runner.run(
        manifest=_sandbox_manifest(),
        args={"command": ["", "echo", "", "hello"]},
        fn=lambda **_kwargs: None,
        cfg=AppConfig(),
        job_id="job-empty-tokens",
    )

    assert result.ok is False
    assert result.data[SKILL_RESULT_KEY]["error_class"] == "invalid_input"


def test_sandbox_exec_all_empty_tokens_returns_error(monkeypatch):
    """A list of only empty/whitespace tokens fails with a clear error."""
    monkeypatch.setattr(PodmanSandboxRunner, "_assert_available", lambda self: None)
    runner = PodmanSandboxRunner()

    result = runner.run(
        manifest=_sandbox_manifest(),
        args={"command": ["", " ", "\t"]},
        fn=lambda **_kwargs: None,
        cfg=AppConfig(),
        job_id="job-all-empty",
    )

    assert result.ok is False
    assert "non-empty list of strings" in result.error


def test_sandbox_exec_shell_control_string_returns_structured_block(monkeypatch):
    monkeypatch.setattr(PodmanSandboxRunner, "_assert_available", lambda self: None)
    runner = PodmanSandboxRunner()

    result = runner.run(
        manifest=_sandbox_manifest(),
        args={"command": "echo hi && uname -a"},
        fn=lambda **_kwargs: None,
        cfg=AppConfig(),
        job_id="job-shell-control",
    )

    assert result.ok is False
    payload = result.data[SKILL_RESULT_KEY]
    assert payload["summary"] == "Rejected sandbox command input"
    assert payload["error_class"] == "invalid_input"


def test_sandbox_exec_whitespace_and_empty_tokens_fail_with_actionable_message(monkeypatch):
    """MANUAL REPRO BUG A: ["   ", ""] must fail fast with actionable message (not reach crun)."""
    monkeypatch.setattr(PodmanSandboxRunner, "_assert_available", lambda self: None)
    runner = PodmanSandboxRunner()

    result = runner.run(
        manifest=_sandbox_manifest(),
        args={"command": ["   ", ""]},
        fn=lambda **_kwargs: None,
        cfg=AppConfig(),
        job_id="job-bugA",
    )

    assert result.ok is False
    # Must contain the actionable hint — not just the short form
    assert "Provide args.command" in result.error
    assert "bash" in result.error  # the example command


def test_sandbox_exec_argv_alias_end_to_end(monkeypatch, tmp_path: Path):
    """MANUAL REPRO BUG B: {"argv": [...]} must reach Podman and succeed."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    captured = _patch_successful_run(monkeypatch)

    runner = PodmanSandboxRunner()
    result = runner.run(
        manifest=_sandbox_manifest(),
        args={"argv": ["bash", "-lc", "echo hello"]},
        fn=lambda **_kwargs: None,
        cfg=AppConfig(),
        job_id="job-bugB-argv",
    )

    assert result.ok is True
    assert captured["cmd"][-3:] == ["bash", "-lc", "echo hello"]


def test_sandbox_exec_cmd_alias_end_to_end(monkeypatch, tmp_path: Path):
    """{"cmd": [...]} must reach Podman and succeed (mirrors argv alias BUG B)."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    captured = _patch_successful_run(monkeypatch)

    runner = PodmanSandboxRunner()
    result = runner.run(
        manifest=_sandbox_manifest(),
        args={"cmd": ["ip", "a"]},
        fn=lambda **_kwargs: None,
        cfg=AppConfig(),
        job_id="job-bugB-cmd",
    )

    assert result.ok is True
    assert captured["cmd"][-2:] == ["ip", "a"]


def test_sandbox_exec_success_emits_canonical_skill_result(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    _patch_successful_run(monkeypatch)

    runner = PodmanSandboxRunner()
    result = runner.run(
        manifest=_sandbox_manifest(),
        args={"command": ["echo", "hello"]},
        fn=lambda **_kwargs: None,
        cfg=AppConfig(),
        job_id="job-structured",
    )

    assert result.ok is True
    payload = result.data[SKILL_RESULT_KEY]
    assert payload["summary"] == "Sandbox command completed successfully"
    assert isinstance(payload["output_artifacts"], list)
    assert payload["next_action_hint"] == "continue"
