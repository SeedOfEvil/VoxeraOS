# Capability: Artifacts and Evidence

Artifacts and evidence are authoritative for what actually happened after execution.

## Runtime Evidence Surfaces
- queue lifecycle sidecars
- execution artifacts (`execution_result.json`, `step_results.json`, envelopes, plan artifacts)
- action logs and output artifacts
- stdout/stderr captures (where emitted)
- approval artifacts and deny/approval metadata
- review summaries and evidence bundles
- failed sidecars/recovery records when applicable

## Artifact Discipline
- Artifacts are not drafts.
- Artifacts are not speculative.
- Artifacts are runtime outputs or canonical reviews derived from runtime outputs.
- Completion follow-up text must be deterministic and evidence-grounded: use normalized completion payload fields (for example `latest_summary`, `failure_summary`, `next_action_hint`, compact highlights) and avoid speculative details or raw JSON dumps.

Execution review consumers should prefer normalized contract fields in
`execution_result.json` when present:
- `artifact_families`
- `artifact_refs`
- `review_summary` (including expected-vs-observed artifact comparison fields)
- `evidence_bundle` (including `trace` and expected artifact observation)

Reviewer/verifier “what happened?” shaping should:
- select latest grounded summary from normalized review/evidence blocks before legacy fallbacks,
- report explicit lifecycle and terminal fields,
- fail closed when evidence is thin or missing.


- Expected artifact families from capability declarations are part of reviewer context: compare expected vs produced evidence and report `observed|partial|missing` deterministically.
- Missing/partial artifact interpretation must remain lifecycle-aware: do not frame `awaiting_approval` gaps as runtime failures, and do not conflate `canceled` with `failed`.

## Verifier/Reviewer Grounding Contract
- Plans describe intent; they do not prove outcomes.
- Conversation can summarize; it does not establish execution truth.
- Verifier/reviewer conclusions must ground on queue lifecycle + artifacts/evidence.
- “Succeeded” requires evidence-backed runtime completion, not planner confidence.

- For canonical future job lanes, forward-declare deterministic expected artifacts at queue creation/normalization so review can compare declared intent vs observed evidence without guessing.

- Review summaries may include `capability_boundary_violation` when runtime behavior request exceeds declared boundaries; treat this as high-signal enforcement evidence (not a speculative claim).
- When available, use normalized non-success classes (`approval_blocked`, `policy_denied`, `capability_boundary_mismatch`, `path_blocked_scope`, `runtime_dependency_missing`, `runtime_execution_failed`, `canceled`, `partial_artifact_gap`, `incomplete_evidence`) to make explanations operationally specific.

- For bounded filesystem skills, prefer machine payload evidence (for example `entries`, `source_path`, `destination_path`, `bytes_copied`, `created`, `exists`, `size_bytes`, `deleted`) over speculative prose.
