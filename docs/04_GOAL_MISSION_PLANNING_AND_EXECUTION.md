# 04 — Goal, Mission, Planning and Execution

This document covers how a goal becomes a planned, executed mission inside
the queue. All references are to `src/voxera/core/`.

## Inputs

A queue job can begin from any of:

- A built-in mission template id (`mission_id`).
- A free-text `goal` (planned by `mission_planner.plan_mission`).
- An inline list of `steps`.
- A `file_organize` payload (governed file mutation flow).
- A `write_file` payload (governed file write flow).

The intended `request_kind` is derived per `core/queue_contracts.py`.

## Intent classifiers

Goals are first inspected by a stack of bounded classifiers before any LLM
plan is requested:

- **`core/simple_intent.py`** (`classify_simple_operator_intent`,
  `check_skill_family_mismatch`). Maps direct operator phrasings to a
  small set of intents: `assistant_question`, `open_terminal`, `open_url`,
  `open_app`, `write_file`, `read_file`, `run_command`,
  `unknown_or_ambiguous`. Each intent maps onto an allowed skill family
  (e.g. `write_file → files.write_text`, `run_command → sandbox.exec`).
  Detects compound actions linked by phrases like "and then".
- **`core/file_intent.py`** (`classify_bounded_file_intent`,
  `detect_blocked_file_intent`). All paths are bounded to
  `~/VoxeraOS/notes/` and the queue control plane subtree is excluded.
  Sub-classifiers cover exists/stat/read/mkdir/delete/copy/move/rename/find/
  grep/list_tree/archive_organize. Each one validates against parent
  traversal (`..`) and scope boundaries.
- **`core/code_draft_intent.py`** (`is_code_draft_request`,
  `extract_code_from_reply`). Identifies "write me a script", config edit,
  command create patterns and infers language from fenced code blocks.
- **`core/writing_draft_intent.py`** — same idea for prose/writing drafts.
- **`core/queue_job_intent.py`** — derives the `job_intent` artifact that
  records the operator-facing classification at submit time.

A request that fails any of these classifiers fails closed: the planner is
not invoked, and the operator gets a deterministic refusal.

## Mission templates

Built-in mission templates live in `core/missions.py`:

- `work_mode`
- `focus_mode`
- `daily_checkin`
- `incident_mode`
- `wrap_up`
- `notes_archive_flow`
- `system_check`
- `system_inspect`
- `system_diagnostics`

Plus on-disk JSON missions discovered from `missions/`:

- `sandbox_smoke`
- `sandbox_net`

`MissionTemplate` and `MissionStep` are dataclasses; `list_missions()`,
`list_missions_best_effort()`, and `get_mission(mission_id)` are the
public lookups. `MissionRunner` executes a sequence of steps, capturing
results.

## Mission planner

`core/mission_planner.py` is the LLM-backed planner used when a free-text
goal is submitted. Highlights:

- `plan_mission(...)` (re-exported through `queue_daemon`) is the main
  entry point.
- `MissionPlannerError` is raised on planner failure.
- `_BrainCandidate` builds an ordered list of brain candidates from the
  resolved `AppConfig` (`_build_brain_candidates`), trying each in turn
  with deterministic error classification (`_classify_planner_error`).
- The planner injects a capabilities snapshot prompt block
  (`_build_capabilities_prompt_block`) so the LLM only sees skills the
  registry knows about.
- Output is constrained: `_normalize_step_args(...)`,
  `_normalize_file_step_paths(...)`,
  `_normalize_sandbox_exec_step(...)`, and a family of "explicit goal" tests
  (`_goal_requests_file_write`, `_goal_requests_file_read`,
  `_goal_explicitly_requests_shell_commands`, `_sandbox_step_uses_disallowed_tooling`)
  refuse to silently extend operator intent.
- `sanitize_goal_for_prompt()` strips ANSI escapes and other unsafe text
  before the goal is passed into the prompt.
- Allowed notes/checkin write extraction
  (`_extract_allowed_notes_write_args`, `_extract_checkin_note_write_args`)
  ensures planned steps land inside the bounded notes workspace.
- `_make_dryrun_deterministic(...)` (in `missions.py`) produces
  byte-stable output for golden tests.

## Capabilities snapshot

`core/capabilities_snapshot.py` (`generate_capabilities_snapshot`) walks
the skill registry and emits a normalized snapshot consumed by the planner
prompt and by `voxera ops capabilities`. It is also used directly by the
panel home page to render which capabilities exist on the host.

`core/planner_context.py` is the small dataclass passed into the planner
that holds the resolved capabilities snapshot, the operator goal, and any
freeze flags from CLI options (`--freeze-capabilities-snapshot`,
`--deterministic`).

## Execution evaluator

`core/execution_evaluator.py` decides whether a planned step is safely
executable given the resolved manifest's capability declaration. It
delegates capability semantics to `core/capability_semantics.py` and
side-effect classification to `core/execution_capabilities.py`:

- `SideEffectClass`: `CLASS_A` (no side effects), `CLASS_B` (moderate),
  `CLASS_C` (broad).
- `FilesystemScope`: `NONE`, `CONFINED` (workspace), `BROADER`.
- `NetworkScope`: `NONE`, `READ_ONLY`, `BROADER`.
- `SandboxProfile`: `HOST_LOCAL`, `SANDBOX_NO_NETWORK`, `SANDBOX_NETWORK_SCOPED`.
- `ExecutionCapabilityDeclaration` — frozen dataclass that normalizes a
  manifest's declared semantics for use by the evaluator.

## Queue execution

`core/queue_execution.py` (`QueueExecutionMixin`) drives the actual
running phase of a queue job:

- Resolves the mission (template, planner output, or inline steps).
- Runs each step through `voxera.skills.runner.SkillRunner`.
- Records `step_results.json`, `actions.jsonl`, `stdout`/`stderr` captures
  where available.
- Emits the canonical `execution_envelope.json`, `execution_result.json`,
  `evidence_bundle.json`, and `review_summary.json` artifacts.
- Promotes the job to the appropriate terminal bucket and updates the
  state sidecar.

## Approvals

`core/queue_approvals.py` (`QueueApprovalMixin`) handles transitions into
and out of `awaiting_approval`:

- Writes the approval payload into `pending/approvals/<job>.json`.
- Accepts approve / approve-always / deny decisions and persists the
  decision into the state sidecar before resuming or terminating the job.
- Approve-always records a policy uplift via the policy engine.
- Auto-approve (dev only) is gated by `VOXERA_DEV_MODE` and the
  `--auto-approve-ask` daemon flag.

## Mission CLI surfaces

- `voxera missions list` — built-in templates plus on-disk missions.
- `voxera missions plan GOAL` — planner dry-run / submit (with
  `--freeze-capabilities-snapshot` and `--deterministic` for stable output).
- `voxera missions run MISSION_ID` — direct mission execution.
- `voxera run SKILL_ID --arg key=value` — direct one-off skill run.
- `voxera inbox add` / `inbox list` — drop a payload into `inbox/`.
- `voxera queue cancel|retry|pause|resume|unlock|prune|reconcile`.

The panel exposes equivalent surfaces under `/missions/create`,
`/missions/templates/create`, `/queue/create`, `/jobs/{id}`, and
`/queue/approvals/{ref}/...`.
