from __future__ import annotations

# Compatibility façade for Vera handoff-facing helpers. The main seams now live
# in preview_drafting.py, draft_revision.py, preview_submission.py,
# saveable_artifacts.py, and investigation_derivations.py. This module stays
# intentionally small so existing imports/call sites remain stable.
from . import investigation_derivations as _investigation_derivations
from . import preview_drafting as _preview_drafting
from . import preview_submission as _preview_submission

__all__ = [
    "drafting_guidance",
    "is_active_preview_submit_request",
    "is_explicit_handoff_request",
    "normalize_preview_payload",
    "submit_preview",
    "maybe_draft_job_payload",
]

DraftingGuidance = _preview_drafting.DraftingGuidance
diagnostics_request_refusal = _preview_drafting.diagnostics_request_refusal
diagnostics_service_or_logs_intent = _preview_drafting.diagnostics_service_or_logs_intent
drafting_guidance = _preview_drafting.drafting_guidance
is_recent_assistant_content_save_request = (
    _preview_drafting.is_recent_assistant_content_save_request
)
maybe_draft_job_payload = _preview_drafting.maybe_draft_job_payload

is_active_preview_submit_request = _preview_submission.is_active_preview_submit_request
is_explicit_handoff_request = _preview_submission.is_explicit_handoff_request
normalize_preview_payload = _preview_submission.normalize_preview_payload
submit_preview = _preview_submission.submit_preview

derive_investigation_comparison = _investigation_derivations.derive_investigation_comparison
derive_investigation_expansion = _investigation_derivations.derive_investigation_expansion
derive_investigation_summary = _investigation_derivations.derive_investigation_summary
draft_investigation_derived_save_preview = (
    _investigation_derivations.draft_investigation_derived_save_preview
)
draft_investigation_save_preview = _investigation_derivations.draft_investigation_save_preview
is_investigation_compare_request = _investigation_derivations.is_investigation_compare_request
is_investigation_derived_followup_save_request = (
    _investigation_derivations.is_investigation_derived_followup_save_request
)
is_investigation_derived_save_request = (
    _investigation_derivations.is_investigation_derived_save_request
)
is_investigation_expand_request = _investigation_derivations.is_investigation_expand_request
is_investigation_save_request = _investigation_derivations.is_investigation_save_request
is_investigation_summary_request = _investigation_derivations.is_investigation_summary_request
select_investigation_results = _investigation_derivations.select_investigation_results
