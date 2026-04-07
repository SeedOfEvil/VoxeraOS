# 07 — Artifacts, Evidence and Review Model

This document records the runtime artifact and evidence model. The
canonical vocabulary lives in `core/queue_object_model.py`
(`ARTIFACT_FAMILIES`) and `core/queue_contracts.py` (schema versions and
minimum required artifact set). Concrete normalization happens in
`core/queue_result_consumers.py`.

## Artifact families

`core/queue_object_model.ArtifactFamily` enumerates:

- `plan`
- `actions`
- `stdout`
- `stderr`
- `review_summary`
- `approval`
- `evidence_bundle`
- `execution_envelope`
- `execution_result`
- `step_results`
- `assistant_advisory`
- `job_intent`

## Minimum artifact set

`queue_contracts.py` declares the minimum artifacts required for any
executed queue job:

- `execution_envelope.json`
- `execution_result.json`
- `step_results.json`
- `job_intent.json`
- `plan.json`
- `actions.jsonl`

`tests/test_queue_artifact_minimum_regression.py` enforces this contract
against the live runner.

## Schema versions

Each artifact family pinned in `queue_contracts.py` carries an additive
schema version:

| Artifact | Schema version constant |
| -------- | ----------------------- |
| `execution_envelope.json` | `EXECUTION_ENVELOPE = 1` |
| `step_results.json` | `STEP_RESULT = 1` |
| `execution_result.json` | `EXECUTION_RESULT = 1` |
| `evidence_bundle.json` | `EVIDENCE_BUNDLE = 1` |
| `review_summary.json` | `REVIEW_SUMMARY = 1` |

## On-disk artifact layout

```
notes/queue/artifacts/<job>/
├── plan.json
├── actions.jsonl
├── step_results.json
├── execution_envelope.json
├── execution_result.json
├── evidence_bundle.json
├── review_summary.json
├── job_intent.json
├── stdout.txt          (where the runner captured stdout)
├── stderr.txt          (where the runner captured stderr)
└── ...                 (skill-declared output artifacts)
```

`pending/approvals/<job>.json` is the approval artifact while a job is in
`awaiting_approval`.

`assistant_advisory` artifacts are emitted by the
`core/queue_assistant.py` advisory lane in addition to the canonical
execution_envelope/execution_result pair, so even fast-lane assistant
jobs leave evidence behind.

## `execution_result.json`

The canonical post-execution artifact carries (additive) the following
contract surfaces:

- `artifact_families` — normalized produced artifact family names.
- `artifact_refs` — `[{artifact_family, artifact_path}, ...]`.
- `review_summary` — reviewer-facing "what happened" surface.
- `evidence_bundle` — normalized evidence/trace bundle linking job/step
  context to artifact refs and review summary.

## Evidence bundle

`evidence_bundle.json` contains a `trace` object that joins job, attempt,
and step context to the produced artifact refs and the review summary.
This is the canonical input to all reviewer surfaces.

## Resolving structured execution

`core/queue_result_consumers.resolve_structured_execution(...)` is the
single function callers should use to translate a queue job's bucket
placement, state sidecar, error sidecar, and artifact directory into a
single normalized payload. Highlights:

- `_normalize_terminal_outcome(...)` maps lifecycle + approval state into
  a `TerminalOutcome` value (`succeeded`, `failed`, `blocked`, `denied`,
  `canceled`).
- `_normalize_lineage(...)` produces a sanitized lineage view
  (`parent_job_id`, `root_job_id`, `orchestration_depth`,
  `sequence_index`).
- `_classify_outcome(...)` returns `normalized_outcome_class`
  (`read_only_success`, `mutating_success`, `approval_blocked`,
  `failed`, etc.) — additive evidence-grounded reviewer/operator
  classification that never overrides canonical lifecycle truth.
- `_classify_child_state(...)` and `_resolve_child_summary(...)` handle
  orchestrated child jobs (`enqueue_child`).

This function is consumed by:

- The panel job detail page (`routes_jobs.py`,
  `job_presentation.py`, `job_detail_sections.py`).
- Vera linked completion ingestion (`vera/linked_completions.py`).
- The CLI (`cli_queue.py` and `cli_queue_payloads.py`).

## Artifact pruning

`core/artifacts.prune_artifacts(...)` is the bounded artifact pruner.
It is invoked by `voxera artifacts prune` and `voxera queue prune`. It
honors `VOXERA_ARTIFACTS_RETENTION_DAYS` and
`VOXERA_ARTIFACTS_RETENTION_MAX_COUNT`. The function only operates on
files inside the resolved queue artifact root and refuses unsafe paths
via `_is_safe(...)`.

## Reviewer contract

The reviewer / verifier contract (enforced by tests, contracts, and the
docs in `docs/QUEUE_OBJECT_MODEL.md`):

- Plan quality is not proof of success.
- Intent confidence is not proof of success.
- Conversation summaries are not proof of success.
- "Succeeded" requires evidence-backed runtime completion.
- Reviews must cite queue lifecycle state plus the canonical artifacts
  (especially `execution_result.json`, `evidence_bundle.json`, and the
  terminal bucket placement).

## Truth surface hierarchy (recap)

| Rank | Surface | Authority |
| ---- | ------- | --------- |
| 1 | `artifact_evidence` | Authoritative post-execution proof. |
| 2 | `queue` | Authoritative lifecycle/progression state. |
| 3 | `preview` | Authoritative draft state pre-submit. |
| 4 | `conversation` | Interaction aid only; never authoritative. |

When two surfaces disagree, the higher-rank one wins. When ambiguous,
the system fails closed (no advancement, no auto-approval, deterministic
refusal).
