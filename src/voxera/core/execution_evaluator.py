from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ..models import RunResult

EvaluationClass = Literal[
    "succeeded",
    "awaiting_approval",
    "blocked_non_retryable",
    "invalid_input_non_retryable",
    "retryable_failure",
    "replannable_mismatch",
    "terminal_failure",
]

_ALLOWED_REPLAN_CLASSES: set[EvaluationClass] = {
    "retryable_failure",
    "replannable_mismatch",
}

_REPLANNABLE_ERROR_CLASSES = {
    "planner_skill_mismatch",
    "skill_not_found",
    "arg_shape_mismatch",
}

_INVALID_INPUT_ERROR_CLASSES = {
    "invalid_input",
    "validation_error",
    "argument_validation_error",
}


@dataclass(frozen=True)
class EvaluationResult:
    evaluation_class: EvaluationClass
    reason: str
    replan_allowed: bool


def evaluate_run_result(*, run_result: RunResult, request_kind: str) -> EvaluationResult:
    if run_result.ok:
        return EvaluationResult(
            evaluation_class="succeeded",
            reason="mission_completed",
            replan_allowed=False,
        )

    data = run_result.data if isinstance(run_result.data, dict) else {}
    if str(data.get("status") or "") == "pending_approval":
        return EvaluationResult(
            evaluation_class="awaiting_approval",
            reason="approval_required",
            replan_allowed=False,
        )

    terminal_outcome = str(data.get("terminal_outcome") or "")
    if terminal_outcome == "blocked":
        return EvaluationResult(
            evaluation_class="blocked_non_retryable",
            reason="policy_or_runtime_boundary_block",
            replan_allowed=False,
        )

    last_result = _last_step_result(data)
    if last_result.get("error_class") in _REPLANNABLE_ERROR_CLASSES:
        return EvaluationResult(
            evaluation_class="replannable_mismatch",
            reason=str(last_result.get("error_class")),
            replan_allowed=request_kind == "goal",
        )

    if isinstance(last_result.get("retryable"), bool) and last_result["retryable"] is True:
        return EvaluationResult(
            evaluation_class="retryable_failure",
            reason="step_marked_retryable",
            replan_allowed=request_kind == "goal",
        )

    if last_result.get("error_class") in _INVALID_INPUT_ERROR_CLASSES:
        return EvaluationResult(
            evaluation_class="invalid_input_non_retryable",
            reason=str(last_result.get("error_class")),
            replan_allowed=False,
        )

    return EvaluationResult(
        evaluation_class="terminal_failure",
        reason=terminal_outcome or "mission_failed",
        replan_allowed=False,
    )


def replan_allowed_for_class(evaluation_class: EvaluationClass) -> bool:
    return evaluation_class in _ALLOWED_REPLAN_CLASSES


def _last_step_result(rr_data: dict[str, Any]) -> dict[str, Any]:
    raw = rr_data.get("results")
    if not isinstance(raw, list):
        return {}
    for item in reversed(raw):
        if isinstance(item, dict):
            return item
    return {}
