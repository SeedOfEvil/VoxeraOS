# Agent Development Notes

Use this file as a quick operational memory index for agent-style development in this repository.

## Project overview

VoxeraOS is an open-source alpha (v0.1.8) queue-driven AI control plane for Linux. Vera is the conversational intelligence layer; VoxeraOS is the trust, policy, execution, and evidence layer.

**Provider support:** OpenRouter is the only officially tested and fully built provider path. Gemini 3 Flash is the current minimum supported requirement.

## Current E2E confidence baseline
- Single skills verified: `system.open_app`, `system.set_volume`, `system.status`.
- Policy behavior verified: `apps.open -> allow`, `system.settings -> ask`.
- Approval gating verified in flow where volume/settings changes required approval.
- Multi-step mission chaining verified via `work_mode` mission (3 steps).
- Audit logging verified for step-level events and mission completion (`mission_done`) with policy reason context.
- Guided demo flow verified: `voxera demo` (offline) and `voxera demo --online` (provider-gated; missing keys remain SKIPPED).
- Queue hygiene commands verified: `voxera queue prune --dry-run`, `voxera queue reconcile` (report-only + `--fix` + `--fix --yes` quarantine modes).
- Daemon reliability verified: single-writer lock, graceful SIGTERM shutdown with structured sidecar, deterministic startup recovery with orphan quarantine.
- Brain fallback reason classification verified: `TIMEOUT | AUTH | RATE_LIMIT | MALFORMED | NETWORK | UNKNOWN` surfaced in health/doctor.
- Simple-intent routing verified: `open_terminal`, `open_url`, `open_app`, `write_file`, `read_file`, `run_command`, `assistant_question`, `unknown_or_ambiguous` with mismatch fail-closed enforcement.
- Fast read-only lane verified: `execution_lane=fast_read_only` for eligible advisory-only requests; non-eligible requests fail closed to `execution_lane=queue`.
- Live job progress verified: `/jobs/{id}/progress` and `/assistant/progress/{id}` endpoints return canonical lifecycle/step/approval state; panel polls with progressive enhancement.
- Lineage metadata verified: `parent_job_id`, `root_job_id`, `orchestration_depth`, `sequence_index`, `lineage_role` surfaced in artifacts, progress, and panel when submitted.
- Child enqueue verified: single `enqueue_child` in job payload produces one `inbox/child-*.json` with server-computed lineage; evidence in `child_job_refs.json`, `actions.jsonl`, `execution_result.json`, and panel.
- Red-team regression suite verified: `make security-check` passes; 17 adversarial tests cover intent hijack, planner mismatch, traversal metadata, approval-state integrity, progress-evidence consistency.

## Current confidence snapshot
- Queue failure paths validated: pre-run parse/planner, runtime, approval deny, approval-resume runtime, graceful SIGTERM shutdown.
- Startup recovery validated: pending in-flight jobs → `failed/` with sidecar, orphan approvals → `recovery/startup-<ts>/` quarantine.
- Queue hygiene toolchain validated: artifacts prune, queue prune, reconcile (report + fix preview + fix apply), safe symlink-aware quarantine.
- Brain fallback reason classified and surfaced in `voxera queue health` (counters + last reason) and `voxera doctor --quick` (last fallback line).
- Simple-intent routing validated: mismatch fail-closed before any skill execution; `execution_result.json` carries `intent_route` evidence for all goal-kind jobs.
- Security regression suite: 17 red-team tests pass deterministically as merge gate.
- Lineage metadata: additive only, does not change execution behavior, approvals, or policy.
- Child enqueue: single-child, explicit, fail-closed validation, server-side lineage, full policy/approval/fail-closed semantics.
- Live progress: canonical artifact-only sourcing; no speculative states; progressive enhancement fallback.

## Key invariants to preserve

- Fail-closed: when uncertain, Voxera fails closed. No degraded-but-executing mode.
- Queue is the system boundary: all execution flows through the queue with lifecycle visibility.
- Additive artifact design: new fields are additive; existing jobs remain readable.
- Policy/approval gates are not bypassable by metadata, lineage, or child enqueue payloads.
- Merge gate: `make merge-readiness-check` includes `security-check` — all 17 red-team tests must pass.

## Release alignment
- Active release line: Alpha v0.1.8 (see `docs/CODEX_MEMORY.md`).
- Next milestone: v0.2 — first platform milestone (see `docs/ROADMAP.md`).
- Previous releases: `docs/ROADMAP_0.1.6.md` (v0.1.6 shipped scope), `docs/ROADMAP_0.1.5.md` (artifacts prune), `docs/ROADMAP_0.1.4.md` (stability + UX baseline).
