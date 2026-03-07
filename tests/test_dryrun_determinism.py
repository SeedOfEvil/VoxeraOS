"""Tests for --deterministic dry-run mode (PR #72).

Proves byte-identical JSON output when --deterministic is active,
timestamp scrubbing, no-capability skill behavior, and that both flags
are rejected when --dry-run is not supplied.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from voxera import cli
from voxera.core.capabilities_snapshot import generate_capabilities_snapshot
from voxera.core.missions import (
    MissionRunner,
    MissionStep,
    MissionTemplate,
    _make_dryrun_deterministic,
)
from voxera.models import PolicyApprovals
from voxera.skills.registry import SkillRegistry
from voxera.skills.runner import SkillRunner

_cli_runner = CliRunner()


def _make_runner() -> tuple[MissionRunner, SkillRegistry]:
    reg = SkillRegistry()
    reg.discover()
    skill_runner = SkillRunner(reg)
    mission_runner = MissionRunner(skill_runner, policy=PolicyApprovals())
    return mission_runner, reg


def test_deterministic_two_runs_byte_identical():
    """Running simulate twice with _make_dryrun_deterministic applied must produce identical JSON."""
    mission_runner, reg = _make_runner()
    mission = MissionTemplate(
        id="t",
        title="T",
        goal="open terminal",
        steps=[MissionStep(skill_id="system.open_app", args={"name": "terminal"})],
    )

    def _run() -> str:
        snapshot = generate_capabilities_snapshot(reg)
        out = mission_runner.simulate(mission, snapshot=snapshot).model_dump()
        _make_dryrun_deterministic(out)
        return json.dumps(out, indent=2, sort_keys=True)

    assert _run() == _run()


def test_deterministic_scrubs_generated_ts_ms():
    """_make_dryrun_deterministic must zero out capabilities_snapshot.generated_ts_ms."""
    mission_runner, reg = _make_runner()
    mission = MissionTemplate(
        id="t",
        title="T",
        goal="open terminal",
        steps=[MissionStep(skill_id="system.open_app", args={"name": "terminal"})],
    )
    snapshot = generate_capabilities_snapshot(reg)
    out = mission_runner.simulate(mission, snapshot=snapshot).model_dump()
    _make_dryrun_deterministic(out)
    assert out["capabilities_snapshot"]["generated_ts_ms"] == 0


def test_non_deterministic_mode_preserves_real_ts(monkeypatch):
    """Without _make_dryrun_deterministic, generated_ts_ms must be a real timestamp."""
    monkeypatch.setattr("voxera.core.capabilities_snapshot.time.time", lambda: 1700000000.0)
    mission_runner, reg = _make_runner()
    mission = MissionTemplate(
        id="t",
        title="T",
        goal="open terminal",
        steps=[MissionStep(skill_id="system.open_app", args={"name": "terminal"})],
    )
    snapshot = generate_capabilities_snapshot(reg)
    out = mission_runner.simulate(mission, snapshot=snapshot).model_dump()
    assert out["capabilities_snapshot"]["generated_ts_ms"] == 1700000000000


def test_deterministic_no_capability_skill():
    """system.status in deterministic mode: capabilities_used==[] and step capability is None."""
    mission_runner, reg = _make_runner()
    mission = MissionTemplate(
        id="t",
        title="T",
        goal="system status",
        steps=[MissionStep(skill_id="system.status", args={})],
    )
    snapshot = generate_capabilities_snapshot(reg)
    out = mission_runner.simulate(mission, snapshot=snapshot).model_dump()
    _make_dryrun_deterministic(out)
    assert out["capabilities_used"] == ["state.read"]
    assert out["steps"][0]["capability"] == "state.read"


def test_deterministic_without_dry_run_rejected():
    """--deterministic without --dry-run must exit 1 with a clear error."""
    result = _cli_runner.invoke(cli.app, ["missions", "plan", "open terminal", "--deterministic"])
    assert result.exit_code == 1
    assert "--dry-run" in result.output


def test_freeze_snapshot_without_dry_run_rejected():
    """--freeze-capabilities-snapshot without --dry-run must exit 1 with a clear error."""
    result = _cli_runner.invoke(
        cli.app, ["missions", "plan", "open terminal", "--freeze-capabilities-snapshot"]
    )
    assert result.exit_code == 1
    assert "--dry-run" in result.output
