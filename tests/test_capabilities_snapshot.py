import json

from typer.testing import CliRunner

from voxera import cli
from voxera.core.capabilities_snapshot import generate_capabilities_snapshot
from voxera.skills.registry import SkillRegistry


def test_generate_capabilities_snapshot_has_schema_and_deterministic_order(monkeypatch):
    monkeypatch.setattr("voxera.core.capabilities_snapshot.time.time", lambda: 1700000000.0)
    reg = SkillRegistry()

    snapshot = generate_capabilities_snapshot(reg)

    assert snapshot["schema_version"] == 1
    assert snapshot["generated_ts_ms"] == 1700000000000
    mission_ids = [item["id"] for item in snapshot["missions"]]
    assert mission_ids == sorted(mission_ids)
    assert snapshot["allowed_apps"] == sorted(snapshot["allowed_apps"])
    skill_ids = [item["id"] for item in snapshot["skills"]]
    assert skill_ids == sorted(skill_ids)


def test_ops_capabilities_command_prints_stable_json(monkeypatch):
    monkeypatch.setattr("voxera.core.capabilities_snapshot.time.time", lambda: 1700000000.0)
    runner = CliRunner()

    result = runner.invoke(cli.app, ["ops", "capabilities"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["generated_ts_ms"] == 1700000000000
    assert payload["allowed_apps"] == sorted(payload["allowed_apps"])
