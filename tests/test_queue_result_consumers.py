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
    assert payload["latest_summary"] == "Step blocked because operator denied approval"
