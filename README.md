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

- **Vera v0 conversational surface (new)**
  - Minimal standalone Vera web app (`voxera.vera_web.app`) intended to run on a separate port from the operator panel with short session context.
  - Explicit trust boundary messaging: Vera can converse, plan, and draft requests, but real-world side effects must go through VoxeraOS queue execution.
  - Explicit structured handoff channel: Vera drafts a minimal JSON job preview, then submits to the real VoxeraOS queue only after explicit user intent.
  - Normal chat remains preview-only by default (no implicit enqueue from ordinary conversation).
  - Honest lifecycle language: proposal/prepared/submitted/queued are distinct from executed/verified evidence states.
  - DEV-friendly diagnostics panel exposes prompt + session metadata for development, and includes an explicit "Clear chat + context" action.

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
- **Simple-intent routing (semantic guardrail, GitHub PR #144–#145)**
  - Goal-kind queue jobs pass through a deterministic classifier before planning with explicit open-intent splits: `open_terminal`, `open_url`, `open_app`, plus `write_file`, `read_file`, `run_command`, and `assistant_question`.
  - Open-intent routing is narrow and conservative (tightened in PR #145): URL presence alone does **not** route to `open_url`, meta/help/explanatory phrasing does **not** execute actions, and ambiguous open phrasing remains `unknown_or_ambiguous`. Terminal demo hijacks were removed and fail-closed behavior was explicitly restored.
  - Compound actionable requests preserve first-step intent metadata (`first_step_only`, `first_action_intent_kind`, `trailing_remainder`) so valid prefixes like "open terminal and …" constrain only step 1 without erasing the remainder.
  - If the planner's first step falls outside the allowed skill family the job fails closed before any side effects occur.
  - Evidence is visible in `execution_result.json` (`intent_route`, `evaluation_reason`) and
    `plan.json` (`intent_route`); action events `queue_simple_intent_routed` /
    `queue_simple_intent_mismatch` are emitted.  No approvals, capabilities, or policy gates are
    bypassed.
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
# separate Vera app (different port)
uvicorn voxera.vera_web.app:app --host 127.0.0.1 --port 8790
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
- **Deterministic open-intent routing tightened** (PR #145): fail-closed behavior restored for open actions; terminal demo hijacks removed; meta/help phrasing explicitly excluded.
- **Live job progress endpoints** (PR #146): panel polls `/jobs/{id}/progress` and `/assistant/progress/{id}` for real-time lifecycle/step/approval state from canonical artifacts only.
- **Red-team regression suite + multi-boundary hardening** (PR #147): `make security-check` gate now merge-blocking; traversal metadata leakage closed at classifier, serializer, runtime, and sidecar boundaries.
- **Queue lineage metadata** (PR #148): additive `parent_job_id` / `root_job_id` / `orchestration_depth` / `sequence_index` / `lineage_role` surfaced in artifacts, progress, and panel — observational only, no behavior changes.
- **Controlled child enqueue primitive** (PR #149): single child-job enqueue with server-side lineage computation, audit evidence, and full approval/policy/fail-closed semantics preserved.
- **Read-only child status rollups** (PR #150): parent progress and panel job detail now include `child_summary` (`total`, `done`, `pending`, `awaiting_approval`, `failed`, `canceled`, `unknown`) derived from canonical child job evidence; observational only (no orchestration semantics).

In short: the architecture extraction/modularization work is largely complete for queue, panel, and CLI boundaries; recent work has focused on security hardening, operator observability, and controlled orchestration primitives.

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
- **Post-v0.1.6 PRs shipped** (tracked in `docs/CODEX_MEMORY.md`):
  - **PR #145**: deterministic open-intent routing tightened; fail-closed behavior restored; terminal demo hijacks removed.
  - **PR #146**: live job progress endpoints (`/jobs/{id}/progress`, `/assistant/progress/{id}`); progressive-enhancement panel polling; stale failure-context shaping fixed.
  - **PR #147**: red-team regression suite (`tests/test_security_redteam.py`); traversal metadata leakage closed at four boundaries; `security-check` wired into merge gate.
  - **PR #148**: queue lineage metadata (additive, observational, no behavior changes); surfaced in artifacts, progress, and panel.
  - **PR #149**: controlled child enqueue primitive; server-side lineage computation; full approval/policy/fail-closed preservation; audit evidence surfaces.

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

Security regression workflow for adversarial fail-closed guardrails:

```bash
make security-check
```

- `make security-check` runs the focused red-team suite (`tests/test_security_redteam.py`) covering intent hijack resistance, planner mismatch fail-closed enforcement, notes/path-scope escape attempts, approval-gated state integrity, and progress/evidence consistency checks. Added in GitHub PR #147.
- This target is a regression-hardening layer; it does not introduce new runtime feature surfaces.

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

`validation-check` is the standard quick gate (format/lint/type + golden + security red-team + critical queue/CLI/doctor contract suites). `full-validation-check` extends that with full pytest, release/failed-sidecar guardrails, and the Golden4 E2E script.
For typing-ratchet baseline maintenance workflows, use `make update-mypy-baseline` intentionally (not as a routine shortcut).

Note: preserve the existing merge gate semantics documented as `merge-readiness / merge-readiness` when touching release process docs.


CI-required merge gate remains `make merge-readiness-check` (`merge-readiness / merge-readiness`), and now composes `make security-check` so adversarial regressions are merge-blocking.

## Structured execution artifact consumption (current behavior)

Operator-facing queue consumers now prefer canonical structured artifacts when present (`execution_result.json`, then `step_results.json`) and fall back to legacy state/error/approval sidecars when absent. This keeps old jobs readable while making panel/CLI/ops bundle summaries more deterministic for new jobs.

Assistant queue artifacts now also include additive lane metadata (`execution_envelope.execution.lane`/`execution_envelope.execution.fast_lane`, `execution_result.execution_lane`/`execution_result.fast_lane`, mirrored in `assistant_response.json`) so operators can see whether the request used `fast_read_only` or standard queue routing and why.

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

## Live job progress in Panel (GitHub PR #146)

The panel job detail pages now use **progressive enhancement** for live updates:

- `/jobs/<job_id>` renders fully server-side first (works without JavaScript).
- If JavaScript is available, the page polls `/jobs/<job_id>/progress` every ~2s and refreshes only evidence-backed fields.
- `/assistant` keeps its server-rendered fallback and can poll `/assistant/progress/<request_id>` for advisory lifecycle transitions.

Live fields are sourced from canonical artifacts only (`*.state.json`, `execution_result.json`, `step_results.json`, approval sidecars, failed sidecars, and assistant response artifacts). No speculative percentages or optimistic states are shown.


## Queue lineage metadata (GitHub PR #148)

Queue jobs now accept additive, descriptive lineage metadata: `parent_job_id`, `root_job_id`, `orchestration_depth`, `sequence_index`, and optional `lineage_role` (`root`/`child`). This metadata is observational only and does **not** change execution behavior, approvals, fail-closed semantics, scheduling, or context passing.

When present, lineage is surfaced in `plan.json`, `execution_envelope.json`, `execution_result.json`, job progress payloads, and panel job detail views.


## Controlled child enqueue primitive (GitHub PR #149)

Queue jobs can now explicitly request a **single** child enqueue by including:

```json
{
  "goal": "parent goal",
  "enqueue_child": {
    "goal": "child goal",
    "title": "optional child title"
  }
}
```

Behavior is intentionally narrow and fail-closed:
- exactly one child can be enqueued from a parent execution
- child enqueue is explicit (never inferred), non-recursive, and does not wait/aggregate
- child is written as a normal `inbox/child-*.json` queue job
- child lineage is computed/sanitized (`parent_job_id`, `root_job_id`, incremented depth, deterministic sequence index, role=`child`)
- child enqueue is auditable in `actions.jsonl`, `child_job_refs.json`, `execution_result.json` (`child_refs`), job progress (`child_refs`), and panel job detail

This is **not** a workflow engine: no dependency graph, no parent/child result passing, no autonomous decomposition, and no approval bypass.

### Vera natural-language preview drafting (PR #154)

Vera now recognizes broader conversational action phrasing while keeping the same queue trust boundary:

- Web nav phrasing like "open/go to/visit/take me to/bring up example.com" prepares the same minimal preview (`{"goal": "open https://example.com"}`).
- Explicit file-inspection asks (for example `read/open/inspect/show me ~/path`) prepare a file-read preview when the target is explicit.
- Common note/file-write asks (for example `make/create/write a note/file called hello.txt`) prepare the smallest supported write preview.
- Submit phrasing (`submit it`, `queue it`, `send it to VoxeraOS`, etc.) only hands off when a preview exists.
- Vera remains preview-first and truthful: prepared is not submitted, submitted is not executed, and execution truth comes from VoxeraOS evidence.
