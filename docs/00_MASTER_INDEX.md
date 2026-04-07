# 00 — Master Index

This is the regenerated VoxeraOS replacement documentation bundle. Every file
listed here was rebuilt by re-scanning the current repository state on the
`claude/regenerate-docs-index-XwAi3` branch (VoxeraOS `0.1.9` alpha). It
replaces the older bundle that was best-effort dated around March 2026; that
prior bundle was stale relative to the current Vera, queue, panel, and skills
surfaces.

The bundle is intended as a grounded technical reference. It is not marketing
copy, and it is not a roadmap. It records what is observable in the repo
today.

## Bundle layout

Markdown reference files (this directory, `docs/`):

| File | Purpose |
| ---- | ------- |
| `00_MASTER_INDEX.md` | This index. |
| `01_REPOSITORY_STRUCTURE_MAP.md` | Top-level repo structure and what each directory holds. |
| `02_CONFIGURATION_AND_RUNTIME_SURFACES.md` | Config files, environment, systemd units, CLI entry points, panel + Vera HTTP surfaces. |
| `03_QUEUE_OBJECT_MODEL_AND_LIFECYCLE.md` | Canonical queue object, lifecycle states, on-disk layout, sidecars, daemon contract. |
| `04_GOAL_MISSION_PLANNING_AND_EXECUTION.md` | Goal/mission planning, intent classifiers, execution evaluator, mission templates, runner. |
| `05_VERA_CONTROL_LAYER_AND_HANDOFF.md` | `voxera.vera` and `voxera.vera_web` modules: chat session, drafting, handoff, evidence ingestion. |
| `06_SKILLS_CAPABILITIES_AND_BUILTINS.md` | Skill manifests, registry, runner, capability semantics, built-in skill catalog. |
| `07_ARTIFACTS_EVIDENCE_AND_REVIEW_MODEL.md` | Artifact families, evidence bundle, review summary, structured execution resolution. |
| `08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md` | Pytest suites, golden surfaces, ops bundles, hygiene/recovery, e2e scripts. |
| `09_CORE_OBJECTS_AND_SCHEMA_REFERENCE.md` | Pydantic / dataclass / TypedDict / Literal type references for core objects. |
| `10_INFORMATIONAL_FILES_MAP.md` | Map of `.md` and `.txt` informational/reference files in the repo. |

JSON / text inventories (regenerated from the live tree):

| File | Generated from |
| ---- | -------------- |
| `root_inventory.json` | Top-level entries of the repo. |
| `directory_tree.txt` | Recursive directory tree (excludes `.git`, `.venv`, caches). |
| `module_inventory.json` | Every Python module under `src/`, `tests/`, `scripts/`, `tools/` with its top-level classes, functions, LOC. |
| `class_inventory.json` | Every class definition with its methods. |
| `function_inventory.json` | Every top-level function (sync + async) with arg names. |
| `mission_inventory.json` | Mission JSON files under `missions/`. |
| `skill_manifest_inventory.json` | Every `manifest.yml` under `skills/`. |

Informational `.txt` files (rewritten in this pass):

| File | Purpose |
| ---- | ------- |
| `Quick-Start.txt` | Concrete current quick-start. |
| `North-Star.txt` | Plain-text projection of the project north star. |
| `Testing-Method.txt` | How tests are organized and run. |
| `Mission-testing-and-building-CLI.txt` | Mission/skill CLI workflows. |
| `Coding-Agent-Prompt-Example.txt` | An example operator/agent prompt grounded in current contracts. |
| `VoxeraOS-Project-Technical-Rundown-March-23rd.txt` | Filename retained for continuity; body rewritten to current repo reality. |
| `March-25-2026-VoxeraOS-—-Engineering-Report.txt` | Filename retained for continuity; body rewritten to current repo reality. |

## Headline facts (current repo)

- **Version:** `0.1.9` (`pyproject.toml`, `src/voxera/version.py`).
- **Python:** `>=3.10`.
- **Top-level packages:** `voxera` (main control plane), `voxera_builtin_skills` (built-in skill entrypoints).
- **Long-running processes:** queue daemon (`voxera daemon`), panel (`voxera panel`), Vera web app (`uvicorn voxera.vera_web.app:app`). Three matching user systemd units live in `deploy/systemd/user/`.
- **Queue root on disk:** `~/VoxeraOS/notes/queue/` (see `paths.queue_root()`).
- **Built-in skills shipped:** 31 manifests under `skills/` mapped to entrypoints in `voxera_builtin_skills`.
- **Built-in missions shipped:** 2 JSON missions under `missions/` (`sandbox_smoke`, `sandbox_net`); additional mission templates live in code (`core/missions.py`).
- **Tests:** ~110 pytest files under `tests/` plus a `tests/golden/` snapshot directory.

## Major deltas vs the older (≈March) bundle

The replacement pass uncovered the following deltas relative to the older
documentation bundle:

- The Vera control layer is now split between `src/voxera/vera/` (control,
  session store, drafting, handoff, evidence ingestion) and
  `src/voxera/vera_web/` (FastAPI chat surface, execution mode classifier,
  response shaping, draft content binding, conversational checklist
  enforcement). Older docs assumed a single Vera module surface.
- The queue daemon is composed via mixins
  (`QueueApprovalMixin`, `QueueRecoveryMixin`, `QueueExecutionMixin`) inside
  `core/queue_daemon.py`. The lifecycle vocabulary is now centralized in
  `core/queue_object_model.py` (states, terminal states, artifact families,
  truth surfaces).
- Intent classification is split into `simple_intent`, `file_intent`,
  `code_draft_intent`, `writing_draft_intent`, and `queue_job_intent` modules.
- The panel routes are split into one route module per surface
  (`routes_jobs`, `routes_assistant`, `routes_home`, `routes_hygiene`,
  `routes_recovery`, `routes_bundle`, `routes_missions`, `routes_queue_control`,
  `routes_vera`).
- The CLI is composed via several `cli_queue_*` and `cli_*` sub-modules
  registered onto a single Typer root (`src/voxera/cli.py`).
- Built-in skill entrypoints all live under `src/voxera_builtin_skills/`.
- Documentation under `docs/` now includes the long-form `ARCHITECTURE.md`,
  `CODEX_MEMORY.md`, `HOTSPOT_AUDIT_EXTRACTION_ROADMAP.md`, multiple
  `ROADMAP*.md` files, plus this regenerated bundle.

See file 10 for the full informational map and file-by-file deltas.
