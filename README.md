# Voxera OS Alpha v0.1.6 — Voice-first AI Control Plane

Voxera OS is an **AI-controlled OS experience** built as a reliable *control plane* on top of a standard Linux substrate.
This repo is **Voxera OS Alpha v0.1.6** — the security hardening + ops visibility release. v0.1.6 ships: planner goal
sanitization + 2,000-char cap, `[USER DATA START]`/`[USER DATA END]` prompt boundaries, panel auth lockout (10/60s → 429
+ `Retry-After`), panel Daemon Health widget (health.json-sourced), panel `/hygiene` page, `sandbox.exec` argv
canonicalization, deterministic terminal demo skill, and OpenRouter invisible attribution. Built on the v0.1.5 artifacts
hygiene baseline and v0.1.4 stability + UX baseline (typed first-run setup, cloud-planned missions, queue daemon with
approval inbox, queue status + panel insights, update tooling, systemd user services, and pluggable “brain” providers).
See `docs/ROADMAP_0.1.6.md` for the full shipped scope.

**Names**
- OS: **Voxera OS**
- Core AI persona: **Vera**
- Wake word (planned): **“Hey Voxera”**
- CLI: `voxera`

## What works now (daily-driver baseline)
- ✅ Cloud mission planner (`voxera missions plan "<goal>"`) with policy + approval gating preserved
- ✅ Deterministic simple-write planning for note/file goals (single `files.write_text` step, no clipboard hops)
- ✅ Queue daemon for mission/goal JSON jobs plus approval inbox (`pending/approvals/*.approval.json`)
- ✅ Queue status UX (`voxera queue status`) and panel insights for pending approvals/audit
- ✅ Panel home Daemon Health widget (collapsible) from `notes/queue/health.json` only: lock status/PID/stale age, last brain fallback, startup recovery summary, shutdown outcome, daemon state
- ✅ DEV-only auto-approve gating for `system.settings` only (`VOXERA_DEV_MODE=1` + `--auto-approve-ask`)
- ✅ Human-friendly inbox entry point (`voxera inbox add`, `voxera inbox list`) for queueing goals
- ✅ Update flow (`make update`) and systemd user service lifecycle (`make services-install`, status/restart/stop)
- ✅ `voxera demo` guided onboarding checklist — offline by default, `--online` for provider checks
- ✅ `voxera artifacts prune` and `voxera queue prune` for operator hygiene (dry-run by default, `--yes` to execute)
- ✅ `voxera queue reconcile` for queue diagnostics and quarantine-first orphan fix
- ✅ Daemon reliability: single-writer lock with stale detection, graceful SIGTERM shutdown, deterministic startup recovery
- ✅ Brain fallback reasons classified and surfaced (`TIMEOUT | AUTH | RATE_LIMIT | MALFORMED | NETWORK | UNKNOWN`)
- ✅ Planner prompt hardening: goals over 2,000 chars are rejected preflight; embedded goals are sanitized (ASCII control-char stripping + whitespace normalization)
- ✅ Modernized setup wizard with non-destructive credential handling (keep/skip/replace)


## Security notes (planner hardening)
- Mission planning rejects overlength goals (>2,000 chars) before any cloud brain/provider call.
- User goal text embedded in planner prompts is sanitized to remove ASCII control chars and normalize whitespace.
- See `docs/SECURITY.md` for threat model and operator guidance.

## Tech stack

| Layer | Technology |
|---|---|
| Language | Python ≥ 3.10 |
| CLI | Typer + Rich |
| Data models | Pydantic v2 |
| Web panel | FastAPI + Uvicorn + Jinja2 |
| HTTP client | httpx (async) |
| Config / secrets | platformdirs + keyring + 0600 file |
| AI backends | Google Gemini, OpenAI-compat (OpenRouter, Ollama, local) |
| Sandbox | Podman (rootless, `--network=none` by default) |
| Service management | systemd user units |
| Linting + format | Ruff |
| Type checking | Mypy + ratchet baseline |
| Tests | pytest + pytest-asyncio |

For the full module map, data flow diagram, queue lifecycle, and config precedence details,
see `docs/ARCHITECTURE.md`.

## Quick start (Alpha)
```bash
make dev
make fmt-check
make check

make update
make services-install
make daemon-restart

.venv/bin/voxera --version
.venv/bin/voxera queue status
voxera inbox add "Write a daily check-in note with priorities and blockers"
voxera daemon --once
voxera queue approvals list
voxera queue approvals approve <job_id_or_filename>
voxera queue approvals approve <job_id_or_filename> --always
# or deny:
voxera queue approvals deny <job_id_or_filename>
voxera queue cancel <job_id_or_filename>
voxera queue retry <job_id_or_filename>
voxera queue pause
voxera queue resume
voxera queue unlock           # safe: stale/dead locks only
voxera queue unlock --force   # override live lock (dangerous)
voxera queue health           # operator health snapshot (lock/auth/csrf counters)
voxera queue lock status      # lock table alias from queue health
```


## Runtime config (central loader)


### Dev Contract
- CI may call `make fmt-check`, `make lint`, `make type`, `make test`, `make test-failed-sidecar`, or `make release-check` individually.
- Each of these targets now depends on `.venv/.dev_installed`, created by `make dev`, so tool binaries (`ruff`, `mypy`, `pytest`) are always present before checks run.
- `make test`/`make check` run pytest with a sanitized `VOXERA_*` env and `VOXERA_LOAD_DOTENV=0` so local `.env` and shell exports do not leak into CI-style test runs.

### Config Contract
- Runtime config file path: `~/.config/voxera/config.json` (optional).
- Precedence is strict and deterministic: **CLI overrides > VOXERA_* env > config file > defaults**.
- Inspect safely: `voxera config show` (sensitive values redacted as `***`).
- Write a redacted runtime snapshot for automation: `voxera config snapshot` (prints absolute path only).
- Validate explicitly: `voxera config validate` (non-zero exit with actionable error details).
- When `--queue-dir` is provided for `voxera ops bundle ...`, archive defaults are anchored under `<queue_dir>/_archive/...`; `VOXERA_OPS_BUNDLE_DIR` is ignored unless `--dir` is passed.

Example `~/.config/voxera/config.json`:
```json
{
  "panel_host": "127.0.0.1",
  "panel_port": 8844,
  "queue_lock_stale_s": 3600,
  "panel_csrf_enabled": true
}
```


- Copy `.env.example` to `.env` for local non-secret defaults.
- Keep secrets out of git; preferred location is `~/.config/voxera/env` (same `KEY=VALUE` format).
- Runtime settings are loaded by `load_config()` into `VoxeraConfig` and include queue root, panel host/port, operator auth, lock stale window, failed-retention limits, and ops bundle directory.
- Brain backoff knobs (used for computed wait and enforced daemon planning delay):
  - `VOXERA_BRAIN_BACKOFF_BASE_S` (default `2`)
  - `VOXERA_BRAIN_BACKOFF_MAX_S` (default `60`)
- Print a redacted config snapshot for audits with:

```bash
.venv/bin/voxera config show
.venv/bin/voxera config snapshot
```

This writes `notes/queue/_ops/config_snapshot.json` and `notes/queue/_ops/config_snapshot.sha256` (redacted; deterministic key ordering).

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
- `~/.config/voxera/config.yml` (app/brain config: brain provider, mode, privacy settings)
- secrets stored via `keyring` when possible; otherwise a 0600 fallback file

The runtime ops config (`~/.config/voxera/config.json`) is separate and optional — create it to
override panel/queue defaults. See the **Runtime config** section above and `docs/ops.md`.


### OpenRouter recommended setup
`voxera setup` now includes an **OpenRouter** cloud option and configures:
- Base URL: `https://openrouter.ai/api/v1`
- Brain tiers: `primary`, `fast`, `reasoning`, `fallback`
- Automatic attribution headers on OpenRouter calls: `HTTP-Referer=https://voxeraos.ca`, `X-OpenRouter-Title=VoxeraOS` (and `X-Title=VoxeraOS` for compatibility)
- Gemini provider is supported for mission planning and participates in the same fallback chain as OpenAI-compatible providers.

OpenRouter attribution is automatic and invisible during setup so usage appears as **VoxeraOS (voxeraos.ca)** by default.
To override defaults, set `VOXERA_APP_URL` and/or `VOXERA_APP_TITLE`, or define provider `extra_headers` in config (explicit headers win).

After setup, run:
```bash
voxera doctor
voxera doctor --self-test
```
to verify each configured model endpoint.

### Quick Demo (safe + repeatable)
Use the guided demo checklist to exercise queue + approval flows without destructive actions:

```bash
voxera demo
voxera demo --online
```

- `voxera demo` is offline-first and marks provider readiness as `SKIPPED`.
- `voxera demo --online` opts into provider readiness checks; missing keys remain `SKIPPED` (not failure).
- Demo jobs are created with a deterministic prefix (`demo-basic-*`, `demo-approval-*`), and approval demo jobs set `approval_required=true`.

Setup wizard UX is non-destructive for provider credentials/config:
- **Keep current** (default when configured)
- **Skip for now** (continue offline)
- **Enter new / replace key** (explicit only)

Config separation remains:
- App/brain setup: `~/.config/voxera/config.yml`
- Runtime ops config (operator-managed): `~/.config/voxera/config.json`

### 2) Try basic commands
```bash
voxera status
voxera skills list
voxera run system.status
voxera run system.open_app --arg name=firefox --dry-run
```

### First-time queue + mission setup (required folders)
Preferred one-time bootstrap command:
```bash
voxera queue init
```
This creates (mkdir -p only; never deletes):
- Queue root: `~/VoxeraOS/notes/queue`
- `~/VoxeraOS/notes/queue/inbox/` (intake)
- `~/VoxeraOS/notes/queue/pending/`
- `~/VoxeraOS/notes/queue/pending/approvals/`
- `~/VoxeraOS/notes/queue/done/`
- `~/VoxeraOS/notes/queue/failed/`
- `~/VoxeraOS/notes/queue/artifacts/`
- `~/VoxeraOS/notes/queue/_archive/`

Equivalent manual command:
```bash
mkdir -p ~/VoxeraOS/notes/queue/{inbox,pending/approvals,done,failed,canceled,artifacts,_archive}
```

Start/restart daemon service:
```bash
systemctl --user restart voxera-daemon.service
# or first-time enable/start:
systemctl --user enable --now voxera-daemon.service
```

Submit a queue job file:
```bash
cat > ~/VoxeraOS/notes/queue/inbox/job-1.json <<'JSON'
{"version":"1","goal":"run a quick system check","mission_id":"system_check"}
JSON
```

View status and resolve approvals:
```bash
voxera queue status
voxera queue approvals list
voxera queue approvals approve <job_id_or_filename>
voxera queue approvals approve <job_id_or_filename> --always
voxera queue approvals deny <job_id_or_filename>
voxera queue cancel <job_id_or_filename>
voxera queue retry <job_id_or_filename>
voxera queue pause
voxera queue resume
voxera queue unlock           # safe: stale/dead locks only
voxera queue unlock --force   # override live lock (dangerous)
```


### 2b) Try built-in missions (agent-style multi-step flow)
```bash
voxera missions list
voxera missions run system_check --dry-run
voxera missions run work_mode
```

### File-based missions
Mission resolution order is deterministic:
1. Built-in mission IDs (`MISSION_TEMPLATES`, hardcoded)
2. Repo mission files: `./missions/<mission_id>.json|yaml|yml`
3. User mission files: `~/.config/voxera/missions/<mission_id>.json|yaml|yml`

Built-in IDs are not overridden by file missions by default.

Mission file schema:
- `id` (optional; defaults to filename mission_id)
- `title` (optional; defaults to mission_id)
- `goal` (optional string)
- `notes` (optional string or list of strings)
- `steps` (required list): each step uses `skill_id` (or alias `skill`) and optional `args` object.

### 2c) Let cloud AI plan a mission from a goal
```bash
voxera missions plan "prep a focused work session" --dry-run
voxera missions plan "run a quick health check and open my terminal"
```
This uses your configured `primary` brain provider and still enforces local policy + approvals.

The `--dry-run` output includes two top-level fields for auditability:
- `capabilities_snapshot`: compact runtime metadata (`schema_version`, `generated_ts_ms`) from the snapshot used during planning.
- `capabilities_used`: sorted, deduplicated list of capability strings referenced by the planned steps.

For deterministic output in CI/golden tests, add `--deterministic`:
- Sets `capabilities_snapshot.generated_ts_ms` to `0`, making JSON byte-identical across runs.
- Add `--freeze-capabilities-snapshot` to make it explicit that the snapshot is generated once per invocation (already the default; this flag documents the guarantee).

```bash
voxera missions plan "open terminal" --dry-run --deterministic
voxera missions plan "open terminal" --dry-run --deterministic --freeze-capabilities-snapshot
```

For simple write goals matching patterns like `Write a note to <path> saying: <text>`, `Write <text> to <path>`, or `Create a note/file at <path> with <text>`, Voxera uses a deterministic fast-path before LLM planning and emits exactly one `files.write_text` step (default `mode=overwrite`, or `append` when explicitly requested).


For non-explicit verification goals, planner-produced `sandbox.exec` steps that use GUI/host-dependent or sandbox-inappropriate tooling (e.g., `xdotool`, `wmctrl`, `xprop`, `gdbus`, `curl`, `wget`) are rewritten to safe `clipboard.copy` manual confirmation steps. Explicit shell-command intent from the user is preserved.


### 2d) Queue missions/goals for daemon execution
```bash
mkdir -p ~/VoxeraOS/notes/queue/inbox
echo '{"mission_id":"system_check"}' > ~/VoxeraOS/notes/queue/inbox/job-1.json
echo '{"mission":"system_check"}' > ~/VoxeraOS/notes/queue/inbox/job-2.json
echo '{"goal":"run a quick system check"}' > ~/VoxeraOS/notes/queue/inbox/job-3.json
# compatibility alias still accepted:
echo '{"plan_goal":"run a quick system check"}' > ~/VoxeraOS/notes/queue/inbox/job-4.json

# human-friendly queueing entry point:
voxera inbox add "Write a daily check-in note with top priorities"
voxera inbox list --n 20

voxera daemon --once
```

Queue intake contract: `notes/queue/inbox/*.json` is the only supported drop location.

Safety/back-compat behavior:
- Jobs dropped in legacy `notes/queue/*.json` are auto-relocated to `inbox/` with audit event `queue_job_autorelocate`.
- Jobs mistakenly dropped in `notes/queue/pending/*.json` are auto-relocated to `inbox/` (never silently stuck).

Queue job schema accepts:
- `mission_id` (or alias `mission`), or
- `goal` (preferred) / compatibility alias `plan_goal`, or
- inline `steps` (non-empty list) where each step accepts `skill_id` or legacy `skill` plus optional `args`.

If a queued mission hits an approval-required step, it is moved to `pending/` (not failed),
and an approval artifact is written to `pending/approvals/*.approval.json` with policy reason, target details, and scope metadata at both top-level (`fs_scope`, `needs_network`) and nested (`scope.fs_scope`, `scope.needs_network`) keys for compatibility.

Resolve approvals with:
```bash
voxera queue approvals list
voxera queue approvals approve <job_id_or_filename>
voxera queue approvals approve <job_id_or_filename> --always
voxera queue approvals deny <job_id_or_filename>
voxera queue cancel <job_id_or_filename>
voxera queue retry <job_id_or_filename>
voxera queue pause
voxera queue resume
voxera queue unlock           # safe: stale/dead locks only
voxera queue unlock --force   # override live lock (dangerous)
```

Queue status troubleshooting:
- Primary pending jobs are counted from `pending/*.json` (excluding `*.pending.json`).
- Approval artifacts are counted from `pending/approvals/*.approval.json`.
- If an approval artifact is malformed, `voxera queue approvals list` still shows an "(unparseable approval artifact)" row and logs `queue_status_parse_failed` in audit output.

Completed jobs are moved to `done/`; invalid or denied jobs are moved to `failed/`; operator-canceled jobs are moved to `canceled/`.

Failed-job sidecar contract and retention:
- Optional sidecar path: `failed/<job_stem>.error.json`.
- Required fields: `schema_version` (currently `1`), `job`, `error`, `timestamp_ms` (epoch milliseconds).
- Optional field: `payload` (object).
- Queue status prefers validated sidecar error text for `recent_failed`, but failed counts include **primary failed jobs only** (sidecars excluded).
- Invalid sidecars are ignored in snapshots and logged as `queue_failed_sidecar_invalid`.
- Queue status and panel expose sidecar health counters: `failed metadata valid`, `failed metadata invalid`, `failed metadata missing`.
- `voxera queue status` now also shows active failed-retention policy (`failed retention max age (s)`, `failed retention max count`) and the latest prune-event summary (`removed jobs/sidecars`).
- Lock/auth observability counters are persisted in `notes/queue/health.json` (shared by daemon + panel).
- Panel home (`/`) includes a collapsible **Daemon Health** widget sourced strictly from `notes/queue/health.json` at request time (no daemon RPC calls), so it remains available in panel-only deployments.
- Panel hygiene page (`/hygiene`) shows the latest `voxera queue prune --json` (dry-run by default; panel never passes `--yes`) and `voxera queue reconcile --json` snapshots and provides operator-trigger buttons for both actions with in-page async refresh.
- Panel recovery inspector (`/recovery`) provides a read-only listing of `notes/queue/recovery/` and `notes/queue/quarantine/` sessions (or loose files) and per-item ZIP downloads for operator triage.
- Widget fields: lock status (`held`/`stale`/`clear`) with PID/stale age, last brain fallback (tier/reason/timestamp), last startup recovery (job_count/orphan_count/timestamp), last shutdown outcome (outcome/timestamp/reason/job), daemon state (defaults to `healthy` when absent).
- Health snapshot ops signals: `daemon_state`, `consecutive_brain_failures`, `brain_backoff_wait_s` (computed current wait in seconds), `brain_backoff_active` (true when computed wait is > 0), and last-applied fields `brain_backoff_last_applied_s`/`brain_backoff_last_applied_ts` when sleep is enforced before planning.
- Health snapshot now also records `last_ok_event` + `last_ok_ts_ms` so operators can confirm recent successful daemon activity; `last_error` remains for failures.
- Health snapshot shutdown keys are always present with deterministic defaults (`null`): `last_shutdown_outcome`, `last_shutdown_ts`, `last_shutdown_reason`, `last_shutdown_job`. Outcome allowlist: `clean`, `failed_shutdown`, `startup_recovered`.
- Use `voxera queue health` for a quick operator summary (paused flag, intake path, lock status, counters, and last safe error summary).
- See `docs/ops.md` Incident Runbook for copy/paste recovery steps.
- Operator response when invalid rises: inspect `failed/*.error.json`, correlate with `queue_failed_sidecar_invalid` audit events, and quarantine/fix malformed sidecars before retrying jobs.
- Schema evolution policy: writer is pinned to version `1`, reader uses an explicit supported-version allowlist (currently `[1]`), and unknown future versions are rejected deterministically.
- Retention pruning keeps newest logical failed units (primary + sidecar) and can be configured with:
  - `VOXERA_QUEUE_FAILED_MAX_AGE_S`
  - `VOXERA_QUEUE_FAILED_MAX_COUNT`
- Queue failure lifecycle is covered by tests (runtime failure -> sidecar snapshot preference -> prune cleanup) in `tests/test_queue_daemon.py`.
- Contributors can run the release-critical guardrail locally with `make test-failed-sidecar` (targets the future-version rejection + lifecycle smoke tests).
- PRs that touch queue-daemon sidecar behavior/docs are expected to pass the `queue-failed-sidecar-guardrail` CI workflow.


Queue job best practice (atomic producer write):
```bash
queue_dir=~/VoxeraOS/notes/queue
inbox_dir="$queue_dir/inbox"
mkdir -p "$inbox_dir"
job_id=job-$(date +%s)
tmp_path="$inbox_dir/.${job_id}.tmp"
final_path="$inbox_dir/${job_id}.json"
printf '{"goal":"run a quick system check"}\n' > "$tmp_path"
mv "$tmp_path" "$final_path"
```

The daemon only processes ready `*.json` job files (ignoring dotfiles, `*.tmp`, and `*.partial` artifacts) and performs brief JSON parse retries to tolerate short partial-write windows before failing a truly invalid job.

### Testing sandbox + approvals via queue
1) Submit a network-off sandbox mission (`sandbox_smoke`) and process once:
```bash
cat > ~/VoxeraOS/notes/queue/inbox/sandbox-smoke.json <<'JSON'
{"version":"1","goal":"sandbox smoke","mission_id":"sandbox_smoke"}
JSON
voxera daemon --once
```
Expected: job moves to `done/`.

2) Submit a network-enabled sandbox mission (`sandbox_net`), then approve:
```bash
cat > ~/VoxeraOS/notes/queue/inbox/sandbox-net.json <<'JSON'
{"version":"1","goal":"sandbox net","mission_id":"sandbox_net"}
JSON
voxera daemon --once
voxera queue approvals list
voxera queue approvals approve sandbox-net
```
Expected: first run moves to `pending/` + writes `pending/approvals/*.approval.json`; after approval it moves to `done/`.


### Queue/artifact directory layout (contract)
- Queue root: `~/VoxeraOS/notes/queue`
  - `inbox/` **(intake; daemon reads `inbox/*.json`)**
  - `pending/`
  - `pending/approvals/`
  - `done/`
  - `failed/`
  - `canceled/` (operator-canceled jobs)
  - `artifacts/`
  - `_archive/`
  - `recovery/` (created by daemon startup recovery; quarantines orphan files — never deleted)
  - `quarantine/` (created by `voxera queue reconcile --fix --yes`; quarantines orphan sidecars — never deleted)

Queue intake is unambiguous: drop primary jobs in `notes/queue/inbox/*.json`.
Back-compat safety behavior:
- `notes/queue/*.json` (legacy root drops) are auto-relocated to `inbox/` with audit event `queue_job_autorelocate`.
- Mis-dropped `notes/queue/pending/*.json` primary jobs are auto-relocated to `inbox/` on daemon tick (never silently stuck forever).

Operator controls:
- `voxera queue cancel <job_id_or_filename>` → move active job to `canceled/` and clean pending/approval sidecars.
- `voxera queue retry <job_id_or_filename>` → move failed/canceled payload back to `inbox/`, archiving prior failure sidecars, and emit `queue_job_retry`.
- `voxera queue pause` / `voxera queue resume` → create/remove queue pause marker (`.paused`) and stop/start new processing.

Panel updates:
- Home dashboard exposes pause/resume and lifecycle actions (approve/deny, cancel, retry, delete).
- Done/Failed/Canceled rows link to job detail with artifacts (`plan.json`, `actions.jsonl`, `stdout.txt`, `stderr.txt`, `outputs/generated_files.json`).

- Sandbox artifacts: `~/.voxera/artifacts/<job_id>/`
- Sandbox workspace: `~/.voxera/workspace/<job_id>/`

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
make panel
# open http://127.0.0.1:8787
```

Panel mutation endpoints (`/queue/create`, `/missions/create`, `/missions/templates/create`) now use `POST` by default.
Legacy GET-based mutation compatibility can be enabled only for test/dev workflows:
```bash
VOXERA_PANEL_ENABLE_GET_MUTATIONS=1 voxera panel
```

### Create Mission (panel)
1. Open `/` in the panel and use **Create Mission**.
2. Enter only **Prompt / Goal** (required).
3. Leave **Approval required** on (default) unless you intentionally want to skip explicit approval expectation metadata.
4. Submit with **Create Mission**.

The panel writes a deterministic inbox job at:
`~/VoxeraOS/notes/queue/inbox/job-panel-mission-<slug>-<ts>.json`

Lifecycle stays the same: `inbox/` → `pending/approvals/` (if policy asks) → `done/`.
Use `/jobs/<job>.json` for per-job artifacts and `/jobs/<job>.json/bundle` for handoff bundles.

## Updating VoxeraOS (Alpha)

### Option 1 (recommended)
From repo root (safe update + smoke checks):
```bash
# from repository root
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
# from repository root
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


## Sandbox execution (v0.1.4 MVP)

### Install rootless Podman on Ubuntu 24.04
```bash
sudo apt update
sudo apt install -y podman uidmap slirp4netns fuse-overlayfs
podman info --debug | head
```

### Run sandbox.exec
`sandbox.exec` runs in the Podman backend. The canonical input format is `command` as a `list[str]`.
Accepted key aliases (`argv`, `cmd`) and string values are resolved before execution via `canonicalize_argv`.

| Input format | Example | Notes |
|---|---|---|
| `list[str]` (**canonical**) | `["bash", "-lc", "echo hello"]` | Preferred; unambiguous |
| `list[str]` (direct) | `["ip", "a"]` | Non-shell exec, no wrapper needed |
| `str` | `"echo hello"` | Tokenised via `shlex.split` → `["echo", "hello"]` |
| `argv` alias | `{"argv": ["ip", "a"]}` | Resolved to `command` key |
| `cmd` alias | `{"cmd": ["ls", "-la"]}` | Resolved to `command` key |

Empty tokens in a list (`["", "ip", "a"]`) are silently stripped before execution.
If the final argv is empty or missing, execution fails fast with a clear, actionable error message.

Example (Python API — canonical form):
```python
from voxera.models import AppConfig
from voxera.skills.registry import SkillRegistry
from voxera.skills.runner import SkillRunner

reg = SkillRegistry(); reg.discover()
runner = SkillRunner(reg, config=AppConfig())
# Canonical: explicit argv list
rr = runner.run(reg.get("sandbox.exec"), {"command": ["bash", "-lc", "echo hi; touch /work/ok"]}, AppConfig().policy)
print(rr.ok, rr.data["artifacts_dir"])

# Non-shell example (direct exec, no bash wrapper)
rr2 = runner.run(reg.get("sandbox.exec"), {"command": ["ip", "a"]}, AppConfig().policy)
print(rr2.ok)
```

### Security model
- Default `--network=none` (network remains blocked unless explicitly requested and approved).
- Read-only root filesystem (`--read-only`).
- Only `~/.voxera/workspace/<job_id>/` is mounted writable to `/work`.
- Artifacts are stored in `~/.voxera/artifacts/<job_id>/` (`stdout.txt`, `stderr.txt`, `runner.json`, `command.txt`).
- `:Z` SELinux mount suffix is used for Podman volume labeling; this is compatible on non-SELinux systems as well.

## Roadmap (user-visible milestones)

Active work is organized as daily/session goals in `docs/ROADMAP.md`.

**Near-term (v0.2 active work):**
- Prompt injection hardening: goal string sanitization + structural `[USER DATA: ...]` delimiters.
- Ops visibility in panel: surface reconcile/prune/recovery/fallback/lock/shutdown status on dashboard.
- CI hardening: golden file validation, versioned release notes, `make release-check` polish.
- Model/provider UX: keyring workflow improvements, provider profiles, safer online readiness checks.
- Long-run daemon behavior: health degradation tracking, backoff on repeated brain failures.

**v0.3:** Voice stack (STT/TTS, wake word, voice-first command loop).
**v0.4:** Signed skills, marketplace, ISO/image packaging.

See `docs/ROADMAP.md` for the full daily goal breakdown and `docs/ROADMAP_0.1.6.md` for the v0.1.6 shipped scope.
Previous releases: `docs/ROADMAP_0.1.6.md` (security hardening + ops visibility), `docs/ROADMAP_0.1.5.md` (artifacts prune), `docs/ROADMAP_0.1.4.md` (stability + UX baseline).

---
**Alpha v0.1.6** ships security hardening (goal sanitization + 2,000-char cap, prompt boundaries, panel auth lockout with 429/Retry-After), ops visibility (Daemon Health widget, `/hygiene` page), `sandbox.exec` argv canonicalization, deterministic terminal demo skill, and OpenRouter invisible attribution — on top of the v0.1.5 artifacts hygiene + v0.1.4 daily-driver baseline: stable queue operations, clearer UX, and strong safety gates before broader voice expansion. See `docs/ROADMAP_0.1.6.md` for the full shipped scope.

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

## Before pushing
Run the required gate from repository root before every push:

- `make merge-readiness-check`

`make dev` installs both pre-commit and pre-push hooks. The pre-push hook also runs `make merge-readiness-check` so local behavior matches CI.

CI run summary now records whether the quality or release phase failed and points to `artifacts/quality-check.txt` / `artifacts/release-check.txt` in uploaded `merge-readiness-logs` artifacts.


## Mypy ratchet baseline governance
Treat `tools/mypy-baseline.txt` as a controlled policy artifact:

- Do **not** run `make update-mypy-baseline` as a first response to a failing ratchet.
- First, fix newly reported typing regressions and rerun `make merge-readiness-check`.
- Refresh the baseline only after intentional debt triage and include a short rationale in the PR description (what changed and why).
- Changes to `tools/mypy-baseline.txt` and `scripts/mypy_ratchet.py` should receive maintainer review.

## Validation tiers
Use these two validation tiers to avoid policy drift:

- Required for PR merge: `make merge-readiness-check`
  - Runs formatting/lint checks and mypy ratchet (`make quality-check`)
  - Runs release consistency checks (`make release-check`)
- Broader local validation before release branches or risky changes: `make full-validation-check`
  - Includes merge-readiness plus failed-sidecar guardrails, full pytest, and E2E smoke

`make premerge` is an alias for `make full-validation-check`.

## Merge-readiness + release consistency checklist
When preparing a release or changing install/service flows, verify:

- Bump `project.version` in `pyproject.toml`; runtime surfaces consume this via `voxera.version.get_version()` (CLI + panel metadata).
- Run unified merge/readiness guardrails from repository root: `make merge-readiness-check`.
- If mypy baseline cleanup is needed, use `make type-check-strict` for full strict checks and `make update-mypy-baseline` only after intentional debt triage.
- Re-run broader guardrails from repository root: `make full-validation-check`.
- Keep operational docs synchronized (`README.md`, `docs/ops.md`, `docs/BOOTSTRAP.md`, `docs/ROADMAP.md`) with one workflow and repository-root command examples.
- Validate queue/service onboarding commands still match current CLI and Make targets (`voxera queue init`, `make services-install`, `make update`).
- Ensure branch protection requires the `merge-readiness / merge-readiness` status check so quality + doc/runtime version drift blocks merges.




## Operator Console

The panel now includes a dedicated Jobs console for incident response:

```bash
# start panel
voxera panel

# open in browser
http://127.0.0.1:8844/jobs?bucket=all&n=80
```

Key endpoints:
- `GET /jobs` with query params `bucket=all|inbox|pending|approvals|done|failed|canceled`, `q=<substring>`, `n=<max 200>`.
- `GET /jobs/{job_id}` for metadata, approval details, artifacts, and audit timeline.
- `GET /jobs/{job_id}/bundle` to export a per-job incident bundle (`.zip`).
- `GET /bundle/system` to export a system snapshot bundle (`.zip`).

Auth/CSRF notes:
- Bundle download endpoints require panel Basic auth (`VOXERA_PANEL_OPERATOR_PASSWORD`, optional `VOXERA_PANEL_OPERATOR_USER` (defaults to `admin` when unset)).
- Mutation routes still require both Basic auth + CSRF token.

CLI equivalents:
```bash
voxera ops bundle job job-123.json
voxera ops bundle system

# write both bundles into one incident handoff folder
voxera ops bundle system --dir notes/queue/_archive/INCIDENT-123
voxera ops bundle job job-123.json --dir notes/queue/_archive/INCIDENT-123
```

Quick offline doctor mode:
```bash
voxera doctor --quick
```
Runs fast local checks only (lock exists/pid/alive, health `last_ok`/`last_error`, queue counts summary, last fallback, last shutdown summary) with no model calls.
When `last_ok_ts_ms` is newer than `last_error_ts_ms` by more than 5 minutes, quick doctor marks `last_error` as stale (`stale; ok newer by <delta>`) and downgrades severity.

### Brain fallback reason observability

When the mission planner falls back from one brain tier to another, VoxeraOS classifies the failure into a stable enum:

| Reason | Trigger |
|---|---|
| `TIMEOUT` | Timeout exceptions or "timed out" messages |
| `AUTH` | HTTP 401/403 or auth-related messages |
| `RATE_LIMIT` | HTTP 429 or rate limit messages |
| `MALFORMED` | JSON decode errors, invalid schema |
| `NETWORK` | DNS, connection refused/reset, connect errors |
| `UNKNOWN` | Everything else |

**Where to check:**
- `voxera queue health` — counters: `brain_fallback_count`, `brain_fallback_reason_timeout`, etc.
- `health.json` — `last_fallback_reason`, `last_fallback_from`, `last_fallback_to`, `last_fallback_ts_ms`
- `voxera doctor --quick` — "Last fallback" line shows most recent transition or "none"

**Troubleshooting:** `RATE_LIMIT` implies API throttling; `AUTH` implies bad key/config; `TIMEOUT` implies network/provider slowness.

Panel bundle downloads write into deterministic incident directories: `notes/queue/_archive/incident-<YYYYMMDD-HHMMSS>-<job_stem_or_system>/`.
Job bundle notes now avoid normal-path noise: optional approval/failed-sidecar notes are collapsed to one optional note, while missing-expected artifacts emit explicit anomaly notes.

## Panel job actions

- **Cancel** (`/queue/jobs/{job}/cancel`) moves active jobs from `inbox/` or `pending/` into `canceled/`.
- Cancel is **active-only**; terminal jobs (`done/`, `failed/`, `canceled/`) are not cancelable and should use retry/delete flows.
- **Retry** (`/queue/jobs/{job}/retry`) accepts jobs in `failed/` or `canceled/` and re-enqueues them into `inbox/`.
- **Delete** (`/queue/jobs/{job}/delete`) is terminal-only (`done/`, `failed/`, `canceled/`) and requires exact `confirm=<job_filename>`.
- Job artifacts live under `~/VoxeraOS/notes/queue/artifacts/<job_stem>/`; per-job bundles are available from `/jobs/<job>.json/bundle`.

## Queue job artifacts

Each queue job now writes a first-class artifact bundle under `~/VoxeraOS/notes/queue/artifacts/<job_id>/` with:

- `plan.json`
- `actions.jsonl` timeline
- `stdout.txt`
- `stderr.txt`
- optional `outputs/generated_files.json`

These artifacts are present for pending, failed, and done queue paths to simplify debugging and audits.

### Artifact pruning (`voxera artifacts prune`)

The `voxera artifacts prune` command deletes stale entries from `notes/queue/artifacts/`.
**Default is always dry-run** — no deletion happens without `--yes`.

```bash
# Dry-run: preview what would be pruned (safe, no changes)
voxera artifacts prune --max-age-days 30

# Dry-run: keep newest 50, prune the rest (no changes)
voxera artifacts prune --max-count 50

# Union rule: prune entries older than 30 days OR outside top-50 newest
voxera artifacts prune --max-age-days 30 --max-count 50

# Actually delete (requires --yes)
voxera artifacts prune --max-age-days 30 --yes

# Machine-readable JSON summary
voxera artifacts prune --max-age-days 30 --json

# Override queue root for testing
voxera artifacts prune --queue-dir /tmp/test-queue --max-age-days 1
```

**Config** (`~/.config/voxera/config.json`):
```json
{
  "artifacts_retention_days": 30,
  "artifacts_retention_max_count": 100
}
```
Or via env: `VOXERA_ARTIFACTS_RETENTION_DAYS=30`, `VOXERA_ARTIFACTS_RETENTION_MAX_COUNT=100`.
CLI flags always override config values. If neither is set, the command prints
`"no pruning rules configured"` and exits 0 (safe default).

**Selection policy:** union — an artifact is selected for pruning if it exceeds
*either* the age rule or the count rule.

### Queue pruning (`voxera queue prune`)

Removes stale job files from terminal buckets (`done/`, `failed/`, `canceled/`).
`inbox/` and `pending/` are **never** touched. Dry-run by default; pass `--yes` to
delete. Matching sidecars (`job-XYZ.error.json`, `job-XYZ.state.json`) in the same
bucket are removed alongside their primary job files. See `docs/ops.md` for full
flag reference, config keys (`queue_prune_max_age_days`, `queue_prune_max_count`),
and env vars (`VOXERA_QUEUE_PRUNE_MAX_AGE_DAYS`, `VOXERA_QUEUE_PRUNE_MAX_COUNT`).

### Queue hygiene diagnostic (`voxera queue reconcile`)

`voxera queue reconcile` is a queue hygiene diagnostic — **report-only by
default** (no changes made). Detects orphan sidecars, orphan approvals, orphan
artifact candidates, and duplicate job filenames across buckets. Output includes
human-readable summaries and an optional stable JSON schema (`--json`).

Add `--fix` to preview quarantine actions (dry-run); add `--fix --yes` to move
orphan sidecars and approvals into a quarantine folder under the queue root.
No data is ever deleted — quarantined files can be restored manually.
See `docs/ops.md` ("Queue reconcile") for full reference, JSON schema, and examples.

### Incident runbook (quick copy/paste)

- Daemon won't start and lock appears held:
  - `voxera queue status`
  - `voxera queue unlock` (safe stale/dead pid reclaim)
  - `voxera queue unlock --force` only when you intentionally override a live holder.
- Panel `401` means Basic auth failure/missing credentials; `403` means CSRF token missing/mismatch on mutation routes.
If `VOXERA_PANEL_OPERATOR_PASSWORD` is missing, panel home/jobs show a Setup required banner with safe systemd user env + restart guidance; no secrets are shown.
- Ops bundles default to `notes/queue/_archive/<YYYYMMDD-HHMMSS>/`, or you can force a single incident handoff folder via `--dir` (or `VOXERA_OPS_BUNDLE_DIR`) so system + job zips land together:
  - `voxera ops bundle system --dir notes/queue/_archive/INCIDENT-123`
  - `voxera ops bundle job <job_ref> --dir notes/queue/_archive/INCIDENT-123`

## Planner runtime capabilities snapshot

Inspect the live runtime catalog that constrains planning/execution:

```bash
voxera ops capabilities
```

The command prints deterministic JSON with `schema_version`, `generated_ts_ms`, `missions`, `allowed_apps`, and `skills`. Data comes from the real mission catalog, skill manifests, and the `system.open_app` allowlist.

Cloud planner prompts now include a compact `SYSTEM CONTEXT (Vera)` preamble (Linux OS-wrangler doctrine + tool-selection heuristics) before the `CAPABILITIES` block from this snapshot. Planning and queue execution fail fast when a mission ID is unknown or `system.open_app` targets an app outside `allowed_apps`, with closest-match suggestions for recovery.

Planner preamble override knobs:
- `VOXERA_PLANNER_PREAMBLE` (highest precedence, full preamble string)
- `VOXERA_PLANNER_PREAMBLE_PATH` (read preamble text from file)
- fallback generated preamble using `VOXERA_PLANNER_AGENT_NAME` (default `Vera`)

To rename the assistant later without touching prompt code, set `VOXERA_PLANNER_AGENT_NAME`.
