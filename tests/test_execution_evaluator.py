from __future__ import annotations

from voxera.core.execution_evaluator import evaluate_run_result
from voxera.models import RunResult


def test_evaluator_success_no_replan():
    rr = RunResult(ok=True, data={"terminal_outcome": "succeeded"})
    out = evaluate_run_result(run_result=rr, request_kind="goal")
    assert out.evaluation_class == "succeeded"
    assert out.replan_allowed is False


def test_evaluator_approval_pauses_no_replan():
    rr = RunResult(ok=False, error="pause", data={"status": "pending_approval"})
    out = evaluate_run_result(run_result=rr, request_kind="goal")
    assert out.evaluation_class == "awaiting_approval"
    assert out.replan_allowed is False


def test_evaluator_policy_block_no_replan():
    rr = RunResult(ok=False, error="blocked", data={"terminal_outcome": "blocked"})
    out = evaluate_run_result(run_result=rr, request_kind="goal")
    assert out.evaluation_class == "blocked_non_retryable"
    assert out.replan_allowed is False


def test_evaluator_retryable_goal_allows_replan():
    rr = RunResult(
        ok=False,
        error="retryable",
        data={"results": [{"retryable": True}], "terminal_outcome": "failed"},
    )
    out = evaluate_run_result(run_result=rr, request_kind="goal")
    assert out.evaluation_class == "retryable_failure"
    assert out.replan_allowed is True
