# Operations (Alpha v0.1.8)

This guide covers day-2 operations for VoxeraOS in service mode.

VoxeraOS is an open-source alpha project. See [README.md](../README.md) for project overview and setup instructions.

## Install services

From repository root:

```bash
make dev
make fmt-check
make check
make services-install
make daemon-restart
```

This installs user units from `deploy/systemd/user/` into `~/.config/systemd/user`, rendering
`WorkingDirectory` and `ExecStart` to your **current checkout path**, then reloads systemd user state
and enables/starts:
- `voxera-daemon.service`
- `voxera-panel.service`
- `voxera-vera.service`

Vera service runtime defaults:
- host: `127.0.0.1`
- port: `8790`
- start command: `@VOXERA_PROJECT_DIR@/.venv/bin/python -m uvicorn voxera.vera_web.app:app --host 127.0.0.1 --port 8790`

Useful Vera service commands:

```bash
make vera-start
make vera-stop
make vera-restart
make vera-status
make vera-logs
```


## Config + env locations

- Use `.env.example` as the template for local values.
- Keep secrets (for example `VOXERA_PANEL_OPERATOR_PASSWORD`) in `~/.config/voxera/env` when possible.
- `.env` in the repo is intentionally gitignored for local overrides.

### Vera read-only web investigation (Brave)

- `voxera setup` now prompts to enable Vera's read-only Brave web investigation.
- When enabled, the wizard stores `BRAVE_API_KEY` using Voxera secrets storage (system keyring when available, secure file fallback otherwise).
- `config.yml` stores only references (`api_key_ref`, `env_api_key_var`) plus `provider: brave` and `max_results`; raw keys are not written to config.
- Informational web queries use this lane and remain side-effect free (no queue submission, no preview generation, no execution).
- Print the effective redacted runtime snapshot with:

```bash
.venv/bin/voxera config show
```

- Persist a redacted config snapshot for incident tooling with:

```bash
.venv/bin/voxera config snapshot
```

This writes `notes/queue/_ops/config_snapshot.json` (or `<VOXERA_QUEUE_ROOT>/_ops/config_snapshot.json`) with `settings`, `sources`, `written_at_ms`, and `schema_version=1`, plus `_ops/config_snapshot.sha256`.

Config precedence: CLI overrides > `VOXERA_*` env > `~/.config/voxera/config.json` > built-in defaults.

Panel port truth:
- Runtime default from config/env/systemd is `127.0.0.1:8844`.
- `make panel` is a local-dev shortcut that intentionally runs on `127.0.0.1:8787`.


### Experimental voice foundation flags (bounded spike)

Voice in this release is a bounded foundation seam only (flag-gated transcript-origin routing + output status metadata), not a full voice UX/runtime loop.

Runtime config keys (`~/.config/voxera/config.json`) and equivalent env vars:

- `enable_voice_foundation` / `VOXERA_ENABLE_VOICE_FOUNDATION`
- `enable_voice_input` / `VOXERA_ENABLE_VOICE_INPUT`
- `enable_voice_output` / `VOXERA_ENABLE_VOICE_OUTPUT`
- `voice_stt_backend` / `VOXERA_VOICE_STT_BACKEND` (placeholder metadata only in this spike)
- `voice_tts_backend` / `VOXERA_VOICE_TTS_BACKEND` (placeholder metadata only in this spike)

Fail-closed behavior:
- If transcript-origin input is sent while voice foundation/input flags are off, Vera web rejects the turn with a clear `voice_input_disabled` error.
- Transcript-origin turns still route through normal Vera orchestration and queue handoff boundaries; no voice shortcut path is introduced.

For deterministic local/CI tests, Make targets run pytest with `VOXERA_LOAD_DOTENV=0` and unset key `VOXERA_*` vars so shell exports and repo `.env` do not alter test outcomes.

### Testing

For repeatable end-to-end runtime validation across CLI, queue, panel, Vera, and artifact evidence, use the dedicated playbook: [`docs/testing/RUNTIME_VALIDATION_PLAYBOOK.md`](testing/RUNTIME_VALIDATION_PLAYBOOK.md).

For compact conversational control-layer smoke checks focused specifically on Vera drafting/save/submit behavior, use: [`docs/testing/VERA_REGRESSION_PACK.md`](testing/VERA_REGRESSION_PACK.md).

Tests isolate the operator health snapshot via the `VOXERA_HEALTH_PATH` environment variable.  The `conftest.py` `_isolate_health_snapshot` fixture sets this automatically for every test.

Current refactor ownership map for contributors/operators:

- `src/voxera/vera/handoff.py` is now a thin compatibility façade. Prefer extending:
  - `src/voxera/vera/preview_drafting.py` for deterministic preview drafting
  - `src/voxera/vera/draft_revision.py` for active-preview rename/path/content follow-ups
  - `src/voxera/vera/preview_submission.py` for active-preview submit detection and queue handoff normalization
  - `src/voxera/vera/investigation_derivations.py` for compare/summarize/expand/save-derived follow-ups
- `src/voxera/vera/service.py` remains the top-level Vera orchestrator; extracted weather/investigation/session helpers should keep growing in their dedicated modules rather than re-concentrating there when possible.
- Queue lifecycle behavior is now intentionally split across `src/voxera/core/queue_execution.py`, `queue_approvals.py`, and `queue_recovery.py`, with `queue_daemon.py` acting as the runtime composition root.
- Panel route ownership is split across `src/voxera/panel/routes_*.py`; prefer extending the route-family module first instead of adding new route behavior directly to `panel/app.py` unless the change is cross-cutting wiring/shared helper work.
- Runtime/operator config (`config.json`, `load_config`) and app/provider config (`config.yml`, `load_app_config`) are distinct surfaces by design; keep docs and operational guidance explicit about which layer is being changed.

Vera modularization safety-net note: session-sensitive Vera characterization coverage now has narrower anchors in `tests/test_vera_session_characterization.py` and `tests/test_vera_contextual_flows.py`, backed by the shared harness/builders in `tests/vera_session_helpers.py`. Prefer extending those focused tests for session/saveability/weather/investigation regressions when the scenario does not require the full mixed-flow surface in `tests/test_vera_web.py`.

Vera session persistence/state ownership now lives in `src/voxera/vera/session_store.py`, with `src/voxera/vera/service.py` retaining compatibility delegation for existing call sites. Prefer adding or updating session read/write helpers in `session_store.py` first when working on future Vera modularization PRs.

Vera weather quick-flow orchestration now lives in `src/voxera/vera/weather_flow.py`, with `src/voxera/vera/service.py` delegating into that module while keeping compatibility aliases for existing patches/tests. Prefer extending `weather_flow.py` for weather-question detection, missing-location handling, follow-up continuity, and fail-closed weather-lane behavior instead of re-growing those branches inside `service.py`.

Vera saveable assistant artifact selection now lives in `src/voxera/vera/saveable_artifacts.py`, with `src/voxera/vera/handoff.py` retaining compatibility imports for existing callers. Prefer extending `saveable_artifacts.py` for meaningful-content selection, courtesy/control filtering, and recent `"save that"` target resolution instead of re-growing that logic inside `handoff.py`.

Vera active preview draft revision interpretation now lives in `src/voxera/vera/draft_revision.py`, with `src/voxera/vera/handoff.py` delegating rename/path/content refinement parsing into that module while keeping the handoff-side entrypoint stable. Prefer extending `draft_revision.py` for active preview rename/path/content follow-up behavior instead of re-growing that seam inside `handoff.py`.

Vera preview submission and handoff normalization now live in `src/voxera/vera/preview_submission.py`, with `src/voxera/vera/handoff.py` and `src/voxera/vera_web/app.py` delegating submit-intent detection, authoritative preview normalization, truthful no-preview submit handling, and queue acknowledgement shaping into that module. Prefer extending `preview_submission.py` for submit/current-preview behavior instead of re-growing that seam inside `handoff.py` or `app.py`.

Vera deterministic preview drafting now lives in `src/voxera/vera/preview_drafting.py`, with `src/voxera/vera/handoff.py` intentionally reduced to an import-stable façade for existing callers. Prefer extending `preview_drafting.py` for narrow action-preview drafting, diagnostics preview shaping, note/file drafting, and contextual save-by-reference glue instead of re-growing those branches inside `handoff.py`.

Checklist/planning conversational turns are intentionally deterministic in-chat artifacts: `src/voxera/vera_web/app.py` hard-locks final rendering to checklist output and strips preview/draft/save/submit/queue narration when no real preview exists. Source priority is explicit user-provided checklist items first, then usable extracted model/JSON items, then generic fallback only if extraction fails. Explicit save/write intent remains the authoritative override into governed preview behavior.

Path-precedence rules (applied inside `health.py`):
- **Explicit `queue_root`**: any call that receives an explicit `queue_root: Path` always uses `queue_root/health.json` and **ignores** `VOXERA_HEALTH_PATH`.  This preserves pre-seeded test fixtures and is the common case for unit and integration tests.
- **Default-path flows** (no explicit `queue_root`): `VOXERA_HEALTH_PATH` is honoured when set, preventing operator / panel / CLI default-path flows from writing to `notes/queue/health.json` during a test run.
- **Panel security counters**: panel auth/CSRF counters now resolve reads and writes through the same health-root path, so homepage counters always reflect the exact snapshot file being updated (default queue root or isolated `VOXERA_HEALTH_PATH`).
- **Production** (unset `VOXERA_HEALTH_PATH`): reads and writes use `notes/queue/health.json` as before; no behavior change.

Key runtime env vars (defaults):
- `VOXERA_QUEUE_ROOT` (`~/VoxeraOS/notes/queue`)
- `VOXERA_HEALTH_PATH` (unset — override health snapshot file path; used in tests via `conftest.py`)
- `VOXERA_PANEL_HOST` (`127.0.0.1`)
- `VOXERA_PANEL_PORT` (`8844` runtime default; local-dev `make panel` intentionally binds `8787`)
- `VOXERA_PANEL_OPERATOR_USER` (`admin`)
- `VOXERA_PANEL_CSRF_ENABLED` (`1`/true by default)
- `VOXERA_QUEUE_LOCK_STALE_S` (`3600`)
- `VOXERA_BRAIN_BACKOFF_BASE_S` (`2`, brain backoff base for computed/applied wait)
- `VOXERA_BRAIN_BACKOFF_MAX_S` (`60`, brain backoff cap for computed/applied wait)
- `VOXERA_OPS_BUNDLE_DIR` (unset => timestamped `_archive/` path)
- When using `voxera ops bundle ... --queue-dir`, default archive output stays under that queue root (`<queue_dir>/_archive/<timestamp>/`); use `--dir` to override explicitly.


Daemon startup writes a fresh redacted `_ops/config_snapshot.json`, `_ops/config_snapshot.sha256`, and baseline `_ops/config_snapshot.last.sha256`. If the effective redacted config fingerprint changes since previous daemon start, Voxera emits exactly one structured audit event (`config_drift_detected`) and writes `config_drift_note.txt` with timestamp and old/new fingerprints. Secrets are never included in these files/events.


## Runtime capabilities snapshot for planning

Use the runtime catalog snapshot to inspect exactly what mission planning/execution may target:

```bash
voxera ops capabilities
```

This prints deterministic JSON (`schema_version`, `generated_ts_ms`, `missions`, `allowed_apps`, `skills`) sourced from the live mission catalog, skill registry manifests, and `system.open_app` allowlist.

Planner prompts include a compact `SYSTEM CONTEXT (Vera)` preamble plus a `CAPABILITIES` block from this snapshot so cloud brains are constrained to runtime-known mission IDs and enum-like arguments. Validation also runs before execution: unknown `mission_id` values and invalid `system.open_app` targets fail fast with closest-match suggestions.

Execution skills now emit canonical blocked-input payloads (not just raw error strings) when command/path/url/app boundaries are violated. Operators should inspect `skill_result.error_class`, `skill_result.summary`, and artifact paths from `execution_result.json` when triaging denied or malformed inputs.

When running `voxera missions plan --dry-run`, the output JSON includes `capabilities_snapshot`
(schema version + snapshot timestamp) and `capabilities_used` (sorted capability strings used by
planned steps) to support auditing and operator review of planned step permissions.

Use `--deterministic` for CI/golden outputs and audits: sets `generated_ts_ms=0` so two runs
produce byte-identical JSON. Use `--freeze-capabilities-snapshot` to assert that the capabilities
snapshot is not regenerated mid-run (already the default; the flag documents the guarantee).

Planner preamble override env vars (precedence: string > path > generated default):
- `VOXERA_PLANNER_PREAMBLE`
- `VOXERA_PLANNER_PREAMBLE_PATH`
- `VOXERA_PLANNER_AGENT_NAME` (used by generated default; default name is `Vera`)

To rename the assistant later, set `VOXERA_PLANNER_AGENT_NAME`.

Note: systemd user services do not inherit ad-hoc shell exports by default; configure env via Voxera runtime env files/service unit overrides if drift behavior seems unexpected.

## Onboarding + Demo

Use `voxera setup` for first-run app config (`~/.config/voxera/config.yml`) while keeping runtime ops configuration in `~/.config/voxera/config.json` (optional/operator-managed).

Operator-facing app config validation is strict by contract: unknown keys in `config.yml` fail fast (including nested `brain`, `policy`, and `privacy` entries) to catch typos before runtime. Internal/volatile payload models are intentionally not tightened repo-wide in this phase.

Provider auth choices are intentionally non-destructive:
- Keep current (default when already configured)
- Skip for now (continue with offline demo flows)
- Enter new/replace key (explicit only)

For cloud setup in v0.1.8, `voxera setup` walks the brain chain one slot at a time (`primary`, `fast`, `reasoning`, `fallback`) with explicit provider/model confirmation for each slot.

If OpenRouter is selected for a slot, setup uses the curated repo catalog (`src/voxera/data/openrouter_catalog.json`) and a vendor-first guided flow (`OpenAI`, `Google`, `Anthropic`, `Meta`, etc.) so onboarding stays manageable. The default recommended models are slot-specific: `primary=google/gemini-3-flash-preview`, `fast=google/gemini-3.1-flash-lite-preview`, `reasoning=anthropic/claude-3.5-sonnet`, `fallback=meta-llama/llama-3.3-70b-instruct`. Advanced users can still choose manual model-id entry when needed.

Policy framing: treat `primary` as the recommended minimum serious cloud brain floor (Gemini 3 Flash), `fast` as the lightweight/economy assistant lane (Gemini 3.1 Flash Lite, also the assistant default), and `reasoning` as the premium reasoning tier (Claude 3.5 Sonnet). This updates defaults/policy truth surfaces only; provider wiring and config contract stay unchanged.

After config save succeeds, setup now ensures the standard user-service runtime stack is started first (`voxera-daemon.service`, `voxera-panel.service`, `voxera-vera.service`) and reports per-service failures honestly. It then offers an explicit optional launch step: open Voxera panel, Vera panel, both, or none.

Maintainer refresh path for curated catalog metadata from live endpoint:

```bash
python scripts/refresh_openrouter_catalog.py
```

Run the guided checklist:

```bash
voxera demo
voxera demo --online
```

Then use operational hygiene commands:

```bash
voxera queue status
voxera queue reconcile
voxera queue reconcile --fix
voxera queue prune
voxera artifacts prune
voxera doctor --quick
voxera doctor --self-test
```
## Queue contract + intake flow

Use `voxera inbox` as the human-friendly front door for queued goals. Ensure queue folders exist once per machine:

```bash
voxera queue init
```

Queue directory contract (`~/VoxeraOS/notes/queue`):
- `inbox/` (**only intake**, daemon consumes `inbox/*.json`)
- `pending/`
- `pending/approvals/`
- `done/`
- `failed/`
- `canceled/` (operator-canceled jobs; move to `inbox/` via `voxera queue retry`)
- `artifacts/`
- `_archive/`
- `recovery/` (created by daemon startup recovery; quarantines orphan approvals + state files — never deleted)
- `quarantine/` (created by `voxera queue reconcile --fix --yes`; quarantines orphan sidecars — never deleted)

Backwards-compatible safety behavior:
- Legacy drops in `notes/queue/*.json` are auto-relocated to `inbox/` with audit event `queue_job_autorelocate`.
- Mis-dropped primary jobs in `notes/queue/pending/*.json` are auto-relocated to `inbox/` (no silent stuck jobs).

Queue producer best practice (atomic write + rename):
```bash
queue_dir=~/VoxeraOS/notes/queue
inbox_dir="$queue_dir/inbox"
mkdir -p "$inbox_dir"
job_id=job-$(date +%s)
tmp_path="$inbox_dir/.${job_id}.tmp"
final_path="$inbox_dir/${job_id}.json"
printf '{"goal":"run a quick system check"}
' > "$tmp_path"
mv "$tmp_path" "$final_path"
```

Operational commands:
```bash
voxera inbox add "Write a daily check-in note with priorities and blockers"
voxera queue files find --root-path ~/VoxeraOS/notes/runtime-validation --glob "*.md"
voxera queue files grep --root-path ~/VoxeraOS/notes --pattern "TODO"
voxera queue files tree --root-path ~/VoxeraOS/notes/runtime-validation --max-depth 3
voxera queue files copy --source-path ~/VoxeraOS/notes/runtime-validation/a.txt --destination-path ~/VoxeraOS/notes/runtime-validation/b.txt
voxera queue files move --source-path ~/VoxeraOS/notes/runtime-validation/b.txt --destination-path ~/VoxeraOS/notes/runtime-validation/c.txt
voxera queue files rename --path ~/VoxeraOS/notes/runtime-validation/c.txt --new-name archived.txt
voxera daemon --once
voxera queue status
voxera queue cancel <job_id_or_filename>
voxera queue retry <job_id_or_filename>
voxera queue pause
voxera queue resume
voxera queue unlock           # safe: stale/orphaned (dead pid) locks only
voxera queue unlock --force   # override live lock (dangerous)
voxera queue health           # summary from notes/queue/health.json (Current State / Recent History / Historical Counters)
voxera queue health --watch   # refresh every 2s (override with --interval)
voxera queue health --json    # same snapshot with section parity fields
voxera queue health-reset --scope current_and_recent   # reset current state + recent history (default safe reset)
voxera queue health-reset --scope current_state          # reset current daemon/degradation state only
voxera queue health-reset --scope recent_history         # reset last error/fallback/shutdown context only
voxera queue lock status      # lock table alias (same lock fields as queue health)
```

`voxera queue files ...` commands are queue-producing helpers, not direct execution:
- they enqueue one-step governed jobs into `inbox/`,
- daemon execution still owns real filesystem effects,
- outcome truth still comes from queue bucket + artifacts (`execution_result.json`, `step_results.json`).

Panel home (`/`) now includes a collapsible **Daemon Health** widget sourced only from `notes/queue/health.json` (no daemon RPC calls), so it is safe/usable even when running panel-only deployments. See [Panel Daemon Health widget](#panel-daemon-health-widget) for field reference.
Reset semantics: default resets clear only **Current State** and/or **Recent History** fields, while **Historical Counters** are preserved unless an explicit counter-group reset is requested. Use resets to clear operator noise after remediation; use investigation + audit trails for active incidents.

The panel `/hygiene` page is admin-protected and now contains a dedicated **Health Reset** tab for these actions, including an advanced section for optional historical counter resets.

Operational effects:
- `queue cancel` moves matching active jobs (`inbox/`, `pending/`, pending approvals/in-flight best effort) into `canceled/` and cleans pending approval markers.
- `queue retry` re-queues a `failed/` or `canceled/` primary payload into `inbox/`, archiving prior failed sidecars when present, and emits `queue_job_retry`.
- `queue pause` creates `.paused`; daemon still reports status but skips processing new jobs until `queue resume` removes marker.
- Daemon run loop acquires `notes/queue/.daemon.lock` with an OS-level exclusive file lock (`flock`) to enforce single-writer processing. If another live daemon holds the lock, startup records `lock_state=locked_by_other`, logs contention, and exits non-zero.
- On `SIGTERM`/`SIGINT`, daemon sets shutdown state immediately, stops intake of new inbox jobs, and handles any in-flight job deterministically as `failed/` with error reason `shutdown: daemon shutdown requested` (plus error sidecar payload). Health snapshot records `last_shutdown_outcome`, `last_shutdown_ts` (epoch seconds), `last_shutdown_reason`, and `last_shutdown_job` (always-present keys with null defaults).
- Checked-in systemd daemon unit templates set `TimeoutStopSec=10` to match the daemon stop-path contract (prompt shutdown intent, deterministic in-flight failure handling, and clean idle exit). Validate a rendered runtime unit with `systemctl --user show voxera-daemon.service -p TimeoutStopUSec`.
- On startup, daemon runs deterministic recovery before intake:
  - **Policy: fail-fast**. Any pending job with in-flight markers (`pending/<job>.pending.json` or `pending/<job>.state.json`) is moved to `failed/` with a structured sidecar payload:
    - `reason="recovered_after_restart"`
    - `message="daemon recovered from unclean shutdown; job marked failed deterministically"`
    - includes `original_bucket`, `detected_state_files`, and best-effort `detected_artifacts_paths`.
  - Orphan approvals (`pending/approvals/*.approval.json` without matching `pending/<job>.json`) are quarantined (never deleted) under `recovery/startup-<ts>/pending/approvals/`.
  - Orphan state files (`*.state.json` referencing missing jobs) are quarantined (never deleted) under `recovery/startup-<ts>/...`.
  - Recovery emits audit event `daemon_startup_recovery`, increments counters (`startup_recovery_runs`, `startup_recovery_jobs_failed`, `startup_recovery_orphans_quarantined`), and updates health fields (`last_startup_recovery_ts`, `last_startup_recovery_counts`, `last_startup_recovery_summary`).
- Use `voxera queue unlock` for safe stale/orphaned lock recovery; if lock is live, stop daemon first or use `voxera queue unlock --force` as an explicit override.

Panel operator notes:
- Panel shows a **Setup required** banner on `/` and `/jobs` when `VOXERA_PANEL_OPERATOR_PASSWORD` is unset; guidance includes systemd user env + restart commands.
- Panel mutation routes (`/queue/create`, `/missions/create`, `/missions/templates/create`) accept `POST` by default.
- `/missions/create` is the operator Create Mission intake (Easy mode: prompt-only) and writes deterministic jobs to `notes/queue/inbox/job-panel-mission-<slug>-<ts>.json`.
- Panel operator mutations now require HTTP Basic auth and CSRF validation. Set `VOXERA_PANEL_OPERATOR_PASSWORD` (and optional `VOXERA_PANEL_OPERATOR_USER`, default `admin`) before starting the panel.
- Shared panel wiring/security/view-model helpers still live in `src/voxera/panel/app.py`, while route-family ownership now lives in the extracted `src/voxera/panel/routes_*.py` modules:
  - `routes_home.py` — home/dashboard + queue create
  - `routes_jobs.py` — approvals, job list/detail/progress, cancel/retry/delete
  - `routes_queue_control.py` — pause/resume/delete queue controls
  - `routes_assistant.py` — operator advisory assistant UI and degraded-mode handling
  - `routes_missions.py` — mission creation surfaces
  - `routes_hygiene.py` — hygiene actions and health reset UI
  - `routes_recovery.py` — recovery browsing/downloads
  - `routes_bundle.py` — job/system bundle downloads
- Panel `/assistant` (Operator Assistant / “Ask Voxera”) is a read-only advisory surface that now uses a queue-backed transport lane: submit creates a dedicated `assistant_question` inbox job, daemon processes it through a constrained advisory path, and panel polls/render status (`queued` / `thinking through Voxera` / `answered` / `failed`). Follow-up prompts stay in the same thread, and bounded recent turns are reused with fresh runtime context. The advisory lane assembles current queue counts, health semantic sections, pending approvals, recent failed jobs, and recent queue/mission/panel audit events and uses configured brain/provider with deterministic dual-brain sequencing (`primary` then `fallback`) to answer in grounded first-person control-plane voice. Fallback is attempted only for explicit retryable classes (timeout/auth/rate-limit/malformed/network), and assistant artifacts/panel status include compact metadata (`provider`, `model`, `fallback_used`, `fallback_reason`, `advisory_mode`, `degraded_reason`). If both advisory attempts fail, the job lands in `failed/` with a clear assistant failure artifact. If queue transport is unavailable at submit time, `/assistant` transparently labels direct recovery responses as `advisory_mode=degraded_brain_only` with `degraded_reason=queue_unavailable`. It does **not** execute tools, mutate queue state, approve actions, or bypass controls.

Fast-lane behavior (read-only advisory only):
- Fast lane is inside the control plane, not a bypass. It still writes canonical artifacts, action events, and audit logs.
- Eligibility is deterministic and conservative: only `assistant_question` requests (detected from canonical request kind, including `job_intent.request_kind`) with explicit `advisory=true`, `read_only=true`, `action_hints=["assistant.advisory"]`, and without mission/goal/steps/approval-required flags.
- Any uncertainty or non-eligible shape fails closed to normal queue lane.
- Operator evidence: assistant jobs emit `execution_envelope.json`, `execution_result.json`, and `assistant_response.json` with aligned lane metadata. `execution_result.json` includes `execution_lane` (`fast_read_only` or `queue`) plus `fast_lane` metadata (`used`, `eligible`, `eligibility_reason`, `request_kind`), and envelope lane fields live at `execution.lane` / `execution.fast_lane`.
- Optional GET mutation compatibility is disabled by default (HTTP 405) and can be enabled for test/dev only with `VOXERA_PANEL_ENABLE_GET_MUTATIONS=1`.
- Panel home shows pause/resume + lifecycle actions (approve/deny, cancel, retry, delete) and links Done/Failed/Canceled jobs to artifact-backed detail pages.
- Job detail pages now include operator-focused **Policy Rationale** and **Evidence Summary** sections sourced from canonical execution/approval/state artifacts, plus explicit next-action source labeling (`execution.next_action_hint` vs panel default guidance).

### Simple-intent routing (goal-kind jobs)

Goal-kind queue jobs (those with a free-form `goal` string) pass through a small deterministic
intent classifier (`src/voxera/core/simple_intent.py`) before planning:

- Obvious operator verbs (`write`, `read the file ~/...`, `open terminal`, `what is ... status?`,
  etc.) are classified into explicit intent classes: `assistant_question`, `open_terminal`, `open_url`, `open_app`,
  `write_file`, `read_file`, `run_command`, or `unknown_or_ambiguous`.
- For recognised deterministic intents the classifier emits the set of allowed first-step skill
  IDs **and** an `extracted_target` when a file path can be parsed directly from the goal.
- **Direct routing for high-confidence intents**: if `extracted_target` is set and within the
  allowed notes root, `mission_planner` skips the cloud brain and constructs the governed
  first-step plan deterministically (`files.read_text` for reads, `files.write_text` for named
  file creates).  This eliminates a class of cloud-brain drift failures.
- **Mismatch = fail closed**: if the planner produces a first step outside the allowed family,
  the job fails immediately before any skill executes.  The operator sees:
  - `evaluation_reason: simple_intent_skill_family_mismatch`
  - `stop_reason: planner_intent_route_rejected`
  - `intent_route` dict in `execution_result.json` and `plan.json` with the full evidence
    (intent kind, allowed IDs, planned skill ID, routing reason).
- **Artifact consistency**: `intent_route` is now present in `execution_result.json` for **all**
  goal-kind jobs (not just mismatches), matching the `simple_intent` field in
  `execution_envelope.json`.
- `unknown_or_ambiguous` intents have no constraint and fall through to normal planning.
- The layer is conservative: only classifies when unambiguously matching.  Multi-step or vague
  goals (e.g. "handle this for me", "open an app and report status") fall through.
- Action events: `queue_simple_intent_routed` (always, for goal-kind) and
  `queue_simple_intent_mismatch` (when mismatch detected).
- Does **not** bypass approvals, capability gates, policy, or any existing trust control.
- **Sub-route notes** (v1.4 refinements):
  - `"open terminal"` and narrow variants (`open the terminal`, `launch terminal`, etc.) map to `open_terminal` and only allow `system.open_app` as deterministic first-step family.
  - `open_url` requires an explicit open/launch verb tied to a URL; URL presence alone is insufficient.
  - `open_app` is restricted to explicit narrow app-open phrasing; vague asks ("open an app", "open something") remain ambiguous.
  - Compound actionable requests preserve deterministic first-step routing metadata rather than being discarded when they contain `and`/`then`.
  - Meta/help/explanatory phrasing (how/why/debug/discussion/quoted phrase) does not trigger deterministic action execution.
  - `write_file` goals: match `write …`, `create file …`, **and** `create a/an/new/empty file …`
    (articles and adjectives before "file" are accepted).  All require `files.write_text` as
    the first step.  `system.terminal_run_once` is **explicitly rejected**.
    Goals of the form `"write/create a file called <name>"` (no content specified) are routed
    deterministically to `files.write_text` with empty text under the notes root.
  - `read_file` goals: accepted with or without article/noun prefixes before the path:
    `"read ~/path"`, `"read the ~/path"`, `"read the file ~/path"`, `"read file ~/path"`,
    `"open and read ~/path"`, `"show contents of ~/path"`, `"cat ~/path"`, etc.
    All require a `~/` or `/` path immediately after the verb; goals without an explicit path
    (e.g. `"read this and copy it"`) fall through to `unknown_or_ambiguous`.
    Only `files.read_text` is accepted as the first step.  `clipboard.copy` is **explicitly
    rejected** even when produced by the planner safety rewrite.
    When the path is within the allowed notes root, the planner routes deterministically to
    `files.read_text` without calling the cloud brain.
  - **Panel and CLI paths are identical**: intent classification runs on the normalized payload
    for all goal-kind jobs regardless of origin (panel, CLI, or direct inbox write).  There is
    no bypass for panel-created payloads.
### Create Mission (panel) quick runbook

- Open panel home (`/`) and locate **Create Mission**.
- Fill **Prompt / Goal** (required), keep **Approval required** toggle on by default, then submit.
- Successful submit redirects to `/` with a success banner containing the created filename / mission id.
- Validation failure (empty prompt) redirects to `/` with a clear error banner.
- Queue flow remains standard: `inbox/` → `pending/approvals/` (when policy requires) → terminal bucket.
- Each job now maintains a deterministic `*.state.json` sidecar (same stem as job file) with explicit lifecycle and step-progress truth:
  - `lifecycle_state`: `queued`, `planning`, `running`, `awaiting_approval`, `resumed`, `done`, `step_failed`, `blocked`, `canceled`
  - `current_step_index`, `total_steps`, `last_completed_step`, `last_attempted_step`
  - `terminal_outcome` (`succeeded|failed|blocked|denied|canceled`) when terminal
  - `failure_summary`, `blocked_reason`, `approval_status` when applicable
  - transition timestamps under `transitions`
- `approval_required=true` is a **hard gate**: daemon always blocks in `pending/approvals/` before any planning or execution, even for safe/no-op missions.
- Runtime capability enforcement is a second **hard gate** at step dispatch (`SkillRunner.run`) before any skill entrypoint invocation:
  - `allow`: step executes.
  - `ask`: step writes canonical pending approval artifacts and does **not** execute.
  - `deny`: step is blocked and job fails with `terminal_outcome=blocked`.
  - missing/malformed/ambiguous/unknown capability metadata: fail-closed block before execution (treated as blocked, never best-effort).
- Blocked and approval-required outcomes are surfaced through canonical artifacts, not side channels:
  - `artifacts/<job>/step_results.json` includes `status`, `machine_payload.required_capabilities`, `machine_payload.required_effect_classes`, and structured `error_class`.
  - `artifacts/<job>/execution_result.json` reflects terminal outcome (`blocked` or `awaiting_approval`) with step-level context.
- Panel approval/deny actions resolve queue approvals in a worker thread to avoid event-loop conflicts (`asyncio.run()` cannot execute inside the active FastAPI loop).
- Artifacts and bundles are available from the Jobs console (`/jobs`, `/jobs/<job>.json`, `/jobs/<job>.json/bundle`).

- Queue daemon + panel update a shared lightweight snapshot at `notes/queue/health.json`.
  - Write pattern is atomic (`health.json.tmp` then rename).
  - `last_ok_event` + `last_ok_ts_ms` indicate recent successful activity (tick/lock/shutdown release).
  - `last_error` + `last_error_ts_ms` capture latest concise failure context.
  - Last shutdown keys are always present: `last_shutdown_outcome` (`clean`/`failed_shutdown`/`startup_recovered`), `last_shutdown_ts` (epoch seconds), `last_shutdown_reason`, `last_shutdown_job`.
  - Quick inspect: `cat notes/queue/health.json | jq '{last_shutdown_outcome,last_shutdown_ts,last_shutdown_reason,last_shutdown_job}'`.
  - Concurrent writes use read-modify-write and may rarely lose an increment under heavy contention; counters are still suitable for operational trend visibility.

### Incident runbook: daemon lock + panel auth/CSRF

1. **Daemon will not start (`QueueLockError`)**
   ```bash
   voxera queue status
   voxera audit | rg "queue_daemon_lock_"
   ```
   - Check `lock_acquire_fail` and `lock_reclaimed` counters for contention/recovery patterns.
   - Confirm whether another daemon process is active before intervention.

2. **Lock appears stuck**
   ```bash
   voxera queue unlock
   ```
   - Safe mode only removes stale/dead PID locks.
   - If lock is held by a live PID, stop that daemon first.
   - Emergency only:
     ```bash
     voxera queue unlock --force
     ```

3. **Panel mutations failing with 401/403**
   - Ensure `VOXERA_PANEL_OPERATOR_PASSWORD` is set in the running panel environment.
   - For 401, verify Basic auth user/password.
   - For 403, verify CSRF cookie + token are both present and matching.
   ```bash
   voxera audit | rg "panel_(auth_missing|auth_invalid|csrf_missing|csrf_invalid|mutation_allowed|operator_config_error)"
   voxera queue health
   voxera doctor --quick   # marks last_error as stale when last_ok is newer by >5m
   ```
   - Check `panel_401_count`, `panel_403_count`, `panel_auth_invalid`, `panel_csrf_missing`, and `panel_csrf_invalid` trends.
   - Quick curl sanity test (missing CSRF should return 403):
   ```bash
   curl -i -u operator:"$VOXERA_PANEL_OPERATOR_PASSWORD" -X POST http://127.0.0.1:8844/queue/create -d "kind=goal&goal=test"
   ```

**Where to find artifacts/logs quickly**
- Lock file: `~/VoxeraOS/notes/queue/.daemon.lock`
- Health snapshot: `~/VoxeraOS/notes/queue/health.json`
- Queue artifacts root: `~/VoxeraOS/notes/queue/artifacts/`
- Audit log stream (systemd): `journalctl --user -u voxera-daemon.service -u voxera-panel.service -f`

## Panel job actions

- **Cancel**: moves active jobs (`inbox/`, `pending/`) to `canceled/` and removes pending/approval sidecars.
- **Retry**: allowed from `failed/` and `canceled/`; moves job payload back to `inbox/` and archives old failure sidecars.
- **Delete**: guarded terminal cleanup for `done/`, `failed/`, `canceled/` only; requires Basic auth + CSRF + exact `confirm` filename.
- Artifacts: `~/VoxeraOS/notes/queue/artifacts/<job_stem>/`; bundles via `/jobs/<job>.json/bundle` or CLI `voxera ops bundle job <job_id>`.

## Artifact bundle contract

Artifacts live under `~/VoxeraOS/notes/queue/artifacts/<job_stem>/` (path resolves against `VOXERA_QUEUE_ROOT`).

**Minimum required artifacts** (expected for every queue execution):
- `execution_envelope.json` — full execution context snapshot at job-start (schema_version=1)
- `execution_result.json` — terminal outcome, evidence summary, artifact families, intent route (schema_version=1)
- `step_results.json` — per-step canonical skill results with status, error_class, approval_status (schema_version=1)
- `job_intent.json` — parsed user intent and request-kind metadata
- `plan.json` — generated/normalized mission plan snapshot
- `actions.jsonl` — append-only lifecycle event stream (rendered newest-first in panel detail)

**Conditional artifacts** (present when applicable):
- `review_summary.json` — reviewer-facing quality/artifact assessment when materialized
- `evidence_bundle.json` — structured execution-to-evidence link bundle when materialized
- `stdout.txt` / `stderr.txt` — aggregated step output/error streams (present when skills produce them)
- `approval` artifacts under `pending/approvals/<job_stem>.approval.json` during approval pause
- `failed/<job_stem>.error.json` sidecar for failed jobs (see section below)

Interpretation quick-guide:
- `execution_result.json` is the primary terminal-outcome truth surface: check `terminal_outcome`, `blocked`, `blocked_reason_class`, and `artifact_families`.
- `step_results.json` gives per-step evidence: `status`, `error_class`, `blocked_reason_class`, `approval_status`.
- `execution_envelope.json` records the full execution context at start: job lineage, request kind, capabilities, plan steps.
- `plan.json` confirms what mission/steps were planned (or attempted before failure).
- `actions.jsonl` is the lifecycle audit stream for queue transitions and skill events.
- `stdout.txt`/`stderr.txt` provide debugging context for skills that write to those streams.

See [`docs/QUEUE_CONSTITUTION.md`](QUEUE_CONSTITUTION.md) for the canonical artifact minimum-guarantee contract and result interpretation rules.

## Failed artifact sidecar contract + retention

Every failed primary job (`failed/*.json`) may have an optional sidecar at
`failed/<job_stem>.error.json` with this schema contract:

- Required fields:
  - `schema_version` (currently `1`)
  - `job` (exact failed filename, e.g. `job-123.json`)
  - `error` (string summary)
  - `timestamp_ms` (Unix epoch **milliseconds**)
- Optional:
  - `payload` (object)

The daemon validates this schema on both write and read. Invalid sidecars are ignored for
status summaries and emit `queue_failed_sidecar_invalid` audit events.

Operator-facing status surfaces expose sidecar-health counters:
- `failed metadata valid`
- `failed metadata invalid`
- `failed metadata missing`

When `failed metadata invalid` is non-zero, inspect `failed/*.error.json` and triage
matching `queue_failed_sidecar_invalid` audit events before retrying jobs.

Schema evolution policy:
- Writer is pinned to a single current version (`1`) to keep emitted artifacts deterministic.
- Reader accepts an explicit allowlist of supported versions (currently `[1]`).
- Unknown future versions are rejected deterministically and surfaced via
  `queue_failed_sidecar_invalid` so operators can detect mixed-version artifacts quickly.
- When introducing a new schema version, update both writer pin and reader allowlist
  intentionally, and document migration/compatibility expectations in release notes.

Retention pruning treats a failed primary job plus `.error.json` as one logical unit:

- Pairing key is the shared stem (`x.json` + `x.error.json`).
- Orphans are still deterministic units:
  - job without sidecar = one unit
  - sidecar without job = one unit
- Newness for pruning uses the newest mtime of either file in the unit.
- Policy keeps newest failures and removes older units:
  - `VOXERA_QUEUE_FAILED_MAX_AGE_S` (optional max age in seconds)
  - `VOXERA_QUEUE_FAILED_MAX_COUNT` (optional max logical units)

When both are set, max-age is applied first, then max-count keeps the newest among survivors.

Troubleshooting notes:
- ASK-policy failures denied from `voxera queue approvals deny ...` write a compliant sidecar
  with `error="Denied in approval inbox"` and payload context when available.
- If you find orphan sidecars/jobs, they will still be listed/pruned predictably by stem unit,
  so cleanup can safely rely on the daemon retention policy.

Mixed-version incident runbook (`queue_failed_sidecar_invalid`):
1. Detect and confirm in audit stream:
   ```bash
   voxera audit | rg "queue_failed_sidecar_invalid"
   ```
2. Scope impacted artifacts by sidecar schema version under `failed/`:
   ```bash
   python - <<'PY'
   import json
   from collections import Counter
   from pathlib import Path

   failed_dir = Path.home() / "VoxeraOS/notes/queue/failed"
   counts = Counter()
   for sidecar in failed_dir.glob("*.error.json"):
       try:
           payload = json.loads(sidecar.read_text(encoding="utf-8"))
       except Exception:
           counts["unparseable"] += 1
           continue
       counts[str(payload.get("schema_version", "missing"))] += 1
   for version, count in sorted(counts.items()):
       print(f"schema_version={version}: {count}")
   PY
   ```
3. Remediate safely:
   - Align service versions first (ensure daemon build and operators are on the same release line).
   - Quarantine incompatible sidecars by moving them out of `failed/` before retries if they came from a newer build.
   - Reprocess by recreating the failed primary job only after version compatibility is restored.
4. Release hygiene for schema changes:
   - Any schema bump must update writer pin + reader allowlist together.
   - Include migration/compatibility notes in release notes and echo the change in `README.md`, `docs/ops.md`, and `docs/CODEX_MEMORY.md`.


## Merge-readiness gate

Use the unified merge gate from repository root:

```bash
make merge-readiness-check
```

This combines fast quality checks (format/lint/type) and release consistency checks (version/doc/runtime alignment) under one workflow status check: `merge-readiness / merge-readiness`.

Validation tiers (post-refactor hardening pass):
- Golden operator-surface gate: `make golden-check` (committed `tests/golden/` help/JSON baselines).
- Focused adversarial gate: `make security-check` (intent hijack/planner mismatch/path escape/approval-state/progress-evidence fail-closed regressions).
- Canonical merge-confidence gate: `make validation-check` (format/lint/type + `make golden-check` + `make security-check` + critical queue/CLI/doctor contract suites).
- Required CI merge gate remains: `make merge-readiness-check` (now composes `make security-check`).
- Broader local/release validation: `make full-validation-check` (`make premerge` alias), which includes `validation-check`, merge-readiness, failed-sidecar guardrails, full pytest, and Golden4 E2E.
- Golden baseline refresh target: `make golden-update` (use only for intentional reviewed contract changes).

Typing policy:
- `make type-check` uses a mypy ratchet against `tools/mypy-baseline.txt` and blocks new type errors.
- `make type-check-strict` runs full mypy for baseline burn-down work.
- `make update-mypy-baseline` should only be used for intentional baseline refreshes after triage.

Baseline governance:
- Do not refresh the baseline as the first response to a ratchet failure; fix new errors first.
- Any baseline refresh should include a clear PR rationale (what debt was triaged and why update is intentional).
- `tools/mypy-baseline.txt` and `scripts/mypy_ratchet.py` are review-sensitive and should receive maintainer approval.

Local workflow parity:
- `make dev` installs pre-commit + pre-push hooks.
- Pre-push runs `make merge-readiness-check` so local gates match CI expectations.

CI diagnostics:
- On merge-readiness failures, GitHub Actions uploads `merge-readiness-logs` artifacts (quality/release logs).
- Workflow step summary will call out whether quality or release phase failed and where to find the logs.
- Download artifacts from the failed workflow run for quick triage without rerunning locally.


Operator-facing contracts explicitly guarded by tests/snapshots/goldens include:
- queue health JSON + reset semantics (`queue health --json`, `queue health-reset --json`),
- queue daemon recovery/approval transition snapshots,
- CLI command/help surface snapshots (including compatibility exports from `voxera.cli`),
- deterministic golden fixtures for high-value CLI help surfaces and normalized queue health JSON (`tests/golden/`, enforced by `make golden-check`),
- assistant advisory artifact schema (`assistant_response.json`),
- ops bundle manifest + config snapshot schema contracts.

Troubleshooting:
- If `ruff` fails, run `make fmt` then rerun `make merge-readiness-check`.
- If mypy ratchet fails with new errors, fix the reported lines or explicitly triage + refresh baseline with `make update-mypy-baseline`.
- If release-check fails, ensure docs and runtime versioned surfaces remain aligned.

## Queue failed-artifact + approval triage runbook

1) Inspect queue state and retention context:

```bash
voxera queue status
```

Interpretation guide:
- `failed/` counts primary failed jobs only.
- `failed metadata invalid` indicates malformed or schema-incompatible `failed/*.error.json` sidecars (audit logs emit `queue_failed_sidecar_invalid`).
- `failed retention max age (s)` / `failed retention max count` show active retention controls from environment.
- `Failed Retention (latest prune event)` summarizes the newest prune pass (`removed jobs`, `removed sidecars`).

2) Inspect pending approvals:

```bash
voxera queue approvals list
```

3) Resume or deny by job reference:

```bash
voxera queue approvals approve <job_id_or_filename>
voxera queue approvals approve <job_id_or_filename> --always
voxera queue approvals deny <job_id_or_filename>
```

4) If invalid sidecars rise, inspect and repair/quarantine malformed artifacts before retrying jobs:

```bash
ls ~/VoxeraOS/notes/queue/failed/*.error.json
```

5) Retention control knobs (set before daemon/panel start):
- `VOXERA_QUEUE_FAILED_MAX_AGE_S`
- `VOXERA_QUEUE_FAILED_MAX_COUNT`

## Operator hygiene (quick reference)

Keep the system clean with three routine commands. All are **dry-run by default** — no
deletions happen without `--yes`. Safe to run at any time, including while the daemon is
running.

| Command | What it does |
|---|---|
| `voxera artifacts prune` | Delete stale artifact directories under `notes/queue/artifacts/`. |
| `voxera queue prune` | Remove stale job files from terminal buckets (`done/`, `failed/`, `canceled/`). |
| `voxera queue reconcile` | Scan for orphans and duplicates; report-only by default. Add `--fix` to preview quarantine actions; add `--fix --yes` to apply. |

Retention rules can be persisted in `~/.config/voxera/config.json`:

```json
{
  "artifacts_retention_days": 30,
  "artifacts_retention_max_count": 100,
  "queue_prune_max_age_days": 30,
  "queue_prune_max_count": 500
}
```

CLI flags always override config values. If neither flags nor config is set, each command
prints `"no pruning rules configured"` and exits 0. See the sections below for full flag
references and env-var overrides.


## Queue prune

Remove stale jobs from terminal buckets (`done/`, `failed/`, `canceled/`).
`inbox/` and `pending/` are **never** touched.

```bash
# dry-run preview (safe default — nothing is deleted)
voxera queue prune --max-age-days 14

# execute deletion
voxera queue prune --max-age-days 14 --yes

# keep only the newest 200 jobs per bucket
voxera queue prune --max-count 200 --yes

# combine both rules (union: prune if either rule matches)
voxera queue prune --max-age-days 30 --max-count 500 --yes

# machine-readable summary
voxera queue prune --max-age-days 14 --json
```

Rules can also be persisted in `~/.config/voxera/config.json` so operators
don't need to repeat flags:

```json
{
  "queue_prune_max_age_days": 30,
  "queue_prune_max_count": 500
}
```

CLI flags take precedence over config values.  If neither flags nor config is
set, the command exits 0 with a "no rules configured" message.

When pruning a job, matching sidecars in the same bucket are also removed
(e.g. `job-XYZ.error.json`, `job-XYZ.state.json`).

Env vars (override config file):
- `VOXERA_QUEUE_PRUNE_MAX_AGE_DAYS`
- `VOXERA_QUEUE_PRUNE_MAX_COUNT`

## Queue reconcile

`voxera queue reconcile` is a queue hygiene diagnostic that scans the queue
directory and reports issues.  **Default behavior is report-only — no changes
are made.**  Safe to run at any time, even while the daemon is running.

Detects four categories of issues:

1. **Orphan sidecars** — `.error.json` / `.state.json` in `done/`, `failed/`,
   or `canceled/` whose primary `job-XYZ.json` is missing from the same bucket.
2. **Orphan approvals** — files in `pending/approvals/` with no corresponding
   `pending/job-*.json`.
3. **Orphan artifact candidates** — direct children of `artifacts/` with no
   matching job file across any bucket (reported conservatively as candidates).
4. **Duplicate job filenames** — `job-*.json` appearing in more than one bucket
   (`inbox/`, `pending/`, `done/`, `failed/`, `canceled/`).

Missing directories are treated as 0 issues — no error is raised.

```bash
# Human-readable summary (report-only)
voxera queue reconcile

# Override queue root
voxera queue reconcile --queue-dir /path/to/queue

# Machine-readable JSON (stable schema)
voxera queue reconcile --json

# Pretty-print JSON
voxera queue reconcile --json | python -m json.tool
```

### Fix mode (quarantine-first)

`--fix` enables quarantine fix mode for the two safest orphan categories:
orphan sidecars in terminal buckets and orphan approvals.  Artifact candidates
and duplicates remain report-only (too ambiguous for auto-fix).

**Without `--yes`**, fix mode is a **dry-run preview** — prints what *would* be
quarantined and exits 0 with no filesystem changes.

**With `--yes`**, orphan files are *moved* (not deleted) into a quarantine
directory under the queue root, preserving relative paths.  No data is ever
deleted; quarantined files can be restored manually.

```bash
# Preview what would be quarantined (dry-run; no changes)
voxera queue reconcile --fix

# Apply quarantine (moves orphan sidecars + approvals)
voxera queue reconcile --fix --yes
```

The quarantine directory defaults to:

```
<queue-dir>/quarantine/reconcile-YYYYMMDD-HHMMSS/
```

Use `--quarantine-dir` to override (must remain within `--queue-dir`):

```bash
voxera queue reconcile --fix --yes --quarantine-dir /path/to/queue/my-quarantine
```

The JSON output schema is stable and extended with fix-mode fields:

```json
{
  "status": "ok",
  "queue_dir": "/path/to/queue",
  "mode": "report | fix_preview | fix_applied",
  "quarantine_dir": null,
  "issue_counts": {
    "orphan_sidecars": 0,
    "orphan_approvals": 0,
    "orphan_artifacts_candidate": 0,
    "duplicate_jobs": 0
  },
  "examples": {
    "orphan_sidecars": [],
    "orphan_approvals": [],
    "orphan_artifacts_candidate": [],
    "duplicate_jobs": []
  },
  "fix_counts": {
    "orphan_sidecars_quarantined": 0,
    "orphan_sidecars_would_quarantine": 0,
    "orphan_approvals_quarantined": 0,
    "orphan_approvals_would_quarantine": 0
  },
  "quarantined_paths": []
}
```

Human output includes up to 10 example paths per issue type.  In report-only
mode it always ends with: **"Report-only; no changes made."**  In fix-applied
mode it ends with: **"No deletions performed; quarantined files can be restored
manually."**

## Contributor guidance: where code belongs

The codebase follows a **thin composition root + focused domain modules** pattern across all three main subsystems. When adding new code:

**Queue subsystem** (`src/voxera/core/`):
- New mission execution or inbox-processing logic → `queue_execution.py` (owns `QueueExecutionMixin`)
- New approval workflow logic → `queue_approvals.py` (owns `QueueApprovalMixin`)
- New startup recovery or shutdown handling → `queue_recovery.py` (owns `QueueRecoveryMixin`)
- New assistant/advisory lane logic → `queue_assistant.py` (module-level functions)
- New state sidecar fields/helpers → `queue_state.py`
- New bucket move or path helpers → `queue_paths.py`
- `queue_daemon.py` owns: lock/watch/tick orchestration, high-level lane routing, and composition of the mixins above. Do not re-grow it with domain logic.

**Panel** (`src/voxera/panel/`):
- New route domains should live in a focused `routes_<domain>.py` module
- Register the new module in `panel/app.py` via `register_<domain>_routes(app, ...)` — keep `app.py` as the composition root
- Shared request/value parsing helpers go in `helpers.py`

**CLI** (`src/voxera/`):
- New queue/operator command families → `cli_queue.py`
- New diagnostic command logic → `cli_doctor.py` (expose via `register(app)`)
- Runtime config command logic → `cli_config.py`; skills/missions/run → `cli_skills_missions.py`
- Ops command logic → `cli_ops.py`; setup/demo/status/audit/panel/daemon → `cli_runtime.py`
- Shared option definitions and parsing helpers → `cli_common.py`
- `cli.py` owns Typer app creation, sub-app registration, and compatibility surfaces — keep it as the composition root

## Information sources (keep in sync)

For current project state and handoff context, keep these files aligned whenever queue/planner behavior changes:
- `README.md` (operator-facing feature and workflow docs)
- `docs/ROADMAP.md` (current baseline + 4/8/12-week user-visible milestones + delivery enablers)
- `docs/CODEX_MEMORY.md` (chronological merged-change memory)
- `AGENT.md` and `CODEX.md` (root-level quick memory pointers)

Documentation + audit hygiene checklist (run for every merged behavior/process change):
1. Update `README.md` for user/operator workflow changes.
2. Update `docs/ROADMAP.md` so completed and upcoming work reflect current state.
3. Append a merged-entry to `docs/CODEX_MEMORY.md` with summary, validation, follow-ups, and risks.
4. Confirm CI/ops guidance in `docs/ops.md` still matches current `Makefile`/workflow behavior.
5. If checks or runbooks changed, verify audit event names and triage steps remain accurate in docs.

## DEV auto-approve warning

`voxera daemon --auto-approve-ask` is **DEV-only** and requires `VOXERA_DEV_MODE=1`.
Without that env var, no ASK actions are auto-approved.

Even in DEV mode, auto-approval is restricted to `system.settings` capability only.
Network asks (for example `system.open_url`) still go to pending approval inbox.

## Update cadence

Recommended cadence: update frequently (daily/weekly) on active systems.

```bash
# from repository root
make update
```

`make update` runs the best-practice updater (`scripts/update.sh --smoke`), which:
- pulls latest `main`
- ensures `.venv` exists and reinstalls editable dependencies
- runs `compileall` + `pytest`
- runs E2E dry-run smoke checks
- restarts installed/enabled Voxera user services

## Rollback guidance (advanced)

If a fresh update causes regressions, rollback to a known commit:

```bash
cd ~/VoxeraOS
git log --oneline --decorate -n 20
git checkout <sha>
cd <your-checkout-path>
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m compileall src
pytest -q
make services-restart
```

Then validate:

```bash
voxera status
voxera queue status
```

This keeps you pinned to the selected commit (detached HEAD) until you intentionally switch back.

When ready, return to main:

```bash
cd ~/VoxeraOS
git checkout main
git pull --ff-only
```

## Logs and audits

### Mission logs
- Mission summaries are appended to `~/VoxeraOS/notes/mission-log.md`.
- Queue and approval flows also leave traces in notes/audit artifacts.

### systemd journal

```bash
systemctl --user status voxera-daemon.service voxera-panel.service
journalctl --user -u voxera-daemon.service -f
journalctl --user -u voxera-panel.service -f
```

### Voxera audit views

```bash
voxera audit
voxera queue status
```


## Planner reliability workstream (dashboard + alerts)

Dashboard (live): `https://grafana.voxera.internal/d/planner-reliability/mission-planner-reliability`

Use planner audit events (`planner_selected`, `planner_fallback`, `plan_built`, `plan_failed`) as the canonical telemetry stream for production operator panels.
Gemini and OpenAI-compatible planners both emit this same fallback telemetry contract.

Required dashboard panels:
- Fallback rate: `% of planner events with fallback_used=true` (slice by provider/model).
- Plan failure rate: `% of requests ending in plan_failed`.
- Planner latency percentiles (p50/p95/p99) from `latency_ms`, segmented by `provider` + `model`.
- `error_class` distribution, grouped by provider/model.
- Attempt-depth trend: distribution/time series of `attempt` values to show retry depth pressure.

Alerting (live): `https://grafana.voxera.internal/alerting/list?search=planner`

Initial thresholds, ownership, and routing:

| Alert | Threshold | Baseline window | Sustain window | Owner | Routing |
|---|---|---|---|---|---|
| fallback-rate spike | fallback_used=true rate > `8%` AND >= `2x` trailing baseline | 7d trailing same-hour baseline | 15m | AI Runtime On-Call | PagerDuty: `voxera-ai-runtime` + `#ops-planner` |
| sustained plan-failure increase | `plan_failed` rate > `3%` | 24h baseline | 20m | AI Runtime On-Call | PagerDuty: `voxera-ai-runtime` |
| timeout spike | `error_class=timeout` > `2%` by provider/model | 24h baseline | 10m | Platform SRE | PagerDuty: `voxera-sre` + `#ops-planner` |
| rate_limit spike | `error_class=rate_limit` > `1.5%` by provider/model | 24h baseline | 10m | Platform SRE | PagerDuty: `voxera-sre` + vendor escalation runbook |
| malformed_json spike (drift signal) | `error_class=malformed_json` > `1%` and > `3x` 7d baseline | 7d trailing baseline | 15m | Planner Maintainers | Slack: `#ops-planner` + Jira component `planner-telemetry` |

### How to interpret planner degradation

1. Open **Fallback Rate** and **Attempt-Depth Trend** panels first. If fallback and higher attempts rise together, primary reliability drift is likely.
2. Check **Error Class Distribution** next:
   - `malformed_json` growth -> likely response-format drift/model behavior change.
   - `timeout` growth -> provider latency/saturation or network transport degradation.
   - `rate_limit` growth -> quota or burst-management pressure.
3. Confirm impact with **Plan Failure Rate** and **Latency p95/p99** panels to distinguish recoverable retries from user-visible failures.
4. Use the alert list above to route response: runtime vs SRE vs planner maintainer ownership.

Quick audit triage command:

```bash
voxera audit | rg "planner_selected|planner_fallback|plan_built|plan_failed"
```


### Planner drift watch (fallback diagnostics)

Use planner audit events to spot model drift and provider instability:
- `planner_fallback` captures `provider`, `model`, `attempt`, `error_class`, `latency_ms`, and `fallback_used`.
- `plan_built` captures the winning provider/model and whether fallback routing was used.

Operational heuristic:
- Rising `fallback_used=true` rates over a rolling window usually indicate primary-plan quality or reliability drift.
- Rising `error_class=malformed_json` typically indicates output-format drift.
- Rising `error_class=timeout` or `rate_limit` usually indicates provider saturation/transport issues.

Start with:

```bash
voxera audit | rg "planner_fallback|plan_built"
```

If fallback frequency spikes, compare by `provider` + `model` and promote/demote routing (`primary` vs `fast` vs `fallback`) until error-class mix returns to baseline.

## Safety note

Operational workflows here do **not** require deleting data under `~/VoxeraOS/notes`.




## Incident bundle export runbook

### Per-job bundle

```bash
# CLI
voxera ops bundle job <job_id>

# Panel (requires Basic auth)
GET /jobs/{job_id}/bundle
```

Panel `/jobs` now shows cross-bucket job rows (inbox/pending/approvals/done/failed/canceled) with artifact presence markers (plan/actions/stdout/stderr), last activity from `actions.jsonl`, flash banners, and direct actions (detail/bundle/approve/deny/cancel/retry/delete).

Bundle includes (size-capped and deterministic):
- `job.json`
- optional `approval.json`
- optional `failed.error.json`
- capped contents of `artifacts/<job_id>/`
- `health.json` snapshot
- `manifest.json` with truncation/byte metadata

### System snapshot bundle

Panel bundle downloads write to deterministic incident folders:
`notes/queue/_archive/incident-<YYYYMMDD-HHMMSS>-<job_stem_or_system>/`.

CLI ops bundles default to `notes/queue/_archive/<YYYYMMDD-HHMMSS>/`. For incident handoff, you can place both system + job bundles in a single folder with `--dir` (or by setting `VOXERA_OPS_BUNDLE_DIR`).

```bash
# CLI
voxera ops bundle system
voxera ops bundle job <job_id>

# one shared incident folder
voxera ops bundle system --dir notes/queue/_archive/INCIDENT-123
voxera ops bundle job <job_id> --dir notes/queue/_archive/INCIDENT-123

# Panel (requires Basic auth)
GET /bundle/system
```

System bundle contains `manifest.json`, queue snapshots (`queue_status.txt`, `queue_health.json`, lock snapshot), redacted config snapshots (`snapshots/config_snapshot.json`, `snapshots/config_snapshot.sha256`), optional `journal_voxera_daemon_tail.txt`, and `panel_log_hint.txt`.
Job bundles include the same redacted config snapshot files under `snapshots/` for operator handoff consistency. Optional approval/failed-sidecar files are now quiet on normal success paths (single optional note), with anomaly notes only when a bucket implies an expected missing artifact.

### OpsConsole golden-path e2e script output directory

`scripts/e2e_opsconsole.sh` supports `--dir` to write all script outputs (`e2e.log`, doctor output, zip listings, and both bundle zips) into an operator-selected directory.

```bash
# path with spaces
scripts/e2e_opsconsole.sh --dir "/tmp/inc test"

# path starting with dash (requires explicit -- separator)
scripts/e2e_opsconsole.sh --dir -- /tmp/-weird
```

Without `--dir`, the script keeps the default archive location under `notes/queue/_archive/ops-e2e-<timestamp>/`.

### Truncation + size troubleshooting

- Per-file cap defaults to 256KB; total bundle cap defaults to 4MB.
- When exceeded, files are truncated and noted in `manifest.json` (`truncated=true`, original/written bytes).
- If you need full raw logs, collect artifacts directly from queue paths under controlled access.

## Doctor quick mode

```bash
voxera doctor --quick
```

`--quick` is offline-only and does **not** call LLM providers. It reports:
- lock status (`exists`, `pid`, `alive`)
- health `last_ok_event/last_ok_ts_ms` and `last_error/last_error_ts_ms`
- queue counts summary (`inbox`, `pending`, `approvals`, `done`, `failed`)
- last brain fallback transition (reason, from/to tier, timestamp)

Example details lines:
- `exists=True pid=12345 alive=True`
- `event=daemon_tick ts=1730000000000`
- `inbox=0 pending=1 approvals=0 done=12 failed=2`
- `primary -> fast reason=RATE_LIMIT ts=1730000099000` (or `none`)

Use full `voxera doctor` when you want provider capability tests; use `--quick` during incidents for immediate local sanity checks.

`voxera doctor` also includes a `skills.registry` diagnostics row. Treat this as the operator contract for skill health:
- `valid`: usable for governed execution.
- `invalid`: malformed schema or malformed/unknown capability metadata; not loadable (fail-closed).
- `incomplete`: loads structurally but missing required runtime governance metadata (for example empty capabilities); excluded from runtime until fixed.
- `warning`: optional/recommended metadata missing (for example `output_schema`).

When `invalid`/`incomplete` is non-zero, review the reason codes and apply the hinted action (`fix_manifest`, `add_capabilities`) before relying on the skill in production workflows.

Built-in skills should also follow a consistent manifest governance baseline so approval/review output stays comparable:
- always declare `exec_mode`, `needs_network`, `fs_scope`, `output_schema`, and `output_artifacts`
- use `fs_scope=read_only` for local read-mostly skills with no filesystem artifact expectations
- filesystem productivity waves 1–2 skills: `files.list_dir`/`files.exists`/`files.stat` (`read_only`) and `files.copy_file`/`files.move_file`/`files.mkdir`/`files.delete_file` (`workspace_only`) remain local-only with `needs_network=false` and notes-root path confinement.
- keep deterministic `output_artifacts` empty (`[]`) when no file artifacts are predictably produced

## Brain fallback reason observability

When the planner falls back between brain tiers, each transition is classified into a stable reason enum:

- `TIMEOUT` — timeout exceptions or "timed out" messages
- `AUTH` — HTTP 401/403 or auth-related messages
- `RATE_LIMIT` — HTTP 429 or rate limit messages
- `MALFORMED` — JSON decode errors, invalid schema
- `NETWORK` — DNS, connection refused/reset, connect errors
- `UNKNOWN` — everything else

Health counters (`voxera queue health`):
- `brain_fallback_count` — total transitions
- `brain_fallback_reason_timeout`, `brain_fallback_reason_auth`, `brain_fallback_reason_rate_limit`, `brain_fallback_reason_malformed`, `brain_fallback_reason_network`, `brain_fallback_reason_unknown`

Health snapshot (`health.json`) adds:
- `last_fallback_reason`, `last_fallback_from`, `last_fallback_to`, `last_fallback_ts_ms`

Audit trail emits `brain_fallback_transition` events with `from_tier`, `to_tier`, `reason`, `attempt_index`, `latency_ms`, `provider`, `model`, and `error_summary` (token-safe; no prompts or response bodies).

Troubleshooting quick reference:
- `RATE_LIMIT` → API throttling; check provider quota/burst limits.
- `AUTH` → bad key/config; verify API key and permissions.
- `TIMEOUT` → network/provider slowness; check connectivity and provider status.

## Doctor golden-path self-test

Use `voxera doctor --self-test` to run a tiny safe queue job (`system_check`) and validate:

- queue daemon processing
- audit event visibility
- queue artifact bundle creation (`plan.json`, `actions.jsonl`, `stdout.txt`, `stderr.txt`)

The command prints pass/fail and actionable fix steps when checks fail.


### approval artifact scope compatibility

Queue approval artifacts now write scope in two locations for compatibility:
- top-level: `fs_scope`, `needs_network`
- nested: `scope.fs_scope`, `scope.needs_network`

Readers prefer top-level keys when present and fall back to nested `scope.*` for older artifacts.


## Panel Daemon Health widget

Panel home (`/`) shows a collapsible **Daemon Health** widget. Data source: `notes/queue/health.json` read via `read_health_snapshot()`. No daemon RPC calls are performed; the widget is safe for panel-only deployments where the daemon is not running.

### Field reference

| Field | Source key(s) in health.json | Meaning |
|---|---|---|
| **Lock status** | `lock_state`, `lock_pid`, `lock_stale_age_s` | `held` / `stale` / `clear`. `stale` means lock file exists but PID is dead. |
| **Lock PID** | `lock_pid` | PID holding the daemon lock; `—` when not held. |
| **Stale age** | `lock_stale_age_s` | Seconds since lock became stale; shown only when `lock_state=stale`. |
| **Last brain fallback** | `last_fallback_reason`, `last_fallback_from`, `last_fallback_to`, `last_fallback_ts_ms` | Tier transition summary (e.g. `primary → fast, TIMEOUT`) or "no recent fallbacks". |
| **Startup recovery** | `last_startup_recovery_ts`, `last_startup_recovery_counts` | Count of jobs failed + orphans quarantined on last daemon start; "clean" when zero. |
| **Last shutdown** | `last_shutdown_ts`, `last_shutdown_reason`, `last_shutdown_outcome` | Outcome of last daemon stop: `clean_shutdown` or `failed_shutdown`; includes timestamp. |
| **Daemon state** | `daemon_state` | `healthy` (default) or `degraded` after 3 consecutive brain fallback events. |

Neutral placeholders are shown for any field that is null/empty (fresh install or daemon not yet run).

Additional degradation fields in `health.json`:
- `consecutive_brain_failures` (int): increments once per fallback plan attempt, resets to `0` on successful mission completion.
- `degraded_since_ts` (float\|null): epoch seconds when the daemon first entered degraded state for the current streak.
- `degraded_reason` (str\|null): currently `brain_fallbacks` when degraded from fallback streaks.

Operator interpretation:
- `brain_backoff_wait_s` (int): computed wait (seconds) from `consecutive_brain_failures` for the next planning attempt.
- `brain_backoff_active` (bool): `true` when computed backoff is currently in effect (`brain_backoff_wait_s > 0`), otherwise `false`.
- `brain_backoff_last_applied_s` (int): most recent wait actually applied by daemon sleep before a plan attempt (default `0`).
- `brain_backoff_last_applied_ts` (float|null): epoch seconds for the most recent applied sleep (default `null`).
- Policy note: when no sleep is needed (`wait_s=0`), last-applied fields are **kept as last known values** to preserve operator visibility.

- `daemon_state=healthy` + `consecutive_brain_failures=0`: normal/cleared state.
- non-zero `consecutive_brain_failures` + `healthy`: warning streak below threshold.
- `daemon_state=degraded`: at least 3 consecutive fallback attempts have occurred; investigate provider health/credentials/network before queue pressure increases.

Inspect quickly with jq:

```bash
jq "{daemon_state, consecutive_brain_failures, brain_backoff_wait_s, brain_backoff_active, brain_backoff_last_applied_s, brain_backoff_last_applied_ts}" ~/VoxeraOS/notes/queue/health.json
```

### Data freshness

The widget reflects the most recent `health.json` write. The daemon and panel both write to this file atomically (`health.json.tmp` → rename). If the daemon is not running, the widget still renders the last snapshot — staleness is not surfaced explicitly in v0.1.8.

---

## Panel queue hygiene workflow

The panel `/hygiene` page gives operators a read-safe window into queue hygiene state and allows triggering diagnostic runs without daemon RPC or terminal access.

### How it works

- **Page source**: reads `last_prune_result` and `last_reconcile_result` from `notes/queue/health.json`.
- **Run prune (dry-run)**: POSTs to `/hygiene/prune-dry-run`. Panel invokes `voxera queue prune --json` as a local CLI subprocess. **Prune is always dry-run from the panel — no `--yes` flag is passed, so no data is deleted.** This is a report-only operation.
- **Run reconcile**: POSTs to `/hygiene/reconcile`. Panel invokes `voxera queue reconcile --json` as a local CLI subprocess. This is a read/analysis-only scan that never modifies files.
- Results are JSON-parsed and merged atomically into `notes/queue/health.json` under:
  - `last_prune_result` — prune dry-run summary (timestamp, per-bucket candidates, reclaimed bytes estimate).
  - `last_reconcile_result` — reconcile scan summary (timestamp, `issue_counts` per category).
- The page JS updates result sections in-place without a full reload; buttons disable during the run and re-enable on completion.
- Both POST endpoints require operator Basic auth + CSRF mutation guard.

### Reconcile issue_counts schema

`last_reconcile_result.issue_counts` is a dict keyed by issue category:

| Category | Meaning |
|---|---|
| `orphan_sidecars` | `.error.json`/`.state.json` in terminal buckets with no matching primary job |
| `orphan_approvals` | Files in `pending/approvals/` with no corresponding `pending/job-*.json` |
| `orphan_artifact_candidates` | Direct children of `artifacts/` with no matching job in any bucket |
| `duplicate_job_filenames` | `job-*.json` appearing in more than one bucket |

A zero count for all categories means the queue is clean.

### Safety model summary

| Action | Deletes data? | Requires `--yes`? | Panel uses `--yes`? |
|---|---|---|---|
| Prune dry-run (panel button) | No | N/A | No — dry-run only |
| Prune with deletion (CLI) | Yes | Yes | Never |
| Reconcile (panel button) | No | N/A | No |
| Reconcile fix+apply (CLI) | No (quarantine move) | Yes | Never |
- UI updates asynchronously after each run (no full page reload).

## Panel recovery inspector (`/recovery`)

The panel includes a read-only **Recovery Inspector** page at `/recovery` for operator triage of queue safety buckets:

- `notes/queue/recovery/`
- `notes/queue/quarantine/`

Behavior:

- Lists immediate child directories (session-style layout) and loose files (legacy layout).
- Shows name, modified timestamp, total size (bytes), and file count.
- Provides **Download ZIP** per row via `/recovery/download/{bucket}/{name}`.
- Download endpoint is operator-auth protected and validates:
  - bucket in `{recovery, quarantine}`
  - `name` is a single path segment
  - resolved path remains inside the allowed bucket root
- ZIP generation skips symlinks and enforces archive safety limits (file count + total bytes).
- Panel flow is read-only: no delete, move, or reconcile operations are performed by this page.


## Queue health observability rendering

`voxera queue health` is structured for operations triage:
- **Current State**: queue root/intake, daemon state + pid, lock state, degradation/backoff, panel auth lockouts.
- **Recent History**: last OK event, last error, last fallback transition, last shutdown context.
- **Historical Counters**: merged lock/runtime/auth cumulative counters from `health.json` for trend context (not current runtime state).

`--json` keeps compatibility with `counters` and also provides `historical_counters`; both represent cumulative history alongside `current_state` and `recent_history`.

Missing/absent history is rendered consistently as `-` across CLI, doctor quick output, and panel performance history (instead of partial pairs like empty value + `None` timestamp). In JSON, absent history remains explicit (`null`) for authoritative semantics.

`--watch` repeatedly refreshes the same layout (default `--interval 2.0`) for live incident response.

Panel home includes a read-only **Performance Stats** tab that surfaces these same operator signals (queue counts, degradation/backoff, recent fallback/error/shutdown, auth/runtime counters) directly from `health.json`.

### Panel code ownership (refactor baseline)
- `src/voxera/panel/app.py` is the FastAPI composition root (setup, shared security/queue helpers, and route registration).
- `src/voxera/panel/routes_home.py` owns home/dashboard and queue-create routes.
- `src/voxera/panel/routes_jobs.py` owns jobs, approvals, cancel, and retry routes.
- `src/voxera/panel/routes_queue_control.py` owns queue delete/pause/resume routes.
- `src/voxera/panel/routes_assistant.py` owns assistant advisory routes and degraded queue/brain fallback behavior.
- `src/voxera/panel/routes_missions.py` owns mission + mission-template creation routes.
- `src/voxera/panel/routes_bundle.py` owns job/system bundle download routes.
- `src/voxera/panel/routes_hygiene.py` owns hygiene/operator-maintenance routes.
- `src/voxera/panel/routes_recovery.py` owns recovery/quarantine inspector routes.
- `src/voxera/panel/helpers.py` contains shared request/value helpers.
- Future panel changes should prefer extending a domain route module (or adding a new `routes_<domain>.py`) instead of growing `app.py` back into a monolith.
- Jobs mutation redirects are intentionally emitted as relative `/jobs?...` URLs (not absolute `url_for` URLs) to avoid proxy/front-door host-scheme mismatches.

## Queue daemon refactor boundaries

- Keep control-plane orchestration in `src/voxera/core/queue_daemon.py` (constructor/setup, lock/watch/tick orchestration, top-level lane routing, config drift snapshotting, and operator status surfaces).
- Keep mission execution/process pipeline mechanics in `src/voxera/core/queue_execution.py` (inbox filtering, queued→planning→running flow, approval-pending handoff, runtime result handling, and done/failed/blocked lifecycle finalization with existing action/audit/health side effects).
- Keep approval workflow mechanics (approval prompts, pending artifacts, approval ref normalization/canonicalization, grants, approve/deny resolution) in `src/voxera/core/queue_approvals.py`.
- Keep assistant/advisory queue-lane mechanics (assistant provider/fallback attempts, response artifact persistence, advisory lifecycle/action events, assistant failure artifact handling) in `src/voxera/core/queue_assistant.py`.
- Keep persisted `*.state.json` sidecar path/read/write/snapshot helpers in `src/voxera/core/queue_state.py`.
- Keep deterministic bucket-transition mechanics (job move + sidecar co-move + collision-safe target selection) in `src/voxera/core/queue_paths.py`.
- Keep startup recovery mechanics (in-flight pending restart recovery, orphan quarantine, recovery report assembly) and shutdown/in-flight fail-on-shutdown helpers in `src/voxera/core/queue_recovery.py`.
- Preserve sidecar naming/location, schema versions, and bucket transition semantics exactly when refactoring.
- Execution/lifecycle semantics are intentionally preserved exactly across this boundary (including intermediate states/timestamps and artifact-write ordering).

## Structured-first operator surfaces

For job detail/status/bundle flows, Voxera now prefers canonical structured execution artifacts (`execution_result.json`, `step_results.json`) to derive terminal outcome, lifecycle state, step summaries, approval/blocked/retryable hints, and declared output artifacts. If these files are missing or partial, operators still get legacy sidecar and audit-based summaries.

`execution_result.json` also carries an additive normalized artifact/evidence contract for review operations:
- `artifact_families` (normalized produced family names)
- `artifact_refs` (family/path refs)
- `review_summary` (reviewer-facing status/summary fields)
- `evidence_bundle` (traceable evidence bundle that links job/step execution context to artifacts)


### Producer intent artifacts and compatibility

Queue jobs may include additive `job_intent` metadata produced by panel/inbox/assistant lanes or derived by daemon normalization. Operators may also see `artifacts/<job>/job_intent.json` for newer jobs. If absent, workflows continue using existing payload/state semantics; absence is expected for legacy jobs.


## Bounded replan operations

- Queue execution evaluates each attempt and records evaluator class/reason in `execution_result.json`.
- Planner unknown-skill mismatches are persisted in `plan.attempt-<n>.json` under `planning_error` and may trigger exactly one bounded replan for goal jobs.

- Replan is bounded by config `max_replan_attempts` (default `1`) and only for retryable/replannable classes.
- Approval-pending and policy/capability boundary blocks do not replan; job remains pending/failed deterministically.
- Inspect adaptation via:
  - `artifacts/<job>/plan.attempt-<n>.json`
  - `artifacts/<job>/execution_envelope.json` (attempt/replan counters)
  - `artifacts/<job>/execution_result.json` (`attempt_index`, `replan_count`, `evaluation_class`, `stop_reason`)
- This is controlled adaptation, not blind retry: the loop exits on success, approval pause, non-retryable block, or max-attempt bound.


### Operator interpretation of normalized skill results

When triaging step failures/blocks, prefer `step_results[].summary`, `operator_note`, `next_action_hint`, `error_class`, `retryable`, `blocked`, and `approval_status`. `machine_payload` is intended for deterministic machine facts; human guidance belongs in `operator_note`.
When triaging capability boundaries, also inspect `review_summary.capability_boundary_violation` and approval payload `capability_boundary_notes`; these are deterministic declared-vs-requested mismatch signals (for example runtime network request against declared `network_scope=none`).

## Operator live progress expectations

For running jobs, use job detail pages and expect the following live states when present in canonical state:

- `queued`, `planning`, `advisory_running`, `running`, `awaiting_approval`, `done`, `failed`, `canceled`

What updates live:

- lifecycle state and terminal outcome
- current/total steps, last attempted/completed step
- approval status
- execution lane / fast-lane metadata
- intent-route metadata
- latest summary/operator note (if emitted)
- failure summary / stop reason for terminal jobs

Limitations:

- No synthetic percent-complete bars are shown.
- If artifacts/sidecars do not contain a field, the panel leaves it unknown/empty.
- Without JavaScript, pages still load and can be manually refreshed.


## Operator visibility: lineage metadata

Operators can inspect optional lineage metadata on job detail and progress surfaces. If a job was submitted with lineage fields, the panel shows a small Lineage block and progress JSON includes lineage keys (`parent_job_id`, `root_job_id`, `orchestration_depth`, `sequence_index`). Missing or malformed values are sanitized/omitted without affecting execution.


## Operator visibility: child enqueue relationships

For parent jobs that enqueue a child, operators can inspect:
- `artifacts/<parent>/child_job_refs.json`
- `artifacts/<parent>/actions.jsonl` event `queue_child_enqueued`
- `artifacts/<parent>/execution_result.json` field `child_refs`
- panel job detail `Child Jobs` block + `Child Summary` rollup
- `/jobs/<id>/progress` `child_refs` + `child_summary`

Child summary is read-only operator visibility and does not change orchestration or approval semantics. Child jobs remain normal queue jobs and expose lineage through existing envelope/result/progress surfaces.

## Vera v0 manual STV (explicit preview + handoff boundary)

1. Start services:
   - `voxera daemon`
   - `voxera panel --host 127.0.0.1 --port 8844`
   - `make vera`
2. Open `http://127.0.0.1:8790/`.
3. Send a greeting and verify you receive a conversational assistant reply.
4. Send a follow-up that depends on prior context; verify short session context continuity.
5. Continue chatting past the rolling cap and confirm behavior remains stable (older turns may roll off by design).
6. Ask for an action-shaped request (for example: open URL, read file, write note).
7. Verify Vera states the execution boundary: real actions must go through VoxeraOS queue handoff.
8. Verify Vera returns a structured JSON preview and clearly states no execution happened yet.
9. Explicitly request handoff (chat command or submit button) and verify a real inbox job is created.
10. Verify Vera reports submission honestly (submitted/queued, not executed yet) with job id/ack when available.
11. Confirm queue status/panel/progress surfaces show the real job lifecycle.
12. If approval is required, verify approval remains in VoxeraOS controls (not bypassed by Vera).
13. Verify Vera does not claim final execution unless VoxeraOS evidence confirms it.
14. Use "Clear chat + context" and verify prior turns are removed for the active session.
15. Verify operator panel routes (`/`, `/jobs`, `/assistant`) continue to work unchanged.

Notes:
- Context is session-scoped and intentionally lightweight for v0.
- Restart persistence is not guaranteed for all deployment modes; this is acceptable in v0.

## Vera natural-language handoff STV (PR #154)

Manual STV sequence for natural phrasing + strict queue handoff:

1. Start daemon, panel, and Vera web app.
2. In Vera, ask: `Can you go to example.com?`
3. Confirm a prepared preview appears (proposal-only language).
4. Confirm DEV diagnostics show `preview_available = True`.
5. Ask: `submit it`.
6. Confirm a real queue inbox job is created and Vera reports submitted/queued (not executed).
7. Confirm queue/panel shows the job lifecycle.
8. Repeat with: `visit example.com`, `take me to example.com`, `read ~/VoxeraOS/notes/test.txt`, and (if supported) `make a note called hello.txt`.
9. Confirm informational asks like `what is example.com` do not auto-draft open-url previews.


## Structured file-write content STV (PR #157)

1. Start daemon, panel, and Vera web app.
2. Ask Vera for a contentful write request (for example: `write a file called funnyjoke.txt with the content "Why don’t scientists trust atoms? Because they make up everything!"`).
3. Confirm preview preserves explicit filename and content intent, and remains proposal-only.
4. Explicitly submit/handoff to VoxeraOS.
5. Confirm queue inbox job contains `goal` plus `write_file.path` and `write_file.content`.
6. Confirm execution writes requested content to requested file path in notes scope.
7. Inspect canonical evidence: `step_results.json`, `execution_result.json`, and `execution_envelope.json`.
8. Verify evidence target path/result match actual on-disk file contents.

## Vera evidence-aware review STV (PR #155)

1. Start daemon, panel, and Vera web app.
2. Ask Vera to prepare and submit a real job.
3. Let that job land in one of: awaiting approval, succeeded, or failed.
4. Ask Vera: `what happened to that job?`, `did it work?`, `why did it fail?`, `what should I do next?`.
5. Verify Vera's answer matches canonical queue evidence (`execution_result.json`, step/state/approval/failed sidecars as applicable).
   - Verify summaries prefer normalized `review_summary.latest_summary` when present.
   - Verify evidence trace metadata is grounded in `evidence_bundle.trace` when present.
   - Verify lifecycle wording clearly distinguishes submitted/queued/planning/running/awaiting_approval/resumed from terminal outcomes.
   - Verify expected-artifact wording is state-aware (`awaiting_approval`/`canceled` gaps not misframed as failures; `failed` guidance points to `stderr`/`step_results`; `succeeded` + partial guidance requests evidence-gap validation).
6. Ask Vera to `prepare the next step` when appropriate.
7. Verify Vera drafts preview-only follow-up JSON.
8. Verify no automatic submit occurs unless explicit handoff is requested.

### Denied approval terminal-state STV (PR #156)

1. Submit an approval-gated job.
2. Confirm it reaches `awaiting_approval`.
3. Deny it from the VoxeraOS panel.
4. Confirm queue and panel no longer report `awaiting_approval` as current truth.
5. Confirm canonical evidence is terminal and consistent:
   - `lifecycle_state = failed`
   - `terminal_outcome = failed`
   - `approval_status = denied` (not `pending`)
6. Confirm `blocked_reason` / `failure_summary` reflect operator denial.
7. Ask Vera what happened to the last job.
8. Confirm Vera reports the denied terminal state from canonical evidence.


## Vera preview draft lifecycle STV (PR #158)

1. Start daemon, panel, and Vera web.
2. Ask Vera to prepare a preview (`open example.com`) and confirm DEV diagnostics show `preview_available = True`.
3. Ask a refinement (`actually open openai.com instead`) and confirm the new preview replaces the previous active draft.
4. Optionally add acknowledgement (`yeah that looks right`) and confirm preview is still active.
5. Ask `submit it` and verify queue inbox payload reflects the latest draft, not an older preview.
6. Repeat for file refinement flows (content update, filename rename, append/overwrite mode switch, semantic refinement like `make it more formal` or `summary of top news`) and evidence-aware follow-up draft replacement.


## Vera authoritative preview pane STV (PR #159)
1. Start daemon, panel, and Vera web app.
2. Ask Vera to prepare a preview.
3. Confirm preview pane shows active JSON draft and DEV diagnostics show `preview_available = True`.
4. Click `Submit current preview to VoxeraOS` in the pane.
5. Confirm the exact shown preview is submitted through queue inbox handoff.
6. Repeat with revised draft: prepare preview A, revise to preview B, confirm pane now shows B, submit, and confirm inbox job payload matches B.
7. Repeat using natural approval phrasing (`that looks good now use it`, `use this preview`) and confirm submission works only when active preview exists.
8. Confirm no-preview phrasing fails honestly and does not enqueue jobs.
9. Confirm successful submit clears active preview so pane affordance is removed.
10. Confirm chat auto-scroll behavior keeps latest messages visible during normal use while still allowing manual scroll-up inspection.

- Operator note: `expected_artifact_status=none_declared` is expected on historical jobs; focus on forward-created jobs to validate new deterministic expected-artifact declarations.

- Operational note: linked Vera completions are ingested from canonical queue state/evidence only, without LLM monitoring.
- Current auto-surfacing behavior is intentionally narrow: on each chat cycle Vera may emit one deterministic follow-up for at most one unsurfaced linked completion with policy `read_only_success`, `mutating_success`, `approval_blocked`, or `failed`, then marks that completion surfaced.
- All other completion classes remain manual-review only in this phase.
- Value-forward surfacing: when a linked completion contains structured evidence from read/inspection skills (file read, exists, stat, list_dir, service_status, recent_service_logs, diagnostics, process_list), the surfaced message prefers concise result text (e.g. "Service X is active/running (user service).", "/notes/todo.txt exists (file).") over generic "completed successfully" wording. Fallback to status-oriented text applies when no useful machine_payload is present.
- Answer-first file reads: file read outputs now surface actual bounded content (up to 2048 chars in evidence, up to 480 chars in surfaced text) plus file metadata (bytes, line count). Small files show full content; large files show a truncated excerpt.
- Service status scope awareness: service status checks both system and user scopes. Voxera services running as user services are correctly reported. When scopes differ, both states are surfaced (e.g. "active/running (user service). In system scope: inactive/dead.").
- Recent logs scope awareness: log retrieval tries both system and user-unit scopes and prefers whichever has actual content. Scope is shown in output. "No recent logs" is stated only when truly empty.
