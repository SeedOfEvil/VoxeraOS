# Agent Memory Notes

Use this file as a quick operational memory index for agent-style development in this repository.

## Current E2E confidence baseline
- Single skills verified: `system.open_app`, `system.set_volume`, `system.status`.
- Policy behavior verified: `apps.open -> allow`, `system.settings -> ask`.
- Approval gating verified in flow where volume/settings changes required approval.
- Multi-step mission chaining verified via `work_mode` mission (3 steps).
- Audit logging verified for step-level events and mission completion (`mission_done`) with policy reason context.

## Latest extension
- Added queue failed-artifact reliability pass: schema-versioned `failed/*.error.json` sidecars, strict read/write validation, retention pruning (pair/orphan aware), and status preference for validated sidecar error details.

## Current confidence snapshot
- Queue failure paths validated: pre-run parse/planner, runtime, approval deny, approval-resume runtime all emit schema-compliant sidecars.
- Queue status keeps failed counts based on primary failed jobs only, while using valid sidecars for richer error summaries.
- Retention controls available via env (`VOXERA_QUEUE_FAILED_MAX_AGE_S`, `VOXERA_QUEUE_FAILED_MAX_COUNT`) and preserve newest logical failure units first.

## What to validate next
- Add CLI surface for failed-retention policy visibility/override (currently env or constructor-based).
- Track sidecar validation failures (`queue_failed_sidecar_invalid`) in panel/operator dashboards.
- Brain fallback behavior when `primary` fails (latency/error path).
- Prompt-injection resistance in planner output validation.
