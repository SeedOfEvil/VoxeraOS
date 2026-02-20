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
