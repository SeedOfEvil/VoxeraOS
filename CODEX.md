# Codex Working Memory Pointer

Primary long-term memory for shipped changes is tracked in:
- `docs/CODEX_MEMORY.md`

This `CODEX.md` file exists as a stable entry point for contributors who expect a root-level Codex memory file.

## Current focus
- Keep queue failed-artifact reliability stable (schema-versioned sidecars, strict validation, deterministic retention pruning).
- Surface and monitor `queue_failed_sidecar_invalid` events in operator workflows/panels.
- Preserve mission planner guardrails (known skills + JSON-only outputs) and overall auditability.
