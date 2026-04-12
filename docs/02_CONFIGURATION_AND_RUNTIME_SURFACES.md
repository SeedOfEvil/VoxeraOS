# 02 — Configuration and Runtime Surfaces

This document maps the configuration surfaces, runtime paths, services, and ports that VoxeraOS actually uses today. Everything here is grounded in the current repo.

## Operator surfaces

VoxeraOS exposes three operator surfaces, all backed by the same queue.

1. **CLI — `voxera`** (Typer app root: `src/voxera/cli.py`)
   - Entry point declared in `pyproject.toml`: `voxera = "voxera.cli:app"`.
   - Top-level commands (from `cli.py` + `tests/golden/voxera_help.txt`):
     `version`, `config-show`, `doctor`, `setup`, `demo`, `status`, `run`, `audit`, `panel`, `daemon`, `vera`.
   - Sub-apps registered on the root:
     `config`, `artifacts`, `skills`, `missions`, `queue`, `ops`, `inbox`, `secrets`.
2. **Web panel — `voxera panel`** (`src/voxera/panel/app.py`)
   - FastAPI app with `title="Voxera Panel"`. Default bind `127.0.0.1:8844`. Route families split into `routes_home`, `routes_jobs`, `routes_queue_control`, `routes_missions`, `routes_hygiene`, `routes_recovery`, `routes_bundle`, `routes_assistant`, `routes_automations`.
   - Shared auth/CSRF/mutation-guard plumbing lives in `panel/app.py` and is passed into each `register_*_routes(...)` call.
3. **Vera web — `voxera vera` / `make vera` / `voxera-vera.service`** (`src/voxera/vera_web/app.py`)
   - FastAPI app with `POST /chat`, `GET /chat/updates`, `POST /handoff`, `POST /clear`, `GET /vera/debug/session.json`. Default bind `127.0.0.1:8790`.

The panel and Vera can be run inline via CLI (`voxera panel`, `voxera vera`) or as user services.

## Config surfaces

Runtime configuration is split into two files and a few environment knobs. Both config loaders live in `src/voxera/config.py`:

- `load_config()` — loads operator / runtime config (`config.json` under the voxera user-config dir). Used for panel auth, queue toggles, mutation guards, etc.
- `load_app_config()` — loads application/provider config (`config.yml`) with curated brain slot defaults. Used by Vera and planner flows.
- `write_config_snapshot()` + `write_config_fingerprint()` — redacted snapshot + fingerprint written into the runtime data directory. Used by `voxera config snapshot` and by ops bundles.
- `load_runtime_env()` — loads `.env` if the caller opts in (`should_load_dotenv()` gate; tests explicitly disable with `VOXERA_LOAD_DOTENV=0`).

### Templates

- `config-templates/config.example.yml`
- `config-templates/policy.example.yml`

These are kept as living templates. They are not loaded at runtime automatically — they are reference material for operators bootstrapping a new config.

### Setup wizard

`voxera setup` runs `src/voxera/setup_wizard.py`. It:

- writes a starter `config.yml`
- offers to configure the four brain slots (`primary`, `fast`, `reasoning`, `fallback`) from the curated OpenRouter catalog (`src/voxera/data/openrouter_catalog.json`)
- optionally starts the stack and opens the panel / Vera

## Paths

From `src/voxera/paths.py`:

- `config_dir()` — XDG config dir via `platformdirs.user_config_path("voxera")`
- `data_dir()` — XDG data dir via `platformdirs.user_data_path("voxera")`
- `ensure_dirs()` — creates config + data + `audit/`
- `queue_root()` — `~/VoxeraOS/notes/queue`
- `queue_root_display()` — same, collapsed to `~/…`

The queue root is hard-coded to `~/VoxeraOS/notes/queue`. The repo README explicitly documents `~/VoxeraOS` as the officially supported workspace path for the alpha; several flows still assume that location.

### Queue directory contract

From `src/voxera/core/queue_daemon.py` and the queue modules under `core/`:

```
~/VoxeraOS/notes/queue/
├── inbox/                 submitted queue jobs waiting to be picked up
├── pending/               active jobs (planning, running, resumed)
│   └── approvals/         approval artifacts for paused jobs
├── done/                  terminal success jobs
├── failed/                terminal failed jobs (+ .error.json sidecars)
├── canceled/              terminal canceled jobs
├── recovery/              startup recovery quarantine
├── quarantine/            reconcile quarantine
├── _archive/              optional archive space
├── artifacts/             per-job runtime artifacts (plan.json, step_results.json, ...)
├── .daemon.lock           queue daemon single-writer lock
├── health.json            queue health snapshot
└── automations/
    └── .runner.lock       automation runner single-writer lock
```

Each job lives as `<bucket>/<job>.json` with an adjacent `<job>.state.json` lifecycle sidecar (managed by `queue_state.py`). Per-job runtime outputs live under `artifacts/<job>/`.

### Vera session artifacts

Vera persists session state under `queue_root()/artifacts/vera_sessions/<session_id>.json` (`src/voxera/vera/session_store.py`). This is queue-managed storage, not a separate data directory — Vera shares the queue root so everything stays in one audit-visible location.

## Systemd user services

Canonical units live under `deploy/systemd/user/`:

- `voxera-daemon.service`
  ```
  ExecStart=@VOXERA_PROJECT_DIR@/.venv/bin/voxera daemon
  ```
- `voxera-panel.service`
  ```
  ExecStart=@VOXERA_PROJECT_DIR@/.venv/bin/voxera panel --host 127.0.0.1 --port 8844
  ```
- `voxera-vera.service`
  ```
  ExecStart=@VOXERA_PROJECT_DIR@/.venv/bin/python -m uvicorn voxera.vera_web.app:app --host 127.0.0.1 --port 8790
  ```
- `voxera-automation.service` (one-shot, directly valid — uses `%h/VoxeraOS`, timer-owned, no `[Install]` section)
  ```
  ExecStart=%h/VoxeraOS/.venv/bin/voxera automation run-due-once
  ```
- `voxera-automation.timer` (`WantedBy=timers.target`)
  ```
  OnCalendar=minutely
  Persistent=true
  ```

The three long-running services declare `Restart=on-failure`, a 2-second restart backoff, and `WantedBy=default.target`. The daemon unit additionally caps `TimeoutStopSec=10` so graceful SIGTERM shutdown has room to write its shutdown sidecar. The automation service is `Type=oneshot` — it evaluates due definitions once per timer tick and exits. It is **timer-owned**: it has no `[Install]` section and is not enabled directly. Scheduling is owned entirely by `voxera-automation.timer`, which fires every minute with `Persistent=true` so missed ticks after a sleep/reboot are caught up on the next wake. The service stays directly addressable for status (`systemctl --user status voxera-automation.service`), logs (`journalctl --user -u voxera-automation.service`), and manual start for debugging.

`make services-install` rewrites `@VOXERA_PROJECT_DIR@` to the absolute project path, copies **all** units (including `voxera-automation.service`) into `$HOME/.config/systemd/user/`, runs `daemon-reload`, and then enables + starts the **enabled subset** with `systemctl --user`: `voxera-daemon.service`, `voxera-panel.service`, `voxera-vera.service`, and `voxera-automation.timer`. The automation service is copied but not directly enabled — the timer owns its cadence. The automation service uses `%h/VoxeraOS` directly (systemd resolves `%h` to the user's home directory) so it loads without the sed render step.

The top-level `systemd/` folder still carries `voxera-core.service` and `voxera-panel.service` as legacy references (they use `%h/VoxeraOS` directly). The supported path is `deploy/systemd/user/`.

## Ports

Current defaults (grep-verified against `Makefile`, `cli_runtime.py`, and the systemd units):

| Surface | Host | Port | Source |
|---|---|---|---|
| Panel (systemd) | `127.0.0.1` | `8844` | `deploy/systemd/user/voxera-panel.service` |
| Panel (`make panel` dev shortcut) | `127.0.0.1` | `8787` | `Makefile` |
| Vera | `127.0.0.1` | `8790` | `Makefile`, `deploy/systemd/user/voxera-vera.service`, `voxera vera` default |

The panel CLI accepts `--host` and `--port` overrides. The daemon binds no network ports; it is a filesystem-queue worker.

## Makefile — canonical targets

`make` targets (`Makefile`):

- **Format / lint / type / test**: `fmt`, `fmt-check`, `lint`, `type`, `test`, `check`
- **Golden surfaces**: `golden-update`, `golden-check`
- **Security**: `security-check` (runs `tests/test_security_redteam.py`)
- **Validation ladders**: `validation-check`, `full-validation-check`, `merge-readiness-check`, `premerge`
- **Typing ratchet**: `type-check`, `update-mypy-baseline`, `type-check-strict`
- **Dev servers**: `panel` (port 8787), `vera`
- **Daemon ops**: `daemon-restart`
- **Services**: `services-install`, `services-restart`, `services-status`, `services-stop`, `services-disable`, `vera-start`, `vera-stop`, `vera-restart`, `vera-status`, `vera-logs`
- **Release helpers**: `release-check`, `e2e`
- **Updates**: `update`, `update-fast` (wrap `scripts/update.sh`)

`merge-readiness-check = quality-check + release-check + security-check`. The CI required merge gate uses this label as-is — do not rename it without updating the GitHub workflow job name.

## Environment variables used at runtime

Observed from `Makefile` `TEST_ENV_PREFIX` and config-loading code:

- `VOXERA_LOAD_DOTENV` — 0 disables `.env` loading (used by tests).
- `VOXERA_DEV_MODE` — 1 plus `--allow-direct-mutation` enables direct CLI execution of mutating skills (intentionally loud; see `docs/EXECUTION_SECURITY_MODEL.md` § 9).
- `VOXERA_OPS_BUNDLE_DIR` — override ops bundle archive dir.
- `VOXERA_QUEUE_LOCK_STALE_S` — stale-lock threshold.
- `VOXERA_QUEUE_FAILED_MAX_AGE_S`, `VOXERA_QUEUE_FAILED_MAX_COUNT` — failed-bucket retention.
- `VOXERA_ARTIFACTS_RETENTION_DAYS`, `VOXERA_ARTIFACTS_RETENTION_MAX_COUNT` — artifacts retention.
- `VOXERA_QUEUE_PRUNE_MAX_AGE_DAYS`, `VOXERA_QUEUE_PRUNE_MAX_COUNT` — queue prune bounds.
- `VOXERA_PANEL_HOST`, `VOXERA_PANEL_PORT`, `VOXERA_PANEL_OPERATOR_USER`, `VOXERA_PANEL_OPERATOR_PASSWORD`, `VOXERA_PANEL_ENABLE_GET_MUTATIONS`, `VOXERA_PANEL_CSRF_ENABLED` — panel runtime toggles.
- `VOXERA_NOTIFY` — notification surface toggle.

Any variable not listed here should be treated as unsupported until it appears in `config.py` or a CLI option.

## Secrets

`src/voxera/secrets.py` wraps `keyring` with a file fallback. Used by:

- `voxera secrets set|get|unset` (see `cli.py`)
- provider key lookups (`OPENROUTER_API_KEY`) during brain initialization
- the Brave search client (`BRAVE_API_KEY`) used by Vera investigations

The secrets store is explicitly additive to `.env` — production setups should prefer the keyring path.

## Provider support (current reality)

From the README and the curated catalog under `src/voxera/data/openrouter_catalog.json`:

- **Officially tested**: OpenRouter, with Gemini 3 Flash (`google/gemini-3-flash-preview`) as the minimum supported slot.
- **Architecturally supported, not extensively validated**: Ollama and other OpenAI-compatible endpoints via the `brain/openai_compat.py` adapter.
- **Brain slots**: `primary`, `fast`, `reasoning`, `fallback` — set during `voxera setup`.
- **Fallback classification**: `brain/fallback.py` classifies provider failures as `TIMEOUT | AUTH | RATE_LIMIT | MALFORMED | NETWORK | UNKNOWN` and surfaces them through `voxera queue health` and `voxera doctor --quick`.

## Health and diagnostics

- `voxera queue health` — canonical queue health snapshot (grouped current state + recent history + counters). Supports `--json`, `--watch`, `--interval` (from `tests/golden/voxera_queue_health_help.txt`).
- `voxera doctor --quick` — runs quick provider readiness checks and summarises queue health.
- `voxera doctor --self-test` — fuller self test.
- `voxera ops capabilities` — prints the deterministic capabilities snapshot (`core/capabilities_snapshot.py`).
- `voxera ops bundle system` / `voxera ops bundle job <ref>` — incident bundles, archived by default under the data dir.
- `voxera automation list` — list saved automation definitions with key fields (id, enabled, trigger_kind, next_run_at_ms, last_run_at_ms, last_job_ref).
- `voxera automation show <id>` — detailed JSON view of a single automation definition.
- `voxera automation enable <id>` — set `enabled=True` and persist. Only changes the enabled flag; unrelated fields are preserved.
- `voxera automation disable <id>` — set `enabled=False` and persist. Only changes the enabled flag; unrelated fields are preserved.
- `voxera automation delete <id>` — delete the saved definition file only. History records under `automations/history/` are preserved as audit trail.
- `voxera automation history <id>` — show run history records for a definition, newest first. Uses the existing history file naming/linkage.
- `voxera automation run-now <id>` — force an immediate run of a single definition through the existing runner, bypassing the due-time check. Disabled definitions and unsupported trigger kinds are still rejected. Submits through the canonical inbox path — the queue remains the execution boundary.
- `voxera automation run-due-once` — automation runner entrypoint. Acquires the automation runner single-writer lock (`<queue_root>/automations/.runner.lock`) before evaluating definitions; if the lock is already held the command exits cleanly with a `BUSY` message and exit code 0. Evaluates saved automation definitions under `<queue_root>/automations/definitions/` and emits a normal canonical queue payload via the existing inbox path for every *enabled*, *supported* (`once_at`, `delay`, `recurring_interval`), due definition. One-shot triggers (`once_at`, `delay`) disable the definition after firing. Recurring triggers (`recurring_interval`) stay enabled and re-arm `next_run_at_ms` for the next interval. `--id <automation_id>` restricts the evaluation to a single definition (without lock). `recurring_cron` and `watch_path` are persisted but skipped by the runner. The `voxera-automation.timer` systemd unit invokes this command every minute.

### Voice subsystem status

`voxera doctor --quick` now includes a `voice: tts status` check that reports the TTS subsystem's configuration and availability state. The check loads `VoiceFoundationFlags` from the runtime config and produces a `TTSStatus` surface (`voice/tts_status.py`) with fields: `configured`, `available`, `enabled`, `backend`, `status`, `reason`, `last_error`, `schema_version`.

Status labels: `available` (foundation + output enabled + backend configured), `unconfigured` (enabled but no backend), `disabled` (foundation or output off), `unavailable` (catch-all). `available` means configured and enabled — it does NOT imply that synthesis has been tested or will succeed.

The STT request/response protocol (`voice/stt_protocol.py`) defines the canonical contract shapes for speech-to-text interactions. It is a protocol definition only — no runtime transcription backend is wired yet. See `09_CORE_OBJECTS_AND_SCHEMA_REFERENCE.md` for the full schema shapes.

Environment variables for voice configuration (loaded by `voice/flags.py`):
- `VOXERA_ENABLE_VOICE_FOUNDATION` — master toggle for the voice subsystem.
- `VOXERA_ENABLE_VOICE_INPUT` — enable STT input path.
- `VOXERA_ENABLE_VOICE_OUTPUT` — enable TTS output path.
- `VOXERA_VOICE_STT_BACKEND` — STT backend identifier.
- `VOXERA_VOICE_TTS_BACKEND` — TTS backend identifier.

See `08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md` for how these wire into STV validation.
