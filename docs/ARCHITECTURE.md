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
│   │   ├── setup_wizard.py          — interactive first-run TUI wizard (sequential brain slots + curated grouped OpenRouter model picker + finish launch step)
│   │   ├── doctor.py                — diagnostic runner (doctor_sync)
│   │   ├── demo.py                  — guided onboarding checklist
│   │   ├── openrouter_catalog.py    — curated OpenRouter catalog loader/grouping + live refresh helpers
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
│   │   │   ├── file_intent.py       — bounded file intent classifier (exists/stat/mkdir/delete/copy/move/archive → preview payloads)
│   │   │   ├── inbox.py             — atomic job intake
│   │   │   ├── capabilities_snapshot.py  — runtime skill/mission catalog + validation
│   │   │   └── planner_context.py   — LLM prompt preamble assembly
│   │   ├── skills/
│   │   │   ├── registry.py          — manifest.yml discovery + strict health classification (valid/invalid/incomplete/warning) + entrypoint loading
│   │   │   ├── runner.py            — policy-gated skill execution + approval callbacks
│   │   │   ├── execution.py         — sandbox selection + audit value sanitization
│   │   │   ├── arg_normalizer.py    — arg canonicalization + alias mapping
│   │   │   └── path_boundaries.py   — deterministic confined-path normalization
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
│       assistant_question jobs → fast-lane eligibility gate         │
│                                  ├─ eligible -> fast_read_only lane│
│                                  └─ else -> normal queue lane      │
│                               (both emit canonical artifacts)       │
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
├── setup_wizard.py           — Interactive TUI first-run setup (voxera setup; provider list, sequential brain slots, curated vendor-grouped OpenRouter catalog, and post-setup panel launch choices)
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
│   │                           daily_checkin, incident_mode, wrap_up, system_check,
│   │                           system_inspect
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
│   ├── arg_normalizer.py     — Argument canonicalization; alias mapping
│   └── path_boundaries.py    — Confined path normalization for file skills
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
                                security-check, validation-check, full-validation-check
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
├── vera             start the standalone Vera web app (uvicorn)
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
| `make security-check` | Before every PR touching routing/planning/approvals/progress contracts | Focused adversarial red-team regression suite for fail-closed semantics |
| `make validation-check` | Before every PR / local merge confidence | ruff format/check + mypy + `make golden-check` + `make security-check` + critical queue/CLI/doctor contract suites |
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

### Assistant fast lane (read-only)

The assistant queue path includes a narrow, fail-closed fast-lane gate for explicitly read-only advisory requests.

- Scope: only assistant advisory requests (`assistant_question` request kind from payload kind or canonical `job_intent.request_kind`), not mission execution.
- Deterministic eligibility signals (all required):
  - `advisory=true`
  - `read_only=true`
  - `action_hints` exactly `['assistant.advisory']`
  - no `goal`/`plan_goal`, no `mission_id`/`mission`, no `steps`, no `approval_required=true`
- Fail-closed: any mismatch or uncertainty routes to normal `queue` lane.
- Governance preserved in both lanes: runtime policy/capability boundaries are unchanged, canonical artifact shaping still occurs, and action/audit logs are emitted.
- Operator evidence: lane selection is explicit in artifacts:
  - `execution_result.json.execution_lane` (`fast_read_only` or `queue`)
  - `execution_result.json.fast_lane` (`used`, `eligible`, `eligibility_reason`, `request_kind`)
  - mirrored lane metadata in `assistant_response.json`
  - assistant jobs also emit `execution_envelope.json` (`execution.mode=assistant_advisory`, `execution.lane`, `execution.fast_lane`)

This keeps legacy queue payloads valid while giving newer jobs a deterministic planning-intent surface for panel detail views, ops bundles, and future retry/recovery logic.


## Bounded evaluate-and-replan loop (execution adaptation guardrail)

The queue execution lane now uses an explicit evaluate-and-replan loop:

- After each mission attempt, execution is classified into one deterministic evaluator class.
- Planner-side unknown-skill failures are normalized into structured planning-attempt artifacts (not daemon crashes), enabling one bounded governed replan for goal jobs.

- Replanning is only allowed for bounded classes (`retryable_failure`, `replannable_mismatch`)
  and only within `max_replan_attempts` (default `1`).
- Approval-pending, policy/capability block, and hard boundary outcomes stop without replan.
- Every attempt still passes through normal policy/approval/path/argv hardening; replanning does
  not bypass trust gates.
- Canonical artifacts carry adaptation metadata (`attempt_index`, `replan_count`,
  `evaluation_class`, `evaluation_reason`, `stop_reason`) plus per-attempt `plan.attempt-<n>.json`.


## Normalized skill_result contract

Built-in skills now emit a consistent canonical `skill_result` payload (`summary`, `machine_payload`, `output_artifacts`, `operator_note`, `next_action_hint`, `retryable`, `blocked`, `approval_status`, `error`, `error_class`). Queue artifact shaping (`step_results.json`, `execution_result.json`) consumes these fields as structured-first inputs, with legacy sidecar fallback retained for backward compatibility.

`execution_result.json` also includes additive normalized review/evidence contract blocks for downstream consumers: `artifact_families`, `artifact_refs`, `review_summary`, and `evidence_bundle` (with `trace` linkage).


## Simple-intent routing (v1.3 / GitHub PRs #144–#145)

`src/voxera/core/simple_intent.py` adds a small deterministic layer between a natural-language
goal string and the planner.  It classifies obvious operator asks into one of:

| Intent kind                    | Trigger pattern                                                    | Allowed first-step skills                              |
|--------------------------------|--------------------------------------------------------------------|--------------------------------------------------------|
| `assistant_question`           | question verb / status phrase                                      | `assistant.advisory`, `system.status`                  |
| `open_terminal`               | direct terminal-open imperative; can be compound-leading            | `system.open_app`                                      |
| `open_url`                    | explicit `open/launch` verb bound to URL                            | `system.open_url`                                      |
| `open_app`                    | explicit narrow app-open imperative                                  | `system.open_app`                                      |
| `write_file`                   | write/append/create-file verb (articles accepted)                  | `files.write_text`                                     |
| `read_file`                    | read/cat/display/view + `~/` or `/` path (articles accepted)       | `files.read_text`                                      |
| `run_command`                  | run command / execute / exec verb                                  | `sandbox.exec`                                         |
| `unknown_or_ambiguous`         | everything else                                                    | (no constraint — normal planning applies)              |

> Deterministic open routing is intentionally narrow: meta/help/explanatory text is guarded out; URL presence alone does not trigger `open_url`; and compound actionable requests preserve first-step metadata (`first_step_only`, `first_action_intent_kind`, `trailing_remainder`) so only step 1 is constrained fail-closed.

**`read_file` accepted trigger patterns** (all require a `~/` or `/` path):

| Form                                    | Example                                          |
|-----------------------------------------|--------------------------------------------------|
| `read <path>`                           | `read ~/VoxeraOS/notes/foo.txt`                  |
| `read the <path>`                       | `read the ~/VoxeraOS/notes/foo.txt`              |
| `read the file <path>`                  | `read the file ~/VoxeraOS/notes/pr144.txt`       |
| `read file <path>`                      | `read file ~/VoxeraOS/notes/foo.txt`             |
| `open and read <path>`                  | `open and read ~/VoxeraOS/notes/foo.txt`         |
| `cat <path>` / `display <path>`         | `cat ~/VoxeraOS/notes/foo.txt`                   |
| `view <path>` / `show contents of <path>` | `show contents of ~/VoxeraOS/notes/foo.txt`    |

Goals without an explicit `~/` or `/` path (e.g. `"read this and copy it"`,
`"read the document"`) fall through to `unknown_or_ambiguous` with no constraint.

### Target extraction and direct routing

`SimpleIntentResult` carries an optional `extracted_target` field:

- **`read_file`**: the exact path extracted from the goal (e.g. `~/VoxeraOS/notes/pr144.txt`).
- **`write_file`** with `"called <name>"` suffix: candidate path `~/VoxeraOS/notes/<name>`.
- All other intents: `None`.

When `extracted_target` is present and the path is within the allowed notes root
(`~/VoxeraOS/notes/`), `mission_planner.plan_mission()` **skips the cloud brain** and returns
a deterministic single-step plan:

| Intent     | Deterministic planner route         | Skill              |
|------------|-------------------------------------|--------------------|
| `read_file`| `_extract_simple_read_args()`       | `files.read_text`  |
| `write_file` (named)| `_extract_named_file_write_args()` | `files.write_text` |


### Filesystem productivity skill pack (bounded waves 1–2)

Additive file-scope skills extend the existing read/write pair without widening trust boundaries:
- `files.list_dir`, `files.exists`, and `files.stat` (inspection): local, `needs_network=false`, `fs_scope=read_only`, notes-root confined.
- `files.copy_file`, `files.move_file`, `files.mkdir`, and `files.delete_file` (mutation): local, `needs_network=false`, `fs_scope=workspace_only`, notes-root confined.

All rely on centralized confined-path normalization and fail closed on traversal/symlink escape/out-of-root paths.

If extraction fails or the path is outside the allowed root, the planner falls through to the
cloud brain normally; the mismatch check then acts as the safety net.

### How it works

1. For goal-kind queue jobs, `classify_simple_operator_intent(goal=...)` runs before planning.
2. The result (`SimpleIntentResult`) carries `intent_kind`, `deterministic`, `allowed_skill_ids`,
   `routing_reason`, `fail_closed`, and optionally `extracted_target`.
3. A `queue_simple_intent_routed` action event is emitted immediately.
4. The planner attempts deterministic direct routing for high-confidence intents with extracted
   targets (read_file, named write_file).  Other goals go to the cloud brain.
5. After the planner returns a mission, the first step's `skill_id` is checked against
   `allowed_skill_ids` via `check_skill_family_mismatch(...)`.
6. If a mismatch is detected:
   - Execution is **stopped before any skill runs** (fail closed).
   - A `queue_simple_intent_mismatch` action event is emitted.
   - `plan.json` / `plan.attempt-<n>.json` record the mismatch evidence.
   - `execution_result.json` reflects `evaluation_reason=simple_intent_skill_family_mismatch`,
     `stop_reason=planner_intent_route_rejected`, and an `intent_route` dict.
   - The job moves to `failed/` immediately.
7. `unknown_or_ambiguous` goals have `fail_closed=False` — they are never constrained.

### Design principles

- **Conservative**: only classifies when the goal is obviously recognisable (explicit verb +
  path for reads, single-word app target for open, question word for advisory, etc.).  Ambiguous
  multi-step or vague goals fall through to `unknown_or_ambiguous`.
- **No NLP**: pure regex pattern matching — no external dependencies, deterministic, inspectable.
- **Additive artifacts only**: existing contract schemas are extended, not replaced.
- **Does not broaden autonomy**: no approvals bypassed, no capabilities widened.  The check only
  constrains which skill family may appear as the first step.
- **Panel and CLI parity**: both surfaces enqueue goal-kind jobs through the same
  `_normalize_payload` → `_classify_goal_intent` path; no surface-specific carve-outs.

### Artifact additions (additive)

- `execution_envelope.json → request.simple_intent`:
  `{intent_kind, deterministic, allowed_skill_ids, routing_reason, fail_closed[, extracted_target]}`
- `execution_result.json → intent_route`:
  **same shape as `simple_intent`; present for ALL goal-kind jobs** (not just mismatches).
  On mismatch the field also contains evidence of what the planner produced.
- `plan.json` / `plan.attempt-<n>.json → intent_route`:
  populated for goal-kind jobs when intent was classified
- `actions.jsonl`: `queue_simple_intent_routed` (always for goal-kind) and
  `queue_simple_intent_mismatch` (on mismatch) events

## Real-time panel progress surfaces (GitHub PR #146)

Panel real-time UX is implemented as a narrow polling layer over canonical queue state:

- `GET /jobs/{job_id}/progress` returns shaped lifecycle/step/approval metadata for mission and assistant-shaped jobs.
- `GET /assistant/progress/{request_id}` returns advisory request lifecycle metadata.
- Existing HTML pages remain authoritative baseline rendering; polling is additive progressive enhancement.

Truth sources remain unchanged:

- queue bucket placement (`inbox/pending/done/failed/canceled`)
- sidecars (`*.state.json`, `*.approval.json`, `*.error.json`)
- execution artifacts (`execution_result.json`, `step_results.json`, `execution_envelope.json`, assistant response artifact)

This keeps Voxera OS as the trust layer: UI reflects persisted control-plane evidence rather than inferred progress.


### Queue lineage metadata (GitHub PR #148, descriptive only)

The queue contract carries optional additive lineage fields for workflow observability: `parent_job_id`, `root_job_id`, `orchestration_depth`, `sequence_index`, and `lineage_role`. These fields are metadata-only: there is no dependency enforcement, automatic child scheduling, traversal, orchestration state machine, or output passing between jobs. Presence, absence, or specific values of lineage fields have no effect on approvals, policy, scheduling, or execution behavior.


### Controlled child enqueue primitive (GitHub PR #149)

Queue payloads may include a narrow `enqueue_child` object (`goal`, optional `title`). During parent execution success, Voxera may enqueue one normal child inbox job and emits audit evidence.

Safety semantics:
- fail-closed validation (`enqueue_child` must be an object, only allowed keys, non-empty `goal`)
- no recursion or nested enqueue support
- no parent waiting, dependency resolution, or output passing
- child lineage is computed server-side from sanitized parent lineage and cannot be user-overridden through child payload metadata
- approval behavior is unchanged: child jobs enter normal approval flow when they execute


## Vera v0 conversational boundary + explicit queue handoff (PR #152/#153)

Vera v0 adds a minimal standalone chat surface (`voxera.vera_web.app`) intended to run on a separate port from the operator panel, with short session-scoped context stored under `notes/queue/artifacts/vera_sessions/`.

Operational defaults:
- local command: `make vera`
- user service: `voxera-vera.service`
- host/port: `127.0.0.1:8790`

- **Vera role:** reasoning + conversation layer (brain-backed text responses, planning help, structured request drafting).
- **VoxeraOS role:** strict execution trust layer (queue intake, policy/approval, runtime execution, evidence artifacts).
- **Boundary:** chat itself is never the execution engine; normal Vera chatting does not enqueue or execute jobs.
- **Handoff path:** action-shaped requests produce a structured preview first; explicit user handoff then submits that payload into the real VoxeraOS queue intake path.
- **Truth semantics:** Vera language must distinguish proposal/prepared/submitted from executed/verified evidence states.
- **Context model:** bounded rolling turn window (`MAX_SESSION_TURNS`) retained per session, intentionally restart-volatile for v0.
- **Developer tooling:** standalone Vera UI includes developer diagnostics (prompt + session metadata), explicit preview visibility, submit control, and explicit context reset (`POST /clear`).
- **Authoritative preview semantics (PR #159):** visible preview pane JSON is the active session draft; pane submit always submits that exact draft through the same trusted queue handoff path; successful handoff clears active preview.
- **Natural approval phrasing:** when an active preview exists, phrases like `use this preview` and `that looks good now use it` map to real handoff of the active draft; when no preview exists they fail closed.

Queue concept (developer framing): the queue is the structured path for real side effects; jobs are submitted into VoxeraOS and moved through lifecycle states with approvals/policy checks and evidence produced in VoxeraOS artifacts. Submission is not execution, and execution is not verification.

## Queue object model (canonical contract)

For an explicit, stable definition of queue jobs, lifecycle states, artifact/evidence semantics,
truth hierarchy, and verifier grounding rules, see `docs/QUEUE_OBJECT_MODEL.md`.

## Vera preview drafting boundary notes (PR #154)

- Vera uses a lightweight deterministic phrase-normalization layer (`src/voxera/vera/handoff.py`) to map common conversational action requests into the smallest supported queue preview payload.
- Supported intent families in this layer are intentionally narrow: web navigation URL opens, explicit file reads, and basic note/file write intents.
- Save-by-reference write intents now include bounded current-session assistant-content resolution for phrases like `that summary`, `your previous answer`, and `the previous response`, routed into the same governed `write_file` preview contract.
- Precedence rule: when a current investigation-derived comparison/summary exists in session, follow-up `save that ...` routes to the derived investigation save path first; generic recent-assistant-content save resolution is fallback-only.
- Recency refinement: derived save-that precedence applies only while the derived comparison/summary remains the latest relevant assistant output; if a newer conversational assistant answer appears, singular `save that ...` resolves to that newer answer.
- Singular vague references (for example `save that`) deterministically resolve to the most recent substantial assistant-authored message in the active session.
- Resolver scope is intentionally limited to recent assistant-authored content in the active session transcript only (no cross-session recall, no broad history search).
- Plural/explicitly ambiguous or unavailable assistant-content references fail conservatively with a clear user-facing refusal rather than guessing.
- Recent-content resolution ignores trivial courtesy assistant turns, so `thanks` / `you're welcome` exchanges do not displace the latest substantial explanation or summary.
- Conversational explanatory/teaching prompts remain in the normal Vera answer lane by default; they are not automatically treated as web investigation requests.
- Bounded prose-drafting prompts (essay/article/writeup/explanation/rewrite/formalize/expand) compile into governed `write_file` previews backed by assistant-authored prose.
- Read-only Brave investigation routing is reserved for explicit search/investigation/current-information intent (for example `search the web`, `look up`, `find the latest`, `latest official docs`).
- Ordinary compare/explain prompts stay conversational unless explicit search/latest/current/web intent is present.
- Because save-by-reference uses session transcript content, this path depends on a real assistant-authored answer existing in the active session first.
- Preview state is persisted per session (`pending_job_preview`) and is independent from rolling chat turn limits.
- The session keeps exactly one active preview draft; follow-up revisions replace that draft, while lightweight acknowledgements leave it unchanged.
- Hidden compiler/deterministic fallback prioritize semantic active-preview refinement interpretation (content/path/mode, pronouns) while preserving strict preview-only JSON mutation contracts and fail-closed behavior.
- Explicit handoff submits only the latest active draft, and successful submit clears the draft after queue confirmation.
- Submission remains a separate explicit step that writes to queue inbox; no direct execution path exists in Vera.


## Structured governed file-write content path (PR #157)

- Queue goal payloads may now include a narrow `write_file` object with `path`, `content`, and optional `mode` (`overwrite|append`).
- `MissionQueueDaemon` normalizes this shape fail-closed and builds a single-step `files.write_text` mission directly on queue rails (no out-of-band writes).
- Explicit operator path/content are preserved into `plan.json`, `execution_envelope.json.request.write_file`, `step_results.json`, and `execution_result.json`.
- Default filename fallback behavior remains limited to legacy goal-only natural-language routing; structured `write_file.path` is never substituted.


## Structured bounded file-organize mission path (PR #next)

- Queue payloads may include a narrow `file_organize` object: `source_path`, `destination_dir`, `mode` (`copy|move`), optional `overwrite`, optional `delete_original`.
- `MissionQueueDaemon` validates this shape fail-closed and builds a governed multi-step mission on normal queue rails:
  `files.exists` → `files.stat` → `files.mkdir` → `files.copy_file|files.move_file` → optional `files.delete_file`.
- This path is additive and does not change queue lifecycle architecture: standard planning/execution artifacts (`execution_envelope.json`, `step_results.json`, `execution_result.json`) remain authoritative.
- Trust boundaries remain unchanged: confined notes-root paths only; control-plane `~/VoxeraOS/notes/queue/**` is blocked by shared path-boundary enforcement.
- Destructive cleanup (`delete_original`) only executes when explicitly requested and remains policy/approval-governed through the existing `file.delete` capability.

## Vera evidence-aware outcome review (PR #155)

Vera v0 now includes a narrow job-outcome review capability in chat while preserving trust boundaries.

- Review path resolves either an explicit job id or the session's latest submitted `handoff_job_id`.
- Outcome summaries are built from canonical queue evidence via shared helpers (`lookup_job` + `resolve_structured_execution`), not ad-hoc parsing.
- Reported fields include state classification (`submitted|queued|planning|running|awaiting_approval|resumed|pending|succeeded|failed|canceled`), lifecycle state, terminal outcome, approval status, latest summary, failure summary, and child summary (if already exposed).
- Structured execution consumption also exposes `normalized_outcome_class` for deterministic non-success taxonomy without overriding canonical queue truth (`approval_blocked`, `policy_denied`, `capability_boundary_mismatch`, `path_blocked_scope`, `runtime_dependency_missing`, `runtime_execution_failed`, `canceled`, `partial_artifact_gap`, `incomplete_evidence`).
- Review shaping is deterministic and lifecycle-aware: when available it prefers normalized `execution_result.review_summary.latest_summary` and `execution_result.evidence_bundle.trace` over ad-hoc prose fallback.
- Review output also surfaces normalized execution capability declaration context (`side_effect_class`, network/fs scope, sandbox profile) and expected-vs-observed artifact status (`observed|partial|missing`) when declared.
- State-aware next-step guidance remains fail-closed and must not claim terminal success without queue + artifact/evidence support.
- Next-step guidance is evidence-grounded and state-aware: awaiting approval explains that runtime output gaps are expected until approval; canceled explains output gaps may be cancellation-driven; failed guidance prioritizes `stderr`/`step_results`; succeeded+partial guidance asks operators to validate whether artifact gaps are benign evidence capture gaps or require rerun.
- Optional follow-up drafting writes a new preview only; submission remains explicit handoff and execution remains VoxeraOS-owned.


- Canonical queue/assistant lanes now use forward-looking expected-artifact defaults so new jobs carry explicit artifact intent from job creation through runtime/review (without historical backfill).

- Vera linked-job completion foundation: for Vera-originated handoffs, session state tracks linked job refs and deterministically ingests completion when canonical queue lifecycle reaches terminal states.
- Current auto-surfacing slice: on chat cycles, Vera surfaces at most one unsurfaced linked completion when surfacing policy is `read_only_success`, `mutating_success`, `approval_blocked`, or `failed`, then marks `surfaced_in_chat=true` with `surfaced_at_ms`.
- For `mutating_success`, surfacing is additionally guarded by terminality metadata (`child_refs`/`child_summary`/stop reason): intermediate orchestrator-parent completions are suppressed until truly terminal from the user perspective.
- Other classes (`canceled`, `manual_only`, `noisy_large_result`) intentionally remain unsurfaced for later additive PRs.

- Evidence-grounded value-forward surfacing layer (`vera/result_surfacing.py`): linked completion messages and review outputs now prefer concise, evidence-grounded result text over generic status-only messaging for read/inspection operations. The layer inspects `step_summaries`/`machine_payload` evidence and deterministically formats bounded results for supported skill families (file read/exists/stat, directory listing, service status, recent logs, diagnostics snapshot, process list). When no useful structured value is available, falls back to current status-oriented completion text. Large outputs (file content, log excerpts) are bounded via truncation/excerpt limits.

## Governed code/script draft lane (PR #TBD)

Vera now has a deterministic code/script/config draft lane that creates real `write_file` preview state backed by LLM-generated code, enabling the `save it` → governed submit flow.

**Classifier (`src/voxera/core/code_draft_intent.py`):**
- `is_code_draft_request(message)`: requires a creation verb + (language keyword + subject noun) OR explicit filename with code extension. Excludes save-by-reference phrases.
- `classify_code_draft_intent(message)`: returns a `write_file` preview payload with an empty content placeholder. Actual code is injected by the caller post-LLM-reply.
- `extract_code_from_reply(text)`: extracts the first fenced code block from the LLM reply for content injection.
- Supports 30+ languages/file types. Single-letter ambiguous tokens (`c`, `r`) excluded from keyword matching; caught via explicit filenames (`.c`, `.r`).

**App-layer integration (`vera_web/app.py`):**
- After `generate_vera_reply()`, if `is_code_draft_request(message)` is True, the reply is scanned for a fenced code block; if found, it is injected into the preview and the session state is updated.
- Code draft replies are excluded from the conversational-control reply suppressor so code-containing answers are always shown in chat.
- The hidden preview builder can also produce a code draft payload; the post-reply step merges the LLM-extracted code into whichever payload is active.
- **Refinement detection:** when an active preview has a code-type file extension and the LLM reply contains a fenced code block, the turn is treated as a code draft update even if `is_code_draft_request()` is False. This lets users refine drafts naturally ("actually use requests library") without triggering a fresh code draft classifier match. The reply is shown, the preview content is updated, and submit remains governed.

**Submit patterns (`vera/handoff.py`):**
- Added `save it`, `save this`, `let's save it/this/that`, and `write it/this/that to file` to `_ACTIVE_PREVIEW_SUBMIT_PATTERNS`. These only fire when a preview exists (fail-closed).

**Code extraction (`core/code_draft_intent.py`):**
- `extract_code_from_reply(text)` uses `r"```[^\n]*\n(.*?)```"` (DOTALL) to match fenced blocks. The `[^\n]*` on the fence line tolerates trailing spaces, version strings, or other characters LLMs sometimes emit after the language tag (e.g. ` ```python ` with a trailing space). Code is stripped before returning.

**Truthfulness guardrails (`vera_web/app.py`):**
- `_text_outside_code_blocks(text)`: strips fenced code blocks from text before phrase-matching, preventing false positives when code content mentions "preview". Uses the same `[^\n]*` fence-line pattern.
- `_looks_like_preview_pane_claim(text)`: detects phrases like "preview pane", "check the preview", "in your preview", "visible in preview", etc. in non-code text. Delegates to `_looks_like_preview_update_claim` for update-style claims.
- `_guardrail_false_preview_claim(text, preview_exists)`: applied after `_guardrail_submission_claim`; when `preview_exists=False`, strips false preview-existence claims from the LLM reply — preserving any embedded code blocks with a truthful note, or replacing the whole reply with a plain "could not prepare preview" message.
- A `write_file` preview with empty `content` is treated as "no real preview" for claim-checking purposes.
- **All-or-nothing enforcement:** when `_guardrail_false_preview_claim` strips a false claim, any empty-content `write_file` placeholder shell is immediately cleared. Failed code-draft attempts leave no orphaned empty preview. Placeholder previews created without a false claim (e.g. "write a file called script.ps1" where the LLM asks what content to add) are intentionally preserved for refinement flows.

**LLM persona override for code-draft turns (`vera/service.py`, `vera_web/app.py`):**
- Vera's default system prompt declares "Not the payload drafter" and "Do not narrate hidden drafting mechanics." Without intervention the LLM never outputs code in fenced blocks; `extract_code_from_reply` always returns `None` and previews stay empty.
- `_CODE_DRAFT_HINT` constant (in `service.py`): a bracketed system note appended to the user message on code-draft turns, explicitly instructing the LLM to write the complete, working code in a properly-fenced block for extraction and governed storage.
- `build_vera_messages` accepts a `code_draft: bool = False` parameter; when `True`, the hint is appended to the user content in the messages list.
- `app.py` pre-computes `is_code_draft_turn` before the LLM call and builds `_vera_user_message = message + _CODE_DRAFT_HINT if is_code_draft_turn else message`. The hint travels inside the user message so `generate_vera_reply`'s signature is unchanged (avoids breaking test infrastructure). Session history stores the original un-augmented message.

## Governed writing/document draft lane (PR #TBD)

Vera now has a bounded prose-writing lane that mirrors the governed code-draft shape for single-document text artifacts.

**Classifier (`src/voxera/core/writing_draft_intent.py`):**
- `is_writing_draft_request(message)`: detects bounded essay/article/writeup/explanation requests plus transform-style phrasing such as rewrite/formalize/expand/turn-into/plain-English explanation.
- `classify_writing_draft_intent(message)`: returns a `write_file` preview payload with a path and empty content placeholder for the prose artifact.
- `is_text_draft_preview(preview)`: distinguishes prose previews from code previews so prose follow-ups do not trigger code-lane handling.
- `extract_text_draft_from_reply(text)`: accepts substantial assistant-authored prose replies for authoritative preview population.

**App-layer integration (`vera_web/app.py`):**
- Writing turns are pre-classified before the LLM call so prose-draft requests do not accidentally inherit the code-lane persona override just because a `.md` filename appears.
- After `generate_vera_reply()`, the bounded prose extractor selects the actual drafted essay/article/writeup/explanation body and injects that body into `write_file.content`, rather than storing conversational wrapper text.
- Internal `<voxera_control>` transport blocks are stripped before user-visible rendering and before prose preview-body extraction, so hidden control payloads stay internal even when a model leaks them into the raw reply.
- Writing replies are excluded from conversational-control suppression, so the user sees the generated prose in chat while the same content becomes the preview body.
- When an active governed writing preview exists, follow-up refinements like `make it more formal` or `rewrite that as ...` refresh the preview content with the new prose reply rather than leaving stale draft content behind.
- Save-as / rename refinements preserve the exact requested filename in `write_file.path`; the renamed path survives through submit rather than snapping back to the default generated filename.

**Recent assistant-content resolver (`vera/handoff.py`, `vera/service.py`):**
- Save-by-reference resolution now recognizes `explanation`/`previous explanation` phrasing alongside summary/answer/response terms.
- Recent-content selection filters out trivial courtesy assistant turns (including extended variants like `You're very welcome ... if you'd like ...`), keeping the latest substantial explanation/saveable prose artifact resolvable across lightweight conversational interruptions.
- Explanation text produced after a code draft is treated as a saveable conversational text artifact and can be renamed/saved through the same governed preview path.

**Investigation/web-routing boundary:**
- Writing follow-ups on top of investigation-derived summaries remain in the writing lane and produce text previews.
- `_is_informational_web_query()` is intentionally narrower: ordinary compare/explain prompts stay conversational; explicit search/latest/current/docs/web-investigation intent still routes to Brave.

**Current limitations:**
- The lane is intentionally bounded to single text documents.
- Prose-body extraction is heuristic and intentionally bounded: wrapper/preface text is stripped only when it matches known draft-introduction patterns or is separated from the body by blank-line block structure.
- No docx/pdf generation, multi-file writing projects, or publishing workflows are added here.
