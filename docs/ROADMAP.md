# Roadmap

## Current baseline — post Alpha v0.1.7 + PRs #145–#161 (active development)

**Released in v0.1.7:**
- Queue contract completion (current lane): producer-side structured intent (`job_intent`) now spans queue entrypoints and flows into execution envelope/artifacts for clearer planning→execution contracts without breaking legacy payloads.
- Assistant read-only fast lane (current lane): explicit low-risk advisory requests can use `execution_lane=fast_read_only` with deterministic fail-closed eligibility and canonical artifact/audit visibility preserved.
- Security hardening: goal sanitization + 2,000-char cap, `[USER DATA START]`/`[USER DATA END]` prompt boundaries, panel auth lockout (10 attempts/60s window → HTTP 429 + `Retry-After: 60`).
- Ops visibility: panel Daemon Health widget (health.json-sourced, no daemon calls), panel `/hygiene` page (prune dry-run + reconcile trigger, results in health.json).
- `sandbox.exec` argv canonicalization (`canonicalize_argv`): `command`/`argv`/`cmd` aliases, shlex.split strings, reject shell-control ambiguity, fail-fast on invalid/empty argv.
- Deterministic open-intent routing with compound first-step preservation and meta/help guards.
- OpenRouter invisible attribution headers (`voxeraos.ca` + `VoxeraOS`; invisible defaults; overrides preserved).
- Reliability: e2e_golden4 approval hang fix (filesystem-based detection, phase timeouts, diagnostics).

**Post-v0.1.6 PRs shipped (see `docs/CODEX_MEMORY.md` for full details):**
- **GitHub PR #145**: tightened deterministic open-intent routing; removed terminal demo hijacks; restored fail-closed behavior for all open-intent categories.
- **GitHub PR #146**: live job/assistant progress endpoints in panel; progressive-enhancement polling; fixed stale failure-context shaping in resolved job progress.
- **GitHub PR #147**: adversarial red-team regression suite; traversal metadata leakage closed at classifier/serializer/runtime/sidecar boundaries; `make security-check` now a first-class merge gate.
- **GitHub PR #148**: additive queue lineage metadata (`parent_job_id`, `root_job_id`, `orchestration_depth`, `sequence_index`, `lineage_role`) surfaced in artifacts, progress, and panel; observational only, no behavior changes.
- **GitHub PR #149**: controlled single child-enqueue primitive; server-side lineage computation; full approval/policy/fail-closed semantics preserved; canonical audit evidence surfaces.
- **GitHub PR #150**: additive read-only child status rollups (`child_summary`) in parent progress and panel job detail; observability-only with no parent/child orchestration behavior.
- **GitHub PR #152 (completed)**: Vera v0 minimal standalone chat web app (separate port) with short session context, explicit Vera↔VoxeraOS boundary, and preview-first execution posture (real side effects via VoxeraOS queue only).
- **GitHub PR #153 (completed)**: Added explicit Vera↔VoxeraOS queue handoff with structured job preview + explicit submit acknowledgement while preserving queue approval/policy/execution boundaries.
- **GitHub PR #154 (completed)**: Improved Vera natural-language action detection + preview preparation quality (broader conversational phrasing support, same strict preview/submit/execution trust boundary).
- **GitHub PR #155 (planned/current)**: Add evidence-aware Vera job outcome review + evidence-grounded next-step guidance with optional follow-up preview drafting (no auto-submit, no autonomy expansion).
- **GitHub PR #157 (completed)**: Added a narrow structured `write_file` queue request (`path` + `content` + optional `mode`) so contentful file writes execute on governed VoxeraOS rails with canonical evidence artifacts.
- **GitHub PR #160 (completed)**: Promoted Vera to first-class ops runtime ergonomics with dedicated make targets, `voxera-vera.service`, and default service-stack inclusion alongside daemon + panel.
- **GitHub PR #161 (completed)**: setup/demo productization pass — version/docs sync to 0.1.7, sequential brain-slot setup (`primary`/`fast`/`reasoning`/`fallback`), provider list selection, OpenRouter live model catalog retrieval with retry/manual fallback, and explicit post-setup launch options for Voxera/Vera panels.

**Released in v0.1.5:**
- `voxera artifacts prune`: operator-grade artifact hygiene, dry-run by default, `--yes` to delete.
- `artifacts_retention_days` / `artifacts_retention_max_count` in runtime config + env vars.
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

## Active work — v0.2 build-out

This is a solo project. Goals are sized for daily or multi-day sessions, not sprints.
Each item below maps to stable roadmap IDs in `docs/ROADMAP_0.1.6.md`.

### Vera conversational draft lifecycle hardening (PR #158)

- ✅ Persist one active structured preview per Vera session.
- ✅ Replace active preview on follow-up revisions (URL/filename/content/evidence-aware next-step edits).
- ✅ Keep active preview across lightweight acknowledgement turns and rolling turn trimming.
- ✅ Submit only the latest active preview on explicit handoff; fail closed honestly when missing.
- ✅ Clear preview only after confirmed successful queue handoff.

### All v0.1.6 items shipped

- ✅ Goal string sanitization + 2,000-char preflight cap — PR #85
- ✅ `[USER DATA START]`/`[USER DATA END]` structural delimiters in planner preamble — PR #88
- ✅ Panel auth lockout: 10 failures/60s window → HTTP 429 + `Retry-After: 60` — PR #89
- ✅ Panel Daemon Health widget (health.json-sourced, no daemon RPC) — PR #92
- ✅ Panel `/hygiene` page (prune dry-run + reconcile trigger, results in health.json) — PR #93
- ✅ `sandbox.exec` argv canonicalization (`canonicalize_argv`) — PR #91
- ✅ Terminal hello-world deterministic demo skill (`system.terminal_run_once`) — PR #84
- ✅ OpenRouter automatic app attribution headers — PR #86
- ✅ e2e_golden4 approval hang fix (filesystem-based detection, phase timeouts, diagnostics) — PR #90

### Simple-intent routing and fail-closed mismatch detection (SHIPPED — v1.3)

- ✅ Deterministic simple-intent classifier (`src/voxera/core/simple_intent.py`) — intent set v1:
  `assistant_question`, `open_terminal`, `open_url`, `open_app`, `write_file`, `read_file`, `run_command`, `unknown_or_ambiguous`.
- ✅ Skill-family allowlists per intent; conservative regex-only classifier (no NLP).
- ✅ `queue_simple_intent_routed` / `queue_simple_intent_mismatch` action events for goal-kind jobs.
- ✅ Mismatch detection: fail closed before any skill execution; `execution_result.json` carries
  `evaluation_reason=simple_intent_skill_family_mismatch`, `stop_reason=planner_intent_route_rejected`,
  and full `intent_route` evidence.
- ✅ Additive artifact extensions: `execution_envelope.json.request.simple_intent`, `plan.json.intent_route`.
- ✅ STV follow-up: "open terminal" accepts `system.terminal_run_once` as valid first step;
  `clipboard.copy` explicitly rejected for all deterministic read/write intents.
- ✅ STV follow-up 2: `write_file` classifier accepts "create a/an/new/empty file" articles.
- ✅ STV follow-up 3 (v1.3):
  - `read_file` classifier expanded to accept "read the file ~/path", "open and read ~/path", etc.
  - `SimpleIntentResult.extracted_target`: path extracted from goal for direct routing.
  - Deterministic read routing in planner (`_extract_simple_read_args` → `files.read_text`).
  - Deterministic named-file create routing (`_extract_named_file_write_args` → `files.write_text`).
  - `execution_result.json → intent_route` now present for **all** goal-kind jobs (not just mismatches),
    consistent with `execution_envelope.json → request.simple_intent`.
- ✅ 86 focused tests in `tests/test_simple_intent.py` (unit + integration + regression).

### Support/Infra shipped (reliability work)

**PR #90 — e2e_golden4 approval hang fix (SHIPPED)**
- [x] Replaced CLI-table-parsing approval detection with filesystem-based artifact check.
- [x] Added PHASE A (detect approval, 120s timeout) and PHASE B (wait for lifecycle, 300s timeout).
- [x] Added `dump_diag` helper for actionable diagnostics on failure.
- [x] Fixed settle loop: exits non-zero with clear summary on timeout.

**PR #91 — sandbox.exec canonicalize_argv (SHIPPED)**
- [x] `canonicalize_argv(args)` as single source of truth for sandbox command normalization.
- [x] Accepts `command`/`argv`/`cmd` priority-order aliases.
- [x] String values tokenized with `shlex.split`; shell-control operators rejected unless explicitly shell-wrapped.
- [x] Empty/whitespace argv list tokens are rejected (no silent stripping).
- [x] Fails fast with actionable error on empty/missing/non-string argv.
- [x] Two-layer defense: `PodmanSandboxRunner.run()` + `canonicalize_args()` pre-flight.

### Security hardening (SHIPPED in v0.1.6)

**P1.1 — Goal string sanitization (SHIPPED)**
- [x] Sanitize user-controlled goal strings before embedding in LLM prompt.
- [x] Reject goals over 2,000 characters with a clear, actionable error.
- [x] Strip control characters; normalize whitespace.

**P1.2 — Structural delimiters in preamble (SHIPPED)**
- [x] Wrap user content with `[USER DATA START]` / `[USER DATA END]` markers in planner preamble.
- [x] Updated `src/voxera/core/planner_context.py` to emit delimiters.
- [x] Existing planner tests pass; added test confirming delimiter presence.
- [x] Updated `docs/SECURITY.md` to document prompt boundary control.

**P1.3 — Panel auth rate limiting (SHIPPED)**
- [x] Track failed Basic auth attempts per IP in `health.json`.
- [x] Return 429 + `Retry-After: 60` after 10 failures within 60 seconds.
- [x] Emit `panel_auth_lockout` audit events (ip, attempt_count).
- [x] Surface lockout status in `voxera queue health` and `voxera doctor --quick`.

### Ops visibility in panel (SHIPPED in v0.1.6)

**P2.1 — Panel home health widget (SHIPPED — PR #92)**
- [x] Add collapsible "Daemon Health" widget to panel home sourced from `health.json`.
- [x] Fields: lock status, last fallback (reason/tier/ts), last recovery (job/orphan counts), last shutdown.
- [x] Neutral display when fields are null/empty (no provider config, fresh install).
- [x] Daemon state badge from health snapshot with `healthy` default and future `degraded` support.

**P2.2 — Panel hygiene status + trigger page (SHIPPED — PR #93)**
- [x] Added `/hygiene` panel page showing last prune result + last reconcile result.
- [x] "Run prune (dry-run)" and "Run reconcile" buttons that surface results inline.
- [x] Store last results in `health.json` under `last_prune_result` / `last_reconcile_result`.
- [x] Async page updates after trigger actions (no full reload).

**P2.3 — Recovery + quarantine inspector in panel (SHIPPED)**
- [x] Add `/recovery` panel page listing `recovery/` and `quarantine/` directory contents.
- [x] Show file/dir metadata (size, timestamp, kind, file count) for each entry.
- [x] "Download ZIP" button per recovery/quarantine session/loose item with operator-auth protection.

### Daemon health + long-run behavior

**P3.1 — Health degradation state tracking (SHIPPED)**
- [x] Track `consecutive_brain_failures` counter in `health.json` (always present, default `0`).
- [x] Set `daemon_state = "degraded"` when counter >= 3; reset on successful mission completion (DONE).
- [x] Snapshot includes `degraded_since_ts` / `degraded_reason` (nullable) for operator context.

**P3.2 — Brain backoff computation + applied planning sleep (SHIPPED)**
- [x] Added deterministic `compute_brain_backoff_s(consecutive_brain_failures)` ladder: 0 (<3), 2 (>=3), 8 (>=5), 30 (>=10), capped by max.
- [x] Added env overrides `VOXERA_BRAIN_BACKOFF_BASE_S` (default `2`) and `VOXERA_BRAIN_BACKOFF_MAX_S` (default `60`) with safe int parsing and clamped non-negative behavior.
- [x] Daemon planning path now applies `time.sleep(wait_s)` before `plan_mission(...)` when computed wait is > 0, at most once per plan attempt (orchestration layer only).
- [x] `health.json` always includes `brain_backoff_wait_s`, `brain_backoff_active` (`wait_s > 0`), and tracks `brain_backoff_last_applied_s` + `brain_backoff_last_applied_ts` (defaults `0`/`null`, keep-last-known when no new sleep).

**P3.3 — Structured shutdown outcome in `voxera queue health` (SHIPPED)**
- [x] Persist `last_shutdown_outcome`, `last_shutdown_job`, `last_shutdown_reason`, `last_shutdown_ts` in `health.json` with deterministic defaults and surface the same fields in `voxera queue health`, `voxera doctor --quick`, and panel home widget (health.json sourced only).
- [ ] Verify systemd `TimeoutStopSec` compliance (clean exit within 10s of SIGTERM).

### CI hardening & release packaging

**P4.1 — Golden operator-surface validation (SHIPPED)**
- [x] Added `tests/golden/` committed fixtures for high-value operator-visible CLI surfaces (`voxera --help`, key `queue` help commands, normalized `queue health --json`).
- [x] Added `make golden-update` (explicit regeneration) and `make golden-check` (drift gate).
- [x] Wired `make golden-check` into canonical `make validation-check` so local/CI merge-confidence flows run deterministic golden contract validation.
- [x] Added a focused adversarial red-team regression pack (`tests/test_security_redteam.py`) for intent hijack, planner mismatch fail-closed behavior, notes path escape attempts, approval-state integrity, and progress/evidence consistency.
- [x] Added `make security-check` and composed it into `make validation-check` and `make merge-readiness-check` so security regressions become a first-class merge gate.
- [x] Added deterministic normalization in test tooling for unstable fields (timestamps + environment-dependent paths) without runtime behavior changes.

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
- [x] Integrate into `voxera doctor` output with stable `skills.registry` skill-health summary (valid/invalid/incomplete/warning + top reason codes).
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

## v0.1.6 milestone (SHIPPED)

See `docs/ROADMAP_0.1.6.md` for full shipped scope and acceptance criteria.

Delivered:
- ✅ Injection-shaped goals are rejected / sanitized before reaching the LLM (+ prompt boundaries).
- ✅ Panel home shows full daemon health at a glance (lock/fallback/recovery/shutdown/state).
- ✅ Panel auth lockout prevents brute-force on operator password.
- ✅ `/hygiene` page surfaces prune + reconcile results and trigger buttons.
- ✅ `sandbox.exec` argv canonicalization prevents malformed command execution.

Not in v0.1.6 scope (carried to v0.2):
- 3+ consecutive brain failures triggering degraded state (P3.x).
- `voxera skills validate` command (P6.x).

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

## Recently completed (v0.1.6 release)

- Goal sanitization + 2,000-char cap: injection-shaped goals rejected before any brain calls (PR #85).
- `[USER DATA START]`/`[USER DATA END]` prompt boundaries: structural separation of user data in planner preamble (PR #88).
- Panel auth lockout: 10 failures/60s → HTTP 429 + `Retry-After: 60` + audit events (PR #89).
- Panel Daemon Health widget: lock status, fallback, recovery, shutdown from `health.json` only (PR #92).
- Panel `/hygiene` page: prune dry-run + reconcile trigger; results persisted to `health.json` (PR #93).
- `sandbox.exec` argv canonicalization: `command`/`argv`/`cmd` aliases, shlex.split, fail-fast (PR #91).
- `system.terminal_run_once` deterministic demo skill + deterministic planner route (PR #84).
- OpenRouter invisible attribution headers (`voxeraos.ca` + `VoxeraOS`) (PR #86).
- e2e_golden4 approval hang fix: filesystem-based detection, PHASE A/B timeouts, diagnostics (PR #90).

## Completed in v0.1.5 (archived)

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


## Observability UX refinements
- `voxera queue health` now supports `--watch`/`--interval` and presents Current State / Recent History / Historical Counters with JSON parity sections.
- Panel home adds a read-only **Performance Stats** tab to surface queue counts, degradation/backoff, fallback/error/shutdown context, and auth/runtime counters from `health.json`.

## Recently delivered

- Queue/panel/CLI/ops surfaces now consume canonical structured execution artifacts first (with legacy fallback) so operator and diagnostic views are more deterministic without breaking older jobs.

## GitHub PR #145 delivered: deterministic open-intent routing tightened

- Narrowed open routing into `open_terminal`, `open_url`, `open_app` with explicit skill family allowlists.
- Meta/help/explanatory phrasing now explicitly excluded from triggering action execution.
- Removed terminal demo hijacks and canned-command shortcuts from planner preamble.
- Compound first-step metadata (`first_step_only`, `first_action_intent_kind`, `trailing_remainder`) preserved for multi-step goals.
- Fail-closed: URL presence alone does not route to `open_url`; ambiguous phrasing stays `unknown_or_ambiguous`.

## GitHub PR #146 delivered: real-time assistant + queue progress UX

Delivered scope:

- live polling progress endpoints (`GET /jobs/{job_id}/progress`, `GET /assistant/progress/{request_id}`) for panel job detail and assistant request detail
- assistant lifecycle visibility (`queued -> planning -> advisory_running -> done/failed` when emitted)
- mission/queue lifecycle + step progress + approval wait state + terminal summaries
- lane metadata visibility (`execution_lane`, `fast_lane`, `intent_route`) in live payloads
- progressive enhancement fallback (server-rendered pages still functional without JS)
- fixed stale failure-context shaping bug: resolved job progress no longer inherits stale failure summaries

Non-goals preserved:

- no speculative progress percentages
- no bypass of approvals/policy/fail-closed routing
- no parallel truth source outside queue artifacts/contracts

## GitHub PR #147 delivered: red-team regression suite + multi-boundary hardening

- Added `tests/test_security_redteam.py` adversarial regression pack (intent hijack, planner mismatch, path traversal, approval-state integrity, progress/evidence consistency).
- Uncovered and fixed traversal metadata leakage at four boundaries: classifier, serializer, runtime, sidecar.
- `make security-check` added and wired into `make validation-check` and `make merge-readiness-check` as a first-class merge gate.

## GitHub PR #148 delivered: queue lineage metadata

- [x] PR 9A (GitHub PR #148): added additive queue lineage metadata + evidence visibility (`plan.json`, `execution_envelope.json`, `execution_result.json`, panel/progress), with no orchestration behavior changes.

## GitHub PR #149 delivered: controlled child enqueue primitive

- [x] PR 9B-lite (GitHub PR #149): added explicit single-child enqueue primitive with deterministic lineage propagation and canonical enqueue evidence (`child_job_refs.json`, `actions.jsonl`, result/progress/panel child refs), without workflow orchestration. Full approval/policy/fail-closed semantics preserved.

- **GitHub PR #157 (completed)**: Added a narrow structured `write_file` queue request (`path` + `content` + optional `mode`) so contentful file writes execute on existing governed VoxeraOS rails with canonical artifact evidence.


## 2026-03-09 — PR #159 (planned/landed): authoritative Vera preview control surface
- Make the visible preview pane authoritative over session draft state (displayed preview = active preview = submit target).
- Add direct pane submit control wired to the existing VoxeraOS queue handoff path.
- Map natural “use this preview” approval phrasing to active-preview submit only when preview exists; otherwise fail closed honestly.
- Keep execution semantics unchanged: submission only, VoxeraOS remains the sole execution layer.
