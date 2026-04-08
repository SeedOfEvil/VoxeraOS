# 04 — Goal, Mission, Planning, and Execution

This document describes how VoxeraOS turns submitted intent into executable work. It covers the four canonical request kinds, the built-in mission catalog, the planner path, the simple-intent guardrail, and how execution is driven from the queue.

## The four canonical request kinds

From `src/voxera/core/queue_contracts.py` and `core/queue_execution.py`. A queue payload resolves to exactly one request kind:

1. **`mission_id`** — run a pre-defined mission template.
2. **`file_organize`** — expand a structured file workflow contract into a bounded mission.
3. **`goal`** — natural-language goal routed through deterministic helpers first, cloud planner second.
4. **`inline_steps`** — explicit pre-composed step list, executed directly.

If none of the above match, the request kind is `unknown` and the job fails closed before execution.

## Built-in missions

Built-in mission templates live in `src/voxera/core/missions.py::MISSION_TEMPLATES`. Current catalog (9 built-ins; see `mission_inventory.json` for the full mechanical listing):

| `mission_id` | Title | Purpose |
|---|---|---|
| `work_mode` | Start Work Mode | Open core work apps and set baseline volume |
| `focus_mode` | Focus Mode | Reduce distractions, keep only essentials active |
| `daily_checkin` | Daily Check-in | Open status surfaces and prefill daily notes |
| `incident_mode` | Incident Mode | Bring up troubleshooting tools quickly |
| `wrap_up` | Wrap Up | End-of-day summary and lower volume |
| `notes_archive_flow` | Notes Archive Flow | Archive an inbox note via bounded notes-scope file skills |
| `system_check` | System Check | Baseline status + write a small report |
| `system_inspect` | System Inspection | Bounded read-only workstation snapshot (status, disk, processes, windows) |
| `system_diagnostics` | System Diagnostics | Bounded read-only diagnostics (host info, memory, load, disk, processes) |

`notes_archive_flow` uses the bounded notes-scope file skills end-to-end and includes a `files.delete_file` step which requires approval policy. `system_inspect` and `system_diagnostics` are strictly read-only, no mutations, no network, no approval — designed for deterministic queue-backed diagnostic evidence.

## File-based missions

In addition to the in-code catalog, `core/missions.py::_mission_search_dirs` loads missions from:

- `<repo>/missions/` — file-based missions committed to the repo
- `~/.config/voxera/missions/` — user-level missions

Currently in-repo:

- `missions/sandbox_net.json`
- `missions/sandbox_smoke.json`

Mission file format: `json`, `yaml`, or `yml`. Fields: `id`, `title`, `goal`, `steps[]` (`skill_id` + `args`), `notes`. Unknown skills, missing ids, or malformed step lists fail validation.

In-code templates take precedence when ids collide. `list_missions()` returns the merged catalog; `list_missions_best_effort()` skips malformed files with an audit event instead of raising.

## Running missions

Three paths, all of them converge on the queue:

1. **CLI direct (in-process):**
   ```
   voxera missions list
   voxera missions run <mission_id> [--dry-run]
   voxera missions plan "<goal>" [--dry-run --deterministic ...]
   ```
   Implemented in `cli_skills_missions.py`. Direct `missions run` uses the local `MissionRunner` (`core/missions.py`). Mutating missions still hit the direct-mutation gate unless they are read-only.
2. **Queue by `mission_id`:** drop a JSON payload with `{"mission_id": "<id>"}` into `inbox/`. The daemon expands it into a mission and runs it through the normal queue lifecycle. This is the recommended way to test missions end-to-end.
3. **Panel / Vera:** the panel has `POST /missions/create` / `POST /missions/templates/create` surfaces. Vera drafts a preview and hands off through its own submit path (see `05_VERA_CONTROL_LAYER_AND_HANDOFF.md`).

## Mission runtime (`MissionRunner`)

`core/missions.py::MissionRunner` takes a `SkillRunner`, a policy, and an optional `require_approval_cb`. It:

- iterates each `MissionStep`
- collects manifest capabilities and `manifest_capability_semantics` (intent class, resource boundaries)
- projects mission-level intent and boundaries (`read_only | mutating | destructive`, filesystem/network/secrets/system)
- calls `skill_runner.simulate(...)` for dry-run previews
- executes steps under the policy gate for real runs
- appends a compact mission log line to `~/VoxeraOS/notes/mission-log.md`

The mission runner is the same code path used by the queue daemon when a `mission_id` job is executed — there is no divergence between CLI and queue mission execution.

## Mission planning (`core/mission_planner.py`)

`plan_mission(...)` is the cloud-planning path, used when a caller asks the brain to produce an executable plan from a natural-language goal.

Key characteristics (from `core/mission_planner.py` and `core/planner_context.py`):

- The planner is **downstream-gated** by capability semantics and policy. Its output cannot bypass the skill registry or policy engine.
- Deterministic helpers run **before** the planner for simple intents (see `core/simple_intent.py`). The cloud planner is only used when deterministic intent detection cannot route the request.
- Dry-run plans can be made byte-identical via `--deterministic` (scrubs timestamps in `capabilities_snapshot`).

## Simple-intent guardrail (`core/simple_intent.py`)

`simple_intent` performs deterministic intent classification for goal-kind jobs. Recognized intents include:

- `open_terminal`
- `open_url`
- `open_app`
- `write_file`
- `read_file`
- `run_command`
- `assistant_question`
- `unknown_or_ambiguous`

If the planner's first step does not match the allowed skill family for a detected simple intent, execution is blocked fail-closed before any skill runs. This is the origin of `intent_route.mismatch` in the audit log and of `execution_result.json.intent_route` evidence on goal-kind jobs.

## `file_organize`

`file_organize` is a first-class structured queue contract (`core/queue_contracts.py`, `core/file_intent.py`). Submit shape:

```json
{
  "file_organize": {
    "source_path": "...",
    "destination_dir": "...",
    "mode": "copy",
    "overwrite": false,
    "delete_original": false
  }
}
```

The daemon expands this into a deterministic multi-step mission using the bounded file skills (`files.exists`, `files.stat`, `files.mkdir`, `files.copy_file`, `files.delete_file`). Boundary enforcement from `core/file_intent.py`:

- only paths under `~/VoxeraOS/notes` are allowed
- queue subtree (`~/VoxeraOS/notes/queue/**`) is rejected fail-closed
- parent traversal (`..`, symlink escapes) is rejected

If expansion violates the allowed lane, the mission fails before execution with a canonical failure summary and a next-action hint.

## `write_file`

`write_file` is a bounded structured contract for small text writes. Expansion produces a single-step mission with `files.write_text` under the same path boundary rules. Like `file_organize`, unknown fields are rejected, and the canonical failure surface is evidence-grounded.

## Inline steps

The `steps` field is a list of `{skill_id, args}` entries. It is the lowest-level queue contract — use it when you want to compose a custom workflow without authoring a mission template. Each step is validated against the skill registry and policy before any execution happens.

Inline steps are the primary direct-queue test surface. See `Mission-testing-and-building-CLI.txt` for the full test method.

## Execution pipeline

Once a queue job has been normalized and its request kind is known, execution looks like this (`core/queue_execution.py`):

1. **Normalize payload** — strict contract validation, lineage sanitization, approval flag coercion.
2. **Derive request kind** — `job_intent.request_kind` → payload `kind` → structural inference → fallback.
3. **Build the mission** — from a template, an expanded structured contract, a planner output, or an inline step list.
4. **Transition to `planning` → `running`** — update `.state.json` sidecar on every transition.
5. **Run each step through the skill runner** — capability + policy gate, approval gate where needed.
6. **Accumulate step results** — `step_results.json`, `actions.jsonl`, `stdout`/`stderr` captures.
7. **Build execution result** — `execution_result.json` + `execution_envelope.json`, normalized terminal outcome, artifact refs, evidence bundle, review summary (when applicable).
8. **Finalize** — `move_job_with_sidecar(...)` places the job in `done/` / `failed/` / `canceled/` with its sidecars intact.

Every transition is audit-logged. The panel and CLI read the same sidecars and artifacts for display — there is no separate view model.

## Dry-run

All three execution paths support dry-run:

- `voxera run <skill_id> --dry-run`
- `voxera missions run <id> --dry-run`
- `voxera missions plan "<goal>" --dry-run [--deterministic]`

Dry-run calls the skill runner's `simulate(...)` path, which walks the policy gate without side effects. Dry-run bypasses the direct-mutation gate because nothing runs.

## Where to look next

- `core/queue_execution.py` — the full execution pipeline
- `core/missions.py` — mission templates and mission runner
- `core/mission_planner.py` — goal → plan flow and planner context
- `core/simple_intent.py` — deterministic intent routing
- `core/file_intent.py` — file workflow boundary enforcement
- `core/capability_semantics.py` — the central capability → effect-class / intent-class / resource-boundary contract
