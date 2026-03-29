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
  into `vera_web/checklist_rendering.py` (or equivalent).
- Keep route orchestration and submit/handoff logic in place.
- Preserve function signatures via import aliases where needed.

### PR-3: Extract Vera execution-mode classifier helpers

- Move non-I/O lane classifier helper cluster into `vera_web/execution_mode.py`.
- Keep final mode decision wiring in `app.py` initially.
- Re-run characterization tests to prove no lane-truth regressions.

### PR-4: Extract panel presentation formatting helpers

- Move pure `_format_*`, `_history_*`, evidence/policy row-builder helpers into
  `panel/view_models.py` (or equivalent).
- Keep current route and security wiring untouched.

### PR-5: Extract panel auth-state storage helpers

- Move `_prune_panel_auth_maps`, `_panel_auth_state_*`, `_active_lockout_until_ms`
  into dedicated `panel/security_state.py`.
- Maintain identical snapshot keys and semantics.
- Add explicit tests for `VOXERA_HEALTH_PATH` and queue-root precedence behavior.

### PR-6: Extract CLI queue-files command family

- Move `queue files *` commands and enqueue helper into `cli_queue_files.py`.
- Keep top-level `cli_queue.register(...)` composition as single truth for app wiring.

### PR-7: Extract CLI health/reconcile/prune command clusters

- Move health and hygiene clusters to dedicated modules with stable output contract.
- Preserve command names/options/help text and JSON output schema.

### PR-8: Final hotspot slimming pass (bounded)

- After prior seams stabilize, perform small import-graph cleanup.
- Update architecture docs and ownership map.
- Explicitly defer any architecture redesign or semantic behavior changes to later milestones.

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
