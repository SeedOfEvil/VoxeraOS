# Durability + Reliability Release Roadmap (3 Weeks)

This roadmap turns the requested outcomes into an execution plan with concrete artifacts and release gates.

## Phase 1 (Week 1): Core durability backbone

### Scope
- Ship a **mission execution state machine** used by daemon processing.
- Persist a **mission record** for every queued job (including current step, status, and timestamps).
- Implement **startup resume** so interrupted missions continue safely after daemon restart.
- Add minimal **CLI status output** for mission-state visibility.

### Deliverables
- State machine states:
  - `queued`
  - `running`
  - `pending_approval`
  - `failed`
  - `completed`
- Mission record persisted to disk per queue job with:
  - mission/job IDs
  - normalized mission payload
  - `current_step`, `completed_steps`
  - `status`, `last_error`, and transition timestamps
- Daemon boot behavior:
  - scans existing mission records
  - resumes jobs in `running`/`pending_approval` states without losing work
- CLI:
  - `voxera queue status` includes active mission count and resumable jobs

### Definition of done
- If daemon is terminated during a mission, the mission is still visible and resumable on restart.
- Mission records are written on every state transition.

---

## Phase 2 (Week 2): Retries + idempotency hardening

### Scope
- Add a **retry taxonomy** to distinguish transient vs permanent failures.
- Track **attempt counters** at mission + step level.
- Prevent **duplicate ingestion/execution** from queue replays and restart races.
- Add crash/restart coverage in tests.

### Deliverables
- Retry classes:
  - `transient` (eligible for bounded retry)
  - `policy_blocked` (no retry)
  - `validation_error` (no retry)
  - `unknown_fatal` (no retry by default)
- Configurable retry policy:
  - max attempts per step
  - backoff strategy (initial + multiplier + cap)
- Idempotency protections:
  - stable idempotency key per mission/job
  - skip already-completed steps after resume
  - guard against duplicate job file ingestion when already terminally recorded
- Tests:
  - daemon restart mid-mission resumes correctly
  - retry counters increment as expected
  - duplicate queue input does not duplicate completed side effects

### Definition of done
- Crash loops cannot cause infinite re-execution of completed steps.
- Retry behavior is deterministic and auditable.

---

## Phase 3 (Week 3): Explainability + simulation gate

### Scope
- Add a **preview schema** that captures policy outcomes and rationale.
- Expose rationale in **CLI and panel explain views**.
- Enforce **high-risk simulation/approval gate** by default.

### Deliverables
- Preview schema fields:
  - per-step policy decision (`allow/ask/deny`)
  - rationale text
  - risk and capability metadata
  - simulation-required boolean
- CLI explain UX:
  - mission preview with per-step rationale
  - clear output for `ask` and `deny` decisions
- Panel explain UX:
  - pending approvals and denied actions show rationale verbatim
- High-risk enforcement:
  - any high-risk step requires simulation review and explicit approval path

### Definition of done
- Every policy `ask/deny` is user-visible with rationale in CLI/panel.
- High-risk mission execution cannot bypass simulation + approval path.

---

## Release exit criteria (must all pass)

1. **No lost missions across daemon restart.**
2. **No duplicate execution of completed steps after resume.**
3. **Every `ask/deny` policy outcome has user-visible rationale.**
4. **High-risk actions require simulation/approval path by default.**

## Validation matrix before release

- Functional
  - restart during step N resumes at N (or N+1 if persisted complete)
  - pending approval survives restart and remains actionable
  - denied missions remain terminal and non-resumable
- Reliability
  - transient failures retry up to cap with backoff
  - permanent failures fail fast and surface clear rationale
- UX/Explainability
  - CLI status includes resumable + blocked counts
  - CLI/panel explain views show policy rationale for each `ask/deny`
- Safety
  - high-risk steps blocked unless simulation/approval completed

