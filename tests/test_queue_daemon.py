import json
import os
import sys
import threading
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

import voxera_builtin_skills.files_write_text as files_write_text_skill
from voxera.core.missions import MissionStep, MissionTemplate
from voxera.core.queue_daemon import MissionQueueDaemon, QueueLockError
from voxera.models import AppConfig, PolicyApprovals, PrivacyConfig, RunResult


def _assert_job_moved(target_dir, job_filename):
    target_dir.mkdir(parents=True, exist_ok=True)
    exact = target_dir / job_filename
    if exact.exists():
        return exact
    job_path = Path(job_filename)
    matches = sorted(target_dir.glob(f"{job_path.stem}-*{job_path.suffix}"))
    assert matches, f"expected moved job for {job_filename} in {target_dir}"
    return matches[-1]


async def _fake_generate_for_sandbox_argv(_messages, tools=None):
    return type(
        "R",
        (),
        {
            "text": json.dumps(
                {
                    "title": "argv",
                    "steps": [{"skill_id": "sandbox.exec", "args": {"command": "echo HELLO-ARGV"}}],
                }
            )
        },
    )()


def _force_policy_ask(monkeypatch, *, redact_logs=True):
    cfg = AppConfig(
        policy=PolicyApprovals(system_settings="ask", network_changes="ask"),
        privacy=PrivacyConfig(redact_logs=redact_logs),
    )
    monkeypatch.setattr("voxera.core.queue_daemon.load_config", lambda: cfg)


def _stub_planner(monkeypatch):
    async def _fake_plan(goal, cfg, registry, source="cli", job_ref=None):
        return MissionTemplate(
            id="cloud_planned",
            title="Stub Plan",
            goal=goal,
            steps=[MissionStep(skill_id="system.status", args={})],
            notes="stub",
        )

    monkeypatch.setattr("voxera.core.queue_daemon.plan_mission", _fake_plan)


def test_queue_daemon_processes_plan_goal_alias_to_done(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_planner(monkeypatch)
    queue_dir = tmp_path / "queue"
    job = queue_dir / "job1.json"
    job.parent.mkdir(parents=True, exist_ok=True)
    job.write_text(json.dumps({"plan_goal": "check machine"}), encoding="utf-8")

    daemon = MissionQueueDaemon(
        queue_root=queue_dir, poll_interval=0.1, mission_log_path=tmp_path / "mission-log.md"
    )
    count = daemon.process_pending_once()

    assert count == 1
    assert (queue_dir / "done" / "job1.json").exists()


def test_queue_daemon_processes_goal_to_done(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_planner(monkeypatch)
    queue_dir = tmp_path / "queue"
    job = queue_dir / "job1.json"
    job.parent.mkdir(parents=True, exist_ok=True)
    job.write_text(json.dumps({"goal": "check machine"}), encoding="utf-8")

    daemon = MissionQueueDaemon(
        queue_root=queue_dir, poll_interval=0.1, mission_log_path=tmp_path / "mission-log.md"
    )
    daemon.process_pending_once()

    assert (queue_dir / "done" / "job1.json").exists()


def test_queue_daemon_rejects_invalid_schema_with_clear_error(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    events = []
    monkeypatch.setattr("voxera.core.queue_daemon.log", lambda e: events.append(e))

    queue_dir = tmp_path / "queue"
    job = queue_dir / "bad.json"
    job.parent.mkdir(parents=True, exist_ok=True)
    job.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")

    daemon = MissionQueueDaemon(
        queue_root=queue_dir, poll_interval=0.1, mission_log_path=tmp_path / "mission-log.md"
    )
    daemon.process_pending_once()

    failed_job = _assert_job_moved(queue_dir / "failed", "bad.json")
    sidecar = failed_job.with_name(f"{failed_job.stem}.error.json")
    assert sidecar.exists()
    details = json.loads(sidecar.read_text(encoding="utf-8"))
    assert "mission_id (or mission), goal (or plan_goal), or inline steps" in details["error"]
    assert any(
        "mission_id (or mission), goal (or plan_goal), or inline steps" in evt.get("error", "")
        for evt in events
        if evt.get("event") == "queue_job_failed"
    )


def test_queue_daemon_accepts_inline_steps_with_legacy_skill_key(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    job = queue_dir / "job-approval-test.json"
    job.parent.mkdir(parents=True, exist_ok=True)
    job.write_text(
        json.dumps(
            {
                "title": "Approval Artifact Test",
                "goal": "Open example.com",
                "steps": [
                    {
                        "skill": "system.open_url",
                        "args": {"url": "https://example.com"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    daemon = MissionQueueDaemon(
        queue_root=queue_dir, poll_interval=0.1, mission_log_path=tmp_path / "mission-log.md"
    )
    monkeypatch.setattr(
        daemon.mission_runner.skill_runner.registry,
        "load_entrypoint",
        lambda _mf: lambda **_kwargs: "ok",
    )

    daemon.process_pending_once()

    assert (queue_dir / "pending" / "job-approval-test.json").exists()
    artifact_path = queue_dir / "pending" / "approvals" / "job-approval-test.approval.json"
    assert artifact_path.exists()
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["skill"] == "system.open_url"
    assert artifact["target"] == {"type": "url", "value": "https://example.com"}


def test_queue_daemon_inline_steps_missing_skill_fails_loudly(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    events = []
    monkeypatch.setattr("voxera.core.queue_daemon.log", lambda e: events.append(e))

    queue_dir = tmp_path / "queue"
    job = queue_dir / "job-invalid-step.json"
    job.parent.mkdir(parents=True, exist_ok=True)
    job.write_text(
        json.dumps(
            {
                "title": "Invalid Step",
                "goal": "broken",
                "steps": [{"args": {"url": "https://example.com"}}],
            }
        ),
        encoding="utf-8",
    )

    daemon = MissionQueueDaemon(
        queue_root=queue_dir, poll_interval=0.1, mission_log_path=tmp_path / "mission-log.md"
    )
    daemon.process_pending_once()

    failed_job = _assert_job_moved(queue_dir / "failed", "job-invalid-step.json")
    sidecar = failed_job.with_name(f"{failed_job.stem}.error.json")
    assert sidecar.exists()
    details = json.loads(sidecar.read_text(encoding="utf-8"))
    assert "missing skill_id (or legacy skill)" in details["error"]
    assert any(
        event.get("event") == "queue_job_invalid"
        and event.get("filename") == "job-invalid-step.json"
        for event in events
    )


def test_queue_daemon_accepts_mission_alias(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    job = queue_dir / "job1.json"
    job.parent.mkdir(parents=True, exist_ok=True)
    job.write_text(json.dumps({"mission": "system_check"}), encoding="utf-8")

    daemon = MissionQueueDaemon(
        queue_root=queue_dir, poll_interval=0.1, mission_log_path=tmp_path / "mission-log.md"
    )
    daemon.process_pending_once()

    assert (queue_dir / "done" / "job1.json").exists()


def test_queue_daemon_ask_goes_to_pending_and_can_approve_or_deny(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    events = []
    monkeypatch.setattr("voxera.core.queue_daemon.log", lambda e: events.append(e))

    queue_dir = tmp_path / "queue"
    approve_job = queue_dir / "approval.json"
    deny_job = queue_dir / "deny.json"
    queue_dir.mkdir(parents=True, exist_ok=True)
    approve_job.write_text(json.dumps({"mission_id": "focus_mode"}), encoding="utf-8")
    deny_job.write_text(json.dumps({"goal": "Open https://example.com"}), encoding="utf-8")

    async def _goal_planner(goal, cfg, registry, source="cli", job_ref=None):
        return MissionTemplate(
            id="goal_url",
            title="Goal URL",
            goal=goal,
            steps=[MissionStep(skill_id="system.open_url", args={"url": "https://example.com"})],
        )

    monkeypatch.setattr("voxera.core.queue_daemon.plan_mission", _goal_planner)

    log_path = tmp_path / "mission-log.md"
    daemon = MissionQueueDaemon(queue_root=queue_dir, poll_interval=0.1, mission_log_path=log_path)
    monkeypatch.setattr(
        daemon.mission_runner.skill_runner.registry,
        "load_entrypoint",
        lambda _mf: lambda **_kwargs: "ok",
    )
    daemon.process_job_file(approve_job)

    pending_job = queue_dir / "pending" / "approval.json"
    artifact_path = queue_dir / "pending" / "approvals" / "approval.approval.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))

    assert pending_job.exists()
    assert artifact["status"] == "pending_approval"
    assert artifact["args"]["percent"] == "<redacted>"
    assert any(e["event"] == "queue_job_received" for e in events)
    assert any(e["event"] == "queue_job_started" for e in events)
    assert any(e["event"] == "queue_job_pending_approval" for e in events)
    assert "status=pending_approval" in log_path.read_text(encoding="utf-8")

    daemon.resolve_approval("approval", approve=True)
    _assert_job_moved(queue_dir / "done", "approval.json")

    daemon.process_job_file(deny_job)
    deny_artifact = queue_dir / "pending" / "approvals" / "deny.approval.json"
    assert deny_artifact.exists()
    deny_details = json.loads(deny_artifact.read_text(encoding="utf-8"))
    assert deny_details["skill"] == "system.open_url"
    daemon.resolve_approval("deny", approve=False)
    _assert_job_moved(queue_dir / "failed", "deny.json")
    assert any(
        e["event"] == "queue_job_failed" and "Denied in approval inbox" in e.get("error", "")
        for e in events
    )
    assert any(e["event"] == "mission_denied" for e in events)
    assert "status=denied" in log_path.read_text(encoding="utf-8")


def test_queue_daemon_dev_auto_approve_constraints(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    system_job = queue_dir / "system.json"
    net_job = queue_dir / "network.json"
    system_job.write_text(json.dumps({"mission_id": "focus_mode"}), encoding="utf-8")
    net_job.write_text(json.dumps({"mission_id": "daily_checkin"}), encoding="utf-8")

    no_dev_events = []
    monkeypatch.setattr("voxera.core.queue_daemon.log", lambda event: no_dev_events.append(event))
    daemon_no_dev = MissionQueueDaemon(
        queue_root=queue_dir,
        mission_log_path=tmp_path / "mission-log-no-dev.md",
        auto_approve_ask=True,
    )
    monkeypatch.setattr(
        daemon_no_dev.mission_runner.skill_runner.registry,
        "load_entrypoint",
        lambda _mf: lambda **_kwargs: "ok",
    )
    daemon_no_dev.process_job_file(system_job)
    assert (queue_dir / "pending" / "system.json").exists()
    assert not any(event["event"] == "queue_auto_approved" for event in no_dev_events)

    (queue_dir / "pending" / "system.json").replace(system_job)
    (queue_dir / "pending" / "system.pending.json").unlink(missing_ok=True)
    (queue_dir / "pending" / "approvals" / "system.approval.json").unlink(missing_ok=True)

    dev_events = []
    monkeypatch.setattr("voxera.core.queue_daemon.log", lambda event: dev_events.append(event))
    monkeypatch.setenv("VOXERA_DEV_MODE", "1")
    daemon_dev = MissionQueueDaemon(
        queue_root=queue_dir,
        mission_log_path=tmp_path / "mission-log-dev.md",
        auto_approve_ask=True,
    )
    monkeypatch.setattr(
        daemon_dev.mission_runner.skill_runner.registry,
        "load_entrypoint",
        lambda _mf: lambda **_kwargs: "ok",
    )
    daemon_dev.process_job_file(system_job)
    daemon_dev.process_job_file(net_job)

    assert (queue_dir / "done" / "system.json").exists()
    assert (queue_dir / "pending" / "network.json").exists()
    assert any(
        event["event"] == "queue_auto_approved" and event.get("capability") == "system.settings"
        for event in dev_events
    )
    assert not any(
        event["event"] == "queue_auto_approved" and event.get("capability") == "network.change"
        for event in dev_events
    )
    assert any(
        event["event"] == "queue_approval_required" and event.get("skill") == "system.open_url"
        for event in dev_events
    )


def test_queue_goal_job_rewrites_default_write_steps_and_completes(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    job = queue_dir / "job-e2e-ask.json"
    queue_dir.mkdir(parents=True, exist_ok=True)
    job.write_text(json.dumps({"goal": "check machine health"}), encoding="utf-8")

    generated_payloads = []

    async def _fake_generate(_messages, tools=None):
        class _Resp:
            text = json.dumps(
                {
                    "title": "E2E Ask",
                    "steps": [
                        {
                            "skill_id": "sandbox.exec",
                            "args": {
                                "command": [
                                    "bash",
                                    "-lc",
                                    'title=$(xdotool getactivewindow getwindowname) && echo $title | grep "Example"',
                                ]
                            },
                        },
                        {
                            "skill_id": "sandbox.exec",
                            "args": {"command": ["bash", "-lc", "curl -I https://example.com"]},
                        },
                        {
                            "skill_id": "system.open_url",
                            "args": {"url": "https://example.com"},
                        },
                    ],
                }
            )

        generated_payloads.append(_Resp.text)
        return _Resp()

    fake_brain = type("B", (), {"generate": _fake_generate})()
    monkeypatch.setattr(
        "voxera.core.mission_planner._build_brain_candidates",
        lambda _cfg: [
            type("C", (), {"name": "primary", "model": "primary-model", "brain": fake_brain})()
        ],
    )

    daemon = MissionQueueDaemon(
        queue_root=queue_dir, poll_interval=0.1, mission_log_path=tmp_path / "mission-log.md"
    )
    monkeypatch.setattr(
        daemon.mission_runner.skill_runner.registry,
        "load_entrypoint",
        lambda _mf: lambda **_kwargs: "ok",
    )

    captured = {}
    real_run = daemon.mission_runner.run

    def _capture_run(mission, context=None):
        captured["mission"] = mission
        return real_run(mission, context=context)

    monkeypatch.setattr(daemon.mission_runner, "run", _capture_run)

    daemon.process_pending_once()

    pending_job = _assert_job_moved(queue_dir / "pending", "job-e2e-ask.json")
    assert not any((queue_dir / "done").glob("job-e2e-ask*.json"))
    approval_artifact = queue_dir / "pending" / "approvals" / f"{pending_job.stem}.approval.json"
    assert approval_artifact.exists()
    assert (queue_dir / "pending" / f"{pending_job.stem}.pending.json").exists()
    assert generated_payloads
    assert "mission" in captured
    step_dump = json.dumps(
        [{"skill_id": s.skill_id, "args": s.args} for s in captured["mission"].steps]
    ).lower()
    assert "sandbox.exec" not in step_dump
    assert "xdotool" not in step_dump
    assert "curl" not in step_dump
    assert "wget" not in step_dump


def test_queue_daemon_watchdog_mode_processes_existing_backlog(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    job = queue_dir / "job1.json"
    job.parent.mkdir(parents=True, exist_ok=True)
    job.write_text(json.dumps({"mission_id": "system_check"}), encoding="utf-8")

    daemon = MissionQueueDaemon(
        queue_root=queue_dir, poll_interval=0.1, mission_log_path=tmp_path / "mission-log.md"
    )

    class _EventHandler:
        pass

    class _Observer:
        def schedule(self, *_args, **_kwargs):
            return None

        def start(self):
            return None

        def stop(self):
            return None

        def join(self):
            return None

    monkeypatch.setitem(sys.modules, "watchdog", types.ModuleType("watchdog"))
    monkeypatch.setitem(
        sys.modules, "watchdog.events", types.SimpleNamespace(FileSystemEventHandler=_EventHandler)
    )
    monkeypatch.setitem(
        sys.modules, "watchdog.observers", types.SimpleNamespace(Observer=_Observer)
    )

    def _interrupt(_seconds: float):
        raise KeyboardInterrupt

    monkeypatch.setattr("voxera.core.queue_daemon.time.sleep", _interrupt)

    with pytest.raises(KeyboardInterrupt):
        daemon.run(once=False)

    assert (queue_dir / "done" / "job1.json").exists()


def test_status_snapshot_counts_and_pending_parsing(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    (queue_dir / "pending" / "approvals").mkdir(parents=True)
    (queue_dir / "done").mkdir(parents=True)
    (queue_dir / "failed").mkdir(parents=True)

    (queue_dir / "pending" / "job1.json").write_text("{}", encoding="utf-8")
    (queue_dir / "pending" / "job1.pending.json").write_text("{}", encoding="utf-8")
    (queue_dir / "done" / "done1.json").write_text("{}", encoding="utf-8")
    (queue_dir / "failed" / "bad1.json").write_text("{}", encoding="utf-8")

    approval = {
        "job": "job1.json",
        "step": 2,
        "skill": "system.set_volume",
        "capability": "system.settings",
        "reason": "system.settings -> ask",
    }
    (queue_dir / "pending" / "approvals" / "job1.approval.json").write_text(
        json.dumps(approval), encoding="utf-8"
    )

    monkeypatch.setattr(
        "voxera.core.queue_daemon.tail",
        lambda _n: [
            {
                "event": "queue_job_failed",
                "job": str(queue_dir / "failed" / "bad1.json"),
                "error": "boom",
            }
        ],
    )
    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    status = daemon.status_snapshot()

    assert status["counts"] == {
        "inbox": 0,
        "pending": 1,
        "pending_approvals": 1,
        "done": 1,
        "failed": 1,
    }
    assert status["pending_approvals"][0]["skill"] == "system.set_volume"
    assert status["recent_failed"][0] == {"job": "bad1.json", "error": "boom"}


def test_status_snapshot_prefers_valid_failed_sidecar_and_excludes_sidecar_from_failed_count(
    tmp_path, monkeypatch
):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    (queue_dir / "failed").mkdir(parents=True)
    (queue_dir / "pending" / "approvals").mkdir(parents=True)
    (queue_dir / "done").mkdir(parents=True)

    failed_job = queue_dir / "failed" / "bad1.json"
    failed_job.write_text("{}", encoding="utf-8")
    failed_job.with_name("bad1.error.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "job": "bad1.json",
                "error": "from-sidecar",
                "timestamp_ms": int(time.time() * 1000),
                "payload": {"goal": "x"},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "voxera.core.queue_daemon.tail",
        lambda _n: [{"event": "queue_job_failed", "job": str(failed_job), "error": "from-audit"}],
    )

    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    status = daemon.status_snapshot()
    assert status["counts"]["failed"] == 1
    assert status["recent_failed"][0] == {"job": "bad1.json", "error": "from-sidecar"}


def test_status_snapshot_failed_sidecar_health_counters(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    (queue_dir / "failed").mkdir(parents=True)
    (queue_dir / "pending" / "approvals").mkdir(parents=True)
    (queue_dir / "done").mkdir(parents=True)

    valid_job = queue_dir / "failed" / "valid.json"
    valid_job.write_text("{}", encoding="utf-8")
    valid_job.with_name("valid.error.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "job": "valid.json",
                "error": "valid sidecar",
                "timestamp_ms": int(time.time() * 1000),
            }
        ),
        encoding="utf-8",
    )

    invalid_job = queue_dir / "failed" / "invalid.json"
    invalid_job.write_text("{}", encoding="utf-8")
    invalid_job.with_name("invalid.error.json").write_text(
        json.dumps({"schema_version": 1, "job": "invalid.json"}), encoding="utf-8"
    )

    missing_job = queue_dir / "failed" / "missing.json"
    missing_job.write_text("{}", encoding="utf-8")

    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    status = daemon.status_snapshot()

    assert status["failed_sidecars_valid"] == 1
    assert status["failed_sidecars_invalid"] == 1
    assert status["failed_sidecars_missing"] == 1


def test_status_snapshot_invalid_sidecar_keeps_recent_failed_renderable(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    (queue_dir / "failed").mkdir(parents=True)
    (queue_dir / "pending" / "approvals").mkdir(parents=True)
    (queue_dir / "done").mkdir(parents=True)

    failed_job = queue_dir / "failed" / "bad1.json"
    failed_job.write_text("{}", encoding="utf-8")
    failed_job.with_name("bad1.error.json").write_text(
        json.dumps(
            {
                "schema_version": 999,
                "job": "bad1.json",
                "error": "broken",
                "timestamp_ms": int(time.time() * 1000),
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "voxera.core.queue_daemon.tail",
        lambda _n: [{"event": "queue_job_failed", "job": str(failed_job), "error": "from-audit"}],
    )

    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    status = daemon.status_snapshot()

    assert status["failed_sidecars_invalid"] == 1
    assert status["recent_failed"][0] == {"job": "bad1.json", "error": "from-audit"}


def test_status_snapshot_invalid_sidecar_logs_once_per_snapshot(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    events = []
    monkeypatch.setattr("voxera.core.queue_daemon.log", lambda event: events.append(event))

    queue_dir = tmp_path / "queue"
    (queue_dir / "failed").mkdir(parents=True)
    (queue_dir / "pending" / "approvals").mkdir(parents=True)
    (queue_dir / "done").mkdir(parents=True)

    failed_job = queue_dir / "failed" / "bad1.json"
    failed_job.write_text("{}", encoding="utf-8")
    failed_job.with_name("bad1.error.json").write_text(
        json.dumps(
            {
                "schema_version": 999,
                "job": "bad1.json",
                "error": "broken",
                "timestamp_ms": int(time.time() * 1000),
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "voxera.core.queue_daemon.tail",
        lambda _n: [{"event": "queue_job_failed", "job": str(failed_job), "error": "from-audit"}],
    )

    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    status = daemon.status_snapshot()

    assert status["failed_sidecars_invalid"] == 1
    invalid_events = [e for e in events if e.get("event") == "queue_failed_sidecar_invalid"]
    assert len(invalid_events) == 1


def test_status_snapshot_includes_retention_and_last_prune_event(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    (queue_dir / "failed").mkdir(parents=True)
    (queue_dir / "pending" / "approvals").mkdir(parents=True)
    (queue_dir / "done").mkdir(parents=True)

    events = [
        {
            "event": "queue_failed_artifacts_pruned",
            "removed_jobs": 2,
            "removed_sidecars": 1,
            "max_age_s": 60.0,
            "max_count": 10,
        }
    ]
    monkeypatch.setattr("voxera.core.queue_daemon.tail", lambda _n: events)

    daemon = MissionQueueDaemon(
        queue_root=queue_dir,
        mission_log_path=tmp_path / "mission-log.md",
        failed_retention_max_age_s=60.0,
        failed_retention_max_count=10,
    )
    status = daemon.status_snapshot()

    assert status["failed_retention"] == {"max_age_s": 60.0, "max_count": 10}
    assert status["failed_prune_last"] == {
        "removed_jobs": 2,
        "removed_sidecars": 1,
        "max_age_s": 60.0,
        "max_count": 10,
    }


def test_status_snapshot_fresh_install_without_queue_dirs(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "missing-queue"

    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    status = daemon.status_snapshot()

    assert status["exists"] is False
    assert status["counts"] == {
        "inbox": 0,
        "pending": 0,
        "pending_approvals": 0,
        "done": 0,
        "failed": 0,
    }
    assert status["pending_approvals"] == []
    assert status["recent_failed"] == []


def test_pending_approval_notification_success_and_failure_events(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    daemon = MissionQueueDaemon(
        queue_root=tmp_path / "queue", mission_log_path=tmp_path / "mission-log.md"
    )

    approval = {"job": "job1.json", "skill": "system.set_volume", "reason": "need approval"}
    events = []
    monkeypatch.setattr("voxera.core.queue_daemon.log", lambda event: events.append(event))
    monkeypatch.setenv("VOXERA_NOTIFY", "1")

    monkeypatch.setattr(
        "voxera.core.queue_daemon.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr=""),
    )
    daemon._notify_pending_approval(approval)
    assert events[-1]["event"] == "queue_notify_sent"

    monkeypatch.setattr(
        "voxera.core.queue_daemon.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr="notify not available"),
    )
    daemon._notify_pending_approval(approval)
    assert events[-1]["event"] == "queue_notify_failed"

    def _raise(*args, **kwargs):
        raise FileNotFoundError("notify-send missing")

    monkeypatch.setattr("voxera.core.queue_daemon.subprocess.run", _raise)
    daemon._notify_pending_approval(approval)
    assert events[-1]["event"] == "queue_notify_failed"


def test_queue_status_and_approvals_list_include_artifacts_and_parse_failures(
    tmp_path, monkeypatch
):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    (queue_dir / "pending" / "approvals").mkdir(parents=True)

    (queue_dir / "pending" / "job-ask-site.json").write_text("{}", encoding="utf-8")
    (queue_dir / "pending" / "job-ask-site.pending.json").write_text("{}", encoding="utf-8")
    (queue_dir / "pending" / "approvals" / "job-ask-site.approval.json").write_text(
        json.dumps(
            {
                "job": "job-ask-site.json",
                "step": 1,
                "skill": "system.open_url",
                "capability": "apps.open",
                "reason": "apps.open -> allow; network.change -> ask",
            }
        ),
        encoding="utf-8",
    )
    (queue_dir / "pending" / "approvals" / "broken.approval.json").write_text(
        "not-json", encoding="utf-8"
    )

    events = []
    monkeypatch.setattr("voxera.core.queue_daemon.log", lambda event: events.append(event))

    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")

    status = daemon.status_snapshot()
    approvals = daemon.approvals_list()

    assert status["counts"]["pending"] == 1
    assert status["counts"]["pending_approvals"] == 2
    assert len(approvals) == 2
    assert any(item.get("job") == "job-ask-site.json" for item in approvals)
    assert any(item.get("skill") == "(unparseable approval artifact)" for item in approvals)
    assert any(
        e.get("event") == "queue_status_parse_failed"
        and e.get("filename") == "broken.approval.json"
        for e in events
    )


def test_resolve_approval_accepts_job_and_approval_filename_variants(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)

    async def _goal_planner(goal, cfg, registry, source="cli", job_ref=None):
        return MissionTemplate(
            id="goal_url",
            title="Goal URL",
            goal=goal,
            steps=[MissionStep(skill_id="system.open_url", args={"url": "https://example.com"})],
        )

    monkeypatch.setattr("voxera.core.queue_daemon.plan_mission", _goal_planner)

    daemon = MissionQueueDaemon(
        queue_root=queue_dir, poll_interval=0.1, mission_log_path=tmp_path / "mission-log.md"
    )
    monkeypatch.setattr(
        daemon.mission_runner.skill_runner.registry,
        "load_entrypoint",
        lambda _mf: lambda **_kwargs: "ok",
    )

    for _idx, ref in enumerate(["job-a", "job-b", "job-c", "job-d"]):
        job = queue_dir / f"{ref}.json"
        job.write_text(json.dumps({"goal": "Open https://example.com"}), encoding="utf-8")
        daemon.process_job_file(job)

    assert daemon.resolve_approval("a", approve=True) is True
    assert daemon.resolve_approval("job-b.json", approve=True) is True
    assert daemon.resolve_approval(str(queue_dir / "pending" / "job-c.json"), approve=True) is True
    assert (
        daemon.resolve_approval(
            str(queue_dir / "pending" / "approvals" / "job-d.approval.json"), approve=True
        )
        is True
    )

    for ref in ["job-a", "job-b", "job-c", "job-d"]:
        _assert_job_moved(queue_dir / "done", f"{ref}.json")


def test_queue_daemon_retries_partial_json_and_stabilizes(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_planner(monkeypatch)
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "inbox").mkdir(parents=True, exist_ok=True)
    job = queue_dir / "inbox" / "partial.json"
    job.write_text("", encoding="utf-8")

    events = []
    monkeypatch.setattr("voxera.core.queue_daemon.log", lambda event: events.append(event))
    monkeypatch.setattr("voxera.core.queue_daemon._PARSE_RETRY_BACKOFF_S", 0.05)

    daemon = MissionQueueDaemon(
        queue_root=queue_dir, poll_interval=0.1, mission_log_path=tmp_path / "mission-log.md"
    )

    def _finish_write():
        time.sleep(0.08)
        job.write_text(json.dumps({"goal": "check machine"}), encoding="utf-8")

    writer = threading.Thread(target=_finish_write)
    writer.start()
    daemon.process_pending_once()
    writer.join(timeout=1)

    _assert_job_moved(queue_dir / "done", "partial.json")
    assert not any((queue_dir / "failed").glob("partial*.json"))
    assert any(e.get("event") == "queue_job_retry_parse" for e in events)
    assert any(e.get("event") == "queue_job_parse_stabilized" for e in events)


def test_queue_daemon_ignores_non_job_artifacts_in_inbox(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_planner(monkeypatch)
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)

    (queue_dir / "inbox").mkdir(parents=True, exist_ok=True)
    (queue_dir / "inbox" / "good.json").write_text(
        json.dumps({"goal": "check machine"}), encoding="utf-8"
    )
    (queue_dir / ".hidden.json").write_text(json.dumps({"goal": "check machine"}), encoding="utf-8")
    (queue_dir / "skip.pending.json").write_text(
        json.dumps({"goal": "check machine"}), encoding="utf-8"
    )
    (queue_dir / "skip.approval.json").write_text(
        json.dumps({"goal": "check machine"}), encoding="utf-8"
    )
    (queue_dir / "skip.tmp.json").write_text(
        json.dumps({"goal": "check machine"}), encoding="utf-8"
    )
    (queue_dir / "skip.partial.json").write_text(
        json.dumps({"goal": "check machine"}), encoding="utf-8"
    )
    (queue_dir / "scratch.tmp").write_text("{}", encoding="utf-8")

    daemon = MissionQueueDaemon(
        queue_root=queue_dir, poll_interval=0.1, mission_log_path=tmp_path / "mission-log.md"
    )
    processed = daemon.process_pending_once()

    assert processed == 1
    assert (queue_dir / "done" / "good.json").exists()
    assert (queue_dir / ".hidden.json").exists()
    assert (queue_dir / "skip.pending.json").exists()
    assert (queue_dir / "skip.approval.json").exists()
    assert (queue_dir / "skip.tmp.json").exists()
    assert (queue_dir / "skip.partial.json").exists()


def test_queue_daemon_persistent_invalid_json_fails_after_retries(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    job = queue_dir / "broken.json"
    job.write_text("{", encoding="utf-8")

    events = []
    monkeypatch.setattr("voxera.core.queue_daemon.log", lambda event: events.append(event))
    monkeypatch.setattr("voxera.core.queue_daemon._PARSE_RETRY_BACKOFF_S", 0.01)

    daemon = MissionQueueDaemon(
        queue_root=queue_dir, poll_interval=0.1, mission_log_path=tmp_path / "mission-log.md"
    )
    daemon.process_pending_once()

    failed_job = _assert_job_moved(queue_dir / "failed", "broken.json")
    assert failed_job.with_name(f"{failed_job.stem}.error.json").exists()
    retry_events = [e for e in events if e.get("event") == "queue_job_retry_parse"]
    assert len(retry_events) >= 1
    failed = [e for e in events if e.get("event") == "queue_job_failed"]
    assert failed
    assert "JSONDecodeError" in failed[-1].get("error", "")


def test_failed_sidecar_schema_for_parse_failure(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    job = queue_dir / "bad-json.json"
    job.write_text("{", encoding="utf-8")

    daemon = MissionQueueDaemon(
        queue_root=queue_dir, poll_interval=0.1, mission_log_path=tmp_path / "mission-log.md"
    )
    daemon.process_pending_once()

    failed_job = _assert_job_moved(queue_dir / "failed", "bad-json.json")
    sidecar = json.loads(
        failed_job.with_name(f"{failed_job.stem}.error.json").read_text(encoding="utf-8")
    )
    assert sidecar["schema_version"] == 1
    assert sidecar["job"] == failed_job.name
    assert isinstance(sidecar["error"], str) and sidecar["error"]
    assert isinstance(sidecar["timestamp_ms"], int) and sidecar["timestamp_ms"] > 10**12
    assert "payload" not in sidecar


def test_failed_sidecar_schema_for_runtime_failure_with_payload(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_planner(monkeypatch)
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    job = queue_dir / "runtime-fail.json"
    job.write_text(json.dumps({"goal": "check machine"}), encoding="utf-8")

    daemon = MissionQueueDaemon(
        queue_root=queue_dir, poll_interval=0.1, mission_log_path=tmp_path / "mission-log.md"
    )
    monkeypatch.setattr(
        daemon.mission_runner,
        "run",
        lambda *_args, **_kwargs: RunResult(ok=False, error="runtime exploded"),
    )

    daemon.process_pending_once()
    failed_job = _assert_job_moved(queue_dir / "failed", "runtime-fail.json")
    sidecar = json.loads(
        failed_job.with_name(f"{failed_job.stem}.error.json").read_text(encoding="utf-8")
    )
    assert sidecar["schema_version"] == 1
    assert sidecar["job"] == failed_job.name
    assert sidecar["error"] == "runtime exploded"
    assert sidecar["payload"] == {"goal": "check machine"}


def test_failed_sidecar_schema_for_approval_denied(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    (queue_dir / "pending" / "approvals").mkdir(parents=True, exist_ok=True)

    (queue_dir / "pending" / "deny.json").write_text(
        json.dumps({"goal": "Open https://example.com"}), encoding="utf-8"
    )
    (queue_dir / "pending" / "deny.pending.json").write_text(
        json.dumps(
            {
                "payload": {"goal": "Open https://example.com"},
                "resume_step": 1,
                "mission": {
                    "id": "goal_url",
                    "title": "Goal URL",
                    "goal": "Open",
                    "steps": [
                        {"skill_id": "system.open_url", "args": {"url": "https://example.com"}}
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    (queue_dir / "pending" / "approvals" / "deny.approval.json").write_text(
        json.dumps({"job": "deny.json"}), encoding="utf-8"
    )

    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    daemon.resolve_approval("deny", approve=False)

    failed_job = _assert_job_moved(queue_dir / "failed", "deny.json")
    sidecar = json.loads(
        failed_job.with_name(f"{failed_job.stem}.error.json").read_text(encoding="utf-8")
    )
    assert sidecar["schema_version"] == 1
    assert sidecar["error"] == "Denied in approval inbox"
    assert sidecar["payload"] == {"goal": "Open https://example.com"}


def test_failed_sidecar_schema_for_approval_resume_runtime_failure(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    (queue_dir / "pending" / "approvals").mkdir(parents=True, exist_ok=True)

    (queue_dir / "pending" / "resume.json").write_text(
        json.dumps({"goal": "Open https://example.com"}), encoding="utf-8"
    )
    (queue_dir / "pending" / "resume.pending.json").write_text(
        json.dumps(
            {
                "payload": {"goal": "Open https://example.com"},
                "resume_step": 1,
                "mission": {
                    "id": "goal_url",
                    "title": "Goal URL",
                    "goal": "Open",
                    "steps": [
                        {"skill_id": "system.open_url", "args": {"url": "https://example.com"}}
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    (queue_dir / "pending" / "approvals" / "resume.approval.json").write_text(
        json.dumps({"job": "resume.json"}), encoding="utf-8"
    )

    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    monkeypatch.setattr(
        daemon.mission_runner,
        "run",
        lambda *_args, **_kwargs: RunResult(ok=False, error="resume runtime failed"),
    )

    daemon.resolve_approval("resume", approve=True)
    failed_job = _assert_job_moved(queue_dir / "failed", "resume.json")
    sidecar = json.loads(
        failed_job.with_name(f"{failed_job.stem}.error.json").read_text(encoding="utf-8")
    )
    assert sidecar["schema_version"] == 1
    assert sidecar["error"] == "resume runtime failed"
    assert sidecar["payload"] == {"goal": "Open https://example.com"}


def test_failed_sidecar_schema_version_policy_rejects_unknown_future_version(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    events = []
    monkeypatch.setattr("voxera.core.queue_daemon.log", lambda event: events.append(event))

    queue_dir = tmp_path / "queue"
    (queue_dir / "failed").mkdir(parents=True, exist_ok=True)
    failed_job = queue_dir / "failed" / "bad1.json"
    failed_job.write_text("{}", encoding="utf-8")
    failed_job.with_name("bad1.error.json").write_text(
        json.dumps(
            {
                "schema_version": 999,
                "job": "bad1.json",
                "error": "future schema",
                "timestamp_ms": int(time.time() * 1000),
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "voxera.core.queue_daemon.tail",
        lambda _n: [{"event": "queue_job_failed", "job": str(failed_job), "error": "from-audit"}],
    )

    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    status = daemon.status_snapshot()

    assert status["recent_failed"][0] == {"job": "bad1.json", "error": "from-audit"}
    invalid_events = [e for e in events if e.get("event") == "queue_failed_sidecar_invalid"]
    assert len(invalid_events) == 1
    assert "unsupported failed sidecar schema version for read" in invalid_events[0]["error"]


def test_queue_failure_lifecycle_smoke_sidecar_snapshot_then_prune(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_planner(monkeypatch)
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    job = queue_dir / "job-fail.json"
    job.write_text(json.dumps({"goal": "check machine"}), encoding="utf-8")

    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    monkeypatch.setattr(
        daemon.mission_runner,
        "run",
        lambda *_args, **_kwargs: RunResult(ok=False, error="runtime exploded"),
    )

    daemon.process_pending_once()
    failed_job = _assert_job_moved(queue_dir / "failed", "job-fail.json")
    sidecar_path = failed_job.with_name(f"{failed_job.stem}.error.json")
    assert sidecar_path.exists()

    monkeypatch.setattr(
        "voxera.core.queue_daemon.tail",
        lambda _n: [
            {
                "event": "queue_job_failed",
                "job": str(failed_job),
                "error": "from-audit",
            }
        ],
    )
    status_before = daemon.status_snapshot()
    assert status_before["counts"]["failed"] == 1
    assert status_before["recent_failed"][0] == {
        "job": failed_job.name,
        "error": "runtime exploded",
    }

    result = daemon.prune_failed_artifacts(max_count=0)
    assert result == {"removed_jobs": 1, "removed_sidecars": 1}
    assert not failed_job.exists()
    assert not sidecar_path.exists()

    status_after = daemon.status_snapshot()
    assert status_after["counts"]["failed"] == 0
    assert status_after["recent_failed"] == []


def test_prune_failed_artifacts_with_pairs_and_orphans(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    daemon = MissionQueueDaemon(
        queue_root=tmp_path / "queue", mission_log_path=tmp_path / "mission-log.md"
    )
    daemon.ensure_dirs()

    now = time.time()

    def _touch(name: str, age_s: float):
        path = daemon.failed / name
        path.write_text("{}", encoding="utf-8")
        ts = now - age_s
        os.utime(path, (ts, ts))
        return path

    _touch("a.json", 10)
    _touch("a.error.json", 10)
    _touch("b.json", 20)
    _touch("orphan-sidecar.error.json", 30)
    _touch("orphan-job.json", 40)
    _touch("old.json", 500)
    _touch("old.error.json", 500)

    result = daemon.prune_failed_artifacts(max_age_s=200, max_count=3)
    assert result == {"removed_jobs": 2, "removed_sidecars": 1}

    remaining_primary = sorted(
        p.name for p in daemon.failed.glob("*.json") if daemon._is_primary_job_json(p)
    )
    remaining_sidecars = sorted(p.name for p in daemon.failed.glob("*.error.json"))
    assert remaining_primary == ["a.json", "b.json"]
    assert remaining_sidecars == ["a.error.json", "orphan-sidecar.error.json"]
    assert not (daemon.failed / "old.json").exists()
    assert not (daemon.failed / "old.error.json").exists()


def test_process_job_file_missing_source_during_failed_move_is_non_fatal(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_planner(monkeypatch)
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    job = queue_dir / "job-race.json"
    job.write_text(json.dumps({"goal": "check machine"}), encoding="utf-8")

    events = []
    monkeypatch.setattr("voxera.core.queue_daemon.log", lambda e: events.append(e))

    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    monkeypatch.setattr(
        daemon.mission_runner, "run", lambda *_args, **_kwargs: RunResult(ok=False, error="boom")
    )

    original_move = daemon._move_job

    def _race_move(src, target_dir):
        src.unlink(missing_ok=True)
        return original_move(src, target_dir)

    monkeypatch.setattr(daemon, "_move_job", _race_move)

    assert daemon.process_job_file(job) is False
    assert not (queue_dir / "failed" / "job-race.json").exists()
    assert any(e.get("event") == "queue_job_already_moved" for e in events)


def test_resolve_approval_missing_source_during_done_move_is_non_fatal(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)

    async def _goal_planner(goal, cfg, registry, source="cli", job_ref=None):
        return MissionTemplate(
            id="goal_status",
            title="Goal Status",
            goal=goal,
            steps=[MissionStep(skill_id="system.status", args={})],
        )

    monkeypatch.setattr("voxera.core.queue_daemon.plan_mission", _goal_planner)

    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")

    events = []
    monkeypatch.setattr("voxera.core.queue_daemon.log", lambda e: events.append(e))

    job = queue_dir / "job-approve-race.json"
    job.write_text(json.dumps({"goal": "check machine"}), encoding="utf-8")
    assert daemon.process_job_file(job) is True

    pending_job = queue_dir / "pending" / "job-approve-race.json"
    pending_job.write_text(json.dumps({"goal": "check machine"}), encoding="utf-8")
    meta_path = queue_dir / "pending" / "job-approve-race.pending.json"
    meta_path.write_text(
        json.dumps(
            {
                "status": "pending_approval",
                "payload": {"goal": "check machine"},
                "mission": {
                    "id": "goal_status",
                    "title": "Goal Status",
                    "goal": "check machine",
                    "steps": [{"skill_id": "system.status", "args": {}}],
                },
                "resume_step": 1,
            }
        ),
        encoding="utf-8",
    )
    artifact_path = queue_dir / "pending" / "approvals" / "job-approve-race.approval.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps({"job": "job-approve-race.json"}), encoding="utf-8")

    original_move = daemon._move_job

    def _race_move(src, target_dir):
        if src == pending_job and target_dir == daemon.done:
            src.unlink(missing_ok=True)
        return original_move(src, target_dir)

    monkeypatch.setattr(daemon, "_move_job", _race_move)

    assert daemon.resolve_approval("job-approve-race", approve=True) is False
    assert not meta_path.exists()
    assert not artifact_path.exists()
    assert any(e.get("event") == "queue_job_already_moved" for e in events)


def test_move_job_collision_uses_timestamp_suffix_and_sidecar_matches_target_name(
    tmp_path, monkeypatch
):
    _force_policy_ask(monkeypatch)
    _stub_planner(monkeypatch)
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "failed").mkdir(parents=True, exist_ok=True)
    (queue_dir / "failed" / "dup.json").write_text("{}", encoding="utf-8")

    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    monkeypatch.setattr(
        daemon.mission_runner, "run", lambda *_args, **_kwargs: RunResult(ok=False, error="boom")
    )

    src = queue_dir / "dup.json"
    src.write_text(json.dumps({"goal": "check machine"}), encoding="utf-8")
    daemon.process_job_file(src)

    moved_matches = sorted((queue_dir / "failed").glob("dup-*.json"))
    assert moved_matches
    moved = moved_matches[-1]
    sidecar = json.loads(moved.with_name(f"{moved.stem}.error.json").read_text(encoding="utf-8"))
    assert sidecar["job"] == moved.name


def test_queue_job_write_notes_defaults_to_relative_ok_txt_end_to_end(tmp_path, monkeypatch):
    cfg = AppConfig(
        policy=PolicyApprovals(system_settings="ask", network_changes="ask"),
        privacy=PrivacyConfig(cloud_allowed=False, redact_logs=True),
    )
    monkeypatch.setattr("voxera.core.queue_daemon.load_config", lambda: cfg)

    allowed_root = tmp_path / "notes"
    monkeypatch.setattr(files_write_text_skill, "ALLOWED_ROOT", allowed_root)

    queue_dir = tmp_path / "queue"
    job = queue_dir / "job-write-notes.json"
    queue_dir.mkdir(parents=True, exist_ok=True)
    job.write_text(
        json.dumps(
            {"goal": "Write a note under the allowed notes directory saying: queue e2e ok."}
        ),
        encoding="utf-8",
    )

    daemon = MissionQueueDaemon(
        queue_root=queue_dir, poll_interval=0.1, mission_log_path=tmp_path / "mission-log.md"
    )
    daemon.process_pending_once()

    _assert_job_moved(queue_dir / "done", "job-write-notes.json")
    assert (allowed_root / "ok.txt").exists()
    assert "queue e2e ok." in (allowed_root / "ok.txt").read_text(encoding="utf-8")


def test_queue_job_sandbox_argv_goal_reaches_done(tmp_path, monkeypatch):
    cfg = AppConfig(
        policy=PolicyApprovals(system_settings="allow", network_changes="allow"),
        privacy=PrivacyConfig(redact_logs=False),
    )
    monkeypatch.setattr("voxera.core.queue_daemon.load_config", lambda: cfg)

    queue_dir = tmp_path / "queue"
    job = queue_dir / "job-sandbox-argv.json"
    queue_dir.mkdir(parents=True, exist_ok=True)
    job.write_text(json.dumps({"goal": "Run command to print HELLO-ARGV"}), encoding="utf-8")

    monkeypatch.setattr(
        "voxera.core.mission_planner._build_brain_candidates",
        lambda _cfg: [
            type(
                "C",
                (),
                {
                    "name": "primary",
                    "model": "primary-model",
                    "brain": type(
                        "B",
                        (),
                        {"generate": staticmethod(_fake_generate_for_sandbox_argv)},
                    )(),
                },
            )()
        ],
    )

    events = []
    monkeypatch.setattr("voxera.skills.runner.log", lambda event: events.append(event))

    daemon = MissionQueueDaemon(
        queue_root=queue_dir, poll_interval=0.1, mission_log_path=tmp_path / "mission-log.md"
    )
    monkeypatch.setattr(
        daemon.mission_runner.skill_runner.registry,
        "load_entrypoint",
        lambda _manifest: lambda **_kwargs: "ok",
    )

    class _Runner:
        runner_name = "unit"

        @staticmethod
        def run(manifest, args, fn, cfg, job_id):
            assert manifest.id == "sandbox.exec"
            assert isinstance(args.get("command"), list)
            assert args["command"] == ["bash", "-lc", "echo HELLO-ARGV"]
            return RunResult(ok=True, output="ok")

    monkeypatch.setattr("voxera.skills.runner.select_runner", lambda _manifest: _Runner())
    daemon.process_pending_once()

    _assert_job_moved(queue_dir / "done", "job-sandbox-argv.json")
    assert not any((queue_dir / "failed").glob("job-sandbox-argv*.json"))

    skill_start = next(
        event
        for event in events
        if event.get("event") == "skill_start" and event.get("skill") == "sandbox.exec"
    )
    assert skill_start["args"]["command"] == ["bash", "-lc", "echo HELLO-ARGV"]


def test_pending_approval_payload_includes_target_scope_and_policy_reason(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    job = queue_dir / "approval-url.json"
    job.write_text(json.dumps({"goal": "Open https://example.com"}), encoding="utf-8")

    async def _goal_planner(goal, cfg, registry, source="cli", job_ref=None):
        return MissionTemplate(
            id="goal_url",
            title="Goal URL",
            goal=goal,
            steps=[MissionStep(skill_id="system.open_url", args={"url": "https://example.com"})],
        )

    monkeypatch.setattr("voxera.core.queue_daemon.plan_mission", _goal_planner)

    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    monkeypatch.setattr(
        daemon.mission_runner.skill_runner.registry,
        "load_entrypoint",
        lambda _mf: lambda **_kwargs: "ok",
    )

    daemon.process_pending_once()
    artifact_path = queue_dir / "pending" / "approvals" / "approval-url.approval.json"
    approval = json.loads(artifact_path.read_text(encoding="utf-8"))

    assert approval["target"] == {"type": "url", "value": "https://example.com"}
    assert approval["fs_scope"] == "broader"
    assert approval["needs_network"] is True
    assert approval["scope"]["fs_scope"] == "broader"
    assert approval["scope"]["needs_network"] is True
    assert "policy_reason" in approval


def test_approval_always_grant_allows_matching_scope_only(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)

    async def _goal_planner(goal, cfg, registry, source="cli", job_ref=None):
        if "example" in goal:
            return MissionTemplate(
                id="goal_url",
                title="Goal URL",
                goal=goal,
                steps=[
                    MissionStep(skill_id="system.open_url", args={"url": "https://example.com"})
                ],
            )
        return MissionTemplate(
            id="goal_settings",
            title="Goal Settings",
            goal=goal,
            steps=[MissionStep(skill_id="system.set_volume", args={"percent": "15"})],
        )

    monkeypatch.setattr("voxera.core.queue_daemon.plan_mission", _goal_planner)

    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    monkeypatch.setattr(
        daemon.mission_runner.skill_runner.registry,
        "load_entrypoint",
        lambda _mf: lambda **_kwargs: "ok",
    )

    (queue_dir / "job1.json").write_text(json.dumps({"goal": "Open example"}), encoding="utf-8")
    daemon.process_pending_once()
    assert (queue_dir / "pending" / "job1.json").exists()

    daemon.resolve_approval("job1", approve=True, approve_always=True)
    assert (queue_dir / "done" / "job1.json").exists()

    (queue_dir / "job2.json").write_text(
        json.dumps({"goal": "Open example again"}), encoding="utf-8"
    )
    daemon.process_pending_once()
    assert (queue_dir / "done" / "job2.json").exists()

    (queue_dir / "job3.json").write_text(json.dumps({"goal": "change volume"}), encoding="utf-8")
    daemon.process_pending_once()
    assert (queue_dir / "pending" / "job3.json").exists()


def test_job_artifacts_written_for_done_and_pending(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)

    async def _goal_planner(goal, cfg, registry, source="cli", job_ref=None):
        return MissionTemplate(
            id="goal_url",
            title="Goal URL",
            goal=goal,
            steps=[MissionStep(skill_id="system.open_url", args={"url": "https://example.com"})],
        )

    monkeypatch.setattr("voxera.core.queue_daemon.plan_mission", _goal_planner)

    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    monkeypatch.setattr(
        daemon.mission_runner.skill_runner.registry,
        "load_entrypoint",
        lambda _mf: lambda **_kwargs: "ok",
    )

    (queue_dir / "art.json").write_text(json.dumps({"goal": "Open example"}), encoding="utf-8")
    daemon.process_pending_once()

    art_dir = queue_dir / "artifacts" / "art"
    assert (art_dir / "plan.json").exists()
    assert (art_dir / "actions.jsonl").exists()

    daemon.resolve_approval("art", approve=True)
    assert (art_dir / "stdout.txt").exists()
    assert (art_dir / "stderr.txt").exists()


def test_pending_approvals_snapshot_scope_fallback_to_nested(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    (queue_dir / "pending" / "approvals").mkdir(parents=True)

    (queue_dir / "pending" / "approvals" / "job-scope.approval.json").write_text(
        json.dumps(
            {
                "job": "job-scope.json",
                "step": 1,
                "skill": "system.open_url",
                "reason": "network_changes -> ask",
                "scope": {"fs_scope": "broader", "needs_network": True},
            }
        ),
        encoding="utf-8",
    )

    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    snapshot = daemon.pending_approvals_snapshot(limit=4)

    assert snapshot[0]["fs_scope"] == "broader"
    assert snapshot[0]["needs_network"] is True
    assert snapshot[0]["scope"] == {"fs_scope": "broader", "needs_network": True}


def test_queue_autorelocates_legacy_root_job(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_planner(monkeypatch)
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True)
    (queue_dir / "legacy.json").write_text('{"goal":"check machine"}', encoding="utf-8")
    events = []
    monkeypatch.setattr("voxera.core.queue_daemon.log", lambda e: events.append(e))
    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    daemon.process_pending_once()
    assert (queue_dir / "done" / "legacy.json").exists()
    assert any(e.get("event") == "queue_job_autorelocate" for e in events)


def test_queue_autorelocates_misplaced_pending_job(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_planner(monkeypatch)
    queue_dir = tmp_path / "queue"
    (queue_dir / "pending").mkdir(parents=True)
    (queue_dir / "pending" / "wrong.json").write_text('{"goal":"check machine"}', encoding="utf-8")
    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    daemon.process_pending_once()
    assert (queue_dir / "done" / "wrong.json").exists()


def test_cancel_retry_pause_flow(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_planner(monkeypatch)
    queue_dir = tmp_path / "queue"
    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    daemon.ensure_dirs()
    job = queue_dir / "inbox" / "cancel-me.json"
    job.write_text('{"goal":"check machine"}', encoding="utf-8")

    daemon.pause()
    assert daemon.process_pending_once() == 0
    assert job.exists()
    daemon.resume()

    failed = daemon.cancel_job("cancel-me.json")
    assert failed.exists()
    assert (queue_dir / "failed" / "cancel-me.error.json").exists()

    retried = daemon.retry_job("cancel-me.json")
    assert retried.exists()
    daemon.process_pending_once()
    assert (queue_dir / "done" / "cancel-me.json").exists()


def test_cancel_pending_approval_cleans_markers(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    (queue_dir / "pending" / "approvals").mkdir(parents=True)
    (queue_dir / "pending" / "x.json").write_text('{"goal":"g"}', encoding="utf-8")
    (queue_dir / "pending" / "x.pending.json").write_text("{}", encoding="utf-8")
    (queue_dir / "pending" / "approvals" / "x.approval.json").write_text("{}", encoding="utf-8")
    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    daemon.cancel_job("x.json")
    assert not (queue_dir / "pending" / "x.pending.json").exists()
    assert not (queue_dir / "pending" / "approvals" / "x.approval.json").exists()
    assert (queue_dir / "failed" / "x.error.json").exists()


def test_run_acquires_and_releases_lock_in_once_mode(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_planner(monkeypatch)
    events = []
    monkeypatch.setattr("voxera.core.queue_daemon.log", lambda e: events.append(e))
    queue_dir = tmp_path / "queue"
    (queue_dir / "inbox").mkdir(parents=True)
    (queue_dir / "inbox" / "job1.json").write_text('{"goal":"check machine"}', encoding="utf-8")

    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    daemon.run(once=True)

    assert (queue_dir / "done" / "job1.json").exists()
    assert not (queue_dir / ".daemon.lock").exists()
    counters = daemon.lock_counters_snapshot()
    assert counters.get("lock_acquire_ok", 0) >= 1
    assert counters.get("lock_released", 0) >= 1
    assert (queue_dir / "health.json").exists()
    emitted = {e.get("event") for e in events}
    assert "queue_daemon_lock_acquired" in emitted
    assert "queue_daemon_lock_released" in emitted


def test_run_refuses_when_active_lock_exists(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    events = []
    monkeypatch.setattr("voxera.core.queue_daemon.log", lambda e: events.append(e))
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True)
    lock = queue_dir / ".daemon.lock"
    lock.write_text(json.dumps({"pid": os.getpid(), "ts": time.time()}), encoding="utf-8")

    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    with pytest.raises(Exception, match="queue lock already held"):
        daemon.run(once=True)
    counters = daemon.lock_counters_snapshot()
    assert counters.get("lock_acquire_fail", 0) >= 1
    contended = [e for e in events if e.get("event") == "queue_daemon_lock_contended"]
    assert contended
    assert contended[-1].get("details", {}).get("existing_pid") == os.getpid()


def test_run_reclaims_stale_lock(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_planner(monkeypatch)
    events = []
    monkeypatch.setattr("voxera.core.queue_daemon.log", lambda e: events.append(e))
    queue_dir = tmp_path / "queue"
    (queue_dir / "inbox").mkdir(parents=True)
    (queue_dir / "inbox" / "job1.json").write_text('{"goal":"check machine"}', encoding="utf-8")
    lock = queue_dir / ".daemon.lock"
    lock.write_text(json.dumps({"pid": 999999, "ts": 1}), encoding="utf-8")

    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    daemon.run(once=True)
    assert (queue_dir / "done" / "job1.json").exists()
    assert not lock.exists()
    counters = daemon.lock_counters_snapshot()
    assert counters.get("lock_reclaimed", 0) >= 1
    reclaimed = [e for e in events if e.get("event") == "queue_daemon_lock_reclaimed"]
    assert reclaimed
    assert reclaimed[-1].get("details", {}).get("existing_pid") == 999999


def test_try_unlock_stale_refuses_live_lock(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    events = []
    monkeypatch.setattr("voxera.core.queue_daemon.log", lambda e: events.append(e))
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True)
    lock = queue_dir / ".daemon.lock"
    lock.write_text(json.dumps({"pid": os.getpid(), "ts": time.time()}), encoding="utf-8")

    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    with pytest.raises(QueueLockError, match="Lock held by live pid="):
        daemon.try_unlock_stale()
    assert lock.exists()
    counters = daemon.lock_counters_snapshot()
    assert counters.get("unlock_refused", 0) >= 1
    refused = [e for e in events if e.get("event") == "queue_daemon_unlock_refused"]
    assert refused


def test_try_unlock_stale_removes_dead_or_stale_lock(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    events = []
    monkeypatch.setattr("voxera.core.queue_daemon.log", lambda e: events.append(e))
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True)
    lock = queue_dir / ".daemon.lock"
    lock.write_text(json.dumps({"pid": 999999, "timestamp": 1}), encoding="utf-8")

    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    assert daemon.try_unlock_stale() is True
    assert not lock.exists()
    counters = daemon.lock_counters_snapshot()
    assert counters.get("unlock_ok", 0) >= 1
    unlocked = [e for e in events if e.get("event") == "queue_daemon_unlock_ok"]
    assert unlocked


def test_force_unlock_logs_dangerous_event(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    events = []
    monkeypatch.setattr("voxera.core.queue_daemon.log", lambda e: events.append(e))
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True)
    lock = queue_dir / ".daemon.lock"
    lock.write_text(json.dumps({"pid": 1234, "ts": 1}), encoding="utf-8")

    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    assert daemon.force_unlock() is True
    emitted = [e for e in events if e.get("event") == "queue_daemon_lock_force_unlocked"]
    assert emitted
    assert emitted[-1].get("details", {}).get("dangerous") is True
    counters = daemon.lock_counters_snapshot()
    assert counters.get("force_unlock_count", 0) >= 1
