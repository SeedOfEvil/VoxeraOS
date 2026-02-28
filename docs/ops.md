# Operations (Alpha)

This guide covers day-2 operations for VoxeraOS in service mode.

## Install services

From repository root:

```bash
make dev
make fmt-check
make check
make services-install
make daemon-restart
```

This installs user units from `deploy/systemd/user/` into `~/.config/systemd/user`, rendering
`WorkingDirectory` and `ExecStart` to your **current checkout path**, then reloads systemd user state
and enables/starts:
- `voxera-daemon.service`
- `voxera-panel.service`


## Config + env locations

- Use `.env.example` as the template for local values.
- Keep secrets (for example `VOXERA_PANEL_OPERATOR_PASSWORD`) in `~/.config/voxera/env` when possible.
- `.env` in the repo is intentionally gitignored for local overrides.
- Print the effective redacted runtime snapshot with:

```bash
.venv/bin/voxera config show
```

- Persist a redacted config snapshot for incident tooling with:

```bash
.venv/bin/voxera config snapshot
```

This writes `notes/queue/_ops/config_snapshot.json` (or `<VOXERA_QUEUE_ROOT>/_ops/config_snapshot.json`) with `settings`, `sources`, `written_at_ms`, and `schema_version=1`, plus `_ops/config_snapshot.sha256`.

Config precedence: CLI overrides > `VOXERA_*` env > `~/.config/voxera/config.json` > built-in defaults.

For deterministic local/CI tests, Make targets run pytest with `VOXERA_LOAD_DOTENV=0` and unset key `VOXERA_*` vars so shell exports and repo `.env` do not alter test outcomes.

Key runtime env vars (defaults):
- `VOXERA_QUEUE_ROOT` (`~/VoxeraOS/notes/queue`)
- `VOXERA_PANEL_HOST` (`127.0.0.1`)
- `VOXERA_PANEL_PORT` (`8844`, `make panel` uses `8787`)
- `VOXERA_PANEL_OPERATOR_USER` (`admin`)
- `VOXERA_PANEL_CSRF_ENABLED` (`1`/true by default)
- `VOXERA_QUEUE_LOCK_STALE_S` (`3600`)
- `VOXERA_OPS_BUNDLE_DIR` (unset => timestamped `_archive/` path)
- When using `voxera ops bundle ... --queue-dir`, default archive output stays under that queue root (`<queue_dir>/_archive/<timestamp>/`); use `--dir` to override explicitly.


Daemon startup writes a fresh redacted `_ops/config_snapshot.json`, `_ops/config_snapshot.sha256`, and baseline `_ops/config_snapshot.last.sha256`. If the effective redacted config fingerprint changes since previous daemon start, Voxera emits exactly one structured audit event (`config_drift_detected`) and writes `config_drift_note.txt` with timestamp and old/new fingerprints. Secrets are never included in these files/events.

Note: systemd user services do not inherit ad-hoc shell exports by default; configure env via Voxera runtime env files/service unit overrides if drift behavior seems unexpected.
## Queue contract + intake flow

Use `voxera inbox` as the human-friendly front door for queued goals. Ensure queue folders exist once per machine:

```bash
voxera queue init
```

Queue directory contract (`~/VoxeraOS/notes/queue`):
- `inbox/` (**only intake**, daemon consumes `inbox/*.json`)
- `pending/`
- `pending/approvals/`
- `done/`
- `failed/`
- `artifacts/`
- `_archive/`

Backwards-compatible safety behavior:
- Legacy drops in `notes/queue/*.json` are auto-relocated to `inbox/` with audit event `queue_job_autorelocate`.
- Mis-dropped primary jobs in `notes/queue/pending/*.json` are auto-relocated to `inbox/` (no silent stuck jobs).

Queue producer best practice (atomic write + rename):
```bash
queue_dir=~/VoxeraOS/notes/queue
inbox_dir="$queue_dir/inbox"
mkdir -p "$inbox_dir"
job_id=job-$(date +%s)
tmp_path="$inbox_dir/.${job_id}.tmp"
final_path="$inbox_dir/${job_id}.json"
printf '{"goal":"run a quick system check"}
' > "$tmp_path"
mv "$tmp_path" "$final_path"
```

Operational commands:
```bash
voxera inbox add "Write a daily check-in note with priorities and blockers"
voxera daemon --once
voxera queue status
voxera queue cancel <job_id_or_filename>
voxera queue retry <job_id_or_filename>
voxera queue pause
voxera queue resume
voxera queue unlock           # safe: stale/orphaned (dead pid) locks only
voxera queue unlock --force   # override live lock (dangerous)
voxera queue health           # summary from notes/queue/health.json
voxera queue lock status      # lock table alias (same lock fields as queue health)
```

Operational effects:
- `queue cancel` moves matching jobs (inbox/pending/pending approvals/in-flight best effort) into `failed/` with sidecar `error="cancelled by operator"` and cleans pending approval markers.
- `queue retry` re-queues a failed primary payload into `inbox/` and emits `queue_job_retry` audit event linking old/new attempt.
- `queue pause` creates `.paused`; daemon still reports status but skips processing new jobs until `queue resume` removes marker.
- Daemon run loop acquires `notes/queue/.daemon.lock` to prevent multi-consumer races. Stale locks are reclaimed after `VOXERA_QUEUE_LOCK_STALE_S` (default 3600s); use `voxera queue unlock` for safe stale/orphaned lock recovery; if lock is live, stop daemon first or use `voxera queue unlock --force` as an explicit override.

Panel operator notes:
- Panel shows a **Setup required** banner on `/` and `/jobs` when `VOXERA_PANEL_OPERATOR_PASSWORD` is unset; guidance includes systemd user env + restart commands.
- Panel mutation routes (`/queue/create`, `/missions/create`, `/panel/missions/create`) accept `POST` by default.
- Panel operator mutations now require HTTP Basic auth and CSRF validation. Set `VOXERA_PANEL_OPERATOR_PASSWORD` (and optional `VOXERA_PANEL_OPERATOR_USER`, default `admin`) before starting the panel.
- Optional GET mutation compatibility is disabled by default (HTTP 405) and can be enabled for test/dev only with `VOXERA_PANEL_ENABLE_GET_MUTATIONS=1`.
- Panel home shows pause/resume, cancel/retry actions, and links Done/Failed jobs to artifact-backed detail pages.
- Panel home has a **Create Mission** card with Easy / Default / Advanced modes that all create the same base queue job schema (`job_version`, `mission_id`, `created_ts_ms`, `source=panel`, `prompt`, `approval_required`), with optional advanced metadata (`brain`, `priority`, `tags`, `dry_run`, `target`).
- Successful Create Mission submits redirect to `/jobs` with a success banner and job filter pre-filled to the created job.
- Queue daemon + panel update a shared lightweight snapshot at `notes/queue/health.json`.
  - Write pattern is atomic (`health.json.tmp` then rename).
  - `last_ok_event` + `last_ok_ts_ms` indicate recent successful activity (tick/lock/shutdown release).
  - `last_error` + `last_error_ts_ms` capture latest concise failure context.
  - Concurrent writes use read-modify-write and may rarely lose an increment under heavy contention; counters are still suitable for operational trend visibility.

### Incident runbook: daemon lock + panel auth/CSRF

1. **Daemon will not start (`QueueLockError`)**
   ```bash
   voxera queue status
   voxera audit | rg "queue_daemon_lock_"
   ```
   - Check `lock_acquire_fail` and `lock_reclaimed` counters for contention/recovery patterns.
   - Confirm whether another daemon process is active before intervention.

2. **Lock appears stuck**
   ```bash
   voxera queue unlock
   ```
   - Safe mode only removes stale/dead PID locks.
   - If lock is held by a live PID, stop that daemon first.
   - Emergency only:
     ```bash
     voxera queue unlock --force
     ```

3. **Panel mutations failing with 401/403**
   - Ensure `VOXERA_PANEL_OPERATOR_PASSWORD` is set in the running panel environment.
   - For 401, verify Basic auth user/password.
   - For 403, verify CSRF cookie + token are both present and matching.
   ```bash
   voxera audit | rg "panel_(auth_missing|auth_invalid|csrf_missing|csrf_invalid|mutation_allowed|operator_config_error)"
   voxera queue health
   voxera doctor --quick   # marks last_error as stale when last_ok is newer by >5m
   ```
   - Check `panel_401_count`, `panel_403_count`, `panel_auth_invalid`, `panel_csrf_missing`, and `panel_csrf_invalid` trends.
   - Quick curl sanity test (missing CSRF should return 403):
   ```bash
   curl -i -u operator:"$VOXERA_PANEL_OPERATOR_PASSWORD" -X POST http://127.0.0.1:8844/queue/create -d "kind=goal&goal=test"
   ```

**Where to find artifacts/logs quickly**
- Lock file: `~/VoxeraOS/notes/queue/.daemon.lock`
- Health snapshot: `~/VoxeraOS/notes/queue/health.json`
- Queue artifacts root: `~/VoxeraOS/notes/queue/artifacts/`
- Audit log stream (systemd): `journalctl --user -u voxera-daemon.service -u voxera-panel.service -f`

## Artifact bundle contract

Each queue job writes/updates artifacts under `~/VoxeraOS/notes/queue/artifacts/<job_stem>/`:
- `plan.json` — normalized payload + mission plan snapshot.
- `actions.jsonl` — event timeline (rendered newest-first in panel detail).
- `stdout.txt` and `stderr.txt` — aggregated step output/error streams.
- `outputs/generated_files.json` (optional) — paths captured from `files.write_text` outputs.

Interpretation quick-guide:
- `plan.json` confirms what mission/steps were executed (or queued for approval).
- `actions.jsonl` is the lifecycle source-of-truth for queue transitions.
- `stdout.txt`/`stderr.txt` provide operator debugging context without needing raw logs.
- Generated files list helps locate mission side effects quickly.

## Failed artifact sidecar contract + retention

Every failed primary job (`failed/*.json`) may have an optional sidecar at
`failed/<job_stem>.error.json` with this schema contract:

- Required fields:
  - `schema_version` (currently `1`)
  - `job` (exact failed filename, e.g. `job-123.json`)
  - `error` (string summary)
  - `timestamp_ms` (Unix epoch **milliseconds**)
- Optional:
  - `payload` (object)

The daemon validates this schema on both write and read. Invalid sidecars are ignored for
status summaries and emit `queue_failed_sidecar_invalid` audit events.

Operator-facing status surfaces expose sidecar-health counters:
- `failed metadata valid`
- `failed metadata invalid`
- `failed metadata missing`

When `failed metadata invalid` is non-zero, inspect `failed/*.error.json` and triage
matching `queue_failed_sidecar_invalid` audit events before retrying jobs.

Schema evolution policy:
- Writer is pinned to a single current version (`1`) to keep emitted artifacts deterministic.
- Reader accepts an explicit allowlist of supported versions (currently `[1]`).
- Unknown future versions are rejected deterministically and surfaced via
  `queue_failed_sidecar_invalid` so operators can detect mixed-version artifacts quickly.
- When introducing a new schema version, update both writer pin and reader allowlist
  intentionally, and document migration/compatibility expectations in release notes.

Retention pruning treats a failed primary job plus `.error.json` as one logical unit:

- Pairing key is the shared stem (`x.json` + `x.error.json`).
- Orphans are still deterministic units:
  - job without sidecar = one unit
  - sidecar without job = one unit
- Newness for pruning uses the newest mtime of either file in the unit.
- Policy keeps newest failures and removes older units:
  - `VOXERA_QUEUE_FAILED_MAX_AGE_S` (optional max age in seconds)
  - `VOXERA_QUEUE_FAILED_MAX_COUNT` (optional max logical units)

When both are set, max-age is applied first, then max-count keeps the newest among survivors.

Troubleshooting notes:
- ASK-policy failures denied from `voxera queue approvals deny ...` write a compliant sidecar
  with `error="Denied in approval inbox"` and payload context when available.
- If you find orphan sidecars/jobs, they will still be listed/pruned predictably by stem unit,
  so cleanup can safely rely on the daemon retention policy.

Mixed-version incident runbook (`queue_failed_sidecar_invalid`):
1. Detect and confirm in audit stream:
   ```bash
   voxera audit | rg "queue_failed_sidecar_invalid"
   ```
2. Scope impacted artifacts by sidecar schema version under `failed/`:
   ```bash
   python - <<'PY'
   import json
   from collections import Counter
   from pathlib import Path

   failed_dir = Path.home() / "VoxeraOS/notes/queue/failed"
   counts = Counter()
   for sidecar in failed_dir.glob("*.error.json"):
       try:
           payload = json.loads(sidecar.read_text(encoding="utf-8"))
       except Exception:
           counts["unparseable"] += 1
           continue
       counts[str(payload.get("schema_version", "missing"))] += 1
   for version, count in sorted(counts.items()):
       print(f"schema_version={version}: {count}")
   PY
   ```
3. Remediate safely:
   - Align service versions first (ensure daemon build and operators are on the same release line).
   - Quarantine incompatible sidecars by moving them out of `failed/` before retries if they came from a newer build.
   - Reprocess by recreating the failed primary job only after version compatibility is restored.
4. Release hygiene for schema changes:
   - Any schema bump must update writer pin + reader allowlist together.
   - Include migration/compatibility notes in release notes and echo the change in `README.md`, `docs/ops.md`, and `docs/CODEX_MEMORY.md`.


## Merge-readiness gate

Use the unified merge gate from repository root:

```bash
make merge-readiness-check
```

This combines fast quality checks (format/lint/type) and release consistency checks (version/doc/runtime alignment) under one workflow status check: `merge-readiness / merge-readiness`.

Validation tiers:
- Required for pull request merge: `make merge-readiness-check`.
- Broader local validation before releases/high-risk refactors: `make full-validation-check` (`make premerge` alias).

Typing policy:
- `make type-check` uses a mypy ratchet against `tools/mypy-baseline.txt` and blocks new type errors.
- `make type-check-strict` runs full mypy for baseline burn-down work.
- `make update-mypy-baseline` should only be used for intentional baseline refreshes after triage.

Baseline governance:
- Do not refresh the baseline as the first response to a ratchet failure; fix new errors first.
- Any baseline refresh should include a clear PR rationale (what debt was triaged and why update is intentional).
- `tools/mypy-baseline.txt` and `scripts/mypy_ratchet.py` are review-sensitive and should receive maintainer approval.

Local workflow parity:
- `make dev` installs pre-commit + pre-push hooks.
- Pre-push runs `make merge-readiness-check` so local gates match CI expectations.

CI diagnostics:
- On merge-readiness failures, GitHub Actions uploads `merge-readiness-logs` artifacts (quality/release logs).
- Workflow step summary will call out whether quality or release phase failed and where to find the logs.
- Download artifacts from the failed workflow run for quick triage without rerunning locally.

Troubleshooting:
- If `ruff` fails, run `make fmt` then rerun `make merge-readiness-check`.
- If mypy ratchet fails with new errors, fix the reported lines or explicitly triage + refresh baseline with `make update-mypy-baseline`.
- If release-check fails, ensure docs and runtime versioned surfaces remain aligned.

## Queue failed-artifact + approval triage runbook

1) Inspect queue state and retention context:

```bash
voxera queue status
```

Interpretation guide:
- `failed/` counts primary failed jobs only.
- `failed metadata invalid` indicates malformed or schema-incompatible `failed/*.error.json` sidecars (audit logs emit `queue_failed_sidecar_invalid`).
- `failed retention max age (s)` / `failed retention max count` show active retention controls from environment.
- `Failed Retention (latest prune event)` summarizes the newest prune pass (`removed jobs`, `removed sidecars`).

2) Inspect pending approvals:

```bash
voxera queue approvals list
```

3) Resume or deny by job reference:

```bash
voxera queue approvals approve <job_id_or_filename>
voxera queue approvals approve <job_id_or_filename> --always
voxera queue approvals deny <job_id_or_filename>
```

4) If invalid sidecars rise, inspect and repair/quarantine malformed artifacts before retrying jobs:

```bash
ls ~/VoxeraOS/notes/queue/failed/*.error.json
```

5) Retention control knobs (set before daemon/panel start):
- `VOXERA_QUEUE_FAILED_MAX_AGE_S`
- `VOXERA_QUEUE_FAILED_MAX_COUNT`

## Information sources (keep in sync)

For current project state and handoff context, keep these files aligned whenever queue/planner behavior changes:
- `README.md` (operator-facing feature and workflow docs)
- `docs/ROADMAP.md` (current baseline + 4/8/12-week user-visible milestones + delivery enablers)
- `docs/CODEX_MEMORY.md` (chronological merged-change memory)
- `AGENT.md` and `CODEX.md` (root-level quick memory pointers)

Documentation + audit hygiene checklist (run for every merged behavior/process change):
1. Update `README.md` for user/operator workflow changes.
2. Update `docs/ROADMAP.md` so completed and upcoming work reflect current state.
3. Append a merged-entry to `docs/CODEX_MEMORY.md` with summary, validation, follow-ups, and risks.
4. Confirm CI/ops guidance in `docs/ops.md` still matches current `Makefile`/workflow behavior.
5. If checks or runbooks changed, verify audit event names and triage steps remain accurate in docs.

## DEV auto-approve warning

`voxera daemon --auto-approve-ask` is **DEV-only** and requires `VOXERA_DEV_MODE=1`.
Without that env var, no ASK actions are auto-approved.

Even in DEV mode, auto-approval is restricted to `system.settings` capability only.
Network asks (for example `system.open_url`) still go to pending approval inbox.

## Update cadence

Recommended cadence: update frequently (daily/weekly) on active systems.

```bash
# from repository root
make update
```

`make update` runs the best-practice updater (`scripts/update.sh --smoke`), which:
- pulls latest `main`
- ensures `.venv` exists and reinstalls editable dependencies
- runs `compileall` + `pytest`
- runs E2E dry-run smoke checks
- restarts installed/enabled Voxera user services

## Rollback guidance (advanced)

If a fresh update causes regressions, rollback to a known commit:

```bash
cd ~/VoxeraOS
git log --oneline --decorate -n 20
git checkout <sha>
cd <your-checkout-path>
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m compileall src
pytest -q
make services-restart
```

Then validate:

```bash
voxera status
voxera queue status
```

This keeps you pinned to the selected commit (detached HEAD) until you intentionally switch back.

When ready, return to main:

```bash
cd ~/VoxeraOS
git checkout main
git pull --ff-only
```

## Logs and audits

### Mission logs
- Mission summaries are appended to `~/VoxeraOS/notes/mission-log.md`.
- Queue and approval flows also leave traces in notes/audit artifacts.

### systemd journal

```bash
systemctl --user status voxera-daemon.service voxera-panel.service
journalctl --user -u voxera-daemon.service -f
journalctl --user -u voxera-panel.service -f
```

### Voxera audit views

```bash
voxera audit
voxera queue status
```


## Planner reliability workstream (dashboard + alerts)

Dashboard (live): `https://grafana.voxera.internal/d/planner-reliability/mission-planner-reliability`

Use planner audit events (`planner_selected`, `planner_fallback`, `plan_built`, `plan_failed`) as the canonical telemetry stream for production operator panels.
Gemini and OpenAI-compatible planners both emit this same fallback telemetry contract.

Required dashboard panels:
- Fallback rate: `% of planner events with fallback_used=true` (slice by provider/model).
- Plan failure rate: `% of requests ending in plan_failed`.
- Planner latency percentiles (p50/p95/p99) from `latency_ms`, segmented by `provider` + `model`.
- `error_class` distribution, grouped by provider/model.
- Attempt-depth trend: distribution/time series of `attempt` values to show retry depth pressure.

Alerting (live): `https://grafana.voxera.internal/alerting/list?search=planner`

Initial thresholds, ownership, and routing:

| Alert | Threshold | Baseline window | Sustain window | Owner | Routing |
|---|---|---|---|---|---|
| fallback-rate spike | fallback_used=true rate > `8%` AND >= `2x` trailing baseline | 7d trailing same-hour baseline | 15m | AI Runtime On-Call | PagerDuty: `voxera-ai-runtime` + `#ops-planner` |
| sustained plan-failure increase | `plan_failed` rate > `3%` | 24h baseline | 20m | AI Runtime On-Call | PagerDuty: `voxera-ai-runtime` |
| timeout spike | `error_class=timeout` > `2%` by provider/model | 24h baseline | 10m | Platform SRE | PagerDuty: `voxera-sre` + `#ops-planner` |
| rate_limit spike | `error_class=rate_limit` > `1.5%` by provider/model | 24h baseline | 10m | Platform SRE | PagerDuty: `voxera-sre` + vendor escalation runbook |
| malformed_json spike (drift signal) | `error_class=malformed_json` > `1%` and > `3x` 7d baseline | 7d trailing baseline | 15m | Planner Maintainers | Slack: `#ops-planner` + Jira component `planner-telemetry` |

### How to interpret planner degradation

1. Open **Fallback Rate** and **Attempt-Depth Trend** panels first. If fallback and higher attempts rise together, primary reliability drift is likely.
2. Check **Error Class Distribution** next:
   - `malformed_json` growth -> likely response-format drift/model behavior change.
   - `timeout` growth -> provider latency/saturation or network transport degradation.
   - `rate_limit` growth -> quota or burst-management pressure.
3. Confirm impact with **Plan Failure Rate** and **Latency p95/p99** panels to distinguish recoverable retries from user-visible failures.
4. Use the alert list above to route response: runtime vs SRE vs planner maintainer ownership.

Quick audit triage command:

```bash
voxera audit | rg "planner_selected|planner_fallback|plan_built|plan_failed"
```


### Planner drift watch (fallback diagnostics)

Use planner audit events to spot model drift and provider instability:
- `planner_fallback` captures `provider`, `model`, `attempt`, `error_class`, `latency_ms`, and `fallback_used`.
- `plan_built` captures the winning provider/model and whether fallback routing was used.

Operational heuristic:
- Rising `fallback_used=true` rates over a rolling window usually indicate primary-plan quality or reliability drift.
- Rising `error_class=malformed_json` typically indicates output-format drift.
- Rising `error_class=timeout` or `rate_limit` usually indicates provider saturation/transport issues.

Start with:

```bash
voxera audit | rg "planner_fallback|plan_built"
```

If fallback frequency spikes, compare by `provider` + `model` and promote/demote routing (`primary` vs `fast` vs `fallback`) until error-class mix returns to baseline.

## Safety note

Operational workflows here do **not** require deleting data under `~/VoxeraOS/notes`.




## Incident bundle export runbook

### Per-job bundle

```bash
# CLI
voxera ops bundle job <job_id>

# Panel (requires Basic auth)
GET /jobs/{job_id}/bundle
```

Panel `/jobs` now shows cross-bucket job rows (inbox/pending/approvals/done/failed) with artifact presence markers (plan/actions/stdout/stderr), last activity from `actions.jsonl`, and direct actions (detail/bundle/cancel/retry).

Bundle includes (size-capped and deterministic):
- `job.json`
- optional `approval.json`
- optional `failed.error.json`
- capped contents of `artifacts/<job_id>/`
- `health.json` snapshot
- `manifest.json` with truncation/byte metadata

### System snapshot bundle

Panel bundle downloads write to deterministic incident folders:
`notes/queue/_archive/incident-<YYYYMMDD-HHMMSS>-<job_stem_or_system>/`.

CLI ops bundles default to `notes/queue/_archive/<YYYYMMDD-HHMMSS>/`. For incident handoff, you can place both system + job bundles in a single folder with `--dir` (or by setting `VOXERA_OPS_BUNDLE_DIR`).

```bash
# CLI
voxera ops bundle system
voxera ops bundle job <job_id>

# one shared incident folder
voxera ops bundle system --dir notes/queue/_archive/INCIDENT-123
voxera ops bundle job <job_id> --dir notes/queue/_archive/INCIDENT-123

# Panel (requires Basic auth)
GET /bundle/system
```

System bundle contains `manifest.json`, queue snapshots (`queue_status.txt`, `queue_health.json`, lock snapshot), redacted config snapshots (`snapshots/config_snapshot.json`, `snapshots/config_snapshot.sha256`), optional `journal_voxera_daemon_tail.txt`, and `panel_log_hint.txt`.
Job bundles include the same redacted config snapshot files under `snapshots/` for operator handoff consistency. Optional approval/failed-sidecar files are now quiet on normal success paths (single optional note), with anomaly notes only when a bucket implies an expected missing artifact.

### OpsConsole golden-path e2e script output directory

`scripts/e2e_opsconsole.sh` supports `--dir` to write all script outputs (`e2e.log`, doctor output, zip listings, and both bundle zips) into an operator-selected directory.

```bash
# path with spaces
scripts/e2e_opsconsole.sh --dir "/tmp/inc test"

# path starting with dash (requires explicit -- separator)
scripts/e2e_opsconsole.sh --dir -- /tmp/-weird
```

Without `--dir`, the script keeps the default archive location under `notes/queue/_archive/ops-e2e-<timestamp>/`.

### Truncation + size troubleshooting

- Per-file cap defaults to 256KB; total bundle cap defaults to 4MB.
- When exceeded, files are truncated and noted in `manifest.json` (`truncated=true`, original/written bytes).
- If you need full raw logs, collect artifacts directly from queue paths under controlled access.

## Doctor quick mode

```bash
voxera doctor --quick
```

`--quick` is offline-only and does **not** call LLM providers. It reports:
- lock status (`exists`, `pid`, `alive`)
- health `last_ok_event/last_ok_ts_ms` and `last_error/last_error_ts_ms`
- queue counts summary (`inbox`, `pending`, `approvals`, `done`, `failed`)

Example details lines:
- `exists=True pid=12345 alive=True`
- `event=daemon_tick ts=1730000000000`
- `inbox=0 pending=1 approvals=0 done=12 failed=2`

Use full `voxera doctor` when you want provider capability tests; use `--quick` during incidents for immediate local sanity checks.

## Doctor golden-path self-test

Use `voxera doctor --self-test` to run a tiny safe queue job (`system_check`) and validate:

- queue daemon processing
- audit event visibility
- queue artifact bundle creation (`plan.json`, `actions.jsonl`, `stdout.txt`, `stderr.txt`)

The command prints pass/fail and actionable fix steps when checks fail.


### approval artifact scope compatibility

Queue approval artifacts now write scope in two locations for compatibility:
- top-level: `fs_scope`, `needs_network`
- nested: `scope.fs_scope`, `scope.needs_network`

Readers prefer top-level keys when present and fall back to nested `scope.*` for older artifacts.
