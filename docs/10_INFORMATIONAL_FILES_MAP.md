# 10 — Informational Files Map

This document maps the rest of the `docs/` tree — the long-form architecture docs, ops playbooks, prompt material, and roadmap files. It complements `01_REPOSITORY_STRUCTURE_MAP.md` by listing files that are informational rather than structural.

All paths are relative to the repo root. Everything listed here exists at the time this bundle was regenerated (see `directory_tree.txt` for the mechanical listing).

## Top-level docs under `docs/`

### Architecture and reference

- **`docs/ARCHITECTURE.md`** — long-form architecture map (component boundaries, queue/panel/Vera split, data flow). Canonical architectural overview.
- **`docs/QUEUE_OBJECT_MODEL.md`** — canonical queue object model contract. Longer-form counterpart to `03_QUEUE_OBJECT_MODEL_AND_LIFECYCLE.md`.
- **`docs/QUEUE_CONSTITUTION.md`** — frozen canonical queue contracts (payload schema, request kinds, lifecycle states, terminal outcomes, artifact guarantees, result interpretation).
- **`docs/EXECUTION_SECURITY_MODEL.md`** — canonical execution security contract (trust classes, capability declarations, filesystem/network scopes, secret model, sandbox model, verifier expectations, direct-CLI mutation gate).

### Operations and security

- **`docs/ops.md`** — day-2 operations reference: services, queue health, hygiene, recovery, incident bundles, failure runbooks.
- **`docs/SECURITY.md`** — security posture, threat model, hardening notes.
- **`docs/UBUNTU_TESTING.md`** — Ubuntu-specific testing notes (systemd user services, desktop integration, sandbox exec).
- **`docs/BOOTSTRAP.md`** — minimal bootstrap guide.
- **`docs/LOCAL_MODELS.md`** — local model / Ollama notes (architecturally supported, not extensively validated).

### Vision and roadmap

- **`docs/NORTH_STAR.md`** — product direction (Vera = intelligence, Voxera OS = trust layer). The bundle's `North-Star.txt` is the operator-facing version kept in sync with this file.
- **`docs/ROADMAP.md`** — current milestone roadmap.
- **`docs/ROADMAP_0.1.4.md`** — shipped scope for v0.1.4 (stability + UX baseline).
- **`docs/ROADMAP_0.1.5.md`** — shipped scope for v0.1.5 (artifacts prune).
- **`docs/ROADMAP_0.1.6.md`** — shipped scope for v0.1.6.
- **`docs/CODEX_MEMORY.md`** — implementation history / PR changelog / long-form operational memory.
- **`docs/HOTSPOT_AUDIT_EXTRACTION_ROADMAP.md`** — bounded extraction roadmap for the Vera / panel / queue CLI hotspot files (`vera_web/app.py`, `panel/app.py`, `cli_queue.py`).

### Prompts (`docs/prompts/`)

Vera / planner / verifier prompt material, used during runtime and during content authoring.

- `docs/prompts/00-system-overview.md`
- `docs/prompts/01-platform-boundaries.md`
- `docs/prompts/02-role-map.md`
- `docs/prompts/03-runtime-technical-overview.md`
- `docs/prompts/roles/vera.md`
- `docs/prompts/roles/planner.md`
- `docs/prompts/roles/verifier.md`
- `docs/prompts/roles/web-investigator.md`
- `docs/prompts/roles/hidden-compiler.md`
- `docs/prompts/capabilities/queue-object-model.md`
- `docs/prompts/capabilities/queue-lifecycle.md`
- `docs/prompts/capabilities/preview-payload-schema.md`
- `docs/prompts/capabilities/handoff-and-submit-rules.md`
- `docs/prompts/capabilities/artifacts-and-evidence.md`
- `docs/prompts/capabilities/execution-security-model.md`
- `docs/prompts/capabilities/web-investigation-rules.md`
- `docs/prompts/capabilities/hidden-compiler-payload-guidance.md`

These files are the authoritative in-tree versions of Vera / planner / verifier prompt content. The runtime prompt strings in `src/voxera/vera/prompt.py` should stay aligned with these docs.

### Testing material (`docs/testing/`)

- **`docs/testing/RUNTIME_VALIDATION_PLAYBOOK.md`** — the canonical runtime validation playbook used for meaningful PRs.
- **`docs/testing/VERA_REGRESSION_PACK.md`** — Vera-specific regression test pack with expected flows.
- **`docs/testing/payloads/`** — reusable direct-queue JSON payloads:
  - `system-inspect.json` — mission_id test
  - `inline-exists-test.json` — inline steps test
  - `blocked-list-dir.json` — fail-closed queue subtree test
  - `delete-approval-test.json` — approval-gated test
  - `missing-source-copy.json` — canonical failure test

These payloads are the reference library for the direct-queue test method. See `Mission-testing-and-building-CLI.txt` in this bundle.

## This bundle (`docs/00_*.md` – `docs/10_*.md` + inventories + text files)

| File | Role |
|---|---|
| `00_MASTER_INDEX.md` | Entry point / bundle map |
| `01_REPOSITORY_STRUCTURE_MAP.md` | Repo layout + module ownership |
| `02_CONFIGURATION_AND_RUNTIME_SURFACES.md` | Config, services, ports, env vars |
| `03_QUEUE_OBJECT_MODEL_AND_LIFECYCLE.md` | Queue payload + lifecycle |
| `04_GOAL_MISSION_PLANNING_AND_EXECUTION.md` | Missions, planner, request kinds |
| `05_VERA_CONTROL_LAYER_AND_HANDOFF.md` | Vera layer + submit + review |
| `06_SKILLS_CAPABILITIES_AND_BUILTINS.md` | Skills, capabilities, built-ins |
| `07_ARTIFACTS_EVIDENCE_AND_REVIEW_MODEL.md` | Artifacts + evidence + review |
| `08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md` | Tests, Make targets, change-surface map |
| `09_CORE_OBJECTS_AND_SCHEMA_REFERENCE.md` | Data shapes reference |
| `10_INFORMATIONAL_FILES_MAP.md` | This file |
| `root_inventory.json` | Repo root entries |
| `directory_tree.txt` | Live bounded directory tree |
| `module_inventory.json` | Python modules under `src/voxera` |
| `class_inventory.json` | Public top-level classes |
| `function_inventory.json` | Public top-level functions |
| `mission_inventory.json` | Built-in + file-based missions |
| `skill_manifest_inventory.json` | Skill manifests |
| `Quick-Start.txt` | Fastest path to a running stack |
| `North-Star.txt` | Product direction (operator-facing) |
| `Testing-Method.txt` | Multi-layer test ladder |
| `Mission-testing-and-building-CLI.txt` | Direct queue JSON test method |
| `Coding-Agent-Prompt-Example.txt` | Coding-agent session prompt example |
| `VoxeraOS-Project-Technical-Rundown-March-23rd.txt` | Technical overview (filename kept; body updated to current reality) |
| `March-25-2026-VoxeraOS-—-Engineering-Report.txt` | Engineering status (filename kept; body updated to current reality) |

## Conventions for updating docs

A few conventions that make keeping this bundle accurate easier:

1. **Inventories are generated, not hand-written.** If you add or remove a module, a class, a mission, or a skill manifest, re-run the scan that produced `docs/*.json` / `docs/directory_tree.txt`. Treat the JSON files as derived artifacts.
2. **Canonical long-form contracts live in `docs/QUEUE_OBJECT_MODEL.md`, `docs/QUEUE_CONSTITUTION.md`, `docs/EXECUTION_SECURITY_MODEL.md`, and `docs/ARCHITECTURE.md`.** The bundle summarizes them — it does not replace them.
3. **Text files in this bundle are operator-facing.** Keep them readable, direct, and short. Prefer actual commands over narrative when describing a workflow.
4. **If a file no longer fits its original framing, keep the filename and update the body.** The two dated text files (`VoxeraOS-Project-Technical-Rundown-March-23rd.txt`, `March-25-2026-VoxeraOS-—-Engineering-Report.txt`) are intentionally preserved with their filenames even after a full rewrite — they serve as recognizable waypoints for long-running operators.
5. **Never add aspirational content to this bundle.** If something is transitional or rough, say so. The bundle's value comes from matching reality.
