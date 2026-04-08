# 09 — Core Objects and Schema Reference

This document is a short index of the data shapes and core objects that flow through VoxeraOS. It is grounded in `src/voxera/core/`, `src/voxera/models.py`, and the canonical contract docs under `docs/QUEUE_*.md` / `docs/EXECUTION_SECURITY_MODEL.md`. Use it as a cross-reference, not as a spec — the code is authoritative.

## Queue job payload (submit-time)

From `core/queue_contracts.py`. Submitted payloads are free to carry additive fields, but the execution contract is grounded on:

```jsonc
{
  "mission_id": "system_inspect",          // optional
  "goal": "...",                            // optional
  "title": "...",                           // optional
  "steps": [                                // optional
    { "skill_id": "...", "args": { ... } }
  ],
  "enqueue_child": { ... },                 // optional (strict object)
  "write_file": { ... },                    // optional (strict object)
  "file_organize": { ... },                 // optional (strict object)
  "approval_required": false,               // strict boolean
  "_simple_intent": { ... },                // routing hint
  "lineage": {                              // metadata-only observability
    "parent_job_id": "...",
    "root_job_id": "...",
    "orchestration_depth": 1,
    "sequence_index": 0,
    "lineage_role": "child"
  },
  "job_intent": {                           // enriched intent sidecar
    "request_kind": "goal",
    "goal": "...",
    "schema_version": 1
  }
}
```

Unknown keys inside the strict object contracts (`enqueue_child`, `write_file`, `file_organize`) are rejected fail-closed.

## `job_intent.json`

`core/queue_job_intent.py::enrich_queue_job_payload(...)` produces / enriches the `job_intent.json` sidecar.

Typical shape:

```jsonc
{
  "request_kind": "mission_id" | "goal" | "file_organize" | "inline_steps" | "unknown",
  "goal": "string",
  "title": "string",
  "mission_id": "string",
  "schema_version": 1,
  "source": "cli" | "panel" | "vera" | "inbox",
  "notes": "string"
}
```

Derivation order for `request_kind`:

1. `job_intent.request_kind`
2. payload `kind`
3. structural inference from top-level fields
4. fallback `unknown`

## `.state.json` lifecycle sidecar

`core/queue_state.py::read_job_state`, `write_job_state`, `update_job_state_snapshot`. Lives next to the job file in its current bucket.

Typical fields:

- `lifecycle_state` — one of: `queued`, `planning`, `running`, `awaiting_approval`, `resumed`, `advisory_running`, `done`, `failed`, `step_failed`, `blocked`, `canceled`. Invalid values normalize fail-closed to `blocked`.
- `current_step_index` — integer, advancing per step.
- `step_outcomes` — per-step terminal info (status, reason, policy decision).
- `approval_status` — `none`, `pending`, `approved`, `denied`.
- `terminal_outcome` — `succeeded`, `failed`, `blocked`, `denied`, `canceled` (when terminal).
- `summary` — compact human-readable summary.
- `updated_at_ms` — monotonic update timestamp.

The state sidecar is queue-owned; producers never write it.

## `step_results.json`

`core/queue_contracts.py::build_structured_step_results`. Canonical per-step structured results:

```jsonc
{
  "steps": [
    {
      "step_index": 0,
      "skill_id": "files.exists",
      "args": { "path": "..." },
      "capability": "files.read",
      "effect_class": "read",
      "status": "succeeded" | "failed" | "blocked" | "denied" | "canceled",
      "outcome_class": "...",
      "stdout": "...",
      "stderr": "...",
      "result": { ... },                 // skill_result.v1 payload
      "started_at_ms": 0,
      "finished_at_ms": 0
    }
  ],
  "schema_version": 1
}
```

## `execution_envelope.json` + `execution_result.json`

`core/queue_contracts.py::build_execution_result`, `refresh_execution_result_artifact_contract`.

The envelope carries the lifecycle + step execution metadata used by consumers during long-running jobs. The result is the terminal normalized surface, carrying:

- canonical terminal outcome
- normalized step results
- `artifact_families`
- `artifact_refs` (`[{artifact_family, artifact_path}, ...]`)
- `review_summary`
- `evidence_bundle` (including `trace`)
- `normalized_outcome_class` (via the resolver)
- `intent_route` evidence (for goal-kind jobs; see `simple_intent`)

Consumers always read via `core/queue_result_consumers.resolve_structured_execution(...)`. Never re-derive outcome independently.

## Lineage metadata

`core/queue_contracts.py::extract_lineage_metadata`, `compute_child_lineage`.

```jsonc
{
  "parent_job_id": "string",
  "root_job_id": "string",
  "orchestration_depth": 0,
  "sequence_index": 0,
  "lineage_role": "root" | "child"
}
```

Lineage is metadata-only. It does not bypass policy, approval, or capability boundaries. Child jobs are submitted through the normal queue intake and go through the normal governance pipeline.

## Capability semantics

`core/capability_semantics.py::manifest_capability_semantics(manifest)`.

Shape:

```jsonc
{
  "effect_class": "read" | "write" | "execute",
  "intent_class": "read_only" | "mutating" | "destructive",
  "resource_boundaries": {
    "filesystem": true|false,
    "network": true|false,
    "secrets": true|false,
    "system": true|false
  },
  "policy_mapping": {
    "<capability>": "<policy field>"
  }
}
```

`CAPABILITY_EFFECT_CLASS` (re-exported through `policy.py`) is the source of truth for the direct-CLI mutation gate classification.

## Skill manifest (`SkillManifest`)

Defined in `src/voxera/models.py` (Pydantic). Matches the `manifest.yml` schema described in `06_SKILLS_CAPABILITIES_AND_BUILTINS.md`. Fields include `id`, `name`, `description`, `entrypoint`, `capabilities`, `risk`, `exec_mode`, `needs_network`, `fs_scope`, `output_schema`, `output_artifacts`, `args`, and optional `network_scope`, `allowed_domains`, `allowed_paths`, `secret_refs`, `sandbox_profile`, `expected_artifacts`.

## `skill_result.v1`

`skills/result_contract.py::extract_skill_result`. Every built-in skill returns a payload that normalizes to:

```jsonc
{
  "schema": "skill_result.v1",
  "status": "succeeded" | "failed" | "blocked",
  "result": { ... },        // skill-specific structured result
  "summary": "string",
  "artifacts": [ ... ]      // optional file references
}
```

## `MissionTemplate` / `MissionStep`

`core/missions.py`:

```python
@dataclass(frozen=True)
class MissionStep:
    skill_id: str
    args: dict[str, Any]

@dataclass(frozen=True)
class MissionTemplate:
    id: str
    title: str
    goal: str
    steps: list[MissionStep]
    notes: str | None = None
```

## `PlanSimulation` / `PlanStep` / `RunResult`

From `src/voxera/models.py`. These are the mission runner simulation/run shapes used by dry-run and the queue mission expansion path.

- `PlanStep` carries `skill_id`, `args`, `capability`, `policy_decision`, and `approvals_required`.
- `PlanSimulation` aggregates steps plus mission-level capability summary.
- `RunResult` carries outcome + per-step detail for a realized mission run.

`MissionRunner.simulate(...)` in `core/missions.py` builds `PlanSimulation`; `MissionRunner.run(...)` realizes it to `RunResult`.

## `capabilities_snapshot`

`core/capabilities_snapshot.py::generate_capabilities_snapshot()` produces a deterministic snapshot of:

- discovered skills
- their declared capabilities
- normalized capability semantics
- generation timestamp (`generated_ts_ms`; scrubbed to 0 in deterministic dry-run)

Used by:

- `voxera ops capabilities`
- the panel capabilities surface
- mission planner dry-runs (can be frozen once per invocation via `--freeze-capabilities-snapshot`)

## `HiddenCompilerDecision`

`src/voxera/vera/service.py::HiddenCompilerDecision`. Bounded action shape Vera uses when the brain produces a preview-update decision:

```jsonc
{
  "action": "replace_preview" | "patch_preview" | "no_change",
  "intent_type": "new_intent" | "refinement" | "unclear",
  "updated_preview": { ... },   // only with replace_preview
  "patch": { ... }              // only with patch_preview
}
```

Any payload with extra keys or mismatched action/payload combinations is rejected fail-closed.

## `AutomationDefinition`

`src/voxera/automation/models.py::AutomationDefinition` (Pydantic, `extra="forbid"`). Durable, definition-only record that describes a *future* governed queue submission. PR1 only adds the data model and file-backed storage — there is no runner, no scheduler, no submitter yet. If a future runner acts on one of these, it must do so by emitting a normal canonical queue job into `inbox/`; the queue stays the execution boundary.

```jsonc
{
  "id": "demo-automation",                 // ^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$
  "title": "Demo automation",              // non-empty, stripped
  "description": "",                        // stripped; may be empty
  "enabled": true,                          // default true
  "trigger_kind": "once_at",               // one of the supported kinds below
  "trigger_config": { "run_at_ms": 0 },    // per-kind strict shape
  "payload_template": { "goal": "..." },   // canonical queue payload shape
  "created_at_ms": 0,                       // positive int epoch-ms
  "updated_at_ms": 0,                       // >= created_at_ms
  "last_run_at_ms": null,                   // positive int epoch-ms | null
  "next_run_at_ms": null,                   // positive int epoch-ms | null
  "last_job_ref": null,                     // non-empty string | null
  "run_history_refs": [],                   // list of non-empty strings
  "policy_posture": "standard",            // "standard" | "strict_review"
  "created_from": "cli"                    // "vera" | "panel" | "cli"
}
```

Supported `trigger_kind` values and their strict `trigger_config` shapes:

| Kind | Required shape | Notes |
|---|---|---|
| `once_at` | `{"run_at_ms": <positive int>}` | Single future epoch-ms target. |
| `delay` | `{"delay_ms": <positive int>}` | Relative delay in ms. |
| `recurring_interval` | `{"interval_ms": <positive int>}` | Fixed interval. |
| `recurring_cron` | `{"cron": <non-empty str>}` | String is accepted as-is at definition time; cron parsing is deferred to the future runner. |
| `watch_path` | `{"path": <non-empty str>, "event": "created"\|"modified"\|"deleted"}` | `event` defaults to `"created"` when omitted. |

`payload_template` must carry at least one canonical queue request field (`mission_id`, `goal`, `steps`, `file_organize`, or `write_file`). The `write_file` and `file_organize` shapes are validated via the same extractors the queue daemon uses at intake (`core/queue_contracts.py::extract_write_file_request` / `extract_file_organize_request`), so an automation definition that validates here would also survive queue intake verbatim. Unknown trigger kinds, unknown `trigger_config` keys, malformed or empty `payload_template`, non-int timestamps, `bool` / `float` leaking into int fields, and id shapes outside `AUTOMATION_ID_PATTERN` are all rejected fail-closed.

Storage lives under `<queue_root>/automations/definitions/<id>.json` via `src/voxera/automation/store.py`. Saves are atomic (`.json.tmp` → `Path.replace`), JSON is sorted for deterministic diffs, and `list_automation_definitions(...)` is best-effort by default so a single malformed file cannot hide the rest of the inventory (`strict=True` surfaces every failure for tooling). A sibling `history/` directory is created now but not written in PR1 — it is reserved for a future runner.

## `job` refs, stems, and ids

- Canonical job id is the queue filename stem (`<job>.json` → `<job>`).
- Artifacts and sidecars resolve by that stem.
- Lineage links jobs without replacing per-job truth.

When referring to a job externally (CLI, panel URL, Vera chat), the stem is the stable reference. `core/queue_inspect.py::lookup_job(...)` resolves the stem back to the current bucket.
