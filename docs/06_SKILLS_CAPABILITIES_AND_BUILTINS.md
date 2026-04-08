# 06 — Skills, Capabilities, and Built-ins

This document describes the skill registry, the capability semantics contract, and the current built-in skill catalog. All of it is grounded in `src/voxera/skills/`, `src/voxera/core/capability_semantics.py`, `src/voxera/policy.py`, and the on-disk `skills/**/manifest.yml` files.

See `skill_manifest_inventory.json` for the full mechanical inventory (31 manifests at regeneration time).

## Skill registry

`src/voxera/skills/registry.py` owns skill discovery. Core concepts:

- **`SkillRegistry`** — walks `skills/` (by default `<repo>/skills`), loads every `manifest.yml`, validates it against the `SkillManifest` Pydantic model, and caches a `{skill_id: SkillManifest}` mapping.
- **`SkillDiscoveryReport`** — carries `{valid, issues, discovered_paths}`. Issue statuses: `valid`, `invalid`, `incomplete`, `warning`.
- **`SkillHealthIssue`** — structured issue surface used for doctor / setup-wizard / manifest diagnostics.
- **`blocks_runtime`** — true if any `invalid` or `incomplete` manifests were discovered. The daemon and CLI fail fast when this is true.

Skill IDs are canonical (`<family>.<action>`, e.g. `files.copy_file`, `system.status`). The registry de-duplicates by id; a duplicate id is an invalid state.

## Skill runner

`src/voxera/skills/runner.py` is the policy-aware skill runner used by both the CLI direct path and the queue mission runner.

Key operations:

- `simulate(manifest, args=..., policy=...)` — deterministic simulation, used for dry-run plans and mission previews.
- `run(manifest, args=..., policy=...)` — full execution under the policy gate.
- enforces declared capability boundaries through `core/capability_semantics.manifest_capability_semantics(...)`.
- integrates with the approval gate when a capability requires `ask`.

Argument normalization and result extraction happen through:

- `skills/arg_normalizer.py` — type coercion and validation.
- `skills/result_contract.py::extract_skill_result` — normalizes to the `skill_result.v1` contract.
- `skills/execution.py` — execution context helpers (env, cwd, timeouts, stream capture).
- `skills/path_boundaries.py` — filesystem allowlist enforcement; rejects queue-subtree paths and parent-traversal.

## Manifest contract (`manifest.yml`)

Every skill ships a `manifest.yml` with at least:

| Field | Purpose |
|---|---|
| `id` | Canonical `<family>.<action>` skill id |
| `name` | Human-readable name |
| `description` | One-line description |
| `entrypoint` | Module:function pointer to the runtime entrypoint |
| `capabilities` | Declared capability list (for example `files.write`, `state.read`) |
| `risk` | Coarse risk label (`low`, `medium`, `high`) |
| `exec_mode` | Execution mode (`local`, etc.) |
| `needs_network` | Boolean network requirement |
| `fs_scope` | `read_only`, `workspace_only`, `broader`, or `none` |
| `output_schema` | `skill_result.v1` for current built-ins |
| `output_artifacts` | Deterministic artifact list (may be `[]`) |
| `args` | Arg schema (type, required, default, description) |

Additional optional fields (per the execution security model): `network_scope`, `allowed_domains`, `allowed_paths`, `secret_refs`, `sandbox_profile`, `expected_artifacts`. Not every current skill populates every optional field yet — the contract is canonical, rollout is incremental.

Example (current `files.copy_file`):

```yaml
id: files.copy_file
name: Copy File
description: Copy a file between allowlisted notes paths.
entrypoint: voxera_builtin_skills.files_copy_file:run
capabilities: ["files.write"]
risk: medium
exec_mode: local
needs_network: false
fs_scope: workspace_only
output_schema: skill_result.v1
output_artifacts: []
args:
  source_path: { type: string, required: true, ... }
  destination_path: { type: string, required: true, ... }
  overwrite: { type: boolean, required: false, default: false, ... }
```

## Capability semantics (`core/capability_semantics.py`)

The central capability contract is normalized in `core/capability_semantics.py` and projected per-manifest through `manifest_capability_semantics(manifest)`.

The normalized contract includes:

- **`effect_class`** — `read`, `write`, or `execute`.
- **`intent_class`** — `read_only`, `mutating`, or `destructive`.
- **`resource_boundaries`** — `{filesystem, network, secrets, system}` booleans.
- **`policy_mapping`** — mapping from capability to policy field when governed.

`CAPABILITY_EFFECT_CLASS` in `core/capability_semantics.py` (re-exported through `policy.py`) is the canonical source for the direct-CLI mutation gate. A skill is considered read-only if **every** declared capability maps to `read`. Skills with no declared capabilities are treated as non-read-only (fail closed).

## Direct-CLI mutation gate

From `docs/EXECUTION_SECURITY_MODEL.md` § 9 and `src/voxera/cli_skills_missions.py::run_impl`:

- **Read-only skills** (all capabilities map to `read`) execute directly under `voxera run <id>`.
- **Mutating skills** (any capability maps to `write` / `execute`) are **blocked by default** on direct CLI execution.
- **Dev-mode override** requires both:
  1. `VOXERA_DEV_MODE=1` environment variable, **and**
  2. `--allow-direct-mutation` CLI flag.
  The override logs a loud warning. It is not the product trust path.
- **Dry-run** (`voxera run <id> --dry-run`) bypasses the gate because dry-run does not execute.

All mutating skills are expected to flow through the queue, which enforces policy + approval semantics consistently.

## Policy (`src/voxera/policy.py`)

`policy.py` is the policy engine entrypoint. It:

- reads effect classes from `CAPABILITY_EFFECT_CLASS`
- resolves `allow` / `ask` / `deny` decisions given a skill, arguments, and the active policy config
- integrates with the approval gate by surfacing `ask` decisions
- fails closed on any unknown or malformed policy mapping

Policy templates live in `config-templates/policy.example.yml`. The runtime policy file is loaded via `load_config` (`src/voxera/config.py`).

## Current built-in skill catalog

The on-disk `skills/` tree (`skill_manifest_inventory.json`):

**`clipboard.*`** (2 skills)
- `clipboard.copy` — copy text to the clipboard
- `clipboard.paste` — paste from the clipboard

**`files.*`** (15 skills)

Read-only (`fs_scope: read_only`):
- `files.exists` — predicate check
- `files.stat` — file metadata
- `files.list_dir` — list a directory
- `files.list_tree` — bounded tree listing
- `files.read_text` — read a text file
- `files.find` — bounded find
- `files.grep_text` — bounded grep over text files

Workspace-bounded mutations (`fs_scope: workspace_only`):
- `files.write_text` — write a text file
- `files.mkdir` — create a directory (parents/exist_ok)
- `files.copy_file` — copy a file under notes
- `files.move_file` — move a file under notes
- `files.rename` — rename within a bounded path
- `files.delete_file` — delete a file (requires approval)
- `files.copy` — legacy copy helper
- `files.move` — legacy move helper

**`sandbox.*`** (1 skill)
- `sandbox.exec` — execute a bounded command in the sandbox profile

**`system.*`** (13 skills)

Read-only system inspection (`state.read` / `window.read`):
- `system.status` — basic system info
- `system.host_info` — host identification
- `system.memory_usage` — memory snapshot
- `system.load_snapshot` — CPU/load snapshot
- `system.disk_usage` — home partition disk usage (no shell)
- `system.process_list` — bounded process snapshot (via `ps -eo ...`)
- `system.window_list` — enumerate windows
- `system.service_status` — systemd service status (bounded)
- `system.recent_service_logs` — recent service journal (bounded)

Interaction / mutation:
- `system.open_app` — launch a named app
- `system.open_url` — open a URL
- `system.set_volume` — set output volume
- `system.terminal_run_once` — one-shot terminal command

All skill runtime entrypoints live in a separate `voxera_builtin_skills.*` module namespace referenced from each manifest's `entrypoint`. The repo's `skills/` tree contains manifests only; the runtime code is wired through the registry.

## Filesystem boundaries (waves 1–2)

From `docs/EXECUTION_SECURITY_MODEL.md`:

- `files.list_dir`, `files.exists`, and `files.stat` are read-only inspection skills (`fs_scope=read_only`, local-only, no network).
- `files.copy_file`, `files.move_file`, `files.mkdir`, and `files.delete_file` are confined mutation skills (`fs_scope=workspace_only`, local-only, no network).
- All file skills are constrained to allowlisted notes-root path normalization and fail closed on boundary violations.

This is enforced by `skills/path_boundaries.py` plus `core/file_intent.py`. Tests: `tests/test_files_control_plane_boundaries.py`, `test_files_wave2_skills.py`, and the red-team suite.

## System inspection boundary

From `docs/EXECUTION_SECURITY_MODEL.md`:

- `system.status`, `system.disk_usage`, `system.process_list`, and `system.window_list` are read-only (`state.read` / `window.read` capability, `fs_scope=read_only`, no network).
- `system.disk_usage` reads disk usage for the home partition via `shutil.disk_usage` — it does **not** shell out.
- `system.process_list` reads process state via `ps -eo pid,user,%cpu,%mem,comm` with bounded output (truncated to 50 entries).
- The `system_inspect` mission composes these into a single bounded queue-backed diagnostic workflow with canonical evidence production and no approvals.

## Extending the skill catalog

Current four-layer integration rule (from `Testing-Method.txt`):

1. **Skill layer** — manifest + entrypoint + runtime behavior.
2. **Mission / workflow layer** — a real reusable workflow built on the skill.
3. **Queue / contract layer** — a canonical payload shape (mission_id / inline steps / structured contract).
4. **Vera / routing layer** — natural-language routing, preview, submit, review.

A feature is not "done" at layer 1. Treat the skill as ready only when all four layers have been verified.

Test entry points: see `08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md` — `test_skill_metadata_and_runners`, `test_skill_result_payloads`, `test_files_*`, `test_system_inspect`, `test_registry`, `test_execution`, `test_execution_capabilities`, `test_execution_evaluator`, `test_policy`, `test_security_redteam`.
