import json

from typer.testing import CliRunner

from voxera import cli


def test_queue_init_creates_expected_directories(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"

    result = runner.invoke(cli.app, ["queue", "init", "--queue-dir", str(queue_dir)])

    assert result.exit_code == 0
    assert queue_dir.exists()
    assert (queue_dir / "pending").exists()
    assert (queue_dir / "pending" / "approvals").exists()
    assert (queue_dir / "done").exists()
    assert (queue_dir / "failed").exists()


def test_queue_init_is_idempotent(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    (queue_dir / "done").mkdir(parents=True)

    first = runner.invoke(cli.app, ["queue", "init", "--queue-dir", str(queue_dir)])
    second = runner.invoke(cli.app, ["queue", "init", "--queue-dir", str(queue_dir)])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert (queue_dir / "pending" / "approvals").exists()
    assert (queue_dir / "done").exists()
    assert (queue_dir / "failed").exists()


def test_queue_approval_list_job_value_can_be_used_with_approve(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    (queue_dir / "pending" / "approvals").mkdir(parents=True)

    (queue_dir / "pending" / "job-e2e-ask.json").write_text(
        json.dumps({"goal": "demo"}), encoding="utf-8"
    )
    (queue_dir / "pending" / "job-e2e-ask.pending.json").write_text(
        json.dumps(
            {
                "payload": {"goal": "demo"},
                "resume_step": 1,
                "mission": {
                    "id": "demo",
                    "title": "Demo",
                    "goal": "demo",
                    "steps": [{"skill_id": "system.status", "args": {}}],
                },
            }
        ),
        encoding="utf-8",
    )
    (queue_dir / "pending" / "approvals" / "job-e2e-ask.approval.json").write_text(
        json.dumps(
            {
                "job": "job-e2e-ask.json",
                "step": 1,
                "skill": "system.open_url",
                "reason": "needs approval",
            }
        ),
        encoding="utf-8",
    )

    listed = runner.invoke(cli.app, ["queue", "approvals", "list", "--queue-dir", str(queue_dir)])

    assert listed.exit_code == 0
    assert "e2e-ask" in listed.output

    approved = runner.invoke(
        cli.app,
        ["queue", "approvals", "approve", "job-e2e-ask.json", "--queue-dir", str(queue_dir)],
    )

    assert approved.exit_code == 0
    assert (queue_dir / "done" / "job-e2e-ask.json").exists()
