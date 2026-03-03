# Voxera OS Alpha v0.1.6 — Security Hardening + Ops Visibility (SHIPPED)

**Status: shipped. See `docs/ROADMAP.md` for the v0.2 active build-out.**

For active daily-goal breakdown, see `docs/ROADMAP.md`.
For the previous shipped release, see `docs/ROADMAP_0.1.5.md`.

---

## Shipped scope for v0.1.6

v0.1.6 delivered two pillars: **security hardening** (close the prompt injection surface and
panel brute-force surface) and **ops visibility** (surface full daemon state in the panel).
Health degradation (P3.x) is deferred to v0.2.

### All shipped items (v0.1.6)

- ✅ Goal string sanitization + 2,000-char preflight cap — PR #85
- ✅ `[USER DATA START]`/`[USER DATA END]` structural prompt boundaries — PR #88
- ✅ Panel auth lockout: 10 failures/60s → HTTP 429 + `Retry-After: 60` + audit events — PR #89
- ✅ Panel Daemon Health widget (health.json-sourced, no daemon RPC calls) — PR #92
- ✅ Panel `/hygiene` page (prune dry-run + reconcile trigger, results in health.json) — PR #93
- ✅ `sandbox.exec` argv canonicalization (`canonicalize_argv`) — PR #91
- ✅ Deterministic terminal demo skill (`system.terminal_run_once`) + planner route — PR #84
- ✅ OpenRouter automatic attribution headers (`voxeraos.ca` + `VoxeraOS`) — PR #86
- ✅ e2e_golden4 approval hang fix (filesystem-based detection, phase timeouts, diagnostics) — PR #90

---

### Pillar 1 — Security hardening

#### P1.1 — Goal string sanitization + length cap (SHIPPED)
- Sanitize user-controlled goal strings before embedding in LLM prompt.
- Reject goals over 2,000 characters with a clear, actionable error message.
- Strip control characters and normalize whitespace to prevent prompt formatting tricks.
- Acceptance: `pytest tests/test_mission_planner.py` includes injection-shaped + overlength cases.

#### P1.2 — Structural `[USER DATA: ...]` delimiters in planner preamble (SHIPPED — PR #88)
- Wrapped user-provided content in `[USER DATA START]` / `[USER DATA END]` delimiters in the preamble.
- Structural separation prevents goal text from being interpreted as system instructions.
- Documented delimiter format in `docs/SECURITY.md`.
- Acceptance: planner preamble output includes delimiter markers; existing tests pass.

#### P1.3 — Panel auth rate limiting (SHIPPED)
- Track failed Basic auth attempts per IP in the health snapshot.
- After 10 failures within 60 seconds: return 429 + `Retry-After: 60` header.
- Lockout events emitted as structured audit entries (`panel_auth_lockout`, ip, attempt_count).
- Lockout status visible in `voxera queue health` and `voxera doctor --quick`.
- Acceptance: 10 rapid failed auth attempts trigger lockout; correct 429 response returned.

---

### Pillar 2 — Ops visibility in panel

#### P2.1 — Panel home health widget (SHIPPED)
- Added a collapsible "Daemon Health" widget to the panel home (`/`) showing:
  - Lock status (held/stale/clear) + lock PID + stale age
  - Last brain fallback (reason, tier, timestamp) or "no recent fallbacks"
  - Last startup recovery (job count, orphan count, timestamp) or "clean"
  - Last shutdown outcome (clean/failed_shutdown) + timestamp
  - Daemon state: `healthy` / `degraded` (if PR #89 is merged)
- Sourced from `health.json`; no daemon call needed — safe for panel-only deployments.
- Acceptance: widget renders on home with all fields; empty/null fields show a neutral state.

#### P2.2 — Panel hygiene status + trigger page (SHIPPED)
- Add `/hygiene` page to panel with:
  - Last `voxera queue prune` result (timestamp, pruned count per bucket, reclaimed bytes).
  - Last `voxera queue reconcile` result (timestamp, issue counts per category).
  - "Run prune (dry-run)" button: triggers CLI prune in report mode, returns JSON result.
  - "Run reconcile" button: triggers CLI reconcile, returns JSON result.
- Results stored in `health.json` under `last_prune_result` and `last_reconcile_result`.
- Acceptance: buttons invoke commands and surface results without page reload (HTMX or polling).

#### P2.3 — Recovery + quarantine inspector in panel (SHIPPED)
- Add `/recovery` page showing:
  - All files under `notes/queue/recovery/` with size, timestamp, and type (approval/state).
  - All files under `notes/queue/quarantine/` with size and timestamp.
  - "Download as ZIP" button for each recovery/quarantine session.
- Read-only view; no deletions from panel.
- Acceptance: page lists recovery and quarantine directories; download returns valid ZIP.

---

### Support/Infra shipped (reliability work)

#### PR #90 — e2e_golden4 approval hang fix (SHIPPED)
- Replaced CLI-table-parsing approval detection with direct filesystem artifact check (`pending/approvals/job-e2e-open.approval.json`).
- Introduced PHASE A (detect approval state, 120s timeout) and PHASE B (wait for lifecycle advance, 300s timeout).
- Added `dump_diag` helper: prints queue status, approvals list, and directory listings on any timeout or failure.
- Fixed settle loop to exit non-zero with clear summary when done-count not reached.
- Added `PANEL_PORT` detection via `VOXERA_PANEL_PORT` env var with fallback to default 8844.

#### PR #91 — sandbox.exec canonicalize_argv (SHIPPED)
- `canonicalize_argv(args)` as single source of truth in `src/voxera/skills/arg_normalizer.py`.
- Accepts keys in priority order: `command` (canonical), `argv`, `cmd` (compatibility aliases).
- String values tokenized with `shlex.split` (no implicit shell wrapper).
- Empty/whitespace-only tokens silently stripped; non-string tokens raise `ValueError` with actionable message.
- Two-layer defense: `PodmanSandboxRunner.run()` (execution path) + `canonicalize_args("sandbox.exec")` (pre-flight).

---

### Pillar 3 — Health degradation + long-run daemon behavior (DEFERRED to v0.2)

#### P3.1 — Health degradation state tracking (SHIPPED)
- Track consecutive brain fallback count in `health.json` under `consecutive_brain_failures` (always present, default `0`).
- When `consecutive_brain_failures >= 3`, set `daemon_state = "degraded"` in health snapshot and stamp `degraded_since_ts` / `degraded_reason`.
- When a mission completes successfully, reset counter and restore `daemon_state = "healthy"`.
- Surface `daemon_state` in `voxera queue health` and `voxera doctor --quick`.
- Acceptance: 3 consecutive fallback events set state to `degraded`; successful mission completion resets counter/state and clears degraded metadata.

#### P3.2 — Brain backoff on repeated failures (PLANNED)
- Add configurable delay between brain calls when consecutive fallbacks exceed a threshold.
- Default backoff: 2s after 3 failures, 8s after 5, 30s after 10. Cap at 60s.
- Configurable via `VOXERA_BRAIN_BACKOFF_BASE_S` and `VOXERA_BRAIN_BACKOFF_MAX_S`.
- Backoff events emitted to audit log (`brain_backoff_applied`, attempt, wait_s).
- Acceptance: consecutive-failure scenario shows increasing delays between plan attempts.

#### P3.3 — Structured shutdown outcome in `voxera queue health` (PLANNED)
- Surface `last_shutdown_outcome`, `last_shutdown_job`, `last_shutdown_reason`, and
  `last_shutdown_ts` in the `voxera queue health` human-readable output.
- Include these fields in the JSON output of `voxera queue health --json`.
- Acceptance: after a graceful SIGTERM, `voxera queue health` shows the shutdown context.

---

### Pillar 4 — CI hardening & release packaging (DEFERRED to v0.2)

#### P4.1 — Golden file validation CI job (PLANNED)
- Add `tests/golden/` directory with committed dry-run output golden files.
- Add `make golden-update` target that regenerates golden files (requires explicit invocation).
- Add `make golden-check` target (and CI step) that fails if dry-run output diverges from golden.
- Use `--deterministic` flag so golden files are timestamp-independent.
- Acceptance: modifying planner context breaks `make golden-check`; CI fails on drift.

#### P4.2 — Release packaging polish + versioned release notes (PLANNED)
- Add `scripts/release_notes.py` that generates a release notes Markdown snippet from
  `docs/CODEX_MEMORY.md` entries since the last version tag.
- Add `make release-notes` target that runs the script and writes `docs/RELEASE_NOTES_<version>.md`.
- Polish `make release-check` to validate all surfaces: `pyproject.toml`, README version header,
  and ROADMAP `current baseline` section are in sync.
- Acceptance: `make release-check` fails if any versioned surface disagrees.

---

### Pillar 5 — Provider / model UX (DEFERRED to v0.2)

#### P5.1 — Keyring credential workflow improvements in setup wizard (PLANNED)
- Show keyring availability status at setup start ("keyring: available" / "keyring: unavailable, using file fallback").
- After entering a new key: test the credential against the provider and show pass/fail before saving.
- Show current key status (stored in keyring / stored in file / not set) for each configured provider.
- Acceptance: setup detects keyring availability; credential test runs before save.

#### P5.2 — Provider profiles (named presets) (PLANNED)
- Add provider profile presets to setup wizard: `openrouter-4tier`, `ollama-local`, `gemini-only`.
- Each preset configures the full brain tier stack (primary / fast / reasoning / fallback) from a template.
- Profiles stored in `config-templates/profiles/` and loadable via `voxera setup --profile <name>`.
- Acceptance: `voxera setup --profile ollama-local` writes correct brain config without interactive prompts.

#### P5.3 — Config hygiene: auto-upgrade legacy placeholder defaults (PLANNED)
- Treat legacy placeholder headers as "unset":
  - `HTTP-Referer: https://localhost` (and common variants)
  - `X-Title: Voxera OS` (legacy default)
- For OpenRouter requests, auto-fill current defaults when legacy placeholders are detected:
  - `HTTP-Referer: https://voxeraos.ca`
  - `X-OpenRouter-Title + X-Title: VoxeraOS`
- Preserve real user overrides (only upgrade placeholder values).
- Surface a small note in `voxera doctor --quick` (or warning line) when legacy defaults are detected.
- Acceptance: config containing localhost referer still results in OpenRouter attribution showing VoxeraOS without manual edits.

---

### Pillar 6 — New utility commands (DEFERRED to v0.2)

#### P6.1 — `voxera skills validate` command (PLANNED)
- New command that eagerly validates all skill manifests (without launching the daemon).
- Checks: required fields present, entrypoint importable, capability declarations valid.
- Surface validation results in `voxera doctor` output ("Skills: N valid, M invalid").
- Emit audit event `skill_manifest_invalid` for each broken manifest.
- Acceptance: intentionally broken manifest shows in `voxera skills validate` output.

#### P6.2 — LLM rate limiter (token bucket around `brain.generate()`) (PLANNED)
- Add configurable token bucket rate limiter around `brain.generate()` calls.
- Default: 30 calls/minute. Configurable via `VOXERA_BRAIN_RATE_LIMIT_RPM`.
- When limit exceeded: emit `brain_rate_limited` audit event; delay or return structured error.
- Surface current RPM and limit in `voxera queue health`.
- Acceptance: rapid repeated plan calls trigger rate limiting at configured threshold.

---

## Acceptance criteria for v0.1.6 (all met)

### Security
- ✅ Goal strings over 2,000 characters are rejected with a clear error before reaching the LLM.
- ✅ Planner preamble includes `[USER DATA START]` / `[USER DATA END]` delimiters (PR #88).
- ✅ 10 rapid failed panel auth attempts trigger 429 lockout with `Retry-After: 60` header and per-IP lockout tracking in health snapshot (PR #89).
- ✅ Lockout events appear in audit log (`panel_auth_lockout` events).

### Ops visibility
- ✅ Panel home shows daemon health widget with lock/fallback/recovery/shutdown fields (PR #92).
- ✅ Panel hygiene page (`/hygiene`) surfaces last prune + reconcile results and trigger buttons with async in-page updates (PR #93).
- ✅ Panel recovery page (`/recovery`) lists `recovery/` and `quarantine/` sessions/loose files with metadata and per-item ZIP download (P2.3).

### Health degradation (DEFERRED to v0.2)
- ✅ 3 consecutive brain fallbacks set `daemon_state = "degraded"` in health snapshot (P3.1).
- ⏳ Backoff delays applied between brain calls after repeated fallbacks (P3.2).
- ⏳ `voxera queue health` shows last shutdown outcome with job and reason (P3.3).

### CI + packaging (DEFERRED to v0.2)
- ⏳ `make golden-check` passes; fails on planner context drift (P4.1).
- ⏳ `make release-check` validates all versioned surfaces (P4.2).

### Provider UX (DEFERRED to v0.2)
- ⏳ `voxera setup` shows keyring status and tests credentials before saving (P5.1).
- ⏳ `voxera setup --profile <name>` applies preset without interactive prompts (P5.2).
- ⏳ Legacy placeholder OpenRouter defaults auto-upgrade to current attribution defaults (P5.3).

### Utility commands (DEFERRED to v0.2)
- ⏳ `voxera skills validate` surfaces broken manifests; `voxera doctor` includes skill health (P6.1).
- ⏳ LLM rate limiter enforces configured RPM; `brain_rate_limited` events appear in audit (P6.2).

---

## Quality gates (same as always)

- `make merge-readiness-check` required before every PR merge.
- `make full-validation-check` before cutting the v0.1.6 release tag.
- All new PRs include matching `docs/CODEX_MEMORY.md` entries.

---

## Known items carried forward to v0.2 (not in v0.1.6 scope)

- Artifact cleanup tied to failed-job pruner (auto-remove artifact dir when job is pruned).
- Mission audit replay (re-run any completed mission from audit log).
- Queue health `--watch` mode (live refresh terminal UI).
- Full-duplex voice interaction loops (v0.3).
- Signed skills + skill marketplace (v0.4).
