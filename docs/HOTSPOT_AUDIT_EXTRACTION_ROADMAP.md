# Hotspot audit and extraction roadmap (bounded prep PR)

## Scope and intent

This document is a **bounded audit** of the current high-complexity hotspot modules:

- `src/voxera/vera_web/app.py`
- `src/voxera/panel/app.py`
- `src/voxera/cli_queue.py`

It is intentionally **not** a feature PR and **not** a broad refactor PR.

Goals of this document:

1. Map responsibilities inside each hotspot file.
2. Identify truth-critical coupling and risky seams.
3. Propose a safe, incremental extraction order for follow-up PRs.
4. Reduce regression risk while preserving current runtime behavior.

### Audit grounding method (current snapshot)

This roadmap is grounded in the current repository code by reviewing top-level seams and route/command composition points in each hotspot:

- Vera web: `ExecutionMode`, `_classify_execution_mode`, `_submit_handoff`, `chat`, `chat_updates`, `handoff`
- Panel app: auth/guard seams (`_require_operator_basic_auth`, `_require_mutation_guard`), health-backed panel auth state helpers, job detail payload shaping, route registration block
- CLI queue: command registration root (`register`) plus queue-files, approvals, health/reset, prune/reconcile command families

These symbol anchors are intentionally called out so follow-up PRs can preserve behavior while moving code.

## Trust-model invariants (must remain unchanged)

Any extraction PR following this roadmap must preserve these invariants:

- Vera can reason, draft, and explain, but execution remains queue-governed.
- Queue is the execution boundary for side effects.
- Artifacts/evidence and queue state remain the truth surfaces.
- Ambiguous or unsafe behavior fails closed.
- No silent shortcut paths around policy/approval/evidence.

## Hotspot 1: `src/voxera/vera_web/app.py`

### Responsibility clusters

1. **Web composition and request surfaces**
   - FastAPI app setup, template/static wiring.
   - Route handlers: `/`, `/chat`, `/chat/updates`, `/handoff`, `/clear`.

2. **Turn classification and execution-mode policy**
   - `ExecutionMode` and classification helpers decide whether a turn is:
     - conversational checklist artifact lane, or
     - governed preview lane.
   - This is a policy-rich decision seam with high regression impact.

3. **Preview/draft guardrails and mutation interpretation**
   - Submission-claim guardrails, false-preview-claim checks, active preview mutation heuristics,
     writing refinement and code-draft intent checks.
   - Protects truthful UX surfaces and preview integrity.

4. **Conversational checklist rendering lane**
   - Extract/list/sanitize/render checklist content for answer-first requests.
   - Enforces artifact rendering and strips queue/preview language where inappropriate.

5. **Vera service orchestration bridge**
   - Calls into `vera.service`, `vera.preview_submission`, `vera.draft_revision`,
     `vera.evidence_review`, `vera.handoff`, and related session helpers.
   - Also manages linked completion surfacing and session state writes.

6. **Voice-foundation input gating**
   - Input-origin normalization and guarded transcript ingestion behavior.

### Truth-critical paths

- Execution mode classification and lane lock (`CONVERSATIONAL_ARTIFACT` vs `GOVERNED_PREVIEW`).
- Preview submission truthfulness (`_submit_handoff`, submission-claim guardrails).
- Active preview content/path mutation integrity (fail-closed on ambiguity).
- Conversational lane sanitization to prevent false submit/preview claims.

### High-churn areas

- Intent-heuristic helpers for nuanced user phrasing.
- Checklist extraction/cleanup logic.
- Preview mutation interpretation and near-miss submit handling.
- Session enrichment/handoff follow-up shaping.

### Existing nearby helpers to leverage

- `src/voxera/vera/draft_revision.py`
- `src/voxera/vera/preview_submission.py`
- `src/voxera/vera/evidence_review.py`
- `src/voxera/vera/handoff.py`
- `src/voxera/vera/service.py`

### Safest early extraction candidates

1. Conversational checklist extraction/render helpers.
2. Pure text/claim sanitization helpers.
3. Non-I/O execution-mode helper cluster (classification predicates).

### Areas to avoid splitting first

- `chat` route orchestration block itself (first keep a single transaction view).
- submit/handoff glue that coordinates queue/session truth and linked job registration.

## Hotspot 2: `src/voxera/panel/app.py`

### Responsibility clusters

1. **App composition root**
   - FastAPI setup, static/templates, shared constants, route-module registration.

2. **Panel auth + CSRF + mutation guardrail mechanics**
   - Basic auth enforcement, CSRF helpers, GET mutation blocking,
     lockout/failure counters keyed by client IP.

3. **Health snapshot integration for security counters**
   - prune/update/read of panel auth maps through health snapshot storage,
     including queue-root vs isolated-health-path behavior.

4. **Job/queue presentation shaping**
   - detail payload construction, timeline assembly, artifact summaries,
     policy rationale/evidence summary rows.

5. **Queue mutation utilities and hygiene command bridge**
   - write queue jobs/mission jobs, invoke hygiene/reconcile operations,
     persist hygiene result outputs.

6. **Assistant degraded-mode bridge and dependency wiring**
   - async degraded-answer generation with controlled route-assistant wiring.

### Truth-critical paths

- Auth/CSRF/mutation guards (operator security boundary).
- Health-backed auth state correctness (lockout integrity).
- Job detail payload/evidence summaries displayed to operators.

### High-churn areas

- UI-centric payload shaping and helper formatting.
- Hygiene and recovery view payload evolution.
- Assistant degraded-mode context/result formatting.

### Existing nearby helpers to leverage

- `src/voxera/panel/routes_*.py` modules already own route families.
- `src/voxera/panel/helpers.py` for request parsing coercion.
- `src/voxera/core/queue_inspect.py`, `queue_result_consumers.py`, health modules.

### Safest early extraction candidates

1. Pure formatting/presentation helpers (`_format_*`, `_history_*`, row builders).
2. Job artifact/evidence summary helpers.
3. Panel security state helpers into a dedicated module while preserving call graph.

### Areas to avoid splitting first

- Cross-cutting auth enforcement call sites until helper interfaces are stable.
- Combined job-detail composition function until characterization coverage is expanded.

## Hotspot 3: `src/voxera/cli_queue.py`

### Responsibility clusters

1. **CLI composition and subcommand registration**
   - Typer app tree (`queue`, `queue approvals`, `queue lock`, `queue files`, `inbox`, `artifacts`).

2. **Queue files governed helper commands**
   - `queue files find/grep/tree/copy/move/rename` with shared enqueue helper.

3. **Queue lifecycle operator commands**
   - init/status/health/health reset/cancel/retry/pause/resume/unlock.

4. **Approvals and inbox flows**
   - list/approve/deny approvals and inbox add/list.

5. **Hygiene and reconciliation flows**
   - `queue prune`, `queue reconcile`, quarantine fix reporting.

6. **Incident bundle/artifact tooling**
   - bundle export and artifact prune operations.

### Truth-critical paths

- Commands that mutate queue state (`cancel`, `retry`, `pause`, `resume`, approvals).
- `queue health reset` scope handling and user-facing truth output.
- Reconcile/prune commands that can move/delete operator data.

### High-churn areas

- Human-facing status table/report formatting.
- Queue files helper command surface as capabilities expand.
- Reconcile/prune UX details and structured JSON output.

### Existing nearby helpers to leverage

- `src/voxera/cli_common.py`
- queue core modules under `src/voxera/core/`
- health/bundle helper modules already imported by this file.

### Safest early extraction candidates

1. `queue files` command family into dedicated `cli_queue_files.py`.
2. `queue health*` commands into dedicated `cli_queue_health.py`.
3. Reconcile/prune/reporting helpers into `cli_queue_hygiene.py`.

### Extraction progress notes

- A bounded helper extraction moved low-risk queue payload-building and argument-normalization
  helpers (queue-files payload shaping + health-reset event/log payload shaping) out of
  `cli_queue.py` into a focused helper module while intentionally keeping CLI registration,
  command contract ownership, and final enqueue/queue-boundary calls in `cli_queue.py`.
- This reduces hotspot density without moving high-sensitivity command wiring seams yet.
- The `queue files` command-family handlers (find, grep, tree, copy, move, rename) have been
  extracted from `cli_queue.py` into `src/voxera/cli_queue_files.py`. The extracted module
  owns `queue_files_app`, `_enqueue_files_step`, `_print_files_enqueue_result`, and all six
  command handlers. Top-level CLI registration (`queue_app.add_typer(queue_files_app, ...)`)
  and the `register()` composition root remain in `cli_queue.py`. CLI contract, command
  names, options, defaults, and help text are unchanged.
- The `queue health` and `queue health-reset` command-family handlers have been extracted
  from `cli_queue.py` into `src/voxera/cli_queue_health.py`. The extracted module owns
  `queue_health` and `queue_health_reset` (full handler implementations). Both are
  registered to `queue_app` in `cli_queue.py` via `queue_app.command(...)(fn)`. Top-level
  CLI registration and `register()` remain in `cli_queue.py`. CLI contracts unchanged.
- The `queue prune`, `queue reconcile`, and `artifacts prune` command-family handlers have
  been extracted from `cli_queue.py` into `src/voxera/cli_queue_hygiene.py`. The extracted
  module owns `queue_prune`, `queue_reconcile`, and `artifacts_prune` (full handler
  implementations including reporting, config-override resolution, and JSON output
  formatting). All three are registered from `cli_queue.py` via
  `queue_app.command(...)(fn)` / `artifacts_app.command(...)(fn)`. Top-level CLI
  registration and `register()` remain in `cli_queue.py`. CLI contracts unchanged.
- The `queue bundle` command handler has been extracted from `cli_queue.py` into
  `src/voxera/cli_queue_bundle.py`. The extracted module owns `queue_bundle` (full handler
  implementation including job/system bundle dispatch, `BundleError` handling, and output
  writing). Registration of the command to `queue_app` remains in `cli_queue.py` via
  `queue_app.command("bundle")(queue_bundle)`, placed before the first `@queue_app.command`
  decorator to preserve subcommand help ordering. Top-level CLI registration and
  `register()` remain in `cli_queue.py`. CLI contracts (command name, option names,
  defaults, help text) are unchanged.
- A bounded characterization pass now anchors the remaining truth-sensitive
  `cli_queue.py` surfaces in tests: `queue status`, lifecycle commands (`cancel`, `retry`,
  `pause`, `resume`, `unlock`), approvals commands (`list`, `approve`, `deny`), inbox
  commands (`add`, `list`), plus root command-tree shape assertions for queue approvals,
  queue lock, and top-level inbox wiring.
- The `queue approvals` command-family handlers (`list`, `approve`, `deny`) have been
  extracted from `cli_queue.py` into `src/voxera/cli_queue_approvals.py`. The extracted
  module owns `queue_approvals_list`, `queue_approvals_approve`, and `queue_approvals_deny`
  (full handler implementations including approval resolution, fail-closed FileNotFoundError
  handling, and rich table rendering). All three are registered to `queue_approvals_app` in
  `cli_queue.py` via `queue_approvals_app.command(...)(fn)`. Top-level CLI registration,
  `queue_approvals_app` ownership, and public CLI contract ownership remain in
  `cli_queue.py`. CLI contracts (command names, option names, defaults, help text) are
  unchanged.
- The `inbox` command-family handlers (`add`, `list`) have been extracted from
  `cli_queue.py` into `src/voxera/cli_queue_inbox.py`. The extracted module owns
  `inbox_add` and `inbox_list` (full handler implementations including atomic job creation,
  goal validation, fail-closed error handling, and rich table rendering with missing-dir
  hints). Both are registered to `inbox_app` in `cli_queue.py` via
  `inbox_app.command(...)(fn)`. Top-level CLI registration, `inbox_app` ownership, and
  public CLI contract ownership remain in `cli_queue.py`. CLI contracts are unchanged.

### Areas to avoid splitting first

- The top-level `register(...)` and command group wiring should remain centralized until
  post-extraction import cycles are verified.

## Coupling and risk matrix (priority order)

1. **Vera execution-mode + preview truth coupling** (highest risk)
   - Mis-split risk: accidental preview language leakage or false submission claims.

2. **Panel security/auth state + health snapshot coupling**
   - Mis-split risk: lockout drift, CSRF/auth bypass regressions, misleading counters.

3. **CLI queue lifecycle commands + queue core contract coupling**
   - Mis-split risk: operator command semantics drift from daemon/object-model truth.

4. **Panel job detail shaping + artifact/evidence schema coupling**
   - Mis-split risk: misleading operator truth surfaces and triage confusion.

## Recommended PR-by-PR extraction order (risk-reduction first)

### PR-1: Add characterization test anchors for hotspot seams

- Add/expand targeted tests for:
  - Vera lane classification invariants and submission-claim guardrails.
  - Panel auth/CSRF/GET-mutation guard behaviors and lockout state transitions.
  - CLI queue mutation command invariants (status, approvals, health-reset scopes).
- No production code motion yet; establish safety rails.

### PR-2: Extract pure Vera conversational checklist helpers

- Move checklist parsing/rendering/sanitization pure functions from `vera_web/app.py`
  into `vera_web/conversational_checklist.py`.
- Keep route orchestration and submit/handoff logic in place.
- Preserve behavior via thin `app.py` wrappers/import aliases where needed.

### PR-3: Extract Vera execution-mode classifier helpers

- Move non-I/O lane classifier helper cluster into `vera_web/execution_mode.py`.
- Keep final mode decision wiring in `app.py` initially.
- Re-run characterization tests to prove no lane-truth regressions.
- Status: completed by extracting execution-mode predicates/classifier helpers into
  `src/voxera/vera_web/execution_mode.py` with thin `app.py` delegation wrappers,
  while leaving submit/handoff/state-write truth-critical boundaries in `app.py`.

### PR-4: Extract panel presentation formatting helpers

- Move pure `_format_*`, `_history_*`, evidence/policy row-builder helpers into
  `panel/view_models.py` (or equivalent).
- Keep current route and security wiring untouched.
- Status: completed by extracting low-risk panel job presentation/status helper
  functions from `src/voxera/panel/app.py` into
  `src/voxera/panel/job_presentation.py` with thin import delegation in
  `app.py`, while leaving route wiring, auth/mutation guards, canonical loading,
  and final truth-critical job-detail assembly ownership in `app.py`.

### Completed follow-on seam (between PR-3 and PR-4)

- Vera preview-body/content-binding helper predicates were extracted from
  `src/voxera/vera_web/app.py` into `src/voxera/vera_web/preview_content_binding.py`.
- Scope remained intentionally bounded to low-risk helper predicates:
  placeholder-body rejection, control-narration body rejection, and targeted
  code-preview content-refinement detection.
- Final preview/session write ownership, canonical submit/handoff path ownership,
  and linked-completion/session truth ownership remained in `app.py`.

### PR-5: Extract panel auth-state storage helpers

- Move `_prune_panel_auth_maps`, `_panel_auth_state_*`, `_active_lockout_until_ms`
  into dedicated `panel/auth_state_store.py`.
- Maintain identical snapshot keys and semantics.
- Add explicit tests for `VOXERA_HEALTH_PATH` and queue-root precedence behavior.
- Status: completed by extracting low-risk panel auth-state storage/cleanup/bookkeeping
  helpers into `src/voxera/panel/auth_state_store.py` with thin update/read wrappers
  kept in `app.py`; final auth/mutation enforcement, final route wiring, and final
  security-boundary decisions remain owned by `app.py`.

### PR-5b: Extract panel job-detail section assembly helpers

- Move low-risk panel job-detail section assembly from `panel/app.py` into
  `panel/job_detail_sections.py` (operator summary, policy rationale, evidence summary,
  why-stopped, recent timeline composition).
- Keep route wiring, auth/mutation guards, canonical queue/artifact/state loading, and
  final truth-critical job-detail payload ownership in `panel/app.py`.
- Status: completed with behavior-preserving extraction and existing characterization
  anchors retained for operator outcome precedence and job-detail rendering semantics.

### PR-6: Extract CLI queue-files command family

- Move `queue files *` commands and enqueue helper into `cli_queue_files.py`.
- Keep top-level `cli_queue.register(...)` composition as single truth for app wiring.
- Status: completed. `queue_files_app`, `_enqueue_files_step`, `_print_files_enqueue_result`,
  and all six command handlers extracted into `src/voxera/cli_queue_files.py`.
  `queue_app.add_typer(queue_files_app, name="files")` and `register()` remain in
  `cli_queue.py`. CLI contracts (names, options, defaults, help) are unchanged.

### PR-7: Extract CLI health/reconcile/prune command clusters

- Move health and hygiene clusters to dedicated modules with stable output contract.
- Preserve command names/options/help text and JSON output schema.
- Status (health): `queue health` and `queue health-reset` command-family handlers extracted
  from `src/voxera/cli_queue.py` into `src/voxera/cli_queue_health.py`. The extracted module
  owns `queue_health` and `queue_health_reset` handler functions (full implementations
  including the snapshot-with-sections builder, the rich-table render helper, and the
  health-reset audit log emission). Both handlers are registered to `queue_app` from
  `cli_queue.py` via `queue_app.command("health")(queue_health)` and
  `queue_app.command("health-reset")(queue_health_reset)`. Top-level CLI registration,
  public CLI contract ownership, and the `register()` composition root remain in
  `cli_queue.py`. CLI contracts (command names, option names, defaults, help text) are
  unchanged.
- Status (hygiene): `queue prune`, `queue reconcile`, and `artifacts prune` command-family
  handlers extracted from `src/voxera/cli_queue.py` into `src/voxera/cli_queue_hygiene.py`.
  The extracted module owns all three handler functions (`queue_prune`, `queue_reconcile`,
  `artifacts_prune`) including their full render/reporting logic and config-override
  resolution. All three are registered from `cli_queue.py` via
  `queue_app.command("prune")(queue_prune)`, `queue_app.command("reconcile")(queue_reconcile)`,
  and `artifacts_app.command("prune")(artifacts_prune)`. Top-level CLI registration,
  public CLI contract ownership, and the `register()` composition root remain in
  `cli_queue.py`. CLI contracts (command names, option names, defaults, help text, JSON
  output schemas) are unchanged. `cli_queue.py` reduced from 909 to 533 lines.

### PR-7c: Extract CLI bundle command handler

- Move `queue bundle` handler into `cli_queue_bundle.py`.
- Keep registration, public contract ownership, and `register()` composition root in `cli_queue.py`.
- Status: completed. `queue_bundle` handler extracted into `src/voxera/cli_queue_bundle.py`.
  Registered to `queue_app` from `cli_queue.py` via `queue_app.command("bundle")(queue_bundle)`.
  Command tree shape, help ordering, option names, defaults, and help text are unchanged.
  Remaining in `cli_queue.py`: top-level app wiring, init, status, lifecycle
  (cancel/retry/unlock/pause/resume), approvals (list/approve/deny), inbox (add/list).

### PR-8: Final hotspot slimming pass (bounded)

- After prior seams stabilize, perform small import-graph cleanup.
- Update architecture docs and ownership map.
- Explicitly defer any architecture redesign or semantic behavior changes to later milestones.

### Recommended next queue extraction after characterization pass

- Precondition status: satisfied for remaining `cli_queue.py` operator surfaces.
- Completed: approvals + inbox family extraction (this PR).
- Recommended bounded next PR: extract one command family at a time while keeping
  `register()` and command-tree ownership in `cli_queue.py`:
  1. lifecycle command extraction next (cancel/retry/unlock/pause/resume),
  2. `queue status` extraction last due to densest operator-truth rendering logic.

## Dependency and sequencing notes

- Do **not** extract submit/handoff core from `vera_web/app.py` before checklist/execution-mode seams are stable.
- Do **not** touch panel auth enforcement call order in same PR as helper extraction.
- Do **not** combine CLI module splits with behavioral option changes.
- Keep each extraction PR under a bounded diff with explicit before/after ownership notes.
- Keep public route paths and CLI command names/options/help text stable during seam extraction PRs unless the PR is explicitly contract-changing.

## Contributor checklist for follow-up extraction PRs

Each follow-up PR should include:

1. Explicit statement: behavior-preserving extraction only.
2. Updated ownership docs for moved seams.
3. Characterization test updates proving no truth-surface drift.
4. Evidence that queue boundary and fail-closed semantics remain unchanged.
