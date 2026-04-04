"""Vera handoff facade — DEPRECATED.

This module previously re-exported helpers from preview_drafting,
preview_submission, and investigation_derivations.  All callers now import
from the true source modules directly.  This file is kept as an empty
placeholder so stale references produce a clear import error rather than
a missing-module crash.

Source modules:
  - vera.preview_drafting       (drafting_guidance, maybe_draft_job_payload, ...)
  - vera.preview_submission     (normalize_preview_payload, submit_preview, ...)
  - vera.investigation_derivations (derive_investigation_*, draft_investigation_*, ...)
"""
