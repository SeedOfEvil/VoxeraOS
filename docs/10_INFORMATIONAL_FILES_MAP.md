# 10 — Informational Files Map

This file maps every informational `.md` and `.txt` file shipped in the
repo, plus the regenerated bundle.

## Repository root

| File | Purpose |
| ---- | ------- |
| `README.md` | Project overview and operator quick path. |
| `AGENT.md` | Guidance for assistants/operators editing the repo. |
| `CODEX.md` | Long-form working notes for code-generation agents. |
| `CHANGELOG.md` | Release notes. |
| `CONTRIBUTING.md` | Contribution guide. |
| `LICENSE` | MIT license. |
| `NOTICE` | License notice. |
| `SECURITY.md` | Top-level security disclosure policy. |

## `docs/` long-form references

| File | Purpose |
| ---- | ------- |
| `ARCHITECTURE.md` | Long-form architecture document covering the three-layer model and per-module structure. |
| `BOOTSTRAP.md` | Bootstrapping a fresh install. |
| `CODEX_MEMORY.md` | Working memory log used by code-generation agents (large file). |
| `EXECUTION_SECURITY_MODEL.md` | Threat model + execution security details. |
| `HOTSPOT_AUDIT_EXTRACTION_ROADMAP.md` | Hotspot audit + extraction order for large surface modules. |
| `LOCAL_MODELS.md` | Notes about running local models. |
| `NORTH_STAR.md` | Project north star (canonical). |
| `QUEUE_CONSTITUTION.md` | Frozen queue contracts (payload, lifecycle, artifacts). |
| `QUEUE_OBJECT_MODEL.md` | Canonical queue object model document. |
| `ROADMAP.md` | Current roadmap. |
| `ROADMAP_0.1.4.md`, `ROADMAP_0.1.5.md`, `ROADMAP_0.1.6.md` | Past release roadmaps. |
| `SECURITY.md` | Security model details (longer than the root file). |
| `UBUNTU_TESTING.md` | Ubuntu-specific testing notes. |
| `ops.md` | Long-form operations reference. |

## `docs/prompts/`

Prompt fragments used by Vera/planner roles:

- `00-system-overview.md`
- `01-platform-boundaries.md`
- `02-role-map.md`
- `03-runtime-technical-overview.md`
- `capabilities/artifacts-and-evidence.md`
- `capabilities/execution-security-model.md`
- `capabilities/handoff-and-submit-rules.md`
- `capabilities/hidden-compiler-payload-guidance.md`
- `capabilities/preview-payload-schema.md`
- `capabilities/queue-lifecycle.md`
- `capabilities/queue-object-model.md`
- `capabilities/web-investigation-rules.md`
- `roles/hidden-compiler.md`
- `roles/planner.md`
- `roles/vera.md`
- `roles/verifier.md`
- `roles/web-investigator.md`

## `docs/testing/`

Operator testing payloads kept in `docs/testing/payloads/`.

## Regenerated bundle (this pass)

| File | Purpose |
| ---- | ------- |
| `00_MASTER_INDEX.md` | Bundle index. |
| `01_REPOSITORY_STRUCTURE_MAP.md` | Repo structure. |
| `02_CONFIGURATION_AND_RUNTIME_SURFACES.md` | Config + runtime surfaces. |
| `03_QUEUE_OBJECT_MODEL_AND_LIFECYCLE.md` | Queue object model. |
| `04_GOAL_MISSION_PLANNING_AND_EXECUTION.md` | Goal/mission planning. |
| `05_VERA_CONTROL_LAYER_AND_HANDOFF.md` | Vera control layer. |
| `06_SKILLS_CAPABILITIES_AND_BUILTINS.md` | Skills + capabilities + built-in catalog. |
| `07_ARTIFACTS_EVIDENCE_AND_REVIEW_MODEL.md` | Artifacts + evidence model. |
| `08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md` | Tests + ops + change surfaces. |
| `09_CORE_OBJECTS_AND_SCHEMA_REFERENCE.md` | Core typed object reference. |
| `10_INFORMATIONAL_FILES_MAP.md` | This file. |
| `root_inventory.json` | Top-level repo entries. |
| `directory_tree.txt` | Recursive directory tree. |
| `module_inventory.json` | Python module inventory. |
| `class_inventory.json` | Class inventory. |
| `function_inventory.json` | Function inventory. |
| `mission_inventory.json` | Mission JSON inventory. |
| `skill_manifest_inventory.json` | Skill manifest inventory. |
| `Quick-Start.txt` | Plain-text current quick-start. |
| `North-Star.txt` | Plain-text north star. |
| `Testing-Method.txt` | Plain-text testing notes. |
| `Mission-testing-and-building-CLI.txt` | Mission/skill CLI workflows. |
| `Coding-Agent-Prompt-Example.txt` | Operator/agent prompt example. |
| `VoxeraOS-Project-Technical-Rundown-March-23rd.txt` | Filename retained, body rewritten to current repo. |
| `March-25-2026-VoxeraOS-—-Engineering-Report.txt` | Filename retained, body rewritten to current repo. |

## Notes about retained filenames

Two files in this bundle keep date-stamped filenames from the prior bundle
even though their contents are now grounded in the current repo:

- `VoxeraOS-Project-Technical-Rundown-March-23rd.txt`
- `March-25-2026-VoxeraOS-—-Engineering-Report.txt`

The dates do not reflect when the content was authored. They are kept so
external links continue to resolve. The body of each file is fully
replaced and reflects the current `0.1.9` repo state.

## Files removed from older bundles

The older bundle assumed certain modules and surfaces that no longer
exist or have moved. The replacement bundle drops references to:

- Any pre-split monolithic Vera module (now `voxera.vera/` +
  `voxera.vera_web/`).
- Any pre-mixin queue daemon (the daemon is composed via
  `QueueApprovalMixin`, `QueueRecoveryMixin`, `QueueExecutionMixin`).
- Any prior single-file panel routing (now one module per surface).
- Any prior centralized intent classifier (now split into
  `simple_intent`, `file_intent`, `code_draft_intent`,
  `writing_draft_intent`, `queue_job_intent`).
