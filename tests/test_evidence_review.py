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
    job_payload: dict | None = None,
) -> None:
    stem = Path(job_id).stem
    (queue_root / bucket).mkdir(parents=True, exist_ok=True)
    payload = job_payload if job_payload is not None else {"goal": "test"}
    (queue_root / bucket / job_id).write_text(json.dumps(payload), encoding="utf-8")

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
    assert evidence.normalized_outcome_class == "approval_blocked"
    message = review_message(evidence)
    assert "Normalized outcome class: `approval_blocked`" in message
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
    assert "Normalized outcome class: `capability_boundary_mismatch`" in message


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


def test_review_outcome_surfaces_path_boundary_class_and_next_step(tmp_path: Path):
    queue = tmp_path / "queue"
    _write_job(
        queue,
        job_id="job-path-blocked.json",
        bucket="failed",
        execution_result={
            "lifecycle_state": "failed",
            "terminal_outcome": "failed",
            "review_summary": {
                "latest_summary": "Path blocked by control-plane scope",
            },
            "step_results": [
                {
                    "step_index": 1,
                    "status": "failed",
                    "error_class": "path_blocked_scope",
                    "error": "Path is inside queue scope",
                }
            ],
        },
    )

    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-path-blocked.json")

    assert evidence is not None
    assert evidence.normalized_outcome_class == "path_blocked_scope"
    message = review_message(evidence)
    assert "Normalized outcome class: `path_blocked_scope`" in message
    assert "blocked by path scope controls" in message


def test_review_outcome_surfaces_runtime_dependency_missing_guidance(tmp_path: Path):
    queue = tmp_path / "queue"
    _write_job(
        queue,
        job_id="job-dep-missing.json",
        bucket="failed",
        execution_result={
            "lifecycle_state": "failed",
            "terminal_outcome": "failed",
            "review_summary": {
                "latest_summary": "Runner failed",
            },
            "step_results": [
                {
                    "step_index": 1,
                    "status": "failed",
                    "error_class": "missing_executable",
                    "error": "command not found",
                }
            ],
        },
    )

    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-dep-missing.json")

    assert evidence is not None
    assert evidence.normalized_outcome_class == "runtime_dependency_missing"
    message = review_message(evidence)
    assert "Normalized outcome class: `runtime_dependency_missing`" in message
    assert "required runtime dependency/tool is missing" in message


# ---------------------------------------------------------------------------
# Value-forward result surfacing in review messages
# ---------------------------------------------------------------------------


def test_review_message_surfaces_file_read_result(tmp_path: Path):
    queue = tmp_path / "queue"
    _write_job(
        queue,
        job_id="job-read.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "step_results": [
                {
                    "step_index": 1,
                    "skill_id": "files.read_text",
                    "status": "succeeded",
                    "summary": "Read text from /notes/todo.txt",
                    "machine_payload": {
                        "path": "/notes/todo.txt",
                        "bytes": 42,
                    },
                }
            ],
        },
    )

    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-read.json")
    assert evidence is not None
    assert evidence.value_forward_text
    assert "todo.txt" in evidence.value_forward_text
    message = review_message(evidence)
    # Content-first: value_forward_text leads the message, not buried as "- Result:"
    assert message.startswith("File") or message.startswith("Read")
    assert "todo.txt" in message
    assert f"Evidence for `{evidence.job_id}`:" in message


def test_review_message_surfaces_file_exists_result(tmp_path: Path):
    queue = tmp_path / "queue"
    _write_job(
        queue,
        job_id="job-exists.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "step_results": [
                {
                    "step_index": 1,
                    "skill_id": "files.exists",
                    "status": "succeeded",
                    "summary": "Checked path existence",
                    "machine_payload": {
                        "path": "/notes/config.yaml",
                        "exists": False,
                        "kind": "file",
                    },
                }
            ],
        },
    )

    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-exists.json")
    assert evidence is not None
    assert "does not exist" in evidence.value_forward_text
    message = review_message(evidence)
    assert "does not exist" in message


def test_review_message_surfaces_service_status_result(tmp_path: Path):
    queue = tmp_path / "queue"
    _write_job(
        queue,
        job_id="job-svc.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "step_results": [
                {
                    "step_index": 1,
                    "skill_id": "system.service_status",
                    "status": "succeeded",
                    "summary": "Service voxera-vera.service: inactive/dead",
                    "machine_payload": {
                        "service": "voxera-vera.service",
                        "ActiveState": "inactive",
                        "SubState": "dead",
                    },
                }
            ],
        },
    )

    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-svc.json")
    assert evidence is not None
    assert "inactive/dead" in evidence.value_forward_text
    message = review_message(evidence)
    assert "inactive/dead" in message


def test_review_message_surfaces_recent_logs_result(tmp_path: Path):
    queue = tmp_path / "queue"
    _write_job(
        queue,
        job_id="job-logs.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "step_results": [
                {
                    "step_index": 1,
                    "skill_id": "system.recent_service_logs",
                    "status": "succeeded",
                    "summary": "Collected 3 recent logs",
                    "machine_payload": {
                        "service": "voxera-daemon.service",
                        "line_count": 3,
                        "since_minutes": 15,
                        "logs": [
                            "2025-01-15T10:00 Started.",
                            "2025-01-15T10:01 Running.",
                            "2025-01-15T10:02 Stopped.",
                        ],
                        "truncated": False,
                    },
                }
            ],
        },
    )

    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-logs.json")
    assert evidence is not None
    assert "3 line" in evidence.value_forward_text
    assert "Started." in evidence.value_forward_text
    message = review_message(evidence)
    # Content-first: log excerpt leads the message
    assert "3 line" in message or "Started." in message
    assert f"Evidence for `{evidence.job_id}`:" in message


def test_review_message_surfaces_diagnostics_snapshot(tmp_path: Path):
    queue = tmp_path / "queue"
    _write_job(
        queue,
        job_id="job-diag.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "step_results": [
                {
                    "step_index": 1,
                    "skill_id": "system.host_info",
                    "status": "succeeded",
                    "summary": "",
                    "machine_payload": {"hostname": "myhost", "uptime_seconds": 3600},
                },
                {
                    "step_index": 2,
                    "skill_id": "system.memory_usage",
                    "status": "succeeded",
                    "summary": "",
                    "machine_payload": {
                        "used_gib": 8.0,
                        "total_gib": 32.0,
                        "used_percent": 25.0,
                    },
                },
            ],
        },
        job_payload={"goal": "diagnostics", "mission_id": "system_diagnostics"},
    )

    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-diag.json")
    assert evidence is not None
    assert "Diagnostics snapshot:" in evidence.value_forward_text
    assert "host=myhost" in evidence.value_forward_text
    assert "memory=8.0/32.0GiB" in evidence.value_forward_text


def test_review_message_surfaces_write_path_for_file_write(tmp_path: Path):
    """files.write_text with path metadata surfaces the path in value_forward_text."""
    queue = tmp_path / "queue"
    _write_job(
        queue,
        job_id="job-write.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "step_results": [
                {
                    "step_index": 1,
                    "skill_id": "files.write_text",
                    "status": "succeeded",
                    "summary": "Wrote file",
                    "machine_payload": {"path": "/notes/x.txt"},
                }
            ],
        },
    )

    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-write.json")
    assert evidence is not None
    assert "x.txt" in evidence.value_forward_text
    message = review_message(evidence)
    # Content-first: file write result leads the message
    assert "x.txt" in message
    assert f"Evidence for `{evidence.job_id}`:" in message


def test_review_message_no_value_forward_for_thin_status(tmp_path: Path):
    """Unknown skills with no extractor still produce empty value_forward_text."""
    queue = tmp_path / "queue"
    _write_job(
        queue,
        job_id="job-unknown.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "step_results": [
                {
                    "step_index": 1,
                    "skill_id": "custom.unknown_skill",
                    "status": "succeeded",
                    "summary": "Did something",
                    "machine_payload": {"key": "value"},
                }
            ],
        },
    )

    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-unknown.json")
    assert evidence is not None
    assert evidence.value_forward_text == ""
    message = review_message(evidence)
    # Fallback path: no content-first header, uses original header
    assert message.startswith("I reviewed canonical VoxeraOS evidence")
    assert "Evidence for `" not in message


def test_existing_review_behavior_preserved(tmp_path: Path):
    """Ensure existing review fields are not broken by the addition of value_forward_text."""
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
            },
            "step_results": [
                {
                    "step_index": 1,
                    "status": "succeeded",
                    "summary": "step summary loses",
                }
            ],
        },
    )

    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-1.json")
    assert evidence is not None
    assert evidence.latest_summary == "review summary wins"
    message = review_message(evidence)
    assert "review summary wins" in message
    # value_forward_text should be empty since no read-style skill
    assert evidence.value_forward_text == ""


# ---------------------------------------------------------------------------
# Content-first output review presentation
# ---------------------------------------------------------------------------


def test_content_first_file_write_leads_with_written_content(tmp_path: Path):
    """Completed file-write job: review message leads with actual written content.

    Regression: 'What was the output?' must return the real content first,
    not a metadata summary with content buried as a bullet point.
    """
    queue = tmp_path / "queue"
    joke = "Why did the programmer quit his job? Because he didn't get arrays."
    _write_job(
        queue,
        job_id="job-content-first.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "step_results": [
                {
                    "step_index": 1,
                    "skill_id": "files.write_text",
                    "status": "succeeded",
                    "summary": "Wrote text to /notes/joke.txt",
                    "machine_payload": {
                        "path": "/notes/joke.txt",
                        "bytes": len(joke),
                        "content": joke,
                    },
                }
            ],
        },
    )

    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-content-first.json")
    assert evidence is not None
    message = review_message(evidence)

    # Content must appear BEFORE the evidence header
    content_pos = message.index(joke)
    evidence_pos = message.index("Evidence for `")
    assert content_pos < evidence_pos, (
        "Canonical output content must lead the message, not follow evidence metadata"
    )

    # Evidence metadata is still present as secondary information
    assert "State: `succeeded`" in message
    assert "Next step:" in message

    # No hallucinated or duplicate content
    assert message.count(joke) == 1


def test_content_first_does_not_hallucinate_alternate_content(tmp_path: Path):
    """Review must never substitute different content for the canonical written text."""
    queue = tmp_path / "queue"
    real_content = "The real canonical output."
    _write_job(
        queue,
        job_id="job-no-hallucinate.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "step_results": [
                {
                    "step_index": 1,
                    "skill_id": "files.write_text",
                    "status": "succeeded",
                    "summary": "Wrote text",
                    "machine_payload": {
                        "path": "/notes/real.txt",
                        "bytes": len(real_content),
                        "content": real_content,
                    },
                }
            ],
        },
    )

    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-no-hallucinate.json")
    assert evidence is not None
    message = review_message(evidence)

    assert real_content in message
    assert "A different thing entirely" not in message


def test_content_first_fallback_when_content_unavailable(tmp_path: Path):
    """When exact output content is not available, fallback to evidence summary."""
    queue = tmp_path / "queue"
    _write_job(
        queue,
        job_id="job-no-content.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "review_summary": {
                "latest_summary": "Task completed, output not captured.",
            },
            "step_results": [
                {
                    "step_index": 1,
                    "skill_id": "custom.opaque_skill",
                    "status": "succeeded",
                    "summary": "Did something opaque",
                    "machine_payload": {"key": "value"},
                }
            ],
        },
    )

    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-no-content.json")
    assert evidence is not None
    assert evidence.value_forward_text == ""
    message = review_message(evidence)

    # Fallback: uses original evidence-oriented header
    assert message.startswith("I reviewed canonical VoxeraOS evidence for")
    # All metadata is present
    assert "State: `succeeded`" in message
    assert "Task completed, output not captured." in message


def test_content_first_file_read_leads_with_file_content(tmp_path: Path):
    """File-read job surfaces actual file content first."""
    queue = tmp_path / "queue"
    file_content = "buy milk\nwalk dog\nfix bug"
    _write_job(
        queue,
        job_id="job-read-first.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "step_results": [
                {
                    "step_index": 1,
                    "skill_id": "files.read_text",
                    "status": "succeeded",
                    "summary": "Read text from /notes/todo.txt",
                    "machine_payload": {
                        "path": "/notes/todo.txt",
                        "bytes": len(file_content),
                        "line_count": 3,
                        "content": file_content,
                        "content_truncated": False,
                    },
                }
            ],
        },
    )

    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-read-first.json")
    assert evidence is not None
    message = review_message(evidence)

    # Content leads the message
    assert message.startswith("Contents of /notes/todo.txt")
    assert "buy milk" in message

    # Evidence metadata follows
    assert "Evidence for `job-read-first.json`:" in message


def test_content_first_preserves_next_step_and_evidence_trace(tmp_path: Path):
    """Content-first mode still includes next-step guidance and evidence trace."""
    queue = tmp_path / "queue"
    _write_job(
        queue,
        job_id="job-trace.json",
        bucket="done",
        execution_result={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "step_results": [
                {
                    "step_index": 1,
                    "skill_id": "files.exists",
                    "status": "succeeded",
                    "summary": "Checked path",
                    "machine_payload": {
                        "path": "/notes/config.yaml",
                        "exists": True,
                        "kind": "file",
                    },
                }
            ],
            "evidence_bundle": {
                "trace": {
                    "terminal_outcome": "succeeded",
                    "lifecycle_state": "done",
                }
            },
        },
    )

    evidence = review_job_outcome(queue_root=queue, requested_job_id="job-trace.json")
    assert evidence is not None
    message = review_message(evidence)

    # Content first
    assert message.startswith("/notes/config.yaml exists")

    # Evidence metadata preserved
    assert "Next step:" in message
    assert "Evidence trace:" in message


def test_content_first_deduplicates_latest_summary():
    """When latest_summary is already contained in value_forward_text,
    suppress the '- Latest summary:' bullet to avoid near-duplicate content.

    This tests review_message() directly since the deduplication is a
    presentation concern, not an extraction concern.
    """
    from voxera.vera.evidence_review import ReviewedJobEvidence

    rich_summary = "buy milk\nwalk dog\nfix bug"
    # Simulate the result_surfacing Strategy 2 path where value_forward_text
    # contains the latest_summary text as its content excerpt.
    evidence = ReviewedJobEvidence(
        job_id="job-dedup.json",
        state="succeeded",
        lifecycle_state="done",
        terminal_outcome="succeeded",
        approval_status="none",
        latest_summary=rich_summary,
        failure_summary="",
        artifact_families=(),
        artifact_refs=(),
        evidence_trace=(),
        child_summary=None,
        execution_capabilities=None,
        capability_boundary_violation=None,
        expected_artifacts=(),
        observed_expected_artifacts=(),
        missing_expected_artifacts=(),
        expected_artifact_status="",
        normalized_outcome_class="",
        value_forward_text=f"Contents of /notes/todo.txt:\n{rich_summary}",
    )
    message = review_message(evidence)

    # Content leads the message
    assert message.startswith("Contents of /notes/todo.txt")

    # latest_summary is redundant with value_forward_text — should be suppressed
    assert "- Latest summary:" not in message

    # Other metadata is still present
    assert "State: `succeeded`" in message
    assert "Next step:" in message


def test_content_first_keeps_latest_summary_when_different():
    """When latest_summary differs from value_forward_text, both are shown."""
    from voxera.vera.evidence_review import ReviewedJobEvidence

    evidence = ReviewedJobEvidence(
        job_id="job-both.json",
        state="succeeded",
        lifecycle_state="done",
        terminal_outcome="succeeded",
        approval_status="none",
        latest_summary="File operation completed successfully.",
        failure_summary="",
        artifact_families=(),
        artifact_refs=(),
        evidence_trace=(),
        child_summary=None,
        execution_capabilities=None,
        capability_boundary_violation=None,
        expected_artifacts=(),
        observed_expected_artifacts=(),
        missing_expected_artifacts=(),
        expected_artifact_status="",
        normalized_outcome_class="",
        value_forward_text="Wrote /notes/output.txt (10 bytes):\nsome text!",
    )
    message = review_message(evidence)

    # Both content and different summary are shown
    assert "some text!" in message
    assert "- Latest summary: File operation completed successfully." in message


def test_no_behavior_drift_in_linked_job_review_routing(tmp_path: Path):
    """Content-first change must not alter which messages match review dispatch."""
    from voxera.vera.evidence_review import is_review_request

    # These must still match
    assert is_review_request("what was the output")
    assert is_review_request("show me the output")
    assert is_review_request("what happened")
    assert is_review_request("did it work")

    # These must still NOT match
    assert not is_review_request("write me a poem")
    assert not is_review_request("hello")
