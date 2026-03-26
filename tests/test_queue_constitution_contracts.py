from __future__ import annotations

from voxera.core.queue_contracts import (
    build_execution_result,
    build_structured_step_results,
    detect_request_kind,
    minimum_artifact_presence,
    refresh_execution_result_artifact_contract,
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


def test_refresh_execution_result_artifact_contract_uses_final_directory_listing(tmp_path) -> None:
    artifacts = tmp_path / "artifacts" / "job-final"
    artifacts.mkdir(parents=True)
    for name in (
        "execution_envelope.json",
        "execution_result.json",
        "step_results.json",
        "job_intent.json",
        "plan.json",
        "actions.jsonl",
    ):
        (artifacts / name).write_text("{}", encoding="utf-8")

    seed = {
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
            "minimum_artifacts": {"status": "missing", "missing": ["execution_result.json"]},
        },
        "evidence_bundle": {"expected_artifacts": {"status": "missing"}},
    }

    refreshed = refresh_execution_result_artifact_contract(
        execution_result=seed,
        artifacts_dir=artifacts,
    )
    minimum = refreshed["review_summary"]["minimum_artifacts"]

    assert minimum["status"] == "observed"
    assert minimum["missing"] == []
    assert set(minimum["observed"]) >= {
        "execution_envelope.json",
        "execution_result.json",
        "step_results.json",
        "job_intent.json",
        "plan.json",
        "actions.jsonl",
    }
    assert refreshed["review_summary"]["expected_artifact_status"] == "observed", (
        "expected artifacts should reflect authoritative on-disk refs"
    )
    assert refreshed["evidence_bundle"]["minimum_artifacts"]["status"] == "observed", (
        "evidence bundle should stay in sync with refreshed contract"
    )
    assert (
        refreshed["evidence_bundle"]["review_summary"]["minimum_artifacts"]["status"] == "observed"
    ), "nested evidence review summary must match top-level review summary"


def test_build_structured_step_results_marks_path_blocked_scope_as_blocked() -> None:
    steps = build_structured_step_results(
        {
            "results": [
                {
                    "step": 1,
                    "skill": "files.list_dir",
                    "ok": False,
                    "error": "Path is outside allowed scope",
                    "error_class": "path_blocked_scope",
                    "blocked": False,
                }
            ],
            "step_outcomes": [{"step": 1, "outcome": "failed"}],
        },
        total_steps=1,
    )
    assert steps[0]["blocked"] is True
    assert steps[0]["blocked_reason_class"] == "path_blocked_scope"


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
