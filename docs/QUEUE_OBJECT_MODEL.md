# Queue Object Model (Canonical)

This document is the canonical architecture contract for VoxeraOS queue execution objects.

## Scope and intent

After submit, a **queue job** is the canonical unit of execution truth.

- Preview is authoritative only **before** submit.
- Queue lifecycle state is authoritative for submitted work progression.
- Artifacts/evidence are authoritative for what happened at runtime.
- Planner output, intent text, and conversation are never runtime outcome truth by themselves.

## 1) Queue job object

A queue job is a submitted JSON payload accepted into `notes/queue/inbox/` and then moved through queue buckets by the daemon.

### Conceptual shape

A queue job includes:
- a stable filename/id reference (`<job>.json`)
- submitted payload fields (mission/goal/steps/assistant intent and metadata)
- queue-managed lifecycle state sidecar (`<job>.state.json`)
- queue-managed artifacts (`artifacts/<job>/...`)

### Where a job begins

- Producer surfaces (CLI/panel/handoff) submit work into `inbox/`.
- Queue acceptance into `inbox/` is the submit boundary where preview truth ends.

### Lifecycle buckets

- `inbox/` — newly submitted queue jobs
- `pending/` — active planning/running jobs
- `pending/approvals/` — approval artifacts for paused jobs
- `done/`, `failed/`, `canceled/` — terminal job files
- `recovery/`, `quarantine/`, `_archive/` — operational safety/remediation surfaces

### Stable identifiers and refs

- The canonical job identifier is the queue filename stem (`<job>` from `<job>.json`).
- Artifact paths and sidecars resolve by that stem.
- Optional lineage (`parent_job_id`, `root_job_id`, `orchestration_depth`, `sequence_index`) links related jobs without replacing per-job truth.

### Payloads vs sidecars

- Job payload = submitted requested work.
- State sidecar = queue-owned lifecycle snapshot and execution progress view.
- Artifacts = runtime outputs and review/evidence material.

## 2) Job lifecycle model

### Major states

- **draft / preview** (pre-submit only; not a queue state)
- `queued`
- `planning`
- `running`
- `awaiting_approval`
- `resumed`
- `advisory_running` (assistant advisory lane; emits canonical artifacts)
- `done`
- `failed` / `step_failed` / `blocked`
- `canceled`
- recovery/quarantine context (operational handling, not normal success flow)

Canonical shape:

`draft/preview -> queued -> planning -> running -> awaiting_approval -> resumed -> done|failed|canceled`

(With direct failure/cancel paths and assistant/recovery variants.)

### Truth boundary

- **Preview truth ends at handoff/submit acknowledgment.**
- **Queue truth begins once a job is accepted into queue buckets.**
- **Artifact/evidence truth becomes decisive for outcome review as execution progresses/completes.**

## 3) Artifact model

Artifacts are durable runtime outputs under `artifacts/<job>/` (plus approval artifacts under `pending/approvals/`).

Common artifact families:
- plan artifacts (`plan.json`, replan attempts)
- action/step results (`step_results.json`)
- execution summaries (`execution_result.json`, `execution_envelope.json`)
- stdout/stderr captures where available
- review summaries
- approval artifacts
- evidence bundles
- assistant/advisory artifacts

Artifact rules:
- Artifacts are not drafts.
- Artifacts are not speculative.
- Artifacts are runtime outputs or canonical review outputs of runtime data.

Canonical execution summary artifacts now standardize an additive contract:
- `execution_result.json.artifact_families` — normalized produced artifact family names.
- `execution_result.json.artifact_refs` — concrete produced artifact refs (`artifact_family`, `artifact_path`).
- `execution_result.json.review_summary` — reviewer-facing summary surface for "what happened".
- `execution_result.json.evidence_bundle` — normalized evidence/trace bundle linking job/step context to artifact refs and review summary.
- `resolve_structured_execution(...).normalized_outcome_class` — additive evidence-grounded reviewer/operator classification that preserves canonical queue state as source of lifecycle truth.

Forward-looking expectation contract: for supported canonical future lanes, queue creation/normalization should declare deterministic `expected_artifacts` intent up front (for example mission/goal/inline/write-file baseline artifacts, and assistant advisory response artifacts). Review remains evidence-grounded and may still report `partial`/`missing` when production does not satisfy declared expectations.

## 4) Evidence model

Evidence is the runtime-grounded material used to determine what actually happened.

Evidence is typically built from:
- queue lifecycle sidecars and terminal bucket placement
- execution artifacts (`execution_result`, `step_results`, approval records, lane metadata)
- error sidecars and recovery records
- concrete output artifacts produced by executed skills

`evidence_bundle.trace` is the canonical execution-to-evidence link surface for job/attempt/step context in outcome review.

Planner text, intent text, and conversation can provide context but are **not** execution proof.

## 5) Truth model

Use this hierarchy consistently:

1. **Conversational truth**: interaction aid only; never authoritative for runtime outcomes.
2. **Preview truth**: authoritative draft state before submit.
3. **Queue truth**: authoritative submitted lifecycle/progression state.
4. **Artifact/evidence truth**: authoritative post-execution outcome proof.

For verification/review, terminal queue state + runtime artifacts/evidence outrank all other surfaces.

## 6) Relationship model

```text
User intent
  -> Preview object (draft truth, pre-submit)
  -> Submit/handoff acknowledgment
  -> Queue job object (canonical submitted contract)
  -> Planning + action execution (governed runtime)
  -> Artifacts + evidence (runtime outputs/proof)
  -> Verifier/reviewer conclusion (must be evidence-grounded)
```

### Practical reviewer/verifier contract

- Plan quality is not proof of success.
- Intent confidence is not proof of success.
- Conversation summaries are not proof of success.
- “Succeeded” requires evidence-backed runtime completion.
- Reviews must cite queue state + artifacts/evidence, especially terminal outcome artifacts.

## 7) Stability expectations

This object model formalizes existing semantics and is intended to be stable as capabilities grow.

Changes to queue bucket semantics, approval gates, or terminal meaning should be treated as explicit architecture changes, not incidental implementation details.

- Vera session linkage (additive): Vera may maintain a session-local linked-job registry keyed by queue job ref. Registry consumption never overrides queue truth; it only ingests terminal lifecycle + evidence-derived normalized completion payloads for linked jobs.
- Shared session context (additive): Vera maintains a bounded `shared_context` object inside each session that tracks workflow-continuity references (active draft, active preview, last submitted/completed/reviewed job, last saved file, active topic, ambiguity flags). This context is a continuity aid subordinate to preview, queue, and artifact/evidence truth. If session context conflicts with canonical truth surfaces, canonical truth wins. If continuity is ambiguous, the system fails closed.
- Session-scoped reference resolution (additive): A bounded reference-resolution layer (`vera/reference_resolver.py`) maps natural in-session phrases to concrete referents from shared session context. Supported reference classes: draft, file, job/result, continuation. Resolution is conservative and fail-closed. The early-exit dispatch uses session context as a fallback for job review and follow-up flows.
