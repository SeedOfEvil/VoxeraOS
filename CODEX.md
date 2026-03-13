# Codex Working Memory Pointer

Primary long-term memory for shipped changes is tracked in:
- `docs/CODEX_MEMORY.md`

This `CODEX.md` file exists as a stable entry point for contributors who expect a root-level Codex memory file.

## Current focus (v0.2 build-out)

Post-v0.1.6 hardening and observability work is complete (PRs #145–#149). Current development focus is the v0.2 milestone:

- **Mission catalog expansion**: document 10+ production-usable missions in `missions/` with manifests, test data, and validated `--dry-run` smoke paths.
- **`voxera skills validate` command**: eager manifest validation at the CLI level without daemon launch; `skill_manifest_invalid` audit events; surface in `voxera doctor` (partial: `skills.registry` row already ships in doctor).
- **LLM rate limiter**: token-bucket around `brain.generate()` calls; default 30 RPM; configurable via `VOXERA_BRAIN_RATE_LIMIT_RPM`; `brain_rate_limited` audit events.
- **E2E test environment**: Podman + Xvfb for clipboard and window-management skill testing; `make e2e-full` target.
- **Provider UX improvements**: keyring availability shown at setup start; credential test before save; named provider profile presets.
- **Panel-first UX**: mobile-responsive layout; full mission authoring from panel; template picker.
- **Artifact/evidence contract hardening**: keep execution outputs review/verifier-friendly via additive normalized `execution_result.json` contract fields (`artifact_families`, `artifact_refs`, `review_summary`, `evidence_bundle.trace`).
- **Verifier review hardening**: keep "what happened?" review deterministic and lifecycle-aware by grounding summary/next-step shaping on canonical queue state + normalized review/evidence contract fields (including explicit `queued` vs `submitted` handling).

## Hardening already shipped (do not re-implement)

These are done. Do not list them as open work:

- Simple-intent routing + fail-closed mismatch detection (PR #144–#145)
- Live job/assistant progress endpoints + stale-failure fix (PR #146)
- Red-team regression suite + multi-boundary traversal hardening (PR #147)
- Queue lineage metadata: additive, observational (PR #148)
- Controlled child enqueue primitive: single child, server-side lineage, fail-closed (PR #149)
- Goal string sanitization + 2,000-char cap + `[USER DATA START]`/`[USER DATA END]` delimiters (PRs #85, #88)
- Panel auth lockout: 10 failures/60s → HTTP 429 (PR #89)
- Daemon health degradation tracking + brain backoff ladder (P3.1, P3.2 — shipped)
- Panel hygiene page + recovery inspector (PRs #92, #93)
- `sandbox.exec` argv canonicalization (PR #91)
- Queue startup recovery + graceful SIGTERM (PRs #80, #81)
- Doctor `skills.registry` row (shipped)

## Key invariants to preserve

- Fail-closed: when uncertain, Voxera fails closed. No degraded-but-executing mode.
- Queue is the system boundary: all execution flows through the queue with lifecycle visibility.
- Additive artifact design: new fields are additive; existing jobs remain readable.
- Policy/approval gates are not bypassable by metadata, lineage, or child enqueue payloads.
- Merge gate: `make merge-readiness-check` includes `security-check` — all 17 red-team tests must pass.
