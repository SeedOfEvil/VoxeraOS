# Voxera OS Alpha v0.1.4 — Voice-first AI Control Plane (Stability + UX Baseline)

Voxera OS is an **AI-controlled OS experience** built as a reliable *control plane* on top of a standard Linux substrate.
This repo is **Voxera OS Alpha v0.1.4**: a stability + UX baseline release with typed first-run setup (`voxera setup`), cloud-planned missions,
a queue daemon with approval inbox, queue status + panel insights, update tooling, systemd user services, and pluggable “brain” providers.

**Names**
- OS: **Voxera OS**
- Core AI persona: **Vera**
- Wake word (planned): **“Hey Voxera”**
- CLI: `voxera`

## What works in Alpha v0.1.4 (daily-driver baseline)
- ✅ Cloud mission planner (`voxera missions plan "<goal>"`) with policy + approval gating preserved
- ✅ Deterministic simple-write planning for note/file goals (single `files.write_text` step, no clipboard hops)
- ✅ Queue daemon for mission/goal JSON jobs plus approval inbox (`pending/approvals/*.approval.json`)
- ✅ Queue status UX (`voxera queue status`) and panel insights for pending approvals/audit
- ✅ DEV-only auto-approve gating for `system.settings` only (`VOXERA_DEV_MODE=1` + `--auto-approve-ask`)
- ✅ Human-friendly inbox entry point (`voxera inbox add`, `voxera inbox list`) for queueing goals
- ✅ Update flow (`make update`) and systemd user service lifecycle (`make services-install`, status/restart/stop)

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
- `~/.config/voxera/config.yml`
- secrets stored via `keyring` when possible; otherwise a 0600 fallback file


### OpenRouter recommended setup
`voxera setup` now includes an **OpenRouter** cloud option and configures:
- Base URL: `https://openrouter.ai/api/v1`
- Headers: `HTTP-Referer` and `X-Title`
- Brain tiers: `primary`, `fast`, `reasoning`, `fallback`
- Gemini provider is supported for mission planning and participates in the same fallback chain as OpenAI-compatible providers.

After setup, run:
```bash
voxera doctor
voxera doctor --self-test
```
to verify each configured model endpoint.

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
mkdir -p ~/VoxeraOS/notes/queue/{inbox,pending/approvals,done,failed,artifacts,_archive}
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

Completed jobs are moved to `done/`; invalid or denied jobs are moved to `failed/`.

Failed-job sidecar contract and retention:
- Optional sidecar path: `failed/<job_stem>.error.json`.
- Required fields: `schema_version` (currently `1`), `job`, `error`, `timestamp_ms` (epoch milliseconds).
- Optional field: `payload` (object).
- Queue status prefers validated sidecar error text for `recent_failed`, but failed counts include **primary failed jobs only** (sidecars excluded).
- Invalid sidecars are ignored in snapshots and logged as `queue_failed_sidecar_invalid`.
- Queue status and panel expose sidecar health counters: `failed metadata valid`, `failed metadata invalid`, `failed metadata missing`.
- `voxera queue status` now also shows active failed-retention policy (`failed retention max age (s)`, `failed retention max count`) and the latest prune-event summary (`removed jobs/sidecars`).
- Lock/auth observability counters are persisted in `notes/queue/health.json` (shared by daemon + panel).
- Health snapshot now also records `last_ok_event` + `last_ok_ts_ms` so operators can confirm recent successful daemon activity; `last_error` remains for failures.
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
  - `artifacts/`
  - `_archive/`

Queue intake is unambiguous: drop primary jobs in `notes/queue/inbox/*.json`.
Back-compat safety behavior:
- `notes/queue/*.json` (legacy root drops) are auto-relocated to `inbox/` with audit event `queue_job_autorelocate`.
- Mis-dropped `notes/queue/pending/*.json` primary jobs are auto-relocated to `inbox/` on daemon tick (never silently stuck forever).

Operator controls:
- `voxera queue cancel <job_id_or_filename>` → move job to `canceled/` and remove pending approval markers.
- `voxera queue retry <job_id_or_filename>` → move failed job payload back to `inbox/` and emit `queue_job_retry` audit event.
- `voxera queue pause` / `voxera queue resume` → create/remove queue pause marker (`.paused`) and stop/start new processing.

Panel updates:
- Home dashboard exposes pause/resume and cancel/retry actions.
- Done/Failed rows link to job detail with artifacts (`plan.json`, `actions.jsonl`, `stdout.txt`, `stderr.txt`, `outputs/generated_files.json`).

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

Panel mutation endpoints (`/queue/create`, `/missions/create`) now use `POST` by default.
Legacy GET-based mutation compatibility can be enabled only for test/dev workflows:
```bash
VOXERA_PANEL_ENABLE_GET_MUTATIONS=1 voxera panel
```

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
`sandbox.exec` always runs in the Podman backend and requires `command` as an array (list) of strings.

Example (Python API):
```python
from voxera.models import AppConfig
from voxera.skills.registry import SkillRegistry
from voxera.skills.runner import SkillRunner

reg = SkillRegistry(); reg.discover()
runner = SkillRunner(reg, config=AppConfig())
rr = runner.run(reg.get("sandbox.exec"), {"command": ["bash", "-lc", "echo hi; touch /work/ok"]}, AppConfig().policy)
print(rr.ok, rr.data["artifacts_dir"])
```

### Security model
- Default `--network=none` (network remains blocked unless explicitly requested and approved).
- Read-only root filesystem (`--read-only`).
- Only `~/.voxera/workspace/<job_id>/` is mounted writable to `/work`.
- Artifacts are stored in `~/.voxera/artifacts/<job_id>/` (`stdout.txt`, `stderr.txt`, `runner.json`, `command.txt`).
- `:Z` SELinux mount suffix is used for Podman volume labeling; this is compatible on non-SELinux systems as well.

## Roadmap (user-visible milestones)
- **Next 4 weeks:** clearer queue reliability signals in CLI/panel and operator workflows.
- **Next 8 weeks:** structured mission planning previews with safer dry-run simulation UX.
- **Next 12 weeks:** stronger OpenAI-compatible provider behavior and broader mission catalog coverage.

See `docs/ROADMAP.md` for measurable 4/8/12-week outcomes, and `docs/ROADMAP_0.1.4.md` for the locked stability/UX scope and release checklist.

---
**Alpha v0.1.4** is the trustworthy daily-driver baseline: stable queue operations, clearer UX, and strong safety gates before broader voice expansion.

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
- `GET /jobs/{job_id}/raw` and `GET /jobs/{job_id}/artifacts` for raw operator payload views.
- `GET /bundle/system` to export a system snapshot bundle (`.zip`).

Auth/CSRF notes:
- Bundle download endpoints require panel Basic auth (`VOXERA_PANEL_OPERATOR_PASSWORD`, optional `VOXERA_PANEL_OPERATOR_USER` (defaults to `admin` when unset)).
- Mutation routes still require both Basic auth + CSRF token.
- Mutations redirect with HTTP 303 back to `/jobs` and preserve active filters (`bucket`, `q`, `n`) with a flash message.

Create Mission modes (progressive enhancement):
- `easy`: prompt + approval toggle + create.
- `default`: easy + optional `mission_id` + optional `title`.
- `advanced`: default + `brain`, `priority`, `tags`, `target`, `scope`, `dry_run`.

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
Runs fast local checks only (lock exists/pid/alive, health `last_ok`/`last_error`, queue counts summary) with no model calls.
When `last_ok_ts_ms` is newer than `last_error_ts_ms` by more than 5 minutes, quick doctor marks `last_error` as stale (`stale; ok newer by <delta>`) and downgrades severity.

Panel bundle downloads write into deterministic incident directories: `notes/queue/_archive/incident-<YYYYMMDD-HHMMSS>-<job_stem_or_system>/`.
Job bundle notes now avoid normal-path noise: optional approval/failed-sidecar notes are collapsed to one optional note, while missing-expected artifacts emit explicit anomaly notes.

## Queue job artifacts

Each queue job now writes a first-class artifact bundle under `~/VoxeraOS/notes/queue/artifacts/<job_id>/` with:

- `plan.json`
- `actions.jsonl` timeline
- `stdout.txt`
- `stderr.txt`
- optional `outputs/generated_files.json`

These artifacts are present for pending, failed, and done queue paths to simplify debugging and audits.


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
