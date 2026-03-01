# Voxera OS Alpha v0.1.6 — Security Hardening + Ops Visibility + Health Degradation (PLANNED)

**Status: in planning.** This is the proposed scope for v0.1.6. Items are not yet shipped.

For active daily-goal breakdown, see `docs/ROADMAP.md`.
For the previous shipped release, see `docs/ROADMAP_0.1.5.md`.

---

## Proposed scope for v0.1.6

v0.1.6 targets three pillars: **security hardening** (close the prompt injection surface),
**ops visibility** (surface the full daemon state in the panel), and **health degradation**
(make the daemon self-aware of sustained failure). Together these complete the control-plane
reliability story before the v0.3 voice expansion.

---

### Pillar 1 — Security hardening

#### PR #83 — Goal string sanitization + length cap
- Sanitize user-controlled goal strings before embedding in LLM prompt.
- Reject goals over 2,000 characters with a clear, actionable error message.
- Strip control characters and normalize whitespace to prevent prompt formatting tricks.
- Acceptance: `pytest tests/test_mission_planner.py` includes injection-shaped + overlength cases.

#### PR #84 — Structural `[USER DATA: ...]` delimiters in planner preamble
- Wrap user-provided content in `[USER DATA START]` / `[USER DATA END]` delimiters in the preamble.
- Structural separation prevents goal text from being interpreted as system instructions.
- Document delimiter format in `docs/SECURITY.md`.
- Acceptance: planner preamble output includes delimiter markers; existing tests unaffected.

#### PR #85 — Panel auth rate limiting
- Track failed Basic auth attempts per IP in the health snapshot.
- After 5 failures within 60 seconds: return 429 + `Retry-After: 60` header.
- Lockout events emitted as structured audit entries (`panel_auth_lockout`, ip, attempt_count).
- Lockout status visible in `voxera queue health` and `voxera doctor --quick`.
- Acceptance: 6 rapid failed auth attempts trigger lockout; correct 429 response returned.

---

### Pillar 2 — Ops visibility in panel

#### PR #86 — Panel home health widget
- Add a collapsible "Daemon Health" widget to the panel home (`/`) showing:
  - Lock status (held/stale/clear) + lock PID + stale age
  - Last brain fallback (reason, tier, timestamp) or "no recent fallbacks"
  - Last startup recovery (job count, orphan count, timestamp) or "clean"
  - Last shutdown outcome (clean/failed_shutdown) + timestamp
  - Daemon state: `healthy` / `degraded` (if PR #89 is merged)
- Sourced from `health.json`; no daemon call needed — safe for panel-only deployments.
- Acceptance: widget renders on home with all fields; empty/null fields show a neutral state.

#### PR #87 — Panel hygiene status + trigger page
- Add `/hygiene` page to panel with:
  - Last `voxera queue prune` result (timestamp, pruned count per bucket, reclaimed bytes).
  - Last `voxera queue reconcile` result (timestamp, issue counts per category).
  - "Run prune (dry-run)" button: triggers CLI prune in report mode, returns JSON result.
  - "Run reconcile" button: triggers CLI reconcile, returns JSON result.
- Results stored in `health.json` under `last_prune_result` and `last_reconcile_result`.
- Acceptance: buttons invoke commands and surface results without page reload (HTMX or polling).

#### PR #88 — Recovery + quarantine inspector in panel
- Add `/recovery` page showing:
  - All files under `notes/queue/recovery/` with size, timestamp, and type (approval/state).
  - All files under `notes/queue/quarantine/` with size and timestamp.
  - "Download as ZIP" button for each recovery/quarantine session.
- Read-only view; no deletions from panel.
- Acceptance: page lists recovery and quarantine directories; download returns valid ZIP.

---

### Pillar 3 — Health degradation + long-run daemon behavior

#### PR #89 — Health degradation state tracking
- Track consecutive brain fallback count in `health.json` under `consecutive_brain_failures`.
- When `consecutive_brain_failures >= 3`, set `daemon_state = "degraded"` in health snapshot.
- When a mission completes successfully, reset counter and restore `daemon_state = "healthy"`.
- Surface `daemon_state` in `voxera queue health` and `voxera doctor --quick`.
- Acceptance: 3 consecutive fallback events set state to `degraded`; success resets.

#### PR #90 — Brain backoff on repeated failures
- Add configurable delay between brain calls when consecutive fallbacks exceed a threshold.
- Default backoff: 2s after 3 failures, 8s after 5, 30s after 10. Cap at 60s.
- Configurable via `VOXERA_BRAIN_BACKOFF_BASE_S` and `VOXERA_BRAIN_BACKOFF_MAX_S`.
- Backoff events emitted to audit log (`brain_backoff_applied`, attempt, wait_s).
- Acceptance: consecutive-failure scenario shows increasing delays between plan attempts.

#### PR #91 — Structured shutdown outcome in `voxera queue health`
- Surface `last_shutdown_outcome`, `last_shutdown_job`, `last_shutdown_reason`, and
  `last_shutdown_ts` in the `voxera queue health` human-readable output.
- Include these fields in the JSON output of `voxera queue health --json`.
- Acceptance: after a graceful SIGTERM, `voxera queue health` shows the shutdown context.

---

### Pillar 4 — CI hardening & release packaging

#### PR #92 — Golden file validation CI job
- Add `tests/golden/` directory with committed dry-run output golden files.
- Add `make golden-update` target that regenerates golden files (requires explicit invocation).
- Add `make golden-check` target (and CI step) that fails if dry-run output diverges from golden.
- Use `--deterministic` flag so golden files are timestamp-independent.
- Acceptance: modifying planner context breaks `make golden-check`; CI fails on drift.

#### PR #93 — Release packaging polish + versioned release notes
- Add `scripts/release_notes.py` that generates a release notes Markdown snippet from
  `docs/CODEX_MEMORY.md` entries since the last version tag.
- Add `make release-notes` target that runs the script and writes `docs/RELEASE_NOTES_<version>.md`.
- Polish `make release-check` to validate all surfaces: `pyproject.toml`, README version header,
  and ROADMAP `current baseline` section are in sync.
- Acceptance: `make release-check` fails if any versioned surface disagrees.

---

### Pillar 5 — Provider / model UX

#### PR #94 — Keyring credential workflow improvements in setup wizard
- Show keyring availability status at setup start ("keyring: available" / "keyring: unavailable, using file fallback").
- After entering a new key: test the credential against the provider and show pass/fail before saving.
- Show current key status (stored in keyring / stored in file / not set) for each configured provider.
- Acceptance: setup detects keyring availability; credential test runs before save.

#### PR #95 — Provider profiles (named presets)
- Add provider profile presets to setup wizard: `openrouter-4tier`, `ollama-local`, `gemini-only`.
- Each preset configures the full brain tier stack (primary / fast / reasoning / fallback) from a template.
- Profiles stored in `config-templates/profiles/` and loadable via `voxera setup --profile <name>`.
- Acceptance: `voxera setup --profile ollama-local` writes correct brain config without interactive prompts.

---

### Pillar 6 — New utility commands

#### PR #96 — `voxera skills validate` command
- New command that eagerly validates all skill manifests (without launching the daemon).
- Checks: required fields present, entrypoint importable, capability declarations valid.
- Surface validation results in `voxera doctor` output ("Skills: N valid, M invalid").
- Emit audit event `skill_manifest_invalid` for each broken manifest.
- Acceptance: intentionally broken manifest shows in `voxera skills validate` output.

#### PR #97 — LLM rate limiter (token bucket around `brain.generate()`)
- Add configurable token bucket rate limiter around `brain.generate()` calls.
- Default: 30 calls/minute. Configurable via `VOXERA_BRAIN_RATE_LIMIT_RPM`.
- When limit exceeded: emit `brain_rate_limited` audit event; delay or return structured error.
- Surface current RPM and limit in `voxera queue health`.
- Acceptance: rapid repeated plan calls trigger rate limiting at configured threshold.

---

## Acceptance criteria for v0.1.6 (to be verified at release)

### Security
- [ ] Goal strings over 2,000 characters are rejected with a clear error before reaching the LLM.
- [ ] Planner preamble includes `[USER DATA START]` / `[USER DATA END]` delimiters.
- [ ] 6 rapid failed panel auth attempts trigger 429 lockout with `Retry-After` header.
- [ ] Lockout events appear in audit log.

### Ops visibility
- [ ] Panel home shows daemon health widget with lock/fallback/recovery/shutdown fields.
- [ ] Panel hygiene page surfaces last prune + reconcile results and trigger buttons.
- [ ] Panel recovery page lists `recovery/` and `quarantine/` contents.

### Health degradation
- [ ] 3 consecutive brain fallbacks set `daemon_state = "degraded"` in health snapshot.
- [ ] Backoff delays applied between brain calls after repeated fallbacks.
- [ ] `voxera queue health` shows last shutdown outcome with job and reason.

### CI + packaging
- [ ] `make golden-check` passes; fails on planner context drift.
- [ ] `make release-check` validates all versioned surfaces.

### Provider UX
- [ ] `voxera setup` shows keyring status and tests credentials before saving.
- [ ] `voxera setup --profile <name>` applies preset without interactive prompts.

### Utility commands
- [ ] `voxera skills validate` surfaces broken manifests; `voxera doctor` includes skill health.
- [ ] LLM rate limiter enforces configured RPM; `brain_rate_limited` events appear in audit.

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
