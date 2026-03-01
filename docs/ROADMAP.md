# Roadmap

## Current baseline — Alpha v0.1.5 (shipped)

All v0.1.5 scope is complete. See `docs/ROADMAP_0.1.5.md` for locked acceptance criteria and release checklist.
For the previous release, see `docs/ROADMAP_0.1.4.md`.

**What shipped in v0.1.5:**
- `voxera artifacts prune`: operator-grade hygiene for `notes/queue/artifacts/`, dry-run by default, `--yes` to delete
- `--max-age-days` and `--max-count` flags with union selection policy
- `artifacts_retention_days` / `artifacts_retention_max_count` in runtime config + env vars
- Version bump to 0.1.5

**What shipped in v0.1.4:**
- Setup wizard (TUI), provider abstraction (cloud / local)
- Skill runner + policy gate + approval workflow
- Queue daemon with failed-sidecar schema v1, retention pruning, health snapshots
- Cloud-assisted mission planner with fallback chains and capabilities snapshot guardrails
- Minimal web panel: job lifecycle (approve/deny/retry/cancel/delete), audit, bundle export
- `voxera doctor` with quick offline mode and structured health output
- Mypy ratchet baseline + merge-readiness CI gate + pre-push parity

---

## Active work — v0.2 build-out

This is a solo project. Goals are sized for daily or multi-day sessions, not sprints.
Each item below is a self-contained improvement that can ship independently.

### Operational hygiene (do first — low risk, high value)

**Day 1**
- [ ] Tie artifact directory cleanup (`~/.voxera/artifacts/<job_id>/`) to the failed-job retention pruner.
      When a failed job is pruned, delete its artifact directory in the same pass.
- [x] Add `voxera artifacts prune` CLI command: dry-run by default, `--yes` to execute. *(shipped in v0.1.5)*
- [ ] Add `make type-debt` target: count and print number of entries in `tools/mypy-baseline.txt`.
      Surface as a CI annotation on PRs that touch typed modules.

**Day 2**
- [ ] Add CLI flags `--max-age` and `--max-count` to a new `voxera queue prune` command
      so retention policy can be tuned without setting env vars.
- [ ] Expose prune result in `voxera queue status` output (items pruned, space reclaimed).

### Observability hardening

**Day 2–3**
- [ ] Classify brain fallback reasons into a structured enum:
      `TIMEOUT | AUTH | RATE_LIMIT | MALFORMED | UNKNOWN`.
- [ ] Log fallback transitions as structured JSON events (reason, tier, latency_ms).
- [ ] Surface latest fallback reason and tier in `voxera doctor` output and health snapshots.
- [ ] Add `brain_fallback_reason` counter to `voxera queue health` output.

### Safety hardening

**Day 3**
- [ ] Validate all skill manifests eagerly at daemon startup (not lazily at job execution time).
- [ ] Surface invalid manifests in `voxera doctor` output with fix hints.
- [ ] Fail fast (daemon exits with non-zero) if a required built-in skill has a broken manifest.

**Day 4**
- [ ] Add failed-attempt rate limiter on panel Basic auth: 5 failed attempts → 60-second lockout.
- [ ] Log lockout events as structured audit entries (`panel_auth_lockout`, ip, attempt_count).
- [ ] Add LLM call rate limiter (token bucket) around `brain.generate()`.
      Default: 30 calls/minute. Configurable via `VOXERA_BRAIN_RATE_LIMIT_RPM`.

### Daemon reliability

**Day 4–5**
- [ ] Install SIGTERM handler in queue daemon:
      (1) stop accepting new jobs, (2) let in-flight job finish or mark it failed with
      `reason=shutdown`, (3) release lock and exit cleanly.
- [ ] Verify systemd `TimeoutStopSec` compliance: daemon stops within 10 seconds of SIGTERM.
- [ ] Add `shutdown_reason` field to failed-job sidecars emitted during graceful shutdown.

### Planner UX

**Day 5–7**
- [ ] Dry-run simulation mode: output deterministic plan (every skill + args) before execution.
      `voxera missions plan "<goal>" --dry-run` already exists; harden the output format.
- [ ] Structured planning preview: separate sections for "will execute" vs "requires approval".
- [ ] Add `--plan-only` flag to `voxera daemon --once` to show planned steps without running.

### Prompt injection mitigation

**Day 6–7**
- [ ] Sanitize and length-cap goal strings before embedding in LLM prompt.
      Reject goals over 2,000 characters with a clear error.
- [ ] Mark user-controlled content with `[USER DATA: ...]` delimiters in the preamble
      so the LLM receives structural separation between system context and user input.
- [ ] Add test: very long or injection-shaped goal strings are rejected or truncated cleanly.

---

## Near-term milestones (next 1–2 weeks)

These are larger and depend on day-by-day items above being stable.

**E2E test environment**
- [ ] Docker/Podman-based test env with Xvfb, wmctrl, xclip for clipboard/window skills.
- [ ] `make e2e-full` target that explicitly requires the display stack (distinct from CI-safe `make e2e`).
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

## v0.2 milestone (target: 2–3 weeks out)

Scope lock (analogous to v0.1.4 stability lock):
- All operational hygiene items above shipped and tested.
- Dry-run simulation mode stable.
- 10+ documented missions.
- Ollama fallback chain validated.
- Artifact cleanup and retention CLI flags merged.

Success metrics:
- `voxera queue prune --dry-run` shows correct retention preview.
- `voxera doctor` shows brain tier health including last fallback reason.
- All built-in skills pass eager manifest validation on daemon start.
- SIGTERM to daemon results in clean exit within 10 seconds.

---

## v0.3 milestone — Voice stack

**Voice-first command loop (bigger lift, plan carefully)**
- Wake word integration ("Hey Voxera").
- STT pipeline (Whisper or compatible engine).
- TTS pipeline (Coqui TTS or system TTS).
- Voice → router → plan → execute loop with audio confirmation.
- Fallback to panel/CLI if voice confidence is low.

This is the largest feature block. Audio stack is currently a placeholder in `src/voxera/audio/`.

---

## v0.4 milestone — Packaging + skill signing

- Signed skills: manifest signature verification before loading.
- Skill marketplace folder: discoverable, installable skills with trust tiers.
- ISO / image packaging: immutable base option with atomic updates.
- Safe-mode boot: limited skill set, no network, confirmation-only.

---

## Delivery guardrails (always-on, non-roadmap-critical)

- **Merge gate:** `make merge-readiness-check` is required before every PR.
- **Mypy ratchet:** `tools/mypy-baseline.txt` — never bulk-reset; triage before refresh.
- **Type-debt visibility:** track baseline entry count with `make type-debt` (to be added, Day 1).
- **Docs hygiene:** every feature PR lands with matching roadmap + memory + docs updates.
- **Release smoke:** `make full-validation-check` before cutting any release tag.
- **No-skip policy:** never use `--no-verify` on commits; fix the hook, don't bypass it.

---

## Recently completed (v0.1.4)

- Planner preamble customization: Vera persona, configurable agent name, prompt ordering.
- Runtime capabilities snapshot: guardrails for mission IDs and `system.open_app` targets.
- Panel job lifecycle parity: cancel, retry, delete, approval resolution, canceled bucket.
- Failed-sidecar schema v1: writer pin + reader allowlist, retention pruner (paired/orphan-aware).
- Mypy ratchet baseline + merge-readiness CI gate + pre-push hook parity.
- Incident bundle export: per-job and system snapshot zips from CLI and panel.
- Queue observability: retention policy and prune event summary in `voxera queue status`.
- Health snapshots: `last_ok_event` + `last_ok_ts_ms` so operators confirm recent daemon activity.
