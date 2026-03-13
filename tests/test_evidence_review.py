from __future__ import annotations

import json
from pathlib import Path

from voxera.vera.evidence_review import review_job_outcome, review_message


def _write_job(
    queue_root: Path,
    *,
    job_id: str,
    bucket: str,
    execution_result: dict,
    state_sidecar: dict | None = None,
    approval: dict | None = None,
    failed_sidecar: dict | None = None,
) -> None:
    stem = Path(job_id).stem
    (queue_root / bucket).mkdir(parents=True, exist_ok=True)
    (queue_root / bucket / job_id).write_text(json.dumps({"goal": "test"}), encoding="utf-8")

    art = queue_root / "artifacts" / stem
    art.mkdir(parents=True, exist_ok=True)
    (art / "execution_result.json").write_text(json.dumps(execution_result), encoding="utf-8")

    if state_sidecar:
        (queue_root / bucket / f"{stem}.state.json").write_text(
            json.dumps(state_sidecar), encoding="utf-8"
        )
    if approval:
        (queue_root / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
        (queue_root / "pending" / "approvals" / f"{stem}.approval.json").write_text(
            json.dumps(approval), encoding="utf-8"
        )
    if failed_sidecar:
        (queue_root / "failed").mkdir(parents=True, exist_ok=True)
        (queue_root / "failed" / f"{stem}.error.json").write_text(
            json.dumps(failed_sidecar), encoding="utf-8"
        )


def test_review_outcome_prefers_normalized_review_summary_and_evidence_contract(tmp_path: Path):
    queue = tmp_path / "queue"
    _write_job(
        queue,
        job_id="job-1.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "review_summary": {
                "latest_summary": "review summary wins",
                "execution_lane": "queue",
                "attempt_index": 1,
                "terminal_outcome": "succeeded",
            },
            "step_results": [
                {
                    "step_index": 1,
                    "status": "succeeded",
                    "summary": "step summary loses",
                }
            ],
            "artifact_families": ["execution_result", "review_summary"],
            "artifact_refs": [
                {
                    "artifact_family": "review_summary",
                    "artifact_path": "review_summary.json",
                    "exists": True,
                }
            ],
            "evidence_bundle": {
                "trace": {
                    "execution_lane": "queue",
                    "attempt_index": 1,
                    "terminal_outcome": "succeeded",
                }
            },
        },
    )

    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-1.json")

    assert evidence is not None
    assert evidence.latest_summary == "review summary wins"
    assert evidence.artifact_families == ("execution_result", "review_summary")
    assert evidence.artifact_refs == ("review_summary:review_summary.json",)
    assert "terminal_outcome=succeeded" in evidence.evidence_trace

    message = review_message(evidence)
    assert "review summary wins" in message
    assert "Artifact families" in message
    assert "Evidence trace" in message


def test_review_outcome_classifies_lifecycle_states_for_next_step(tmp_path: Path):
    queue = tmp_path / "queue"
    _write_job(
        queue,
        job_id="job-plan.json",
        bucket="pending",
        execution_result={
            "lifecycle_state": "planning",
            "terminal_outcome": "",
            "step_results": [],
        },
        state_sidecar={"lifecycle_state": "planning"},
    )

    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-plan.json")

    assert evidence is not None
    assert evidence.state == "planning"
    assert "planning now" in review_message(evidence)


def test_review_outcome_awaiting_approval_reports_blocked_next_step(tmp_path: Path):
    queue = tmp_path / "queue"
    _write_job(
        queue,
        job_id="job-approval.json",
        bucket="pending",
        execution_result={
            "lifecycle_state": "awaiting_approval",
            "approval_status": "pending",
            "review_summary": {"latest_summary": "Waiting for operator approval."},
        },
        approval={"reason": "manual gate"},
    )

    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-approval.json")

    assert evidence is not None
    assert evidence.state == "awaiting_approval"
    message = review_message(evidence)
    assert "blocked on operator approval" in message


def test_review_outcome_distinguishes_queued_from_submitted(tmp_path: Path):
    queue = tmp_path / "queue"
    _write_job(
        queue,
        job_id="job-queued.json",
        bucket="pending",
        execution_result={
            "lifecycle_state": "queued",
            "terminal_outcome": "",
        },
        state_sidecar={"lifecycle_state": "queued"},
    )

    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-queued.json")

    assert evidence is not None
    assert evidence.state == "queued"
    assert "accepted and queued" in review_message(evidence)


def test_review_outcome_prefers_normalized_failure_summary(tmp_path: Path):
    queue = tmp_path / "queue"
    _write_job(
        queue,
        job_id="job-fail.json",
        bucket="failed",
        execution_result={
            "lifecycle_state": "failed",
            "terminal_outcome": "failed",
            "error": "generic runtime failure",
            "review_summary": {
                "latest_summary": "Execution failed",
                "failure_summary": "Permission denied for target path",
            },
            "evidence_bundle": {
                "review_summary": {
                    "failure_summary": "fallback failure summary",
                },
                "trace": {
                    "lifecycle_state": "failed",
                    "approval_status": "none",
                },
            },
        },
        state_sidecar={"failure_summary": "state sidecar failure"},
        failed_sidecar={"error": "legacy sidecar failure"},
    )

    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-fail.json")

    assert evidence is not None
    assert evidence.failure_summary == "Permission denied for target path"
    message = review_message(evidence)
    assert "Execution failed; use the grounded failure summary" in message
    assert "lifecycle_state=failed" in message
    assert "approval_status=none" in message
