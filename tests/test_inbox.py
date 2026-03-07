import json

import pytest
from typer.testing import CliRunner

from voxera import cli
from voxera.core.inbox import add_inbox_job, generate_inbox_id
from voxera.core.missions import MissionStep, MissionTemplate
from voxera.core.queue_daemon import MissionQueueDaemon
from voxera.models import AppConfig, PolicyApprovals, PrivacyConfig


def _force_policy_ask(monkeypatch):
    cfg = AppConfig(
        policy=PolicyApprovals(system_settings="ask", network_changes="ask"),
        privacy=PrivacyConfig(redact_logs=True),
    )
    monkeypatch.setattr("voxera.core.queue_daemon.load_config", lambda: cfg)


def _stub_planner(monkeypatch):
    async def _fake_plan(goal, cfg, registry, source="cli", job_ref=None, **_kwargs):
        return MissionTemplate(
            id="cloud_planned",
            title="Stub Plan",
            goal=goal,
            steps=[MissionStep(skill_id="system.status", args={})],
            notes="stub",
        )

    monkeypatch.setattr("voxera.core.queue_daemon.plan_mission", _fake_plan)


def test_inbox_add_writes_goal_and_id(tmp_path):
    queue_dir = tmp_path / "queue"
    created = add_inbox_job(queue_dir, "Write daily check-in", job_id="alpha-1")

    payload = json.loads(created.read_text(encoding="utf-8"))
    assert created.name == "inbox-alpha-1.json"
    assert payload["id"] == "alpha-1"
    assert payload["goal"] == "Write daily check-in"
    assert payload["job_intent"]["request_kind"] == "goal"
    assert payload["job_intent"]["source_lane"] == "inbox_cli"


def test_inbox_add_rejects_duplicate_id_without_overwriting(tmp_path):
    queue_dir = tmp_path / "queue"
    created = add_inbox_job(queue_dir, "first goal", job_id="dup-1")

    with pytest.raises(FileExistsError):
        add_inbox_job(queue_dir, "second goal", job_id="dup-1")

    payload = json.loads(created.read_text(encoding="utf-8"))
    assert payload["id"] == "dup-1"
    assert payload["goal"] == "first goal"
    assert payload["job_intent"]["goal"] == "first goal"


def test_inbox_add_cli_reports_duplicate_id_error(tmp_path):
    queue_dir = tmp_path / "queue"
    runner = CliRunner()

    first = runner.invoke(
        cli.app, ["inbox", "add", "first goal", "--id", "dup-2", "--queue-dir", str(queue_dir)]
    )
    second = runner.invoke(
        cli.app, ["inbox", "add", "second goal", "--id", "dup-2", "--queue-dir", str(queue_dir)]
    )

    assert first.exit_code == 0
    assert second.exit_code == 1
    assert "inbox job already exists" in second.stdout


def test_generate_inbox_id_is_stable_for_goal_and_timestamp():
    job_id = generate_inbox_id("hello", now_ms=1730000000123)
    assert job_id == "1730000000123-7133abfa"


def test_inbox_list_handles_missing_dirs_with_hints(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli.app, ["inbox", "list", "--queue-dir", str(tmp_path / "missing")])

    assert result.exit_code == 0
    assert "No inbox jobs found" in result.stdout
    assert "Hint:" in result.stdout
    assert "missing directory" in result.stdout


def test_inbox_add_job_is_processed_by_queue_daemon(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_planner(monkeypatch)

    queue_dir = tmp_path / "queue"
    created = add_inbox_job(queue_dir, "Open status and report", job_id="goal-1")

    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    daemon.process_pending_once()

    assert not created.exists()
    assert (queue_dir / "done" / "inbox-goal-1.json").exists()
