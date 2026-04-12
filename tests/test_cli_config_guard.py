"""Tests for the first-run config guard on CLI runtime surfaces.

Guarded commands must exit 1 with the missing-config hint when config.yml
is absent.  Unguarded commands (setup, doctor, version, config show, help)
must remain usable without config.
"""

from __future__ import annotations

import re
import sys

from typer.testing import CliRunner

from voxera import cli
from voxera.cli_common import _CONFIG_GUARD_MESSAGE

runner = CliRunner()


def _simulate_argv(monkeypatch, args: list[str]) -> None:
    """Set sys.argv so the ``--help`` skip in require_config fires."""
    monkeypatch.setattr(sys, "argv", ["voxera", *args])


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _with_missing_config(monkeypatch, tmp_path):
    """Point default_config_path at a non-existent file."""
    monkeypatch.setattr(
        "voxera.config.default_config_path",
        lambda: tmp_path / "nonexistent" / "config.yml",
    )


def _with_present_config(monkeypatch, tmp_path):
    """Point default_config_path at a real (minimal) file."""
    cfg = tmp_path / "config.yml"
    cfg.write_text("mode: cli\n", encoding="utf-8")
    monkeypatch.setattr("voxera.config.default_config_path", lambda: cfg)


# ── Guarded commands: exit 1 + hint when config is missing ──────────


def test_vera_guarded_when_config_missing(monkeypatch, tmp_path):
    _with_missing_config(monkeypatch, tmp_path)
    result = runner.invoke(cli.app, ["vera"])
    assert result.exit_code == 1
    assert _CONFIG_GUARD_MESSAGE in _strip_ansi(result.stdout)


def test_panel_guarded_when_config_missing(monkeypatch, tmp_path):
    _with_missing_config(monkeypatch, tmp_path)
    result = runner.invoke(cli.app, ["panel"])
    assert result.exit_code == 1
    assert _CONFIG_GUARD_MESSAGE in _strip_ansi(result.stdout)


def test_daemon_guarded_when_config_missing(monkeypatch, tmp_path):
    _with_missing_config(monkeypatch, tmp_path)
    result = runner.invoke(cli.app, ["daemon"])
    assert result.exit_code == 1
    assert _CONFIG_GUARD_MESSAGE in _strip_ansi(result.stdout)


def test_queue_group_guarded_when_config_missing(monkeypatch, tmp_path):
    _with_missing_config(monkeypatch, tmp_path)
    result = runner.invoke(cli.app, ["queue", "status"])
    assert result.exit_code == 1
    assert _CONFIG_GUARD_MESSAGE in _strip_ansi(result.stdout)


def test_queue_init_guarded_when_config_missing(monkeypatch, tmp_path):
    _with_missing_config(monkeypatch, tmp_path)
    result = runner.invoke(cli.app, ["queue", "init"])
    assert result.exit_code == 1
    assert _CONFIG_GUARD_MESSAGE in _strip_ansi(result.stdout)


def test_automation_group_guarded_when_config_missing(monkeypatch, tmp_path):
    _with_missing_config(monkeypatch, tmp_path)
    result = runner.invoke(cli.app, ["automation", "list"])
    assert result.exit_code == 1
    assert _CONFIG_GUARD_MESSAGE in _strip_ansi(result.stdout)


# ── Guarded commands proceed normally when config is present ────────


def test_vera_proceeds_when_config_present(monkeypatch, tmp_path):
    _with_present_config(monkeypatch, tmp_path)
    # Stub out the actual impl so we don't start uvicorn
    monkeypatch.setattr("voxera.cli.vera_impl", lambda **kw: None)
    result = runner.invoke(cli.app, ["vera"])
    assert result.exit_code == 0


def test_daemon_proceeds_when_config_present(monkeypatch, tmp_path):
    _with_present_config(monkeypatch, tmp_path)
    monkeypatch.setattr("voxera.cli.daemon_impl", lambda **kw: None)
    result = runner.invoke(cli.app, ["daemon"])
    assert result.exit_code == 0


# ── Unguarded commands remain usable without config ─────────────────


def test_setup_unguarded(monkeypatch, tmp_path):
    _with_missing_config(monkeypatch, tmp_path)
    monkeypatch.setattr("voxera.cli.setup_impl", lambda **kw: None)
    result = runner.invoke(cli.app, ["setup"])
    assert result.exit_code == 0
    assert _CONFIG_GUARD_MESSAGE not in _strip_ansi(result.stdout)


def test_doctor_unguarded(monkeypatch, tmp_path):
    _with_missing_config(monkeypatch, tmp_path)
    monkeypatch.setattr("voxera.cli_doctor.doctor_sync", lambda **kw: None)
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0
    assert _CONFIG_GUARD_MESSAGE not in _strip_ansi(result.stdout)


def test_version_unguarded(monkeypatch, tmp_path):
    _with_missing_config(monkeypatch, tmp_path)
    monkeypatch.setattr("voxera.cli.get_version", lambda: "0.0.0-test")
    monkeypatch.setattr("voxera.cli._git_sha", lambda: None)
    result = runner.invoke(cli.app, ["version"])
    assert result.exit_code == 0
    assert _CONFIG_GUARD_MESSAGE not in _strip_ansi(result.stdout)


def test_config_show_unguarded(monkeypatch, tmp_path):
    _with_missing_config(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "voxera.cli.config_show_impl",
        lambda **kw: None,
    )
    result = runner.invoke(cli.app, ["config", "show"])
    assert result.exit_code == 0
    assert _CONFIG_GUARD_MESSAGE not in _strip_ansi(result.stdout)


# ── Help flows remain usable without config ─────────────────────────


def test_root_help_unguarded(monkeypatch, tmp_path):
    _with_missing_config(monkeypatch, tmp_path)
    _simulate_argv(monkeypatch, ["--help"])
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    assert _CONFIG_GUARD_MESSAGE not in _strip_ansi(result.stdout)


def test_vera_help_unguarded(monkeypatch, tmp_path):
    _with_missing_config(monkeypatch, tmp_path)
    _simulate_argv(monkeypatch, ["vera", "--help"])
    result = runner.invoke(cli.app, ["vera", "--help"])
    assert result.exit_code == 0
    assert _CONFIG_GUARD_MESSAGE not in _strip_ansi(result.stdout)


def test_daemon_help_unguarded(monkeypatch, tmp_path):
    _with_missing_config(monkeypatch, tmp_path)
    _simulate_argv(monkeypatch, ["daemon", "--help"])
    result = runner.invoke(cli.app, ["daemon", "--help"])
    assert result.exit_code == 0
    assert _CONFIG_GUARD_MESSAGE not in _strip_ansi(result.stdout)


def test_queue_help_unguarded(monkeypatch, tmp_path):
    _with_missing_config(monkeypatch, tmp_path)
    _simulate_argv(monkeypatch, ["queue", "--help"])
    result = runner.invoke(cli.app, ["queue", "--help"])
    assert result.exit_code == 0
    assert _CONFIG_GUARD_MESSAGE not in _strip_ansi(result.stdout)


def test_queue_subcommand_help_unguarded(monkeypatch, tmp_path):
    _with_missing_config(monkeypatch, tmp_path)
    _simulate_argv(monkeypatch, ["queue", "status", "--help"])
    result = runner.invoke(cli.app, ["queue", "status", "--help"])
    assert result.exit_code == 0
    assert _CONFIG_GUARD_MESSAGE not in _strip_ansi(result.stdout)


def test_automation_help_unguarded(monkeypatch, tmp_path):
    _with_missing_config(monkeypatch, tmp_path)
    _simulate_argv(monkeypatch, ["automation", "--help"])
    result = runner.invoke(cli.app, ["automation", "--help"])
    assert result.exit_code == 0
    assert _CONFIG_GUARD_MESSAGE not in _strip_ansi(result.stdout)
