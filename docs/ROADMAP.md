# Roadmap

## Current product baseline (Alpha v0.1.4 stability + UX baseline)
- Setup wizard (TUI)
- Provider abstraction (cloud/local)
- Skill runner + policy gate
- Minimal panel (approvals + audit)
- Audit logs (JSONL)
- Cloud-assisted mission planning (`voxera missions plan "<goal>"`)
- Queue reliability hardening:
  - schema-versioned failed sidecars (`failed/*.error.json`)
  - centralized schema-version policy (writer pin + reader allowlist) for sidecar validation
  - deterministic failed retention pruning (paired/orphan-aware, max-age/max-count)
  - failed status snapshots prefer valid sidecars while counting only primary jobs
  - queue failure lifecycle smoke coverage (fail -> snapshot -> prune)

For the locked v0.1.4 release scope, acceptance criteria, and release checklist, see `docs/ROADMAP_0.1.4.md`.


## Next 4/8/12 weeks (user-visible milestones)

### Next 4 weeks — safer and clearer queue operations
1. Queue observability surfacing in CLI/panel summaries.
2. Reliability-focused operator guidance for failed artifacts and approval handling.

Success metrics:
- `voxera queue status` exposes failed-sidecar invalid counts and retention context in one pass.
- Operators can diagnose failed artifact state from docs + CI artifacts without ad-hoc local scripts.

### Next 8 weeks — predictable mission planning UX
1. Structured mission planning + dry-run simulation mode for safer previews.
2. Mission review output that clearly separates planned actions from approval-gated actions.

Success metrics:
- Dry-run output deterministically lists every skill/action before execution.
- Reduced approval-loop retries for planner-generated jobs (tracked via audit trend sampling).

### Next 12 weeks — stronger provider/runtime experience
1. OpenAI-compatible provider hardening (Ollama and compatible endpoints).
2. Broader v0.2 mission catalog expansion for daily workflows.

Success metrics:
- Stable provider fallback behavior across configured brain tiers.
- At least 10 production-usable missions documented and validated in release flow.


## Delivery enablers (non-user-visible, still important)

### Next 4 weeks
1. Add lightweight CI timing snapshots for merge-readiness phases to spot slowdowns quickly.
2. Keep type-ratchet baseline drift under control by reducing at least a small, fixed count of baseline entries in touched modules.

Reachable targets:
- Merge-readiness median runtime trend is tracked in CI summaries.
- At least 5 baseline entries removed through normal feature/refactor work (not bulk reset).

### Next 8 weeks
1. Expand regression coverage for queue lifecycle and planner approval edge cases.
2. Tighten local-dev parity so `make dev` setup remains one-command reliable for a solo maintainer workflow.

Reachable targets:
- Add or harden at least 6 focused tests across queue/planner reliability paths.
- Fresh-machine bootstrap + merge-readiness flow validated with no manual workaround steps.

### Next 12 weeks
1. Improve release packaging confidence with a repeatable smoke path for install/update/service lifecycle.
2. Keep docs and memory audit trails continuously current as part of merge hygiene.

Reachable targets:
- Release smoke checklist executes end-to-end on each release candidate.
- No merged feature PR lands without matching roadmap/memory/docs updates.

## Guardrails (ongoing, non-roadmap-critical)
- Unified merge-readiness guardrail:
  - single `make merge-readiness-check` target runs quality (format/lint/type) and release consistency checks.
  - PR gate consolidated under one required status check (`merge-readiness / merge-readiness`).
- Mypy ratchet governance (`scripts/mypy_ratchet.py`, `tools/mypy-baseline.txt`) with protected review paths.
- Validation tier clarity: PR-required checks (`make merge-readiness-check`) vs broader local validation (`make full-validation-check`).

## Recently completed
- Added release consistency checks for package/runtime/CLI/docs version alignment.
- Added CI enforcement for release-sensitive file changes.
- Hardened queue failed-artifact sidecar validation + retention behavior with lifecycle smoke coverage.
- Consolidated quality + release checks into one merge-readiness gate for branch protection.
- Strengthened merge-readiness with a mypy ratchet baseline (`scripts/mypy_ratchet.py` + `tools/mypy-baseline.txt`).
- Split validation tiers into PR-required merge checks (`make merge-readiness-check`) vs broader local validation (`make full-validation-check`).
- Added local pre-push parity via `.pre-commit-config.yaml` so pre-push runs the same merge-readiness gate as CI.
- Improved CI diagnosability by capturing quality/release logs, publishing step summaries, and uploading `merge-readiness-logs` artifacts.

## Alpha v0.2
- OpenAI-compatible provider solidified (Ollama, etc.)
- First 10 missions (work mode, status, volume, app launch, updates in ask-mode)
- Structured planning + dry-run simulation mode

## Alpha v0.3
- Voice stack: wake word + STT + TTS
- Voice-first command loop

## Alpha v0.4
- Sandbox runner (Podman)
- Signed skills + marketplace folder

## Beta v1.0
- ISO / image packaging
- Immutable base option + atomic updates
