# 03 — Queue Object Model and Lifecycle

This document describes the queue object model as it is implemented in the current repo. It is the condensed, index-bundle version of `docs/QUEUE_OBJECT_MODEL.md` and `docs/QUEUE_CONSTITUTION.md`; those two files remain the canonical long-form contracts. Everything here is grounded in `src/voxera/core/queue_*.py`.

## Why the queue exists

VoxeraOS is a queue-driven AI control plane. The queue is the submit boundary: once a job enters `inbox/`, preview truth ends and queue truth takes over. All real-world side effects are required to flow through that boundary — the CLI, the panel, and Vera are all submitters, not executors.

> Preview is authoritative only **before** submit.
> Queue lifecycle state is authoritative for submitted work progression.
> Artifacts/evidence are authoritative for what happened at runtime.

Canonical long-form docs:

- `docs/QUEUE_OBJECT_MODEL.md`
- `docs/QUEUE_CONSTITUTION.md`
- `docs/EXECUTION_SECURITY_MODEL.md`

## Queue directory layout

Hard-coded to `~/VoxeraOS/notes/queue` (`src/voxera/paths.py::queue_root`). The daemon owns:

```
inbox/                  submitted jobs waiting to be picked up
pending/                jobs currently planning/running/resuming
pending/approvals/      approval artifacts blocking a job
done/                   terminal success jobs
failed/                 terminal failed jobs (+ .error.json sidecars)
canceled/               terminal canceled jobs
recovery/               startup recovery quarantine
quarantine/             reconcile quarantine
_archive/               optional archive space
artifacts/<job>/        per-job runtime outputs
automations/            durable automation definition storage (PR1 foundation)
  definitions/          one JSON file per AutomationDefinition, id-based filename
  history/              reserved for a future runner; not written in PR1
.daemon.lock            single-writer lock
health.json             queue health snapshot
```

The daemon (`MissionQueueDaemon` in `core/queue_daemon.py`) holds the lock, drains `inbox/`, advances lifecycle states, writes artifacts and sidecars, and finalizes placement into a terminal bucket.

The `automations/` subtree is **owned by the automation object model layer** (`src/voxera/automation/`), not by the daemon. It stores durable definitions that describe *deferred or triggered queue submission*. A definition is not a second execution path — if a future runner ever acts on a saved definition, it must do so by emitting a normal canonical queue job into `inbox/`. The queue remains the execution boundary. PR1 contains only the model and storage helpers; no runner, scheduler, or submitter behavior exists yet.

## Canonical payload schema

From `core/queue_contracts.py`, normalized at intake. A submitted queue payload MAY carry additive fields, but the execution contract is grounded on these canonical fields:

- `mission_id`
- `goal`
- `title`
- `steps`
- `enqueue_child`
- `write_file`
- `file_organize`
- `approval_required`
- `_simple_intent`
- `lineage`
- `job_intent`

Normalization rules:

- `approval_required` is strict boolean — `true` only when explicitly `true`.
- `enqueue_child`, `write_file`, and `file_organize` are strict object contracts; unknown keys are rejected fail-closed.
- `lineage` values are sanitized; malformed lineage normalizes to safe defaults.
- Chat text is never execution truth. Only the submitted payload plus queue artifacts are authoritative.

### Request-kind derivation

Canonical request kinds: `mission_id`, `file_organize`, `goal`, `inline_steps`, `unknown`.

Derivation order:

1. `job_intent.request_kind`
2. payload `kind`
3. structural inference (`mission_id`/`mission`, `file_organize`, `goal`/`plan_goal`, `steps`)
4. fallback `unknown`

Aliases: `mission -> mission_id`, `steps -> inline_steps`. Unknown non-empty tokens normalize to `unknown`.

## Lifecycle state machine

From `core/queue_state.py` and the daemon loop:

Canonical lifecycle states:

- `queued`
- `planning`
- `running`
- `awaiting_approval`
- `resumed`
- `advisory_running`
- `done`
- `failed`
- `step_failed`
- `blocked`
- `canceled`

Invalid state values fail closed to `blocked`. `.state.json` is the queue-owned lifecycle truth sidecar for each job.

Canonical happy-path flow:

```
inbox(<job>.json)
  -> queued
  -> planning
  -> running [-> awaiting_approval -> resumed -> running ...]
  -> done | failed | step_failed | canceled
```

Approvals are not terminal outcomes. `awaiting_approval` and `pending_approval` are lifecycle states; they resolve to a real terminal outcome only after approve/deny/timeout through the approval pipeline.

## Terminal outcomes

Canonical terminal outcomes (`core/queue_contracts.py`, `core/queue_result_consumers.py`):

- `succeeded`
- `failed`
- `blocked`
- `denied`
- `canceled`

Interpretation:

- If approval was denied and no explicit terminal outcome is present, consumers normalize to `denied`.
- For governed mutating filesystem actions, `path_blocked_scope` normalizes to blocked semantics across step results, execution result, state sidecar, panel surfaces, and Vera linked outcome review.
- Missing minimum artifacts downgrade a declared success to an evidence-incomplete interpretation rather than silently promoting it.

## Lineage

Lineage is metadata-only observability (`core/queue_contracts.py::extract_lineage_metadata`, `compute_child_lineage`):

- `parent_job_id`
- `root_job_id`
- `orchestration_depth`
- `sequence_index`
- `lineage_role` (`root` or `child`)

Lineage **does not** bypass policy, approvals, or capability boundaries. Child jobs still run through normal queue governance.

## `enqueue_child`

Queue payloads support a single governed `enqueue_child` request. The daemon:

1. Validates the child payload as a normal queue intake.
2. Computes server-side lineage (parent id, root id, depth, sequence index, role).
3. Writes the child into `inbox/child-*.json`.
4. Records the linkage in `child_job_refs.json`, `actions.jsonl`, and the parent's `execution_result.json`.

There is exactly one `enqueue_child` per parent payload. Nested children go through the normal queue submit path — not through a multi-child embedded list.

## Artifacts produced per job

Artifacts live under `artifacts/<job>/` (`core/queue_execution.py`, `queue_contracts.py`). Always expected for queue execution:

- `plan.json`
- `actions.jsonl`
- `step_results.json`
- `execution_envelope.json`
- `execution_result.json`
- `job_intent.json`

Conditionally expected:

- `review_summary.json` — when the review materialization path runs.
- `evidence_bundle.json` — when evidence materialization runs.
- `stdout.txt` / `stderr.txt` — when the skill surface produced stream captures.
- `child_job_refs.json` — when the parent enqueued a child.
- `.state.json` — lifecycle sidecar, written next to the job file inside its bucket.
- `failed/<job>.error.json` — failure sidecar with cause/reason/next-action hint.

`execution_result.json` now carries an additive canonical contract:

- `artifact_families` — normalized produced artifact family names.
- `artifact_refs` — concrete `(artifact_family, artifact_path)` refs.
- `review_summary` — reviewer-facing "what happened" surface.
- `evidence_bundle` — normalized evidence/trace bundle (queue/step context → artifact refs → review summary).
- `resolve_structured_execution(...).normalized_outcome_class` — reviewer/operator classification that preserves canonical queue state as the source of lifecycle truth.

Expected-artifact contract: canonical queue/assistant lanes populate `expected_artifacts` at submission or during daemon normalization. Historical jobs without declarations are valid (`none_declared`) and are not backfilled.

## Approvals

Approvals are handled by `core/queue_approvals.py`. When a step requires approval (per its manifest/capability policy), the daemon:

1. Writes an approval artifact into `pending/approvals/`.
2. Transitions the job state to `awaiting_approval`.
3. Holds the job until the operator resolves it.

Resolution paths:

- **Approve** (CLI `voxera queue approvals approve`, panel `POST /queue/approvals/{ref}/approve`, `POST /queue/approvals/{ref}/approve-always`) — lifecycle transitions to `resumed` and continues runtime.
- **Deny** (CLI `voxera queue approvals deny`, panel `POST /queue/approvals/{ref}/deny`) — terminal outcome normalizes to `denied`.

`approve-always` is a short-lived session grant applied through the same governance pipeline — not a policy bypass.

## Startup recovery and shutdown

From `core/queue_recovery.py`:

- **Startup recovery** — on daemon start, any in-flight jobs still sitting in `pending/` are moved to `failed/` with a deterministic sidecar reason. Orphan approvals move into `recovery/startup-<ts>/`.
- **Graceful shutdown** — on SIGTERM the daemon writes a structured shutdown sidecar, drains the current in-flight job, releases the lock, and exits within `TimeoutStopSec`.
- **Reconcile** — `voxera queue reconcile` (report-only by default, `--fix` with `--yes` quarantine mode) surfaces stale files, orphaned approvals, broken lineage pointers, and corrupt state sidecars.

## Result consumers

`core/queue_result_consumers.py::resolve_structured_execution` is the single canonical result resolver. It prioritizes structured artifacts in this order:

1. `execution_result.json`
2. `step_results.json`
3. `.state.json`
4. approval / failure sidecars

Outcome classification is deterministic and fail-closed:

- pending approval → `approval_blocked`
- policy / capability denials → `policy_denied` / `capability_boundary_mismatch`
- missing required runtime evidence → `incomplete_evidence`
- runtime errors → `runtime_execution_failed` (or a dependency-specific class)

Consumers include the panel job detail pages, `voxera queue status`, `voxera queue health`, the ops bundle, and Vera's evidence-review flow.

## Relationship diagram

```
User / Vera / Panel / CLI (producers)
  -> preview object (pre-submit only)
  -> submit/handoff acknowledgement
  -> inbox/<job>.json
  -> queued -> planning -> running
                 |-> awaiting_approval -> approve/deny -> resumed
                 |-> step_failed -> failed
                 |-> canceled
  -> done | failed | canceled
  -> artifacts/<job>/* (runtime proof)
  -> evidence-grounded review (panel / Vera evidence_review / queue status)
```

## Boundary guarantees

These invariants are enforced by the code and asserted by tests (queue daemon contracts, security red-team, contract snapshots):

- Vera is conversational reasoning/drafting only. It is **not** the execution runtime.
- Queue submit is the canonical execution boundary.
- Real-world effects must not bypass queue, capability, or policy enforcement.
- When uncertain, the system fails closed.

## Pointers to the code

If you need to change queue behavior, start here:

| Concern | Module |
|---|---|
| Daemon loop, locking, directory contract | `src/voxera/core/queue_daemon.py` |
| Payload normalization, mission construction, planning handoff | `src/voxera/core/queue_execution.py` |
| Approval artifacts, approve/deny, grants | `src/voxera/core/queue_approvals.py` |
| Startup recovery + shutdown + quarantine | `src/voxera/core/queue_recovery.py` |
| Canonical contract shape, step results, execution result | `src/voxera/core/queue_contracts.py` |
| `.state.json` sidecar IO | `src/voxera/core/queue_state.py` |
| Bucket path + move helpers | `src/voxera/core/queue_paths.py` |
| `job_intent.json` enrichment | `src/voxera/core/queue_job_intent.py` |
| Result consumer / outcome classification | `src/voxera/core/queue_result_consumers.py` |
| Snapshot + lookup for panel/CLI | `src/voxera/core/queue_inspect.py` |
| Prune + retention | `src/voxera/core/queue_hygiene.py` |
| Reconcile + fix apply | `src/voxera/core/queue_reconcile.py` |
| Advisory assistant lane | `src/voxera/core/queue_assistant.py` |
