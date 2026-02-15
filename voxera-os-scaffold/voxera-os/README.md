# Voxera OS Alpha v0.1.2 — Voice-first AI Control Plane

Voxera OS is an **AI-controlled OS experience** built as a reliable *control plane* on top of a standard Linux substrate.
This repo is **Voxera OS Alpha v0.1.2**: it ships a typed first-run setup (`voxera setup`), cloud-planned missions,
a queue daemon with approval inbox, queue status + panel insights, update tooling, systemd user services, and pluggable “brain” providers.

**Names**
- OS: **Voxera OS**
- Core AI persona: **Vera**
- Wake word (planned): **“Hey Voxera”**
- CLI: `voxera`

## What works in Alpha v0.1.2
- ✅ Cloud mission planner (`voxera missions plan "<goal>"`) with policy + approval gating preserved
- ✅ Deterministic simple-write planning for note/file goals (single `files.write_text` step, no clipboard hops)
- ✅ Queue daemon for mission/goal JSON jobs plus approval inbox (`pending/approvals/*.approval.json`)
- ✅ Queue status UX (`voxera queue status`) and panel insights for pending approvals/audit
- ✅ DEV-only auto-approve gating for `system.settings` only (`VOXERA_DEV_MODE=1` + `--auto-approve-ask`)
- ✅ Human-friendly inbox entry point (`voxera inbox add`, `voxera inbox list`) for queueing goals
- ✅ Update flow (`make update`) and systemd user service lifecycle (`make services-install`, status/restart/stop)

## Quick start (Alpha)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"

make update
make services-install

voxera --version
voxera queue status
voxera inbox add "Write a daily check-in note with priorities and blockers"
voxera daemon --once
voxera queue approvals list
voxera queue approvals approve <job_id_or_filename>
# or deny:
voxera queue approvals deny <job_id_or_filename>
```

## Quick start (dev VM)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
```

### 1) Run setup
```bash
voxera setup
```
This writes config to:
- `~/.config/voxera/config.yml`
- secrets stored via `keyring` when possible; otherwise a 0600 fallback file


### OpenRouter recommended setup
`voxera setup` now includes an **OpenRouter** cloud option and configures:
- Base URL: `https://openrouter.ai/api/v1`
- Headers: `HTTP-Referer` and `X-Title`
- Brain tiers: `primary`, `fast`, `reasoning`, `fallback`

After setup, run:
```bash
voxera doctor
```
to verify each configured model endpoint.

### 2) Try basic commands
```bash
voxera status
voxera skills list
voxera run system.status
voxera run system.open_app --arg name=firefox --dry-run
```

### 2b) Try built-in missions (agent-style multi-step flow)
```bash
voxera missions list
voxera missions run system_check --dry-run
voxera missions run work_mode
```

### 2c) Let cloud AI plan a mission from a goal
```bash
voxera missions plan "prep a focused work session" --dry-run
voxera missions plan "run a quick health check and open my terminal"
```
This uses your configured `primary` brain provider and still enforces local policy + approvals.

For simple write goals matching patterns like `Write a note to <path> saying: <text>`, `Write <text> to <path>`, or `Create a note/file at <path> with <text>`, Voxera uses a deterministic fast-path before LLM planning and emits exactly one `files.write_text` step (default `mode=overwrite`, or `append` when explicitly requested).



### 2d) Queue missions/goals for daemon execution
```bash
mkdir -p ~/VoxeraOS/notes/queue
echo '{"mission_id":"system_check"}' > ~/VoxeraOS/notes/queue/job-1.json
echo '{"mission":"system_check"}' > ~/VoxeraOS/notes/queue/job-2.json
echo '{"goal":"run a quick system check"}' > ~/VoxeraOS/notes/queue/job-3.json
# compatibility alias still accepted:
echo '{"plan_goal":"run a quick system check"}' > ~/VoxeraOS/notes/queue/job-4.json

# human-friendly queueing entry point:
voxera inbox add "Write a daily check-in note with top priorities"
voxera inbox list --n 20

voxera daemon --once
```
Queue job schema accepts either:
- `mission_id` (or alias `mission`)
- `goal` (preferred) or compatibility alias `plan_goal`

If a queued mission hits an approval-required step, it is moved to `pending/` (not failed),
and an approval artifact is written to `pending/approvals/*.approval.json`.

Resolve approvals with:
```bash
voxera queue approvals list
voxera queue approvals approve <job_id_or_filename>
voxera queue approvals deny <job_id_or_filename>
```

Queue status troubleshooting:
- Primary pending jobs are counted from `pending/*.json` (excluding `*.pending.json`).
- Approval artifacts are counted from `pending/approvals/*.approval.json`.
- If an approval artifact is malformed, `voxera queue approvals list` still shows an "(unparseable approval artifact)" row and logs `queue_status_parse_failed` in audit output.

Completed jobs are moved to `done/`; invalid or denied jobs are moved to `failed/`.


Queue job best practice (atomic producer write):
```bash
queue_dir=~/VoxeraOS/notes/queue
job_id=job-$(date +%s)
tmp_path="$queue_dir/.${job_id}.tmp"
final_path="$queue_dir/${job_id}.json"
printf '{"goal":"run a quick system check"}\n' > "$tmp_path"
mv "$tmp_path" "$final_path"
```

The daemon only processes ready `*.json` job files (ignoring dotfiles, `*.tmp`, and `*.partial` artifacts) and performs brief JSON parse retries to tolerate short partial-write windows before failing a truly invalid job.

### 2e) Run end-to-end smoke checks
```bash
make e2e
# optional live mission:
E2E_RUN_LIVE=1 make e2e
```
The smoke script checks optional OS tools (`wmctrl`, `xdg-open`, clipboard utilities, `pactl`)
and prints install hints when missing.

### 3) Start the panel (optional)
```bash
voxera panel
# open http://127.0.0.1:8844
```

## Updating VoxeraOS (Alpha)

### Option 1 (recommended)
From repo root (safe update + smoke checks):
```bash
cd ~/VoxeraOS/voxera-os-scaffold/voxera-os
make update
```

### Option 2 (direct script usage)
```bash
# default: fetch/pull, reinstall editable dev env, compile + tests
bash scripts/update.sh

# include e2e dry-run smoke checks
bash scripts/update.sh --smoke

# skip compile/tests for advanced users
bash scripts/update.sh --skip-tests

# force update when local changes exist (uses rebase pull with autostash)
bash scripts/update.sh --force
```

What the update flow does:
- Pulls latest commits from `main` (unless `VOXERA_UPDATE_ALLOW_BRANCH=1` is set).
- Reinstalls VoxeraOS in editable mode in `.venv` (`pip install -e ".[dev]"`).
- Runs `python -m compileall src` and `pytest -q` by default.
- Runs `E2E_DRY_RUN=1 make e2e` when `--smoke` is enabled.
- Restarts user services (`voxera-daemon.service`, `voxera-panel.service`) if installed/enabled.

> Safety note: update steps do not delete anything under `~/VoxeraOS/notes`.

### Service lifecycle commands
```bash
cd ~/VoxeraOS/voxera-os-scaffold/voxera-os
make services-install   # install + enable + start user units
make services-status    # show service status
make services-restart   # restart enabled units
make services-stop      # stop units (keeps enabled state)
make services-disable   # disable and stop units
```

Systemd units run from your project venv path (`.venv/bin/voxera`) and update in place.
`make services-install` renders unit paths from the checkout directory you run it from, so clones outside
`~/VoxeraOS` also work. Restarting services picks up the latest code after updates.

### Updating troubleshooting
- **Update blocked by local changes**: run `git status`, then either commit/stash changes, or re-run with `--force`.
- **Service fails to restart**:
  - `systemctl --user status voxera-daemon.service voxera-panel.service`
  - `journalctl --user -u voxera-daemon.service -n 100 --no-pager`
  - `journalctl --user -u voxera-panel.service -n 100 --no-pager`

## How the “OS” is structured
Voxera is designed as three layers:
1. **Substrate OS** (Ubuntu/Fedora/etc.) — drivers, updates, filesystem, networking
2. **AI Control Plane** — intent routing, planning, memory, tool runner, policy enforcement
3. **Experience Layer** — voice shell + minimal confirmation panel (and later full GUI/CLI modes)

See `docs/ARCHITECTURE.md`, `docs/BOOTSTRAP.md`, and `docs/CODEX_MEMORY.md`.

For Ubuntu validation, follow `docs/UBUNTU_TESTING.md` for a full machine test checklist.

## Safety model (MVP)
- No silent risky changes (network/install/credentials)
- Everything is audited (what/why/how to undo)
- Skills declare permissions; policies decide “allow/ask/deny”

## Roadmap
- Voice stack (wake word + STT/TTS)
- Container sandbox runner (Podman)
- First-boot “installer-by-conversation” flow
- Immutable base image (Silverblue-style) for atomic upgrades + rollback

---
**Alpha v0.1.2** is meant to give you a working system + fast iteration loop while preserving safety gates.

`files.write_text` now supports `mode=overwrite|append` for note updates, and mission runs append summaries to `~/VoxeraOS/notes/mission-log.md` (redacted when `privacy.redact_logs` is enabled).



## Mission log and redaction behavior
- Mission runs append to `~/VoxeraOS/notes/mission-log.md`.
- In redacted mode (`privacy.redact_logs=true`), entries only include status + skill ids (no args/URLs).
- Queue approval pauses append `status=pending_approval` with paused step.
- Approval denials append a denied/failed record with minimal details.

## DEV-only auto-approval for queue testing (dangerous)
`voxera daemon --auto-approve-ask` is **off by default** and only active when `VOXERA_DEV_MODE=1`.

- Auto-approval allowlist is intentionally strict: `system.settings` only.
- Network capabilities (for example `network.change`, `system.open_url`) are **never** auto-approved and still go to `pending/`.
- Auto-approvals emit loud audit events (`queue_auto_approved`) for test visibility.
