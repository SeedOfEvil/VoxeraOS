import json

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
