# 00 — Master Index

Replacement documentation bundle for VoxeraOS, regenerated from the current repository state.

This bundle replaces an older (circa March 2026) project-index bundle. Many things have changed since then — services, Vera internals, panel routes, queue surfaces, skills, missions, tests, and inventories — so this is a full replacement pass, not a delta on top of the old snapshot.

## What this bundle is

This bundle is a descriptive technical index of the repository as it exists on the current branch. It is grounded in actual files:

- inventories are produced by AST-scanning `src/voxera` and reading manifests directly from `skills/` and `missions/`
- the structure map is derived from a live walk of the repo tree
- the markdown documents cite files under `src/voxera/`, `skills/`, `missions/`, `tests/`, and `deploy/systemd/user/` that exist today

It is not a marketing document, not a roadmap, and not aspirational. Where something is still rough or transitional, the docs say so in plain language.

## Alignment with repo reality

At the time of this regeneration, the repo advertises:

- project name: `voxera-os`, version `0.1.9` (`pyproject.toml`)
- minimum Python: 3.10
- CLI entrypoint: `voxera = "voxera.cli:app"`
- three operator surfaces: CLI (`voxera`), web panel (`voxera panel`), and Vera (`voxera vera` / `make vera` / user service)
- systemd user units: `voxera-daemon.service`, `voxera-panel.service`, `voxera-vera.service` under `deploy/systemd/user/`
- Makefile targets for validation, golden surfaces, security red-team, and merge readiness

Any doc here that contradicts current files should be treated as a doc bug.

## Files in this bundle

Markdown docs (this directory, `docs/`):

| File | Purpose |
|------|---------|
| `00_MASTER_INDEX.md` | This file — entry point and map of the bundle |
| `01_REPOSITORY_STRUCTURE_MAP.md` | Top-level repo layout, key directories, and module ownership |
| `02_CONFIGURATION_AND_RUNTIME_SURFACES.md` | Config loading, paths, systemd units, ports, environment vars |
| `03_QUEUE_OBJECT_MODEL_AND_LIFECYCLE.md` | Queue payload shape, buckets, state sidecars, and lifecycle contract |
| `04_GOAL_MISSION_PLANNING_AND_EXECUTION.md` | Mission templates, mission planner, inline steps, goal planning |
| `05_VERA_CONTROL_LAYER_AND_HANDOFF.md` | Vera chat layer: preview → submit → review → linked completions |
| `06_SKILLS_CAPABILITIES_AND_BUILTINS.md` | Skill registry, manifest contract, capability semantics, current built-ins |
| `07_ARTIFACTS_EVIDENCE_AND_REVIEW_MODEL.md` | Per-job artifacts, evidence bundles, review summaries |
| `08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md` | Test suite themes, Make targets, day-to-day change surfaces |
| `09_CORE_OBJECTS_AND_SCHEMA_REFERENCE.md` | Data model references: capability semantics, job_intent, lineage |
| `10_INFORMATIONAL_FILES_MAP.md` | Where the rest of `docs/` lives and what it covers |

Inventories (JSON + text, machine-checkable):

| File | What it holds |
|------|---------------|
| `root_inventory.json` | Top-level files/dirs at the repo root, one-line descriptions |
| `directory_tree.txt` | Bounded `tree`-style view of the repo (skipping caches/venv) |
| `module_inventory.json` | Every Python module under `src/voxera` with docstring + counts |
| `class_inventory.json` | Public top-level classes discovered via AST |
| `function_inventory.json` | Public top-level functions discovered via AST |
| `mission_inventory.json` | Built-in `MISSION_TEMPLATES` plus file-based `missions/*.json` |
| `skill_manifest_inventory.json` | Every `skills/**/manifest.yml` parsed directly |

Informational text files (kept for continuity with the older bundle, updated to current repo reality):

| File | Purpose |
|------|---------|
| `Quick-Start.txt` | Fastest path from clone to a running stack |
| `North-Star.txt` | Product direction — what Vera and VoxeraOS are, and what they are not |
| `Testing-Method.txt` | Multi-layer STV / test ladder used for meaningful PRs |
| `Mission-testing-and-building-CLI.txt` | Direct queue JSON / CLI-level mission testing method |
| `Coding-Agent-Prompt-Example.txt` | Example prompt shape for coding-agent sessions on this repo |
| `VoxeraOS-Project-Technical-Rundown-March-23rd.txt` | Technical overview, updated to current repo reality (filename kept for continuity) |
| `March-25-2026-VoxeraOS-—-Engineering-Report.txt` | Engineering status report, updated to current repo reality (filename kept for continuity) |

## How this bundle was regenerated

1. `src/voxera`, `skills/`, `missions/`, `tests/`, and `deploy/systemd/user/` were scanned on the current branch.
2. `tools/scan_inventories.py`-style AST walk produced the JSON inventories from live code.
3. Markdown and text docs were written against the inventories and against direct reads of the canonical modules:
   - `src/voxera/core/queue_daemon.py`
   - `src/voxera/core/queue_contracts.py`
   - `src/voxera/core/queue_execution.py`
   - `src/voxera/core/queue_approvals.py`
   - `src/voxera/core/queue_recovery.py`
   - `src/voxera/core/queue_result_consumers.py`
   - `src/voxera/core/missions.py`
   - `src/voxera/core/mission_planner.py`
   - `src/voxera/core/capability_semantics.py`
   - `src/voxera/core/simple_intent.py`
   - `src/voxera/vera/service.py` and the rest of `src/voxera/vera/`
   - `src/voxera/vera_web/app.py`
   - `src/voxera/panel/app.py` plus `src/voxera/panel/routes_*.py`
   - `src/voxera/skills/registry.py`
   - `src/voxera/cli.py` plus `src/voxera/cli_*.py`
4. The older bundle's framing was preserved where still accurate; everything else was rewritten.

## What to read first

- If you want the big picture: `01_REPOSITORY_STRUCTURE_MAP.md`, then `North-Star.txt`.
- If you want runtime boundaries: `02_CONFIGURATION_AND_RUNTIME_SURFACES.md`, then `03_QUEUE_OBJECT_MODEL_AND_LIFECYCLE.md`.
- If you want to understand Vera's boundary with the queue: `05_VERA_CONTROL_LAYER_AND_HANDOFF.md`.
- If you want to verify a feature end-to-end: `Testing-Method.txt` plus `Mission-testing-and-building-CLI.txt`.

## Confidence and caveats

- All inventories and module counts come from a direct AST walk of the current tree. They are mechanically accurate for the snapshot they were generated from.
- Higher-level descriptions are best effort. Where logic is complex (for example queue execution and Vera follow-up handling), the docs describe the observable contract and point to the canonical module, rather than re-deriving every branch.
- "Best effort" language is used only where the repo itself treats something as transitional — voice-first UX, mission breadth, and provider support beyond OpenRouter are the main examples.
