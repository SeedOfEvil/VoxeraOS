# Codex Working Memory Pointer

Primary long-term memory for shipped changes is tracked in:
- `docs/CODEX_MEMORY.md`

This `CODEX.md` file exists as a stable entry point for contributors who expect a root-level Codex memory file.

## Project context

VoxeraOS is an open-source alpha (v0.1.8) queue-driven AI control plane for Linux. Vera is the conversational intelligence layer; VoxeraOS is the trust, policy, execution, and evidence layer.

**Provider support:** OpenRouter is the only officially tested and fully built provider path. Gemini 3 Flash is the current minimum supported requirement.

## Current focus

Post-v0.1.8 work is focused on hardening Vera as a stable conversational control layer and preparing for the v0.1.9 governed capability expansion milestone. See `docs/ROADMAP.md` for the milestone themes.

## Hardening already shipped (do not re-implement)

These are done. Do not list them as open work:

- Queue-first direct CLI mutation gate: mutating `voxera run` blocked by default; read-only direct CLI preserved; explicit dev-mode override via `VOXERA_DEV_MODE=1` + `--allow-direct-mutation`
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
- Queue job intent normalization should preserve explicit, forward-looking expected-artifact declarations for canonical lanes so runtime/review can compare expected vs observed artifacts deterministically.
- Linked Vera completion ingestion + deterministic chat surfacing slice: deterministic session-linked tracking, terminal completion payload extraction, and one-per-chat-cycle auto-surface for unsurfaced linked completions.
