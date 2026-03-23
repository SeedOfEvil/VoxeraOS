from __future__ import annotations

from voxera.vera import handoff, investigation_derivations, preview_drafting, preview_submission


def test_handoff_keeps_stable_preview_drafting_exports():
    assert handoff.maybe_draft_job_payload is preview_drafting.maybe_draft_job_payload
    assert handoff.drafting_guidance is preview_drafting.drafting_guidance
    assert (
        handoff.is_recent_assistant_content_save_request
        is preview_drafting.is_recent_assistant_content_save_request
    )
    assert handoff.diagnostics_request_refusal is preview_drafting.diagnostics_request_refusal
    assert (
        handoff.diagnostics_service_or_logs_intent
        is preview_drafting.diagnostics_service_or_logs_intent
    )


def test_handoff_keeps_stable_submission_and_investigation_exports():
    assert handoff.normalize_preview_payload is preview_submission.normalize_preview_payload
    assert handoff.submit_preview is preview_submission.submit_preview
    assert handoff.is_explicit_handoff_request is preview_submission.is_explicit_handoff_request
    assert (
        handoff.is_active_preview_submit_request
        is preview_submission.is_active_preview_submit_request
    )
    assert (
        handoff.draft_investigation_derived_save_preview
        is investigation_derivations.draft_investigation_derived_save_preview
    )
    assert (
        handoff.select_investigation_results
        is investigation_derivations.select_investigation_results
    )
