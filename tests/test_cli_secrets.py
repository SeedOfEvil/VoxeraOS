from __future__ import annotations

from typer.testing import CliRunner

from voxera import cli


def test_secrets_set_uses_explicit_value_without_echo(monkeypatch):
    runner = CliRunner()
    seen: dict[str, str] = {}

    def _fake_write(name: str, value: str) -> str:
        seen["name"] = name
        seen["value"] = value
        return f"keyring:{name}"

    monkeypatch.setattr(cli, "write_secret", _fake_write)

    res = runner.invoke(cli.app, ["secrets", "set", "BRAVE_API_KEY", "--value", "hidden-value"])

    assert res.exit_code == 0
    assert seen == {"name": "BRAVE_API_KEY", "value": "hidden-value"}
    assert "hidden-value" not in res.stdout


def test_secrets_set_prompts_hidden_input_when_missing_value(monkeypatch):
    runner = CliRunner()
    seen: dict[str, str] = {}

    monkeypatch.setattr(
        cli, "write_secret", lambda name, value: seen.update({"name": name, "value": value})
    )

    res = runner.invoke(cli.app, ["secrets", "set", "OPENROUTER_API_KEY"], input="abc\nabc\n")

    assert res.exit_code == 0
    assert seen == {"name": "OPENROUTER_API_KEY", "value": "abc"}


def test_secrets_get_defaults_to_safe_presence_output(monkeypatch):
    runner = CliRunner()
    monkeypatch.setattr(cli, "get_secret", lambda _: "secret")

    res = runner.invoke(cli.app, ["secrets", "get", "BRAVE_API_KEY"])

    assert res.exit_code == 0
    assert res.stdout.strip() == "present"


def test_secrets_get_exists_only_missing_is_nonzero(monkeypatch):
    runner = CliRunner()
    monkeypatch.setattr(cli, "get_secret", lambda _: None)

    res = runner.invoke(cli.app, ["secrets", "get", "BRAVE_API_KEY", "--exists-only"])

    assert res.exit_code == 1
    assert res.stdout.strip() == "missing"


def test_secrets_get_show_value_requires_opt_in(monkeypatch):
    runner = CliRunner()
    monkeypatch.setattr(cli, "get_secret", lambda _: "visible-only-when-opted-in")

    res = runner.invoke(cli.app, ["secrets", "get", "OPENROUTER_API_KEY", "--show-value"])

    assert res.exit_code == 0
    assert res.stdout.strip() == "visible-only-when-opted-in"


def test_secrets_unset_exit_codes(monkeypatch):
    runner = CliRunner()

    monkeypatch.setattr(cli, "unset_secret", lambda _: True)
    ok = runner.invoke(cli.app, ["secrets", "unset", "BRAVE_API_KEY"])
    assert ok.exit_code == 0
    assert "Removed secret: BRAVE_API_KEY" in ok.stdout

    monkeypatch.setattr(cli, "unset_secret", lambda _: False)
    missing = runner.invoke(cli.app, ["secrets", "unset", "BRAVE_API_KEY"])
    assert missing.exit_code == 1
    assert "Secret not found: BRAVE_API_KEY" in missing.stdout
