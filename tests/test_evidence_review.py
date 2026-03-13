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


def test_review_outcome_surfaces_execution_capabilities_and_missing_expected_artifacts(
    tmp_path: Path,
):
    queue = tmp_path / "queue"
    _write_job(
        queue,
        job_id="job-missing-artifacts.json",
        bucket="failed",
        execution_result={
            "lifecycle_state": "failed",
            "terminal_outcome": "failed",
            "review_summary": {
                "latest_summary": "Execution failed",
                "execution_capabilities": {
                    "side_effect_class": "class_b",
                    "network_scope": "none",
                    "fs_scope": "confined",
                    "sandbox_profile": "host_local",
                },
                "capability_boundary_violation": {
                    "boundary": "network",
                    "declared_network_scope": "none",
                    "requested_network": True,
                },
                "expected_artifacts": ["execution_result", "stdout"],
                "expected_artifact_status": "partial",
                "observed_expected_artifacts": ["execution_result"],
                "missing_expected_artifacts": ["stdout"],
            },
        },
        state_sidecar={"failure_summary": "runtime failed before writing stdout"},
    )

    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-missing-artifacts.json")

    assert evidence is not None
    assert evidence.expected_artifact_status == "partial"
    assert evidence.missing_expected_artifacts == ("stdout",)

    message = review_message(evidence)
    assert "Execution capabilities:" in message
    assert "Capability boundary violation: boundary=network" in message
    assert "Expected artifacts were partially observed" in message
    assert "Missing expected artifacts: stdout" in message
    assert "Execution failed with partial expected outputs" in message


def test_review_outcome_succeeded_with_partial_expected_artifacts_is_state_aware(tmp_path: Path):
    queue = tmp_path / "queue"
    _write_job(
        queue,
        job_id="job-succeeded-partial.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "review_summary": {
                "latest_summary": "Execution succeeded",
                "expected_artifacts": ["execution_result", "review_summary"],
                "expected_artifact_status": "partial",
                "observed_expected_artifacts": ["execution_result"],
                "missing_expected_artifacts": ["review_summary"],
            },
        },
    )

    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-succeeded-partial.json")

    assert evidence is not None
    message = review_message(evidence)
    assert "Expected artifacts were partially observed" in message
    assert "succeeded with partial expected outputs" in message


def test_review_outcome_canceled_missing_expected_artifacts_frames_absence_honestly(tmp_path: Path):
    queue = tmp_path / "queue"
    _write_job(
        queue,
        job_id="job-canceled-missing.json",
        bucket="canceled",
        execution_result={
            "lifecycle_state": "canceled",
            "terminal_outcome": "canceled",
            "review_summary": {
                "latest_summary": "Execution canceled",
                "capability_boundary_violation": {
                    "boundary": "network",
                    "declared_network_scope": "none",
                    "requested_network": True,
                },
                "expected_artifacts": ["execution_result", "stdout"],
                "expected_artifact_status": "missing",
                "observed_expected_artifacts": [],
                "missing_expected_artifacts": ["stdout"],
            },
        },
    )

    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-canceled-missing.json")

    assert evidence is not None
    message = review_message(evidence)
    assert "Expected artifacts were not observed" in message
    assert "missing expected outputs may be caused by cancellation" in message


def test_review_outcome_missing_expected_artifacts_while_awaiting_approval_are_not_misframed(
    tmp_path: Path,
):
    queue = tmp_path / "queue"
    _write_job(
        queue,
        job_id="job-approval-missing.json",
        bucket="pending",
        execution_result={
            "lifecycle_state": "awaiting_approval",
            "approval_status": "pending",
            "review_summary": {
                "latest_summary": "Waiting for operator approval",
                "capability_boundary_violation": {
                    "boundary": "network",
                    "declared_network_scope": "none",
                    "requested_network": True,
                },
                "expected_artifacts": ["execution_result", "stdout"],
                "expected_artifact_status": "missing",
                "observed_expected_artifacts": [],
                "missing_expected_artifacts": ["stdout"],
            },
        },
        approval={"reason": "manual gate"},
    )

    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-approval-missing.json")

    assert evidence is not None
    message = review_message(evidence)
    assert "Expected artifacts were not observed" in message
    assert (
        "missing runtime outputs are expected until approval allows execution to continue"
        in message
    )


def test_review_outcome_handles_jobs_without_declared_expected_artifacts(tmp_path: Path):
    queue = tmp_path / "queue"
    _write_job(
        queue,
        job_id="job-no-expected.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "review_summary": {
                "latest_summary": "Execution succeeded",
            },
        },
    )

    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-no-expected.json")

    assert evidence is not None
    message = review_message(evidence)
    assert "Expected artifacts: none declared for this job." in message
