# Operations (Alpha)

This guide covers day-2 operations for VoxeraOS in service mode.

## Install services

From the Python project root:

```bash
cd ~/VoxeraOS/voxera-os-scaffold/voxera-os
make services-install
```

This installs user units from `deploy/systemd/user/` into `~/.config/systemd/user`, rendering
`WorkingDirectory` and `ExecStart` to your **current checkout path**, then reloads systemd user state
and enables/starts:
- `voxera-daemon.service`
- `voxera-panel.service`

## Inbox -> queue processing flow

Use `voxera inbox` as the human-friendly front door for queued goals:

```bash
voxera inbox add "Write a daily check-in note with priorities and blockers"
voxera inbox list --n 20
voxera daemon --once
voxera queue status
```

`voxera inbox add` writes queue-compatible JSON (`{"id":"...","goal":"..."}`) into the queue root,
then the daemon processes it through the normal planner + policy + audit pipeline.

Planner note: simple write goals (for example, writing explicit text to a notes file path) take a deterministic fast-path and produce a single `files.write_text` step, bypassing cloud planner variability and clipboard detours.

## Approval deny workflow

When a queued mission hits an ASK policy gate, it is moved to `pending/` and a
`pending/approvals/*.approval.json` artifact is created.

Queue status troubleshooting quick checks:
- `voxera queue status` counts `pending/*.json` as pending jobs, excluding `*.pending.json` metadata files.
- `voxera queue status` counts approvals from `pending/approvals/*.approval.json`.
- `voxera queue approvals list` will surface malformed artifacts as `(unparseable approval artifact)` and emit `queue_status_parse_failed` audit events.

Queue job best practice (atomic producer write + rename):
```bash
queue_dir=~/VoxeraOS/notes/queue
job_id=job-$(date +%s)
tmp_path="$queue_dir/.${job_id}.tmp"
final_path="$queue_dir/${job_id}.json"
printf '{"goal":"run a quick system check"}\n' > "$tmp_path"
mv "$tmp_path" "$final_path"
```
The daemon ignores temporary artifacts (`.*`, `*.tmp`, `*.partial`) and retries JSON parsing briefly so short partial writes can stabilize before the job is marked failed.

```bash
voxera queue approvals list
voxera queue approvals deny <job_id_or_filename>
voxera queue status
```

Denied jobs are visible in `failed/`, and audit/mission logs include deny lifecycle entries.

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

## DEV auto-approve warning

`voxera daemon --auto-approve-ask` is **DEV-only** and requires `VOXERA_DEV_MODE=1`.
Without that env var, no ASK actions are auto-approved.

Even in DEV mode, auto-approval is restricted to `system.settings` capability only.
Network asks (for example `system.open_url`) still go to pending approval inbox.

## Update cadence

Recommended cadence: update frequently (daily/weekly) on active systems.

```bash
cd ~/VoxeraOS/voxera-os-scaffold/voxera-os
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
cd voxera-os-scaffold/voxera-os
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
