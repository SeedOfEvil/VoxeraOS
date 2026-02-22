# Roadmap

## Alpha v0.1.3 (current)
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
- Unified merge-readiness guardrail:
  - single `make merge-readiness-check` target runs both quality checks (format/lint/type) and release consistency checks
  - PR gate consolidated under one required status check (`merge-readiness / merge-readiness`)

## Recently completed
- Added release consistency checks for package/runtime/CLI/docs version alignment.
- Added CI enforcement for release-sensitive file changes.
- Hardened queue failed-artifact sidecar validation + retention behavior with lifecycle smoke coverage.
- Consolidated quality + release checks into one merge-readiness gate for branch protection.
- Strengthened merge-readiness with a mypy ratchet baseline (`scripts/mypy_ratchet.py` + `tools/mypy-baseline.txt`).
- Split validation tiers into PR-required merge checks (`make merge-readiness-check`) vs broader local validation (`make full-validation-check`).
- Added local pre-push parity via `.pre-commit-config.yaml` so pre-push runs the same merge-readiness gate as CI.
- Improved CI diagnosability by capturing quality/release logs and uploading `merge-readiness-logs` artifacts on failures.

## Next up (ordered)
1. Queue observability surfacing: make retention policy and invalid-sidecar counters more visible in CLI/panel summaries.
2. Structured mission planning + dry-run simulation mode for safer preview before execution.
3. OpenAI-compatible provider hardening and broader v0.2 mission catalog expansion.

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
