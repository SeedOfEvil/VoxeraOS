# 06 — Skills, Capabilities and Built-in Catalog

This document describes the skills subsystem under `src/voxera/skills/`,
the built-in skill manifests under `skills/`, and the corresponding
entrypoints in `src/voxera_builtin_skills/`.

For machine-readable detail, see `skill_manifest_inventory.json`.

## Skill subsystem (`src/voxera/skills/`)

| File | Purpose |
| ---- | ------- |
| `registry.py` | `SkillRegistry` discovers manifests from the `skills/` tree, validates them via `models.SkillManifest`, and produces a `SkillDiscoveryReport` (valid / invalid / incomplete / warning). |
| `runner.py` | `SkillRunner` resolves the entrypoint (`module:function`), constructs a `JobPaths` workspace and artifact dir, normalizes args, executes the skill, redacts secrets, and persists the canonical `skill_result` artifact. |
| `execution.py` | Execution helpers including `JobPaths` (job_id, workspace_dir, artifacts_dir), `generate_job_id()`, `sanitize_command()`, `sanitize_env()`, and the redaction patterns for KEY/TOKEN/SECRET/PASS plus base64/hex fragments. |
| `arg_normalizer.py` | `canonicalize_argv(...)` for command-line arg validation and normalization. |
| `path_boundaries.py` | Workspace and read-only scope enforcement used by `fs_scope`. |
| `result_contract.py` | `SKILL_RESULT_KEY`, `build_skill_result(...)`, and the standard skill result envelope. |

## Skill manifest contract

A `manifest.yml` (validated by `models.SkillManifest`) declares:

- `id` — globally unique skill id (e.g. `files.copy`, `sandbox.exec`).
- `name` — human display label.
- `description` — short description for operator surfaces.
- `entrypoint` — `module:function` reference into `voxera_builtin_skills`.
- `capabilities` — list of capability strings (e.g. `files.write`,
  `sandbox.exec`). Routed through `core/capability_semantics.py`.
- `risk` — `low` / `medium` / `high`.
- `exec_mode` — `local` (host) or `sandbox` (rootless Podman).
- `needs_network` — bool.
- `fs_scope` — `workspace_only` / `read_only` / `broader`.
- `output_schema` — output envelope (`skill_result.v1` for shipped skills).
- `output_artifacts` — list of expected artifact filenames.
- `args` — typed argument schema (name, type, required, default,
  description).

The skill registry rejects unknown manifest fields and emits a warning
report for incomplete manifests rather than crashing the daemon.

## Built-in skill catalog

The current skill tree under `skills/` ships 31 manifests. They map 1:1
to entrypoints in `voxera_builtin_skills`. Categories:

### Files (workspace_only)

| Skill id | Description |
| -------- | ----------- |
| `files.copy` | Copy a file between allowlisted notes paths. |
| `files.copy_file` | Copy a single file (alternate variant kept for compatibility). |
| `files.delete_file` | Delete a file from the bounded workspace. |
| `files.exists` | Check whether a path exists. |
| `files.find` | Bounded find by name/pattern. |
| `files.grep_text` | Bounded grep over text files. |
| `files.list_dir` | List a directory inside the workspace. |
| `files.list_tree` | Recursive tree listing of a workspace subtree. |
| `files.mkdir` | Create a directory inside the workspace. |
| `files.move` | Move a path inside the workspace. |
| `files.move_file` | Move a single file (alternate variant). |
| `files.read_text` | Read text content from a workspace file. |
| `files.rename` | Rename a workspace path. |
| `files.stat` | Stat a workspace path. |
| `files.write_text` | Write text content to a workspace file. |

### Clipboard

| Skill id | Description |
| -------- | ----------- |
| `clipboard.copy` | Copy text to the host clipboard. |
| `clipboard.paste` | Read text from the host clipboard. |

### System / host

| Skill id | Description |
| -------- | ----------- |
| `system.disk_usage` | Disk usage snapshot. |
| `system.host_info` | Host info snapshot. |
| `system.memory_usage` | Memory usage snapshot. |
| `system.load_snapshot` | Load average snapshot. |
| `system.process_list` | Process list snapshot. |
| `system.recent_service_logs` | Recent journald logs for an allowlisted service. |
| `system.service_status` | Status for an allowlisted systemd service. |
| `system.set_volume` | Set system volume. |
| `system.status` | High-level system status snapshot. |
| `system.terminal_run_once` | Open a one-shot terminal command. |
| `system.window_list` | List open windows. |
| `system.open_app` | Launch an allowlisted application. |
| `system.open_url` | Open a URL in the default browser. |

### Sandbox

| Skill id | Description |
| -------- | ----------- |
| `sandbox.exec` | Execute a command (argv list or shell string) in a rootless Podman sandbox. Output artifacts: `stdout.txt`, `stderr.txt`, `runner.json`, `command.txt`. |

`needs_network` is false by default for `sandbox.exec`; setting `network=true`
in args triggers an approval gate under default policy (see
`missions/sandbox_net.json`).

## Capability semantics

`core/capability_semantics.py` is the centralized source of truth for what
each capability means. Each capability declares:

- `effect_class` — `read | write | execute`.
- `intent_class` — `read_only | mutating | destructive`.
- `policy_field` — name of the policy approval field (when applicable).
- `resource_boundaries` — `filesystem | network | secrets | system`.
- short operator-facing summary string.

`manifest_capability_semantics(...)` projects this onto a manifest so
the registry, policy engine, mission planner, panel UI, and capability
snapshot all read the same vocabulary.

## Policy

`src/voxera/policy.py` is the capability → allow / ask / deny resolver
applied at queue execution time. It consults the resolved `AppConfig`
policy block plus per-job approval gates. Approve-always decisions are
recorded as policy uplifts.

## Capabilities snapshot

`core/capabilities_snapshot.generate_capabilities_snapshot()` walks the
skill registry plus capability semantics and produces the snapshot used
by:

- `voxera ops capabilities` (CLI).
- `voxera missions plan --freeze-capabilities-snapshot` (planner pin).
- The panel home page widget.
- Golden tests (`tools/golden_surfaces.py`).

## Direct CLI execution

`voxera run SKILL_ID --arg key=value` runs a skill directly via
`SkillRunner`. Mutating skills are blocked unless `VOXERA_DEV_MODE=1` and
`--allow-direct-mutation` is supplied. This guard exists because direct
CLI runs bypass the queue lifecycle, so they cannot honor approval
contracts the way queue jobs can.

## Direct sandbox skill notes

`voxera_builtin_skills/sandbox_exec.py` is the only built-in that uses
`exec_mode: sandbox`. It expects rootless Podman on the host. The
manifest declares `output_artifacts: [stdout.txt, stderr.txt, runner.json,
command.txt]`, all of which the runner enforces post-execution.
