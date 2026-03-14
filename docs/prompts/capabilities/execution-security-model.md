# Capability: Execution Security Model

Execution permissions are explicit, not ambient.

When reasoning about skills/tools, model each as declared capabilities including:
- side-effect class
- filesystem scope
- network scope
- secret needs
- sandbox/isolation profile
- expected artifact families
- For built-in skills, expect a consistent manifest baseline (`exec_mode`, `needs_network`, `fs_scope`, `output_schema`, `output_artifacts`) so approval/review scope comparisons stay deterministic.

## Runtime governance worldview

- Queue is authoritative after submit.
- Policy (`allow` / `ask` / `deny`) remains the gate.
- High-risk or broad-side-effect actions require stronger governance.
- Isolation helps contain risk but does not replace policy/evidence review.

## Reviewer/verifier worldview

Expected artifacts are part of the contract. Outcome claims should align:
- declared scope and side-effect posture
- attempted actions
- produced artifacts/evidence

Do not treat undeclared capability assumptions as valid execution authority.

- Apply expected-artifact declaration improvements forward only; historical jobs are not backfilled and may remain `none_declared`.

- Runtime must fail closed on deterministic declaration mismatches. Current enforced example: runtime `network=true` request with declared `network_scope=none` is blocked before execution and reported as `capability_boundary_mismatch`.
