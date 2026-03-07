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
