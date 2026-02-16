from typer.testing import CliRunner

from voxera import cli


def test_version_command_prints_semver(monkeypatch):
    monkeypatch.setattr("voxera.cli._git_sha", lambda: "abc1234")
    runner = CliRunner()

    result = runner.invoke(cli.app, ["version"])

    assert result.exit_code == 0
    assert "0.1.3 (abc1234)" in result.stdout


def test_root_version_option_prints_and_exits(monkeypatch):
    monkeypatch.setattr("voxera.cli._git_sha", lambda: None)
    runner = CliRunner()

    result = runner.invoke(cli.app, ["--version"])

    assert result.exit_code == 0
    assert "0.1.3" in result.stdout
