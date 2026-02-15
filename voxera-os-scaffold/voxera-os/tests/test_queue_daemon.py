import json
import sys
import types

import pytest

from voxera.core.queue_daemon import MissionQueueDaemon


def test_queue_daemon_processes_json_job_to_done(tmp_path):
    queue_dir = tmp_path / "queue"
    job = queue_dir / "job1.json"
    job.parent.mkdir(parents=True, exist_ok=True)
    job.write_text(json.dumps({"mission_id": "system_check"}), encoding="utf-8")

    daemon = MissionQueueDaemon(
        queue_root=queue_dir,
        poll_interval=0.1,
        mission_log_path=tmp_path / "mission-log.md",
    )

    count = daemon.process_pending_once()

    assert count == 1
    assert not job.exists()
    assert (queue_dir / "done" / "job1.json").exists()
    assert daemon.stats.processed == 1
    assert daemon.stats.failed == 0


def test_queue_daemon_moves_invalid_job_to_failed(tmp_path):
    queue_dir = tmp_path / "queue"
    job = queue_dir / "bad.json"
    job.parent.mkdir(parents=True, exist_ok=True)
    job.write_text("not-json", encoding="utf-8")

    daemon = MissionQueueDaemon(
        queue_root=queue_dir,
        poll_interval=0.1,
        mission_log_path=tmp_path / "mission-log.md",
    )

    count = daemon.process_pending_once()

    assert count == 1
    assert not job.exists()
    assert (queue_dir / "failed" / "bad.json").exists()
    assert daemon.stats.processed == 0
    assert daemon.stats.failed == 1


def test_queue_daemon_rejects_ask_policy_without_approval(tmp_path):
    queue_dir = tmp_path / "queue"
    job = queue_dir / "approval.json"
    job.parent.mkdir(parents=True, exist_ok=True)
    job.write_text(json.dumps({"mission_id": "work_mode"}), encoding="utf-8")

    daemon = MissionQueueDaemon(
        queue_root=queue_dir,
        poll_interval=0.1,
        mission_log_path=tmp_path / "mission-log.md",
    )

    daemon.process_pending_once()

    assert (queue_dir / "failed" / "approval.json").exists()
    assert not (queue_dir / "done" / "approval.json").exists()


def test_queue_daemon_watchdog_mode_processes_existing_backlog(tmp_path, monkeypatch):
    queue_dir = tmp_path / "queue"
    job = queue_dir / "job1.json"
    job.parent.mkdir(parents=True, exist_ok=True)
    job.write_text(json.dumps({"mission_id": "system_check"}), encoding="utf-8")

    daemon = MissionQueueDaemon(
        queue_root=queue_dir,
        poll_interval=0.1,
        mission_log_path=tmp_path / "mission-log.md",
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
    monkeypatch.setitem(sys.modules, "watchdog.events", types.SimpleNamespace(FileSystemEventHandler=_EventHandler))
    monkeypatch.setitem(sys.modules, "watchdog.observers", types.SimpleNamespace(Observer=_Observer))

    def _interrupt(_seconds: float):
        raise KeyboardInterrupt

    monkeypatch.setattr("voxera.core.queue_daemon.time.sleep", _interrupt)

    with pytest.raises(KeyboardInterrupt):
        daemon.run(once=False)

    assert not job.exists()
    assert (queue_dir / "done" / "job1.json").exists()
