# Execution Security Model (Canonical)

This document defines the canonical execution security contract for VoxeraOS.

It formalizes execution as a governed control-plane decision based on declared capability needs, explicit isolation posture, and post-execution evidence.

## Scope and invariants

This model preserves existing architectural invariants:

- Queue is the canonical execution contract after submit.
- Policy remains the allow / ask / deny gate.
- Preview remains authoritative only before submit.
- Artifacts/evidence remain post-execution truth.
- Fail-closed remains the default posture.

See also:
- `docs/QUEUE_OBJECT_MODEL.md`
- `docs/prompts/03-runtime-technical-overview.md`
- `docs/prompts/capabilities/queue-object-model.md`
- `docs/prompts/capabilities/artifacts-and-evidence.md`

## 1) Execution trust classes / side-effect classes

Voxera classifies execution surfaces into coarse trust classes:

- **Class A** (pure/read-mostly helpers)
  - deterministic or low-side-effect operations
  - no network by default
  - no secrets by default
  - no broader host writes
- **Class B** (scoped host interaction)
  - controlled host interaction, writes, or execute effects
  - explicit filesystem/network scope required
  - governed by policy and approval where needed
- **Class C** (high-risk mutation)
  - broad side effects or high-impact actions
  - stronger policy/approval posture
  - explicit operator scrutiny expected

Class labels are a governance aid; they do not bypass capability-level policy checks.

## 2) Capability declaration model

A skill/tool should be representable as an explicit capability declaration with at least:

- `side_effect_class`
- `needs_network`
- `network_scope`
- `allowed_domains`
- `fs_scope`
- `allowed_paths`
- `secret_refs`
- `sandbox_profile`
- `expected_artifacts`

Not every current skill must populate every field immediately. The contract is canonical; rollout can be incremental.

## 3) Filesystem scope model

Filesystem scope is explicit and intentionally coarse:

- **none**: no filesystem reads/writes required
- **confined**: access limited to approved bounded paths (for example job workspace scope)
- **broader**: broader host scope; higher governance posture

`confined` is the preferred default where filesystem interaction is required.

## 4) Network scope model

Network scope must be explicit:

- **none**: no network access
- **read_only**: constrained read/intake usage where applicable
- **broader**: mutation-sensitive and/or broad egress capability

`needs_network=true` alone is not a sufficient long-term contract. Domain/purpose scoping is preferred where practical.

## 4.1) Built-in skill manifest baseline

To keep approval/review outputs comparable, built-in skills should declare a consistent baseline metadata set in `manifest.yml`:

- `exec_mode`
- `needs_network`
- `fs_scope`
- `output_schema` (`skill_result.v1` for current built-ins)
- `output_artifacts` (deterministic list; `[]` allowed when there are no deterministic file artifacts)

Current conventions in this repository:

- read-mostly local skills use `fs_scope=read_only`
- confined notes/file skills use `fs_scope=workspace_only`
- broad browser/network skills use `needs_network=true` with `fs_scope=broader`
- sandbox skills remain explicit and may declare deterministic runtime artifact outputs

This baseline improves governance comparability without broadening runtime permissions.

## 5) Secret access model

Secrets are explicit capability requirements, not ambient permissions:

- secret needs must be declared (`secret_refs`)
- scope should be minimal and per-skill/per-step where feasible
- no global ambient secret capability by default
- secret declaration is part of execution contract and review context

## 6) Sandbox / isolation model

Isolation is a defense layer, not the only control:

- sandbox profile selection is part of capability declaration
- no-network sandbox profiles are preferred by default where practical
- rootless/containerized isolation is preferred when sandboxing is used
- policy/approval/evidence still govern outcomes regardless of sandbox mode

## 7) Verifier expectations

Verifier/reviewer should be able to answer:

- What side effects were allowed?
- What side effects were attempted?
- What artifacts were expected?
- What evidence proves execution stayed in bounds?

Canonical verifier-facing execution artifacts should rely on the normalized contract in
`execution_result.json`:
- `artifact_families`
- `artifact_refs`
- `review_summary` (including execution capability declaration visibility and expected artifact observation status)
- `evidence_bundle` (including `trace` linkage fields and expected artifact observation payload)

Expected artifact families are part of the declaration contract and should be deterministically compared against produced runtime evidence as `observed`, `partial`, or `missing`.

For forward-created jobs, canonical queue/assistant lanes should populate expected-artifact declarations during submission or daemon normalization; historical jobs without declarations remain valid (`none_declared`) and are not backfilled.

## 8) Relationship to queue/object model and policy gates

Execution governance flow:

1. Planner/role reasoning proposes work.
2. Queue submission creates canonical execution contract.
3. Policy (`allow` / `ask` / `deny`) evaluates declared capabilities.
4. Runtime executes within declared scope/isolation posture.
   - Deterministic fail-closed guard: if runtime arguments request network access while declared `network_scope=none`/`needs_network=false`, execution is blocked as a capability boundary mismatch before launch.
5. Artifacts/evidence become outcome truth for verifier/reviewer (including explicit capability-boundary violation details when present).

This model is additive to current behavior and is intended to make permissions explicit before expanding capability/skill surface area.


## 9) Queue-first direct CLI mutation gate

Direct CLI execution (`voxera run <skill_id>`) is gated by the skill's mutation posture:

- **Read-only skills** (all declared capabilities have `read` effect class): execute directly.
- **Mutating skills** (any capability has `write` or `execute` effect class): blocked by default.

Blocked direct mutation runs print an actionable message explaining:
- Which skill was blocked and why.
- What effect classes triggered the gate.
- How to use the governed queue path instead.
- How to use the explicit dev-mode override.

### Dev-mode override

For development workflows, mutating skills can be executed directly with both:
1. `VOXERA_DEV_MODE=1` environment variable set, **and**
2. `--allow-direct-mutation` CLI flag passed.

Both are required. The override logs a visible warning. This is intentionally loud and explicit — it is not the product trust path.

### Dry-run is unaffected

`voxera run <skill_id> --dry-run` bypasses the mutation gate for all skills, since dry-run does not execute.

### Classification

Skill mutability is determined by the canonical `CAPABILITY_EFFECT_CLASS` mapping in `policy.py`. A skill is read-only only when every declared capability maps to `read`. Skills with no declared capabilities are treated as non-read-only (fail-closed).

## Filesystem productivity pack boundary (waves 1–2)

- `files.list_dir`, `files.exists`, and `files.stat` are read-only inspection skills (`fs_scope=read_only`, local-only, no network).
- `files.copy_file`, `files.move_file`, `files.mkdir`, and `files.delete_file` are confined mutation skills (`fs_scope=workspace_only`, local-only, no network).
- All file skills remain constrained to allowlisted notes-root path normalization and fail closed on boundary violations.

## System inspection skills boundary

- `system.status`, `system.disk_usage`, `system.process_list`, and `system.window_list` are read-only system inspection skills (`fs_scope=read_only`, `state.read` / `window.read` capability, local-only, no network).
- `system.disk_usage` reads disk usage for the home partition via `shutil.disk_usage` (no shell commands).
- `system.process_list` reads process state via `ps -eo pid,user,%cpu,%mem,comm` (bounded, read-only, output truncated to 50 entries).
- The `system_inspect` mission composes all four into a single bounded queue-backed diagnostic workflow that produces canonical evidence without mutations or approval requirements.
