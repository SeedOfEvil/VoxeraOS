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

## Project Folder Structure

```
VoxeraOS/
├── src/
│   ├── voxera/                      — main application package
│   │   ├── cli.py                   — Typer composition root
│   │   ├── cli_common.py            — shared CLI primitives/options/constants
│   │   ├── cli_queue.py             — queue/operator command family
│   │   ├── cli_config.py            — runtime config command implementations
│   │   ├── cli_skills_missions.py   — skills/missions/run command implementations
│   │   ├── cli_ops.py               — ops capability/bundle command implementations
│   │   ├── cli_runtime.py           — setup/demo/status/audit/panel/daemon implementations
│   │   ├── cli_doctor.py            — doctor command wiring
│   │   ├── config.py                — runtime config loader + fingerprinting
│   │   ├── models.py                — Pydantic models (BrainConfig, AppConfig, …)
│   │   ├── policy.py                — capability → allow/ask/deny engine
│   │   ├── audit.py                 — JSONL audit log writer/reader
│   │   ├── health.py                — health snapshot r/w + backoff constants
│   │   ├── health_reset.py          — health snapshot reset helper
│   │   ├── health_semantics.py      — human-readable health section builder
│   │   ├── operator_assistant.py    — thread persistence, ASSISTANT_JOB_KIND
│   │   ├── incident_bundle.py       — per-job incident bundle (zip export)
│   │   ├── ops_bundle.py            — system snapshot bundle export
│   │   ├── version.py               — version from pyproject.toml
│   │   ├── paths.py                 — XDG path resolution (config/data/queue)
│   │   ├── secrets.py               — keyring + 0600 file fallback
│   │   ├── setup_wizard.py          — interactive first-run TUI wizard
│   │   ├── doctor.py                — diagnostic runner (doctor_sync)
│   │   ├── demo.py                  — guided onboarding checklist
│   │   ├── brain/
│   │   │   ├── base.py              — Brain protocol (generate, capability_test)
│   │   │   ├── openai_compat.py     — OpenAI-compatible adapter (OpenRouter, Ollama…)
│   │   │   ├── gemini.py            — Google Gemini API adapter
│   │   │   ├── fallback.py          — fallback reason enum + exception classifier
│   │   │   └── json_recovery.py     — malformed JSON rescue from LLM output
│   │   ├── core/
│   │   │   ├── queue_daemon.py      — MissionQueueDaemon (composition root)
│   │   │   ├── queue_execution.py   — QueueExecutionMixin
│   │   │   ├── queue_contracts.py   — canonical envelope + step/execution result shaping
│   │   │   ├── queue_recovery.py    — QueueRecoveryMixin
│   │   │   ├── queue_approvals.py   — QueueApprovalMixin
│   │   │   ├── queue_assistant.py   — assistant advisory lane (module-level fns)
│   │   │   ├── queue_state.py       — *.state.json sidecar path/r/w/update helpers
│   │   │   ├── queue_paths.py       — move_job_with_sidecar, deterministic_target_path
│   │   │   ├── queue_inspect.py     — JobLookup, list_jobs, queue_snapshot
│   │   │   ├── queue_hygiene.py     — terminal bucket pruning
│   │   │   ├── queue_reconcile.py   — orphan/duplicate detection + fix
│   │   │   ├── missions.py          — MissionTemplate, MissionRunner, built-ins
│   │   │   ├── mission_planner.py   — LLM planning + brain fallback orchestration
│   │   │   ├── router.py            — intent routing (local vs cloud lane)
│   │   │   ├── inbox.py             — atomic job intake
│   │   │   ├── capabilities_snapshot.py  — runtime skill/mission catalog + validation
│   │   │   └── planner_context.py   — LLM prompt preamble assembly
│   │   ├── skills/
│   │   │   ├── registry.py          — manifest.yml discovery + strict health classification (valid/invalid/incomplete/warning) + entrypoint loading
│   │   │   ├── runner.py            — policy-gated skill execution + approval callbacks
│   │   │   ├── execution.py         — sandbox selection + audit value sanitization
│   │   │   └── arg_normalizer.py    — arg canonicalization + alias mapping
│   │   ├── audio/                   — placeholder (STT/TTS, v0.3+)
│   │   └── panel/
│   │       ├── app.py               — FastAPI composition/wiring root
│   │       ├── helpers.py           — request_value, coerce_int
│   │       ├── assistant.py         — assistant thread helpers
│   │       ├── routes_home.py       — GET /, POST /queue/submit
│   │       ├── routes_jobs.py       — GET/POST /jobs, /jobs/{id}/…
│   │       ├── routes_queue_control.py  — POST /queue/pause|resume|delete
│   │       ├── routes_assistant.py  — GET/POST /assistant
│   │       ├── routes_missions.py   — GET/POST /missions/…
│   │       ├── routes_bundle.py     — GET /jobs/{id}/bundle, /bundle/system
│   │       ├── routes_hygiene.py    — GET/POST /hygiene
│   │       ├── routes_recovery.py   — GET /recovery, /recovery/download/…
│   │       ├── templates/
│   │       │   ├── home.html
│   │       │   ├── jobs.html
│   │       │   ├── job_detail.html
│   │       │   ├── assistant.html
│   │       │   ├── hygiene.html
│   │       │   ├── recovery.html
│   │       │   └── _daemon_health_widget.html
│   │       └── static/panel.css
│   └── voxera_builtin_skills/       — 11 built-in Python skill callables
│       ├── clipboard_copy.py        clipboard_paste.py
│       ├── files_read_text.py       files_write_text.py
│       ├── open_app.py              open_url.py
│       ├── sandbox_exec.py          set_volume.py
│       ├── system_status.py         terminal_run_once.py
│       └── window_list.py
├── skills/                          — skill manifest definitions (manifest.yml per skill)
│   ├── clipboard/{copy,paste}/
│   ├── files/{read_text,write_text}/
│   ├── sandbox/exec/
│   └── system/{open_app,open_url,set_volume,status,terminal_run_once,window_list}/
├── missions/                        — example/repo mission JSON files
│   ├── sandbox_smoke.json
│   └── sandbox_net.json
├── tests/                           — pytest suite (~60 files, ~7k lines)
├── docs/
│   ├── ARCHITECTURE.md              — this file
│   ├── BOOTSTRAP.md                 — first-run install guide
│   ├── CODEX_MEMORY.md              — PR/milestone change log
│   ├── LOCAL_MODELS.md              — local model setup (Ollama)
│   ├── ROADMAP.md                   — current roadmap
│   ├── ROADMAP_0.1.{4,5,6}.md      — completed roadmap archives
│   ├── SECURITY.md                  — security model + threat boundaries
│   ├── UBUNTU_TESTING.md            — Ubuntu-specific testing notes
│   └── ops.md                       — operator runbook
├── deploy/systemd/user/             — packaged systemd user units
│   ├── voxera-daemon.service
│   └── voxera-panel.service
├── systemd/                         — dev/legacy systemd units
│   ├── voxera-core.service
│   └── voxera-panel.service
├── config-templates/
│   ├── config.example.yml
│   └── policy.example.yml
├── scripts/
│   ├── e2e_smoke.sh   e2e_golden4.sh   e2e_opsconsole.sh
│   ├── mypy_ratchet.py
│   └── update.sh
├── tools/mypy-baseline.txt
├── AGENT.md   CODEX.md   LICENSE   NOTICE
├── Makefile                         — 30+ targets (dev, fmt, lint, type, test, e2e…)
└── pyproject.toml   mypy.ini   uv.lock
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

## Component Interaction Map

Runtime component topology — how subsystems call each other:

```
┌─────────────────────────────────────────────────────────────────────┐
│                        User Entry Points                            │
│  ┌──────────────────────────────┐  ┌───────────────────────────┐   │
│  │  CLI  (Typer)                │  │  Web Panel  (FastAPI)      │   │
│  │  cli.py (root)               │  │  panel/app.py (root)       │   │
│  │  cli_queue.py  cli_doctor.py │  │  routes_home.py            │   │
│  │  cli_common.py               │  │  routes_jobs.py            │   │
│  └─────────────┬────────────────┘  │  routes_queue_control.py   │   │
│                │                   │  routes_assistant.py        │   │
│                │ inbox.add()        │  routes_missions.py        │   │
│                │ writes job JSON   │  routes_bundle.py           │   │
│                │                   │  routes_hygiene.py          │   │
│                │                   │  routes_recovery.py         │   │
│                │                   └──────────────┬──────────────┘   │
└────────────────┼──────────────────────────────────┼─────────────────┘
                 │                                  │
                 ▼                                  │ reads/controls queue
┌────────────────────────────────────────────────────────────────────┐
│                   Queue Directory  (filesystem)                    │
│   ~/VoxeraOS/notes/queue/                                          │
│   inbox/  pending/  done/  failed/  canceled/                      │
│   pending/approvals/                                               │
│   recovery/startup-<ts>/   quarantine/   _archive/                 │
└────────────────────────────┬───────────────────────────────────────┘
                             │ flock exclusive lock
                             ▼
┌────────────────────────────────────────────────────────────────────┐
│              Queue Daemon  (voxera-daemon.service)                 │
│                                                                    │
│  MissionQueueDaemon  (queue_daemon.py — composition root)          │
│  │                                                                 │
│  ├─ QueueExecutionMixin   (queue_execution.py)                     │
│  │    inbox filter → payload normalize → parse-retry →             │
│  │    plan → MissionRunner → step execution → state update         │
│  ├─ QueueApprovalMixin    (queue_approvals.py)                     │
│  │    approval prompt → artifact write → gate →                    │
│  │    resolve (approve/deny) → grant scope                         │
│  ├─ QueueRecoveryMixin    (queue_recovery.py)                      │
│  │    startup orphan detection → quarantine                        │
│  │    SIGTERM → in-flight finalization → shutdown record           │
│  └─ queue_assistant module  (queue_assistant.py)                   │
│       assistant_question jobs → brain → response artifact          │
│                                                                    │
│  Supporting helpers:                                               │
│  queue_state.py   queue_paths.py   queue_inspect.py               │
│  queue_hygiene.py   queue_reconcile.py                             │
└────────────┬─────────────────────────┬─────────────────────────────┘
             │ plan_mission()           │ process_assistant_job()
             ▼                         ▼
┌───────────────────────┐  ┌──────────────────────────────────────┐
│   Mission Planner     │  │       Operator Assistant             │
│   mission_planner.py  │  │       operator_assistant.py          │
│   + MissionRunner     │  │       queue_assistant.py (queue lane)│
│     (missions.py)     │  │       assistant.py (panel thread)    │
└───────────┬───────────┘  └────────────────┬─────────────────────┘
            │                               │
            │ select brain provider         │
            └────────────────┬──────────────┘
                             ▼
┌────────────────────────────────────────────────────────────────────┐
│                         Brain Layer                                │
│  brain/base.py           Brain protocol (generate/capability_test) │
│  brain/gemini.py         Google Gemini API adapter                 │
│  brain/openai_compat.py  OpenAI-compatible adapter                 │
│  brain/fallback.py       fallback reason classifier                │
│  brain/json_recovery.py  malformed JSON rescue                     │
└────────────────────────────┬───────────────────────────────────────┘
                             │ step list
                             ▼
┌────────────────────────────────────────────────────────────────────┐
│                      Policy + Skills                               │
│  policy.py              capability → allow / ask / deny            │
│  skills/registry.py     manifest.yml discovery + loading           │
│  skills/runner.py       policy-gated execution + approval callbacks│
│  skills/execution.py    sandbox selection + audit sanitization     │
│  skills/arg_normalizer.py  arg canonicalization + alias mapping    │
└────────────────────────────┬───────────────────────────────────────┘
                             │ per-action JSONL + health counters
                             ▼
┌────────────────────────────────────────────────────────────────────┐
│               Cross-Cutting Infrastructure                         │
│  audit.py            JSONL audit log (daily, ~/.voxera/data/)      │
│  health.py           health snapshot r/w + exponential backoff     │
│  health_reset.py     health snapshot reset                         │
│  health_semantics.py human-readable health sections                │
│  config.py           runtime config loader + fingerprinting        │
│  models.py           Pydantic models (BrainConfig, AppConfig…)     │
│  paths.py            XDG path resolution (config/data/queue)       │
│  secrets.py          keyring + 0600 file fallback                  │
└────────────────────────────────────────────────────────────────────┘
```

---

## Module Map

```
src/voxera/
│
│   ── CLI (thin composition root + focused command families) ──
│
├── cli.py                    — Typer composition/registration root. Owns public app,
│                               command/group registration, root callback/version wiring,
│                               and compatibility re-export surfaces used by tests/monkeypatches.
├── cli_config.py             — Runtime config command implementations (show/snapshot/validate).
├── cli_skills_missions.py    — skills list + run + missions list/plan/run implementations.
├── cli_ops.py                — ops capabilities + ops bundle command implementations.
├── cli_runtime.py            — setup/demo/status/audit/panel/daemon implementations.
│                               New CLI command families should be registered in cli.py but
│                               implemented in a focused cli_<domain>.py module.
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
│                               and operator-visible skill registry health summary (`skills.registry`)
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
│   ├── runner.py             — Runtime capability enforcement (fail-closed) + policy/approval execution gate
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
                                merge-readiness-check, golden-check,
                                validation-check, full-validation-check
```

---

## Subsystem Maps

### Queue Subsystem Composition

```
MissionQueueDaemon  (queue_daemon.py — composition root)
│
│  mixin inheritance (Python MRO)
├── QueueExecutionMixin    (queue_execution.py)
│     _is_ready_job_file / _normalize_payload / _load_job_payload_with_retry
│     _build_inline_mission / _build_mission_for_payload
│     process_job_file()      queued → planning → running → done / failed / pending
│     process_pending_once()  awaiting_approval → resumed / failed
│
├── QueueApprovalMixin     (queue_approvals.py)
│     _queue_approval_prompt / _write_pending_artifacts / _ensure_hard_approval_gate
│     canonicalize_approval_ref / _resolve_pending_approval_paths
│     resolve_approval()       approve → resume job | deny → move to failed/
│     grant_approval_scope() / _has_approval_grant()
│     pending_approvals_snapshot()
│
└── QueueRecoveryMixin     (queue_recovery.py)
      recover_on_startup()     in-flight jobs → failed/ + sidecar (reason=recovered_after_restart)
                               orphan approvals/state → recovery/startup-<ts>/
      request_shutdown() / _finalize_job_shutdown_failure()
      _record_clean_shutdown() / _record_failed_shutdown()

queue_daemon.py also calls module-level functions from:
└── queue_assistant  (queue_assistant.py — not a mixin)
      process_assistant_job(daemon, job_path, payload)
      create_assistant_brain(provider) / assistant_brain_candidates(cfg)
      assistant_answer_via_brain(...) / assistant_response_artifact_path(daemon, job_ref)

All daemon + mixin code uses shared helpers:
├── queue_state.py       job_state_sidecar_path / read_job_state / write_job_state
│                        update_job_state_snapshot   (JOB_STATE_SCHEMA_VERSION = 1)
├── queue_paths.py       move_job_with_sidecar / deterministic_target_path
├── queue_inspect.py     JobLookup / list_jobs / queue_snapshot
├── queue_hygiene.py     prune_terminal_buckets  (done/ failed/ canceled/)
└── queue_reconcile.py   reconcile_queue  (orphan detection + quarantine-first fix)
```

---

### Queue Job State Machine

```
             [daemon startup]
                   │ recover_on_startup()
                   ▼
              ┌─────────┐
              │ queued  │  ◄── inbox/*.json picked up by tick loop
              └────┬────┘
                   │ _build_mission_for_payload / plan_mission()
                   ▼
             ┌──────────┐
             │ planning │
             └────┬─────┘
                  │ MissionRunner.run()
                  ▼
            ┌─────────┐
            │ running │ ◄──────────────────────────────────┐
            └────┬────┘                                    │
                 │                                         │
      ┌──────────┼────────────────────┐                    │
      │          │                    │                    │
      ▼          ▼                    ▼                    │
   allow        ask                 deny                   │
      │          │                    │                    │
      │     ┌────┴──────────────┐   blocked               │
      │     │ awaiting_approval │     │                    │
      │     └────┬─────────┬────┘     │                    │
      │          │         │          │                    │
      │        approve   deny         │                    │
      │          │         │          │                    │
      │       resumed    failed/ ◄────┘                    │
      │          │                                         │
      │          └─────────────────────────────────────────┘
      │                   (resume from next step)
      │
      ▼  (all steps complete)
    done/

   canceled/  — operator explicit cancel (CLI or panel)
  step_failed — transient per-step failure (retried or moved to failed/)
advisory_running — assistant_question jobs in parallel advisory lane
```

---

### Brain Fallback Chain

```
Mission Planner  (mission_planner.py)        Assistant lane  (queue_assistant.py)
        │                                              │
   ┌────┴──────────────────────────────────────────┐   │
   │  [1] primary brain    (cfg.brain.primary)     │   │  [1] primary brain
   │       timeout: 25 s (_PLANNER_TIMEOUT_SECONDS)│   │  [2] fallback brain
   │       on fail: classify via brain/fallback.py │   │
   │                                               │   │  on all fail:
   │  [2] fast brain       (cfg.brain.fast)        │   │  degraded advisory answer
   │       planner only; skipped if not configured │   │  (advisory_mode=degraded_brain_only)
   │       on fail: classify + try next            │   │
   │                                               │   │
   │  [3] fallback brain   (cfg.brain.fallback)    │   │
   │       on fail: raise PlannerError             │   │
   └───────────────────────────────────────────────┘   │

Fallback reason enum  (brain/fallback.py):
  TIMEOUT  AUTH  RATE_LIMIT  MALFORMED  NETWORK  UNKNOWN

Brain adapters:
  brain/gemini.py          → Google Gemini API            (type: gemini)
  brain/openai_compat.py   → OpenAI-compatible endpoint   (type: openai_compat)
                             (OpenRouter, Ollama, LM Studio, any OAI-compat API)
  brain/json_recovery.py   → JSON rescue applied after malformed planner output
```

---

### Panel Route Domain Map

```
panel/app.py  (FastAPI composition root)
│   creates FastAPI app · mounts /static · sets up Jinja2 templates
│   wires shared auth / CSRF / queue helpers · calls register_*_routes()
│
├── register_home_routes(app)           → routes_home.py
│     GET  /                              home dashboard (queue snapshot, health widget)
│     POST /queue/submit                  create new job from goal text
│
├── register_job_routes(app)            → routes_jobs.py
│     GET  /jobs                          job list  (filter: bucket, query, n)
│     GET  /jobs/{job_id}                 job detail + artifacts
│     POST /jobs/{job_id}/approve         approve pending step
│     POST /jobs/{job_id}/deny            deny pending step
│     POST /jobs/{job_id}/cancel          cancel queued/pending job
│     POST /jobs/{job_id}/retry           re-queue a failed job
│
├── register_queue_control_routes(app)  → routes_queue_control.py
│     POST /queue/jobs/{ref}/delete       delete terminal job (mutation guard)
│     POST /queue/pause                   pause daemon     (mutation guard)
│     POST /queue/resume                  resume daemon    (mutation guard)
│
├── register_assistant_routes(app)      → routes_assistant.py
│     GET  /assistant                     operator assistant UI
│     POST /assistant/ask                 submit question
│                                         stall detection + degraded-mode fallback
│
├── register_mission_routes(app)        → routes_missions.py
│     GET  /missions/templates/create    mission template creation form
│     POST /missions/templates/create    save new mission template
│     GET  /missions/create              mission creation form
│     POST /missions/create              save new mission JSON
│
├── register_bundle_routes(app)         → routes_bundle.py
│     GET  /jobs/{job_id}/bundle         per-job incident bundle  (zip download)
│     GET  /bundle/system                system ops bundle         (zip download)
│
├── register_hygiene_routes(app)        → routes_hygiene.py
│     GET  /hygiene                      hygiene dashboard
│     POST /hygiene/prune-dry-run        dry-run prune (terminal buckets)
│     POST /hygiene/reconcile            reconcile queue (orphan detection)
│     POST /hygiene/health-reset         reset health snapshot
│
└── register_recovery_routes(app)       → routes_recovery.py
      GET  /recovery                      recovery + quarantine bucket listing
      GET  /recovery/download/{bucket}/{name}  ZIP download (traversal-protected)

Shared panel helpers  (not route modules):
  panel/helpers.py       request_value (query/form/JSON extraction), coerce_int
  panel/assistant.py     assistant thread helpers  (used by routes_assistant.py)
  panel/static/panel.css panel stylesheet
  panel/templates/       Jinja2 HTML templates
    home.html  jobs.html  job_detail.html  assistant.html
    hygiene.html  recovery.html  _daemon_health_widget.html
```

---

### CLI Command Tree

```
voxera                        (cli.py — Typer composition root)
│
├── run              submit a goal to the queue (inline, non-blocking)
├── status           daemon status + health summary
├── audit            tail JSONL audit log
├── panel            start the web panel (uvicorn)
├── daemon           start the queue daemon
├── setup            interactive first-run TUI wizard
├── demo             guided onboarding checklist (offline + online modes)
├── version          show installed version
│
├── missions         mission CRUD
│   └── ...          list / run / create / show built-in missions
│
├── skills           skill listing + inspection
├── ops              ops bundle export (system snapshot zip)
├── config           config inspect + snapshot
│
├── queue            (cli_queue.py — queue_app)
│   ├── status       queue health + job counters
│   ├── prune        remove stale terminal jobs  (dry-run default)
│   ├── reconcile    orphan/duplicate detection + quarantine-first fix
│   ├── health       raw health snapshot (JSON)
│   ├── health-reset reset health snapshot
│   ├── cancel       cancel a queued or pending job
│   ├── retry        re-queue a failed job
│   ├── delete       delete a terminal job + all sidecars
│   │
│   ├── approvals    (queue_approvals_app)
│   │   ├── list     list pending approvals
│   │   ├── approve  grant approval for a pending step
│   │   └── deny     deny a pending step
│   │
│   └── lock         (queue_lock_app)
│       ├── status   show daemon lock status
│       └── unlock   force-release a stale lock
│
├── inbox            (cli_queue.py — inbox_app)
│   ├── add          submit a goal text as a job file
│   └── list         list inbox items
│
├── artifacts        (cli_queue.py — artifacts_app)
│   └── ...          artifact inspection commands
│
└── doctor           (cli_doctor.py — registered via register(app))
                     diagnostic: endpoint health, model test, lock/auth checks
                     options: --self-test  --quick  --timeout-s
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

- **Capability-based permissions** — every skill declares what it needs (capabilities + effect class). Runtime enforces metadata validity and policy allow/ask/deny **before invocation**; uncertainty fails closed.
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
policy + runtime capability gate
    ├── allow (valid metadata + policy allow) → execute (persist step outcomes/state) → done/
    ├── ask   (valid metadata + policy ask) → write approval artifact + state sidecar update → pending/approvals/
    │           (resume on approve, move to failed/ on deny)
    └── deny / metadata invalid|missing|ambiguous|unknown → fail-closed block → failed/ + error sidecar + structured step/execution artifacts

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

See [Queue Job State Machine](#queue-job-state-machine) and [Queue Subsystem Composition](#queue-subsystem-composition) in the Subsystem Maps section above for visual diagrams.

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
| `make golden-check` | Before/inside validation-check; whenever CLI/help contracts change | Validate committed `tests/golden/` operator-surface baselines against live output |
| `make golden-update` | Only when intentionally accepting reviewed output changes | Regenerate committed `tests/golden/` baselines |
| `make validation-check` | Before every PR / local merge confidence | ruff format/check + mypy + `make golden-check` + critical queue/CLI/doctor contract suites |
| `make full-validation-check` | Before releases or risky changes | validation-check + merge-readiness + failed-sidecar guardrails + full pytest + Golden4 E2E |
| `make test-failed-sidecar` | Queue daemon changes | Sidecar schema policy + lifecycle smoke tests |

---

See also: `docs/BOOTSTRAP.md`, `docs/SECURITY.md`, `docs/ROADMAP.md`, `docs/ops.md`.

---

## Public Contract Checklist (Regression Guard)

- **CLI command names/options unchanged**
  - Root commands and nested groups (`config`, `queue`, `ops`) are snapshot-tested.
  - High-value operator help/JSON outputs are golden-validated from committed fixtures under `tests/golden/` (`make golden-check`).
  - Help surfaces for key commands (for example `doctor`, `queue status`) remain covered by targeted snapshot/contract tests.
- **Panel route paths unchanged**
  - FastAPI route surface is snapshot-tested against the public paths used by operators.
- **Panel jobs mutation redirects are relative by design**
  - Redirects target `/jobs?...` to remain origin-safe in proxy/front-door/root-path deployments.
- **Queue artifacts/state transitions unchanged**
  - Daemon startup recovery keeps deterministic failed/quarantine behavior.
  - Approval deny flow keeps the `pending -> failed` transition and failed sidecar schema/fields.

When evolving CLI/panel/daemon behavior, update tests and this checklist intentionally in the same change.

## Structured result consumption order (additive)

Queue consumers resolve execution context using this preference order:
1. `artifacts/<job>/execution_result.json`
2. `artifacts/<job>/step_results.json`
3. legacy `*.state.json`, `*.error.json`, `*.approval.json`
4. existing derived/audit fallbacks

This is intentionally additive and backward-compatible: canonical structured fields are preferred, while legacy jobs remain fully supported.

## Producer-side queue intent contract (additive)

In addition to execution-time artifacts, queue producer lanes now emit/normalize additive `job_intent` metadata for queued work. This is centralized in `src/voxera/core/queue_job_intent.py` and is intentionally tolerant of partial inputs. The daemon persists `artifacts/<job>/job_intent.json` when present and includes the same object under `execution_envelope.json -> request.job_intent`.

This keeps legacy queue payloads valid while giving newer jobs a deterministic planning-intent surface for panel detail views, ops bundles, and future retry/recovery logic.
