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

## Verifier/Reviewer Grounding Contract
- Plans describe intent; they do not prove outcomes.
- Conversation can summarize; it does not establish execution truth.
- Verifier/reviewer conclusions must ground on queue lifecycle + artifacts/evidence.
- “Succeeded” requires evidence-backed runtime completion, not planner confidence.
