import json

import pytest

import voxera.core.missions as missions_module
from voxera.core.missions import MissionRunner, get_mission, list_missions
from voxera.models import PolicyApprovals
from voxera.skills.registry import SkillRegistry
from voxera.skills.runner import SkillRunner


def test_list_missions_contains_daily_cards_and_system_check():
    mission_ids = {m.id for m in list_missions()}
    assert {
        "work_mode",
        "focus_mode",
        "daily_checkin",
        "incident_mode",
        "wrap_up",
        "system_check",
        "notes_archive_flow",
        "system_inspect",
        "system_diagnostics",
    }.issubset(mission_ids)


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
    assert len(rr.data["results"]) == 2
    assert any(item.get("skill") == "files.write_text" for item in rr.data["results"])


def test_mission_runner_appends_redacted_log(tmp_path):
    reg = SkillRegistry()
    reg.discover()
    runner = SkillRunner(reg)
    log_path = tmp_path / "mission-log.md"
    mission_runner = MissionRunner(
        runner,
        policy=PolicyApprovals(),
        redact_logs=True,
        mission_log_path=log_path,
    )

    rr = mission_runner.run(get_mission("system_check"))

    assert rr.ok is True
    content = log_path.read_text(encoding="utf-8")
    assert "system_check" in content
    assert "details:" not in content


def test_mission_runner_appends_unredacted_log_details(tmp_path):
    reg = SkillRegistry()
    reg.discover()
    runner = SkillRunner(reg)
    log_path = tmp_path / "mission-log.md"
    mission_runner = MissionRunner(
        runner,
        policy=PolicyApprovals(),
        redact_logs=False,
        mission_log_path=log_path,
    )

    rr = mission_runner.run(get_mission("system_check"))

    assert rr.ok is True
    content = log_path.read_text(encoding="utf-8")
    assert "details:" in content
    assert "system.status" in content


def test_get_mission_loads_file_based_json_mission(tmp_path, monkeypatch):
    mission_dir = tmp_path / "missions"
    mission_dir.mkdir(parents=True)
    mission_path = mission_dir / "custom_mission.json"
    mission_path.write_text(
        json.dumps(
            {
                "title": "Custom Mission",
                "goal": "Run system status via file mission",
                "notes": ["line one", "line two"],
                "steps": [{"skill_id": "system.status", "args": {}}],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(missions_module, "_mission_search_dirs", lambda: [mission_dir])

    mission = get_mission("custom_mission")
    assert mission.id == "custom_mission"
    assert mission.title == "Custom Mission"
    assert mission.notes == "line one\nline two"
    assert mission.steps[0].skill_id == "system.status"


def test_get_mission_prefers_hardcoded_template_over_file(tmp_path, monkeypatch):
    mission_dir = tmp_path / "missions"
    mission_dir.mkdir(parents=True)
    (mission_dir / "system_check.json").write_text(
        json.dumps(
            {
                "title": "Fake",
                "goal": "Should not override built-in",
                "steps": [
                    {"skill_id": "sandbox.exec", "args": {"command": ["bash", "-lc", "echo nope"]}}
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(missions_module, "_mission_search_dirs", lambda: [mission_dir])

    mission = get_mission("system_check")
    assert mission.title == "System Check"
    assert mission.steps[0].skill_id == "system.status"


def test_list_missions_stable_order_hardcoded_then_file(tmp_path, monkeypatch):
    mission_dir = tmp_path / "missions"
    mission_dir.mkdir(parents=True)
    (mission_dir / "zeta.json").write_text(
        json.dumps({"steps": [{"skill_id": "system.status", "args": {}}]}),
        encoding="utf-8",
    )
    (mission_dir / "alpha.json").write_text(
        json.dumps({"steps": [{"skill": "system.status", "args": {}}]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(missions_module, "_mission_search_dirs", lambda: [mission_dir])

    mission_ids = [mission.id for mission in list_missions()]
    hardcoded_prefix = list(missions_module.MISSION_TEMPLATES.keys())
    assert mission_ids[: len(hardcoded_prefix)] == hardcoded_prefix
    assert mission_ids[-2:] == ["alpha", "zeta"]


def test_get_mission_rejects_invalid_file_with_path(tmp_path, monkeypatch):
    mission_dir = tmp_path / "missions"
    mission_dir.mkdir(parents=True)
    invalid_path = mission_dir / "bad.json"
    invalid_path.write_text(
        json.dumps({"steps": [{"args": {}}]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(missions_module, "_mission_search_dirs", lambda: [mission_dir])

    with pytest.raises(
        ValueError, match=r"Invalid mission file .*bad\.json: step 1 missing non-empty skill_id"
    ):
        get_mission("bad")


def test_mission_runner_propagates_structured_skill_result_fields():
    reg = SkillRegistry()
    reg.discover()
    runner = SkillRunner(reg)
    mission_runner = MissionRunner(runner, policy=PolicyApprovals(network_changes="allow"))

    mission = missions_module.MissionTemplate(
        id="structured_open_url",
        title="Structured Open URL",
        goal="Exercise structured skill result propagation",
        steps=[missions_module.MissionStep(skill_id="system.open_url", args={"url": "ftp://bad"})],
    )

    rr = mission_runner.run(mission)
    assert rr.ok is False
    step = rr.data["results"][0]
    assert step["summary"] == "Rejected non-http(s) URL"
    assert step["next_action_hint"] == "provide_supported_url"
    assert step["operator_note"] == "Use an http:// or https:// URL."
    assert step["retryable"] is False
    assert step["error_class"] == "invalid_input"
