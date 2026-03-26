import json

from voxera.core.queue_result_consumers import resolve_structured_execution


def test_resolve_structured_execution_prefers_execution_result(tmp_path):
    art = tmp_path / "artifacts" / "job-a"
    art.mkdir(parents=True)
    (art / "execution_result.json").write_text(
        json.dumps(
            {
                "terminal_outcome": "failed",
                "lifecycle_state": "failed",
                "current_step_index": 2,
                "last_completed_step": 1,
                "last_attempted_step": 2,
                "total_steps": 3,
                "approval_status": "pending",
                "error": "canonical error",
                "step_results": [
                    {
                        "step_index": 2,
                        "skill_id": "assistant.advisory",
                        "status": "failed",
                        "summary": "step failed",
                        "retryable": True,
                        "blocked": True,
                        "operator_note": "fix input",
                        "next_action_hint": "retry",
                        "output_artifacts": ["outputs/report.json"],
                        "machine_payload": {"code": "E_FAIL"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = resolve_structured_execution(artifacts_dir=art)

    assert payload["terminal_outcome"] == "failed"
    assert payload["latest_summary"] == "step failed"
    assert payload["approval_status"] == "pending"
    assert payload["blocked"] is True
    assert payload["retryable"] is True
    assert payload["output_artifacts"] == ["outputs/report.json"]
    assert payload["machine_payload"] == {"code": "E_FAIL"}


def test_resolve_structured_execution_falls_back_to_legacy_inputs(tmp_path):
    art = tmp_path / "artifacts" / "job-legacy"
    art.mkdir(parents=True)

    payload = resolve_structured_execution(
        artifacts_dir=art,
        state_sidecar={
            "lifecycle_state": "done",
            "terminal_outcome": "succeeded",
            "current_step_index": 1,
            "total_steps": 1,
            "approval_status": "approved",
        },
        failed_sidecar={"error": "legacy error"},
    )

    assert payload["terminal_outcome"] == "succeeded"
    assert payload["lifecycle_state"] == "done"
    assert payload["current_step_index"] == 1
    assert payload["total_steps"] == 1
    assert payload["approval_status"] == "approved"
    assert payload["latest_summary"] == "legacy error"


def test_resolve_structured_execution_surfaces_lineage_from_execution_result(tmp_path):
    art = tmp_path / "artifacts" / "job-lineage"
    art.mkdir(parents=True)
    (art / "execution_result.json").write_text(
        json.dumps(
            {
                "lineage": {
                    "parent_job_id": "parent-1.json",
                    "root_job_id": "root-1.json",
                    "orchestration_depth": 2,
                    "sequence_index": 5,
                    "lineage_role": "child",
                }
            }
        ),
        encoding="utf-8",
    )

    payload = resolve_structured_execution(artifacts_dir=art)
    assert payload["lineage"]["parent_job_id"] == "parent-1.json"
    assert payload["lineage"]["orchestration_depth"] == 2


def test_resolve_structured_execution_sanitizes_malformed_lineage(tmp_path):
    art = tmp_path / "artifacts" / "job-lineage-bad"
    art.mkdir(parents=True)
    (art / "execution_result.json").write_text(
        json.dumps(
            {
                "lineage": {
                    "parent_job_id": " ",
                    "root_job_id": 100,
                    "orchestration_depth": "x",
                    "sequence_index": -1,
                    "lineage_role": "other",
                }
            }
        ),
        encoding="utf-8",
    )

    payload = resolve_structured_execution(artifacts_dir=art)
    assert payload["lineage"] == {
        "parent_job_id": None,
        "root_job_id": None,
        "orchestration_depth": 0,
        "sequence_index": None,
        "lineage_role": None,
    }


def test_resolve_structured_execution_child_summary_succeeded_child(tmp_path):
    queue_root = tmp_path
    parent_art = queue_root / "artifacts" / "parent"
    parent_art.mkdir(parents=True)
    (parent_art / "execution_result.json").write_text(
        json.dumps({"child_refs": [{"child_job_id": "child-ok.json"}]}),
        encoding="utf-8",
    )

    (queue_root / "done").mkdir(parents=True, exist_ok=True)
    (queue_root / "done" / "child-ok.json").write_text(json.dumps({"goal": "ok"}), encoding="utf-8")
    child_art = queue_root / "artifacts" / "child-ok"
    child_art.mkdir(parents=True)
    (child_art / "execution_result.json").write_text(
        json.dumps({"lifecycle_state": "done", "terminal_outcome": "succeeded"}), encoding="utf-8"
    )

    payload = resolve_structured_execution(artifacts_dir=parent_art)
    assert payload["child_summary"] == {
        "total": 1,
        "done": 1,
        "awaiting_approval": 0,
        "pending": 0,
        "failed": 0,
        "canceled": 0,
        "unknown": 0,
    }


def test_resolve_structured_execution_child_summary_awaiting_approval(tmp_path):
    queue_root = tmp_path
    parent_art = queue_root / "artifacts" / "parent"
    parent_art.mkdir(parents=True)
    (parent_art / "execution_result.json").write_text(
        json.dumps({"child_refs": [{"child_job_id": "child-approval.json"}]}),
        encoding="utf-8",
    )

    (queue_root / "pending").mkdir(parents=True, exist_ok=True)
    (queue_root / "pending" / "child-approval.json").write_text(
        json.dumps({"goal": "approval"}), encoding="utf-8"
    )
    (queue_root / "pending" / "approvals").mkdir(parents=True, exist_ok=True)
    (queue_root / "pending" / "approvals" / "child-approval.approval.json").write_text(
        json.dumps({"reason": "manual"}), encoding="utf-8"
    )

    payload = resolve_structured_execution(artifacts_dir=parent_art)
    assert payload["child_summary"]["awaiting_approval"] == 1
    assert payload["child_summary"]["total"] == 1


def test_resolve_structured_execution_child_summary_mixed_and_missing(tmp_path):
    queue_root = tmp_path
    parent_art = queue_root / "artifacts" / "parent"
    parent_art.mkdir(parents=True)
    (parent_art / "execution_result.json").write_text(
        json.dumps(
            {
                "child_refs": [
                    {"child_job_id": "child-pending.json"},
                    {"child_job_id": "child-failed.json"},
                    {"child_job_id": "child-canceled.json"},
                    {"child_job_id": "missing-child.json"},
                    {"child_job_id": "   "},
                ]
            }
        ),
        encoding="utf-8",
    )

    (queue_root / "pending").mkdir(parents=True, exist_ok=True)
    (queue_root / "failed").mkdir(parents=True, exist_ok=True)
    (queue_root / "canceled").mkdir(parents=True, exist_ok=True)

    (queue_root / "pending" / "child-pending.json").write_text(
        json.dumps({"goal": "p"}), encoding="utf-8"
    )
    (queue_root / "failed" / "child-failed.json").write_text(
        json.dumps({"goal": "f"}), encoding="utf-8"
    )
    (queue_root / "canceled" / "child-canceled.json").write_text(
        json.dumps({"goal": "c"}), encoding="utf-8"
    )

    payload = resolve_structured_execution(artifacts_dir=parent_art)
    assert payload["child_summary"] == {
        "total": 5,
        "done": 0,
        "awaiting_approval": 0,
        "pending": 1,
        "failed": 1,
        "canceled": 1,
        "unknown": 2,
    }


def test_resolve_structured_execution_denied_approval_is_not_pending(tmp_path):
    art = tmp_path / "artifacts" / "job-denied"
    art.mkdir(parents=True)
    (art / "execution_result.json").write_text(
        json.dumps(
            {
                "terminal_outcome": "failed",
                "lifecycle_state": "failed",
                "approval_status": "denied",
                "error": "Denied in approval inbox",
                "step_results": [
                    {
                        "step_index": 1,
                        "skill_id": "system.open_url",
                        "status": "failed",
                        "summary": "Step blocked because operator denied approval",
                        "approval_status": "denied",
                        "blocked": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = resolve_structured_execution(artifacts_dir=art)

    assert payload["terminal_outcome"] == "failed"
    assert payload["lifecycle_state"] == "failed"
    assert payload["approval_status"] == "denied"
    assert payload["normalized_outcome_class"] == "policy_denied"
    assert payload["latest_summary"] == "Step blocked because operator denied approval"


def test_resolve_structured_execution_classifies_capability_boundary_mismatch(tmp_path):
    art = tmp_path / "artifacts" / "job-cap-boundary"
    art.mkdir(parents=True)
    (art / "execution_result.json").write_text(
        json.dumps(
            {
                "terminal_outcome": "failed",
                "lifecycle_state": "failed",
                "step_results": [
                    {
                        "step_index": 1,
                        "status": "failed",
                        "error_class": "capability_boundary_mismatch",
                    }
                ],
                "review_summary": {
                    "capability_boundary_violation": {
                        "boundary": "network",
                        "declared_network_scope": "none",
                        "requested_network": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    payload = resolve_structured_execution(artifacts_dir=art)
    assert payload["normalized_outcome_class"] == "capability_boundary_mismatch"


def test_resolve_structured_execution_classifies_runtime_dependency_missing(tmp_path):
    art = tmp_path / "artifacts" / "job-missing-dep"
    art.mkdir(parents=True)
    (art / "execution_result.json").write_text(
        json.dumps(
            {
                "terminal_outcome": "failed",
                "lifecycle_state": "failed",
                "step_results": [
                    {
                        "step_index": 1,
                        "status": "failed",
                        "error_class": "missing_executable",
                        "error": "executable not found",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = resolve_structured_execution(artifacts_dir=art)
    assert payload["normalized_outcome_class"] == "runtime_dependency_missing"


def test_resolve_structured_execution_classifies_partial_artifact_gap(tmp_path):
    art = tmp_path / "artifacts" / "job-partial"
    art.mkdir(parents=True)
    (art / "execution_result.json").write_text(
        json.dumps(
            {
                "terminal_outcome": "succeeded",
                "lifecycle_state": "done",
                "review_summary": {
                    "expected_artifact_status": "partial",
                    "expected_artifacts": ["execution_result", "stdout"],
                    "observed_expected_artifacts": ["execution_result"],
                    "missing_expected_artifacts": ["stdout"],
                },
            }
        ),
        encoding="utf-8",
    )

    payload = resolve_structured_execution(artifacts_dir=art)
    assert payload["normalized_outcome_class"] == "partial_artifact_gap"


def test_resolve_structured_execution_surfaces_blocked_path_scope_metadata(tmp_path):
    art = tmp_path / "artifacts" / "job-path-blocked"
    art.mkdir(parents=True)
    (art / "execution_result.json").write_text(
        json.dumps(
            {
                "terminal_outcome": "failed",
                "lifecycle_state": "step_failed",
                "step_results": [
                    {
                        "step_index": 1,
                        "status": "failed",
                        "blocked": False,
                        "error_class": "path_blocked_scope",
                        "error": "Blocked: path outside allowed control-plane scope",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = resolve_structured_execution(
        artifacts_dir=art,
        state_sidecar={"blocked_reason_class": "path_blocked_scope"},
    )
    assert payload["blocked"] is True
    assert payload["blocked_reason_class"] == "path_blocked_scope"
    assert payload["step_summaries"][0]["blocked_reason_class"] is None


def test_resolve_structured_execution_exposes_normalized_artifact_contract_fields(tmp_path):
    art = tmp_path / "artifacts" / "job-artifacts"
    art.mkdir(parents=True)
    (art / "execution_result.json").write_text(
        json.dumps(
            {
                "artifact_families": ["execution_result", "review_summary"],
                "artifact_refs": [
                    {
                        "artifact_family": "review_summary",
                        "artifact_path": "review_summary.json",
                        "exists": True,
                    }
                ],
                "review_summary": {"latest_summary": "summary from review block"},
                "evidence_bundle": {
                    "trace": {"execution_lane": "queue", "terminal_outcome": "succeeded"}
                },
            }
        ),
        encoding="utf-8",
    )

    payload = resolve_structured_execution(artifacts_dir=art)

    assert payload["artifact_families"] == ["execution_result", "review_summary"]
    assert payload["artifact_refs"] == [
        {
            "artifact_family": "review_summary",
            "artifact_path": "review_summary.json",
            "exists": True,
        }
    ]


def test_resolve_structured_execution_exposes_expected_artifact_observation_fields(tmp_path):
    art = tmp_path / "artifacts" / "job-expected"
    art.mkdir(parents=True)
    (art / "execution_result.json").write_text(
        json.dumps(
            {
                "review_summary": {
                    "execution_capabilities": {
                        "side_effect_class": "class_b",
                        "network_scope": "read_only",
                        "fs_scope": "confined",
                        "sandbox_profile": "sandbox_no_network",
                        "expected_artifacts": ["execution_result", "review_summary"],
                    },
                    "expected_artifacts": ["execution_result", "review_summary"],
                    "expected_artifact_status": "partial",
                    "observed_expected_artifacts": ["execution_result"],
                    "missing_expected_artifacts": ["review_summary"],
                }
            }
        ),
        encoding="utf-8",
    )

    payload = resolve_structured_execution(artifacts_dir=art)

    assert payload["execution_capabilities"]["side_effect_class"] == "class_b"
    assert payload["expected_artifacts"] == ["execution_result", "review_summary"]
    assert payload["expected_artifact_status"] == "partial"
    assert payload["observed_expected_artifacts"] == ["execution_result"]
    assert payload["missing_expected_artifacts"] == ["review_summary"]
