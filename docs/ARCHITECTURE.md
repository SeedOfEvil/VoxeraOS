# Architecture

Voxera OS is an intent-driven AI control plane that sits *above* a real Linux substrate.
It routes user goals through a planning → policy → execution → audit pipeline.

---

## Three-Layer Model

```
┌─────────────────────────────────────────────────────────┐
│  Experience Layer                                       │
│  Voice shell (planned) · Web Panel · CLI (voxera)       │
├─────────────────────────────────────────────────────────┤
│  AI Control Plane                                       │
│  Intent router · Mission planner · Queue daemon         │
│  Skill registry · Policy engine · Approval workflow     │
│  Audit log · Health monitor · Capabilities snapshot     │
├─────────────────────────────────────────────────────────┤
│  Substrate OS                                           │
│  Linux (Ubuntu) · Audio stack · Filesystem              │
│  Networking · Systemd user services · Podman            │
└─────────────────────────────────────────────────────────┘
```

---

## Data Flow

```
User intent (CLI / panel / future: voice)
    │
    ▼
Router (voxera/core/router.py)
    │  classify intent type
    ▼
Mission Planner (voxera/core/mission_planner.py)
    │  call primary brain → validate JSON output → check known skills
    │  fallback chain: primary → fast → reasoning → fallback brain
    ▼
Plan (list of PlanStep objects)
    │
    ▼
Policy Gate (voxera/policy.py)
    │  capability → allow / ask / deny
    │  ask → pause job, write approval artifact → wait
    ▼
Skill Runner (voxera/skills/runner.py)
    │  arg normalization → sandbox selection → execution
    ▼
Audit Log (voxera/audit.py)
    │  JSONL entry: action, args, result, timestamp
    ▼
Queue (done / failed / canceled)
```

---

## Module Map

```
src/voxera/
│
│   ── CLI (thin composition root + focused command families) ──
│
├── cli.py                    — Typer composition/registration root. Adds sub-apps from
│                               cli_queue.py (queue_app, inbox_app, artifacts_app),
│                               registers doctor via cli_doctor.register(app),
│                               and implements top-level commands (run, missions, ops,
│                               config, status, audit, panel, daemon, setup, demo, version).
│                               New CLI command families should be registered here but
│                               implemented in their own focused module.
├── cli_common.py             — Shared CLI helpers/primitives/options/constants:
│                               console, RUN_ARG_OPTION, OUT_PATH_OPTION,
│                               OPS_BUNDLE_ARCHIVE_DIR_OPTION, SNAPSHOT_PATH_OPTION,
│                               DEMO_QUEUE_DIR_OPTION, now_ms(), queue_dir_path().
├── cli_queue.py              — Queue/operator-facing command implementation + registration.
│                               Owns: queue_app, queue_approvals_app, queue_lock_app,
│                               inbox_app, artifacts_app Typer sub-apps and all their
│                               command implementations (status, prune, reconcile,
│                               approvals list/approve/deny, cancel, retry, delete, health,
│                               health-reset, lock status/unlock, inbox add/list, etc.).
├── cli_doctor.py             — Doctor command wiring/implementation boundary.
│                               Exposes register(app) to attach the doctor command to the
│                               root Typer app from cli.py.
│
├── config.py                 — Runtime config loader
│                               precedence: CLI flags > VOXERA_* env > config file > defaults
├── models.py                 — Pydantic data models: BrainConfig, AppConfig,
│                               PolicyApprovals, SkillManifest, PlanStep, RunResult
├── policy.py                 — Capability → allow/ask/deny decision engine
│                               maps skill capabilities to policy fields
├── audit.py                  — JSONL audit log (daily files in ~/.voxera/data/audit/)
├── health.py                 — Health snapshot: lock status, counters,
│                               last_ok/last_error timestamps
├── version.py                — Version from pyproject.toml or installed package
├── paths.py                  — XDG path resolution: config, data, queue directories
├── secrets.py                — Keyring integration; fallback to 0600 file
├── setup_wizard.py           — Interactive TUI first-run setup (voxera setup)
├── doctor.py                 — Diagnostic CLI: endpoint health, model test,
│                               lock/auth checks, quick offline mode
├── demo.py                   — Guided onboarding checklist (offline + online modes);
│                               creates deterministic demo jobs without destructive actions
├── incident_bundle.py        — Per-job incident bundle (zip export)
├── ops_bundle.py             — System snapshot bundle export
│
├── brain/
│   ├── base.py               — Brain protocol: async generate(), capability_test()
│   ├── openai_compat.py      — OpenAI-compatible adapter (OpenRouter, Ollama, etc.)
│   ├── gemini.py             — Google Gemini API adapter
│   └── json_recovery.py      — Malformed JSON rescue from LLM planner output
│
├── core/
│   │
│   │   ── Queue subsystem (thin composition root + focused domain modules) ──
│   │
│   ├── queue_daemon.py       — Composition/orchestration root for the queue subsystem.
│   │                           Inherits QueueExecutionMixin, QueueApprovalMixin,
│   │                           QueueRecoveryMixin. Owns: lock acquisition/release,
│   │                           watch/tick/poll orchestration, high-level job routing
│   │                           (mission vs assistant lane), config drift snapshotting,
│   │                           top-level daemon run loop, operator-facing status entrypoints,
│   │                           and re-exports `plan_mission` for monkeypatch compatibility.
│   │                           New queue lifecycle/process logic should go in the domain
│   │                           modules below, not back into this file.
│   │
│   ├── queue_execution.py    — QueueExecutionMixin. Owns: mission execution/process pipeline,
│   │                           inbox filtering (`_is_ready_job_file`), payload normalization
│   │                           (`_normalize_payload`), parse-retry behavior
│   │                           (`_load_job_payload_with_retry`), mission building/planning
│   │                           integration (`_build_mission_for_payload`),
│   │                           `process_job_file(...)` (full queued→planning→running→
│   │                           pending/done/failed flow), `process_pending_once(...)`.
│   │
│   ├── queue_recovery.py     — QueueRecoveryMixin. Owns: startup recovery
│   │                           (`recover_on_startup`), orphan approval/state detection
│   │                           (`_collect_orphan_approval_files`,
│   │                           `_collect_orphan_state_files`), quarantine path handling
│   │                           (`_quarantine_startup_recovery_path`), shutdown request
│   │                           handling (`request_shutdown`), in-flight fail-on-shutdown
│   │                           finalization (`_finalize_job_shutdown_failure`),
│   │                           clean/failed shutdown record helpers.
│   │
│   ├── queue_approvals.py    — QueueApprovalMixin. Owns: approval prompt/grant logic
│   │                           (`_queue_approval_prompt`), approval artifact path/read/write
│   │                           helpers (`_read_approval_artifact`, `_write_pending_artifacts`),
│   │                           pending approval payload building, normalization/canonicalization
│   │                           of approval refs (`canonicalize_approval_ref`,
│   │                           `_resolve_pending_approval_paths`), approval grants /
│   │                           approve-always behavior (`grant_approval_scope`,
│   │                           `_has_approval_grant`), approval resolution behavior
│   │                           (`resolve_approval`), pending approval notifications
│   │                           (`_notify_pending_approval`).
│   │
│   ├── queue_assistant.py    — Module-level functions (not a mixin). Owns: assistant advisory
│   │                           queue lane (`process_assistant_job`), provider construction
│   │                           (`create_assistant_brain`), ordered primary/fallback candidate
│   │                           logic (`assistant_brain_candidates`), advisory answer path
│   │                           (`assistant_answer_via_brain`), assistant response artifact
│   │                           path/handling (`assistant_response_artifact_path`), advisory
│   │                           failure handling, thread persistence/continuity
│   │                           (via `operator_assistant` helpers).
│   │
│   ├── queue_state.py        — `*.state.json` sidecar path/read/write/update helpers.
│   │                           Owns: `job_state_sidecar_path()`, `read_job_state()`,
│   │                           `write_job_state()`, `update_job_state_snapshot()`.
│   │                           Schema version: `JOB_STATE_SCHEMA_VERSION = 1`.
│   │
│   ├── queue_paths.py        — Deterministic bucket-transition helpers.
│   │                           Owns: `move_job_with_sidecar()` (atomic rename + co-move
│   │                           of `*.state.json` sidecar), `deterministic_target_path()`
│   │                           (collision-safe target naming with suffix tags).
│   │
│   │   ── Other core modules ──
│   │
│   ├── missions.py           — Mission templates + runner; YAML/JSON mission loading
│   │                           built-in mission IDs: work_mode, focus_mode,
│   │                           daily_checkin, incident_mode, wrap_up, system_check
│   ├── mission_planner.py    — LLM-based planning; fallback chains (primary→fast→fallback);
│   │                           deterministic write/terminal-demo routes; step normalization
│   │                           and rewriting; error classification; planner timeouts (25s)
│   ├── queue_inspect.py      — Queue status snapshots; bucket filtering
│   │                           (inbox / pending / done / failed / canceled)
│   ├── queue_hygiene.py      — `voxera queue prune`: removes stale job files from terminal
│   │                           buckets (done/failed/canceled); sidecar-aware; dry-run default
│   ├── queue_reconcile.py    — `voxera queue reconcile`: report-only orphan/duplicate detector;
│   │                           quarantine-first fix mode (`--fix [--yes]`); symlink-safe
│   ├── router.py             — Intent routing: CLI / voice / panel inputs
│   ├── inbox.py              — Atomic job intake; human-friendly entry point
│   ├── capabilities_snapshot.py — Runtime catalog: missions, skills, allowed_apps;
│   │                           used by planner as validation guardrail;
│   │                           `generate_capabilities_snapshot()`,
│   │                           `validate_mission_id_against_snapshot()`,
│   │                           `validate_mission_steps_against_snapshot()`
│   └── planner_context.py    — Preamble assembly for LLM prompt (Vera persona,
│                               system context, capabilities block)
│
├── skills/
│   ├── registry.py           — manifest.yml discovery + entrypoint loading
│   ├── runner.py             — Policy-gated execution + approval callbacks
│   ├── execution.py          — Job ID generation, sandbox runner selection,
│   │                           audit value sanitization
│   └── arg_normalizer.py     — Argument canonicalization; alias mapping
│                               (e.g., content → text, skill → skill_id)
│
├── audio/                    — Placeholder; STT/TTS planned for v0.3
│
└── panel/
    │
    │   ── Panel (thin composition root + focused route-domain modules) ──
    │
    ├── app.py                — FastAPI composition/wiring root. Creates the FastAPI app,
    │                           mounts static files, sets up Jinja2 templates, manages CSRF
    │                           and operator auth, wires shared helpers, and calls
    │                           register_*_routes() from each domain module. Route paths,
    │                           HTTP methods, auth guards, and redirect contracts were
    │                           preserved during the modularization passes (PRs #116–#118).
    │                           New panel routes should live in focused domain modules;
    │                           panel/app.py remains the composition root.
    ├── helpers.py            — Shared request/value parsing helpers reused by route modules:
    │                           coerce_int(), request_value() (query/form/JSON extraction).
    ├── routes_home.py        — Home/dashboard + queue-create route domain
    ├── routes_jobs.py        — Jobs list/detail + approvals/cancel/retry route domain
    ├── routes_queue_control.py — Queue delete/pause/resume route domain:
    │                           POST /queue/jobs/{ref}/delete, POST /queue/pause,
    │                           POST /queue/resume. All guarded by require_mutation_guard.
    ├── routes_assistant.py   — Operator assistant route domain + degraded advisory logic:
    │                           GET /assistant, POST /assistant/ask. Implements stall
    │                           detection, degraded-mode fallback (advisory_mode=
    │                           degraded_brain_only), and thread persistence.
    ├── routes_missions.py    — Mission + mission-template creation route domain:
    │                           GET/POST /missions/templates/create,
    │                           GET/POST /missions/create.
    ├── routes_bundle.py      — Job/system incident bundle download route domain:
    │                           GET /jobs/{job_id}/bundle, GET /bundle/system.
    │                           Bundles archived under queue_root/_archive/.
    ├── routes_hygiene.py     — Hygiene/operator-maintenance route domain:
    │                           GET /hygiene, POST /hygiene/prune-dry-run,
    │                           POST /hygiene/reconcile, POST /hygiene/health-reset.
    ├── routes_recovery.py    — Recovery/quarantine inspector route domain:
    │                           GET /recovery, GET /recovery/download/{bucket}/{name}.
    │                           Read-only listing + ZIP downloads with traversal protection.
    ├── templates/            — Jinja2 HTML: home.html, jobs.html, job_detail.html
    └── static/panel.css      — Panel stylesheet

src/voxera_builtin_skills/    — 11 built-in skills packaged as Python callables

skills/                       — Skill definitions (manifest.yml + .py per skill)
├── clipboard/copy/
├── clipboard/paste/
├── files/read_text/
├── files/write_text/         — supports mode=overwrite|append
├── system/status/
├── system/open_app/
├── system/open_url/
├── system/set_volume/
├── system/window_list/
└── sandbox/exec/             — Podman-based; rootless; --network=none by default

tests/                        — ~30 test files, ~7k lines (run `cloc --vcs git` for current counts)
├── test_mission_planner.py   — Planner fallback chains, error classification, JSON recovery (46 KB)
├── test_cli_queue.py         — Queue lifecycle, approvals, retry/cancel/delete (15 KB)
├── test_queue_daemon.py      — Failed-sidecar schema v1, retention pruning, lifecycle smoke
├── test_doctor.py            — Diagnostic endpoints, version alignment (14 KB)
└── ...                       — Config, execution, inbox, capabilities, CLI version tests

deploy/systemd/user/
├── voxera-daemon.service     — Queue processor; polls inbox/ every second
└── voxera-panel.service      — FastAPI panel; requires VOXERA_PANEL_OPERATOR_PASSWORD

docs/                         — Architecture, security, ops, roadmap, memory
Makefile                      — 30+ targets: dev, fmt, lint, type, test, e2e,
                                check, panel, services-*, update, release-check,
                                merge-readiness-check, full-validation-check
```

---

## Tech Stack

| Layer | Technology | Version |
|---|---|---|
| Language | Python | ≥ 3.10 |
| CLI framework | Typer + Rich | ≥ 0.12 / ≥ 13.7 |
| Data validation | Pydantic v2 | ≥ 2.6 |
| Web panel | FastAPI + Uvicorn | ≥ 0.110 / ≥ 0.27 |
| HTTP client | httpx (async) | ≥ 0.27 |
| Templating | Jinja2 | ≥ 3.1 |
| Config / secrets | platformdirs + keyring | ≥ 4.2 / ≥ 25.0 |
| YAML parsing | PyYAML | ≥ 6.0 |
| TOML parsing | tomli (Python < 3.11) | ≥ 2.0 |
| AI backends | Gemini API, OpenAI-compat | — |
| Sandbox | Podman (rootless) | — |
| Service management | systemd user units | — |
| Linting + formatting | Ruff | ≥ 0.6 |
| Type checking | Mypy + ratchet baseline | ≥ 1.10 |
| Testing | pytest + pytest-asyncio | ≥ 8.0 / ≥ 0.23 |
| Pre-commit hooks | pre-commit | ≥ 3.7 |

---

## Architectural Pattern: Thin Composition Root + Focused Domain Modules

A recurring structural pattern now present across the three main subsystems:

**Queue daemon** (`src/voxera/core/`)
- `queue_daemon.py` is the composition root — it inherits from `QueueExecutionMixin`, `QueueApprovalMixin`, `QueueRecoveryMixin` and owns lock/tick/routing only
- Domain-specific logic lives in the focused modules: `queue_execution.py`, `queue_approvals.py`, `queue_recovery.py`, `queue_assistant.py`, `queue_state.py`, `queue_paths.py`
- New queue process/lifecycle logic should go in the relevant domain module, not back into `queue_daemon.py`

**Panel** (`src/voxera/panel/`)
- `panel/app.py` is the composition root — it creates the FastAPI app, wires shared auth/CSRF/queue helpers, and calls `register_*_routes()` from each domain module
- Each route domain owns a focused set of paths: `routes_assistant.py`, `routes_missions.py`, `routes_bundle.py`, `routes_queue_control.py`, `routes_hygiene.py`, `routes_recovery.py`, `routes_home.py`, `routes_jobs.py`
- New panel route domains should live in focused route modules; `panel/app.py` remains the composition root

**CLI** (`src/voxera/`)
- `cli.py` is the composition root — it creates the Typer app, registers sub-apps from `cli_queue.py`, and registers the `doctor` command from `cli_doctor.py`
- Queue/operator command implementations live in `cli_queue.py`; doctor command wiring lives in `cli_doctor.py`; shared primitives live in `cli_common.py`
- New CLI command families should follow the same modular registration pattern rather than growing `cli.py`

---

## Key Principles

- **Capability-based permissions** — every skill declares what it needs (network, install, files, apps, settings); the policy engine decides allow / ask / deny per capability.
- **No silent risky actions** — high-risk steps pause the job and write an approval artifact; nothing executes without an explicit decision.
- **Audit and replay** — every action is logged to JSONL with what ran, why, and how to undo. Artifacts (`plan.json`, `actions.jsonl`, `stdout.txt`, `stderr.txt`) persist for each job.
- **Rollback-first** — config and operational changes favor reversible paths; failed jobs emit sidecars with structured error context.
- **Fail fast on bad state** — invalid skill manifests, malformed planner output, unknown mission IDs, and unsupported app targets are rejected with closest-match suggestions before any execution.
- **Brain tiering** — three configurable brain tiers (primary / fast+fallback / reasoning); planner degrades gracefully through the chain on timeout, auth failure, or malformed output.
- **Pluggable everything** — brains, skills, and missions are all registered/discovered at runtime; no hardcoded provider or skill list in the core engine.

---

## Queue / Job Lifecycle

```
Daemon startup
    │  acquire flock exclusive lock (.daemon.lock)
    │  run startup recovery:
    │    pending + in-flight markers → failed/ + sidecar (reason=recovered_after_restart)
    │    orphan approvals / state files → recovery/startup-<ts>/ quarantine
    ▼
inbox/*.json
    │  daemon tick (every 1s)
    ▼
policy gate
    ├── allow → execute (persist step outcomes/state) → done/
    ├── ask   → write approval artifact + state sidecar update → pending/approvals/
    │           (resume on approve, move to failed/ on deny)
    └── deny  → failed/ + error sidecar (schema v1)

SIGTERM / SIGINT
    │  stop intake; mark in-flight job failed/ + sidecar (reason=shutdown)
    │  release lock; exit cleanly within TimeoutStopSec
    ▼
canceled/ (operator cancel via CLI or panel)

failed/*.json + failed/*.error.json (sidecar)
    │  voxera queue prune: max-age-days + max-count (terminal buckets only)
    ▼
pruned (oldest logical units removed first; symlink-safe)

notes/queue/quarantine/  (voxera queue reconcile --fix --yes)
    │  orphan sidecars + orphan approvals moved here; never deleted
    ▼
operator can restore manually or prune explicitly
```

Each job also emits a compact `*.state.json` sidecar (same stem as job file) to capture
operator truth beyond bucket location.

**Queue artifact types:**
- Primary job file: `inbox/<job>.json`, `pending/<job>.json`, `done/<job>.json`, `failed/<job>.json`, `canceled/<job>.json`
- `<job>.state.json` — lifecycle state sidecar (co-moved with job on bucket transitions)
- `<job>.pending.json` — awaiting-approval metadata (written to `pending/` when `awaiting_approval`)
- `<job>.approval.json` — approval prompt artifact (written to `pending/approvals/`)
- `<job>.error.json` — failed job error sidecar (schema_version=1, required: job/error/timestamp_ms)
- `artifacts/<job_stem>/assistant_response.json` — assistant advisory lane response artifact
- `recovery/startup-<ts>/` — orphan approvals/state files quarantined during daemon startup recovery

**Module ownership:**
- `src/voxera/core/queue_daemon.py` — lock handling, tick loop, high-level routing; orchestrates all other modules
- `src/voxera/core/queue_execution.py` — `process_job_file()`, `process_pending_once()`, inbox filtering, payload normalization, planning integration
- `src/voxera/core/queue_recovery.py` — `recover_on_startup()`, orphan detection, quarantine, `request_shutdown()`, shutdown failure finalization
- `src/voxera/core/queue_approvals.py` — approval prompts, pending artifact write/read, `resolve_approval()`, `grant_approval_scope()`
- `src/voxera/core/queue_assistant.py` — `process_assistant_job()`, `assistant_answer_via_brain()`, `assistant_response_artifact_path()`
- `src/voxera/core/queue_state.py` — `job_state_sidecar_path()`, `read_job_state()`, `write_job_state()`, `update_job_state_snapshot()`
- `src/voxera/core/queue_paths.py` — `move_job_with_sidecar()`, `deterministic_target_path()`

**`*.state.json` sidecar tracks:**
- `lifecycle_state`: `queued|planning|running|awaiting_approval|resumed|done|step_failed|blocked|canceled`
- `advisory_running` (assistant advisory lane jobs only)
- step progress: `current_step_index`, `total_steps`, `last_completed_step`, `last_attempted_step`
- `terminal_outcome` (terminal only): `succeeded|failed|blocked|denied|canceled`
- contextual fields when applicable: `failure_summary`, `blocked_reason`, `approval_status`
- transition timestamps under `transitions`

---

## Config Precedence

```
CLI flags (highest)
    │
VOXERA_* environment variables
    │
~/.config/voxera/config.json   (runtime ops config; panel/queue settings, JSON only)
~/.config/voxera/config.yml    (app config; brain/mode/privacy; written by voxera setup)
    │
Built-in defaults (lowest)
```

Secrets: keyring preferred; fallback to `~/.config/voxera/env` (mode 0600).

---

## Validation Tiers

| Target | When to run | What it covers |
|---|---|---|
| `make merge-readiness-check` | Before every PR merge | fmt + lint + mypy ratchet + release consistency |
| `make full-validation-check` | Before releases or risky changes | merge-readiness + failed-sidecar guardrails + full pytest + E2E smoke |
| `make test-failed-sidecar` | Queue daemon changes | Sidecar schema policy + lifecycle smoke tests |

---

See also: `docs/BOOTSTRAP.md`, `docs/SECURITY.md`, `docs/ROADMAP.md`, `docs/ops.md`.

---

## Public Contract Checklist (Regression Guard)

- **CLI command names/options unchanged**
  - Root commands and nested groups (`config`, `queue`, `ops`) are snapshot-tested.
  - Help surfaces for key commands (for example `doctor`, `queue status`) are snapshot-tested.
- **Panel route paths unchanged**
  - FastAPI route surface is snapshot-tested against the public paths used by operators.
- **Panel jobs mutation redirects are relative by design**
  - Redirects target `/jobs?...` to remain origin-safe in proxy/front-door/root-path deployments.
- **Queue artifacts/state transitions unchanged**
  - Daemon startup recovery keeps deterministic failed/quarantine behavior.
  - Approval deny flow keeps the `pending -> failed` transition and failed sidecar schema/fields.

When evolving CLI/panel/daemon behavior, update tests and this checklist intentionally in the same change.
