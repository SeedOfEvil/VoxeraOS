from __future__ import annotations

import re

import typer
from typer.testing import CliRunner

from voxera import cli


def test_cli_public_command_surface_snapshot():
    root = typer.main.get_command(cli.app)

    assert sorted(root.commands.keys()) == [
        "artifacts",
        "audit",
        "config",
        "config-show",
        "daemon",
        "demo",
        "doctor",
        "inbox",
        "missions",
        "ops",
        "panel",
        "queue",
        "run",
        "setup",
        "skills",
        "status",
        "version",
    ]
    assert sorted(root.commands["config"].commands.keys()) == ["show", "snapshot", "validate"]
    assert sorted(root.commands["ops"].commands.keys()) == ["bundle", "capabilities"]
    assert sorted(root.commands["queue"].commands.keys()) == [
        "approvals",
        "bundle",
        "cancel",
        "health",
        "health-reset",
        "init",
        "lock",
        "pause",
        "prune",
        "reconcile",
        "resume",
        "retry",
        "status",
        "unlock",
    ]


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_cli_doctor_help_surface_snapshot():
    runner = CliRunner()

    result = runner.invoke(cli.app, ["doctor", "--help"], color=False)

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    expected_fragments = [
        "--self-test",
        "--quick",
        "--timeout-s",
        "Run provider capability tests and write a report.",
    ]
    for fragment in expected_fragments:
        assert fragment in help_text


def test_cli_queue_status_help_surface_snapshot():
    runner = CliRunner()

    result = runner.invoke(cli.app, ["queue", "status", "--help"], color=False)

    assert result.exit_code == 0
    help_text = _strip_ansi(result.stdout)
    expected_fragments = [
        "Usage:",
        "queue status",
        "--queue-dir",
    ]
    for fragment in expected_fragments:
        assert fragment in help_text


def test_cli_compatibility_exports_remain_reachable():
    assert callable(cli.log)
    assert callable(cli.tail)
    assert cli.console is not None
    assert callable(cli.get_version)
    assert callable(cli._git_sha)
    assert callable(cli.load_runtime_config)
    assert cli.MissionQueueDaemon is not None
