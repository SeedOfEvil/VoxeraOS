from typer.testing import CliRunner

from voxera import cli


def test_version_command_uses_shared_version_source(monkeypatch):
    monkeypatch.setattr("voxera.cli.get_version", lambda: "9.9.9")
    monkeypatch.setattr("voxera.cli._git_sha", lambda: "abc1234")
    runner = CliRunner()

    result = runner.invoke(cli.app, ["version"])

    assert result.exit_code == 0
    assert "9.9.9 (abc1234)" in result.stdout


def test_root_version_option_prints_and_exits(monkeypatch):
    monkeypatch.setattr("voxera.cli.get_version", lambda: "7.8.9")
    monkeypatch.setattr("voxera.cli._git_sha", lambda: None)
    runner = CliRunner()

    result = runner.invoke(cli.app, ["--version"])

    assert result.exit_code == 0
    assert "7.8.9" in result.stdout
