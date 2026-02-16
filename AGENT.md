# Agent Memory Notes

Use this file as a quick operational memory index for agent-style development in this repository.

## Current E2E confidence baseline
- Single skills verified: `system.open_app`, `system.set_volume`, `system.status`.
- Policy behavior verified: `apps.open -> allow`, `system.settings -> ask`.
- Approval gating verified in flow where volume/settings changes required approval.
- Multi-step mission chaining verified via `work_mode` mission (3 steps).
- Audit logging verified for step-level events and mission completion (`mission_done`) with policy reason context.

## Latest extension
- Added cloud-assisted mission planning (`voxera missions plan "<goal>"`) that generates steps with the configured cloud brain and then executes via local policy/approval/audit pipeline.

## What to validate next
- Brain fallback behavior when `primary` fails (latency/error path).
- Prompt-injection resistance in planner output validation.
- Policy deny cases inside cloud-planned missions.
- Repeatability metrics (same goal, similar plan quality across providers/models).
