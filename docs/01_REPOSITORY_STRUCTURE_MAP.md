# 01 — Repository Structure Map

This file maps the current repository. It is derived from a live walk of the tree plus direct reads of the key entrypoints. Every path referenced below exists in the current branch; see `directory_tree.txt` for the full mechanical listing.

## Top-level layout

```
VoxeraOS/
├── .github/                 CI workflows (ci, merge-readiness, failed-sidecar)
├── AGENT.md                 Agent-style operational memory index
├── CHANGELOG.md             Human-facing release changelog
├── CODEX.md                 Codex / AI-assistant handoff notes
├── CONTRIBUTING.md          Contribution guide
├── LICENSE                  Apache 2.0 license
├── Makefile                 Canonical build / test / validation / services targets
├── NOTICE                   NOTICE file
├── README.md                Project readme
├── SECURITY.md              Security reporting guidance
├── config-templates/        Example config.yml and policy.yml
├── deploy/
│   └── systemd/
│       └── user/            voxera-daemon.service, voxera-panel.service, voxera-vera.service
├── docs/                    Architecture, ops, security, this index bundle
├── missions/                File-based mission templates (JSON/YAML)
├── mypy.ini                 mypy config
├── pyproject.toml           Project metadata (voxera-os 0.1.9), deps, ruff, pytest
├── scripts/                 e2e shell scripts, mypy ratchet, update.sh
├── skills/                  Skill manifest directories (manifest.yml per skill)
├── src/
│   └── voxera/              Main Python package
├── systemd/                 Legacy top-level systemd units (kept for reference)
├── tests/                   pytest suite: unit, contract, golden, red-team
├── tools/                   golden_surfaces.py, mypy-baseline.txt
└── uv.lock                  uv lockfile
```

`src/voxera/` is the only runtime source. There is no secondary package.

## `src/voxera/` — runtime package

The package ships 127 Python modules (see `module_inventory.json`). The top-level files group by role:

**Entrypoints and CLI composition**
- `cli.py` — Typer app root; registers config, skills, missions, queue, ops, inbox, secrets, artifacts sub-apps.
- `cli_common.py`, `cli_config.py`, `cli_doctor.py`, `cli_ops.py`, `cli_runtime.py`, `cli_skills_missions.py` — focused CLI surface modules.
- `cli_queue.py`, `cli_queue_approvals.py`, `cli_queue_bundle.py`, `cli_queue_files.py`, `cli_queue_health.py`, `cli_queue_hygiene.py`, `cli_queue_inbox.py`, `cli_queue_lifecycle.py`, `cli_queue_payloads.py` — queue CLI family.

**Config / paths / health / audit**
- `config.py` — loads `config.json` (runtime/operator) and `config.yml` (app/provider); snapshot + fingerprint.
- `paths.py` — XDG config/data, default `queue_root = ~/VoxeraOS/notes/queue`.
- `health.py`, `health_reset.py`, `health_semantics.py` — health snapshot + semantics.
- `audit.py` — JSONL audit log with `log()` + `tail()`.
- `version.py` — version string source.
- `policy.py` — capability → effect-class mapping (queue-first mutation gate input).
- `secrets.py` — keyring-backed secret store with file fallback.
- `golden_surfaces.py` + `tools/golden_surfaces.py` — CLI contract baseline checker.
- `doctor.py` + `cli_doctor.py` — provider readiness + quick diagnostics.
- `incident_bundle.py`, `ops_bundle.py` — system/job incident bundle export.
- `operator_assistant.py` — advisory assistant lane used by the panel.
- `openrouter_catalog.py` — cached OpenRouter model catalog (data file in `data/`).
- `setup_wizard.py` — typed first-run wizard.

**Brain / providers**
- `brain/base.py` — adapter protocol.
- `brain/fallback.py` — fallback reason classification (`TIMEOUT | AUTH | RATE_LIMIT | MALFORMED | NETWORK | UNKNOWN`).
- `brain/gemini.py` — Gemini adapter.
- `brain/openai_compat.py` — OpenAI-compatible adapter (OpenRouter default, Ollama etc.).
- `brain/json_recovery.py` — resilient JSON recovery for LLM responses.

**Data / prompts / models**
- `models.py` — Pydantic models (manifests, plan/run results, snapshots).
- `prompts.py` — prompt templates for runtime reasoning.
- `data/openrouter_catalog.json` — curated OpenRouter model catalog.

**Core control plane (`src/voxera/core/`)**
Canonical composition of queue daemon, mission runtime, planner, and queue contracts.
- `queue_daemon.py` — `MissionQueueDaemon`, lock handling, directory contract, composition of approvals/recovery/execution mixins.
- `queue_execution.py` — payload normalization, mission construction, planner handoff, execution-state transitions.
- `queue_approvals.py` — approval prompts, grants, pending artifacts, approve/deny resolution.
- `queue_recovery.py` — startup recovery, shutdown handling, quarantine/report shaping.
- `queue_contracts.py` — canonical payload shape, structured step results, execution result builder, lineage helpers.
- `queue_result_consumers.py` — structured-execution resolver, outcome classification.
- `queue_state.py` — `.state.json` sidecar read/write.
- `queue_paths.py` — deterministic queue path helpers; `move_job_with_sidecar`.
- `queue_job_intent.py` — `job_intent.json` enrichment.
- `queue_object_model.py` — object-model helpers / normalization.
- `queue_inspect.py` — lookup + snapshot helpers used by panel and CLI.
- `queue_hygiene.py` — prune / artifacts prune / retention.
- `queue_reconcile.py` — reconcile report + fix apply modes.
- `queue_assistant.py` — advisory assistant queue lane.
- `missions.py` — `MissionTemplate`, `MissionStep`, `MISSION_TEMPLATES`, `MissionRunner`, file-mission loading.
- `mission_planner.py` — plan-a-mission (cloud-planned) path.
- `planner_context.py` — planner context helpers.
- `capabilities_snapshot.py` — deterministic capabilities snapshot producer.
- `capability_semantics.py` — canonical capability semantics (`effect_class`, `intent_class`, resource boundaries, policy mapping).
- `execution_capabilities.py`, `execution_evaluator.py` — runtime capability/policy gating.
- `simple_intent.py` — deterministic intent classification + mismatch guard.
- `file_intent.py` — file-workflow boundary detection (queue subtree, traversal, allowed roots).
- `code_draft_intent.py`, `writing_draft_intent.py` — draft-intent classifiers used by Vera.
- `inbox.py` — inbox helpers.
- `router.py` — minimal router shim.
- `artifacts.py` — artifact helpers.

**Automation object model and runner (`src/voxera/automation/`)**
Durable automation definition layer plus a minimal runner. The runner
is scoped: it fires ``once_at``, ``delay``, and ``recurring_interval``
triggers, it never executes skills directly, and it submits work through
the same canonical inbox path the CLI/panel/Vera use so the queue remains
the execution boundary. Unsupported trigger kinds (``recurring_cron``,
``watch_path``) are persisted but explicitly skipped by the runner.
- `models.py` — `AutomationDefinition` Pydantic model, supported trigger kinds
  (`once_at`, `delay`, `recurring_interval`, `recurring_cron`, `watch_path`),
  per-kind trigger-config validation, canonical `payload_template` gating via
  the queue contract helpers in `core/queue_contracts.py`.
- `store.py` — file-backed CRUD helpers
  (`ensure_automation_dirs`, `save_automation_definition`,
  `load_automation_definition`, `list_automation_definitions`,
  `delete_automation_definition`) rooted under
  `~/VoxeraOS/notes/queue/automations/definitions/` with a sibling
  `history/` directory that the runner writes run records into.
- `runner.py` — minimal runner surface
  (`evaluate_due_automation`, `process_automation_definition`,
  `run_automation_once`, `run_due_automations`). Emits normal canonical
  queue payloads via `core/inbox.add_inbox_payload` using the
  `automation_runner` source lane. One-shot semantics: a fired ``once_at``
  or ``delay`` definition is saved back with `enabled=False`,
  `last_run_at_ms`, `last_job_ref`, and an appended `run_history_refs`
  entry, so repeated runner passes cannot double-submit. Recurring
  semantics: a fired ``recurring_interval`` definition stays enabled and
  re-arms ``next_run_at_ms`` to ``fired_at_ms + interval_ms``.
- `history.py` — durable run-history records
  (`build_history_record`, `write_history_record`, `generate_run_id`,
  `history_record_ref`, `list_history_records`). One JSON file per run event under
  `~/VoxeraOS/notes/queue/automations/history/`, atomically written via
  a `.tmp` sidecar and `Path.replace`. `list_history_records` returns all
  records for a given automation id, newest first, skipping malformed files.

**Skills subsystem (`src/voxera/skills/`)**
- `registry.py` — `SkillRegistry`, `SkillDiscoveryReport`, manifest validation.
- `runner.py` — `SkillRunner`, policy-aware simulation and execution.
- `execution.py` — execution context helpers.
- `result_contract.py` — `skill_result.v1` extraction.
- `arg_normalizer.py` — arg validation/normalization.
- `path_boundaries.py` — notes-root path allowlist + traversal rejection.

**Vera chat layer (`src/voxera/vera/`)**
Conversational control surface — reasoning, preview, submit, review. Vera is not the runtime.
- `service.py` — top-level `generate_vera_reply`, provider selection, linked-job completion delivery.
- `prompt.py` — `VERA_SYSTEM_PROMPT`, `VERA_PREVIEW_BUILDER_PROMPT`, queue-boundary summary.
- `session_store.py` — bounded session persistence; active-preview state; saveable artifacts registry.
- `preview_drafting.py` — deterministic preview drafting; drafting guidance; save-by-reference shaping.
- `preview_submission.py` — active-preview submit detection, payload normalization, queue handoff acknowledgment.
- `draft_revision.py` — active preview rename/path/content follow-up interpretation.
- `handoff.py` — thin compatibility façade over the split handoff seams.
- `evidence_review.py` — review flow grounded in canonical queue evidence.
- `linked_completions.py` — linked-job tracking, ingestion, auto-surfacing.
- `result_surfacing.py` — result-forward text extraction.
- `saveable_artifacts.py` — recent assistant-content selection for save flows.
- `context_lifecycle.py` — shared-context update points (preview create/revise/clear, handoff, completion ingest, review, clear).
- `reference_resolver.py` — bounded reference-resolution layer (draft/file/job/continuation).
- `investigation_flow.py`, `investigation_derivations.py` — read-only web investigation and derived follow-ups.
- `weather.py`, `weather_flow.py` — live weather lookup flow.
- `brave_search.py` — Brave Search API client used by the investigation flow.

**Vera web app (`src/voxera/vera_web/`)**
- `app.py` — FastAPI app (`GET /`, `POST /chat`, `GET /chat/updates`, `POST /handoff`, `POST /clear`, `GET /vera/debug/session.json`).
- `conversational_checklist.py` — conversational checklist helpers.
- `chat_early_exit_dispatch.py` — fast-path dispatch for queue review/follow-up.
- `execution_mode.py` — execution mode helpers.
- `draft_content_binding.py`, `preview_content_binding.py` — binding authored content to drafts/previews.
- `response_shaping.py` — reply shaping / trimming.
- `static/`, `templates/` — Vera UI assets.

**Panel (`src/voxera/panel/`)**
FastAPI operator panel. Route modules plug into a single `FastAPI` app composed in `app.py`.
- `app.py` — `FastAPI(title="Voxera Panel")`, shared auth + CSRF + mutation guard, template env, route registration.
- `routes_home.py` — `GET /`, `GET/POST /queue/create`.
- `routes_jobs.py` — `GET /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/progress`, `POST /queue/jobs/{ref}/cancel|retry`, `POST /queue/approvals/{ref}/approve|approve-always|deny`, `GET /queue/jobs/{job}/detail|progress`.
- `routes_queue_control.py` — `POST /queue/jobs/{ref}/delete`, `POST /queue/pause`, `POST /queue/resume`.
- `routes_missions.py` — `GET/POST /missions/templates/create`, `GET/POST /missions/create`.
- `routes_hygiene.py` — `GET /hygiene`, `POST /hygiene/prune-dry-run|reconcile|health-reset`.
- `routes_recovery.py` — `GET /recovery`, `GET /recovery/download/{bucket}/{name}`.
- `routes_bundle.py` — `GET /jobs/{job_id}/bundle`, `GET /bundle/system`.
- `routes_assistant.py` — `GET /assistant`, `POST /assistant/ask`, `GET /assistant/progress/{request_id}` (advisory assistant lane).
- `routes_automations.py` — `GET /automations`, `GET /automations/{id}`, `POST /automations/{id}/enable|disable|run-now|delete`. Automation inspection and control dashboard — operator visibility into saved definitions, enable/disable toggling, queue-submitting run-now, and definition delete (history preserved). Does not author definitions or bypass the queue.
- `routes_vera.py` — `GET /vera`, `POST /vera/chat` route module (present in source; registration is not currently wired into `panel/app.py`, which composes home/jobs/queue-control/missions/hygiene/recovery/bundle/assistant/automations routes).
- `assistant.py` — assistant thread persistence helpers.
- `auth_state_store.py` — panel auth window/lockout state.
- `helpers.py`, `job_detail_sections.py`, `job_presentation.py` — request/response helpers, job view shaping.
- `static/`, `templates/` — panel UI assets.

**Audio / voice**
- `audio/` — audio foundation (currently a bounded seam; README describes the placeholder).
- `voice/flags.py`, `voice/input.py`, `voice/output.py`, `voice/models.py` — bounded voice-foundation interfaces; no full duplex loop yet.

## `skills/` — on-disk skill manifests

Every skill is a directory with a `manifest.yml`. The canonical registry is `src/voxera/skills/registry.py`, and runtime entrypoints live under `voxera_builtin_skills.*` (a separate built-ins package referenced from the manifests).

Current families (see `skill_manifest_inventory.json` for the full 31-manifest listing):

- `clipboard/`: `copy`, `paste`
- `files/`: `copy`, `copy_file`, `delete_file`, `exists`, `find`, `grep_text`, `list_dir`, `list_tree`, `mkdir`, `move`, `move_file`, `read_text`, `rename`, `stat`, `write_text`
- `sandbox/`: `exec`
- `system/`: `disk_usage`, `host_info`, `load_snapshot`, `memory_usage`, `open_app`, `open_url`, `process_list`, `recent_service_logs`, `service_status`, `set_volume`, `status`, `terminal_run_once`, `window_list`

## `missions/` — file-based mission templates

- `missions/README.md`
- `missions/sandbox_net.json`
- `missions/sandbox_smoke.json`

File-based missions are merged with in-code `MISSION_TEMPLATES`. In-code templates take precedence when ids collide. User-level missions under `~/.config/voxera/missions` are also loaded at runtime but are not in the repo. See `04_GOAL_MISSION_PLANNING_AND_EXECUTION.md`.

## `deploy/systemd/user/` — user services

All three units under `deploy/systemd/user/` use a token `@VOXERA_PROJECT_DIR@` that `make services-install` rewrites to the absolute project directory:

- `voxera-daemon.service` — `voxera daemon` (queue + missions).
- `voxera-panel.service` — `voxera panel --host 127.0.0.1 --port 8844`.
- `voxera-vera.service` — `python -m uvicorn voxera.vera_web.app:app --host 127.0.0.1 --port 8790`.

The top-level `systemd/` directory still carries `voxera-core.service` and `voxera-panel.service` as legacy references. `services-install` uses the `deploy/` path.

## `tests/` — pytest suite

See `08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md` for the themed breakdown. At a glance:

- queue daemon + lifecycle contracts
- queue contract snapshot + CLI contract snapshot (golden surfaces)
- Vera service, Vera web, preview, submission, review, linked completions
- skills registry, runner, wave2 files skills, sandbox exec, terminal_run_once
- panel, panel contract snapshot
- policy, capability semantics, execution evaluator, execution capabilities
- security / red-team regression
- ops bundle, doctor, setup wizard, config, CLI surfaces

## `scripts/` + `tools/`

- `scripts/e2e_smoke.sh` — small e2e smoke.
- `scripts/e2e_opsconsole.sh` — ops-console e2e.
- `scripts/e2e_golden4.sh` — golden-surface e2e used by full validation.
- `scripts/mypy_ratchet.py` — mypy ratchet used by `make type-check` and `make update-mypy-baseline`.
- `scripts/refresh_openrouter_catalog.py` — refresh the cached catalog.
- `scripts/update.sh` — update/smoke helper.
- `tools/golden_surfaces.py` — CLI contract baseline checker (`make golden-check`).
- `tools/mypy-baseline.txt` — canonical mypy baseline file.

## Module ownership (quick navigation)

| You want to change… | Go to |
|---|---|
| Queue lifecycle, buckets, daemon loop | `src/voxera/core/queue_daemon.py`, `queue_execution.py`, `queue_approvals.py`, `queue_recovery.py` |
| Payload contract, step results, execution result | `src/voxera/core/queue_contracts.py`, `queue_result_consumers.py` |
| `.state.json` sidecars | `src/voxera/core/queue_state.py` |
| Mission templates / file-mission loading | `src/voxera/core/missions.py` |
| Goal → plan pipeline | `src/voxera/core/mission_planner.py`, `planner_context.py` |
| Capability semantics / policy mapping | `src/voxera/core/capability_semantics.py`, `policy.py` |
| Skill manifest validation | `src/voxera/skills/registry.py` |
| Path boundaries (notes / queue subtree) | `src/voxera/skills/path_boundaries.py`, `core/file_intent.py` |
| Vera reply top-of-stack | `src/voxera/vera/service.py` |
| Vera preview / submit | `src/voxera/vera/preview_drafting.py`, `preview_submission.py`, `draft_revision.py` |
| Vera review / linked completions | `src/voxera/vera/evidence_review.py`, `linked_completions.py` |
| Vera web routes (chat UI) | `src/voxera/vera_web/app.py` |
| Panel route families | `src/voxera/panel/routes_*.py` |
| CLI new subcommand | the appropriate `src/voxera/cli_*.py`, wired from `cli.py` |
| Automation definition model / storage | `src/voxera/automation/models.py`, `src/voxera/automation/store.py` |
| Automation runner (once_at / delay / recurring_interval) | `src/voxera/automation/runner.py`, `src/voxera/automation/history.py` |
| Automation operator CLI (list / show / enable / disable / history / run-now) | `src/voxera/cli_automation.py` |
| Systemd units | `deploy/systemd/user/` |
| CLI help baselines | `tests/golden/` via `tools/golden_surfaces.py` |
