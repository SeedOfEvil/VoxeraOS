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
