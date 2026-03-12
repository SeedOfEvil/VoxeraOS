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

Expected artifact families are part of the declaration contract and should be comparable against produced runtime evidence.

## 8) Relationship to queue/object model and policy gates

Execution governance flow:

1. Planner/role reasoning proposes work.
2. Queue submission creates canonical execution contract.
3. Policy (`allow` / `ask` / `deny`) evaluates declared capabilities.
4. Runtime executes within declared scope/isolation posture.
5. Artifacts/evidence become outcome truth for verifier/reviewer.

This model is additive to current behavior and is intended to make permissions explicit before expanding capability/skill surface area.
