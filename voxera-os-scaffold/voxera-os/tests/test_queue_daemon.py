import json
import sys
import threading
import time
import types
from types import SimpleNamespace

import pytest

from voxera.core.missions import MissionStep, MissionTemplate
from voxera.core.queue_daemon import MissionQueueDaemon
from voxera.models import AppConfig, PolicyApprovals, PrivacyConfig



def _force_policy_ask(monkeypatch, *, redact_logs=True):
    cfg = AppConfig(policy=PolicyApprovals(system_settings="ask", network_changes="ask"), privacy=PrivacyConfig(redact_logs=redact_logs))
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

    daemon = MissionQueueDaemon(queue_root=queue_dir, poll_interval=0.1, mission_log_path=tmp_path / "mission-log.md")
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

    daemon = MissionQueueDaemon(queue_root=queue_dir, poll_interval=0.1, mission_log_path=tmp_path / "mission-log.md")
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

    daemon = MissionQueueDaemon(queue_root=queue_dir, poll_interval=0.1, mission_log_path=tmp_path / "mission-log.md")
    daemon.process_pending_once()

    assert (queue_dir / "failed" / "bad.json").exists()
    assert any("mission_id (or mission) or goal (or plan_goal)" in evt.get("error", "") for evt in events if evt.get("event") == "queue_job_failed")


def test_queue_daemon_accepts_mission_alias(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    job = queue_dir / "job1.json"
    job.parent.mkdir(parents=True, exist_ok=True)
    job.write_text(json.dumps({"mission": "system_check"}), encoding="utf-8")

    daemon = MissionQueueDaemon(queue_root=queue_dir, poll_interval=0.1, mission_log_path=tmp_path / "mission-log.md")
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
    monkeypatch.setattr(daemon.mission_runner.skill_runner.registry, "load_entrypoint", lambda _mf: (lambda **_kwargs: "ok"))
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
    assert (queue_dir / "done" / "approval.json").exists()

    daemon.process_job_file(deny_job)
    deny_artifact = queue_dir / "pending" / "approvals" / "deny.approval.json"
    assert deny_artifact.exists()
    deny_details = json.loads(deny_artifact.read_text(encoding="utf-8"))
    assert deny_details["skill"] == "system.open_url"
    daemon.resolve_approval("deny", approve=False)
    assert (queue_dir / "failed" / "deny.json").exists()
    assert any(e["event"] == "queue_job_failed" and "Denied in approval inbox" in e.get("error", "") for e in events)
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
    monkeypatch.setattr(daemon_no_dev.mission_runner.skill_runner.registry, "load_entrypoint", lambda _mf: (lambda **_kwargs: "ok"))
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
    monkeypatch.setattr(daemon_dev.mission_runner.skill_runner.registry, "load_entrypoint", lambda _mf: (lambda **_kwargs: "ok"))
    daemon_dev.process_job_file(system_job)
    daemon_dev.process_job_file(net_job)

    assert (queue_dir / "done" / "system.json").exists()
    assert (queue_dir / "pending" / "network.json").exists()
    assert any(
        event["event"] == "queue_auto_approved"
        and event.get("capability") == "system.settings"
        for event in dev_events
    )
    assert not any(
        event["event"] == "queue_auto_approved"
        and event.get("capability") == "network.change"
        for event in dev_events
    )
    assert any(
        event["event"] == "queue_approval_required"
        and event.get("skill") == "system.open_url"
        for event in dev_events
    )


def test_queue_daemon_watchdog_mode_processes_existing_backlog(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    job = queue_dir / "job1.json"
    job.parent.mkdir(parents=True, exist_ok=True)
    job.write_text(json.dumps({"mission_id": "system_check"}), encoding="utf-8")

    daemon = MissionQueueDaemon(queue_root=queue_dir, poll_interval=0.1, mission_log_path=tmp_path / "mission-log.md")

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
    monkeypatch.setitem(sys.modules, "watchdog.events", types.SimpleNamespace(FileSystemEventHandler=_EventHandler))
    monkeypatch.setitem(sys.modules, "watchdog.observers", types.SimpleNamespace(Observer=_Observer))

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

    assert status["counts"] == {"pending": 1, "pending_approvals": 1, "done": 1, "failed": 1}
    assert status["pending_approvals"][0]["skill"] == "system.set_volume"
    assert status["recent_failed"][0] == {"job": "bad1.json", "error": "boom"}


def test_status_snapshot_fresh_install_without_queue_dirs(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "missing-queue"

    daemon = MissionQueueDaemon(queue_root=queue_dir, mission_log_path=tmp_path / "mission-log.md")
    status = daemon.status_snapshot()

    assert status["exists"] is False
    assert status["counts"] == {"pending": 0, "pending_approvals": 0, "done": 0, "failed": 0}
    assert status["pending_approvals"] == []
    assert status["recent_failed"] == []


def test_pending_approval_notification_success_and_failure_events(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    daemon = MissionQueueDaemon(queue_root=tmp_path / "queue", mission_log_path=tmp_path / "mission-log.md")

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


def test_queue_status_and_approvals_list_include_artifacts_and_parse_failures(tmp_path, monkeypatch):
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
    (queue_dir / "pending" / "approvals" / "broken.approval.json").write_text("not-json", encoding="utf-8")

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
    assert any(e.get("event") == "queue_status_parse_failed" and e.get("filename") == "broken.approval.json" for e in events)


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

    daemon = MissionQueueDaemon(queue_root=queue_dir, poll_interval=0.1, mission_log_path=tmp_path / "mission-log.md")
    monkeypatch.setattr(daemon.mission_runner.skill_runner.registry, "load_entrypoint", lambda _mf: (lambda **_kwargs: "ok"))

    for idx, ref in enumerate(["job-a", "job-b", "job-c", "job-d"]):
        job = queue_dir / f"{ref}.json"
        job.write_text(json.dumps({"goal": "Open https://example.com"}), encoding="utf-8")
        daemon.process_job_file(job)

    assert daemon.resolve_approval("job-a", approve=True) is True
    assert daemon.resolve_approval("job-b.json", approve=True) is True
    assert daemon.resolve_approval("job-c.approval", approve=True) is True
    assert daemon.resolve_approval("job-d.approval.json", approve=True) is True

    for ref in ["job-a", "job-b", "job-c", "job-d"]:
        assert (queue_dir / "done" / f"{ref}.json").exists()



def test_queue_daemon_retries_partial_json_and_stabilizes(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_planner(monkeypatch)
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    job = queue_dir / "partial.json"
    job.write_text("", encoding="utf-8")

    events = []
    monkeypatch.setattr("voxera.core.queue_daemon.log", lambda event: events.append(event))
    monkeypatch.setattr("voxera.core.queue_daemon._PARSE_RETRY_BACKOFF_S", 0.05)

    daemon = MissionQueueDaemon(queue_root=queue_dir, poll_interval=0.1, mission_log_path=tmp_path / "mission-log.md")

    def _finish_write():
        time.sleep(0.08)
        job.write_text(json.dumps({"goal": "check machine"}), encoding="utf-8")

    writer = threading.Thread(target=_finish_write)
    writer.start()
    daemon.process_pending_once()
    writer.join(timeout=1)

    assert (queue_dir / "done" / "partial.json").exists()
    assert not (queue_dir / "failed" / "partial.json").exists()
    assert any(e.get("event") == "queue_job_retry_parse" for e in events)
    assert any(e.get("event") == "queue_job_parse_stabilized" for e in events)


def test_queue_daemon_ignores_non_job_artifacts_in_inbox(tmp_path, monkeypatch):
    _force_policy_ask(monkeypatch)
    _stub_planner(monkeypatch)
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)

    (queue_dir / "good.json").write_text(json.dumps({"goal": "check machine"}), encoding="utf-8")
    (queue_dir / ".hidden.json").write_text(json.dumps({"goal": "check machine"}), encoding="utf-8")
    (queue_dir / "skip.pending.json").write_text(json.dumps({"goal": "check machine"}), encoding="utf-8")
    (queue_dir / "skip.approval.json").write_text(json.dumps({"goal": "check machine"}), encoding="utf-8")
    (queue_dir / "skip.tmp.json").write_text(json.dumps({"goal": "check machine"}), encoding="utf-8")
    (queue_dir / "skip.partial.json").write_text(json.dumps({"goal": "check machine"}), encoding="utf-8")
    (queue_dir / "scratch.tmp").write_text("{}", encoding="utf-8")

    daemon = MissionQueueDaemon(queue_root=queue_dir, poll_interval=0.1, mission_log_path=tmp_path / "mission-log.md")
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

    daemon = MissionQueueDaemon(queue_root=queue_dir, poll_interval=0.1, mission_log_path=tmp_path / "mission-log.md")
    daemon.process_pending_once()

    assert (queue_dir / "failed" / "broken.json").exists()
    retry_events = [e for e in events if e.get("event") == "queue_job_retry_parse"]
    assert len(retry_events) >= 1
    failed = [e for e in events if e.get("event") == "queue_job_failed"]
    assert failed
    assert "JSONDecodeError" in failed[-1].get("error", "")

