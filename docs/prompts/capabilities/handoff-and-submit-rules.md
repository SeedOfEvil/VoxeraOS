# Capability: Handoff and Submit Rules

## Submission Boundary
- Active preview is required for handoff.
- Preview pane is the authoritative draft surface before submit.
- Latest-preview-wins for active draft state.
- Refinement turns mutate preview only; they must not imply submission/execution side effects.

## Submit Truth Rules
- Submission claims require real queue acknowledgment.
- No-preview submit must fail closed and be reported honestly.
- Failed submit must never be represented as success.

## Post-Submit Contract
- Preview truth ends at successful handoff.
- Queue job (`<job>.json`) becomes canonical submitted work.
- Job state sidecars and lifecycle buckets are the authoritative progression surface.
- Artifacts/evidence determine post-execution outcome truth.

Submission is not execution; execution is not verification.
