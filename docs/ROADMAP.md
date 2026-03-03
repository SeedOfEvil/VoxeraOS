# Roadmap

## Current baseline — post Alpha v0.1.5 (active development)

**Released in v0.1.5:**
- `voxera artifacts prune`: operator-grade artifact hygiene, dry-run by default, `--yes` to delete.
- `artifacts_retention_days` / `artifacts_retention_max_count` in runtime config + env vars.

**Shipped since v0.1.5 (unreleased, in codebase):**
- Daemon reliability: single-writer lock hardening, graceful SIGTERM shutdown with structured sidecar,
  deterministic startup recovery with orphan quarantine.
- Queue hygiene toolchain: `voxera queue prune` (terminal buckets) + `voxera queue reconcile`
  (report-only + quarantine-first fix mode) + symlink-safe quarantine paths.
- Brain fallback observability: `TIMEOUT | AUTH | RATE_LIMIT | MALFORMED | NETWORK | UNKNOWN` enum
  surfaced in health/doctor.
- Config path standardization across all CLI and documentation surfaces.
- `voxera demo` guided onboarding checklist (offline + online modes, non-destructive).
- Modernized setup wizard with non-destructive credential handling (keep/skip/replace).

**Released in v0.1.4:**
- Setup wizard (TUI), provider abstraction (cloud / local)
- Skill runner + policy gate + approval workflow
- Queue daemon with failed-sidecar schema v1, retention pruning, health snapshots
- Cloud-assisted mission planner with fallback chains and capabilities snapshot guardrails
- Minimal web panel: job lifecycle (approve/deny/retry/cancel/delete), audit, bundle export
- `voxera doctor` with quick offline mode and structured health output
- Mypy ratchet baseline + merge-readiness CI gate + pre-push parity

See `docs/ROADMAP_0.1.6.md` for the full planned scope of the upcoming v0.1.6 release.

---

## Active work — v0.1.6 build-out

This is a solo project. Goals are sized for daily or multi-day sessions, not sprints.
Each item below maps to stable roadmap IDs in `docs/ROADMAP_0.1.6.md`.

### Shipped so far (since v0.1.5)

- ✅ Goal string sanitization + 2,000-char preflight cap (planner hardening; tests + docs)
- ✅ OpenRouter automatic app attribution headers (`voxeraos.ca` + `VoxeraOS`; invisible defaults; overrides preserved)
- ✅ Terminal hello-world deterministic demo skill (`system.terminal_run_once`)

### Security hardening (do first — closes highest-risk surface)

**P1.1 — Goal string sanitization (SHIPPED)**
- [x] Sanitize user-controlled goal strings before embedding in LLM prompt.
- [x] Reject goals over 2,000 characters with a clear, actionable error.
- [x] Strip control characters; normalize whitespace.
- [ ] Add Unicode edge-case tests (injection-shaped and overlength tests are already complete).

**P1.2 — Structural delimiters in preamble (PLANNED)**
- [ ] Wrap user content with `[USER DATA START]` / `[USER DATA END]` markers in planner preamble.
- [ ] Update `src/voxera/core/planner_context.py` to emit delimiters.
- [ ] Verify existing planner tests pass; add a test confirming delimiter presence.
- [ ] Update `docs/SECURITY.md` known gaps to mark prompt injection as FIXED.

**P1.3 — Panel auth rate limiting (PLANNED)**
- [ ] Track failed Basic auth attempts per IP in `health.json`.
- [ ] Return 429 + `Retry-After: 60` after 5 failures within 60 seconds.
- [ ] Emit `panel_auth_lockout` audit events (ip, attempt_count).
- [ ] Surface lockout status in `voxera queue health` and `voxera doctor --quick`.

### Ops visibility in panel (highest user-visible value)

**P2.1 — Panel home health widget (SHIPPED)**
- [x] Add collapsible "Daemon Health" widget to panel home sourced from `health.json`.
- [x] Fields: lock status, last fallback (reason/tier/ts), last recovery (job/orphan counts), last shutdown.
- [x] Neutral display when fields are null/empty (no provider config, fresh install).
- [x] Daemon state badge from health snapshot with `healthy` default and future `degraded` support.

**P2.2 — Panel hygiene status + trigger page (SHIPPED)**
- [x] Added `/hygiene` panel page showing last prune result + last reconcile result.
- [x] "Run prune (dry-run)" and "Run reconcile" buttons that surface results inline.
- [x] Store last results in `health.json` under `last_prune_result` / `last_reconcile_result`.
- [x] Async page updates after trigger actions (no full reload).

**P2.3 — Recovery + quarantine inspector in panel (PLANNED)**
- [ ] Add `/recovery` panel page listing `recovery/` and `quarantine/` directory contents.
- [ ] Show file size, timestamp, and type (approval/state) for each entry.
- [ ] "Download as ZIP" button per recovery/quarantine session.

### Daemon health + long-run behavior

**P3.1 — Health degradation state tracking (PLANNED)**
- [ ] Track `consecutive_brain_failures` counter in `health.json`.
- [ ] Set `daemon_state = "degraded"` when counter >= 3; reset on successful mission.
- [ ] Surface `daemon_state` in `voxera queue health` and `voxera doctor --quick`.

**P3.2 — Brain backoff on repeated failures (PLANNED)**
- [ ] Add configurable delay between brain calls on consecutive fallbacks.
- [ ] Default schedule: 2s (after 3), 8s (after 5), 30s (after 10), cap at 60s.
- [ ] Configurable via `VOXERA_BRAIN_BACKOFF_BASE_S` / `VOXERA_BRAIN_BACKOFF_MAX_S`.
- [ ] Emit `brain_backoff_applied` audit event with `attempt` and `wait_s`.

**P3.3 — Structured shutdown outcome in `voxera queue health` (PLANNED)**
- [ ] Surface `last_shutdown_outcome`, `last_shutdown_job`, `last_shutdown_reason`, `last_shutdown_ts`
      in `voxera queue health` human-readable and `--json` output.
- [ ] Verify systemd `TimeoutStopSec` compliance (clean exit within 10s of SIGTERM).

### CI hardening & release packaging

**P4.1 — Golden file validation CI (PLANNED)**
- [ ] Add `tests/golden/` with committed dry-run output files.
- [ ] Add `make golden-update` (explicit regeneration) and `make golden-check` (CI gate).
- [ ] Wire `make golden-check` into the merge-readiness CI workflow.
- [ ] Use `--deterministic` flag for timestamp-independent golden outputs.

**P4.2 — Release packaging polish (PLANNED)**
- [ ] Add `scripts/release_notes.py` — generates release notes from `CODEX_MEMORY.md`.
- [ ] Add `make release-notes` target outputting `docs/RELEASE_NOTES_<version>.md`.
- [ ] Polish `make release-check`: validate `pyproject.toml`, README header, ROADMAP baseline all agree.

### Provider / model UX

**P5.0 — OpenRouter default attribution headers (SHIPPED)**
- [x] OpenRouter calls now auto-include app attribution headers by default (`HTTP-Referer`, `X-OpenRouter-Title`, `X-Title`) with invisible setup UX.
- [x] User/provider header overrides remain respected; defaults apply only when keys are absent.

**P5.1 — Keyring credential workflow improvements (PLANNED)**
- [ ] Show keyring availability at setup start (available / unavailable + file fallback).
- [ ] After entering a new key: test against provider before saving; show pass/fail.
- [ ] Show current key status (keyring / file / not set) per configured provider.

**P5.2 — Provider profiles (named presets) (PLANNED)**
- [ ] Add preset profile templates: `openrouter-4tier`, `ollama-local`, `gemini-only`.
- [ ] Store templates in `config-templates/profiles/`.
- [ ] Wire `voxera setup --profile <name>` to apply preset without interactive prompts.

**P5.3 — Config hygiene: auto-upgrade legacy placeholder defaults (PLANNED)**
- [ ] Treat legacy placeholder headers as "unset" (`HTTP-Referer: https://localhost` variants, `X-Title: Voxera OS`).
- [ ] For OpenRouter requests, auto-fill current defaults when placeholders are detected (`HTTP-Referer=https://voxeraos.ca`, `X-OpenRouter-Title` + `X-Title` = `VoxeraOS`).
- [ ] Preserve real user overrides (upgrade placeholder values only).
- [ ] Surface a small note/warning in `voxera doctor --quick` when legacy defaults are detected.
- [ ] Acceptance: localhost referer in config still yields OpenRouter attribution as VoxeraOS without manual edits.

### New utility commands

**P6.1 — `voxera skills validate` (PLANNED)**
- [ ] New CLI command: validate all skill manifests eagerly without launching daemon.
- [ ] Checks: required fields, entrypoint importable, capability declarations valid.
- [ ] Integrate into `voxera doctor` output ("Skills: N valid, M invalid").
- [ ] Emit `skill_manifest_invalid` audit events for each broken manifest.

**P6.2 — LLM rate limiter (PLANNED)**
- [ ] Add token-bucket rate limiter around `brain.generate()` calls.
- [ ] Default: 30 RPM. Configurable via `VOXERA_BRAIN_RATE_LIMIT_RPM`.
- [ ] Emit `brain_rate_limited` audit event when limit exceeded.
- [ ] Surface current RPM + limit in `voxera queue health`.

---

## Near-term milestones (next 2–3 weeks)

**E2E test environment**
- [ ] Docker/Podman-based test env with Xvfb, wmctrl, xclip for clipboard/window skills.
- [ ] `make e2e-full` target that explicitly requires the display stack.
- [ ] CI optional job that runs `e2e-full` on Ubuntu with display setup.

**Ollama / OpenAI-compat hardening**
- [ ] Improve Ollama cold-start tolerance (longer timeout, clearer error on model-not-found).
- [ ] Surface model name and endpoint in `voxera doctor` for all configured brain tiers.
- [ ] Test fallback chain with Ollama as `primary` and a cloud model as `fallback`.

**Mission catalog expansion**
- [ ] Document at least 10 production-usable missions in `missions/` with manifests and test data.
- [ ] Add `morning_routine`, `end_of_day`, `disk_check`, `update_all`, `focus_block` missions.
- [ ] Validate each mission with `voxera missions plan --dry-run` in release smoke checklist.

---

## v0.1.6 milestone (target: 2–3 weeks out)

See `docs/ROADMAP_0.1.6.md` for full planned scope and acceptance criteria.

Success metrics:
- Injection-shaped goals are rejected / sanitized before reaching the LLM.
- Panel home shows full daemon health at a glance (lock/fallback/recovery/shutdown/state).
- 3+ consecutive brain failures trigger degraded state; backoff delays applied.
- `make golden-check` passes in CI; `make release-check` validates all versioned surfaces.
- `voxera skills validate` surfaces broken manifests; skills health visible in doctor.

---

## v0.2 milestone — Panel-first UX + mission catalog

After v0.1.6, the next milestone focuses on the panel becoming the primary operator interface
and the mission catalog becoming a curated library of daily-driver automations.

- Full panel-based mission authoring (drag-and-drop step builder, template picker).
- Mission catalog: 25+ documented missions with tags, difficulty ratings, and test data.
- Panel mobile-responsive layout for tablet/phone approval workflows.
- Mission marketplace: share and discover community missions with signature verification.
- E2E test environment (Podman + Xvfb) fully integrated into CI.

---

## v0.3 milestone — Voice stack

**Voice-first command loop (bigger lift, plan carefully)**
- Wake word integration ("Hey Voxera").
- STT pipeline (Whisper or compatible engine).
- TTS pipeline (Coqui TTS or system TTS).
- Voice → router → plan → execute loop with audio confirmation.
- Fallback to panel/CLI if voice confidence is low.

Audio stack is currently a placeholder in `src/voxera/audio/`.

---

## v0.4 milestone — Packaging + skill signing

- Signed skills: manifest signature verification before loading.
- Skill marketplace folder: discoverable, installable skills with trust tiers.
- ISO / image packaging: immutable base option with atomic updates.
- Safe-mode boot: limited skill set, no network, confirmation-only.

---

## Delivery guardrails (always-on)

- **Merge gate:** `make merge-readiness-check` is required before every PR.
- **Mypy ratchet:** `tools/mypy-baseline.txt` — never bulk-reset; triage before refresh.
- **Docs hygiene:** every feature PR lands with matching roadmap + CODEX_MEMORY + docs updates.
- **Release smoke:** `make full-validation-check` before cutting any release tag.
- **No-skip policy:** never use `--no-verify` on commits; fix the hook, don't bypass it.

---

## Recently completed (post v0.1.5, pre v0.1.6)

- Daemon lock hardening: single-writer `flock`-based lock with PID validation and stale detection.
- Graceful SIGTERM shutdown: in-flight job marked `failed/` with `reason=shutdown`, sidecar written.
- Deterministic startup recovery: pending in-flight markers → `failed/`+sidecar, orphans → quarantine.
- Queue hygiene: `voxera queue prune` (terminal buckets, dry-run default, max-age + max-count flags).
- Queue diagnostics: `voxera queue reconcile` (report-only + quarantine-first fix mode).
- Symlink-safe quarantine: reconcile fix mode never follows symlinks outside queue root.
- Brain fallback classification: `TIMEOUT | AUTH | RATE_LIMIT | MALFORMED | NETWORK | UNKNOWN`.
- Config path standardization: all CLI and docs consistently reference `config.json` for ops config.
- `voxera demo` guided checklist: offline (default) and online modes, safe repeatable demo jobs.
- Modernized setup wizard: non-destructive credential choices (keep current / skip / enter new).
