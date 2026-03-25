from __future__ import annotations

from voxera.core.queue_contracts import (
    build_execution_result,
    detect_request_kind,
    minimum_artifact_presence,
)
from voxera.core.queue_result_consumers import resolve_structured_execution
from voxera.core.queue_state import update_job_state_snapshot


def test_detect_request_kind_normalizes_unknown_tokens_fail_closed() -> None:
    payload = {"job_intent": {"request_kind": "totally_new_kind"}, "goal": "hello"}
    assert detect_request_kind(payload) == "unknown"


def test_update_job_state_snapshot_normalizes_invalid_lifecycle_to_blocked() -> None:
    snapshot = update_job_state_snapshot(
        "job-1.json",
        lifecycle_state="wild_state",
        current={},
        now_ms=1234,
    )
    assert snapshot["lifecycle_state"] == "blocked"
    assert snapshot["completed_at_ms"] == 1234


def test_update_job_state_snapshot_derives_denied_terminal_outcome() -> None:
    snapshot = update_job_state_snapshot(
        "job-2.json",
        lifecycle_state="failed",
        current={},
        now_ms=4567,
        approval_status="denied",
    )
    assert snapshot["terminal_outcome"] == "denied"


def test_minimum_artifact_presence_reports_missing_contract_artifacts() -> None:
    presence = minimum_artifact_presence(
        [
            {"artifact_path": "execution_result.json"},
            {"artifact_path": "step_results.json"},
        ]
    )
    assert presence["status"] == "missing"
    assert "execution_envelope.json" in presence["missing"]


def test_build_execution_result_includes_minimum_artifacts_in_review_and_evidence(tmp_path) -> None:
    artifacts = tmp_path / "artifacts" / "job-a"
    artifacts.mkdir(parents=True)
    (artifacts / "execution_result.json").write_text("{}", encoding="utf-8")

    payload = build_execution_result(
        job_ref="pending/job-a.json",
        rr_data={"lifecycle_state": "done"},
        step_results=[],
        terminal_outcome="succeeded",
        ok=True,
        artifacts_dir=artifacts,
    )

    assert payload["review_summary"]["minimum_artifacts"]["status"] == "missing"
    assert payload["evidence_bundle"]["minimum_artifacts"]["status"] == "missing"


def test_resolve_structured_execution_normalizes_awaiting_approval_terminal_to_blocked(
    tmp_path,
) -> None:
    art = tmp_path / "artifacts" / "job-await"
    art.mkdir(parents=True)
    (art / "execution_result.json").write_text(
        '{"terminal_outcome":"awaiting_approval","lifecycle_state":"awaiting_approval","approval_status":"pending"}',
        encoding="utf-8",
    )

    payload = resolve_structured_execution(artifacts_dir=art)
    assert payload["terminal_outcome"] == "blocked"
    assert payload["normalized_outcome_class"] == "approval_blocked"
