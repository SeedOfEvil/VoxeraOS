# Voxera OS — Queue-driven AI control plane for Linux operators

Voxera OS is a local-first control plane that turns operator intent into auditable queue jobs, policy-gated mission execution, and a web panel for approvals, recovery, and incident handling.

## What VoxeraOS is today

VoxeraOS is a Python application with three operator surfaces: a `voxera` CLI, a FastAPI panel, and a queue daemon that processes JSON jobs from `notes/queue/inbox/`.

In the current implementation, the queue is the system boundary: jobs are normalized, planned or resolved into missions, run through policy and approvals, and moved through explicit lifecycle buckets (`inbox/`, `pending/`, `done/`, `failed/`, `canceled/`) with sidecars and artifacts for debugging and audit.

This repository already includes queue hygiene, startup recovery, lock management, advisory assistant transport, doctor diagnostics, and deterministic incident bundle export. See `docs/ARCHITECTURE.md` and `docs/ops.md` for deeper operational detail.

## Why VoxeraOS exists

Most AI automation demos skip operator control: they execute directly, hide state, and make failure handling opaque. VoxeraOS takes the opposite approach: queue-first execution, explicit approvals, and filesystem-visible state so operators can inspect, pause, resume, reconcile, recover, and package incidents.

That architecture matters because it keeps behavior observable and recoverable even when providers fail, approvals block, or daemon restarts occur.

## Current key capabilities

- **Queue-driven mission execution**
  - Daemon reads `notes/queue/inbox/*.json`, enforces queue contracts, and drives deterministic lifecycle transitions.
- **Approval workflow (HITL gates)**
  - Policy ASK decisions produce approval artifacts in `pending/approvals/*.approval.json`, with CLI + panel approval/deny flows and optional approval grants.
- **Runtime capability enforcement (fail-closed)**
  - Before any skill invocation, runtime validates manifest capability metadata and effect classification. Missing/malformed/unknown/ambiguous metadata is blocked, policy `deny` is blocked, and policy `ask` enters approval flow; blocked reasons are written into canonical `step_results.json` and `execution_result.json` artifacts.
- **Operator panel**
  - Home/jobs dashboards, queue controls, mission creation, approvals, retries/cancel/delete, hygiene operations, recovery inspector, and bundle downloads.
- **Assistant advisory lane**
  - `/assistant` submits queue-backed advisory jobs (`assistant_question`), with bounded thread continuity and degraded read-only fallback mode when queue transport is unavailable.
  - Explicitly read-only advisory requests can use an in-control-plane fast lane (`execution_lane=fast_read_only`) when deterministic eligibility checks pass (including canonical request-kind detection via `job_intent.request_kind`); uncertain or non-eligible requests fail closed to normal queue lane (`execution_lane=queue`).
- **Health + doctor + observability**
  - `health.json` snapshots, semantic queue health views, auth lockout counters, fallback metadata, and `voxera doctor` checks.
  - `voxera doctor` now includes a `skills.registry` row summarizing strict manifest health (`valid` / `invalid` / `incomplete` / `warning`) with top remediation-oriented reason codes.
- **Strict skill manifest contract**
  - Discovery validates manifest schema strictly (no extra keys, non-empty IDs/entrypoints, deterministic string-list checks) and classifies skill health as valid/invalid/incomplete/warning.
  - Invalid manifests remain fail-closed; incomplete manifests are visible to operators and excluded from usable runtime skill set until governance metadata is fixed.
- **Hygiene and recovery tooling**
  - `voxera queue prune`, `voxera artifacts prune`, `voxera queue reconcile`, startup recovery quarantine, and `/recovery` archive exports.
- **Incident and ops bundles**
  - Deterministic per-job and system bundle exports from CLI and panel.
- **Modular CLI + panel + queue internals**
  - Composition roots are thin and domain logic lives in focused modules.

## Architecture at a glance

VoxeraOS currently follows a **thin composition root + focused domain modules** pattern:

- **Queue daemon root**: `src/voxera/core/queue_daemon.py`
  - Composes queue runtime, lock lifecycle, health/status surfaces, and routing.
- **Queue domain modules**:
  - `queue_execution.py`: payload normalization, mission building/planning, lifecycle processing.
  - `queue_approvals.py`: approval artifacts, grants, resolution flows.
  - `queue_recovery.py`: startup recovery + shutdown handling.
  - `queue_assistant.py`: advisory queue lane and provider fallback sequencing.
  - `queue_state.py`: `*.state.json` sidecar schema/write helpers.
  - `queue_paths.py`: deterministic move/collision path helpers.
- **Panel root**: `src/voxera/panel/app.py`
  - FastAPI composition + shared security/mutation wiring.
- **Panel route domains**:
  - `routes_assistant.py`, `routes_missions.py`, `routes_bundle.py`, `routes_queue_control.py`, `routes_hygiene.py`, `routes_recovery.py` (plus home/jobs route modules).
- **CLI root**: `src/voxera/cli.py`
  - Typer composition + command registration.
- **CLI domain modules**:
  - `cli_queue.py` (queue/inbox/artifacts/operator flows), `cli_doctor.py` (doctor wiring), `cli_config.py` (runtime config commands), `cli_skills_missions.py` (skills/missions/run command logic), `cli_ops.py` (ops capability/bundle commands), `cli_runtime.py` (setup/demo/status/audit/panel/daemon command logic), `cli_common.py` (shared options/helpers).

## Repository structure (high signal)

- `src/voxera/core/` — queue daemon + mission/planner/control-plane internals.
- `src/voxera/panel/` — FastAPI operator panel and route-domain modules.
- `src/voxera/cli.py` plus `src/voxera/cli_*.py` modules — CLI entrypoints and focused command implementations.
- `docs/ARCHITECTURE.md` — architecture map and module boundaries.
- `docs/ops.md` — day-2 operations and incident workflows.
- `docs/CODEX_MEMORY.md` — implementation history log across major PR milestones.
- `deploy/systemd/user/` — user service units for daemon and panel.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
```

Initialize local config + queue directories:

```bash
voxera setup
voxera queue init
```

Run the two core services locally:

```bash
voxera daemon
voxera panel --host 127.0.0.1 --port 8787
```

Or install user services:

```bash
make services-install
make services-status
```

## Common workflows

### Queue + daemon

```bash
voxera inbox add "Run a quick system check"
voxera queue status
voxera daemon --once
```

### Approvals

```bash
voxera queue approvals list
voxera queue approvals approve <job_ref>
voxera queue approvals deny <job_ref>
```

### Health + diagnostics

```bash
voxera queue health
voxera doctor --quick
voxera doctor --self-test
```

### Hygiene + recovery

```bash
voxera queue reconcile --json
voxera queue prune --max-age-days 14
voxera artifacts prune --max-age-days 30
```

### Incident bundles

```bash
voxera ops bundle system
voxera ops bundle job <job_ref>
```

## Documentation map

- **Architecture and module ownership**: `docs/ARCHITECTURE.md`
- **Operations runbook and service workflows**: `docs/ops.md`
- **Security posture and hardening notes**: `docs/SECURITY.md`
- **Roadmap and release milestone tracking**: `docs/ROADMAP.md`, `docs/ROADMAP_0.1.6.md`
- **Implementation memory / PR history**: `docs/CODEX_MEMORY.md`

## Project status

VoxeraOS is in **Alpha (v0.1.6)** and already includes the major control-plane foundation:

- Queue daemon and queue lifecycle buckets are implemented and operator-visible.
- Approval artifacts, policy ask/allow/deny gates, and CLI/panel resolution flows are implemented.
- Queue startup recovery, lock reclamation, and quarantine-first hygiene flows are implemented.
- Panel route modularization and CLI modularization are completed.
- Advisory assistant queue lane with fallback/degraded behavior is implemented.
- Incident/ops bundle export, doctor checks, and health semantics are implemented.

In short: the architecture extraction/modularization work is largely complete for queue, panel, and CLI boundaries; ongoing work is now mostly incremental hardening and UX improvements.

## Roadmap / what’s next

Near-term roadmap focus in `docs/ROADMAP.md` is on:

- Additional reliability/ops hardening around daemon behavior and health semantics.
- CI/release guardrail refinement and docs consistency automation.
- Provider/model UX improvements (credential workflow and safer profiles).
- Planned extensions such as skill validation and future voice-layer work (tracked, not yet implemented).

Major completed milestones already backfilled in repo history:

- **v0.1.4**: stability + UX baseline (queue daemon, approvals, mission flows, panel/doctor foundations).
- **v0.1.5**: hygiene/recovery baseline (`artifacts prune`, `queue prune`, `queue reconcile`, lock/shutdown hardening).
- **v0.1.6**: security hardening + panel ops visibility + sandbox argv canonicalization + modularization wave.

## Contributing and maintenance guidance

When extending VoxeraOS:

- Keep composition roots thin (`queue_daemon.py`, `panel/app.py`, `cli.py`).
- Add new behavior in focused domain modules (`queue_*`, `routes_*`, `cli_*`).
- Preserve operator-visible contracts (queue paths, CLI flags, panel route behavior) unless intentionally versioned/changed.
- Prefer additive, auditable workflows over implicit behavior.

Before opening a PR, run the canonical hardening validation target:

```bash
make validation-check
```

Golden contract workflow for operator-visible CLI surfaces:

```bash
make golden-check
```

- `make golden-check` validates committed baselines in `tests/golden/` for high-value
  operator surfaces (`voxera --help`, key `voxera queue ... --help` commands, and a
  normalized empty `voxera queue health --json` payload).
- `make golden-update` intentionally regenerates those baselines when a reviewed CLI
  contract change is expected.
- Golden files are distinct from behavioral snapshot/contract tests: they optimize
  human-readable diff review for operator-facing text/JSON surfaces.

For release-grade confidence, run:

```bash
make full-validation-check
```

`validation-check` is the standard quick gate (format/lint/type + critical queue/CLI/doctor contract suites). `full-validation-check` extends that with full pytest, release/failed-sidecar guardrails, and the Golden4 E2E script.
For typing-ratchet baseline maintenance workflows, use `make update-mypy-baseline` intentionally (not as a routine shortcut).

Note: preserve the existing merge gate semantics documented as `merge-readiness / merge-readiness` when touching release process docs.


CI-required merge gate remains `make merge-readiness-check` (`merge-readiness / merge-readiness`).

## Structured execution artifact consumption (current behavior)

Operator-facing queue consumers now prefer canonical structured artifacts when present (`execution_result.json`, then `step_results.json`) and fall back to legacy state/error/approval sidecars when absent. This keeps old jobs readable while making panel/CLI/ops bundle summaries more deterministic for new jobs.

Assistant queue artifacts now also include additive lane metadata (`execution_result.execution_lane`, `execution_result.fast_lane`, mirrored in `assistant_response.json`) so operators can see whether the request used `fast_read_only` or standard queue routing and why.

## Structured producer intent (queue producer/planner lane)

Queue producers now attach additive canonical `job_intent` metadata to queued jobs (for example panel mission prompts, inbox CLI jobs, and assistant advisory jobs). This internal shape captures request kind/source lane, normalized title/goal/notes, optional step summaries, candidate skills/action hints, approval/artifact hints, operator rationale/summary, and optional machine planning payload.

During daemon normalization, Voxera also derives `job_intent` for legacy jobs that do not provide it, preserving backward compatibility while giving downstream consumers a deterministic intent contract. When available, daemon artifacts include `artifacts/<job>/job_intent.json`, and `execution_envelope.json` now carries `request.job_intent` for end-to-end planning→execution→operator traceability.

## Execution boundary hardening (PR 3)

- `sandbox.exec` now fails closed for ambiguous command strings containing shell-control operators (`&&`, `;`, pipes, redirects) unless the caller uses explicit argv shell wrapping like `['bash','-lc','...']`.
- List-form argv no longer silently strips empty/whitespace tokens; malformed argv is rejected with canonical `skill_result` payloads (`error_class=invalid_input`).
- `files.read_text` and `files.write_text` share centralized confined-path normalization with deterministic out-of-bounds/traversal/symlink-escape blocking.
- `system.open_app` and `system.open_url` enforce stricter normalized inputs and now always emit canonical `skill_result` metadata for allowlist/input failures.


### Bounded evaluate-and-replan loop (PR 4)

Queue mission execution now performs an explicit evaluator phase after each attempt and classifies
outcomes into deterministic classes: `succeeded`, `awaiting_approval`, `blocked_non_retryable`,
`invalid_input_non_retryable`, `retryable_failure`, `replannable_mismatch`, and `terminal_failure`.

Replanning is bounded (`max_replan_attempts`, default `1`) and only considered for
`retryable_failure`/`replannable_mismatch` on goal-planned jobs. Approval-required outcomes,
policy/capability blocks, and hard boundary failures do not replan and remain fail-closed.

Artifacts now include attempt/evaluation fields (`attempt_index`, `replan_count`,
`evaluation_class`, `evaluation_reason`, `stop_reason`) and per-attempt plan snapshots
(`plan.attempt-<n>.json`) so operators can inspect adaptation history without log reconstruction.

Planner mismatch failures are also captured as first-class attempt artifacts: if a goal-planning pass
returns an unknown skill, Voxera records `plan.attempt-1.json` with `planning_error` metadata and can
bounded-replan once (`max_replan_attempts`) to produce a second governed attempt.

### Skill result contract normalization (PR 5)

Built-in skills now converge on a normalized `skill_result` shape for both success and failure paths:

- `summary` (compact operator-facing result)
- `machine_payload` (structured facts, no prose)
- `output_artifacts` (explicit artifact paths when produced)
- `operator_note` and `next_action_hint`
- `retryable`, `blocked`, and `approval_status`
- `error` and `error_class` for consistent failure semantics

Major consumers (mission step shaping and queue structured artifact builders) now prefer this contract first and keep legacy fallbacks only for compatibility with older job artifacts.
