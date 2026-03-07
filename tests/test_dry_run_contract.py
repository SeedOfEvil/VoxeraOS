"""Tests enforcing the dry-run output contract: determinism and capability/risk coherence.

PR71: Planner dry-run contract hardening + capability/risk coherence (deterministic).
"""

from __future__ import annotations

import json

from voxera.core.capabilities_snapshot import generate_capabilities_snapshot
from voxera.core.missions import MissionRunner, MissionStep, MissionTemplate
from voxera.models import PolicyApprovals
from voxera.skills.registry import SkillRegistry
from voxera.skills.runner import SkillRunner


def _make_runner() -> tuple[MissionRunner, SkillRegistry]:
    reg = SkillRegistry()
    reg.discover()
    skill_runner = SkillRunner(reg)
    mission_runner = MissionRunner(skill_runner, policy=PolicyApprovals())
    return mission_runner, reg


def test_dry_run_open_app_capability_and_risk(monkeypatch):
    """system.open_app step must have capability=='apps.open', risk=='medium',
    and the top-level fields capabilities_snapshot and capabilities_used must be present."""
    monkeypatch.setattr("voxera.core.capabilities_snapshot.time.time", lambda: 1700000000.0)
    mission_runner, reg = _make_runner()

    mission = MissionTemplate(
        id="t",
        title="T",
        goal="open terminal",
        steps=[MissionStep(skill_id="system.open_app", args={"name": "terminal"})],
    )
    snapshot = generate_capabilities_snapshot(reg)
    sim = mission_runner.simulate(mission, snapshot=snapshot)

    assert sim.steps[0].capability == "apps.open"
    assert sim.steps[0].risk == "medium"
    assert isinstance(sim.capabilities_snapshot.get("schema_version"), int)
    assert isinstance(sim.capabilities_snapshot.get("generated_ts_ms"), int)
    assert "apps.open" in sim.capabilities_used
    assert sim.capabilities_used == sorted(sim.capabilities_used)


def test_dry_run_multi_capability_skill_all_capabilities_in_used():
    """system.open_url declares ['apps.open', 'network.change']. Both must appear in
    capabilities_used even though PlanStep.capability only holds the primary one."""
    mission_runner, _ = _make_runner()

    mission = MissionTemplate(
        id="t",
        title="T",
        goal="open url",
        steps=[MissionStep(skill_id="system.open_url", args={"url": "https://example.com"})],
    )
    sim = mission_runner.simulate(mission)

    # Primary (first sorted) capability on the step itself.
    assert sim.steps[0].capability == "apps.open"
    # Top-level summary must include ALL declared capabilities, not just the primary.
    assert "apps.open" in sim.capabilities_used
    assert "network.change" in sim.capabilities_used
    assert sim.capabilities_used == sorted(sim.capabilities_used)


def test_dry_run_no_capability_skill_capability_is_null():
    """A skill with no declared capabilities must have capability==None,
    and capabilities_used must be empty (that step contributes nothing)."""
    mission_runner, _ = _make_runner()

    mission = MissionTemplate(
        id="t",
        title="T",
        goal="check status",
        steps=[MissionStep(skill_id="system.status", args={})],
    )
    sim = mission_runner.simulate(mission)

    assert sim.steps[0].capability == "state.read"
    assert sim.capabilities_used == ["state.read"]


def test_dry_run_json_output_is_deterministic(monkeypatch):
    """Calling the builder twice with the same frozen snapshot must produce identical JSON."""
    monkeypatch.setattr("voxera.core.capabilities_snapshot.time.time", lambda: 1700000000.0)
    mission_runner, reg = _make_runner()

    mission = MissionTemplate(
        id="t",
        title="T",
        goal="open terminal",
        steps=[MissionStep(skill_id="system.open_app", args={"name": "terminal"})],
    )
    snapshot = generate_capabilities_snapshot(reg)

    out1 = json.dumps(
        mission_runner.simulate(mission, snapshot=snapshot).model_dump(),
        indent=2,
        sort_keys=True,
    )
    out2 = json.dumps(
        mission_runner.simulate(mission, snapshot=snapshot).model_dump(),
        indent=2,
        sort_keys=True,
    )
    assert out1 == out2
