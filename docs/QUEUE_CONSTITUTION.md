# Queue Constitution (VoxeraOS 0.1.8 alpha)

This document freezes queue contracts for submitted jobs. It is the canonical reference for queue payload shape, lifecycle semantics, artifact guarantees, and result interpretation.


## 0) Canonical capability semantics

Capability meaning is centralized in `src/voxera/core/capability_semantics.py` and projected per manifest via `manifest_capability_semantics(...)`.

The normalized contract includes:
- effect class (`read|write|execute`)
- intent class (`read_only|mutating|destructive`)
- resource boundaries (`filesystem|network|secrets|system`)
- policy field mapping when capability is approval-governed

Queue approval/result surfaces should derive semantics from this model, not from skill-name heuristics.

## 1) Canonical payload schema (submit-time)

Queue payloads MAY contain extra additive fields, but the execution contract is grounded on these canonical fields:

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
- `approval_required` is interpreted as strict boolean (`true` only when explicitly `true`).
- `enqueue_child`, `write_file`, and `file_organize` are strict object contracts; unknown keys are rejected (fail closed).
- `lineage` values are sanitized; malformed lineage values are normalized to safe `null`/default values.
- Chat text is never execution truth; only submitted queue payload + queue artifacts are authoritative.

## 2) Request-kind derivation rules

Canonical request kinds:
- `mission_id`
- `file_organize`
- `goal`
- `inline_steps`
- `unknown`

Derivation order:
1. `job_intent.request_kind`
2. payload `kind`
3. structural inference (`mission_id`/`mission`, `file_organize`, `goal`/`plan_goal`, `steps`)
4. fallback `unknown`

Alias normalization:
- `mission` -> `mission_id`
- `steps` -> `inline_steps`

Unknown non-empty request-kind tokens normalize to `unknown` (fail-closed contract clarity).

## 3) Lifecycle model

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

`.state.json` is queue-owned lifecycle truth for submitted jobs.

Invalid lifecycle values are normalized fail-closed to `blocked` in state snapshots.

## 4) Terminal outcomes

Canonical terminal outcomes:
- `succeeded`
- `failed`
- `blocked`
- `denied`
- `canceled`

Interpretation notes:
- `awaiting_approval` and `pending_approval` are lifecycle states, not terminal outcomes.
- If approval status is denied and no explicit terminal outcome is present, consumers normalize to `denied`.
- For governed mutating filesystem actions, `path_blocked_scope` is a blocked boundary class (fail-closed) and should normalize as blocked semantics across `step_results`, `execution_result`, state sidecars, panel surfaces, and Vera linked outcome review.

## 5) Lineage semantics

Lineage is metadata-only observability:
- `parent_job_id`
- `root_job_id`
- `orchestration_depth`
- `sequence_index`
- `lineage_role` (`root|child`)

Lineage MUST NOT bypass policy/approval/capability boundaries. Child jobs still run through normal queue governance.

## 6) Artifact minimum guarantees

Per-job artifacts are expected as follows:

Always expected for queue execution:
- `execution_envelope.json`
- `execution_result.json`
- `step_results.json`
- `job_intent.json`
- `plan.json`
- `actions.jsonl`

Conditionally expected:
- `review_summary.json` where review materialization exists
- `evidence_bundle.json` where evidence materialization exists
- `.state.json` sidecars in queue buckets
- failure sidecars (`failed/<job>.error.json`) for failed jobs

`review_summary.minimum_artifacts` captures observed vs missing baseline artifact contract.

## 7) Result interpretation rules (operator-safe)

Result consumers prioritize structured artifacts in this order:
1. `execution_result.json`
2. `step_results.json`
3. `.state.json`
4. approval/failure sidecars

Outcome classification is deterministic and fail-closed:
- pending approval -> `approval_blocked`
- policy/capability denials -> `policy_denied` / `capability_boundary_mismatch`
- missing required runtime evidence -> `incomplete_evidence`
- runtime errors -> `runtime_execution_failed` (or dependency-specific class)

For successful terminal outcomes, missing minimum artifacts degrades interpretation to evidence-incomplete.

## 8) Boundary guarantees

- Vera is conversational reasoning/drafting only.
- Queue submit is the canonical execution boundary.
- Real-world effects must not bypass queue/capability/policy enforcement.
- When uncertain, the system fails closed.
