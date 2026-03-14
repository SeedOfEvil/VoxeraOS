"""Focused tests for the system_inspect read-only inspection workflow.

Covers:
- New read-only skills: disk_usage, process_list
- system_inspect mission composition
- Simulation (dry-run) for inspection workflow
- Queue contract / request_kind propagation
- Evidence / review output readiness
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from voxera.core.missions import MissionRunner, get_mission, list_missions
from voxera.core.queue_contracts import detect_request_kind
from voxera.core.queue_job_intent import build_queue_job_intent
from voxera.models import PolicyApprovals
from voxera.skills.registry import SkillRegistry
from voxera.skills.result_contract import SKILL_RESULT_KEY
from voxera.skills.runner import SkillRunner, is_skill_read_only
from voxera_builtin_skills import disk_usage, process_list

_ORIGINAL_CHECK_OUTPUT = subprocess.check_output


def _mock_check_output_wmctrl(cmd, **kwargs):
    """Mock subprocess.check_output: fake wmctrl, pass through everything else."""
    if isinstance(cmd, list) and cmd and cmd[0] == "wmctrl":
        return "0x01  0 user  Terminal\n0x02  0 user  Firefox\n"
    return _ORIGINAL_CHECK_OUTPUT(cmd, **kwargs)


def _run_inspect_mission():
    """Run the system_inspect mission with wmctrl mocked."""
    reg = SkillRegistry()
    reg.discover()
    runner = SkillRunner(reg)
    mission_runner = MissionRunner(runner, policy=PolicyApprovals())
    with patch("subprocess.check_output", side_effect=_mock_check_output_wmctrl):
        return mission_runner.run(get_mission("system_inspect"))


# ---------------------------------------------------------------------------
# disk_usage skill
# ---------------------------------------------------------------------------


def test_disk_usage_returns_structured_success():
    rr = disk_usage.run()
    assert rr.ok is True
    payload = rr.data[SKILL_RESULT_KEY]
    assert "Disk usage" in payload["summary"]
    assert isinstance(payload["machine_payload"], dict)
    assert "total_bytes" in payload["machine_payload"]
    assert "used_percent" in payload["machine_payload"]
    assert payload["approval_status"] == "none"
    assert payload["retryable"] is False
    assert payload["blocked"] is False


def test_disk_usage_machine_payload_has_expected_fields():
    rr = disk_usage.run()
    mp = rr.data[SKILL_RESULT_KEY]["machine_payload"]
    for field in (
        "total_bytes",
        "used_bytes",
        "free_bytes",
        "total_gb",
        "used_gb",
        "free_gb",
        "used_percent",
        "mount_path",
    ):
        assert field in mp, f"missing field: {field}"


# ---------------------------------------------------------------------------
# process_list skill
# ---------------------------------------------------------------------------


def test_process_list_returns_structured_success():
    rr = process_list.run()
    assert rr.ok is True
    payload = rr.data[SKILL_RESULT_KEY]
    assert "processes" in payload["summary"].lower()
    assert isinstance(payload["machine_payload"], dict)
    assert "count" in payload["machine_payload"]
    assert isinstance(payload["machine_payload"]["processes"], list)
    assert payload["approval_status"] == "none"


def test_process_list_truncates_at_50():
    rr = process_list.run()
    mp = rr.data[SKILL_RESULT_KEY]["machine_payload"]
    assert len(mp["processes"]) <= 50


def test_process_list_missing_ps_returns_structured_error():
    with patch("subprocess.check_output", side_effect=FileNotFoundError):
        rr = process_list.run()
    assert rr.ok is False
    payload = rr.data[SKILL_RESULT_KEY]
    assert payload["error_class"] == "missing_dependency"
    assert payload["retryable"] is False


# ---------------------------------------------------------------------------
# system_inspect mission
# ---------------------------------------------------------------------------


def test_system_inspect_in_mission_list():
    mission_ids = {m.id for m in list_missions()}
    assert "system_inspect" in mission_ids


def test_system_inspect_mission_is_read_only():
    """Every skill in system_inspect must be read-only."""
    reg = SkillRegistry()
    reg.discover()
    mission = get_mission("system_inspect")
    for step in mission.steps:
        manifest = reg.get(step.skill_id)
        assert is_skill_read_only(manifest), f"{step.skill_id} is not read-only"


def test_system_inspect_mission_has_four_steps():
    mission = get_mission("system_inspect")
    assert len(mission.steps) == 4
    skill_ids = [s.skill_id for s in mission.steps]
    assert skill_ids == [
        "system.status",
        "system.disk_usage",
        "system.process_list",
        "system.window_list",
    ]


def test_simulate_system_inspect_requires_no_approvals():
    reg = SkillRegistry()
    reg.discover()
    runner = SkillRunner(reg)
    mission_runner = MissionRunner(runner, policy=PolicyApprovals())

    sim = mission_runner.simulate(get_mission("system_inspect"))

    assert sim.blocked is False
    assert sim.approvals_required == 0
    assert len(sim.steps) == 4


def test_run_system_inspect_mission_succeeds():
    rr = _run_inspect_mission()

    assert rr.ok is True
    assert "Mission completed" in rr.output
    results = rr.data["results"]
    assert len(results) == 4
    for step in results:
        assert step["ok"] is True, f"step {step['skill']} failed: {step.get('error')}"
    assert [s["skill"] for s in results] == [
        "system.status",
        "system.disk_usage",
        "system.process_list",
        "system.window_list",
    ]


def test_system_inspect_produces_step_outcomes():
    rr = _run_inspect_mission()

    assert rr.ok is True
    step_outcomes = rr.data["step_outcomes"]
    assert len(step_outcomes) == 4
    for outcome in step_outcomes:
        assert outcome["outcome"] == "succeeded"


def test_system_inspect_results_contain_summaries_and_payloads():
    rr = _run_inspect_mission()

    for step in rr.data["results"]:
        assert step["summary"], f"step {step['skill']} missing summary"
        assert isinstance(step["machine_payload"], dict), (
            f"step {step['skill']} missing machine_payload"
        )


def test_system_inspect_lifecycle_state_is_done():
    rr = _run_inspect_mission()

    assert rr.data["lifecycle_state"] == "done"
    assert rr.data["terminal_outcome"] == "succeeded"
    assert rr.data["total_steps"] == 4


# ---------------------------------------------------------------------------
# Queue contract integration
# ---------------------------------------------------------------------------


def test_detect_request_kind_for_system_inspect():
    payload = {"mission_id": "system_inspect"}
    assert detect_request_kind(payload) == "mission_id"


def test_build_queue_job_intent_for_system_inspect():
    payload = {
        "mission_id": "system_inspect",
        "title": "System Inspection",
        "goal": "Bounded read-only workstation health snapshot",
    }
    intent = build_queue_job_intent(payload, source_lane="queue")
    assert intent["request_kind"] == "mission_id"
    assert intent["mission_id"] == "system_inspect"
    assert intent["title"] == "System Inspection"
    assert isinstance(intent["expected_artifacts"], list)
    assert len(intent["expected_artifacts"]) > 0


# ---------------------------------------------------------------------------
# Skill registry classification
# ---------------------------------------------------------------------------


def test_disk_usage_skill_manifest_is_read_only():
    reg = SkillRegistry()
    reg.discover()
    manifest = reg.get("system.disk_usage")
    assert manifest.risk == "low"
    assert manifest.needs_network is False
    assert manifest.fs_scope == "read_only"
    assert is_skill_read_only(manifest)


def test_process_list_skill_manifest_is_read_only():
    reg = SkillRegistry()
    reg.discover()
    manifest = reg.get("system.process_list")
    assert manifest.risk == "low"
    assert manifest.needs_network is False
    assert manifest.fs_scope == "read_only"
    assert is_skill_read_only(manifest)
