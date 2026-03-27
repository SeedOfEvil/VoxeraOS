# Voxera OS Alpha v0.1.4 — Stability + UX Baseline (SHIPPED)

> **Historical snapshot:** This file captures roadmap framing at the time it was written and is not the current source of truth. For current milestone state, use `docs/ROADMAP.md`.


**Status: complete.** This release locked around one goal: make daily usage predictable before expanding into voice and new capability surfaces.

For the next phase of work, see `docs/ROADMAP.md`.

---

## What shipped in v0.1.4

### Queue and daemon reliability
- Failed-job sidecar contract: schema v1 with required fields (`schema_version`, `job`, `error`, `timestamp_ms`).
- Schema-version policy: writer pinned to v1, reader uses explicit allowlist, unknown versions rejected deterministically.
- Deterministic retention pruner: paired primary+sidecar as one logical unit, orphan-aware, max-age + max-count configurable.
- Health snapshots: `last_ok_event`, `last_ok_ts_ms`, `last_error` persisted to `notes/queue/health.json`.
- Queue pause/resume: `.paused` marker file; daemon stops new processing without killing in-flight jobs.
- Job relocation safety: legacy root drops and mis-dropped pending jobs auto-relocated to `inbox/` with audit events.

### User-facing UX
- `voxera queue status` exposes failed-sidecar invalid counts, retention policy, and latest prune-event summary.
- `voxera queue health` for operator quick-check (lock, counters, last_ok, last_error).
- Panel job lifecycle parity: approve, deny, cancel, retry, delete, canceled bucket.
- Panel Create Mission flow with deterministic inbox job writing.
- Job artifact bundle: `plan.json`, `actions.jsonl`, `stdout.txt`, `stderr.txt`, per-job and system zip exports.
- `voxera doctor --quick`: offline check (lock, health, queue counts) with no model calls.

### Planning and capabilities
- Cloud-assisted mission planner with fallback chains (primary → fast → reasoning → fallback).
- Runtime capabilities snapshot used as planner guardrail: unknown missions and disallowed apps rejected with suggestions.
- Planner preamble: Vera persona, configurable via `VOXERA_PLANNER_PREAMBLE` / `_PATH` / `VOXERA_PLANNER_AGENT_NAME`.
- Hard-gate `approval_required` check before any queue planning/execution.

### Operational observability
- Mypy ratchet baseline (`scripts/mypy_ratchet.py`, `tools/mypy-baseline.txt`) with protected review path.
- `make merge-readiness-check` gate: quality (fmt/lint/mypy) + release consistency checks.
- `make full-validation-check`: merge-readiness + failed-sidecar guardrails + full pytest + E2E smoke.
- Pre-push hook (`pre-commit`) runs merge-readiness gate locally matching CI behavior.
- CI captures quality/release logs and uploads `merge-readiness-logs` artifacts on failure.
- Separate `queue-failed-sidecar.yml` CI workflow guards sidecar schema and lifecycle tests.

---

## Acceptance criteria (all met)

### `voxera daemon`
- ✅ One-shot (`--once`) processing is deterministic for inbox/mission jobs.
- ✅ Failed jobs emit valid sidecar artifacts and status remains readable.
- ✅ Approval pauses and resumes are reflected in audit + queue state without manual repair.

### `voxera queue`
- ✅ `voxera queue status` exposes stable counts and failed metadata health.
- ✅ `voxera queue approvals list` gives clear, operator-usable pending approval details.
- ✅ Queue init path remains idempotent and safe on existing directories.

### `voxera missions`
- ✅ `voxera missions plan ... --dry-run` cleanly separates planned actions from execution.
- ✅ Mission execution outcomes are recorded consistently in mission log + queue artifacts.

### `voxera doctor`
- ✅ Provider/model checks report model-level health with clear error/latency notes.
- ✅ Doctor output is actionable for local troubleshooting before daemon runs.
- ✅ `--quick` offline mode works with no model calls.

---

## Release checklist (completed)

- ✅ Smoke CLI path:
  - `voxera --version`
  - `voxera doctor`
  - `voxera queue init`
  - `voxera queue status`
  - `voxera missions plan "prep a focused work session" --dry-run`
  - `voxera daemon --once`
- ✅ Service lifecycle: `make services-install`, `make services-status`
- ✅ Quality gate: `make merge-readiness-check`

---

## Known gaps carried forward to v0.2

These are not blocking for v0.1.4 but are tracked for the next release:

1. **Artifact directory cleanup** — `~/.voxera/artifacts/<job_id>/` has no automatic pruning tied to job retention. Tracked in ROADMAP.md Day 1.
2. **Graceful SIGTERM handling** — daemon uses Python default; mid-job shutdown can leave jobs in ambiguous state. Tracked in ROADMAP.md Day 4–5.
3. **Brain fallback error classification** — fallback chain swallows error type; reason is not surfaced structurally. Tracked in ROADMAP.md Day 2–3.
4. **Panel auth rate limiting** — single password, no lockout on repeated failures. Tracked in ROADMAP.md Day 4.
5. **Eager skill manifest validation** — manifests validated at job execution time, not at daemon startup. Tracked in ROADMAP.md Day 3.
6. **Prompt injection surface** — goal strings are injected into LLM prompt without length cap or structural delimiters. Tracked in ROADMAP.md Day 6–7.

---

## Out of scope (unchanged)

- Full-duplex voice interaction loops.
- Wake-word runtime integration.
- New high-risk capability surfaces that bypass existing policy/approval paths.
