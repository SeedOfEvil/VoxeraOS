# Runtime Technical Overview

This document gives all model roles a concise mental model of VoxeraOS runtime structure.

## 1) User Entry Points
- **CLI (Typer)**: command families for queue, doctor, config, runtime, ops, skills/missions.
- **Web Panel (FastAPI)**: operator routes for queue/job control, assistant, missions, bundles, hygiene, and recovery.

Both entry points ultimately read from or write to the queue filesystem contract.

## 2) Queue Is the Execution Spine
Queue root buckets define lifecycle truth:
- `inbox/` for intake
- `pending/` for active work
- `done/`, `failed/`, `canceled/` for terminal outcomes
- `pending/approvals/` for gated work
- recovery/quarantine/archive paths for safe remediation and retention

A daemon-held file lock governs exclusive queue processing.

## 3) Daemon Composition (Execution Authority)
`MissionQueueDaemon` is the queue composition root and runtime execution authority.

It composes focused lifecycle modules:
- **QueueExecutionMixin**: payload normalization, planning handoff, mission execution, state transitions
- **QueueApprovalMixin**: approval prompts, artifact writes, approve/deny resolution, scoped grants
- **QueueRecoveryMixin**: startup orphan recovery/quarantine, shutdown finalization records
- **queue_assistant module**: assistant-question routing to advisory lanes with canonical artifacts

Supporting queue helpers also carry stable responsibilities:
- `queue_contracts.py` shapes canonical queue/execution objects
- `queue_result_consumers.py` normalizes structured execution for operator/Vera review surfaces
- `queue_state.py` owns lifecycle sidecar reads/writes
- `queue_paths.py` owns deterministic path movement helpers

## 4) Planning and Assistant Paths
- **Mission path**: payload -> planner -> mission runner -> policy-gated steps -> terminal state/artifacts
- **Assistant path**: advisory question jobs may use a fast read-only lane when eligible; otherwise normal lane

Both paths must emit canonical artifacts for post-run inspection.

## 4a) Vera conversational decomposition
Vera is no longer one large handoff file. Current boundaries:
- `vera/service.py` — conversation orchestration root
- `vera/session_store.py` — persisted session state
- `vera/preview_drafting.py` — deterministic preview drafting
- `vera/draft_revision.py` — active-preview follow-up interpretation
- `vera/preview_submission.py` — active-preview submit detection and queue handoff normalization
- `vera/investigation_flow.py` — explicit read-only web investigation lane
- `vera/investigation_derivations.py` — compare/summarize/expand follow-up shaping
- `vera/weather_flow.py` — quick weather lane and follow-up continuity
- `vera/saveable_artifacts.py` — recent meaningful assistant-content save targeting
- `vera/handoff.py` — compatibility façade across the extracted seams

### Session context and continuity
`vera/session_store.py` persists a bounded `shared_context` dict alongside turns, preview, handoff, and other session fields. It tracks workflow-continuity references (active draft, active preview, last submitted/completed/reviewed job, active topic, ambiguity flags). Context is updated at preview creation, submit/handoff, completion ingestion, job review, and session clear. It is subordinate to preview, queue, and artifact/evidence truth — if context conflicts with canonical truth, canonical truth wins.

### Session-scoped reference resolution
`vera/reference_resolver.py` provides a bounded, fail-closed reference-resolution layer that maps natural in-session phrases ("that draft", "the result", "the follow-up") to concrete referents using shared session context. Supported reference classes: draft, file, job/result, and continuation. Resolution respects a strict priority ordering per class and returns string ref values — callers validate against canonical truth downstream. Missing or ambiguous references always fail closed. The early-exit dispatch uses session context as a fallback for job review and follow-up flows when handoff state is unavailable.

## 5) Brain Layer (Reasoning Providers)
Brain adapters provide model generation but are not execution truth:
- base protocol (`generate`, `capability_test`)
- provider adapters (e.g., Gemini, OpenAI-compatible)
- fallback/error classification and JSON recovery

## 6) Policy and Skill Execution
Execution is capability-governed:
- policy maps capability to `allow` / `ask` / `deny`
- skill registry discovers manifests and loaders
- skill runner enforces policy and approval gates
- execution helpers handle sandboxing and audit sanitization
- argument normalization canonicalizes action inputs


Execution capability declarations should be explicit (side effects, filesystem/network scope, secrets, sandbox profile, expected artifacts), not ambient assumptions.

## 7) Cross-Cutting Operational Surfaces
- audit logs (JSONL)
- health snapshots/counters/backoff
- config loading + fingerprinting
- typed models
- path resolution
- secrets storage

Runtime/operator config and app/provider config are intentionally separate:
- `config.py:load_config()` -> runtime/operator settings (`config.json`, env, defaults)
- `config.py:load_app_config()` -> stricter app/provider config (`config.yml`)
- `paths.py` -> XDG helpers and default queue-root fallback

These surfaces support observability, diagnosis, and operational safety.

## 8) State and Truth Discipline
- **Preview truth**: current drafted intent
- **Queue truth**: accepted lifecycle state and execution contract
- **Artifact/evidence truth**: what runtime results actually prove

Models must not infer execution success from intent or plan quality alone.

## 9) Queue State Machine (Condensed)
Typical flow:
`queued -> planning -> running -> (awaiting_approval -> resumed|failed) -> done|failed|canceled`

Recovery and assistant-specific states may appear, but terminal truth still belongs to queue + artifacts.


See also: `docs/QUEUE_OBJECT_MODEL.md` for the canonical queue/job/artifact/evidence contract.
