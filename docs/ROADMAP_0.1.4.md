# Voxera OS Alpha v0.1.4 — Stability + UX Baseline Scope

This release locks v0.1.4 around one goal: make daily usage predictable before expanding major new features.

## In scope
1. Queue and daemon reliability hardening for normal and failure paths.
2. User-facing UX polish for queue visibility, approvals, and mission run outcomes.
3. Operational observability improvements for service, queue, and release checks.
4. Documentation and release messaging alignment for a single v0.1.4 narrative.

## Out of scope
- Full-duplex voice interaction loops.
- Wake-word runtime integration.
- New high-risk capability surfaces that bypass existing policy/approval paths.

## Acceptance criteria by command surface

### `voxera daemon`
- One-shot (`--once`) processing is deterministic for inbox/mission jobs.
- Failed jobs emit valid sidecar artifacts and status remains readable.
- Approval pauses and resumes are reflected in audit + queue state without manual repair.

### `voxera queue`
- `voxera queue status` exposes stable counts for pending/done/failed and failed metadata health.
- `voxera queue approvals list` gives clear, operator-usable pending approval details.
- Queue init path remains idempotent and safe on existing directories.

### `voxera missions`
- `voxera missions plan ... --dry-run` cleanly separates planned actions from execution.
- Mission execution outcomes are recorded consistently in mission log + queue artifacts.

### `voxera doctor`
- Provider/model checks report model-level health and clear error/latency notes.
- Doctor output remains actionable for local troubleshooting before daemon runs.

## Release checklist
- Smoke CLI path:
  - `voxera --version`
  - `voxera doctor`
  - `voxera queue init`
  - `voxera queue status`
  - `voxera missions plan "prep a focused work session" --dry-run`
  - `voxera daemon --once`
- Service lifecycle checks:
  - `make services-install`
  - `make services-status`
- Quality gate:
  - `make merge-readiness-check`

## Release positioning
Alpha v0.1.4 is the "trustworthy daily driver" baseline for Voxera OS:
policy-aware automation, stable queue execution, and cleaner operator UX as the foundation for upcoming voice-first work.
