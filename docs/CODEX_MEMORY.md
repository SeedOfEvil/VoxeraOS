# Codex Memory Log

This file is the single, persistent project memory for Codex-assisted work.

## How to use this file
- Before starting any task, read this file first.
- After every merged PR, append a new entry using the template below.
- Do not rewrite previous entries except to fix factual mistakes.
- Keep entries concise and operational (what changed, why, risks, follow-ups).

## Entry template
```
## YYYY-MM-DD — PR #<number> — <short title>
- Summary:
  - <1-3 bullets of what shipped>
- Validation:
  - <tests/checks run>
- Follow-ups:
  - <open tasks or "none">
- Risks/notes:
  - <migration steps, rollback notes, caveats>
```

## 2026-02-12 — PR #N/A (pre-history) — Introduce persistent Codex memory log
- Summary:
  - Added this canonical memory file for Codex agents to keep merged work history.
  - Linked the file from `README.md` so contributors can find and maintain it.
- Validation:
  - `python -m pytest` (from `voxera-os-scaffold/voxera-os`) passed.
- Follow-ups:
  - Replace `#TBD` with the real PR number after merge.
- Risks/notes:
  - Process-only change; no runtime behavior changed.

## 2026-02-15 — PR #5 — Add cloud-assisted mission planning path
- Summary:
  - Added `voxera missions plan` to let the configured cloud brain draft a mission from a natural-language goal.
  - Added strict planner validation so only known skill IDs and JSON outputs are accepted before execution.
  - Updated mission docs and added root-level `AGENT.md`/`CODEX.md` memory pointers for operator continuity.
- Validation:
  - `pytest -q`
- Follow-ups:
  - Add provider fallback selection for planning (`primary` -> `fast`/`fallback`) when cloud requests fail.
  - Add tests for policy deny + approval rejection paths on cloud-planned missions.
- Risks/notes:
  - Cloud planner quality depends on model behavior; guardrails reject malformed output.

## 2026-02-16 — PR #23 — Rewrite unsafe non-explicit sandbox.exec planner steps
- Summary:
  - Added planner-side safety rewrite for non-explicit goals so `sandbox.exec` steps using host-GUI/sandbox-inappropriate tools (`xdotool`, `wmctrl`, `xprop`, `gdbus`, `curl`, `wget`) are converted into `clipboard.copy` manual confirmation prompts.
  - Kept explicit user shell-command intent intact so command-oriented goals still allow planner `sandbox.exec` output.
  - Updated docs to describe the new planner guardrail behavior and aligned note-path examples with `~/VoxeraOS/notes`.
- Validation:
  - `pytest -q tests/test_mission_planner.py tests/test_queue_daemon.py`
- Follow-ups:
  - Add telemetry/metrics on rewrite frequency to detect planner drift.
- Risks/notes:
  - Intent detection is heuristic and should be monitored for false positives/negatives.


## 2026-02-21 — PR #29 — Queue failed-artifact reliability pass
- Summary:
  - Added a stable failed-sidecar contract with schema versioning (`schema_version=1`) and required fields (`job`, `error`, `timestamp_ms`) plus optional `payload`.
  - Added strict sidecar validation on write/read paths and ensured all queue failure paths emit schema-compliant sidecars.
  - Added deterministic failed-artifact retention pruning that treats primary+sidecar as one logical unit, handles orphans predictably, and supports max-age/max-count while preserving newest failures.
- Validation:
  - `pytest -q tests/test_queue_daemon.py tests/test_cli_queue.py`
- Follow-ups:
  - Consider adding a first-class CLI command to inspect/prune failed retention state.
- Risks/notes:
  - Invalid legacy sidecars are intentionally ignored for status summaries and logged via `queue_failed_sidecar_invalid`.


## 2026-02-21 — PR #34 — Tighten sidecar schema policy + lifecycle smoke coverage
- Summary:
  - Centralized failed-sidecar schema version checks with explicit writer pin (`1`) and reader allowlist (`[1]`).
  - Added deterministic rejection handling for unknown/future sidecar versions while preserving `queue_failed_sidecar_invalid` audit signaling.
  - Added a queue failure lifecycle smoke test validating fail -> sidecar-preferred snapshot -> prune -> empty snapshot behavior.
- Validation:
  - `pytest -q tests/test_queue_daemon.py`
  - `pytest -q tests/test_cli_queue.py`
- Follow-ups:
  - If a future schema bump is needed, update writer pin + reader allowlist together and document migration path before rollout.
- Risks/notes:
  - Mixed-version sidecars now surface deterministically as invalid until compatibility is explicitly added.


## 2026-02-21 — PR #34 — Add failed-sidecar CI guardrail + mixed-version runbook
- Summary:
  - Added a dedicated `make test-failed-sidecar` target that runs the sidecar schema-policy future-version rejection test and lifecycle smoke coverage.
  - Added PR CI workflow `.github/workflows/queue-failed-sidecar.yml` to run the guardrail tests whenever queue-daemon sidecar logic or operator docs are changed.
  - Expanded `docs/ops.md` with a mixed-version incident runbook for `queue_failed_sidecar_invalid` and linked contributor guidance in `README.md`.
- Validation:
  - `make test-failed-sidecar`
- Follow-ups:
  - Mark `queue-failed-sidecar-guardrail` as a required branch protection check on the default branch.
- Risks/notes:
  - Docs include shell snippets for ops triage; keep queue root paths aligned with deployment conventions.


## 2026-02-22 — PR #40 — Strengthen merge-readiness with mypy ratchet, validation tiers, and CI artifacts
- Summary:
  - Added a mypy ratchet utility and committed baseline flow (`scripts/mypy_ratchet.py`, `tools/mypy-baseline.txt`) so new type regressions are blocked while preserving controlled debt burn-down.
  - Split validation tiers into merge-required checks (`make merge-readiness-check`) and broader local validation (`make full-validation-check`), then aligned local pre-push parity through `.pre-commit-config.yaml`.
  - Updated merge-readiness CI to include scripts/tools path triggers, capture quality/release logs, and upload `merge-readiness-logs` artifacts on failure.
- Validation:
  - `make merge-readiness-check`
  - `pytest -q tests/test_mypy_ratchet.py`
  - `make full-validation-check`
- Follow-ups:
  - Add policy controls for baseline-file review ownership and rationale requirements when refreshing `tools/mypy-baseline.txt`.
- Risks/notes:
  - Baseline updates should remain triaged/intentional; avoid using baseline rewrites as a shortcut for unresolved type regressions.

## 2026-02-22 — PR #41 — Strengthen merge-readiness governance, CI summaries, and docs alignment
- Summary:
  - Updated merge-readiness CI to capture quality/release logs under `artifacts/`, publish a concise `$GITHUB_STEP_SUMMARY`, and fail the job if either phase fails.
  - Added baseline governance guidance for `tools/mypy-baseline.txt` refresh/review expectations in both `README.md` and `docs/ops.md`.
  - Added review protection in `.github/CODEOWNERS` for `tools/mypy-baseline.txt` and `scripts/mypy_ratchet.py`, and backfilled roadmap/memory references to reflect completed ratchet + validation-tier + CI-artifact work.
- Validation:
  - `make merge-readiness-check` (initial failure: missing `types-PyYAML` stubs)
  - `pip install types-PyYAML`
  - `make merge-readiness-check` (pass: quality/type and release checks)
- Follow-ups:
  - Keep 30/60/90 roadmap milestones focused on user-visible outcomes while maintaining guardrails as ongoing policy.
- Risks/notes:
  - Baseline refreshes remain review-sensitive; avoid using baseline rewrites to mask unresolved typing regressions.

## 2026-02-22 — PR #42 — Re-scope roadmap cadence to 4/8/12 weeks with delivery enablers
- Summary:
  - Replaced 30/60/90-day roadmap framing with 4/8/12-week milestones better matched to current solo-maintainer delivery pace.
  - Added non-user-visible delivery enablers (CI timing visibility, test reliability growth, release-smoke repeatability, docs/audit hygiene) with reachable targets.
  - Synced roadmap references in `README.md` and `docs/ops.md` to the new week-based cadence and enabler coverage.
- Validation:
  - `git diff -- README.md docs/ROADMAP.md docs/ops.md docs/CODEX_MEMORY.md`
- Follow-ups:
  - Keep enabler targets small and incremental each sprint so user-visible milestones remain primary.
- Risks/notes:
  - Enabler work should not displace product-visible outcomes; use it to reduce delivery friction and regressions.

## 2026-02-22 — PR #N/A — Rebrand to v0.1.4 and lock stability/UX baseline scope
- Summary:
  - Bumped project branding/version references from `0.1.3` to `0.1.4` across package metadata, README, roadmap/testing docs, mission docs, and legal notice.
  - Added `docs/ROADMAP_0.1.4.md` to lock the release scope around reliability, UX polish, observability, and release acceptance criteria.
  - Updated top-level release messaging to position v0.1.4 as a trustworthy daily-driver baseline ahead of broader voice-first expansion.
- Validation:
  - `make release-check`
- Follow-ups:
  - Replace `PR #N/A` with the merged PR number.
- Risks/notes:
  - Version sync is intentionally documentation-first; runtime version is sourced from package metadata and should be released/tagged with matching git state.


## Queue observability surfacing pass (CLI + panel + ops docs)
- Added queue status surfacing for failed-retention policy and latest prune-event summary.
- Exposed the same retention/prune snapshot in panel queue health view.
- Expanded operator and Ubuntu testing docs with direct triage steps for sidecar-invalid + approvals workflows.


## 2026-02-28 — PR #N/A — Full codebase analysis + documentation alignment pass
- Summary:
  - Conducted full codebase analysis (as of 2026-02-28): ~120 source files, ~17k lines Python,
    ~7k lines tests, ~170 git commits. Run `cloc --vcs git` for current counts.
  - Rewrote `docs/ARCHITECTURE.md` from stub (33 lines) to complete reference doc: 3-layer diagram, full
    module map with file-level descriptions, tech stack table, data flow, queue lifecycle diagram,
    config precedence, and validation tiers.
  - Rewrote `docs/ROADMAP.md`: replaced 4/8/12-week milestone blocks with daily/session-sized goals
    calibrated for solo development. Items grouped by area: operational hygiene, observability,
    safety hardening, daemon reliability, planner UX, prompt injection mitigation.
  - Updated `docs/ROADMAP_0.1.4.md`: marked as shipped, documented all completed items,
    added "known gaps carried forward" section to track technical debt items going into v0.2.
  - Expanded `docs/SECURITY.md`: added threat model table with current mitigation status,
    documented all current controls in detail, added "known gaps" section with planned fixes
    cross-referenced to ROADMAP.md daily goals, added prioritized hardening backlog (10 items),
    added operator quick-reference section.
- Validation:
  - Docs reviewed against live source code for accuracy.
  - No runtime behavior changed.
- Follow-ups:
  - Replace `PR #N/A` with merged PR number.
  - Begin Day 1 items from ROADMAP.md: artifact cleanup, `voxera artifacts prune`, `make type-debt`.
- Risks/notes:
  - Process and docs only; no code changes in this pass.

## 2026-03-01 — PR #74 — v0.1.5: artifacts prune + retention CLI
- Summary:
  - Bumped version from 0.1.4 to 0.1.5 in `pyproject.toml`, `README.md`, and docs.
  - Added `voxera artifacts prune` CLI command: dry-run by default, `--yes` to delete, union
    selection policy for `--max-age-days` and `--max-count` flags, `--json` for machine-readable output.
  - Added `artifacts_retention_days` and `artifacts_retention_max_count` to `VoxeraConfig` with
    corresponding env vars (`VOXERA_ARTIFACTS_RETENTION_DAYS`, `VOXERA_ARTIFACTS_RETENTION_MAX_COUNT`).
  - Created `src/voxera/core/artifacts.py` with `prune_artifacts()` pure logic function.
  - Added `docs/ROADMAP_0.1.5.md` (locked scope) and updated `docs/ROADMAP.md` to v0.1.5 baseline.
- Validation:
  - `ruff format src tests && ruff check src tests` — clean.
  - `mypy src/voxera tests` — no new errors beyond baseline.
  - `pytest -q` — all tests pass including 7 new artifact-prune tests.
- Follow-ups:
  - Tie artifact cleanup to failed-job retention pruner (when failed job is pruned, delete artifact dir).
  - Add `voxera queue prune` command for failed job files (Day 2 ROADMAP item).
  - Add `make type-debt` target (Day 1 ROADMAP item).
- Risks/notes:
  - Prune is always dry-run without `--yes`; safe by design.
  - Union policy documented in help text and README.

### PR #72 – Dry-run determinism: snapshot freeze + deterministic output mode (2026-02-28)
- Added `--freeze-capabilities-snapshot` and `--deterministic` flags to `voxera missions plan`.
- Added `_make_dryrun_deterministic()` helper in `src/voxera/core/missions.py` that zeroes
  `capabilities_snapshot.generated_ts_ms` in dry-run output (only when `--deterministic` is used).
- Default dry-run output is unchanged; both flags are opt-in.
- `--freeze-capabilities-snapshot` is a semantic commitment (snapshot already generated once per
  invocation); no runtime logic change needed.
- Verified:
  - `pytest tests/test_dryrun_determinism.py -q` — 4 new tests, all pass.
  - `ruff format src tests`, `ruff check src tests`, `mypy src` — clean.
  - `pytest -q` — all existing tests pass.
- Files changed: `src/voxera/core/missions.py`, `src/voxera/cli.py`,
  `tests/test_dryrun_determinism.py`, `README.md`, `docs/ops.md`, `docs/CODEX_MEMORY.md`.

## 2026-03-01 — PR #73 — Structured brain fallback reasons + health/doctor surfacing
- Summary:
  - Added stable `BrainFallbackReason` enum: `TIMEOUT | AUTH | RATE_LIMIT | MALFORMED | NETWORK | UNKNOWN`.
  - All exception paths in `openai_compat.py` and `gemini.py` classified into the enum before bubbling up.
  - Surfaced last fallback reason, source tier, and destination tier in `voxera queue health` and `health.json`.
  - Added per-reason health counters (`brain_fallback_reason_timeout`, `_auth`, `_rate_limit`, etc.).
  - `voxera doctor --quick` shows "Last fallback" line with most recent transition or "none".
- Validation:
  - `pytest -q tests/test_brain_fallback.py` — passes (new tests for each reason class).
  - `make merge-readiness-check` — clean.
- Follow-ups:
  - Surface fallback reason counters on panel home dashboard (tracked in Ops visibility milestone).
- Risks/notes:
  - Existing `UNKNOWN` fallback events remain in audit logs; no migration needed.
- Files changed: `src/voxera/brain/openai_compat.py`, `src/voxera/brain/gemini.py`,
  `src/voxera/health.py`, `src/voxera/cli.py`, `src/voxera/doctor.py`,
  `tests/test_brain_fallback.py`.

## 2026-03-01 — PR #75 — `voxera queue prune` command (terminal buckets only)
- Summary:
  - Added `voxera queue prune` CLI command that removes stale job files from terminal buckets
    (`done/`, `failed/`, `canceled/`). `inbox/` and `pending/` are never touched.
  - Dry-run by default; `--yes` to execute deletions.
  - Flags: `--max-age-days`, `--max-count`, `--json`, `--queue-dir`.
  - Matching sidecars (`.error.json`, `.state.json`) removed in the same pass as their primary job.
  - Env vars: `VOXERA_QUEUE_PRUNE_MAX_AGE_DAYS`, `VOXERA_QUEUE_PRUNE_MAX_COUNT`.
  - Runtime config keys: `queue_prune_max_age_days`, `queue_prune_max_count`.
  - Fixed: sidecars excluded from primary job enumeration to avoid double-counting.
  - Fixed: `safe_delete` tolerates already-deleted files gracefully.
- Validation:
  - `pytest -q tests/test_cli_queue.py` — passes (new prune lifecycle tests).
  - `make merge-readiness-check` — clean.
- Follow-ups:
  - Expose latest prune result in `voxera queue status` output.
  - Tie artifact dir cleanup to failed-job pruner pass.
- Risks/notes:
  - Union policy (age OR count) documented in help text and ops.md.
- Files changed: `src/voxera/core/queue_hygiene.py` (new), `src/voxera/cli.py`,
  `src/voxera/config.py`, `docs/ops.md`, `README.md`.

## 2026-03-01 — PR #76 — `voxera queue reconcile` report-only diagnostic
- Summary:
  - Added `voxera queue reconcile` as a read-only queue hygiene diagnostic.
  - Detects four issue categories: orphan sidecars, orphan approvals, orphan artifact candidates,
    duplicate job filenames across buckets.
  - Report-only by default — no filesystem changes in default mode.
  - `--json` flag emits stable JSON schema for automation.
  - Safe to run while daemon is running.
- Validation:
  - `pytest -q tests/test_cli_queue.py` — passes (new reconcile tests).
  - `make merge-readiness-check` — clean.
- Follow-ups:
  - Add fix/quarantine mode (tracked in PR #78).
- Risks/notes:
  - Missing queue directories are treated as 0 issues (no error raised).
- Files changed: `src/voxera/core/queue_reconcile.py` (new), `src/voxera/cli.py`, `docs/ops.md`.

## 2026-03-01 — PR #77 — Config path standardization (config.json)
- Summary:
  - Standardized all CLI help text, log messages, and documentation to consistently reference
    `~/.config/voxera/config.json` (not `config.yml` or ambiguous paths) for the runtime ops config.
  - Updated `docs/ops.md`, `README.md`, and affected CLI modules for consistency.
- Validation:
  - `make merge-readiness-check` — clean.
- Follow-ups:
  - None.
- Risks/notes:
  - Documentation-only change + CLI string cleanup; no runtime behavior changed.
- Files changed: `src/voxera/cli.py`, `README.md`, `docs/ops.md`.

## 2026-03-01 — PR #78 — Queue reconcile quarantine-first fix mode
- Summary:
  - Extended `voxera queue reconcile` with `--fix` flag enabling quarantine-first fix mode.
  - Without `--yes`: fix mode is a dry-run preview — prints what *would* be quarantined, exits 0.
  - With `--yes`: orphan sidecars in terminal buckets and orphan approvals are *moved* (not deleted)
    into `<queue-dir>/quarantine/reconcile-YYYYMMDD-HHMMSS/` preserving relative paths.
  - `--quarantine-dir` override supported (must remain within `--queue-dir`).
  - Stable JSON output schema extended with `mode`, `fix_counts`, and `quarantined_paths` fields.
  - Artifact candidates and duplicates remain report-only (too ambiguous for auto-fix).
- Validation:
  - `pytest -q tests/test_cli_queue.py` — passes.
  - `make merge-readiness-check` — clean.
- Follow-ups:
  - Symlink safety in quarantine paths (tracked in PR #79).
- Risks/notes:
  - No data is ever deleted; quarantined files can be restored manually.
- Files changed: `src/voxera/core/queue_reconcile.py`, `src/voxera/cli.py`, `docs/ops.md`.

## 2026-03-01 — PR #79 — Reconcile symlink orphan fix (safe relative path for quarantine)
- Summary:
  - Fixed reconcile fix mode to never follow symlinks when computing the safe relative path for
    quarantine destination. Prevents symlink traversal outside the queue root.
  - Resolves edge case where orphan sidecar is itself a symlink pointing outside `queue-dir`.
- Validation:
  - `pytest -q tests/test_cli_queue.py` — passes.
  - `make merge-readiness-check` — clean.
- Follow-ups:
  - None.
- Risks/notes:
  - Security-adjacent fix; no user-visible behavior change for normal (non-symlink) orphans.
- Files changed: `src/voxera/core/queue_reconcile.py`.

## 2026-03-01 — PR #80 — Daemon lock hardening + graceful SIGTERM shutdown
- Summary:
  - Hardened daemon lock: `flock`-based exclusive lock with PID validation, stale-window detection
    (configurable via `VOXERA_QUEUE_LOCK_STALE_S`), and structured audit event on contention.
  - Added explicit `SIGTERM`/`SIGINT` handler: sets shutdown flag immediately, stops intake of new
    inbox jobs, and handles any in-flight job deterministically as `failed/` with
    `error="shutdown: daemon shutdown requested"` plus a structured sidecar payload.
  - Health snapshot records `last_shutdown_ts`, `last_shutdown_reason`, and (if affected)
    `last_shutdown_job` + `last_shutdown_outcome=failed_shutdown`.
  - Concurrent daemon startup exits cleanly (non-zero) without disrupting the running daemon.
- Validation:
  - `pytest -q tests/test_queue_daemon.py` — passes (new lock + shutdown tests).
  - `make merge-readiness-check` — clean.
- Follow-ups:
  - Deterministic startup recovery for jobs that were in-flight at shutdown (PR #81).
- Risks/notes:
  - Fixes SECURITY.md known gap: "No SIGTERM handler — crash or stop leaves jobs in ambiguous state".
- Files changed: `src/voxera/core/queue_daemon.py`, `src/voxera/health.py`,
  `tests/test_queue_daemon.py`.

## 2026-03-01 — PR #81 — Deterministic daemon startup recovery
- Summary:
  - Added startup recovery pass that runs before any inbox intake on daemon start.
  - Policy: fail-fast. Any `pending/` job with in-flight state markers (`*.pending.json`,
    `*.state.json`) is moved to `failed/` with a structured sidecar:
    `reason="recovered_after_restart"`, includes `original_bucket`, `detected_state_files`,
    and best-effort `detected_artifacts_paths`.
  - Orphan approvals (`pending/approvals/*.approval.json` with no matching pending job) are
    quarantined under `recovery/startup-<ts>/pending/approvals/` (never deleted).
  - Orphan state files are quarantined under `recovery/startup-<ts>/...`.
  - Recovery emits audit event `daemon_startup_recovery` and increments health counters
    (`startup_recovery_runs`, `startup_recovery_jobs_failed`, `startup_recovery_orphans_quarantined`).
  - Health fields updated: `last_startup_recovery_ts`, `last_startup_recovery_counts`,
    `last_startup_recovery_summary`.
- Validation:
  - `pytest -q tests/test_queue_daemon.py` — passes (new recovery scenario tests).
  - `make merge-readiness-check` — clean.
- Follow-ups:
  - Surface `last_startup_recovery_counts` in panel dashboard (tracked in Ops visibility milestone).
- Risks/notes:
  - Recovery is deterministic and conservative: orphans are quarantined not deleted.
  - Double-execution risk for non-idempotent skills is eliminated for the shutdown-then-restart path.
- Files changed: `src/voxera/core/queue_daemon.py`, `src/voxera/health.py`,
  `src/voxera/audit.py`, `tests/test_queue_daemon.py`, `docs/ops.md`.

## 2026-03-01 — PR #82 — `voxera demo` guided checklist + modernized setup wizard
- Summary:
  - Added `voxera demo` CLI command: guided onboarding checklist that exercises queue + approval flows
    without destructive actions. Creates jobs with deterministic prefixes (`demo-basic-*`,
    `demo-approval-*`). Offline by default (provider readiness marked `SKIPPED`).
  - `voxera demo --online` opts into provider readiness checks; missing keys remain `SKIPPED`
    (not failure) so demo always completes.
  - Modernized setup wizard UX: auth prompt choices rendered with explicit labels
    (Keep current / Skip for now / Enter new / replace key) to avoid terminal rendering ambiguity.
  - Setup choices are intentionally non-destructive: existing credentials are never overwritten
    without an explicit "Enter new" selection.
  - Fixed: demo overall status aggregation for skipped online checks (skipped ≠ failed).
- Validation:
  - `pytest -q tests/test_demo_cli.py tests/test_setup_wizard.py` — passes (new demo + wizard tests).
  - `make merge-readiness-check` — clean.
- Follow-ups:
  - Replace PR #N/A with the merged PR number.
  - Add `voxera demo` to UBUNTU_TESTING.md validation checklist.
- Risks/notes:
  - Demo creates real queue jobs; operators should run `voxera queue prune` after extended demo sessions.
- Files changed: `src/voxera/demo.py` (new), `src/voxera/setup_wizard.py`, `src/voxera/cli.py`,
  `tests/test_demo_cli.py`, `tests/test_setup_wizard.py`, `README.md`, `docs/ops.md`.
