# 07 — Artifacts, Evidence, and Review Model

VoxeraOS treats canonical artifacts as the authoritative post-execution truth of a queue job. This document describes what gets written, where, and how consumers interpret it.

Canonical contract source: `src/voxera/core/queue_contracts.py`, `src/voxera/core/queue_execution.py`, `src/voxera/core/queue_result_consumers.py`, and `docs/QUEUE_CONSTITUTION.md`.

## Where artifacts live

All runtime outputs for a queue job `<job>` land under:

```
~/VoxeraOS/notes/queue/
├── <bucket>/<job>.json           # the job payload (inbox/pending/done/failed/canceled)
├── <bucket>/<job>.state.json     # lifecycle sidecar (queue-owned)
├── artifacts/<job>/              # per-job runtime outputs
├── failed/<job>.error.json       # failure sidecar (failed bucket only)
└── pending/approvals/            # approval artifacts (while paused)
```

`artifacts/<job>/` is the canonical evidence directory. Everything needed to answer "what happened on this job" lives there.

## Always-expected artifacts

From `core/queue_contracts.py` and the constitution, every executed queue job should produce:

| Artifact | Purpose |
|---|---|
| `plan.json` | The plan the runtime actually executed (not the planner draft) |
| `actions.jsonl` | Append-only action log (per-step start/end, policy decisions, transitions) |
| `step_results.json` | Structured per-step results: skill id, args, capability, status, outputs |
| `execution_envelope.json` | Lifecycle / step envelope used by consumers and the panel |
| `execution_result.json` | Normalized terminal execution result (source of truth for outcome) |
| `job_intent.json` | Enriched job intent (request kind, intent text, derivation path) |

`stdout.txt` / `stderr.txt` are added when the skill surface captured stream output.

## Conditionally expected artifacts

| Artifact | When produced |
|---|---|
| `review_summary.json` | When the review materialization path runs (Vera review, panel job detail, ops bundles) |
| `evidence_bundle.json` | When evidence materialization runs — normalized trace linking queue/step context to artifact refs and review summary |
| `child_job_refs.json` | When the parent payload contained `enqueue_child` |
| `expected_artifacts` declaration | For forward-created jobs; canonical queue/assistant lanes populate this during submission or daemon normalization |
| `failed/<job>.error.json` | Failed bucket only — cause, reason, and next-action hint |

Historical jobs without `expected_artifacts` declarations remain valid. They are classified as `none_declared` and are not backfilled.

## `execution_result.json` contract

`execution_result.json` is the normalized terminal execution surface. Beyond the basic status/outcome fields it now carries an additive canonical contract:

- **`artifact_families`** — normalized family names for everything produced at runtime.
- **`artifact_refs`** — concrete `(artifact_family, artifact_path)` references.
- **`review_summary`** — reviewer-facing "what happened" summary, including execution-capability declaration visibility and `observed | partial | missing` status for expected artifacts.
- **`evidence_bundle`** — normalized evidence/trace bundle linking job/attempt/step context to artifact refs and review summary.
- **`normalized_outcome_class`** — reviewer/operator outcome classification (via `resolve_structured_execution(...)`), preserving the canonical queue state as the source of lifecycle truth.

Consumers (panel, CLI, Vera review) always read through `core/queue_result_consumers.py::resolve_structured_execution` — they never re-derive outcome independently.

## Outcome classification (deterministic, fail-closed)

From `core/queue_result_consumers.py`:

| Class | Meaning |
|---|---|
| `succeeded` | Terminal success with coherent evidence |
| `failed` | Terminal failure; evidence carries the cause |
| `blocked` | Boundary/scope/path violation |
| `denied` | Policy/capability denial (including denied approvals) |
| `canceled` | Operator-canceled |
| `approval_blocked` | Waiting on approval (lifecycle state, not terminal) |
| `policy_denied` | Explicit policy deny |
| `capability_boundary_mismatch` | Runtime capability request violated declaration |
| `incomplete_evidence` | Terminal but missing minimum artifacts |
| `runtime_execution_failed` | Runtime error during execution |

Missing minimum artifacts degrade a claimed success to `incomplete_evidence` rather than silently promoting the outcome.

## Result priority order

Result consumers prioritize structured artifacts in this order (from `core/queue_result_consumers.py`):

1. `execution_result.json`
2. `step_results.json`
3. `.state.json`
4. approval / failure sidecars

If a higher-priority source is missing, the next is used. If none of them are coherent, the job is reported as `incomplete_evidence`.

## Evidence model

From `docs/QUEUE_OBJECT_MODEL.md`:

> Evidence is the runtime-grounded material used to determine what actually happened.

Evidence is built from:

- queue lifecycle sidecars (`<job>.state.json`)
- terminal bucket placement
- execution artifacts (`execution_result.json`, `step_results.json`, approval records)
- failure sidecars (`failed/<job>.error.json`) and recovery records
- concrete output artifacts produced by executed skills

Planner text, intent text, and conversation can provide context but are **not** execution proof.

`evidence_bundle.trace` is the canonical execution-to-evidence link surface for job / attempt / step context.

## Truth hierarchy

From the canonical queue object model:

1. **Conversational truth** — interaction aid only; never authoritative.
2. **Preview truth** — authoritative draft state before submit.
3. **Queue truth** — authoritative submitted lifecycle/progression state.
4. **Artifact / evidence truth** — authoritative post-execution outcome proof.

For verification and review, terminal queue state + runtime artifacts / evidence outrank all other surfaces.

## Review surfaces

### Panel

- `GET /jobs/{id}` renders `artifacts/<job>/` through `panel/job_detail_sections.py` and `panel/job_presentation.py`.
- `GET /jobs/{id}/progress` returns live structured progress (lifecycle, step progress, approval status) from the same resolver.
- `GET /jobs/{id}/bundle` builds a per-job ops bundle via `core/queue_inspect.py` + `incident_bundle.py`.
- `GET /bundle/system` exports a full-system ops bundle.

### CLI

- `voxera queue status` — compact per-bucket status, uses the resolver.
- `voxera queue health` — canonical health snapshot (`--json`, `--watch`, `--interval`).
- `voxera ops bundle system` / `voxera ops bundle job <ref>` — deterministic incident bundles (`cli_ops.py`).

### Vera

- `vera/evidence_review.py` reads the same canonical resolver and renders a compact evidence-grounded answer. When the last job is ambiguous, it refuses to summarize rather than fabricate.
- `vera/linked_completions.py` ingests terminal lifecycle + normalized completion payloads for linked jobs and surfaces them into the session.

## Audit log

`src/voxera/audit.py` writes a JSONL audit stream. Every lifecycle transition, policy decision, approval resolution, and skill invocation emits an audit event. The audit file lives under the data dir and is the backing store for `voxera audit` and `voxera queue status` historical counts.

## Ops bundles

`src/voxera/ops_bundle.py` and `src/voxera/incident_bundle.py` build:

- **System bundle** — the queue snapshot, health, recent audit entries, capabilities snapshot, config snapshot, and (optionally) recent job artifacts, all rolled into a deterministic archive.
- **Job bundle** — a specific job's state sidecar, artifacts directory, and relevant audit slices.

Use case: exporting a reproducible state for a review, a bug report, or a post-incident handoff.

## Review rules (from the constitution)

- Plan quality is not proof of success.
- Intent confidence is not proof of success.
- Conversation summaries are not proof of success.
- "Succeeded" requires evidence-backed runtime completion.
- Reviews must cite queue state + artifacts / evidence, especially terminal outcome artifacts.

## What gets pruned

- `voxera queue prune` — prunes terminal buckets (dry-run by default; `--yes` to delete).
- `voxera artifacts prune` — prunes `artifacts/<job>/` directories older than configured retention.
- Retention bounds come from `VOXERA_ARTIFACTS_RETENTION_DAYS`, `VOXERA_ARTIFACTS_RETENTION_MAX_COUNT`, `VOXERA_QUEUE_PRUNE_MAX_AGE_DAYS`, `VOXERA_QUEUE_PRUNE_MAX_COUNT`.
- Failed bucket retention: `VOXERA_QUEUE_FAILED_MAX_AGE_S`, `VOXERA_QUEUE_FAILED_MAX_COUNT`.

Canonical artifacts for active/done jobs are not speculative and are not pruned until retention thresholds are reached. This preserves the invariant that a just-completed job's evidence is always inspectable.
