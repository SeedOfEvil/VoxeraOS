# Voxera OS Alpha v0.1.5 — Artifacts Hygiene + Retention CLI (SHIPPED)

> **Historical snapshot:** This file captures roadmap framing at the time it was written and is not the current source of truth. For current milestone state, use `docs/ROADMAP.md`.


**Status: complete.** This release adds operator-grade artifact hygiene on top of the v0.1.4 stability baseline.

For the next phase of work, see `docs/ROADMAP.md`.
For the previous release, see `docs/ROADMAP_0.1.4.md`.

---

## What shipped in v0.1.5

### Version bump
- Bumped `pyproject.toml` version from `0.1.4` to `0.1.5`.
- Updated `README.md` and `docs/ROADMAP.md` to reflect the new baseline.

### `voxera artifacts prune` CLI
- New `voxera artifacts prune` subcommand under the `artifacts` command group.
- **Dry-run by default** — no deletion occurs without `--yes`.
- `--max-age-days <int>`: prune entries older than N days.
- `--max-count <int>`: keep newest N entries, prune the rest.
- `--yes`: execute deletions (without it, only a dry-run preview is shown).
- `--json`: emit machine-readable JSON summary.
- `--queue-dir <path>`: override queue root (defaults to `~/VoxeraOS/notes/queue`).
- Union selection policy: an artifact is pruned if it exceeds *either* the age rule or the count rule.
- Safe defaults: if neither flags nor config is set, prints "no pruning rules configured" and exits 0.
- Graceful missing-dir handling: if `artifacts/` does not exist, prints a helpful message and exits 0.
- Safety: path `realpath` checks prevent escaping the artifacts root via symlinks.
- Symlink handling: deletes the link itself only, never follows.
- Reclaimed bytes estimate included in output summary (best-effort directory walk).

### Runtime config additions
- `artifacts_retention_days: int | None` — sets default max age for prune in days.
- `artifacts_retention_max_count: int | None` — sets default max count to keep.
- Both override-able per-invocation via CLI flags.
- Env vars: `VOXERA_ARTIFACTS_RETENTION_DAYS`, `VOXERA_ARTIFACTS_RETENTION_MAX_COUNT`.

---

## Acceptance criteria (all met)

### `voxera artifacts prune`
- ✅ Default (no `--yes`) is always dry-run; no files deleted accidentally.
- ✅ `--max-age-days 1` selects artifacts older than 1 day in a seeded temp directory.
- ✅ `--max-count 2` with 4 entries selects 2 for pruning (newest-first ordering).
- ✅ `--yes` performs deletion and `reclaimed_bytes > 0` for non-empty entries.
- ✅ Missing artifacts directory exits 0 with a helpful message (no crash).
- ✅ No flags and no config: exits 0 with "no pruning rules configured".
- ✅ `--json` emits parseable JSON with `status`, `total_candidates`, `pruned_count`, `reclaimed_bytes`.

### Quality gates
- ✅ `ruff format` + `ruff check` — clean.
- ✅ `mypy src/voxera tests` — no new errors beyond ratchet baseline.
- ✅ `pytest -q` — all tests pass including 7 new artifact-prune tests.

---

## Release checklist (completed)

- ✅ `voxera --version` reports `0.1.5`.
- ✅ `voxera artifacts prune --help` shows correct flags and docstring.
- ✅ Dry-run smoke: `voxera artifacts prune --queue-dir /tmp/test-queue`.
- ✅ JSON smoke: `voxera artifacts prune --queue-dir /tmp/test-queue --json`.
- ✅ Quality gate: `make merge-readiness-check` (or equivalent ruff/mypy/pytest).

---

## Known gaps carried forward to v0.2

- Artifact cleanup is not yet tied to failed-job retention pruner (when a failed job is deleted, its artifact dir is not automatically removed in the same pass). Tracked in `docs/ROADMAP.md` Day 1.
- `voxera queue prune` command (for failed job files) is not yet implemented. Tracked in `docs/ROADMAP.md` Day 2.
- `make type-debt` target not yet added. Tracked in `docs/ROADMAP.md` Day 1.
