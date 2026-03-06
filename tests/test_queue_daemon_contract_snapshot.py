from __future__ import annotations

import json
from pathlib import Path

from voxera.core.queue_daemon import MissionQueueDaemon
from voxera.models import AppConfig, PolicyApprovals, PrivacyConfig


def _force_policy_ask(monkeypatch) -> None:
    cfg = AppConfig(
        policy=PolicyApprovals(system_settings="ask", network_changes="ask"),
        privacy=PrivacyConfig(redact_logs=True),
    )
    monkeypatch.setattr("voxera.core.queue_daemon.load_config", lambda: cfg)


def test_queue_daemon_startup_recovery_contract_snapshot(tmp_path: Path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    (queue_dir / "pending").mkdir(parents=True, exist_ok=True)
    (queue_dir / "pending" / "job-a.json").write_text(json.dumps({"goal": "x"}), encoding="utf-8")
    (queue_dir / "pending" / "job-a.pending.json").write_text("{}", encoding="utf-8")
    (queue_dir / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
    (queue_dir / "pending" / "approvals" / "job-a.approval.json").write_text("{}", encoding="utf-8")

    daemon = MissionQueueDaemon(queue_root=queue_dir)
    report = daemon.recover_on_startup(now_ms=1700000000123)

    assert report == {
        "ts_ms": 1700000000123,
        "policy": "fail_fast",
        "reason": "recovered_after_restart",
        "message": "daemon recovered from unclean shutdown; job marked failed deterministically",
        "counts": {
            "jobs_failed": 1,
            "orphan_approvals_quarantined": 0,
            "orphan_state_files_quarantined": 0,
            "total_quarantined": 2,
        },
        "jobs_failed": ["job-a"],
        "failed_details": [
            {
                "job_id": "job-a",
                "failed_path": "failed/job-a.json",
                "reason": "recovered_after_restart",
                "message": "daemon recovered from unclean shutdown; job marked failed deterministically",
                "original_bucket": "pending",
                "detected_state_files": ["pending/job-a.pending.json"],
                "detected_artifacts_paths": [],
            }
        ],
        "quarantined_paths": [
            "recovery/startup-1700000000123/pending/approvals/job-a.approval.json",
            "recovery/startup-1700000000123/pending/job-a.pending.json",
        ],
        "recovery_dir": "recovery/startup-1700000000123",
    }


def test_queue_daemon_approval_state_transition_contract_snapshot(tmp_path: Path, monkeypatch):
    _force_policy_ask(monkeypatch)
    queue_dir = tmp_path / "queue"
    (queue_dir / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
    (queue_dir / "pending" / "job-a.json").write_text(json.dumps({"goal": "x"}), encoding="utf-8")
    (queue_dir / "pending" / "job-a.pending.json").write_text(
        json.dumps(
            {
                "payload": {"goal": "x"},
                "resume_step": 1,
                "mission": {
                    "id": "demo",
                    "title": "Demo",
                    "goal": "x",
                    "steps": [{"skill_id": "system.status", "args": {}}],
                },
            }
        ),
        encoding="utf-8",
    )
    (queue_dir / "pending" / "approvals" / "job-a.approval.json").write_text(
        json.dumps({"job": "job-a.json", "step": 1, "skill": "system.status", "reason": "ask"}),
        encoding="utf-8",
    )

    daemon = MissionQueueDaemon(queue_root=queue_dir)
    daemon.resolve_approval("job-a", approve=False)

    failed_job = queue_dir / "failed" / "job-a.json"
    assert failed_job.exists()
    assert not (queue_dir / "pending" / "job-a.json").exists()
    assert not (queue_dir / "pending" / "approvals" / "job-a.approval.json").exists()

    sidecar = json.loads((queue_dir / "failed" / "job-a.error.json").read_text(encoding="utf-8"))
    assert sidecar["schema_version"] == 1
    assert sidecar["job"] == "job-a.json"
    assert "Denied in approval inbox" in sidecar["error"]
