# Codex Working Memory Pointer

Primary long-term memory for shipped changes is tracked in:
- `docs/CODEX_MEMORY.md`

This `CODEX.md` file exists as a stable entry point for contributors who expect a root-level Codex memory file.

## Current focus
- Prompt-injection hardening: goal string sanitization (2,000 char length cap), structural `[USER DATA: ...]` delimiters in LLM preamble, regression tests for injection-shaped inputs.
- Ops visibility in panel: surface reconcile/prune/recovery/fallback/lock/shutdown status on panel home dashboard.
- Long-run daemon behavior: health degradation tracking (consecutive failures → degraded state), backoff on repeated brain failures, structured shutdown outcome in `voxera queue health`.
- Maintain daemon reliability guarantees (single-writer lock, graceful SIGTERM, deterministic startup recovery) as scope expands.
