# 02 — Configuration and Runtime Surfaces

This document records the configuration files, environment variables,
processes, and HTTP/CLI surfaces a current VoxeraOS install exposes.

## Project metadata

- Package name: `voxera-os`
- Version: `0.1.9` (`pyproject.toml [project] version`).
- Python: `>=3.10`.
- Console script: `voxera = "voxera.cli:app"`.
- Runtime deps: `typer`, `rich`, `pyyaml`, `pydantic>=2.6`, `httpx`, `fastapi`,
  `uvicorn`, `jinja2`, `keyring`, `platformdirs`, `tomli` on Python <3.11.
- Dev deps: `ruff`, `pytest`, `pytest-asyncio`, `mypy`, `types-PyYAML`, `pre-commit`.

## Configuration sources

`src/voxera/config.py` resolves runtime config from a layered set of sources:

1. Defaults in `VoxeraConfig` (frozen dataclass).
2. YAML application config at `~/.config/voxera/config.yml`
   (`DEFAULT_CONFIG_NAME`), validated by `models.AppConfig`.
3. JSON runtime config at `~/.config/voxera/config.json`
   (`_DEFAULT_RUNTIME_CONFIG`).
4. Environment variables (loaded from `~/.config/voxera/env` and `.env` if
   `should_load_dotenv()` is true; controlled by `VOXERA_LOAD_DOTENV`).
5. Per-process overrides supplied via CLI options (e.g.
   `voxera panel --host/--port`).

The resolved snapshot may be written via
`config_snapshot_impl(...)` and fingerprinted via
`write_config_fingerprint(...)`.

## Environment variables

The current `.env.example` plus `Makefile` `TEST_ENV_PREFIX` reveal the
canonical environment surface:

| Variable | Purpose |
| -------- | ------- |
| `VOXERA_QUEUE_ROOT` | Override queue root (`paths.queue_root()` default `~/VoxeraOS/notes/queue`). |
| `VOXERA_QUEUE_LOCK_STALE_S` | Stale lock threshold for the daemon singleton lock. |
| `VOXERA_QUEUE_FAILED_MAX_AGE_S` | Max age for terminal failed jobs before hygiene prunes. |
| `VOXERA_QUEUE_FAILED_MAX_COUNT` | Max count for terminal failed jobs. |
| `VOXERA_QUEUE_PRUNE_MAX_AGE_DAYS` | General prune cap. |
| `VOXERA_QUEUE_PRUNE_MAX_COUNT` | General prune cap by count. |
| `VOXERA_ARTIFACTS_RETENTION_DAYS` | Artifact retention by age. |
| `VOXERA_ARTIFACTS_RETENTION_MAX_COUNT` | Artifact retention by count. |
| `VOXERA_PANEL_HOST` / `VOXERA_PANEL_PORT` | Panel bind. |
| `VOXERA_PANEL_OPERATOR_USER` / `VOXERA_PANEL_OPERATOR_PASSWORD` | Panel basic auth (password normally provided via systemd drop-in). |
| `VOXERA_PANEL_CSRF_ENABLED` | Panel CSRF gate. |
| `VOXERA_PANEL_ENABLE_GET_MUTATIONS` | Dev-only escape hatch. |
| `VOXERA_OPS_BUNDLE_DIR` | Override ops bundle output dir. |
| `VOXERA_DEV_MODE` | Enables dev-only escape hatches (e.g. direct CLI mutation). |
| `VOXERA_NOTIFY` | Enables operator notifications. |
| `VOXERA_LOAD_DOTENV` | Set to `0` in tests to disable `.env` loading. |

The `Makefile` test target unsets all of the above before invoking `pytest`
to ensure deterministic CI runs.

## Long-running processes

VoxeraOS expects three local services to be running for full functionality.

| Service | Command | Default bind |
| ------- | ------- | ------------ |
| Queue daemon | `voxera daemon` | n/a (filesystem-driven) |
| Panel | `voxera panel --host 127.0.0.1 --port 8844` | `127.0.0.1:8844` |
| Vera web app | `python -m uvicorn voxera.vera_web.app:app --host 127.0.0.1 --port 8790` | `127.0.0.1:8790` |

The panel default in `Makefile` `panel` target is `127.0.0.1:8787`; the
systemd unit installs as `:8844`.

### systemd user units

`deploy/systemd/user/`:

- `voxera-daemon.service` — `Type=simple`, `Restart=on-failure`,
  `TimeoutStopSec=10`, runs `voxera daemon` from `@VOXERA_PROJECT_DIR@`.
- `voxera-panel.service` — runs `voxera panel --host 127.0.0.1 --port 8844`.
- `voxera-vera.service` — runs `uvicorn voxera.vera_web.app:app --host 127.0.0.1 --port 8790`.

`make services-install` templates `@VOXERA_PROJECT_DIR@` with the absolute
repo path, copies the units to `~/.config/systemd/user`, runs
`systemctl --user daemon-reload`, then enables and starts all three.

`make services-restart` re-loads and restarts only units that are already
enabled. `make services-status / services-stop / services-disable` are
provided for symmetry.

`systemd/` (top-level) holds older non-templated units kept for reference.

## CLI surface (`voxera`)

`src/voxera/cli.py` is the Typer composition root. The subcommand tree
observed in code:

- `voxera version` — print version.
- `voxera setup` — first-run wizard (`run_setup`).
- `voxera demo [--queue-dir] [--online] [--yes] [--json]`.
- `voxera status` — config summary.
- `voxera audit [--n N]` — tail of `audit.jsonl`.
- `voxera doctor …` — diagnostic runner (registered via `cli_doctor.register`).
- `voxera run SKILL_ID --arg key=value [--dry-run] [--allow-direct-mutation]`.
- `voxera panel [--host] [--port]`.
- `voxera daemon [--once] [--queue-dir] [--poll-interval] [--auto-approve-ask]`.
- `voxera vera [--host] [--port]` — launches the Vera web app via uvicorn.
- `voxera config show | snapshot | validate`.
- `voxera config-show` — backwards-compatible alias.
- `voxera secrets set | get | unset`.
- `voxera skills list`.
- `voxera missions list | plan GOAL [--dry-run] [--freeze-capabilities-snapshot] [--deterministic] | run MISSION_ID [--dry-run]`.
- `voxera queue init | status | bundle | health | health-reset | cancel | retry | unlock | pause | resume | prune | reconcile`.
- `voxera queue approvals list | approve | deny`.
- `voxera queue lock status`.
- `voxera queue files find | grep | tree | copy | move | rename`.
- `voxera artifacts prune`.
- `voxera inbox add | list`.
- `voxera ops capabilities`.
- `voxera ops bundle system | job`.

(See `class_inventory.json` and `function_inventory.json` for the underlying
implementation symbols.)

## Panel HTTP surface (`src/voxera/panel/app.py`)

The panel is a FastAPI application that mounts a `static/` directory and
Jinja2 templates from `panel/templates/`. Its routes are defined across one
module per surface:

| Module | Endpoints |
| ------ | --------- |
| `routes_home.py` | `GET /`, `GET /queue/create`, `POST /queue/create` |
| `routes_jobs.py` | `POST /queue/approvals/{ref}/approve`, `POST /queue/approvals/{ref}/approve-always`, `POST /queue/approvals/{ref}/deny`, `GET /jobs`, `GET /jobs/{job_id}/progress`, `GET /queue/jobs/{job}/progress`, `GET /jobs/{job_id}`, `GET /queue/jobs/{job}/detail`, `POST /queue/jobs/{ref}/cancel`, `POST /queue/jobs/{ref}/retry` |
| `routes_missions.py` | `GET /missions/templates/create`, `POST /missions/templates/create`, `GET /missions/create`, `POST /missions/create` |
| `routes_assistant.py` | `GET /assistant`, `POST /assistant/ask`, `GET /assistant/progress/{request_id}` |
| `routes_hygiene.py` | `GET /hygiene`, `POST /hygiene/prune-dry-run`, `POST /hygiene/reconcile`, `POST /hygiene/health-reset` |
| `routes_recovery.py` | `GET /recovery`, `GET /recovery/download/{bucket}/{name}` |
| `routes_bundle.py` | `GET /jobs/{job_id}/bundle`, `GET /bundle/system` |
| `routes_queue_control.py` | `POST /queue/jobs/{ref}/delete`, `POST /queue/pause`, `POST /queue/resume` |
| `routes_vera.py` | `GET /vera`, `POST /vera/chat` |

Templates: `home.html`, `jobs.html`, `job_detail.html`, `assistant.html`,
`hygiene.html`, `recovery.html`, `vera.html`, `_daemon_health_widget.html`.

Auth + CSRF state is stored in `panel/auth_state_store.py`. Job
presentation helpers live in `job_presentation.py` and
`job_detail_sections.py`.

## Vera web surface (`src/voxera/vera_web/app.py`)

The Vera web app is a FastAPI uvicorn target with a single Jinja2 template
(`vera_web/templates/index.html`). Routes:

| Method | Path | Purpose |
| ------ | ---- | ------- |
| `GET` | `/` | Render chat surface for the cookie-bound session. |
| `POST` | `/chat` | Main chat orchestration: voice ingestion → early-exit dispatch → execution mode classification → preview build → reply generation → guardrails → response shaping. |
| `GET` | `/chat/updates` | Polling endpoint for new turns/timestamps. |
| `POST` | `/handoff` | Submit the active session preview into the queue inbox. |
| `POST` | `/clear` | Clear current session turns and shared context. |
| `GET` | `/vera/debug/session.json` | Operator debug snapshot for the current session. |

Vera state lives in `~/VoxeraOS/notes/queue/artifacts/vera_sessions/` (see
file 05).

## Filesystem surfaces

| Path | Purpose |
| ---- | ------- |
| `~/.config/voxera/` | App and runtime config; secret file fallback. |
| `~/.local/share/voxera/audit/` | JSONL audit log. |
| `~/VoxeraOS/notes/queue/` | Queue root (`paths.queue_root()`). |
| `~/VoxeraOS/notes/queue/inbox/` | Newly enqueued jobs. |
| `~/VoxeraOS/notes/queue/pending/` | Active planning/running jobs. |
| `~/VoxeraOS/notes/queue/pending/approvals/` | Approval payloads. |
| `~/VoxeraOS/notes/queue/done/` `failed/` `canceled/` | Terminal buckets. |
| `~/VoxeraOS/notes/queue/artifacts/` | Per-job artifact directories. |
| `~/VoxeraOS/notes/queue/artifacts/vera_sessions/` | Vera session JSON store. |
| `~/VoxeraOS/notes/queue/_archive/` | Recovery / archive bucket. |
| `~/VoxeraOS/notes/queue/.daemon.lock` | Singleton daemon lock. |

`~/VoxeraOS/notes/` is the bounded operator workspace for file skills (see
`core/file_intent.py`); the queue control plane subtree is excluded from
that workspace.
