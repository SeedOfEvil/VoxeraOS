# 03 — Queue Object Model and Lifecycle

This document describes the canonical queue object as observed in
`src/voxera/core/`. The vocabulary in this file mirrors the constants in
`core/queue_object_model.py` (the canonical source of truth) and the
contracts in `core/queue_contracts.py`.

## Queue object

A queue job is a single submitted JSON payload that the daemon moves through
buckets on disk. After submit, the queue job is the canonical unit of
runtime truth.

Each job is identified by a stable filename stem (`<job>.json`). Sidecars
and artifact directories resolve by that stem.

### Pieces of a queue job

- **Payload file**: `inbox/<job>.json` then moved between bucket directories.
- **State sidecar**: `<job>.state.json` next to the payload file in whichever
  bucket the daemon currently has it placed.
- **Error sidecar**: `<job>.error.json` (failures only).
- **Artifact directory**: `artifacts/<job>/` with the artifact families
  enumerated in `queue_object_model.ARTIFACT_FAMILIES`.
- **Approval payload**: `pending/approvals/<job>.json` while waiting for
  human approval.

## Lifecycle states

`core/queue_object_model.QueueLifecycleState` lists the canonical states:

- `queued`
- `planning`
- `running`
- `awaiting_approval`
- `resumed`
- `advisory_running` — assistant advisory lane (still emits canonical artifacts)
- `done`
- `failed`
- `step_failed`
- `blocked`
- `canceled`

`COMPLETED_AT_LIFECYCLE_STATES` (terminal): `done`, `failed`, `step_failed`,
`blocked`, `canceled`.

### Terminal outcomes (separate from lifecycle state)

`TerminalOutcome`: `succeeded`, `failed`, `blocked`, `denied`, `canceled`.

### Canonical flow

```
preview (pre-submit, not a queue state)
   |
   v
inbox -> queued -> planning -> running -> { awaiting_approval -> resumed -> running }
                                           -> done | failed | step_failed | blocked | canceled
```

`advisory_running` is a parallel lane used by `core/queue_assistant.py` for
read-only assistant advisory jobs that still emit canonical artifacts.

## Truth surfaces

`core/queue_object_model.TRUTH_SURFACES`:

| Surface | Authority |
| ------- | --------- |
| `conversation` | Interaction aid only; never authoritative for runtime outcomes. |
| `preview` | Authoritative draft state before submit. |
| `queue` | Authoritative submitted lifecycle/progression state. |
| `artifact_evidence` | Authoritative runtime-grounded post-execution outcome proof. |

## On-disk layout

Default queue root: `~/VoxeraOS/notes/queue/` (see `paths.queue_root()`).

```
notes/queue/
├── inbox/                      newly enqueued jobs awaiting planning
├── pending/                    active planning/running jobs
│   └── approvals/              approval payloads for awaiting_approval jobs
├── done/                       terminal successful jobs
├── failed/                     terminal failed jobs (subject to hygiene pruning)
├── canceled/                   terminal canceled jobs
├── artifacts/                  durable per-job artifact directories
│   ├── <job>/                  per-job artifact bundle
│   └── vera_sessions/          Vera session JSON store
├── _archive/                   archive/offload bucket (recovery partner)
└── .daemon.lock                singleton daemon lock
```

`core/queue_paths.py` provides `move_job_with_sidecar()` and
`deterministic_target_path()` to keep payload + state sidecar moves
atomic between buckets.

## Canonical payload schema (submit time)

`core/queue_contracts.py` defines the canonical fields a submitted payload
may carry:

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

- `approval_required` is a strict boolean (only literal `true` flips it).
- `enqueue_child`, `write_file`, `file_organize` are strict object contracts;
  unknown keys are rejected (fail closed).
- `lineage` is sanitized; malformed lineage values become safe defaults.
- Chat text and conversation never act as execution truth.

`request_kind` is derived from the payload via the rules in
`queue_contracts.py`: `mission_id`, `file_organize`, `goal`, `inline_steps`,
`unknown`.

## Schema versions

`queue_contracts.py` pins additive schema versions for the canonical
artifact families:

- `EXECUTION_ENVELOPE = 1`
- `STEP_RESULT = 1`
- `EXECUTION_RESULT = 1`
- `EVIDENCE_BUNDLE = 1`
- `REVIEW_SUMMARY = 1`

The minimum required artifact set per executed job is enforced by
`test_queue_artifact_minimum_regression.py` and consumes the constants in
`queue_contracts.py`.

## Queue daemon

`core/queue_daemon.py` defines `MissionQueueDaemon`, composed via mixins:

- `QueueApprovalMixin` (`core/queue_approvals.py`) — `awaiting_approval`
  transitions, approve/deny/approve-always handling, approval payload
  storage in `pending/approvals/`.
- `QueueRecoveryMixin` (`core/queue_recovery.py`) — clean-shutdown record,
  startup recovery for jobs left running by an unclean shutdown
  (deterministically marked failed with a recorded reason).
- `QueueExecutionMixin` (`core/queue_execution.py`) — actual planning and
  step execution, delegating to the skill runner.

The daemon enforces a singleton via `notes/queue/.daemon.lock` (see
`QueueLockError` and `voxera queue lock status`).

Auto-approval (`--auto-approve-ask`) is dev-only and limited to allowlisted
ASK capabilities.

### Hygiene

`core/queue_hygiene.py`:

- Operates only on terminal buckets (`done`, `failed`, `canceled`).
- Never touches `inbox/` or `pending/`.
- Tracks each `JobEntry` plus its sidecars (`.state.json`, `.error.json`).
- Retention bounds: max age (default ~30 days) and max count.

### Reconcile

`core/queue_reconcile.py` repairs drift between bucket placement and state
sidecars (e.g. orphaned approvals, mismatched terminal placement). It is
exposed via `voxera queue reconcile` and `POST /hygiene/reconcile` in the
panel.

### Recovery

`core/queue_recovery.py` and `core/queue_inspect.py` provide:

- Clean-shutdown reason recording.
- Startup recovery marking interrupted `running` jobs as `failed` with the
  startup-recovery reason.
- Inspect helpers used by `voxera queue health` and the panel recovery page.

## Inbox path

`core/inbox.py` is the small contract used by Vera handoff and CLI inbox
commands to drop a payload file into `inbox/` atomically. The job ID is
returned by stripping the filename stem; this is the same identifier used
later as the queue ref.

## Result consumers

`core/queue_result_consumers.py` standardizes how callers translate the
mixed bucket placement, state sidecar, and `execution_result.json`
artifacts into a single `resolve_structured_execution(...)` payload that
includes:

- Canonical lifecycle state and bucket placement.
- Normalized artifact families and refs.
- A `normalized_outcome_class` value (read_only_success, mutating_success,
  approval_blocked, failed, etc.).
- `evidence_bundle.trace` join across attempts and steps.

This is the function Vera calls (via `vera/linked_completions.py`) to
ingest terminal job state without re-implementing queue truth on the chat
side.

## Queue assistant lane

`core/queue_assistant.py` accepts strictly read-only advisory requests
(`advisory=true`, `read_only=true`, `action_hints=["assistant.advisory"]`,
no approval gates, no steps/missions/goals) and runs them on a fast lane
that still emits canonical artifacts (`assistant_advisory`,
`execution_envelope`, `execution_result`). Brain calls go through
`brain/` with explicit fallback reasons (`TIMEOUT`, `AUTH`, `RATE_LIMIT`,
`MALFORMED`, `NETWORK`).

## Capability semantics

`core/capability_semantics.py` is the centralized capability metadata model
referenced by queue contracts and policy. Each capability declares:

- `effect_class` (`read | write | execute`)
- `intent_class` (`read_only | mutating | destructive`)
- `policy_field` (when approval-governed)
- `resource_boundaries` (`filesystem | network | secrets | system`)
- short operator-facing summary

`manifest_capability_semantics(...)` projects this metadata onto a skill
manifest so policy and approval surfaces share one vocabulary instead of
inferring meaning from skill names.

## Reviewer contract

The reviewer contract enforced by tests (and explicitly by
`docs/QUEUE_OBJECT_MODEL.md`) is:

- Plan quality is not proof of success.
- Intent confidence is not proof of success.
- Conversation summaries are not proof of success.
- “Succeeded” requires evidence-backed runtime completion.
- Reviews must cite queue state plus artifacts/evidence, especially the
  terminal outcome artifacts.
