from voxera.core.missions import MissionRunner, get_mission, list_missions
from voxera.models import PolicyApprovals
from voxera.skills.registry import SkillRegistry
from voxera.skills.runner import SkillRunner


def test_list_missions_contains_work_mode_and_system_check():
    mission_ids = {m.id for m in list_missions()}
    assert "work_mode" in mission_ids
    assert "system_check" in mission_ids


def test_simulate_work_mode_requires_approval_for_system_settings():
    reg = SkillRegistry()
    reg.discover()
    runner = SkillRunner(reg)
    mission_runner = MissionRunner(runner, policy=PolicyApprovals(system_settings="ask"))

    sim = mission_runner.simulate(get_mission("work_mode"))

    assert sim.blocked is False
    assert sim.approvals_required == 1
    assert len(sim.steps) == 3


def test_run_system_check_mission_succeeds():
    reg = SkillRegistry()
    reg.discover()
    runner = SkillRunner(reg)
    mission_runner = MissionRunner(runner, policy=PolicyApprovals())

    rr = mission_runner.run(get_mission("system_check"))

    assert rr.ok is True
    assert "Mission completed" in rr.output
    assert len(rr.data["results"]) == 1
