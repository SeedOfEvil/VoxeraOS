from __future__ import annotations

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


def test_cli_doctor_help_surface_snapshot():
    runner = CliRunner()

    result = runner.invoke(cli.app, ["doctor", "--help"])

    assert result.exit_code == 0
    expected_fragments = [
        "--self-test",
        "--quick",
        "--timeout-s",
        "Run provider capability tests and write a report.",
    ]
    for fragment in expected_fragments:
        assert fragment in result.stdout


def test_cli_queue_status_help_surface_snapshot():
    runner = CliRunner()

    result = runner.invoke(cli.app, ["queue", "status", "--help"])

    assert result.exit_code == 0
    expected_fragments = [
        "Usage: root queue status",
        "--queue-dir",
    ]
    for fragment in expected_fragments:
        assert fragment in result.stdout
