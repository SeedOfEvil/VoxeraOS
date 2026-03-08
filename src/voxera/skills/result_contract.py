from __future__ import annotations

from typing import Any, TypedDict

SKILL_RESULT_KEY = "skill_result"


class CanonicalSkillResult(TypedDict, total=False):
    summary: str
    machine_payload: dict[str, Any]
    output_artifacts: list[str]
    operator_note: str | None
    next_action_hint: str | None
    retryable: bool | None
    blocked: bool | None
    approval_status: str | None
    error: str | None
    error_class: str | None


def build_skill_result(
    *,
    summary: str,
    machine_payload: dict[str, Any] | None = None,
    output_artifacts: list[str] | None = None,
    operator_note: str | None = None,
    next_action_hint: str | None = None,
    retryable: bool | None = None,
    blocked: bool | None = None,
    approval_status: str | None = None,
    error: str | None = None,
    error_class: str | None = None,
) -> CanonicalSkillResult:
    return {
        "summary": str(summary).strip(),
        "machine_payload": machine_payload if isinstance(machine_payload, dict) else {},
        "output_artifacts": output_artifacts if isinstance(output_artifacts, list) else [],
        "operator_note": operator_note,
        "next_action_hint": next_action_hint,
        "retryable": retryable if isinstance(retryable, bool) else None,
        "blocked": blocked if isinstance(blocked, bool) else None,
        "approval_status": str(approval_status).strip() if approval_status is not None else None,
        "error": str(error) if error is not None else None,
        "error_class": error_class,
    }


def extract_skill_result(data: dict[str, Any]) -> CanonicalSkillResult:
    raw = data.get(SKILL_RESULT_KEY)
    if not isinstance(raw, dict):
        return build_skill_result(summary="")
    return build_skill_result(
        summary=str(raw.get("summary") or "").strip(),
        machine_payload=raw.get("machine_payload")
        if isinstance(raw.get("machine_payload"), dict)
        else {},
        output_artifacts=raw.get("output_artifacts")
        if isinstance(raw.get("output_artifacts"), list)
        else [],
        operator_note=str(raw.get("operator_note"))
        if raw.get("operator_note") is not None
        else None,
        next_action_hint=(
            str(raw.get("next_action_hint")) if raw.get("next_action_hint") is not None else None
        ),
        retryable=raw.get("retryable") if isinstance(raw.get("retryable"), bool) else None,
        blocked=raw.get("blocked") if isinstance(raw.get("blocked"), bool) else None,
        approval_status=(
            str(raw.get("approval_status")) if raw.get("approval_status") is not None else None
        ),
        error=str(raw.get("error")) if raw.get("error") is not None else None,
        error_class=str(raw.get("error_class")) if raw.get("error_class") is not None else None,
    )
