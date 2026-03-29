from __future__ import annotations

from pathlib import Path
from typing import Any

from .job_presentation import (
    evidence_summary_rows,
    job_context_summary,
    job_recent_timeline,
    operator_outcome_summary,
    policy_rationale_rows,
    why_stopped_rows,
)


def build_job_detail_sections(
    *,
    primary: dict[str, Any],
    state_sidecar: dict[str, Any],
    approval: dict[str, Any],
    failed_sidecar: dict[str, Any],
    structured_execution: dict[str, Any],
    artifacts_dir: Path,
    approval_path: Path | None,
    failed_sidecar_path: Path | None,
    state_sidecar_paths: list[Path],
    bucket: str,
    actions: list[dict[str, Any]],
    audit_timeline: list[dict[str, Any]],
) -> dict[str, Any]:
    has_approval = bool(approval)
    context_summary = job_context_summary(
        primary,
        state_sidecar=state_sidecar,
        approval=approval,
        failed_sidecar=failed_sidecar,
        structured_execution=structured_execution,
    )
    return {
        "job_context": context_summary,
        "operator_summary": operator_outcome_summary(
            bucket=bucket,
            execution=structured_execution,
            state_sidecar=state_sidecar,
            job_context=context_summary,
            has_approval=has_approval,
        ),
        "policy_rationale": policy_rationale_rows(
            execution=structured_execution,
            state_sidecar=state_sidecar,
            approval=approval,
            has_approval=has_approval,
        ),
        "evidence_summary": evidence_summary_rows(
            artifacts_dir=artifacts_dir,
            approval_path=approval_path,
            failed_sidecar_path=failed_sidecar_path,
            state_sidecar_paths=state_sidecar_paths,
        ),
        "why_stopped": why_stopped_rows(
            execution=structured_execution,
            state_sidecar=state_sidecar,
            job_context=context_summary,
        ),
        "recent_timeline": job_recent_timeline(actions, audit_timeline),
    }
