# Agent Memory Notes

Use this file as a quick operational memory index for agent-style development in this repository.

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

## Latest extension
- Added `voxera demo` guided checklist + modernized setup wizard UX with non-destructive credential handling (PR #82).
- Added deterministic daemon startup recovery: in-flight pending jobs marked failed/sidecar, orphan approvals/state files quarantined under `recovery/startup-<ts>/` (PR #81).
- Added daemon lock hardening + graceful SIGTERM shutdown with structured sidecar payload and health snapshot fields (PR #80).
- Added `voxera queue reconcile` quarantine-first fix mode + symlink-safe quarantine paths (PRs #78, #79).
- Added `voxera queue reconcile` report-only diagnostic for orphans, duplicates, mismatches (PR #76).
- Added `voxera queue prune` command for terminal buckets (done/failed/canceled), dry-run by default (PR #75).
- Added structured brain fallback reason enum surfaced in `voxera queue health` and `voxera doctor --quick` (PR #73).

## Current confidence snapshot
- Queue failure paths validated: pre-run parse/planner, runtime, approval deny, approval-resume runtime, graceful SIGTERM shutdown.
- Startup recovery validated: pending in-flight jobs → `failed/` with sidecar, orphan approvals → `recovery/startup-<ts>/` quarantine.
- Queue hygiene toolchain validated: artifacts prune, queue prune, reconcile (report + fix preview + fix apply), safe symlink-aware quarantine.
- Brain fallback reason classified and surfaced in `voxera queue health` (counters + last reason) and `voxera doctor --quick` (last fallback line).
- Demo system validated: offline mode completes without provider config, online mode non-blocking on missing keys.

## What to validate next
- Prompt-injection resistance: goal string sanitization (length cap + structural `[USER DATA: ...]` delimiters in preamble).
- Ops visibility in panel: reconcile/prune/recovery/fallback/lock/shutdown status surfaced on panel home dashboard.
- Long-run daemon health degradation: consecutive failures → degraded state, backoff on repeated brain failures.
- CI hardening: golden file validation stability, versioned release notes.

## Release alignment
- Active release line: Alpha v0.1.6 (daemon reliability + queue hygiene + demo/onboarding).
- Keep release-sensitive docs and package metadata aligned with `docs/ROADMAP_0.1.6.md`.
- Previous releases: `docs/ROADMAP_0.1.5.md` (artifacts prune), `docs/ROADMAP_0.1.4.md` (stability + UX baseline).
