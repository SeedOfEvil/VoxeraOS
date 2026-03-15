# Codex Working Memory Pointer

Primary long-term memory for shipped changes is tracked in:
- `docs/CODEX_MEMORY.md`

This `CODEX.md` file exists as a stable entry point for contributors who expect a root-level Codex memory file.

## Current focus (v0.2 build-out)

Post-v0.1.6 hardening and observability work is complete (PRs #145–#149). Current development focus is the v0.2 milestone:

- **Mission catalog expansion**: document 10+ production-usable missions in `missions/` with manifests, test data, and validated `--dry-run` smoke paths.
- **`voxera skills validate` command**: eager manifest validation at the CLI level without daemon launch; `skill_manifest_invalid` audit events; surface in `voxera doctor` (partial: `skills.registry` row already ships in doctor).
- **Built-in skill metadata baseline discipline**: keep manifests aligned on governance fields (`exec_mode`, `needs_network`, `fs_scope`, `output_schema`, `output_artifacts`) so approval/review capability surfaces remain comparable before wider skill-pack expansion.
- **Filesystem productivity waves 1–2 (bounded)**: additive `files.list_dir`, `files.copy_file`, `files.move_file`, `files.mkdir`, `files.exists`, `files.stat`, and `files.delete_file` with confined notes-root path enforcement and deterministic skill-result payload evidence.
- **Bounded filesystem planner routing**: deterministic `file_intent.py` classifier routes natural-language file requests into bounded file skills and `file_organize` contracts via Vera handoff, eliminating generic fallback for clear file intents.
- **System inspection workflow**: `system_inspect` mission composes `system.status`, `system.disk_usage`, `system.process_list`, and `system.window_list` into a bounded read-only diagnostic workflow that executes through the queue for canonical audit evidence.
- **LLM rate limiter**: token-bucket around `brain.generate()` calls; default 30 RPM; configurable via `VOXERA_BRAIN_RATE_LIMIT_RPM`; `brain_rate_limited` audit events.
- **E2E test environment**: Podman + Xvfb for clipboard and window-management skill testing; `make e2e-full` target.
- **Provider UX improvements**: keyring availability shown at setup start; credential test before save; named provider profile presets.
- **Panel-first UX**: mobile-responsive layout; full mission authoring from panel; template picker. UX productization pass shipped (PR #TBD): send-state management, humanized labels, semantic badge coloring, bubble-based Vera chat layout — see CODEX_MEMORY.md for full scope.
- **Artifact/evidence contract hardening**: keep execution outputs review/verifier-friendly via additive normalized `execution_result.json` contract fields (`artifact_families`, `artifact_refs`, `review_summary`, `evidence_bundle.trace`).
- **Verifier review hardening**: keep "what happened?" review deterministic and lifecycle-aware by grounding summary/next-step shaping on canonical queue state + normalized review/evidence contract fields (including explicit `queued` vs `submitted` handling).
- **State-aware expected-artifact review**: keep reviewer guidance explicit for fully observed/partial/missing/none-declared outputs without conflating approval-blocked/canceled states with runtime failure.
- **Normalized non-success taxonomy**: keep reviewer/operator explanations explicit via additive `normalized_outcome_class` shaping (`approval_blocked`, `policy_denied`, `capability_boundary_mismatch`, `path_blocked_scope`, `runtime_dependency_missing`, `runtime_execution_failed`, `canceled`, `partial_artifact_gap`, `incomplete_evidence`) while preserving canonical queue truth.

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

- Linked Vera completion ingestion + first chat surfacing slice: deterministic session-linked tracking, terminal completion payload extraction, and one-per-chat-cycle auto-surface for unsurfaced linked `read_only_success` completions only (others remain manual), grounded in queue/artifact truth.
