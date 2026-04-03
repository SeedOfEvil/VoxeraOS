# Architecture

VoxeraOS is an open-source alpha (v0.1.9) intent-driven AI control plane that sits *above* a real Linux substrate. It routes user goals through a planning → policy → execution → audit pipeline.

**Vera** is the conversational intelligence layer — she understands intent, drafts work, and guides operators. **VoxeraOS** is the trust layer — every real-world side effect is capability-gated, policy-evaluated, and evidence-tracked.

For project overview, see [README.md](../README.md). For the product vision, see [NORTH_STAR.md](NORTH_STAR.md).
For the current bounded hotspot audit and extraction order for large surface modules, see [HOTSPOT_AUDIT_EXTRACTION_ROADMAP.md](HOTSPOT_AUDIT_EXTRACTION_ROADMAP.md).

---

## Three-Layer Model

```
┌─────────────────────────────────────────────────────────┐
│  Experience Layer                                       │
│  Voice foundation seam (bounded) · Web Panel · CLI (voxera) │
├─────────────────────────────────────────────────────────┤
│  AI Control Plane                                       │
│  Intent router · Mission planner · Queue daemon         │
│  Skill registry · Policy engine · Approval workflow     │
│  Capability semantics model · Audit/health snapshots    │
├─────────────────────────────────────────────────────────┤
│  Substrate OS                                           │
│  Linux (Ubuntu) · Audio stack · Filesystem              │
│  Networking · Systemd user services · Podman            │
└─────────────────────────────────────────────────────────┘
```



## Canonical capability semantics model

VoxeraOS now treats capability meaning as a centralized runtime contract (not scattered inference):

- Source of truth: `src/voxera/core/capability_semantics.py`
- Per-capability metadata: `effect_class`, `intent_class`, `policy_field`, `resource_boundaries`, and concise operator summary
- Manifest projection: `manifest_capability_semantics(...)` produces normalized intent/boundary/policy expectations consumed by registry validation, policy decisions, mission dry-run semantics, and capability snapshots

This keeps policy/approval behavior, blocked semantics, and operator-facing interpretation aligned as capability families expand.

---

## Project Folder Structure

```
VoxeraOS/
├── src/
│   ├── voxera/                      — main application package
│   │   ├── cli.py                   — Typer composition root
│   │   ├── cli_common.py            — shared CLI primitives/options/constants
│   │   ├── cli_queue.py             — queue/operator command family (registration + wiring)
│   │   ├── cli_queue_approvals.py   — queue approvals command-family handlers (list/approve/deny)
│   │   ├── cli_queue_bundle.py      — queue bundle/incident-bundle command handler
│   │   ├── cli_queue_files.py       — queue files command-family handlers
│   │   ├── cli_queue_health.py      — queue health/health-reset command-family handlers
│   │   ├── cli_queue_hygiene.py     — queue prune/reconcile/artifacts-prune hygiene handlers
│   │   ├── cli_queue_inbox.py       — inbox command-family handlers (add/list)
│   │   ├── cli_queue_lifecycle.py   — queue lifecycle command-family handlers (cancel/retry/unlock/pause/resume)
│   │   ├── cli_queue_payloads.py    — low-risk CLI queue payload/arg shaping helpers
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
│   │   ├── vera/
│   │   │   ├── service.py           — top-level Vera orchestration + compatibility delegation
│   │   │   ├── session_store.py     — session turns/preview/handoff/shared-context persistence
│   │   │   ├── preview_drafting.py  — deterministic preview drafting + save-by-reference previews
│   │   │   ├── draft_revision.py    — active preview rename/path/content refinement + active-draft content refresh parsing
│   │   │   ├── preview_submission.py — active-preview submit detection + queue handoff normalization
│   │   │   ├── investigation_flow.py — explicit read-only web investigation orchestration
│   │   │   ├── investigation_derivations.py — compare/summarize/expand follow-up shaping
│   │   │   ├── weather_flow.py      — live-weather quick flow + follow-up continuity
│   │   │   ├── saveable_artifacts.py — recent meaningful assistant-content selection
│   │   │   ├── reference_resolver.py — bounded session-scoped reference resolution
│   │   │   ├── result_surfacing.py  — evidence-grounded value-forward result extraction
│   │   │   ├── evidence_review.py   — queue evidence review / review-message shaping
│   │   │   ├── handoff.py           — thin compatibility façade for extracted handoff seams
│   │   │   └── weather.py           — weather provider client + snapshot types
│   │   ├── vera_web/
│   │   │   ├── app.py               — FastAPI Vera web service (port 8790); session
│   │   │   │                          management, conversational turns, preview/submit
│   │   │   │                          flows, investigation and weather routing
│   │   │   ├── templates/index.html — Vera single-page HTML shell
│   │   │   └── static/vera.css      — Vera web stylesheet
│   │   ├── skills/
│   │   │   ├── registry.py          — manifest.yml discovery + strict health classification (valid/invalid/incomplete/warning) + entrypoint loading
│   │   │   ├── runner.py            — policy-gated skill execution + approval callbacks
│   │   │   ├── execution.py         — sandbox selection + audit value sanitization
│   │   │   ├── arg_normalizer.py    — arg canonicalization + alias mapping
│   │   │   └── path_boundaries.py   — deterministic confined-path normalization
│   │   ├── voice/                   — bounded voice foundation flags + input/output seams
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
│   │       │   ├── vera.html
│   │       │   └── _daemon_health_widget.html
│   │       └── static/panel.css
│   └── voxera_builtin_skills/       — 31 built-in Python skill callables
│       ├── clipboard_copy.py        clipboard_paste.py
│       ├── disk_usage.py            host_info.py
│       ├── files_copy_file.py       files_delete_file.py
│       ├── files_exists.py          files_list_dir.py
│       ├── files_mkdir.py           files_move_file.py
│       ├── files_read_text.py       files_stat.py
│       ├── files_write_text.py      load_snapshot.py
│       ├── memory_usage.py          open_app.py
│       ├── open_url.py              process_list.py
│       ├── recent_service_logs.py   sandbox_exec.py
│       ├── service_status.py        set_volume.py
│       ├── system_status.py         terminal_run_once.py
│       └── window_list.py
├── skills/                          — skill manifest definitions (manifest.yml per skill)
│   ├── clipboard/{copy,paste}/
│   ├── files/{copy_file,delete_file,exists,list_dir,mkdir,move_file,read_text,stat,write_text}/
│   ├── sandbox/exec/
│   └── system/{disk_usage,host_info,load_snapshot,memory_usage,open_app,open_url,
│             process_list,recent_service_logs,service_status,set_volume,
│             status,terminal_run_once,window_list}/
├── missions/                        — example/repo mission JSON files
│   ├── sandbox_smoke.json
│   └── sandbox_net.json
├── tests/                           — pytest suite (~89 files)
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

## Refactor Ownership Boundaries

The current codebase is intentionally more decomposed than earlier `v0.1.8`/`v0.1.9` snapshots. These are the ownership boundaries to preserve when making changes.

### Vera control layer

- `vera/service.py` remains the **conversation orchestration root**: it builds model messages, coordinates session state, routes into the extracted weather/investigation lanes, and manages linked-job completion delivery.
- `vera/handoff.py` is now a **compatibility façade**, not the preferred place to grow new logic.
- Conversational checklist/planning mode is a **chat artifact lane**: `vera_web/conversational_checklist.py` owns deterministic checklist sanitization/rendering helpers, while `vera_web/app.py` keeps classification and route-level wiring. In that lane, preview/draft/save/submit/queue wording must not surface unless a real governed preview flow is active.
- Add or extend behavior in the dedicated modules first:
  - `preview_drafting.py` for deterministic preview generation
  - `draft_revision.py` for active-preview follow-up parsing
  - `preview_submission.py` for submit/current-preview handoff semantics
  - `investigation_flow.py` for read-only web investigation turns
  - `investigation_derivations.py` for summarize/compare/expand/save-derived flows
  - `weather_flow.py` for quick live-weather turns
  - `saveable_artifacts.py` for recent meaningful-content save targeting
  - `session_store.py` for persisted Vera session state
- Save/write preview integrity invariants:
  - submit serializes the canonical active session preview (`pending_job_preview`), never a stale caller snapshot
  - submit fails closed when supplied preview state and canonical session preview diverge
  - linked completion auto-surfacing prioritizes the most recently submitted linked job so older unsurfaced history does not masquerade as the current submit outcome
  - when a new handoff is active, linked-completion auto-surfacing is scoped to that latest submitted job ref; older completions are withheld until the latest linked job completion is known
  - linked-completion status text (for example, "Your linked ... job completed successfully") is excluded from saveable assistant artifact candidates by default, so it cannot silently become future `write_file.content`
  - combined generate+save turns bind `write_file.content` to the assistant-authored answer produced in the same turn (not canned fallback text or control acknowledgments)
  - clear single-turn generate+save requests (for example, "write a short poem ... and save it as ...") do not require a prior assistant artifact; Vera can stage the preview shell and bind same-turn authored output post-reply
  - draft-management wrapper narration (for example, "I added a new joke ...", "You can see the current draft ...") is excluded from authoritative `write_file.content`; when wrapper text quotes authored body content, only the quoted authored body is stored
  - explanatory tail text appended after authored body (for example, "I've drafted a plan ...", readiness/status lines) is stripped from canonical preview content
  - with an active text preview, clear content-generation turns (for example "tell me a joke") may refresh `write_file.content` from the current assistant-authored answer while keeping the existing destination path unchanged
  - **active-draft content refresh**: when a valid write preview exists and the user makes a clear content-refresh request (for example "generate a different poem", "tell me a different joke", "give me a shorter summary", "give me a different fact"), Vera deterministically generates fresh replacement content, updates `write_file.content` with pure authored body only, and preserves the existing destination path; the deterministic refresh takes priority over speculative LLM-text binding
  - ambiguous active-draft change requests (for example "change it", "make it better", "fix it") fail closed with explicit "draft unchanged" messaging; no fake "(updated)" content is injected
  - accepted rename/name-note mutations must immediately change canonical `write_file.path` and produce explicit destination confirmation; ambiguous naming requests fail closed; when the hidden compiler overrides a deterministic rename with an unchanged preview, the app falls back to the deterministic rename path so the mutation is never silently lost
  - summary-type generate+save flows strip "You can review..." and "Please review..." helper prefixes from preview content via extended preface sentence and wrapper block detection
  - typo-like near-submit phrases (for example "send iit") fail closed with an explicit "did not submit" message before reaching the LLM, preventing conversational overclaiming

### Queue orchestration

`MissionQueueDaemon` is still the queue composition root, but queue behavior is intentionally split across focused lifecycle modules:

- `queue_execution.py` — payload normalization, mission construction, planning handoff, execution, state transitions
- `queue_approvals.py` — approval prompts, grants, pending artifacts, approve/deny resolution
- `queue_recovery.py` — deterministic startup recovery, shutdown handling, quarantine/report surfaces
- `queue_contracts.py` — canonical queue/execution envelope shaping
- `queue_result_consumers.py` — structured execution/result normalization for operator and Vera review surfaces
- `queue_state.py` / `queue_paths.py` — state sidecars and deterministic movement/path helpers

### Panel composition

The panel is now split by route family:

- `panel/app.py` is the FastAPI composition/wiring root and shared helper home
- `routes_home.py`, `routes_jobs.py`, `routes_queue_control.py`, `routes_assistant.py`, `routes_missions.py`, `routes_bundle.py`, `routes_hygiene.py`, and `routes_recovery.py` own their route surfaces
- `panel/assistant.py` owns assistant-thread persistence helpers

### Config and path layers

Two related but distinct config surfaces exist:

- `config.py:load_config()` loads runtime/operator settings from `config.json`, environment overrides, and defaults
- `config.py:load_app_config()` loads stricter app/provider settings from `config.yml`
- `paths.py` provides XDG helper paths and the default queue-root fallback used by runtime config

When documenting or extending config behavior, preserve that distinction explicitly so queue-root/runtime ops settings are not confused with provider/app-model settings.

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
├── cli_queue.py              — Intentional root CLI composition/truth surface for the
│                               queue command family. Owns: queue_app,
│                               queue_approvals_app, queue_lock_app, inbox_app,
│                               artifacts_app Typer sub-apps; command implementations
│                               for init, status, and lock status; _render_lock_status
│                               helper; register() composition root. Attaches extracted
│                               handler modules (cli_queue_files, cli_queue_bundle,
│                               cli_queue_approvals, cli_queue_inbox, cli_queue_health,
│                               cli_queue_lifecycle, cli_queue_hygiene) via
│                               app.command(...)(fn). This is the intentional endpoint
│                               of the CLI extraction series — remaining contents are
│                               truth-sensitive operator-facing rendering and root
│                               composition that stays here by design.
├── cli_queue_approvals.py    — queue approvals command-family handlers (list, approve,
│                               deny). Owns: queue_approvals_list, queue_approvals_approve,
│                               and queue_approvals_deny handler functions (full
│                               implementations including approval resolution, fail-closed
│                               error handling, and rich table rendering). Registered to
│                               queue_approvals_app in cli_queue.py; top-level CLI
│                               registration and public CLI contract ownership stay in
│                               cli_queue.py.
├── cli_queue_bundle.py       — queue bundle/incident-bundle command handler.
├── cli_queue_files.py        — queue files command-family handlers (find, grep, tree,
│                               copy, move, rename). Owns queue_files_app Typer sub-app,
│                               _enqueue_files_step, and files-local payload-builder
│                               invocation. queue_files_app is attached to queue_app in
│                               cli_queue.py; top-level CLI registration stays there.
├── cli_queue_health.py       — queue health/health-reset command-family handlers.
│                               Owns: queue_health and queue_health_reset handler
│                               functions (full implementation including snapshot/render
│                               helpers and health-reset audit log emission). Registered
│                               to queue_app in cli_queue.py; top-level CLI registration
│                               and public CLI contract ownership stay in cli_queue.py.
├── cli_queue_hygiene.py      — queue prune/reconcile and artifacts-prune hygiene
│                               command-family handlers. Owns: queue_prune,
│                               queue_reconcile, and artifacts_prune handler functions
│                               (full implementations including reporting, config-override
│                               resolution, and JSON output formatting). Registered to
│                               queue_app / artifacts_app in cli_queue.py; top-level CLI
│                               registration and public CLI contract ownership stay in
│                               cli_queue.py.
├── cli_queue_inbox.py        — inbox command-family handlers (add, list). Owns: inbox_add
│                               and inbox_list handler functions (full implementations
│                               including job creation, goal validation, fail-closed error
│                               handling, and rich table rendering). Registered to inbox_app
│                               in cli_queue.py; top-level CLI registration and public CLI
│                               contract ownership stay in cli_queue.py.
├── cli_queue_lifecycle.py    — queue lifecycle command-family handlers (cancel, retry,
│                               unlock, pause, resume). Owns: queue_cancel, queue_retry,
│                               queue_unlock, queue_pause, and queue_resume handler
│                               functions (full implementations including fail-closed
│                               FileNotFoundError and QueueLockError handling, force-unlock
│                               path, and stale-lock detection). Registered to queue_app in
│                               cli_queue.py via queue_app.command(...)(fn); top-level CLI
│                               registration and public CLI contract ownership stay in
│                               cli_queue.py.
├── cli_queue_payloads.py     — Low-risk CLI queue payload/arg shaping helpers used by
│                               queue-files and health-reset commands. Keeps pure-ish
│                               payload normalization out of command wiring/orchestration.
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
│   │                           notes_archive_flow, system_inspect, system_diagnostics
│   ├── mission_planner.py    — LLM-based planning; fallback chains (primary→fast→fallback);
│   │                           deterministic write/terminal-demo routes; step normalization
│   │                           and rewriting; error classification; planner timeouts (25s)
│   ├── simple_intent.py      — Deterministic goal-kind intent classifier; direct routing
│   │                           for high-confidence intents (read_file, write_file,
│   │                           open_url, open_app, open_terminal, run_command,
│   │                           assistant_question); fail-closed mismatch detection
│   ├── file_intent.py        — Bounded file operation intent classifier; maps to
│   │                           preview payloads (exists/stat/mkdir/delete/copy/move/archive)
│   ├── code_draft_intent.py  — Code draft detection and extraction from planner output
│   ├── writing_draft_intent.py — Writing draft detection and extraction
│   ├── queue_job_intent.py   — Job intent metadata parsing and request-kind derivation
│   ├── queue_object_model.py — Canonical lifecycle state constants (QUEUE_LIFECYCLE_STATES,
│   │                           TERMINAL_OUTCOMES, COMPLETED_STATES)
│   ├── queue_result_consumers.py — Structured execution/result normalization;
│   │                           normalized_outcome_class; evidence-grounded reviewer surfaces;
│   │                           boundary-blocked vs retryable-failure classification
│   ├── execution_capabilities.py — Capability boundary tracking and scope metadata
│   ├── execution_evaluator.py    — Mission outcome evaluation helpers
│   ├── artifacts.py          — Artifact directory pruning (max_age_s / max_count policies);
│   │                           symlink-safe safety checks; dry-run support
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
├── vera_web/
│   │
│   │   ── Vera Web Service (conversational surface, port 8790) ──
│   │
│   ├── app.py                — FastAPI Vera web service root. Manages session lifecycle,
│   │                           conversational turns, execution-mode routing
│   │                           (CONVERSATIONAL_ARTIFACT vs GOVERNED_PREVIEW), preview
│   │                           drafting/revision/submission, investigation flows,
│   │                           weather routing, and linked-job result delivery.
│   ├── execution_mode.py     — Non-I/O execution-mode predicates/classifier helpers.
│   ├── conversational_checklist.py — Deterministic conversational checklist parsing/rendering/sanitization helpers.
│   ├── preview_content_binding.py — Low-risk preview-body/content-binding purity helpers (selection/rejection predicates).
│   ├── chat_early_exit_dispatch.py — Early-exit intent handler dispatch: evaluates the coherent cluster of special-intent / short-circuit conditions (diagnostics refusal, job review, follow-up preview, investigation derived-save/compare/summary/expand/save, near-miss submit) before the LLM path. Returns EarlyExitResult with write instructions; app.py performs all session writes, turn appends, and rendering.
│   ├── draft_content_binding.py — Post-LLM reply content extraction and draft-to-preview binding derivation (code/writing/generation/create-and-save).
│   ├── response_shaping.py   — Post-guardrail assistant reply assembly: preview-content derivation, false-claim guardrails, stale-shell cleanup predicate, and assemble_assistant_reply() (naming-mutation, control-turn, preview-dump suppression, ambiguous-request, fail-closed messaging, status derivation).
│   ├── templates/index.html  — Single-page HTML shell for the Vera conversational UI
│   └── static/vera.css       — Vera web stylesheet
│
├── voice/                    — Bounded voice foundation seam (flag-gated transcript input + placeholder output status)
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

src/voxera_builtin_skills/    — 31 built-in skills packaged as Python callables
                                clipboard (copy, paste)
                                files (copy_file, delete_file, exists, list_dir, mkdir,
                                       move_file, read_text, stat, write_text)
                                system (disk_usage, host_info, load_snapshot, memory_usage,
                                        open_app, open_url, process_list, recent_service_logs,
                                        service_status, set_volume, status, terminal_run_once,
                                        window_list)
                                sandbox (exec)

skills/                       — Skill definitions (manifest.yml per skill)
├── clipboard/{copy,paste}/
├── files/{copy_file,delete_file,exists,list_dir,mkdir,move_file,read_text,stat,write_text}/
├── sandbox/exec/             — Podman-based; rootless; --network=none by default
└── system/{disk_usage,host_info,load_snapshot,memory_usage,open_app,open_url,
           process_list,recent_service_logs,service_status,set_volume,
           status,terminal_run_once,window_list}/

tests/                        — ~89 test files (run `cloc --vcs git` for current counts)
├── test_mission_planner.py   — Planner fallback chains, error classification, JSON recovery
├── test_cli_queue.py         — Queue lifecycle, approvals, retry/cancel/delete
├── test_queue_daemon.py      — Failed-sidecar schema v1, retention pruning, lifecycle smoke
├── test_vera_web.py          — Vera web service session/flow coverage
├── test_panel.py             — Panel route and operator-surface coverage
├── test_doctor.py            — Diagnostic endpoints, version alignment
└── ...                       — Config, execution, inbox, capabilities, security, contracts

deploy/systemd/user/
├── voxera-daemon.service     — Queue processor; polls inbox/ every second
├── voxera-panel.service      — FastAPI panel; requires VOXERA_PANEL_OPERATOR_PASSWORD
└── voxera-vera.service       — Vera web service; port 8790

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
  panel/job_presentation.py
                         job-detail presentation/status row builders used by app.py
  panel/job_detail_sections.py
                         low-risk job-detail section assembly helpers used by app.py
  panel/auth_state_store.py
                         panel auth-state storage/cleanup/bookkeeping helpers used by app.py
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
│   ├── prune        remove stale terminal jobs  (cli_queue_hygiene.py — queue_prune)
│   ├── reconcile    orphan/duplicate detection + fix (cli_queue_hygiene.py — queue_reconcile)
│   ├── health       raw health snapshot (cli_queue_health.py — queue_health)
│   ├── health-reset reset health snapshot (cli_queue_health.py — queue_health_reset)
│   ├── cancel       cancel a queued or pending job (cli_queue_lifecycle.py — queue_cancel)
│   ├── retry        re-queue a failed job (cli_queue_lifecycle.py — queue_retry)
│   ├── unlock       remove stale/dead lock (cli_queue_lifecycle.py — queue_unlock)
│   ├── pause        pause queue processing (cli_queue_lifecycle.py — queue_pause)
│   ├── resume       resume queue processing (cli_queue_lifecycle.py — queue_resume)
│   ├── delete       delete a terminal job + all sidecars
│   │
│   ├── files        (cli_queue_files.py — queue_files_app)
│   │   ├── find     enqueue files.find as a governed queue job
│   │   ├── grep     enqueue files.grep_text as a governed queue job
│   │   ├── tree     enqueue files.list_tree as a governed queue job
│   │   ├── copy     enqueue files.copy as a governed queue job
│   │   ├── move     enqueue files.move as a governed queue job
│   │   └── rename   enqueue files.rename as a governed queue job
│   │
│   ├── approvals    (cli_queue_approvals.py — queue_approvals_app)
│   │   ├── list     list pending approvals
│   │   ├── approve  grant approval for a pending step
│   │   └── deny     deny a pending step
│   │
│   └── lock         (queue_lock_app)
│       └── status   show daemon lock status
│
├── inbox            (cli_queue_inbox.py — inbox_app)
│   ├── add          submit a goal text as a job file
│   └── list         list inbox items
│
├── artifacts        (cli_queue.py — artifacts_app)
│   └── prune        prune stale artifacts (cli_queue_hygiene.py — artifacts_prune)
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
- `panel/job_presentation.py` owns low-risk/pure-ish row/presentation helpers used for operator outcome labeling and policy/evidence/why-stopped rendering
- `panel/job_detail_sections.py` owns low-risk job-detail section assembly helpers that compose row builders from already-loaded canonical data while final truth-critical payload ownership remains in `panel/app.py`
- `panel/auth_state_store.py` owns low-risk panel auth-state storage/cleanup/bookkeeping helpers (map pruning, payload updates, lockout state reads) while final auth/mutation enforcement remains in `panel/app.py`
- Each route domain owns a focused set of paths: `routes_assistant.py`, `routes_missions.py`, `routes_bundle.py`, `routes_queue_control.py`, `routes_hygiene.py`, `routes_recovery.py`, `routes_home.py`, `routes_jobs.py`
- New panel route domains should live in focused route modules; `panel/app.py` remains the composition root

**CLI** (`src/voxera/`)
- `cli.py` is the composition root — it creates the Typer app, registers sub-apps from `cli_queue.py`, and registers the `doctor` command from `cli_doctor.py`
- `cli_queue.py` is the intentional root CLI composition/truth surface for the queue command family — it owns Typer sub-app definitions, `register()`, and the remaining truth-sensitive command implementations (`queue status`, `queue init`, `queue lock status`). Eight extracted handler modules (`cli_queue_payloads.py`, `cli_queue_files.py`, `cli_queue_health.py`, `cli_queue_hygiene.py`, `cli_queue_bundle.py`, `cli_queue_approvals.py`, `cli_queue_inbox.py`, `cli_queue_lifecycle.py`) own their respective command-family implementations and are registered from `cli_queue.py`. The CLI extraction series is considered complete; future extraction of remaining commands is optional, not presumed. Doctor command wiring lives in `cli_doctor.py`; shared primitives live in `cli_common.py`
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
- Inspection skills: `files.list_dir`, `files.exists`, `files.stat`, `files.find`, `files.grep_text`, and `files.list_tree` are local, `needs_network=false`, `fs_scope=read_only`, and notes-root confined.
- Mutating organization skills: `files.copy_file`, `files.move_file`, `files.copy`, `files.move`, `files.rename`, `files.mkdir`, and `files.delete_file` are local, `needs_network=false`, `fs_scope=workspace_only`, and notes-root confined.

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
For submit payload, request-kind normalization, minimum artifact guarantees, and result-consumer
interpretation contracts, see `docs/QUEUE_CONSTITUTION.md`.

## Vera preview drafting boundary notes (PR #154)

- Vera's deterministic preview drafting now lives in `src/voxera/vera/preview_drafting.py`, while `src/voxera/vera/handoff.py` remains an intentionally small compatibility façade for existing handoff-facing imports.
- Supported intent families in this layer are intentionally narrow: web navigation URL opens, explicit file reads, and basic note/file write intents.
- Save-by-reference write intents now include bounded current-session assistant-content resolution for phrases like `that summary`, `your previous answer`, and `the previous response`, routed into the same governed `write_file` preview contract.
- Precedence rule: when a current investigation-derived comparison/summary/expanded-result exists in session, follow-up `save that` / `save it` routes to the derived investigation save path first; generic recent-assistant-content save resolution is fallback-only.
- Routing boundary refinement: transform-style follow-ups over investigation-derived text (for example `write an article based on that summary`) are not treated as derived-save requests, even if they reference `that`/`it`; those turns hand off to the governed writing lane instead.
- Recency refinement: derived save-that precedence applies only while the derived comparison/summary/expanded result remains the latest relevant assistant output; if a newer conversational assistant answer appears, singular `save that ...` resolves to that newer answer.
- Singular vague references (for example `save that`, `save it`, `put that in a note`) deterministically resolve through a bounded latest-saveable-assistant-artifact layer rather than lane-specific special cases.
- Resolver scope is intentionally limited to recent assistant-authored content in the active session transcript only (no cross-session recall, no broad history search).
- Plural/explicitly ambiguous or unavailable assistant-content references fail conservatively with a clear user-facing refusal rather than guessing.
- Recent-content resolution ignores trivial courtesy assistant turns plus queue/preview/control boilerplate, so `thanks` / `you're welcome` exchanges do not displace the latest substantial explanation, summary, weather answer, or other meaningful informational artifact.
- Conversational explanatory/teaching prompts remain in the normal Vera answer lane by default; they are not automatically treated as web investigation requests.
- Bounded prose-drafting prompts (essay/article/writeup/explanation/rewrite/formalize/expand) compile into governed `write_file` previews backed by assistant-authored prose.
- Read-only Brave investigation routing is reserved for explicit search/investigation/current-information intent (for example `search the web`, `look up`, `find the latest`, `latest official docs`).
- Ordinary weather/current-condition questions use a dedicated structured quick-weather lane first; they do not fall through to freeform conversational guessing.
- The quick-weather lane is truthful-by-construction: if location is missing Vera asks for it, if a structured provider lookup succeeds Vera returns a concise synthesized weather answer, and if lookup fails or source evidence is too weak Vera refuses to invent live conditions.
- Generic multi-result investigation dumps are not the default weather UX; explicit browse/search/investigation phrasing is required to enter the generic web-investigation lane for weather.
- Ordinary compare/explain prompts stay conversational unless explicit search/latest/current/web intent is present.
- Because save-by-reference uses session transcript content, this path depends on a real assistant-authored answer existing in the active session first.
- Preview state is persisted per session (`pending_job_preview`) and is independent from rolling chat turn limits.
- Session state also keeps a bounded recent-saveable-assistant-artifact list so meaningful assistant-displayed content can be saved naturally without introducing open-ended memory or cross-session recall.
- Session state now also carries bounded weather context so short follow-ups like `hourly`, `7 day`, or `weekend` can continue the same weather flow naturally without opening a new generic investigation.
- The session keeps exactly one active preview draft; follow-up revisions replace that draft, while lightweight acknowledgements leave it unchanged.
- Hidden compiler/deterministic fallback prioritize semantic active-preview refinement interpretation (content/path/mode, pronouns) while preserving strict preview-only JSON mutation contracts and fail-closed behavior.
- Explicit handoff submits only the latest active draft, and successful submit clears the draft after queue confirmation.
- Submission remains a separate explicit step that writes to queue inbox; no direct execution path exists in Vera.

## Vera empty-state guidance layer (PR #next)

The standalone Vera UI now includes a lightweight first-run guidance layer on the main screen when no conversation turns exist.

- Purpose: teach the product model quickly without a tour or blocking onboarding.
- Placement: rendered inside the normal empty-chat/main-thread state so the composer remains primary.
- Content: a short "How to use Vera" explanation, a concise preview/submit truth note, and grouped example prompts for **Ask**, **Investigate**, **Save**, **Write**, **Code**, and **System** lanes.
- Interaction: prompt chips are clickable and populate the composer only; they do not auto-submit or bypass normal conversational flow.
- Lifecycle: once chat turns exist, the guidance disappears from the main thread view and the normal conversation UI remains unchanged.


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
- Prose-body cleanup now also strips leading assistant setup/preface sentences before the real document start, using bounded heuristics around headings, bolded titles, and first substantial body blocks so saved artifacts begin at the document itself. The same cleanup is applied when explanation-style artifacts are later saved by reference from recent assistant content, including short conversational preambles before the real explanation body.
- Internal `<voxera_control>` transport blocks are stripped before user-visible rendering and before prose preview-body extraction, so hidden control payloads stay internal even when a model leaks them into the raw reply.
- Writing replies are excluded from conversational-control suppression, so the user sees the generated prose in chat while the same content becomes the preview body.
- When an active governed writing preview exists, follow-up refinements like `make it more formal` or `rewrite that as ...` refresh the preview content with the new prose reply rather than leaving stale draft content behind.
- Save-as / rename refinements preserve the exact requested filename in `write_file.path`; the renamed path survives through submit rather than snapping back to the default generated filename.
- Active `write_file` previews can be renamed before submit via natural phrases ("call the note X", "call this note X", "rename it to X") and explicit path directives ("use path: ~/VoxeraOS/notes/X", "change the path to ..."). Unsafe path targets (parent traversal, queue scope) fail closed. Content and mode are preserved across rename/path changes.
- `normalize_preview_payload()` enforces `is_safe_notes_path` on all `write_file.path` values — this is the authoritative safety gate for both deterministic and LLM-generated preview mutations. Unsafe paths raise ValueError, leaving the prior preview unchanged.
- Combined prose refinement + save-as turns are resolved as preview updates, not implicit submit intents: Vera first keeps the new assistant-authored prose body authoritative in `write_file.content`, then applies the requested filename/path update before any later explicit handoff.

**Recent assistant-content resolver (`vera/handoff.py`, `vera/service.py`):**
- Save-by-reference resolution now recognizes `explanation`/`previous explanation` phrasing alongside summary/answer/response terms.
- Recent-content selection filters out trivial courtesy assistant turns (including extended variants like `You're very welcome ... if you'd like ...`), keeping the latest substantial explanation/saveable prose artifact resolvable across lightweight conversational interruptions. Concise meaningful factual answers (e.g. "2 + 2 is 4.") are saveable — the minimum content threshold allows short complete statements while still excluding trivial fragments and courtesy responses.
- Explanation text produced after a code draft is treated as a saveable conversational text artifact and can be renamed/saved through the same governed preview path.

**Investigation/web-routing boundary:**
- Writing follow-ups on top of investigation-derived summaries remain in the writing lane and produce text previews.
- Plain save/save-as follow-ups on investigation-derived summaries still stay on the derived-artifact lane, so compare/summary/expanded-result save behavior remains unchanged when no transform is requested.
- Expanded investigation-result writeups are stored in the same bounded session slot as compare/summary outputs, so follow-up `save it` / `save it as <name>.md` requests bind deterministically to the latest expanded result.
- `_is_informational_web_query()` is intentionally narrower: ordinary compare/explain prompts stay conversational; explicit search/latest/current/docs/web-investigation intent still routes to Brave.

**Current limitations:**
- The lane is intentionally bounded to single text documents.
- Prose-body extraction is heuristic and intentionally bounded: wrapper/preface text is stripped only when it matches known draft-introduction patterns or is separated from the body by blank-line block structure.
- No docx/pdf generation, multi-file writing projects, or publishing workflows are added here.

## Conversational mode lock (checklist / planning / structured reasoning)

Non-actionable structured reasoning requests (checklists, planning, brainstorming, itineraries) are answered conversationally by default — preview drafting is not attempted. The answer is stored as a saveable artifact so `save that` creates a governed preview afterward.

### Execution mode (`vera_web/app.py`, `vera_web/execution_mode.py`)

Every chat turn is classified into one of two execution modes **early** — the mode is then enforced **globally** at every downstream decision point:

| Mode | When | Effect |
|------|------|--------|
| `CONVERSATIONAL_ARTIFACT` | Planning/checklist keyword detected, no save/write intent, no active preview | Preview builder skipped, heavy guardrails bypassed, control-reply suppression bypassed, hard sanitization applied |
| `GOVERNED_PREVIEW` | Everything else (save intent, active preview, non-planning requests) | Normal preview/builder/guardrail flow |

**Classifier (`_classify_execution_mode`, `_is_conversational_answer_first_request`):**
- Rule-based, not LLM-based — deterministic for the same input every time.
- Matches planning/checklist patterns (checklist, list, plan, organize, prepare, grocery list, packing list, to do, brainstorm, itinerary, etc.) while excluding messages with explicit save/write/file intent (`_SAVE_WRITE_FILE_SIGNAL_RE`).
- `vera_web/execution_mode.py` owns the low-risk non-I/O lane predicates/classification helpers; `vera_web/app.py` keeps final submit/handoff boundary ownership and preview/queue truth writes.
- `vera_web/preview_content_binding.py` owns low-risk preview-body/content-binding helper predicates (placeholder-body rejection, control-narration body rejection, targeted code-content refinement detection), while `vera_web/app.py` retains final preview/session writes and canonical submit/handoff truth ownership.
- `vera_web/chat_early_exit_dispatch.py` owns the early-exit intent handler dispatch cluster: diagnostics refusal, job review, follow-up preview, investigation derived-save/compare/summary/expand/save, and near-miss submit blocking. `app.py` performs all session writes described by the returned `EarlyExitResult`, owns `append_session_turn`, `_render_page`, and all submit/handoff truth decisions.

### Multi-turn continuation (`vera/session_store.py`, `vera_web/app.py`)
- A `conversational_planning_active` boolean is persisted in session state whenever a `CONVERSATIONAL_ARTIFACT` turn occurs.
- On the next turn, if the flag is set, the user has no save/write intent, and no preview is active, the turn stays conversational. This allows multi-turn planning flows where Vera asks for details and the user provides them.
- The flag is cleared whenever the turn is `GOVERNED_PREVIEW` (save intent, preview exists, or the user changes topics).

### Shared session context (`vera/session_store.py`, `vera_web/app.py`)
- A bounded `shared_context` dict is persisted in the session payload alongside other preserved fields.
- Tracks workflow-continuity references: `active_draft_ref`, `active_preview_ref`, `last_submitted_job_ref`, `last_completed_job_ref`, `last_reviewed_job_ref`, `last_saved_file_ref`, `active_topic`, `ambiguity_flags`.
- Updated at lifecycle points: preview creation/update, submit/handoff, completion ingestion, job review, session clear.
- **Subordinate to canonical truth:** preview truth, queue truth, and artifact/evidence truth always win if they conflict with session context. Context is a continuity aid, not a trust replacement.
- If continuity is ambiguous, the system fails closed rather than guessing.
- Schema is normalized on every read/write: unknown keys dropped, missing keys filled from defaults, non-string refs cleared.

### Session-scoped reference resolution (`vera/reference_resolver.py`)
- A bounded reference-resolution layer maps natural in-session phrases to concrete referents using shared session context.
- Supported reference classes: `DRAFT` ("that draft", "the draft"), `FILE` ("that file", "the note"), `JOB_RESULT` ("that result", "the last job"), `CONTINUATION` ("the follow-up", "the last one").
- Resolution priority per class:
  - **Draft**: `active_draft_ref` > `active_preview_ref`.
  - **File**: `last_saved_file_ref` > `active_draft_ref` (only if path-like).
  - **Job/result**: `last_completed_job_ref` > `last_reviewed_job_ref` > `last_submitted_job_ref`.
  - **Continuation**: `active_preview_ref` > completed/reviewed/submitted job refs.
- The resolver returns string ref values only — callers validate against canonical preview/queue/artifact truth downstream.
- Missing or ambiguous references fail closed (`UnresolvedReference`).
- `resolve_job_id_from_context()` provides a job-ID fallback for the early-exit dispatch when neither explicit job ID nor handoff state provides one.
- The early-exit dispatch (`vera_web/chat_early_exit_dispatch.py`) uses session context as a fallback for job review and follow-up flows.
- Successful job reviews now set `last_reviewed_job_ref` in session context. File-save submissions now set `last_saved_file_ref`.

### Hard conversational mode lock — zero preview/JSON/meta leakage guarantee

**Six-phase sanitizer (`_sanitize_false_preview_claims_from_answer`):**
1. **JSON blocks (Phase 1a+1b):** Strips fenced (`` ```json...``` ``) AND unfenced multi-line JSON blocks.
2. **False-claim phrases (Phase 2):** Strips lines matching 55+ known phrases and broader regex references via `_PREVIEW_OR_DRAFT_REFERENCE_LINE_RE`.
3. **HARD MODE LOCK (Phase 3):** Strips ANY remaining non-list-item line containing a banned token (`preview`, `draft`, `submit`, `submitted`, `submission`, `queue`, `queued`). List items are protected.
4. **Workflow narration (Phase 4):** Strips save-adjacent language, confirmation prompts, attention-directing.
5. **Meta-commentary (Phase 5):** Strips lines like "I've organized...", "Here's what I came up with", "I've broken it down..." — but ONLY when actual list items are present.
6. **Bare JSON payloads (Phase 6):** Strips single-line JSON objects matching `_BARE_JSON_PAYLOAD_RE` (`{"intent":...}`, `{"goal":...}`, `{"action":...}`, `{"write_file":...}`).

- Applied instead of both `_guardrail_submission_claim` and `_guardrail_false_preview_claim` for `CONVERSATIONAL_ARTIFACT` turns.
- **Core rule:** Conversational artifact mode must render the artifact itself (checklist items, plan steps) — not workflow narration, not JSON payloads, not meta-commentary.

### Save intent override
- If a message contains explicit save/write/file/note intent, it is classified as `GOVERNED_PREVIEW` regardless of planning keywords.
- **Create-and-save fallback:** Handles hybrid requests like "save a checklist to a note for my wedding prep" that have both explicit save intent AND planning keywords but no prior content to reference. When the builder fails, the system creates a note preview from the LLM's reply content post-reply.

### Gating points (enforced by `ExecutionMode`)
- Builder skip: preview builder is not called for `CONVERSATIONAL_ARTIFACT` turns.
- Sanitizer: six-phase sanitizer with hard mode lock replaces both heavy guardrails.
- Create-and-save fallback: fires after LLM reply when builder failed on a save+planning hybrid.
- Control-reply suppression skip: full conversational answer is always shown.

### Save-after-answer flow
- Conversational answers are stored as saveable artifacts (`build_saveable_assistant_artifact`).
- `save that` / `save that to a note` resolves to the most recent substantial artifact and creates a governed preview.
