from __future__ import annotations

import json
from pathlib import Path

from voxera.core.queue_daemon import MissionQueueDaemon
from voxera.panel.assistant import read_assistant_result


def _write_assistant_job(
    queue_root: Path, *, question: str = "What is happening right now?"
) -> Path:
    inbox = queue_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    job = inbox / "job-assistant-test.json"
    job.write_text(
        json.dumps(
            {
                "kind": "assistant_question",
                "question": question,
                "advisory": True,
                "read_only": True,
            }
        ),
        encoding="utf-8",
    )
    return job


def test_assistant_job_runs_through_queue_read_only(tmp_path):
    queue_root = tmp_path / "queue"
    daemon = MissionQueueDaemon(queue_root=queue_root)

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("mission runner should not execute for assistant jobs")

    daemon.mission_runner.run = _fail_if_called  # type: ignore[method-assign]
    job = _write_assistant_job(queue_root)

    processed = daemon.process_job_file(job)
    assert processed is True

    done_job = queue_root / "done" / "job-assistant-test.json"
    assert done_job.exists()
    response_artifact = queue_root / "artifacts" / "job-assistant-test" / "assistant_response.json"
    assert response_artifact.exists()
    payload = json.loads(response_artifact.read_text(encoding="utf-8"))
    assert payload["kind"] == "assistant_question"
    assert "answer" in payload


def test_assistant_job_failure_persists_failed_state(tmp_path):
    queue_root = tmp_path / "queue"
    daemon = MissionQueueDaemon(queue_root=queue_root)
    bad_job = _write_assistant_job(queue_root, question="")

    processed = daemon.process_job_file(bad_job)
    assert processed is False
    failed_job = queue_root / "failed" / "job-assistant-test.json"
    assert failed_job.exists()


def test_assistant_result_reader_surfaces_answer(tmp_path):
    queue_root = tmp_path / "queue"
    (queue_root / "done").mkdir(parents=True, exist_ok=True)
    (queue_root / "done" / "job-assistant-test.json").write_text("{}", encoding="utf-8")
    (queue_root / "artifacts" / "job-assistant-test").mkdir(parents=True, exist_ok=True)
    (queue_root / "artifacts" / "job-assistant-test" / "assistant_response.json").write_text(
        json.dumps({"answer": "From inside Voxera, I see a clear queue.", "updated_at_ms": 1}),
        encoding="utf-8",
    )

    result = read_assistant_result(queue_root, "job-assistant-test.json")
    assert result["status"] == "answered"
    assert "From inside Voxera" in result["answer"]
