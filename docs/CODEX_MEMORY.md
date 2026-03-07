# Codex Memory Log

This file is the persistent implementation-memory timeline for major VoxeraOS architecture and contract changes.

---

## 2026-03-06 — PR #124 — CLI modularization completed (`cli.py` / `cli_queue.py` / `cli_doctor.py` / `cli_common.py`)
- What changed:
  - Split the CLI into a thin Typer composition root (`src/voxera/cli.py`) plus focused command modules.
  - `src/voxera/cli_queue.py` now owns queue/inbox/artifacts/operator command registration and implementations.
  - `src/voxera/cli_doctor.py` owns doctor command registration; `src/voxera/cli_common.py` owns shared options/helpers.
- Boundary established:
  - `cli.py` is registration/composition only; domain logic belongs in `cli_*` modules.
- Semantics preserved:
  - Command/group names, help surfaces, flags, and operator contract outputs remain stable.
- Guardrails/tests emphasized:
  - CLI contract/help snapshots and queue CLI tests continue to protect command surface stability.

## 2026-03-06 — PR #123 — execution pipeline extracted from daemon
- What changed:
  - Extracted queue mission execution/process pipeline into `src/voxera/core/queue_execution.py` (`QueueExecutionMixin`).
- Boundary established:
  - Intake filtering, payload normalization, planning integration, and queued→terminal result handling live in the execution module.
- Semantics preserved:
  - Existing lifecycle progression, failure handling, and artifact/sidecar ordering behavior were intentionally preserved.
- Guardrails/tests emphasized:
  - Queue daemon + contract snapshot tests remain the primary execution contract checks.

## 2026-03-06 — PR #122 — startup recovery + shutdown handling extracted
- What changed:
  - Moved startup recovery and deterministic shutdown failure behavior into `src/voxera/core/queue_recovery.py` (`QueueRecoveryMixin`).
- Boundary established:
  - Recovery scanning/quarantine/reporting and shutdown in-flight finalization now live outside the daemon root.
- Semantics preserved:
  - Fail-fast startup recovery policy, quarantine-not-delete behavior, and shutdown failure sidecar semantics remain unchanged.
- Guardrails/tests emphasized:
  - Startup recovery contract snapshots and queue daemon tests validate deterministic recovery/shutdown outcomes.

## 2026-03-06 — PR #121 — assistant advisory lane extracted
- What changed:
  - Extracted assistant queue-lane processing into `src/voxera/core/queue_assistant.py`.
- Boundary established:
  - Assistant job processing, provider fallback sequencing, and assistant response artifact writes are isolated from daemon orchestration.
- Semantics preserved:
  - Queue-backed advisory behavior, degraded fallback handling, and assistant artifact schema usage remain intact.
- Guardrails/tests emphasized:
  - Operator assistant queue tests validate advisory lane behavior and fallback semantics.

## 2026-03-06 — PR #120 — approval workflow extracted
- What changed:
  - Moved approval prompts/artifacts/grants/resolution logic into `src/voxera/core/queue_approvals.py` (`QueueApprovalMixin`).
- Boundary established:
  - Approval lifecycle (`*.approval.json`, `*.pending.json`, grant scope persistence, approve/deny resolution) is owned by approval module.
- Semantics preserved:
  - Hard approval gates and deny→failed/blocked semantics remain unchanged.
- Guardrails/tests emphasized:
  - Queue daemon and approval-path tests cover pending approval, approval grant, approve, and deny transitions.

## 2026-03-06 — PR #119 — state sidecar lifecycle helpers extracted
- What changed:
  - Moved `.state.json` sidecar path/read/write/update functions into `src/voxera/core/queue_state.py`.
- Boundary established:
  - Sidecar lifecycle snapshot logic and transition timestamp handling are centralized.
- Semantics preserved:
  - Existing state-sidecar schema shape and lifecycle transition semantics remain compatible.
- Guardrails/tests emphasized:
  - Queue tests asserting lifecycle sidecars continue to enforce state contract behavior.

## 2026-03-06 — PR #118 / #117 / #116 — panel route modularization completed
- What changed:
  - Refactored panel into FastAPI composition root (`src/voxera/panel/app.py`) with route-domain modules:
    - assistant, missions, bundle, queue control, hygiene, recovery, plus home/jobs route slices.
- Boundary established:
  - `app.py` wires shared concerns + registration; route behavior ownership lives in `routes_*.py` modules.
- Semantics preserved:
  - Public panel path surface and operator workflows were kept stable during extraction.
- Guardrails/tests emphasized:
  - Panel contract snapshot and panel route tests protect route/path behavior and key mutation flows.

## 2026-03-05 — PR #115 — contract/guardrail baseline for public surfaces
- What changed:
  - Added/expanded snapshot-style contract coverage for CLI, panel route surfaces, and daemon/operator flows.
- Boundary established:
  - Public operator surfaces are treated as explicit compatibility contracts.
- Semantics preserved:
  - No runtime behavior changes; focus was regression guardrails.
- Guardrails/tests emphasized:
  - Snapshot-based tests for CLI help/routes and daemon contract behavior.

## 2026-03-03 to 2026-03-04 — failed-sidecar contract + validation hardening
- What changed:
  - Standardized failed sidecar schema behavior and strengthened validation/reporting across queue and ops surfaces.
- Boundary established:
  - Failed sidecars are first-class operator-visible contract artifacts paired with failed primary payloads.
- Semantics preserved:
  - Error sidecar presence/shape semantics remained deterministic and backwards-safe.
- Guardrails/tests emphasized:
  - Failed-sidecar focused checks and queue failure-path tests.

## 2026-03-03 — PR #81 and PR #80 lineage — deterministic startup recovery + shutdown reliability
- What changed:
  - Added deterministic startup recovery, lock hardening, and graceful/deterministic shutdown failure handling.
- Boundary established:
  - Recovery and shutdown semantics are explicit operational contracts (health counters + audit/report fields).
- Semantics preserved:
  - Single-writer lock model and queue safety-first behavior remain the default operator posture.
- Guardrails/tests emphasized:
  - Queue daemon recovery/shutdown tests plus contract snapshots for startup-recovery report shape.

## 2026-03-03 — PR #78 / #79 / #76 / #75 — hygiene: prune + reconcile + quarantine safety model
- What changed:
  - Added `voxera queue prune` (terminal buckets), `voxera queue reconcile` report diagnostics, and explicit quarantine-first fix mode.
  - Hardened reconcile safety (including symlink-safe handling).
- Boundary established:
  - Hygiene commands own queue-shape correction; queue daemon remains execution control-plane.
- Semantics preserved:
  - Reconcile is report-first by default; mutations require explicit `--fix --yes`.
- Guardrails/tests emphasized:
  - CLI queue prune/reconcile tests cover dry-run defaults, candidate selection, and quarantine safety behavior.

## 2026-03-02 to 2026-03-03 — assistant and observability hardening wave
- What changed:
  - Added queue-backed assistant transport behavior improvements and degraded advisory fallback paths.
  - Expanded operator health/panel observability surfaces and health semantics consistency.
- Boundary established:
  - Advisory lane remains read-only and queue-mediated for control-plane visibility.
- Semantics preserved:
  - Assistant lane does not bypass policy or execute mutating tools.
- Guardrails/tests emphasized:
  - Assistant queue tests, panel tests, and doctor/health checks for operator-visible diagnostics.

## Ongoing documentation alignment policy
- Keep README, ARCHITECTURE, ops, and this memory file synchronized with actual module ownership.
- When boundaries move (queue/panel/CLI extraction), record:
  - what moved,
  - which file now owns it,
  - what operator-visible semantics stayed the same,
  - and what tests/guardrails protect the contract.
