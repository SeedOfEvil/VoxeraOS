# Agent Memory Notes

Use this file as a quick operational memory index for agent-style development in this repository.

## Current E2E confidence baseline
- Single skills verified: `system.open_app`, `system.set_volume`, `system.status`.
- Policy behavior verified: `apps.open -> allow`, `system.settings -> ask`.
- Approval gating verified in flow where volume/settings changes required approval.
- Multi-step mission chaining verified via `work_mode` mission (3 steps).
- Audit logging verified for step-level events and mission completion (`mission_done`) with policy reason context.

## Latest extension
- Added queue failed-artifact reliability pass with explicit schema-version compatibility policy (writer pin + reader allowlist), strict read/write validation, retention pruning (pair/orphan aware), and status preference for validated sidecar error details.

## Current confidence snapshot
- Queue failure paths validated: pre-run parse/planner, runtime, approval deny, approval-resume runtime all emit schema-compliant sidecars.
- Queue status keeps failed counts based on primary failed jobs only, while using valid sidecars for richer error summaries.
- Retention controls available via env (`VOXERA_QUEUE_FAILED_MAX_AGE_S`, `VOXERA_QUEUE_FAILED_MAX_COUNT`) preserve newest logical failure units first, with lifecycle smoke coverage validating fail -> snapshot -> prune behavior.

## What to validate next
- Add optional CLI override flags for failed retention policy (currently visibility is surfaced; overrides remain env or constructor-based).
- Brain fallback behavior when `primary` fails (latency/error path).
- Prompt-injection resistance in planner output validation.


## Release alignment
- Active release line: Alpha v0.1.4 (stability + UX baseline).
- Keep release-sensitive docs and package metadata aligned with `docs/ROADMAP_0.1.4.md`.
