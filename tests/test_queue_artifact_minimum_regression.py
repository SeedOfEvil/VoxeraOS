from __future__ import annotations

import json

from voxera.core.queue_daemon import MissionQueueDaemon


def test_action_event_refreshes_execution_result_minimum_artifacts_from_disk(tmp_path) -> None:
    queue_root = tmp_path / "queue"
    daemon = MissionQueueDaemon(queue_root=queue_root)
    job_ref = "done/job-minimum.json"
    artifacts = daemon._job_artifacts_dir(job_ref)

    for name in (
        "execution_envelope.json",
        "execution_result.json",
        "step_results.json",
        "job_intent.json",
        "plan.json",
    ):
        (artifacts / name).write_text("{}", encoding="utf-8")

    seeded = {
        "artifact_families": ["execution_result", "step_results"],
        "artifact_refs": [
            {
                "artifact_family": "step_results",
                "artifact_path": "step_results.json",
                "exists": True,
            }
        ],
        "review_summary": {
            "expected_artifacts": [
                "execution_envelope.json",
                "execution_result.json",
                "step_results.json",
                "job_intent.json",
                "plan.json",
                "actions.jsonl",
            ],
            "expected_artifact_status": "missing",
            "observed_expected_artifacts": [],
            "missing_expected_artifacts": ["execution_result.json", "actions.jsonl"],
            "minimum_artifacts": {
                "status": "missing",
                "required": [],
                "observed": [],
                "missing": ["execution_result.json", "actions.jsonl"],
            },
        },
        "evidence_bundle": {
            "expected_artifacts": {"status": "missing"},
            "minimum_artifacts": {"status": "missing", "missing": ["actions.jsonl"]},
        },
    }
    (artifacts / "execution_result.json").write_text(json.dumps(seeded), encoding="utf-8")

    daemon._write_action_event(job_ref, "queue_job_done")

    refreshed = json.loads((artifacts / "execution_result.json").read_text(encoding="utf-8"))
    minimum = refreshed["review_summary"]["minimum_artifacts"]
    assert minimum["status"] == "observed"
    assert minimum["missing"] == []
    assert "execution_result.json" in minimum["observed"]
    assert "actions.jsonl" in minimum["observed"]
    assert refreshed["evidence_bundle"]["minimum_artifacts"]["status"] == "observed"
