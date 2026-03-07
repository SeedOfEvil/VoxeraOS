import json
import os
import time
from pathlib import Path

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
    assert (queue_dir / "canceled").exists()


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
    assert (queue_dir / "canceled").exists()


def test_queue_status_shows_canceled_bucket_count(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    (queue_dir / "canceled").mkdir(parents=True, exist_ok=True)
    (queue_dir / "canceled" / "job-a.json").write_text("{}", encoding="utf-8")
    (queue_dir / "canceled" / "job-b.json").write_text("{}", encoding="utf-8")

    result = runner.invoke(cli.app, ["queue", "status", "--queue-dir", str(queue_dir)])

    assert result.exit_code == 0
    assert "canceled/" in result.output
    assert "2" in result.output


def test_queue_status_shows_lifecycle_snapshot_from_state_sidecar(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    (queue_dir / "done").mkdir(parents=True, exist_ok=True)
    (queue_dir / "done" / "job-state.json").write_text("{}", encoding="utf-8")
    (queue_dir / "done" / "job-state.state.json").write_text(
        json.dumps(
            {
                "lifecycle_state": "done",
                "terminal_outcome": "succeeded",
                "current_step_index": 2,
                "total_steps": 2,
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(cli.app, ["queue", "status", "--queue-dir", str(queue_dir)])

    assert result.exit_code == 0
    assert "Job Lifecycle Snapshot" in result.output
    assert "done: done 2/2 · succeeded" in result.output


def test_queue_status_lifecycle_prefers_structured_execution_result(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    (queue_dir / "done").mkdir(parents=True, exist_ok=True)
    (queue_dir / "done" / "job-structured.json").write_text("{}", encoding="utf-8")
    art = queue_dir / "artifacts" / "job-structured"
    art.mkdir(parents=True, exist_ok=True)
    (art / "execution_result.json").write_text(
        json.dumps(
            {
                "lifecycle_state": "done",
                "terminal_outcome": "succeeded",
                "current_step_index": 3,
                "total_steps": 3,
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(cli.app, ["queue", "status", "--queue-dir", str(queue_dir)])

    assert result.exit_code == 0
    assert "done: done 3/3 · succeeded" in result.output


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


def test_queue_status_renders_failed_metadata_counters(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    (queue_dir / "failed").mkdir(parents=True)
    (queue_dir / "pending" / "approvals").mkdir(parents=True)
    (queue_dir / "done").mkdir(parents=True)

    (queue_dir / "failed" / "valid.json").write_text("{}", encoding="utf-8")
    (queue_dir / "failed" / "valid.error.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "job": "valid.json",
                "error": "ok",
                "timestamp_ms": 1700000000000,
            }
        ),
        encoding="utf-8",
    )
    (queue_dir / "failed" / "invalid.json").write_text("{}", encoding="utf-8")
    (queue_dir / "failed" / "invalid.error.json").write_text(
        json.dumps({"schema_version": 1, "job": "invalid.json"}), encoding="utf-8"
    )
    (queue_dir / "failed" / "missing.json").write_text("{}", encoding="utf-8")

    result = runner.invoke(cli.app, ["queue", "status", "--queue-dir", str(queue_dir)])

    assert result.exit_code == 0
    assert "failed metadata valid" in result.output
    assert "failed metadata invalid" in result.output
    assert "failed metadata missing" in result.output
    assert "failed retention max age (s)" in result.output
    assert "failed retention max count" in result.output


def test_queue_approvals_list_shows_target_and_scope(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    (queue_dir / "pending" / "approvals").mkdir(parents=True)
    (queue_dir / "pending" / "job-a.json").write_text(
        json.dumps({"goal": "demo"}), encoding="utf-8"
    )
    (queue_dir / "pending" / "job-a.pending.json").write_text(
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
    (queue_dir / "pending" / "approvals" / "job-a.approval.json").write_text(
        json.dumps(
            {
                "job": "job-a.json",
                "step": 1,
                "skill": "system.open_url",
                "reason": "needs approval",
                "policy_reason": "network_changes -> ask",
                "target": {"type": "url", "value": "https://example.com"},
                "scope": {"fs_scope": "workspace_only", "needs_network": True},
            }
        ),
        encoding="utf-8",
    )

    listed = runner.invoke(cli.app, ["queue", "approvals", "list", "--queue-dir", str(queue_dir)])

    assert listed.exit_code == 0
    assert "Target" in listed.output
    assert "Scope" in listed.output
    assert "Queue Approval Inbox" in listed.output


def test_queue_status_prints_artifacts_root(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    (queue_dir / "pending" / "approvals").mkdir(parents=True)
    (queue_dir / "done").mkdir(parents=True)
    (queue_dir / "failed").mkdir(parents=True)

    result = runner.invoke(cli.app, ["queue", "status", "--queue-dir", str(queue_dir)])

    assert result.exit_code == 0
    assert "Artifacts root:" in result.output


def test_queue_status_prints_intake_and_inbox(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    (queue_dir / "inbox").mkdir(parents=True)
    (queue_dir / "inbox" / "a.json").write_text("{}", encoding="utf-8")
    result = runner.invoke(cli.app, ["queue", "status", "--queue-dir", str(queue_dir)])
    assert result.exit_code == 0
    assert "Queue intake:" in result.output
    assert "inbox/" in result.output


def test_queue_cancel_retry_pause_resume_cli(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    (queue_dir / "inbox").mkdir(parents=True)
    (queue_dir / "inbox" / "job-x.json").write_text('{"goal":"g"}', encoding="utf-8")

    pause = runner.invoke(cli.app, ["queue", "pause", "--queue-dir", str(queue_dir)])
    assert pause.exit_code == 0
    assert (queue_dir / ".paused").exists()

    cancel = runner.invoke(
        cli.app, ["queue", "cancel", "job-x.json", "--queue-dir", str(queue_dir)]
    )
    assert cancel.exit_code == 0
    assert (queue_dir / "canceled" / "job-x.json").exists()

    retry = runner.invoke(cli.app, ["queue", "retry", "job-x.json", "--queue-dir", str(queue_dir)])
    assert retry.exit_code == 0
    assert (queue_dir / "inbox" / "job-x.json").exists()

    resume = runner.invoke(cli.app, ["queue", "resume", "--queue-dir", str(queue_dir)])
    assert resume.exit_code == 0
    assert not (queue_dir / ".paused").exists()


def test_queue_unlock_refuses_live_lock_without_force(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True)
    (queue_dir / ".daemon.lock").write_text(
        json.dumps({"pid": os.getpid(), "ts": time.time()}),
        encoding="utf-8",
    )

    result = runner.invoke(cli.app, ["queue", "unlock", "--queue-dir", str(queue_dir)])

    assert result.exit_code == 1
    assert "Lock held by live pid=" in result.output
    assert (queue_dir / ".daemon.lock").exists()


def test_queue_unlock_removes_stale_lock(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True)
    (queue_dir / ".daemon.lock").write_text(
        json.dumps({"pid": 999999, "ts": 1}),
        encoding="utf-8",
    )

    result = runner.invoke(cli.app, ["queue", "unlock", "--queue-dir", str(queue_dir)])

    assert result.exit_code == 0
    assert "Removed stale daemon lock (age_s=" in result.output
    assert not (queue_dir / ".daemon.lock").exists()


def test_queue_unlock_removes_dead_pid_lock(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True)
    (queue_dir / ".daemon.lock").write_text(
        json.dumps({"pid": 999999, "ts": time.time()}),
        encoding="utf-8",
    )

    result = runner.invoke(cli.app, ["queue", "unlock", "--queue-dir", str(queue_dir)])

    assert result.exit_code == 0
    assert "Removed orphaned daemon lock (pid not alive)." in result.output
    assert not (queue_dir / ".daemon.lock").exists()


def test_queue_unlock_force_removes_live_lock(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True)
    (queue_dir / ".daemon.lock").write_text(
        json.dumps({"pid": os.getpid(), "ts": time.time()}),
        encoding="utf-8",
    )

    result = runner.invoke(cli.app, ["queue", "unlock", "--force", "--queue-dir", str(queue_dir)])

    assert result.exit_code == 0
    assert "Force-removed daemon lock." in result.output
    assert not (queue_dir / ".daemon.lock").exists()


def test_queue_bundle_job_and_system(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    (queue_dir / "done").mkdir(parents=True, exist_ok=True)
    (queue_dir / "done" / "job-z.json").write_text('{"goal":"bundle"}', encoding="utf-8")
    (queue_dir / "artifacts" / "job-z").mkdir(parents=True, exist_ok=True)
    (queue_dir / "artifacts" / "job-z" / "stdout.txt").write_text("hello", encoding="utf-8")

    out_job = tmp_path / "job.zip"
    job_res = runner.invoke(
        cli.app,
        ["queue", "bundle", "job-z.json", "--out", str(out_job), "--queue-dir", str(queue_dir)],
    )
    assert job_res.exit_code == 0
    assert out_job.exists()

    out_sys = tmp_path / "system.zip"
    sys_res = runner.invoke(
        cli.app,
        ["queue", "bundle", "--system", "--out", str(out_sys), "--queue-dir", str(queue_dir)],
    )
    assert sys_res.exit_code == 0
    assert out_sys.exists()


def test_ops_bundle_system_and_job_commands(tmp_path, monkeypatch):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    (queue_dir / "done").mkdir(parents=True, exist_ok=True)
    (queue_dir / "done" / "job-z.json").write_text('{"goal":"bundle"}', encoding="utf-8")
    art = queue_dir / "artifacts" / "job-z"
    art.mkdir(parents=True, exist_ok=True)
    (art / "plan.json").write_text("{}", encoding="utf-8")
    (art / "actions.jsonl").write_text("{}\n", encoding="utf-8")
    (art / "stdout.txt").write_text("ok", encoding="utf-8")
    (art / "stderr.txt").write_text("", encoding="utf-8")

    monkeypatch.setattr(
        "voxera.ops_bundle.subprocess.check_output", lambda *args, **kwargs: "journal"
    )

    sys_res = runner.invoke(cli.app, ["ops", "bundle", "system", "--queue-dir", str(queue_dir)])
    assert sys_res.exit_code == 0
    assert len(sys_res.output.strip().splitlines()) == 1
    sys_candidates = sorted((queue_dir / "_archive").glob("*/bundle-system.zip"))
    assert sys_candidates
    assert sys_candidates[-1].exists()

    job_res = runner.invoke(
        cli.app,
        ["ops", "bundle", "job", "job-z.json", "--queue-dir", str(queue_dir)],
    )
    assert job_res.exit_code == 0
    assert len(job_res.output.strip().splitlines()) == 1
    job_candidates = sorted((queue_dir / "_archive").glob("*/bundle-job-job-z.zip"))
    assert job_candidates
    assert job_candidates[-1].exists()


def test_ops_bundle_system_and_job_with_explicit_dir(tmp_path, monkeypatch):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    archive_dir = tmp_path / "incident-123"
    (queue_dir / "done").mkdir(parents=True, exist_ok=True)
    (queue_dir / "done" / "job-z.json").write_text('{"goal":"bundle"}', encoding="utf-8")

    monkeypatch.setattr(
        "voxera.ops_bundle.subprocess.check_output", lambda *args, **kwargs: "journal"
    )

    sys_res = runner.invoke(
        cli.app,
        ["ops", "bundle", "system", "--queue-dir", str(queue_dir), "--dir", str(archive_dir)],
    )
    assert sys_res.exit_code == 0
    system_zip = Path(sys_res.output.strip())
    assert system_zip == archive_dir.resolve() / "bundle-system.zip"
    assert system_zip.exists()

    job_res = runner.invoke(
        cli.app,
        [
            "ops",
            "bundle",
            "job",
            "job-z.json",
            "--queue-dir",
            str(queue_dir),
            "--dir",
            str(archive_dir),
        ],
    )
    assert job_res.exit_code == 0
    job_zip = Path(job_res.output.strip())
    assert job_zip == archive_dir.resolve() / "bundle-job-job-z.zip"
    assert job_zip.exists()


def test_ops_bundle_queue_dir_ignores_env_archive_override(tmp_path, monkeypatch):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    env_archive = tmp_path / "global-archive"
    (queue_dir / "done").mkdir(parents=True, exist_ok=True)
    (queue_dir / "done" / "job-z.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("VOXERA_OPS_BUNDLE_DIR", str(env_archive))
    monkeypatch.setattr(
        "voxera.ops_bundle.subprocess.check_output", lambda *args, **kwargs: "journal"
    )

    res = runner.invoke(cli.app, ["ops", "bundle", "system", "--queue-dir", str(queue_dir)])
    assert res.exit_code == 0
    path = Path(res.output.strip())
    assert str(path).startswith(str((queue_dir / "_archive").resolve()))
    assert not str(path).startswith(str(env_archive.resolve()))


def test_queue_lock_status_alias_renders_lock_fields(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True)

    result = runner.invoke(cli.app, ["queue", "lock", "status", "--queue-dir", str(queue_dir)])

    assert result.exit_code == 0
    assert "Lock Status" in result.output
    assert "lock path" in result.output
    assert "lock exists" in result.output
    assert "lock pid alive" in result.output


def test_queue_help_lists_lock_status_command():
    runner = CliRunner()

    result = runner.invoke(cli.app, ["queue", "lock", "--help"])

    assert result.exit_code == 0
    assert "status" in result.output


def test_queue_health_renders_observability_sections(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text(
        json.dumps(
            {
                "daemon_state": "degraded",
                "consecutive_brain_failures": 4,
                "degraded_reason": "brain_fallbacks",
                "brain_backoff_last_applied_s": 2,
                "brain_backoff_last_applied_ts": 1700000200.0,
                "last_error": "timeout",
                "last_error_ts_ms": 1700000000555,
                "last_ok_event": "mission_complete",
                "last_ok_ts_ms": 1700000000444,
                "last_fallback_reason": "timeout",
                "last_fallback_from": "primary",
                "last_fallback_to": "fallback",
                "last_fallback_ts_ms": 1700000000333,
                "counters": {"brain_fallback_count": 3},
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(cli.app, ["queue", "health", "--queue-dir", str(queue_dir)])

    assert result.exit_code == 0
    assert "Current State" in result.output
    assert "Recent History" in result.output
    assert "Historical Counters" in result.output
    assert "degraded" in result.output
    assert "brain_fallbacks" in result.output
    assert "timeout" in result.output


def test_queue_health_missing_history_renders_dash_not_none(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text("{}", encoding="utf-8")

    result = runner.invoke(cli.app, ["queue", "health", "--queue-dir", str(queue_dir)])

    assert result.exit_code == 0
    assert "Last OK" in result.output
    assert "Last Error" in result.output
    assert "Last Brain Fallback" in result.output
    assert "Last Shutdown" in result.output
    assert "@ None" not in result.output


def test_queue_health_json_contains_section_parity(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text(
        json.dumps(
            {
                "daemon_state": "healthy",
                "last_ok_event": "tick",
                "counters": {"brain_fallback_count": 1, "panel_auth_invalid": 2},
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(cli.app, ["queue", "health", "--json", "--queue-dir", str(queue_dir)])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "current_state" in payload
    assert "recent_history" in payload
    assert "counters" in payload
    assert "historical_counters" in payload
    assert payload["current_state"]["daemon_state"] == "healthy"
    assert payload["recent_history"]["last_ok_event"] == "tick"
    assert payload["historical_counters"]["brain_fallback_count"] == 1
    assert payload["recent_history"]["last_brain_fallback"]["reason"] is None


def test_queue_health_watch_mode_refreshes_once(tmp_path, monkeypatch):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(cli.console, "clear", lambda: None)

    calls = {"n": 0}

    def _sleep(_seconds: float) -> None:
        calls["n"] += 1
        raise KeyboardInterrupt

    monkeypatch.setattr(time, "sleep", _sleep)

    result = runner.invoke(
        cli.app,
        ["queue", "health", "--watch", "--interval", "0.2", "--queue-dir", str(queue_dir)],
    )

    assert result.exit_code == 0
    assert calls["n"] == 1
    assert "Refreshing every 0.2s" in result.output
    assert "Stopped watch mode." in result.output


def test_queue_health_prints_last_shutdown_block(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text(
        json.dumps(
            {
                "last_shutdown_outcome": "clean",
                "last_shutdown_ts": 1700000000.5,
                "last_shutdown_reason": "SIGTERM",
                "last_shutdown_job": "job-a.json",
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(cli.app, ["queue", "health", "--queue-dir", str(queue_dir)])

    assert result.exit_code == 0
    assert "Last Shutdown" in result.output
    assert "clean" in result.output
    assert "SIGTERM" in result.output
    assert "job-a.json" in result.output


def test_queue_health_json_includes_last_shutdown_fields(tmp_path):
    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "health.json").write_text(
        json.dumps(
            {
                "last_shutdown_outcome": "failed_shutdown",
                "last_shutdown_ts": 1700000200.0,
                "last_shutdown_reason": "RuntimeError: boom",
                "last_shutdown_job": "job-b.json",
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(cli.app, ["queue", "health", "--json", "--queue-dir", str(queue_dir)])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["last_shutdown_outcome"] == "failed_shutdown"
    assert payload["last_shutdown_reason"] == "RuntimeError: boom"
    assert payload["last_shutdown_job"] == "job-b.json"
    assert payload["last_shutdown_ts"] == 1700000200.0


def test_queue_health_reset_current_and_recent_logs_audit(tmp_path, monkeypatch):
    from voxera.health import read_health_snapshot, write_health_snapshot

    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    events: list[dict[str, object]] = []
    monkeypatch.setattr(cli, "log", lambda event: events.append(event))
    write_health_snapshot(
        queue_dir,
        {
            "daemon_state": "degraded",
            "consecutive_brain_failures": 3,
            "last_error": "oops",
            "last_error_ts_ms": 10,
            "counters": {"panel_401_count": 4},
        },
    )

    result = runner.invoke(
        cli.app,
        ["queue", "health-reset", "--scope", "current_and_recent", "--queue-dir", str(queue_dir)],
    )
    assert result.exit_code == 0
    assert "Historical counters preserved by default" in result.output
    payload = read_health_snapshot(queue_dir)
    assert payload["consecutive_brain_failures"] == 0
    assert payload["last_error"] is None
    assert payload["counters"]["panel_401_count"] == 4
    assert events and events[0]["event"] == "health_reset_current_and_recent"


def test_queue_health_reset_counter_group_json(tmp_path, monkeypatch):
    from voxera.health import write_health_snapshot

    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    monkeypatch.setattr(cli, "log", lambda _event: None)
    write_health_snapshot(
        queue_dir,
        {
            "counters": {"panel_401_count": 2, "brain_fallback_count": 6},
            "last_error": "x",
        },
    )

    result = runner.invoke(
        cli.app,
        [
            "queue",
            "health-reset",
            "--scope",
            "recent_history",
            "--counter-group",
            "panel_auth_counters",
            "--json",
            "--queue-dir",
            str(queue_dir),
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["counter_group"] == "panel_auth_counters"
    assert "counters.panel_401_count" in payload["changed_fields"]


def test_queue_health_reset_json_reports_cleared_fallback_fields(tmp_path, monkeypatch):
    from voxera.health import write_health_snapshot

    runner = CliRunner()
    queue_dir = tmp_path / "queue"
    monkeypatch.setattr(cli, "log", lambda _event: None)
    write_health_snapshot(
        queue_dir,
        {
            "last_fallback_reason": "timeout",
            "last_fallback_from": "primary",
            "last_fallback_to": "fallback",
            "last_fallback_ts_ms": 1700000000333,
        },
    )

    result = runner.invoke(
        cli.app,
        [
            "queue",
            "health-reset",
            "--scope",
            "current_and_recent",
            "--json",
            "--queue-dir",
            str(queue_dir),
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "last_fallback_reason" in payload["changed_fields"]
    assert "last_fallback_from" in payload["changed_fields"]
    assert "last_fallback_to" in payload["changed_fields"]
    assert "last_fallback_ts_ms" in payload["changed_fields"]


def test_queue_health_reset_default_queue_dir_uses_expanded_operator_path(tmp_path, monkeypatch):
    from voxera.health import read_health_snapshot, write_health_snapshot

    runner = CliRunner()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli, "log", lambda _event: None)

    operator_queue = tmp_path / "VoxeraOS" / "notes" / "queue"
    write_health_snapshot(
        operator_queue,
        {
            "last_fallback_reason": "timeout",
            "last_fallback_from": "primary",
            "last_fallback_to": "fallback",
            "last_fallback_ts_ms": 123,
        },
    )

    result = runner.invoke(cli.app, ["queue", "health-reset", "--scope", "current_and_recent"])
    assert result.exit_code == 0

    payload = read_health_snapshot(operator_queue)
    assert payload["last_fallback_reason"] is None
    assert payload["last_fallback_from"] is None
    assert payload["last_fallback_to"] is None
    assert payload["last_fallback_ts_ms"] is None

    accidental_tilde_path = Path("~") / "VoxeraOS" / "notes" / "queue" / "health.json"
    assert not accidental_tilde_path.exists()


def test_queue_health_and_reset_share_effective_health_path(tmp_path, monkeypatch):
    from voxera.health import write_health_snapshot

    runner = CliRunner()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli, "log", lambda _event: None)
    operator_queue = tmp_path / "VoxeraOS" / "notes" / "queue"
    write_health_snapshot(operator_queue, {"last_fallback_reason": "timeout"})

    reset = runner.invoke(
        cli.app,
        ["queue", "health-reset", "--scope", "recent_history", "--json"],
    )
    assert reset.exit_code == 0

    health = runner.invoke(cli.app, ["queue", "health", "--json"])
    assert health.exit_code == 0
    health_payload = json.loads(health.output)
    assert health_payload["health_path"] == str((operator_queue / "health.json").resolve())
    assert health_payload["recent_history"]["last_brain_fallback"]["reason"] is None
