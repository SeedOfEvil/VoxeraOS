from __future__ import annotations

import json

from typer.testing import CliRunner

from voxera import cli
from voxera.core.queue_daemon import QueueLockError


def test_queue_status_empty_shows_structural_sections(tmp_path):
    runner = CliRunner()

    result = runner.invoke(cli.app, ["queue", "status", "--queue-dir", str(tmp_path / "missing")])

    assert result.exit_code == 0
    assert "Queue Status" in result.stdout
    assert "Pending Approvals" in result.stdout
    assert "No pending approvals" in result.stdout
    # Lifecycle table title rendering can vary by Rich terminal width/box mode;
    # assert durable empty-state marker instead.
    assert "No jobs" in result.stdout
    assert "Recent Failed Jobs" in result.stdout
    assert "No failed jobs" in result.stdout
    assert "Hint:" in result.stdout
    assert "queue root not found yet" in result.stdout


def test_queue_status_with_approval_and_failed_job_surfaces_operator_truth(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    (queue_dir / "pending" / "approvals").mkdir(parents=True)
    (queue_dir / "pending").mkdir(parents=True, exist_ok=True)
    (queue_dir / "failed").mkdir(parents=True)
    (queue_dir / "pending" / "job-review.json").write_text('{"goal":"demo"}', encoding="utf-8")
    (queue_dir / "pending" / "job-review.pending.json").write_text(
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

    (queue_dir / "pending" / "approvals" / "job-review.approval.json").write_text(
        json.dumps(
            {
                "job": "job-review.json",
                "step": 2,
                "skill": "system.open_url",
                "policy_reason": "network_changes -> ask",
                "target": {"type": "url", "value": "https://example.com"},
                "scope": {"fs_scope": "workspace_only", "needs_network": True},
            }
        ),
        encoding="utf-8",
    )
    (queue_dir / "failed" / "job-failed.json").write_text("{}", encoding="utf-8")
    (queue_dir / "failed" / "job-failed.error.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "job": "job-failed.json",
                "error": "runtime failed",
                "timestamp_ms": 1700000000000,
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(cli.app, ["queue", "status", "--queue-dir", str(queue_dir)])

    assert result.exit_code == 0
    assert "pending/approvals/" in result.stdout
    assert "network_changes -> ask" in result.stdout
    assert "https://example.com" in result.stdout
    assert "workspace_only" in result.stdout
    assert "system.open_url" in result.stdout
    assert "job-failed.json" in result.stdout
    assert "runtime failed" in result.stdout


def test_queue_lifecycle_commands_cover_success_and_fail_closed_paths(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    (queue_dir / "inbox").mkdir(parents=True)
    (queue_dir / "inbox" / "job-1.json").write_text('{"goal":"demo"}', encoding="utf-8")

    canceled = runner.invoke(
        cli.app, ["queue", "cancel", "job-1.json", "--queue-dir", str(queue_dir)]
    )
    assert canceled.exit_code == 0
    assert "Cancelled: job-1.json" in canceled.stdout
    assert (queue_dir / "canceled" / "job-1.json").exists()

    retried = runner.invoke(
        cli.app, ["queue", "retry", "job-1.json", "--queue-dir", str(queue_dir)]
    )
    assert retried.exit_code == 0
    assert "Re-queued: job-1.json" in retried.stdout
    assert (queue_dir / "inbox" / "job-1.json").exists()

    missing_cancel = runner.invoke(
        cli.app, ["queue", "cancel", "missing-job.json", "--queue-dir", str(queue_dir)]
    )
    assert missing_cancel.exit_code == 1
    assert "ERROR:" in missing_cancel.stdout

    missing_retry = runner.invoke(
        cli.app, ["queue", "retry", "missing-job.json", "--queue-dir", str(queue_dir)]
    )
    assert missing_retry.exit_code == 1
    assert "ERROR:" in missing_retry.stdout


def test_queue_pause_resume_and_unlock_contracts(tmp_path, monkeypatch):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"

    paused = runner.invoke(cli.app, ["queue", "pause", "--queue-dir", str(queue_dir)])
    resumed = runner.invoke(cli.app, ["queue", "resume", "--queue-dir", str(queue_dir)])

    assert paused.exit_code == 0
    assert resumed.exit_code == 0
    assert "Queue processing paused." in paused.stdout
    assert "Queue processing resumed." in resumed.stdout

    no_lock = runner.invoke(cli.app, ["queue", "unlock", "--queue-dir", str(queue_dir)])
    assert no_lock.exit_code == 0
    assert "No daemon lock was present." in no_lock.stdout

    monkeypatch.setattr(
        "voxera.cli_queue.MissionQueueDaemon.try_unlock_stale",
        lambda _self: (_ for _ in ()).throw(QueueLockError("lock held by active daemon")),
    )
    refused = runner.invoke(cli.app, ["queue", "unlock", "--queue-dir", str(queue_dir)])
    assert refused.exit_code == 1
    assert "ERROR:" in refused.stdout
    assert "lock held by active daemon" in refused.stdout


def test_queue_approvals_help_and_fail_closed_behavior(tmp_path):
    runner = CliRunner()

    help_result = runner.invoke(cli.app, ["queue", "approvals", "--help"])
    assert help_result.exit_code == 0
    assert "list" in help_result.stdout
    assert "approve" in help_result.stdout
    assert "deny" in help_result.stdout

    queue_dir = tmp_path / "queue"
    missing_approve = runner.invoke(
        cli.app,
        ["queue", "approvals", "approve", "missing.json", "--queue-dir", str(queue_dir)],
    )
    assert missing_approve.exit_code == 1
    assert "ERROR:" in missing_approve.stdout

    missing_deny = runner.invoke(
        cli.app,
        ["queue", "approvals", "deny", "missing.json", "--queue-dir", str(queue_dir)],
    )
    assert missing_deny.exit_code == 1
    assert "ERROR:" in missing_deny.stdout


def test_queue_approvals_approve_reports_still_pending_when_daemon_returns_false(
    tmp_path, monkeypatch
):
    runner = CliRunner()

    monkeypatch.setattr(
        "voxera.cli_queue.MissionQueueDaemon.resolve_approval",
        lambda _self, _ref, approve, approve_always=False: False,
    )

    result = runner.invoke(
        cli.app,
        ["queue", "approvals", "approve", "job-a.json", "--queue-dir", str(tmp_path / "queue")],
    )

    assert result.exit_code == 0
    assert "job still pending another approval" in result.stdout


def test_queue_approvals_deny_moves_job_to_failed(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    (queue_dir / "pending" / "approvals").mkdir(parents=True)
    (queue_dir / "pending" / "job-deny.json").write_text('{"goal":"demo"}', encoding="utf-8")
    (queue_dir / "pending" / "job-deny.pending.json").write_text(
        json.dumps(
            {"mission": {"id": "demo", "steps": [{"skill_id": "system.status", "args": {}}]}}
        ),
        encoding="utf-8",
    )
    (queue_dir / "pending" / "approvals" / "job-deny.approval.json").write_text(
        json.dumps(
            {"job": "job-deny.json", "step": 1, "skill": "system.open_url", "reason": "ask"}
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        cli.app,
        ["queue", "approvals", "deny", "job-deny.json", "--queue-dir", str(queue_dir)],
    )

    assert result.exit_code == 0
    assert "Denied. Job moved to failed/." in result.stdout
    assert (queue_dir / "failed" / "job-deny.json").exists()


def test_inbox_help_shape_and_queue_backed_add_list(tmp_path):
    runner = CliRunner()

    help_result = runner.invoke(cli.app, ["inbox", "--help"])
    assert help_result.exit_code == 0
    assert "add" in help_result.stdout
    assert "list" in help_result.stdout

    queue_dir = tmp_path / "queue"
    created = runner.invoke(
        cli.app,
        ["inbox", "add", "Open status", "--id", "in-1", "--queue-dir", str(queue_dir)],
    )
    listed = runner.invoke(cli.app, ["inbox", "list", "--queue-dir", str(queue_dir)])

    assert created.exit_code == 0
    assert "Created inbox job:" in created.stdout
    assert "ID: in-1" in created.stdout
    assert (queue_dir / "inbox" / "inbox-in-1.json").exists()

    assert listed.exit_code == 0
    assert "Inbox Jobs" in listed.stdout
    assert "inbox-in-1.json" in listed.stdout
    assert "Open status" in listed.stdout


def test_inbox_add_fail_closed_on_malformed_goal(tmp_path):
    runner = CliRunner()

    result = runner.invoke(
        cli.app,
        ["inbox", "add", "   ", "--queue-dir", str(tmp_path / "queue")],
    )

    assert result.exit_code == 1
    assert "ERROR:" in result.stdout
