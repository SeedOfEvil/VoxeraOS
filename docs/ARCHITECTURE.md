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
├── cli.py                    — Typer CLI router; all user-facing commands
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
│   ├── missions.py           — Mission templates + runner; YAML/JSON mission loading
│   │                           built-in mission IDs: work_mode, focus_mode,
│   │                           daily_checkin, system_check, sandbox_smoke, sandbox_net
│   ├── mission_planner.py    — LLM-based planning; fallback chains; step validation;
│   │                           error classification; planner timeouts
│   ├── queue_daemon.py       — Job processor: lock mgmt, approval workflow,
│   │                           failed-job sidecars, retention pruning, health tracking
│   ├── queue_inspect.py      — Queue status snapshots; bucket filtering
│   │                           (inbox / pending / done / failed / canceled)
│   ├── queue_hygiene.py      — `voxera queue prune`: removes stale job files from terminal
│   │                           buckets (done/failed/canceled); sidecar-aware; dry-run default
│   ├── queue_reconcile.py    — `voxera queue reconcile`: report-only orphan/duplicate detector;
│   │                           quarantine-first fix mode (`--fix [--yes]`); symlink-safe
│   ├── router.py             — Intent routing: CLI / voice / panel inputs
│   ├── inbox.py              — Atomic job intake; human-friendly entry point
│   ├── capabilities_snapshot.py — Runtime catalog: missions, skills, allowed_apps;
│   │                           used by planner as validation guardrail
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
    ├── app.py                — FastAPI endpoints: queue ops, job lifecycle,
    │                           mission create, bundles; CSRF + Basic auth
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
operator truth beyond bucket location. The sidecar tracks:

- `lifecycle_state`: `queued|planning|running|awaiting_approval|resumed|done|step_failed|blocked|canceled`
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
