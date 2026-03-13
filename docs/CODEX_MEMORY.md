## 2026-03-13 — GitHub PR #TBD — feat(vera/compiler): improve active-preview semantic refinement while keeping strict JSON mutations

- Improved deterministic active-preview refinement interpretation for fluent follow-up language focused on `write_file.content`, `write_file.path`, and `write_file.mode`.
- Added semantic content refinement support for phrases like summary/news and formal-tone rewrites, while preserving fail-closed behavior for ambiguous references (for example `put that into the file` when `that` is ungrounded).
- Kept compiler contract strict: only preview mutation decisions (`replace_preview`, `patch_preview`, `no_change`) with valid preview JSON; no submission/runtime claims added.
- Added focused tests in hidden compiler + Vera web flows covering semantic content updates, fail-closed ambiguous references, and active-preview refinement stability.

## 2026-03-12 — GitHub PR #TBD — feat(vera/verifier): harden lifecycle-aware evidence-grounded review shaping

- Hardened Vera evidence review output shaping so "what happened?" responses are more deterministic and lifecycle-aware while remaining additive.
- Review summary selection now prefers normalized execution contract fields first (`review_summary.latest_summary`, then `evidence_bundle.review_summary.latest_summary`) before legacy fallback summaries.
- Review responses now surface normalized artifact/evidence context (`artifact_families`, `artifact_refs`, and selected `evidence_bundle.trace` fields) when available.
- Lifecycle-specific state handling and next-step guidance were expanded (`submitted`, `planning`, `running`, `awaiting_approval`, `resumed`, terminal outcomes), preserving fail-closed semantics.
- Structured execution consumers now expose additive `artifact_families`/`artifact_refs` passthrough for downstream reviewers.

## 2026-03-12 — GitHub PR #TBD — feat(queue): normalize execution artifact/evidence contract surfaces

- Added additive normalized contract fields to `execution_result.json`:
  - `artifact_families`
  - `artifact_refs`
  - `review_summary`
  - `evidence_bundle` (with `trace` linkage)
- Kept runtime behavior stable: queue lifecycle, approvals, capability enforcement, and terminal semantics are unchanged.
- Updated structured execution consumer helpers so reviewer-facing summary fallback can use `review_summary.latest_summary` when present.
- Updated canonical docs (README/ops/architecture/queue object model/execution security/prompt capability docs/CODEX) to keep artifact/evidence terminology aligned.

## 2026-03-12 — GitHub PR #TBD — feat(prompts): capability prompt docs + runtime composition

- Added core capability prompt docs under `docs/prompts/capabilities/` (preview schema, queue lifecycle, artifacts/evidence, handoff/submit, web investigation).
- Added hidden-compiler-specific payload guidance doc with schema/refinement/truth-discipline examples and stronger role boundaries.
- Added runtime prompt composition loader (`src/voxera/prompts.py`) with deterministic shared -> role -> capability ordering.
- Wired major prompt surfaces to composed markdown docs (Vera system prompt, hidden compiler prompt, planner preamble default path).
- Added focused tests for loader behavior, deterministic composition, hidden compiler rich bundle inclusion, and runtime integration hooks.

## 2026-03-10 — GitHub PR #160 — chore(vera/ops): add first-class startup commands and user-service integration

- Promoted Vera to a first-class runtime component in operations tooling.
- Added dedicated Make targets for foreground Vera startup plus service lifecycle wrappers (`vera`, `vera-start`, `vera-stop`, `vera-restart`, `vera-status`, `vera-logs`).
- Added `deploy/systemd/user/voxera-vera.service` with deterministic repo-venv startup command (`.venv/bin/python -m uvicorn ...`) on `127.0.0.1:8790`.
- Updated `make services-install`/`services-*` flows so default user-service stack now includes daemon + panel + Vera.
- Updated README/ops/architecture/Ubuntu testing docs so operators can run Vera locally, manage it with systemd user services, inspect logs/status, and treat daemon+panel+Vera as the standard runtime stack.
- Non-goals preserved: no Vera feature-surface expansion, no queue semantics changes, no auth redesign, no orchestration redesign.

## 2026-03-09 — GitHub PR #157 — feat(files/queue): structured file-write content support

- Added a narrow governed queue contract: payloads can include `write_file` (`path`, `content`, optional `mode`).
- Queue execution now preserves explicit filename/content and builds a single `files.write_text` mission on existing policy/approval/execution rails.
- Canonical artifacts now carry structured write intent/evidence (`execution_envelope.request.write_file`, plus step/execution results).
- Vera preview normalization now accepts and drafts contentful file-write payloads, enabling honest preview-to-queue handoff for this capability.

## 2026-03-09 — GitHub PR #155 — feat(vera): add evidence-aware job review and follow-up previewing

- Added a narrow Vera evidence-review path for explicit job IDs or latest submitted session job (`handoff_job_id`) in the standalone Vera web app flow.
- Reused canonical queue truth surfaces through shared helpers (`lookup_job`, `resolve_structured_execution`) so Vera summaries align with panel/queue evidence contracts.
- Vera now summarizes lifecycle/outcome/approval/latest/failure/child summary fields conservatively and proposes evidence-grounded next steps.
- Added bounded follow-up behavior: when explicitly asked, Vera drafts a new preview from evidence but never auto-submits it.
- Reinforced Vera system prompt language to prefer canonical evidence and avoid invented outcomes under ambiguity.
- Added focused Vera web tests for latest/specific job review, awaiting approval/success/failure/missing evidence handling, and follow-up draft-without-submit behavior.

## 2026-03-09 — GitHub PR #154 — feat(vera): improve natural-language action detection and preview preparation for VoxeraOS handoff

- Summary:
  - Expanded Vera handoff phrase normalization to cover broader conversational action variants for URL navigation (`open/go to/visit/take me to/bring up/...`) with deterministic URL normalization to `open https://...` previews.
  - Added explicit file-read phrase normalization for inspect/show/open/read variants when file target is explicit enough.
  - Added basic note/file-write phrase normalization for supported request forms while preserving smallest valid preview payload shape.
  - Expanded explicit handoff trigger phrase support (`submit/hand off/send/queue/enqueue/push through/...`) while keeping no-preview submits fail-closed and honest.
  - Preserved session preview durability across follow-up turns and rolling turn caps; preview replaces cleanly when a newer action request is drafted.
  - Refined preview wording to be more natural while preserving strict truth labels (prepared vs submitted vs executed).
- Non-goals preserved:
  - No execution semantics change.
  - No policy/approval bypass.
  - No direct side effects from chat.
  - No orchestration/workflow expansion.

## 2026-03-09 — GitHub PR #152 — feat(vera): minimal chat web app with session context + VoxeraOS-only execution boundary

- Summary:
  - Follow-up refinement: moved Vera v0 to a standalone web app (`voxera.vera_web.app`) intended for a separate port from the operator panel, with a single-pane chat UI and bottom composer.
  - Added lightweight per-session rolling context (`notes/queue/artifacts/vera_sessions/*.json`) with deterministic cap (`MAX_SESSION_TURNS=8`) for short back-and-forth continuity.
  - Added a dedicated Vera system prompt (`src/voxera/vera/prompt.py`) defining identity/personality, strict Vera↔VoxeraOS boundary, queue framing, and execution-truthfulness states.
  - Wired Vera chat generation through existing brain/provider stack (OpenAI-compatible + Gemini adapters), with clean degraded responses when providers are unavailable.
  - Enforced preview-only default behavior: normal Vera chatting does not enqueue queue jobs and does not claim side effects.
  - Added explicit Vera→VoxeraOS handoff path: action-shaped requests draft structured job JSON previews, and explicit submit routes through queue inbox with honest queue acknowledgement.
  - Added small internal drafting guide/examples for supported minimal job JSON (`{"goal": "..."}` + optional supported fields only).
  - Added DEV-mode diagnostics support in standalone Vera UI (prompt/session debug visibility) and explicit clear-chat/context control (`/clear`) for iterative development workflows.
- Non-goals preserved:
  - No direct tool execution from Vera chat.
  - No approvals/policy changes.
  - No queue lifecycle mutation except existing paths.
  - No voice, streaming, multimodal, long-term memory, or orchestration/autonomy features.

## 2026-03-09 — GitHub PR #150 — feat(panel/progress): read-only parent child status rollups

- Summary:
  - Added additive `child_summary` rollups for parent jobs that expose `child_refs`.
  - Rollups are computed from canonical child job evidence and normalized as: `total`, `done`, `pending`, `awaiting_approval`, `failed`, `canceled`, `unknown`.
  - Surfaces updated: structured execution payload, `/jobs/{id}/progress`, and panel job detail (`Child Summary` block).
- Guarantees:
  - Observability-only: no parent waiting, no dependency semantics, no result/context aggregation, no approval semantics changes.

## 2026-03-09 — GitHub PR #149 — feat(queue): controlled child enqueue primitive with deterministic lineage

- Summary:
  - Added a narrow, explicit child-enqueue primitive: queue payloads may include `enqueue_child: {goal, title?}` to request one child job from a successfully completing parent execution.
  - Child lineage (`parent_job_id`, `root_job_id`, `orchestration_depth`, incremented, `sequence_index`, `lineage_role=child`) is computed server-side from sanitized parent lineage. User-supplied lineage overrides inside the child payload are rejected.
  - Validation is strict and fail-closed: `enqueue_child` must be a plain object with only the allowed keys (`goal`, `title`); non-object payloads, empty goals, extra keys, and nested `enqueue_child` structures are all rejected with no child written.
  - Child is written as a normal `inbox/child-*.json` queue job and enters the full queue lifecycle including policy, approvals, and fail-closed semantics — no parent approval gate is bypassed.
  - Evidence surfaces: `artifacts/<parent>/child_job_refs.json`, `artifacts/<parent>/actions.jsonl` (`queue_child_enqueued` event), `artifacts/<parent>/execution_result.json` (`child_refs`), job progress `child_refs`, and panel job detail `Child Jobs` section.
  - This is not a workflow engine: no dependency graph, no parent/child result passing, no autonomous decomposition, and no approval bypass.
- Why it matters:
  - Provides a governed, observable, single-step child orchestration surface for use cases that genuinely need to queue follow-on work from within an execution — while preserving every existing safety guarantee.
  - Server-side lineage computation prevents lineage spoofing via crafted payloads.
- Validation:
  - `ruff format --check .` ✓
  - `ruff check .` ✓
  - `mypy src/voxera` ✓
  - `pytest -q` ✓
  - `make security-check` ✓
  - `make validation-check` ✓

## 2026-03-09 — GitHub PR #148 — feat(queue): descriptive lineage metadata for jobs and surfaces

- Summary:
  - Added additive, descriptive lineage metadata to the queue contract: `parent_job_id`, `root_job_id`, `orchestration_depth`, `sequence_index`, and optional `lineage_role` (`root` / `child`).
  - When present, lineage is surfaced in `plan.json`, `execution_envelope.json`, `execution_result.json`, job progress payloads (`/jobs/{id}/progress`), and panel job detail views.
  - Lineage metadata is observational only: it does not change execution behavior, approvals, fail-closed semantics, scheduling, or context passing between jobs.
  - Missing or malformed values are sanitized and omitted without affecting execution.
- Why it matters:
  - Provides the observability foundation for tracking job family relationships in the panel and in artifacts without introducing any orchestration coupling or widening any authority surface.
  - Additive design means all existing jobs and operator surfaces remain unaffected.
- Validation:
  - `ruff format --check .` ✓
  - `ruff check .` ✓
  - `mypy src/voxera` ✓
  - `pytest -q` ✓
  - `make validation-check` ✓

## 2026-03-08 — GitHub PR #146 — feat(panel): live job progress endpoints and UI polling

- Summary:
  - Added `GET /jobs/{job_id}/progress` and `GET /assistant/progress/{request_id}` endpoints that return shaped lifecycle/step/approval metadata sourced exclusively from canonical queue artifacts (no speculative states).
  - Panel job detail pages (`/jobs/<job_id>`) and assistant pages (`/assistant`) now use progressive enhancement: server-rendered first (works without JavaScript); with JavaScript, pages poll every ~2s and refresh only evidence-backed fields.
  - Fixed stale failure-context shaping bug: resolved job progress no longer surfaces stale failure summaries for terminal success states.
  - Preserved `intent_route` metadata in done-job progress payloads so operators can inspect routing decisions after completion.
  - Live fields: `terminal_outcome`, `lifecycle_state`, `intent_route`, `lineage`, `child_refs`, `step_summaries`, `approval_status`, `blocked`, `retryable`, `execution_lane`, `fast_lane`.
  - Non-goals preserved: no speculative percentages, no bypass of approvals/policy/fail-closed routing, no parallel truth source outside queue artifacts/contracts.
- Why it matters:
  - Operators can observe job lifecycle transitions in real time without refreshing pages or polling CLI tools.
  - Progressive enhancement means panel remains fully functional for operators who prefer static views or restricted environments.
- Validation:
  - `ruff format --check .` ✓
  - `ruff check .` ✓
  - `mypy src/voxera` ✓
  - `pytest -q` ✓
  - `make validation-check` ✓

## 2026-03-08 — GitHub PR #147 — security(red-team): adversarial regression pack + multi-boundary hardening + `security-check` CI gate

- Summary:
  - Added `tests/test_security_redteam.py` with deterministic adversarial coverage for: simple-intent hijack resistance, planner first-step mismatch fail-closed rejection, notes/path traversal escape attempts, approval-gated pending-state correctness, and progress/evidence consistency for terminal success/failure shaping.
  - Uncovered and fixed traversal metadata leakage: traversal-style paths (for example `../`) in `read_file` goals were producing deterministic extracted targets in intent metadata; fixed so traversal-style goals produce no `extracted_target` at any artifact boundary.
  - Hardened classifier boundary: `_contains_parent_traversal()` guard prevents traversal-shaped phrasing from creating actionable routing shortcuts.
  - Hardened serializer boundary: `sanitize_serialized_intent_route()` strips potentially unsafe field values at the serialization layer so they cannot escape into artifacts, sidecars, or state writes.
  - Hardened runtime boundary: traversal target metadata is not surfaced in envelope, plan, or sidecar artifacts even when extracted during classification.
  - Hardened sidecar boundary: `_simple_intent` is sanitized before writing to failed sidecar and state files so boundary violations do not leak through failure paths.
  - Added `make security-check` and wired it into both `make validation-check` and `make merge-readiness-check` so adversarial regressions are first-class merge gates.
- Why it matters:
  - Red-team regressions are now deterministic and merge-blocking. Any future change that weakens intent classification, serialization, or artifact boundaries will surface as a `security-check` failure before merge.
  - The multi-boundary hardening closed a traversal leakage path where metadata about unsafe path inputs could propagate through artifacts into operator surfaces.
- Validation:
  - `ruff format --check .` ✓
  - `ruff check .` ✓
  - `mypy src/voxera` ✓
  - `pytest -q` ✓
  - `make security-check` ✓
  - `make golden-check` ✓
  - `make validation-check` ✓
  - `make merge-readiness-check` ✓
  - Added `make security-check` and wired it into both `make validation-check` and `make merge-readiness-check`.
  - Updated operator docs (README/architecture/ops/roadmap/ubuntu testing) to describe scope, expectations, and interpretation of `security-check` failures as regressions in trust guarantees rather than new features.
- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`
  - `make security-check`
  - `make golden-check`
  - `make validation-check`
  - `make merge-readiness-check`

## 2026-03-08 — GitHub PR #145 — fix(intent): narrow deterministic open-intent routing + remove terminal demo hijacks

- Split deterministic open routing into `open_terminal`, `open_url`, and `open_app` and added compound first-step metadata (`compound_action`, `first_step_only`, `first_action_intent_kind`, `trailing_remainder`).
- Added meta/help/explanatory guards so quoted/discussed/how/why phrasing does not trigger action execution.
- Tightened skill families: `open_terminal` => `system.open_app`; `open_url` => `system.open_url`; `open_app` => `system.open_app`; `run_command` no longer allows `system.terminal_run_once`.
- Removed deterministic terminal hello-world planning shortcut and updated planner preamble to avoid demo injection.
- Updated `system.terminal_run_once` semantics to open a plain terminal only (no hello-world/canned command bootstrap).
- Why it matters:
  - Narrowing open-intent routing prevents accidental or adversarial phrasing from triggering execute-type actions when the goal is explanatory or meta (e.g. "tell me how to open a terminal" must never execute).
  - Compound first-step metadata (`first_step_only`, `first_action_intent_kind`, `trailing_remainder`) keeps valid multi-step goals like "open terminal and run X" constrained at step 1 without discarding the remainder.
  - Fail-closed: URL presence alone does not route to `open_url`; ambiguous open phrasing stays `unknown_or_ambiguous`.


## 2026-03-08 — PR #144 STV follow-up 3 — feat(intent): deterministic read routing + extracted_target + artifact consistency

**Root issues fixed (STV findings, three distinct problems):**

### A. `read_file` classifier gap ("read the file ~/path" → unknown_or_ambiguous)
- **Failure observed**: goal `"read the file ~/VoxeraOS/notes/pr144-read-target.txt"` was
  classified as `unknown_or_ambiguous` → `fail_closed=False` → planner produced `clipboard.copy`
  as first step → job succeeded with a synthetic fallback string (semantically wrong).
- **Root cause**: `_RE_READ_VERB` pattern `read\s+[~/]` required the path immediately after the
  verb — articles "the file" between verb and path broke the match.
- **Fix**: expanded `_RE_READ_VERB` to match all forms:
  `read [the] [file] ~/path`, `open and read ~/path`, `cat ~/path`, `display ~/path`, `view ~/path`,
  `show contents of ~/path`.  Goals without a `~/` or `/` path (e.g. "read this and copy it",
  "read the document") still fall through to `unknown_or_ambiguous`.

### B. Deterministic target extraction + direct routing
- **New field**: `SimpleIntentResult.extracted_target: str | None` — set for `read_file` (the
  exact path from goal) and `write_file` with "called `<name>`" suffix (candidate notes-root path).
- **Direct routing in `mission_planner`**: for `read_file` and named `write_file` goals with a
  safe notes-root path, `plan_mission()` now skips the cloud brain entirely and returns a
  single-step deterministic plan:
  - `_extract_simple_read_args()` → `files.read_text` step
  - `_extract_named_file_write_args()` → `files.write_text` step (empty text, creates the file)
- **Fail-closed fallback**: if extraction fails or the path is outside the notes root, falls
  through to cloud brain; the mismatch check acts as the safety net.

### C. Artifact consistency — `intent_route` now in `execution_result.json` for ALL goal-kind jobs
- **Previous bug**: `execution_result.json → intent_route` was only populated on mismatch;
  for successful goal-kind jobs it was `null`, inconsistent with `execution_envelope.json →
  request.simple_intent`.
- **Fix**: `queue_execution.py` now calls `rr.data.setdefault("intent_route", simple_intent.to_dict())`
  after evaluation, propagating the classification to `execution_result.json` for all outcomes
  (success, terminal failure, pending approval).

**Files changed:**
- `src/voxera/core/simple_intent.py`: expanded `_RE_READ_VERB`, new `_RE_READ_PATH`,
  `_RE_WRITE_CALLED`; `extracted_target` field on `SimpleIntentResult`; updated `to_dict()`
- `src/voxera/core/mission_planner.py`: `_RE_PLANNER_READ_PATH`, `_extract_simple_read_args()`,
  `_RE_PLANNER_WRITE_CALLED`, `_extract_named_file_write_args()`; deterministic read + named-write
  routes in `plan_mission()` before cloud brain candidates
- `src/voxera/core/queue_execution.py`: `rr.data.setdefault("intent_route", ...)` propagation

**Regression tests added** (13 new, total 694 passed):
- Classifier unit: `test_read_the_file_path`, `test_read_the_file_extracted_target`,
  `test_read_path_bare_extracted_target`, `test_open_and_read_path`, `test_read_file_path`,
  `test_write_file_called_extracted_target`, `test_create_file_called_extracted_target`,
  `test_write_without_called_has_no_extracted_target`, `test_read_this_and_copy_it_is_unknown`,
  `test_read_without_leading_path_is_unknown`
- Integration: `test_read_the_file_path_succeeds`, `test_read_the_file_path_clipboard_fails_closed`,
  `test_intent_route_present_in_execution_result_on_success`
- Updated: `test_open_terminal_routes_to_terminal_run_once_succeeds` (now asserts `intent_route`
  in `execution_result.json`), `test_ambiguous_request_not_forced_into_wrong_route` (same)

**Validation**: ruff ✓, mypy ✓, pytest 694 passed, 2 skipped ✓.

## 2026-03-08 — PR #144 follow-up 2 — fix(intent): close write_file classifier gap for "create a file called X" goals

- **Production failure reproduced**: goal "create a file called whatupboy.txt" (or any goal
  starting with "create a/an/new/empty file ...") was classified as `unknown_or_ambiguous`
  because `_RE_WRITE_VERB` matched `create\s+file` (literal "create file") but not
  "create a file", "create a new file", or "create an empty file".  With no constraint
  applied, the planner could produce any first step — including `system.terminal_run_once` —
  and the job would succeed without mismatch detection.
- **Root cause (verified)**: `re.match(r"^\s*(?:...|create\s+file)\b", "create a file called x")`
  returns None because the article "a" between "create" and "file" breaks the match.
- **Fix**: Updated `_RE_WRITE_VERB` in `simple_intent.py` to
  `create\s+(?:(?:a|an|new|empty)\s+)*file\b`.  Non-file "create" goals ("create an
  application", "create a task") still fall through to `unknown_or_ambiguous`.
- **Also confirmed**: "write a file called whatupboy.txt" was always correctly classified as
  `write_file`; the subtle gap was the "create a file" variant.
- **Panel and CLI paths behave identically**: the simple_intent classification runs on the
  normalized payload for ALL goal-kind jobs regardless of origin (panel vs CLI vs direct inbox).
- **Regression tests added** (7 new, total 681 passed):
  - `test_create_file_called_name`, `test_create_a_new_file` (classifier unit)
  - `test_create_application_is_unknown`, `test_create_task_is_unknown` (classifier unit)
  - `test_write_file_terminal_run_once_is_mismatch` (mismatch unit)
  - `test_write_file_called_terminal_run_once_fails_closed` (integration, queue goal path)
  - `test_create_file_called_panel_payload_fails_closed` (integration, panel payload path)
- Validation: ruff ✓, mypy ✓, pytest 681 passed ✓.

## 2026-03-08 — PR #144 follow-up — fix(intent): refine open_resource terminal route and document clipboard.copy rejection

- **STV findings addressed (PR #144)**:
  - `pr144-open-terminal`: planner produces `system.terminal_run_once` for "open terminal" goals,
    which was incorrectly rejected because `_OPEN_SKILLS` only included `system.open_app` /
    `system.open_url`.
  - `pr144-read`: planner safety rewrite (PR #23) converts non-explicit sandbox.exec steps to
    `clipboard.copy`; this is correctly rejected by the mismatch guard (fail-closed, expected
    behavior) — `clipboard.copy` is **not** a valid substitute for `files.read_text`.
- **Fix**: added `_TERMINAL_OPEN_SKILLS = frozenset({"system.terminal_run_once", "system.open_app"})`.
  The `"open terminal"` exact-match branch now returns `allowed_skill_ids=_TERMINAL_OPEN_SKILLS`
  instead of `_OPEN_SKILLS`, accepting both `system.open_app` and `system.terminal_run_once` as
  valid first steps.  Other `open_resource` goals (single-word app name, URL) still use
  `_OPEN_SKILLS` only.
- **`INTENT_ALLOWED_SKILLS["open_resource"]`** updated to the union of both sets for documentation
  accuracy; the classifier returns refined per-goal subsets.
- **Regression tests added** (`tests/test_simple_intent.py`):
  - `test_open_terminal_terminal_run_once_no_mismatch` (unit)
  - `test_read_intent_clipboard_copy_is_mismatch` (unit)
  - `test_open_terminal_routes_to_terminal_run_once_succeeds` (integration)
  - `test_read_file_clipboard_copy_fails_closed_regression` (integration)
- **Docs updated**: ARCHITECTURE.md intent table now shows the terminal sub-route separately;
  ops.md documents the refined routing and explicit clipboard.copy rejection.
- Validation: ruff ✓, mypy ✓, pytest (all tests pass including 4 new regression tests) ✓.

## 2026-03-08 — PR #TBD — feat(intent): deterministic simple-intent routing and fail-closed planner mismatch detection

- Added `src/voxera/core/simple_intent.py` — a small, deterministic classifier for common
  operator goal strings.  No NLP, no external dependencies; pure regex + frozenset.
- Intent set (v1): `assistant_question`, `open_resource`, `write_file`, `read_file`,
  `run_command`, `unknown_or_ambiguous`.
- Skill-family allowlists per intent (e.g. `write_file` → only `files.write_text`).
- `classify_simple_operator_intent(goal=...) → SimpleIntentResult` — returns intent kind,
  determinism flag, allowed skill IDs, routing reason, and fail_closed flag.
- `check_skill_family_mismatch(intent, first_step_skill_id) → (bool, reason)` — compares
  planner's first step against the intent's allowed family.
- Integrated into `QueueExecutionMixin.process_job_file` for goal-kind requests:
  1. Classifies intent before the planning loop, stashes on payload as `_simple_intent`.
  2. Emits `queue_simple_intent_routed` action event.
  3. After planning, checks first-step skill vs allowed family.
  4. If mismatch: emits `queue_simple_intent_mismatch`, writes canonical failure artifacts,
     moves job to failed **before any skill execution** (fail closed).
- Error codes: `simple_intent_skill_family_mismatch`, `planner_intent_route_rejected`.
- Additive artifact extensions:
  - `execution_envelope.json`: `request.simple_intent` (intent kind, determinism, allowed IDs)
  - `execution_result.json`: `intent_route` dict (full mismatch evidence)
  - `plan.json` + `plan.attempt-<n>.json`: `intent_route` metadata
  - `actions.jsonl`: `queue_simple_intent_routed` and `queue_simple_intent_mismatch` events
- `unknown_or_ambiguous` goals pass through to normal planning with no constraint.
- Classifier is conservative: only classifies when obviously matching (single-word app names,
  explicit path prefixes for read, write verb prefix, etc.).
- Added `tests/test_simple_intent.py` with 62 tests covering classifier, mismatch detection,
  and integration through the queue daemon (including regression tests for all mismatch patterns).
- Validation: ruff format ✓, ruff check ✓, mypy ✓, pytest 670 passed ✓, golden-check ✓,
  validation-check ✓, merge-readiness-check ✓.

## 2026-03-08 — PR 4 — planner-executor-evaluator loop with bounded replan

- Added `src/voxera/core/execution_evaluator.py` for deterministic post-attempt outcome classification.
- Added bounded evaluate-and-replan loop in `QueueExecutionMixin.process_job_file(...)` with
  `max_replan_attempts` (default `1`) and explicit `queue_job_replanned` action/log events.
- Replan eligibility is fail-closed: only retryable/replannable classes and goal-planned jobs;
  approval pending + policy/capability blocks remain non-replan terminal/pause states.
- Extended canonical artifacts additively with attempt/evaluation metadata:
  - `execution_envelope.json`: `attempt_index`, `replan_count`, `max_replans`, `supersedes_attempt`
  - `plan.json` + `plan.attempt-<n>.json`: attempt lineage + compact `plan_delta`
  - `execution_result.json`: `attempt_index`, `replan_count`, `max_replans`, `evaluation_class`,
    `evaluation_reason`, `stop_reason`
- Added focused tests for evaluator taxonomy, replan-allowed and replan-forbidden outcomes, and
  max-attempt stop behavior.
- Follow-up fix: normalized planner unknown-skill failures and runtime missing-skill lookups into structured outcomes so bounded replan is exercisable end-to-end (`plan.attempt-1` planning_error -> bounded attempt 2).

## 2026-03-08 — PR #TBD — harden(exec): strict argv/path boundaries for execution skills
- Summary:
  - Hardened sandbox command normalization: reject ambiguous shell-control operators in string commands, reject empty/whitespace argv tokens, and emit canonical structured blocked-input payloads in `PodmanSandboxRunner`.
  - Added centralized `src/voxera/skills/path_boundaries.py` and wired `files.read_text` / `files.write_text` to deterministic confined-path checks (traversal/symlink/out-of-root blocked fail-closed).
  - Hardened local execution surfaces: `system.open_app` now rejects unsafe identifiers + emits canonical result payloads; `system.open_url` now rejects hostless or credential-embedded URLs.
  - Expanded tests for accepted/rejected argv and path cases plus structured error payload expectations.

## 2026-03-07 — PR #TBD — feat(queue): enrich planner-produced jobs with canonical structured intent
- Summary:
  - Added `src/voxera/core/queue_job_intent.py` to centralize additive producer-side queue intent shaping (`job_intent`) from mission/goal/assistant payloads with deterministic normalization and legacy-tolerant defaults.
  - Updated producer entrypoints (`core/inbox.py`, `panel/app.py`, `panel/assistant.py`) to attach structured `job_intent` hints when enqueuing work.
  - Updated daemon normalization/envelope flow to derive `job_intent` for legacy jobs, include intent in `execution_envelope.json`, and persist additive `artifacts/<job>/job_intent.json`.
  - Added focused tests for canonical intent shaping, producer emission paths, and backward-compatible execution contract propagation.
- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`
  - `make golden-check`
  - `make validation-check`
  - `make full-validation-check`

## 2026-03-07 — PR #TBD — feat(queue): consume canonical structured step results in operator surfaces and recovery flows
- Added `src/voxera/core/queue_result_consumers.py` with thin composable structured-first resolution helpers used by queue consumers.
- Updated panel job detail payload/template to prefer canonical structured execution fields (step summaries, operator note, next action hint, machine payload, retryable/blocked/approval hints, output artifacts) while preserving legacy fallback behavior.
- Updated queue CLI/daemon status surfaces to prefer structured lifecycle and failure summaries when available.
- Updated ops bundle job export to include a structured execution summary note derived from canonical artifacts with safe fallback.
- Added focused tests for structured-first + legacy fallback behavior across helper, panel, CLI queue status, daemon failed snapshot, and ops bundle surfaces.

## 2026-03-07 — PR TBD — feat(core): canonical queue execution envelope + structured step results
- Added `src/voxera/core/queue_contracts.py` to centralize queue execution contract shaping:
  - canonical `execution_envelope.json` builder for normalized queue jobs.
  - structured per-step result shaping for success/failure/approval/assistant paths.
  - `execution_result.json` builder for deterministic machine-readable terminal summaries.
- Updated queue execution + assistant lanes to persist additive artifacts under `artifacts/<job_stem>/`:
  - `execution_envelope.json`
  - `step_results.json`
  - `execution_result.json`
- Expanded mission step runtime output in `missions.py` to include per-step timestamps/duration and machine payload passthrough used by structured step results.
- Added focused contract tests in `tests/test_queue_execution_contracts.py` and updated ops bundle coverage to include new result artifact inclusion.

## 2026-03-07 — PR #TBD — hardening(ci): add golden operator surface checks and contract validation workflow
- Summary:
  - Added deterministic golden operator-surface tooling in `tools/golden_surfaces.py` and committed baselines under `tests/golden/` for high-value CLI surfaces: root help, queue help subcommands (`status`, `approvals`, `reconcile`, `prune`, `health`), doctor help, and normalized empty `queue health --json` output.
  - Added targeted golden framework tests in `tests/test_golden_surfaces.py` for help normalization, JSON deterministic normalization (timestamps + path placeholders), and drift failure behavior.
  - Added explicit Make targets `make golden-update` and `make golden-check`, and wired `golden-check` into `make validation-check` as the canonical merge-confidence flow.
  - Synced README/architecture/ops/roadmap docs to distinguish goldens vs snapshot/contract tests and document contributor usage expectations for update/check workflows.
- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q tests/test_golden_surfaces.py -vv`
  - `pytest -q tests/test_cli_contract_snapshot.py -vv`
  - `pytest -q tests/test_operator_contract_guardrails.py -vv`
  - `pytest -q tests/test_queue_daemon_contract_snapshot.py -vv`
  - `pytest -q tests/test_cli_queue.py -vv`
  - `pytest -q tests/test_doctor.py -vv`
  - `pytest -q`
  - `make golden-check`
  - `make validation-check`
  - `make full-validation-check`
- Follow-ups:
  - Consider adding a dedicated CI job step that runs `make golden-check` independently for faster drift diagnostics, while retaining `validation-check` composition.
- Risks/notes:
  - Hardening-only pass: runtime behavior and operator contracts remain unchanged; determinism is handled in test tooling normalization.

## 2026-03-07 — PR #TBD — refactor(cli): finish thin composition root split for voxera.cli

## 2026-03-07 — PR #130 — harden(validation): canonical validation pipeline + operator contract guardrails
- Summary:
  - Added canonical validation targets in `Makefile`: `make validation-check` (standard) and updated `make full-validation-check` (release-grade) to compose standard validation, merge-readiness/release checks, failed-sidecar guardrail tests, full pytest, and `scripts/e2e_golden4.sh`.
  - Added focused operator-facing contract guardrail tests for queue health JSON required fields, assistant response artifact schema keys/version, ops bundle system manifest fields, and config snapshot payload shape; also added CLI compatibility export continuity assertions for monkeypatch surfaces (`log`, `tail`, `console`, `get_version`, `_git_sha`, `load_runtime_config`, `MissionQueueDaemon`).
  - Updated README/architecture/ops docs to document the hardening validation ritual and the specific operator-visible contracts now protected by tests/snapshots.
- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q tests/test_queue_daemon.py tests/test_queue_daemon_contract_snapshot.py -vv`
  - `pytest -q tests/test_cli_queue.py tests/test_doctor.py tests/test_cli_contract_snapshot.py -vv`
  - `pytest -q tests/test_operator_contract_guardrails.py -vv`
  - `make validation-check`
  - `make full-validation-check`
  - `bash scripts/e2e_golden4.sh`
- Follow-ups:
  - None.
- Risks/notes:
  - Hardening-only pass: no intended operator-visible behavior changes.

- Summary:
  - Completed the final CLI cleanup pass by extracting remaining feature-heavy command logic from `src/voxera/cli.py` into focused modules: `cli_config.py`, `cli_skills_missions.py`, `cli_ops.py`, and `cli_runtime.py`.
  - Kept `src/voxera/cli.py` as the thin public composition/registration root that owns the Typer app, root callback/version handling, command/group registration order, and compatibility surfaces required by tests/monkeypatching.
  - Preserved command/group names, help text, options, defaults, JSON output shapes, and runtime behavior.
- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`
  - Focused CLI suites (`test_cli_queue.py`, `test_doctor.py`, `test_cli_queue_reconcile.py`, `test_cli_queue_prune.py`, `test_cli_contract_snapshot.py`, `test_cli_version.py`).
- Follow-ups:
  - None.
- Risks/notes:
  - Compatibility symbols intentionally remain reachable from `voxera.cli` (including `log`, `console`, `get_version`, `_git_sha`, `load_runtime_config`, `MissionQueueDaemon`).

## 2026-03-06 — PR #TBD — docs: sync documentation to current codebase architecture
- Summary:
  - Performed documentation reality-sync pass against the codebase after the recent architecture refactor wave (PRs #116–#124).
  - Updated `docs/ARCHITECTURE.md`: expanded queue module map with per-file ownership descriptions, added CLI module map with `cli.py`/`cli_common.py`/`cli_queue.py`/`cli_doctor.py` boundaries, expanded panel route module map with path/method ownership, added "Architectural Pattern: Thin Composition Root + Focused Domain Modules" section explicitly documenting the pattern used across queue/panel/CLI, updated queue lifecycle section to name artifact types and module owners explicitly.
  - Updated `README.md`: expanded "What works now" queue section to list all 7 queue submodule files with ownership, added panel and CLI modularization as completed milestone bullets, added operator assistant advisory lane as a completed bullet.
  - Updated `docs/ops.md`: added "Contributor guidance: where code belongs" section documenting queue/panel/CLI extension points.
  - Updated `docs/CODEX_MEMORY.md`: backfilled PR numbers and added missing entries for PRs #119–#124 (queue_state, queue_approvals, queue_assistant, queue_execution, queue_recovery, CLI modularization).
- Validation:
  - Docs reviewed against live source code for accuracy.
  - No runtime behavior changed.
- Follow-ups:
  - None.
- Risks/notes:
  - Documentation-only; no code changes in this pass.

## 2026-03-06 — PR #124 — refactor(cli): modularize CLI command registration into focused modules
- Summary:
  - Extracted queue/operator command implementations from `src/voxera/cli.py` into `src/voxera/cli_queue.py`, which owns `queue_app`, `queue_approvals_app`, `queue_lock_app`, `inbox_app`, and `artifacts_app` Typer sub-apps and all their command implementations.
  - Extracted doctor command wiring into `src/voxera/cli_doctor.py` with a `register(app)` function called from the root CLI.
  - Extracted shared CLI helpers/primitives/options/constants into `src/voxera/cli_common.py` (`console`, `RUN_ARG_OPTION`, `OPS_BUNDLE_ARCHIVE_DIR_OPTION`, `SNAPSHOT_PATH_OPTION`, `DEMO_QUEUE_DIR_OPTION`, `now_ms()`, `queue_dir_path()`).
  - `src/voxera/cli.py` remains the Typer composition/registration root; imports and registers sub-apps from `cli_queue` and doctor from `cli_doctor`. Preserved all command/group names, help surfaces, defaults, option flags, and operator-facing behavior as stable contracts.
  - Added lint fix to preserve `log` monkeypatch surface in `cli.py`.
- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`
- Follow-ups:
  - None.
- Risks/notes:
  - Operator-visible CLI surface (command names, option flags, JSON shapes) remains stable across the refactor. Monkeypatch compatibility surfaces preserved in `cli.py`.

## 2026-03-06 — PR #123 — refactor(queue): extract mission execution pipeline mixin
- Summary:
  - Extracted mission execution/process pipeline from `src/voxera/core/queue_daemon.py` into `src/voxera/core/queue_execution.py` as `QueueExecutionMixin`.
  - `QueueExecutionMixin` owns: inbox filtering (`_is_ready_job_file`, `_is_primary_job_json`), payload normalization (`_normalize_payload`), parse-retry behavior (`_load_job_payload_with_retry`), mission building/planning integration (`_build_mission_for_payload`, `_build_inline_mission`), `process_job_file(...)` (full queued→planning→running→pending/done/failed flow), `process_pending_once(...)`.
  - `queue_daemon.py` remains the orchestration root and still owns lock handling, tick loop, and high-level lane routing.
- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`
- Follow-ups:
  - None.
- Risks/notes:
  - `_PARSE_RETRY_ATTEMPTS` and `_PARSE_RETRY_BACKOFF_S` constants remain in `queue_daemon.py` and are accessed via `_queue_daemon_module()` from `queue_execution.py` to preserve monkeypatch compatibility.

## 2026-03-06 — PR #122 — refactor(core): extract queue startup recovery + shutdown handling
- Summary:
  - Extracted startup recovery and shutdown/in-flight deterministic failure handling from `src/voxera/core/queue_daemon.py` into `src/voxera/core/queue_recovery.py` as `QueueRecoveryMixin`.
  - Moved recovery scanning/quarantine/report assembly helpers (`recover_on_startup`, orphan approval/state collection, `_detected_inflight_pending_jobs`, `_collect_orphan_approval_files`, `_collect_orphan_state_files`, `_quarantine_startup_recovery_path`).
  - Moved shutdown helpers (`request_shutdown`, `_record_clean_shutdown`, `_record_failed_shutdown`, `_finalize_job_shutdown_failure`) while preserving health/audit/failed-sidecar semantics.
  - Kept `queue_daemon.py` as orchestration root (lock handling, process loop, planning/routing, lifecycle transitions).
  - Updated docs (`README.md`, `docs/ops.md`, `docs/ARCHITECTURE.md`) to reflect the new boundary and future refactor guidance.
- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`
  - `pytest -q tests/test_queue_daemon.py tests/test_queue_daemon_contract_snapshot.py`
  - `bash scripts/e2e_golden4.sh`

## 2026-03-06 — PR #121 — refactor(core): extract assistant advisory queue lane
- Summary:
  - Extracted assistant advisory queue lane from `src/voxera/core/queue_daemon.py` into `src/voxera/core/queue_assistant.py` as module-level functions (not a mixin).
  - `queue_assistant.py` owns: `process_assistant_job(daemon, job_path, payload)` (main advisory job handler), `create_assistant_brain(provider)` (provider construction), `assistant_brain_candidates(cfg)` (ordered primary/fallback candidate list), `assistant_answer_via_brain(...)` (advisory answer path with primary/fallback sequencing), `assistant_response_artifact_path(daemon, job_ref)` (artifact path helper), advisory failure handling (writes failed artifact + moves to failed/), thread persistence via `operator_assistant` helpers (`append_thread_turn`, `read_assistant_thread`).
  - Preserved advisory lifecycle states (`advisory_running` → `done`/`step_failed`) and all audit event semantics.
- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`
- Follow-ups:
  - None.
- Risks/notes:
  - Advisory lane uses module-level functions rather than a mixin because it operates with access to the daemon instance passed explicitly, which suits a function boundary better than class inheritance.

## 2026-03-06 — PR #120 — refactor(core): extract queue approval workflow and artifact handling
- Summary:
  - Extracted approval workflow mechanics from `src/voxera/core/queue_daemon.py` into `src/voxera/core/queue_approvals.py` as `QueueApprovalMixin`.
  - `QueueApprovalMixin` owns: approval prompt/grant logic (`_queue_approval_prompt`), approval artifact path/read/write helpers (`_read_approval_artifact`, `_write_pending_artifacts`, `_approval_target`), pending approval payload building, normalization/canonicalization of approval refs (`canonicalize_approval_ref`, `_resolve_pending_approval_paths`, `_approval_ref_candidates`, `_approval_ref_variants`), approval grants/approve-always behavior (`grant_approval_scope`, `_has_approval_grant`, `_read_grants`, `_write_grants`), approval resolution behavior (`resolve_approval`), pending approval notifications (`_notify_pending_approval`), hard approval gate (`_ensure_hard_approval_gate`).
  - Preserved all approval artifact contracts (`*.approval.json`, `*.pending.json`) and `pending_approvals_snapshot()` public surface.
- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`
- Follow-ups:
  - None.
- Risks/notes:
  - `_AUTO_APPROVE_ALLOWLIST = {"system.settings"}` and `_APPROVAL_GRANTS_FILE = "grants.json"` are constants internal to `queue_approvals.py`.

## 2026-03-06 — PR #119 — refactor(core): extract queue daemon state persistence and helpers
- Summary:
  - Extracted `*.state.json` sidecar path/read/write/update helpers from `src/voxera/core/queue_daemon.py` into `src/voxera/core/queue_state.py`.
  - `queue_state.py` owns: `job_state_sidecar_path()`, `read_job_state()`, `write_job_state()`, `update_job_state_snapshot()`. Schema version: `JOB_STATE_SCHEMA_VERSION = 1`.
  - Also extracted `move_job_with_sidecar()` and `deterministic_target_path()` into `src/voxera/core/queue_paths.py`.
  - `queue_paths.py` owns: `move_job_with_sidecar()` (atomic rename + co-move of `*.state.json` sidecar with collision-safe naming), `deterministic_target_path()` (suffix-tag-based collision-safe target naming).
  - `queue_daemon.py` imports and delegates to these helpers; sidecar co-move behavior and state semantics are preserved.
- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`
- Follow-ups:
  - None.
- Risks/notes:
  - `read_job_state`, `update_job_state_snapshot`, `write_job_state` are re-exported from `queue_daemon.py` for backward compatibility.

## 2026-03-06 — PR #118 — refactor(panel): extract remaining route domains from app.py
- Completed final panel modularization pass: extracted assistant, missions, bundle, and queue-control route domains from `panel/app.py` into `routes_assistant.py`, `routes_missions.py`, `routes_bundle.py`, and `routes_queue_control.py` while preserving route/method/auth/csrf contracts.
- Route ownership:
  - `routes_assistant.py`: `GET /assistant`, `POST /assistant/ask` (with degraded advisory fallback logic)
  - `routes_missions.py`: `GET/POST /missions/templates/create`, `GET/POST /missions/create`
  - `routes_bundle.py`: `GET /jobs/{job_id}/bundle`, `GET /bundle/system`
  - `routes_queue_control.py`: `POST /queue/jobs/{ref}/delete`, `POST /queue/pause`, `POST /queue/resume`
- Kept `panel/app.py` as composition/wiring root (FastAPI setup, shared security + queue helpers, dependency wiring, route registration), reducing domain-heavy inline route logic.
- Updated README/ops/architecture docs with final panel module layout and guidance to add future panel work in domain modules instead of regrowing `app.py`.

## 2026-03-06 — PR #117 — refactor(panel): modularize hygiene + recovery route domains
- Extracted panel hygiene routes from `panel/app.py` into `panel/routes_hygiene.py` (`/hygiene`, `/hygiene/prune-dry-run`, `/hygiene/reconcile`, `/hygiene/health-reset`) while preserving auth/csrf/flash/reset semantics and response contracts.
- Extracted panel recovery routes from `panel/app.py` into `panel/routes_recovery.py` (`/recovery`, `/recovery/download/{bucket}/{name}`) while preserving read-only listing, traversal protections, ZIP limits, and download behavior.
- Kept `panel/app.py` as FastAPI composition/wiring (setup + shared helpers + route registration), and updated README/ops/architecture docs to reflect ownership boundaries for future panel changes.

## 2026-03-06 — PR #116 follow-up — fix(panel): keep jobs mutation redirects relative for proxy safety
- Fixed regression in `routes_jobs._jobs_redirect`: switched redirect target from absolute `request.url_for("jobs_page")` URL back to relative `/jobs?...`.
- Preserved existing query semantics (`flash`, `bucket`, `q`, sanitized/clamped `n`).
- Added panel regression test asserting mutation redirect `Location` is relative (origin-safe for proxied/front-door deployments).

## 2026-03-06 — PR #116 — refactor(panel): modularize app.py by route domain + shared helpers
- Split panel structure into route-domain modules while preserving public contract: extracted `routes_home.py` (home + queue create) and `routes_jobs.py` (jobs list/detail + approvals + cancel/retry), with shared request/int parsing helpers in `helpers.py`.
- Kept `panel/app.py` as the unchanged public FastAPI entrypoint and composition/wiring layer; route paths/methods/auth guards remain contract-equivalent.
- Updated README/ops/architecture docs with the new panel ownership boundaries and extension guidance.

## 2026-03-05 — PR TBD — Fail fast on unknown keys for operator-facing configuration models
- Summary
  - Hardened operator-facing app config contracts by forbidding unknown fields on `AppConfig`, `BrainConfig`, `PolicyApprovals`, and `PrivacyConfig`.
  - Added explicit tests for valid config loading and unknown-key rejection at top-level and nested config levels.
  - Improved `load_app_config` error surfacing with an operator-focused hint for unknown keys/typos in `config.yml`.
  - Kept volatile/internal payload models (for example planner/runtime payload models like `PlanStep`) permissive for staged rollout compatibility.
- Validation
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`


## 2026-03-04 — PR #N/A — test: isolate health snapshot writes during pytest (surgical fix)
- Summary:
  - Corrected `_health_snapshot_path` precedence in `src/voxera/health.py`:
    - **Explicit `queue_root` (not None)**: always returns `queue_root / "health.json"`; `VOXERA_HEALTH_PATH` is **ignored**.  Prevents the env var from hijacking tests that pre-seed their own temp queue directories.
    - **`queue_root=None` (default-path flows)**: honours `VOXERA_HEALTH_PATH` when set, then falls back to `~/VoxeraOS/notes/queue/health.json`.
  - Added `_default_operator_queue_root()` inline helper (no `platformdirs` import) for the None-path fallback.
  - Added `_isolate_health_snapshot` `autouse=True` fixture in `tests/conftest.py`; depends on `_sanitize_voxera_env` for correct ordering.
  - Updated `tests/test_health_snapshot_isolation.py`: replaced old `test_health_writes_go_to_isolated_path_not_queue_root` with `test_explicit_queue_root_wins_over_voxera_health_path`, asserting that explicit queue_root writes land in `queue_root/health.json` and do not modify the VOXERA_HEALTH_PATH file.
  - Updated `tests/test_health.py`: uses `read_health_snapshot()` instead of a direct file read.
  - Updated `docs/ops.md` Testing section with three-level precedence rules.
- Validation:
  - `ruff format .`
  - `ruff check .`
  - `pytest`

## 2026-03-04 — PR #N/A — feat(P3.2): apply brain backoff sleep on repeated brain failures
- Summary:
  - Applied brain backoff at daemon orchestration layer in `src/voxera/core/queue_daemon.py` immediately before queue-driven `plan_mission(...)` attempts.
  - Backoff wait uses `compute_brain_backoff_s(consecutive_brain_failures)` from health snapshot and sleeps only when wait > 0.
  - Added health snapshot fields in `src/voxera/health.py`: `brain_backoff_last_applied_s` (default `0`) and `brain_backoff_last_applied_ts` (default `null`).
  - Added writer helper `record_brain_backoff_applied(...)`; daemon records these fields only when sleep is applied.
  - Chosen policy: when no sleep is applied, keep last-applied values unchanged for operator visibility.
  - Added deterministic tests in `tests/test_queue_daemon.py` (mocked sleep/time, threshold/no-threshold, once-per-attempt) and `tests/test_brain_fallback.py` (defaults + update semantics).
  - Updated docs (`README.md`, `docs/ops.md`, `docs/ROADMAP.md`, `docs/ROADMAP_0.1.6.md`, `docs/SECURITY.md`) to reflect enforced backoff + observability fields.
- Validation:
  - `ruff format .`
  - `ruff check . --fix`
  - `pytest`
  - `make merge-readiness-check`


## 2026-03-04 — PR #N/A — feat(P3.2): compute brain backoff wait from consecutive failures
- Summary:
  - Added deterministic `compute_brain_backoff_s(consecutive_brain_failures)` in `src/voxera/health.py` with ladder semantics: `<3 => 0`, `>=3 => base`, `>=5 => 4*base`, `>=10 => 15*base`, capped by max.
  - Added safe env parsing for `VOXERA_BRAIN_BACKOFF_BASE_S` (default `2`) and `VOXERA_BRAIN_BACKOFF_MAX_S` (default `60`), with invalid values falling back to defaults and negative values clamped to `0`.
  - Extended health snapshot normalization so `brain_backoff_wait_s` is always present and derived from `consecutive_brain_failures`, including normalization of older snapshots missing the new field.
  - Expanded deterministic unit tests in `tests/test_brain_fallback.py` for ladder mapping, cap behavior, env overrides, invalid/negative env handling, and snapshot integration.
  - Updated informational docs (`README.md`, `docs/ops.md`, `docs/ROADMAP.md`, `docs/ROADMAP_0.1.6.md`) to reflect reporting-only backoff computation scope.
- Validation:
  - `ruff format .`
  - `ruff check . --fix`
  - `pytest`
  - `make merge-readiness-check`


## 2026-03-03 — Panel recovery/quarantine inspector (P2.3)
- Added panel `/recovery` read-only inspector for `notes/queue/recovery/` + `notes/queue/quarantine/`.
- Added `/recovery/download/{bucket}/{name}` operator-auth ZIP downloads with traversal protections,
  symlink exclusion, deterministic ordering, and size/file-count safety limits.
- Added panel tests for empty state, listing, ZIP download validity, and traversal rejection.
- Updated docs: README, ops, SECURITY, ROADMAP, ROADMAP_0.1.6.
- Validation commands run: `ruff format .`, `ruff check .`, `pytest`, `make merge-readiness-check`.

## 2026-03-03 — PR #N/A — docs(release): bump version to 0.1.6 + refresh internal docs/roadmap
- Summary:
  - Bumped `pyproject.toml` version from `0.1.5` to `0.1.6`; updated description string.
  - Updated `README.md` title/header and summary paragraphs to reflect v0.1.6 as the current release.
  - Updated `docs/ROADMAP.md`: baseline now "post Alpha v0.1.6"; marked P1.2, P1.3 SHIPPED; added Support/Infra section documenting PR #90 and PR #91; updated milestone section to SHIPPED; archived v0.1.5 completed items.
  - Updated `docs/ROADMAP_0.1.6.md`: status changed from IN PROGRESS to SHIPPED; P1.2 marked SHIPPED; added Support/Infra shipped section; pillar headers 3-6 marked DEFERRED to v0.2; acceptance criteria updated to reflect delivered vs deferred items.
  - Updated `docs/SECURITY.md`: fixed PR references (goal sanitization = PR #85, prompt boundaries = PR #88); updated hardening backlog to move resolved items to "Previously resolved"; added mention of prompt boundaries in goal-hardening known-gaps section.
  - Updated `docs/ops.md`: added Panel Daemon Health widget section with field reference table and data-freshness note; expanded Panel queue hygiene section with reconcile `issue_counts` schema, safety model table, and how-it-works detail.
  - Updated `docs/CODEX_MEMORY.md`: filled in all `PR #N/A` entries with real PR numbers; updated PR #83 → PR #85 for goal sanitization; added new entries for PR #84, PR #86, PR #89.
- Validation:
  - `ruff format .`
  - `ruff check .`
  - `pytest`
  - `make merge-readiness-check`
  - `make release-check`
- Follow-ups:
  - None.
- Risks/notes:
  - Documentation-only and version-surface change; no runtime behavior changed.

## 2026-03-02 — PR #93 — feat(panel): add /hygiene page showing last prune/reconcile + trigger buttons (P2.2)

- What changed:
  - Added Panel `/hygiene` page with two action cards: queue prune (dry-run) and queue reconcile.
  - Added POST endpoints `/hygiene/prune-dry-run` and `/hygiene/reconcile` guarded by operator auth + CSRF mutation guard.
  - Endpoints execute local CLI subprocess commands (`voxera queue prune --dry-run --json`, `voxera queue reconcile --json`), parse JSON, and persist compact results into `notes/queue/health.json` under `last_prune_result` and `last_reconcile_result`.
  - Added minimal JS fetch flow to update summaries in-place without full page reload, including running/disabled states and neutral error banner.
  - Added home quicklink to `/hygiene`.
- Why:
  - Gives operators panel-only queue hygiene observability and safe trigger actions without daemon RPC dependency.
- Tests:
  - Added panel tests for neutral rendering, prune endpoint write path, reconcile endpoint write path, and auth requirements.
- Commands run:
  - `ruff format .`
  - `ruff check .`
  - `pytest`
  - `make merge-readiness-check`

# Codex Memory Log

This file is the single, persistent project memory for Codex-assisted work.

## 2026-03-02 — PR #92 — feat(panel): add home Daemon Health widget sourced from health.json (P2.1)
- Summary:
  - Added a collapsible **Daemon Health** widget on panel home (`/`) using only `read_health_snapshot()` data from `notes/queue/health.json` (no daemon calls), with neutral placeholders for missing fields.
  - Added `_daemon_health_view()` normalization in panel app for lock status/PID/stale age, last fallback, startup recovery, shutdown outcome, and daemon state (`healthy` default).
  - Added panel tests covering empty/minimal health snapshots and populated snapshots, verifying neutral and populated rendering paths.
  - Updated informational docs (README + ROADMAP + SECURITY) so operators can discover the widget and panel-only safety behavior.
- Validation:
  - `ruff format .`
  - `ruff check .`
  - `pytest`
  - `make merge-readiness-check`
- Follow-ups:
  - None.
- Risks/notes:
  - Widget intentionally reflects persisted snapshot state; freshness depends on latest `health.json` writes.

## 2026-03-02 — PR #88 — security(planner): wrap user goal in [USER DATA START]/[USER DATA END] delimiters (P1.2)
- Summary:
  - Added planner prompt boundary constants and wrapped embedded sanitized goal text in a single `[USER DATA START]` / `[USER DATA END]` region.
  - Updated default planner preamble guidance to explicitly treat bounded user-data content as untrusted and non-instructional.
  - Expanded mission planner tests to verify delimiter presence/order/scope and that injection-shaped goal content appears only inside the bounded region.
  - Documented planner prompt boundary hardening in `docs/SECURITY.md` as a complement to sanitization and length caps.
- Validation:
  - `ruff format .`
  - `ruff check .`
  - `pytest`
  - `make merge-readiness-check`
- Follow-ups:
  - None.
- Risks/notes:
  - Prompt boundary hardening is structural defense-in-depth; deterministic runtime validation rules remain unchanged.

## 2026-03-02 — PR #87 — docs(roadmap): sync v0.1.6 with shipped reality + config hygiene planning
- Summary:
  - Synced v0.1.6 roadmap docs from "planning" to "in progress" and added a concise shipped-so-far block for already merged work.
  - Replaced drifting PR-number labels with stable roadmap IDs (`P1.x`..`P6.x`) and tagged scope items as `(SHIPPED)` vs `(PLANNED)`.
  - Added a new planned Provider UX item for config hygiene: auto-upgrade legacy placeholder OpenRouter attribution defaults while preserving real user overrides.
  - Updated v0.1.6 acceptance criteria markers to reflect current reality (`✅` shipped vs `⏳` planned).
- Validation:
  - `ruff format .`
  - `ruff check .`
  - `pytest`
  - `make merge-readiness-check`
- Follow-ups:
  - None.
- Risks/notes:
  - Documentation-only change to reduce plan drift and preserve truthful release tracking.

## 2026-03-02 — PR #85 follow-up — ANSI sequence cleanup + informational docs refresh
- Summary:
  - Tightened planner goal sanitization to remove ANSI/CSI escape remnants (e.g., `\x1b[31m` no longer leaves `[31m` in prompt text).
  - Strengthened mission-planner tests with a direct `sanitize_goal_for_prompt()` assertion and strict expected prompt goal text.
  - Updated informational docs (`README.md`, `docs/ROADMAP.md`) to reflect shipped planner hardening status and remaining Unicode test follow-up.
- Validation:
  - `ruff format .`
  - `ruff check .`
  - `pytest`
  - `make merge-readiness-check`
- Follow-ups:
  - Add Unicode edge-case sanitization tests under planner hardening backlog.
- Risks/notes:
  - ANSI-removal regex is intentionally conservative and scoped to prompt-sanitization output only.

## 2026-03-02 — PR #85 — Planner goal sanitization + 2,000-char preflight cap
- Summary:
  - Added planner goal hardening in `mission_planner`: reject goals over 2,000 chars before any provider selection or brain calls.
  - Added `sanitize_goal_for_prompt()` to remove ASCII control chars and normalize whitespace before embedding user goals in planner prompts.
  - Added mission-planner tests for overlength rejection (with no brain invocation) and prompt sanitization behavior on injection-shaped input.
  - Updated security docs to record the shipped control and retire the previous "planned fix" note.
- Validation:
  - `ruff format .`
  - `ruff check .`
  - `pytest`
  - `make merge-readiness-check`
- Follow-ups:
  - Added structural user-data delimiters in planner prompts as defense-in-depth (shipped in PR #88).
- Risks/notes:
  - Goal sanitization is prompt-scoped; deterministic goal parsing paths intentionally continue using raw input semantics.

## 2026-03-02 — PR #90 — test(e2e): fix approval wait hang in scripts/e2e_golden4.sh
- Summary:
  - Replaced CLI-table-parsing approval detection in `e2e_golden4.sh` with a
    direct filesystem check on the deterministic approval artifact path
    (`pending/approvals/job-e2e-open.approval.json`), mirroring the approach
    already used in `e2e_opsconsole.sh`.
  - Introduced two explicit phases: PHASE A (detect approval state, bounded
    at 120 s) and PHASE B (wait for job lifecycle to advance to done/failed
    after operator panel approval, bounded at 300 s).
  - Added `dump_diag` helper that prints queue status, approvals list, and
    all relevant directory listings on any timeout or failure, giving
    actionable diagnostics without needing to re-run.
  - Fixed the final settle loop: now exits non-zero (exit 1) with a clear
    summary when the 4-job done-count is not reached within 120 s, instead
    of silently falling through.
  - Added `PANEL_PORT` detection via `VOXERA_PANEL_PORT` env var (falling
    back to default 8844) and prints the exact panel URL when approval is
    needed.
  - No production code changed; only `scripts/e2e_golden4.sh` touched.
- Validation:
  - `ruff format --check .` — clean (96 files already formatted).
  - `ruff check .` — All checks passed.
  - `pytest` — 371 passed, 2 skipped.
  - `make merge-readiness-check` — clean.
- Follow-ups:
  - None.
- Risks/notes:
  - The e2e script is now interactive for the approval step: a human must
    approve via the Panel. PHASE B has a 300 s timeout so unattended runs
    fail with diagnostics rather than hanging indefinitely.
  - Filesystem-based checks are resilient to changes in CLI output format or
    approval artifact naming conventions that previously caused hangs.

## 2026-03-02 — PR #89 — security(panel): auth lockout 10/60s → HTTP 429 + Retry-After + health/audit/doctor surfaces (P1.3)
- Summary:
  - Implemented per-IP failed Basic auth tracking in `health.json` under `panel_auth`: `failures_by_ip` (rolling counters) and `lockouts_by_ip` (lockout windows).
  - Policy: `FAIL_THRESHOLD = 10` attempts within `WINDOW_S = 60` seconds triggers a `LOCKOUT_S = 60` second lockout.
  - Panel auth returns HTTP `429` with `Retry-After: 60` header during lockout period; 401 outside lockout.
  - Emits structured `panel_auth_lockout` audit events with `ip`, `attempt_count`, `window_s`, and `lockout_s`.
  - Lockout status surfaced in `voxera queue health` and `voxera doctor --quick` output.
- Validation:
  - `ruff format .`
  - `ruff check .`
  - `pytest`
  - `make merge-readiness-check`
- Follow-ups:
  - None.
- Risks/notes:
  - Tracking is per-IP via health.json; concurrent panel instances on same machine share lockout state via atomic health snapshot write.

## 2026-03-02 — PR #86 — feat(brain): OpenRouter invisible attribution defaults (voxeraos.ca + VoxeraOS)
- Summary:
  - OpenRouter calls now auto-include `HTTP-Referer: https://voxeraos.ca`, `X-OpenRouter-Title: VoxeraOS`, and `X-Title: VoxeraOS` as app attribution metadata by default.
  - Defaults are invisible to users: only applied when the corresponding header keys are absent from the request config.
  - Real user-provided overrides are always respected; defaults never overwrite explicit values.
  - Non-secret metadata; not included in audit redaction.
- Validation:
  - `ruff format .`
  - `ruff check .`
  - `pytest`
  - `make merge-readiness-check`
- Follow-ups:
  - P5.3 (config hygiene auto-upgrade for legacy placeholder defaults) tracked in v0.2 scope.
- Risks/notes:
  - Attribution values are informational metadata for OpenRouter dashboards; no auth or privacy impact.

## 2026-03-02 — PR #84 — feat(skills): terminal_run_once deterministic hello-world demo + deterministic planner route
- Summary:
  - Added `system.terminal_run_once` skill: deterministic terminal demo that runs a hello-world command and exits.
  - Added a deterministic planner route for simple terminal/hello-world goals that bypasses cloud brain calls, producing a predictable single-step plan for offline demo and CI golden tests.
  - Skill registered in the built-in skill registry; planner route gated behind `--deterministic` flag or specific goal patterns.
  - Used in `voxera demo` checklist for a reliable offline-first demo flow.
- Validation:
  - `ruff format .`
  - `ruff check .`
  - `pytest`
  - `make merge-readiness-check`
- Follow-ups:
  - None.
- Risks/notes:
  - Deterministic planner route is intentionally limited in scope; complex goals still go through cloud brain.

## 2026-03-02 — PR #91 — fix(sandbox.exec): canonicalize_argv — accept aliases, shlex.split strings, strip empty tokens, fail fast on empty argv
- Summary:
  - Introduced `canonicalize_argv(args)` in `src/voxera/skills/arg_normalizer.py` as the single source of truth for sandbox command normalisation.
  - Accepts keys in priority order: `command` (canonical), `argv`, `cmd` (compatibility aliases).
  - String values are tokenised with `shlex.split` (no implicit `bash -lc` wrapper).
  - List values: all elements must be `str`; empty/whitespace-only tokens are silently stripped.
  - Raises `ValueError` with an actionable message when the final argv is empty, missing, or contains non-string tokens.
  - Applied in `PodmanSandboxRunner.run()` (execution path) and `canonicalize_args("sandbox.exec")` (SkillRunner pre-flight path) — two-layer defence.
  - Bug symptom fixed: intermittent `RuntimeError('sandbox.exec command must be a non-empty list of strings.')` from planners or tools that emit `argv`/`cmd` aliases or include empty string tokens.
  - Updated `tests/test_execution.py` (new alias/empty-token tests, error-message assertions) and created `tests/test_sandbox_exec_args.py` (33 targeted unit tests for `canonicalize_argv`).
  - Updated `tests/test_mission_planner.py` and `tests/test_queue_daemon.py` for behaviour change: string commands are now shlex-split (not wrapped in `bash -lc`); whitespace-only list tokens are stripped instead of rejected.
  - Docs updated: `README.md` (sandbox.exec input format table + examples), `docs/SECURITY.md` (canonicalize_argv validation contract), `docs/ROADMAP_0.1.6.md` (marked shipped).
- Validation:
  - `ruff format .`
  - `ruff check .`
  - `pytest` (all tests pass)
  - `make merge-readiness-check`
- Follow-ups:
  - None.
- Risks/notes:
  - Behaviour change: string `"echo hello"` is now tokenised to `["echo", "hello"]` (not `["bash","-lc","echo hello"]`). Callers wanting shell interpretation should pass `["bash","-lc","echo hello"]` explicitly. The mission planner already produces list form, so no production regression is expected.
  - Empty/whitespace tokens in lists are silently stripped (previously rejected by `_normalize_sandbox_exec_step`). This is a deliberate robustness choice at the execution layer.

## How to use this file
- Before starting any task, read this file first.
- After every merged PR, append a new entry using the template below.
- Do not rewrite previous entries except to fix factual mistakes.
- Keep entries concise and operational (what changed, why, risks, follow-ups).

## Entry template
```
## YYYY-MM-DD — PR #<number> — <short title>
- Summary:
  - <1-3 bullets of what shipped>
- Validation:
  - <tests/checks run>
- Follow-ups:
  - <open tasks or "none">
- Risks/notes:
  - <migration steps, rollback notes, caveats>
```

## 2026-02-12 — PR #N/A (pre-history) — Introduce persistent Codex memory log
- Summary:
  - Added this canonical memory file for Codex agents to keep merged work history.
  - Linked the file from `README.md` so contributors can find and maintain it.
- Validation:
  - `python -m pytest` (from `voxera-os-scaffold/voxera-os`) passed.
- Follow-ups:
  - Replace `#TBD` with the real PR number after merge.
- Risks/notes:
  - Process-only change; no runtime behavior changed.

## 2026-02-15 — PR #5 — Add cloud-assisted mission planning path
- Summary:
  - Added `voxera missions plan` to let the configured cloud brain draft a mission from a natural-language goal.
  - Added strict planner validation so only known skill IDs and JSON outputs are accepted before execution.
  - Updated mission docs and added root-level `AGENT.md`/`CODEX.md` memory pointers for operator continuity.
- Validation:
  - `pytest -q`
- Follow-ups:
  - Add provider fallback selection for planning (`primary` -> `fast`/`fallback`) when cloud requests fail.
  - Add tests for policy deny + approval rejection paths on cloud-planned missions.
- Risks/notes:
  - Cloud planner quality depends on model behavior; guardrails reject malformed output.

## 2026-02-16 — PR #23 — Rewrite unsafe non-explicit sandbox.exec planner steps
- Summary:
  - Added planner-side safety rewrite for non-explicit goals so `sandbox.exec` steps using host-GUI/sandbox-inappropriate tools (`xdotool`, `wmctrl`, `xprop`, `gdbus`, `curl`, `wget`) are converted into `clipboard.copy` manual confirmation prompts.
  - Kept explicit user shell-command intent intact so command-oriented goals still allow planner `sandbox.exec` output.
  - Updated docs to describe the new planner guardrail behavior and aligned note-path examples with `~/VoxeraOS/notes`.
- Validation:
  - `pytest -q tests/test_mission_planner.py tests/test_queue_daemon.py`
- Follow-ups:
  - Add telemetry/metrics on rewrite frequency to detect planner drift.
- Risks/notes:
  - Intent detection is heuristic and should be monitored for false positives/negatives.


## 2026-02-21 — PR #29 — Queue failed-artifact reliability pass
- Summary:
  - Added a stable failed-sidecar contract with schema versioning (`schema_version=1`) and required fields (`job`, `error`, `timestamp_ms`) plus optional `payload`.
  - Added strict sidecar validation on write/read paths and ensured all queue failure paths emit schema-compliant sidecars.
  - Added deterministic failed-artifact retention pruning that treats primary+sidecar as one logical unit, handles orphans predictably, and supports max-age/max-count while preserving newest failures.
- Validation:
  - `pytest -q tests/test_queue_daemon.py tests/test_cli_queue.py`
- Follow-ups:
  - Consider adding a first-class CLI command to inspect/prune failed retention state.
- Risks/notes:
  - Invalid legacy sidecars are intentionally ignored for status summaries and logged via `queue_failed_sidecar_invalid`.


## 2026-02-21 — PR #34 — Tighten sidecar schema policy + lifecycle smoke coverage
- Summary:
  - Centralized failed-sidecar schema version checks with explicit writer pin (`1`) and reader allowlist (`[1]`).
  - Added deterministic rejection handling for unknown/future sidecar versions while preserving `queue_failed_sidecar_invalid` audit signaling.
  - Added a queue failure lifecycle smoke test validating fail -> sidecar-preferred snapshot -> prune -> empty snapshot behavior.
- Validation:
  - `pytest -q tests/test_queue_daemon.py`
  - `pytest -q tests/test_cli_queue.py`
- Follow-ups:
  - If a future schema bump is needed, update writer pin + reader allowlist together and document migration path before rollout.
- Risks/notes:
  - Mixed-version sidecars now surface deterministically as invalid until compatibility is explicitly added.


## 2026-02-21 — PR #34 — Add failed-sidecar CI guardrail + mixed-version runbook
- Summary:
  - Added a dedicated `make test-failed-sidecar` target that runs the sidecar schema-policy future-version rejection test and lifecycle smoke coverage.
  - Added PR CI workflow `.github/workflows/queue-failed-sidecar.yml` to run the guardrail tests whenever queue-daemon sidecar logic or operator docs are changed.
  - Expanded `docs/ops.md` with a mixed-version incident runbook for `queue_failed_sidecar_invalid` and linked contributor guidance in `README.md`.
- Validation:
  - `make test-failed-sidecar`
- Follow-ups:
  - Mark `queue-failed-sidecar-guardrail` as a required branch protection check on the default branch.
- Risks/notes:
  - Docs include shell snippets for ops triage; keep queue root paths aligned with deployment conventions.


## 2026-02-22 — PR #40 — Strengthen merge-readiness with mypy ratchet, validation tiers, and CI artifacts
- Summary:
  - Added a mypy ratchet utility and committed baseline flow (`scripts/mypy_ratchet.py`, `tools/mypy-baseline.txt`) so new type regressions are blocked while preserving controlled debt burn-down.
  - Split validation tiers into merge-required checks (`make merge-readiness-check`) and broader local validation (`make full-validation-check`), then aligned local pre-push parity through `.pre-commit-config.yaml`.
  - Updated merge-readiness CI to include scripts/tools path triggers, capture quality/release logs, and upload `merge-readiness-logs` artifacts on failure.
- Validation:
  - `make merge-readiness-check`
  - `pytest -q tests/test_mypy_ratchet.py`
  - `make full-validation-check`
- Follow-ups:
  - Add policy controls for baseline-file review ownership and rationale requirements when refreshing `tools/mypy-baseline.txt`.
- Risks/notes:
  - Baseline updates should remain triaged/intentional; avoid using baseline rewrites as a shortcut for unresolved type regressions.

## 2026-02-22 — PR #41 — Strengthen merge-readiness governance, CI summaries, and docs alignment
- Summary:
  - Updated merge-readiness CI to capture quality/release logs under `artifacts/`, publish a concise `$GITHUB_STEP_SUMMARY`, and fail the job if either phase fails.
  - Added baseline governance guidance for `tools/mypy-baseline.txt` refresh/review expectations in both `README.md` and `docs/ops.md`.
  - Added review protection in `.github/CODEOWNERS` for `tools/mypy-baseline.txt` and `scripts/mypy_ratchet.py`, and backfilled roadmap/memory references to reflect completed ratchet + validation-tier + CI-artifact work.
- Validation:
  - `make merge-readiness-check` (initial failure: missing `types-PyYAML` stubs)
  - `pip install types-PyYAML`
  - `make merge-readiness-check` (pass: quality/type and release checks)
- Follow-ups:
  - Keep 30/60/90 roadmap milestones focused on user-visible outcomes while maintaining guardrails as ongoing policy.
- Risks/notes:
  - Baseline refreshes remain review-sensitive; avoid using baseline rewrites to mask unresolved typing regressions.

## 2026-02-22 — PR #42 — Re-scope roadmap cadence to 4/8/12 weeks with delivery enablers
- Summary:
  - Replaced 30/60/90-day roadmap framing with 4/8/12-week milestones better matched to current solo-maintainer delivery pace.
  - Added non-user-visible delivery enablers (CI timing visibility, test reliability growth, release-smoke repeatability, docs/audit hygiene) with reachable targets.
  - Synced roadmap references in `README.md` and `docs/ops.md` to the new week-based cadence and enabler coverage.
- Validation:
  - `git diff -- README.md docs/ROADMAP.md docs/ops.md docs/CODEX_MEMORY.md`
- Follow-ups:
  - Keep enabler targets small and incremental each sprint so user-visible milestones remain primary.
- Risks/notes:
  - Enabler work should not displace product-visible outcomes; use it to reduce delivery friction and regressions.

## 2026-02-22 — PR #N/A — Rebrand to v0.1.4 and lock stability/UX baseline scope
- Summary:
  - Bumped project branding/version references from `0.1.3` to `0.1.4` across package metadata, README, roadmap/testing docs, mission docs, and legal notice.
  - Added `docs/ROADMAP_0.1.4.md` to lock the release scope around reliability, UX polish, observability, and release acceptance criteria.
  - Updated top-level release messaging to position v0.1.4 as a trustworthy daily-driver baseline ahead of broader voice-first expansion.
- Validation:
  - `make release-check`
- Follow-ups:
  - Replace `PR #N/A` with the merged PR number.
- Risks/notes:
  - Version sync is intentionally documentation-first; runtime version is sourced from package metadata and should be released/tagged with matching git state.


## Queue observability surfacing pass (CLI + panel + ops docs)
- Added queue status surfacing for failed-retention policy and latest prune-event summary.
- Exposed the same retention/prune snapshot in panel queue health view.
- Expanded operator and Ubuntu testing docs with direct triage steps for sidecar-invalid + approvals workflows.


## 2026-02-28 — PR #N/A — Full codebase analysis + documentation alignment pass
- Summary:
  - Conducted full codebase analysis (as of 2026-02-28): ~120 source files, ~17k lines Python,
    ~7k lines tests, ~170 git commits. Run `cloc --vcs git` for current counts.
  - Rewrote `docs/ARCHITECTURE.md` from stub (33 lines) to complete reference doc: 3-layer diagram, full
    module map with file-level descriptions, tech stack table, data flow, queue lifecycle diagram,
    config precedence, and validation tiers.
  - Rewrote `docs/ROADMAP.md`: replaced 4/8/12-week milestone blocks with daily/session-sized goals
    calibrated for solo development. Items grouped by area: operational hygiene, observability,
    safety hardening, daemon reliability, planner UX, prompt injection mitigation.
  - Updated `docs/ROADMAP_0.1.4.md`: marked as shipped, documented all completed items,
    added "known gaps carried forward" section to track technical debt items going into v0.2.
  - Expanded `docs/SECURITY.md`: added threat model table with current mitigation status,
    documented all current controls in detail, added "known gaps" section with planned fixes
    cross-referenced to ROADMAP.md daily goals, added prioritized hardening backlog (10 items),
    added operator quick-reference section.
- Validation:
  - Docs reviewed against live source code for accuracy.
  - No runtime behavior changed.
- Follow-ups:
  - Replace `PR #N/A` with merged PR number.
  - Begin Day 1 items from ROADMAP.md: artifact cleanup, `voxera artifacts prune`, `make type-debt`.
- Risks/notes:
  - Process and docs only; no code changes in this pass.

## 2026-03-01 — PR #74 — v0.1.5: artifacts prune + retention CLI
- Summary:
  - Bumped version from 0.1.4 to 0.1.5 in `pyproject.toml`, `README.md`, and docs.
  - Added `voxera artifacts prune` CLI command: dry-run by default, `--yes` to delete, union
    selection policy for `--max-age-days` and `--max-count` flags, `--json` for machine-readable output.
  - Added `artifacts_retention_days` and `artifacts_retention_max_count` to `VoxeraConfig` with
    corresponding env vars (`VOXERA_ARTIFACTS_RETENTION_DAYS`, `VOXERA_ARTIFACTS_RETENTION_MAX_COUNT`).
  - Created `src/voxera/core/artifacts.py` with `prune_artifacts()` pure logic function.
  - Added `docs/ROADMAP_0.1.5.md` (locked scope) and updated `docs/ROADMAP.md` to v0.1.5 baseline.
- Validation:
  - `ruff format src tests && ruff check src tests` — clean.
  - `mypy src/voxera tests` — no new errors beyond baseline.
  - `pytest -q` — all tests pass including 7 new artifact-prune tests.
- Follow-ups:
  - Tie artifact cleanup to failed-job retention pruner (when failed job is pruned, delete artifact dir).
  - Add `voxera queue prune` command for failed job files (Day 2 ROADMAP item).
  - Add `make type-debt` target (Day 1 ROADMAP item).
- Risks/notes:
  - Prune is always dry-run without `--yes`; safe by design.
  - Union policy documented in help text and README.

### PR #72 – Dry-run determinism: snapshot freeze + deterministic output mode (2026-02-28)
- Added `--freeze-capabilities-snapshot` and `--deterministic` flags to `voxera missions plan`.
- Added `_make_dryrun_deterministic()` helper in `src/voxera/core/missions.py` that zeroes
  `capabilities_snapshot.generated_ts_ms` in dry-run output (only when `--deterministic` is used).
- Default dry-run output is unchanged; both flags are opt-in.
- `--freeze-capabilities-snapshot` is a semantic commitment (snapshot already generated once per
  invocation); no runtime logic change needed.
- Verified:
  - `pytest tests/test_dryrun_determinism.py -q` — 4 new tests, all pass.
  - `ruff format src tests`, `ruff check src tests`, `mypy src` — clean.
  - `pytest -q` — all existing tests pass.
- Files changed: `src/voxera/core/missions.py`, `src/voxera/cli.py`,
  `tests/test_dryrun_determinism.py`, `README.md`, `docs/ops.md`, `docs/CODEX_MEMORY.md`.

## 2026-03-01 — PR #73 — Structured brain fallback reasons + health/doctor surfacing
- Summary:
  - Added stable `BrainFallbackReason` enum: `TIMEOUT | AUTH | RATE_LIMIT | MALFORMED | NETWORK | UNKNOWN`.
  - All exception paths in `openai_compat.py` and `gemini.py` classified into the enum before bubbling up.
  - Surfaced last fallback reason, source tier, and destination tier in `voxera queue health` and `health.json`.
  - Added per-reason health counters (`brain_fallback_reason_timeout`, `_auth`, `_rate_limit`, etc.).
  - `voxera doctor --quick` shows "Last fallback" line with most recent transition or "none".
- Validation:
  - `pytest -q tests/test_brain_fallback.py` — passes (new tests for each reason class).
  - `make merge-readiness-check` — clean.
- Follow-ups:
  - Surface fallback reason counters on panel home dashboard (tracked in Ops visibility milestone).
- Risks/notes:
  - Existing `UNKNOWN` fallback events remain in audit logs; no migration needed.
- Files changed: `src/voxera/brain/openai_compat.py`, `src/voxera/brain/gemini.py`,
  `src/voxera/health.py`, `src/voxera/cli.py`, `src/voxera/doctor.py`,
  `tests/test_brain_fallback.py`.

## 2026-03-01 — PR #75 — `voxera queue prune` command (terminal buckets only)
- Summary:
  - Added `voxera queue prune` CLI command that removes stale job files from terminal buckets
    (`done/`, `failed/`, `canceled/`). `inbox/` and `pending/` are never touched.
  - Dry-run by default; `--yes` to execute deletions.
  - Flags: `--max-age-days`, `--max-count`, `--json`, `--queue-dir`.
  - Matching sidecars (`.error.json`, `.state.json`) removed in the same pass as their primary job.
  - Env vars: `VOXERA_QUEUE_PRUNE_MAX_AGE_DAYS`, `VOXERA_QUEUE_PRUNE_MAX_COUNT`.
  - Runtime config keys: `queue_prune_max_age_days`, `queue_prune_max_count`.
  - Fixed: sidecars excluded from primary job enumeration to avoid double-counting.
  - Fixed: `safe_delete` tolerates already-deleted files gracefully.
- Validation:
  - `pytest -q tests/test_cli_queue.py` — passes (new prune lifecycle tests).
  - `make merge-readiness-check` — clean.
- Follow-ups:
  - Expose latest prune result in `voxera queue status` output.
  - Tie artifact dir cleanup to failed-job pruner pass.
- Risks/notes:
  - Union policy (age OR count) documented in help text and ops.md.
- Files changed: `src/voxera/core/queue_hygiene.py` (new), `src/voxera/cli.py`,
  `src/voxera/config.py`, `docs/ops.md`, `README.md`.

## 2026-03-01 — PR #76 — `voxera queue reconcile` report-only diagnostic
- Summary:
  - Added `voxera queue reconcile` as a read-only queue hygiene diagnostic.
  - Detects four issue categories: orphan sidecars, orphan approvals, orphan artifact candidates,
    duplicate job filenames across buckets.
  - Report-only by default — no filesystem changes in default mode.
  - `--json` flag emits stable JSON schema for automation.
  - Safe to run while daemon is running.
- Validation:
  - `pytest -q tests/test_cli_queue.py` — passes (new reconcile tests).
  - `make merge-readiness-check` — clean.
- Follow-ups:
  - Add fix/quarantine mode (tracked in PR #78).
- Risks/notes:
  - Missing queue directories are treated as 0 issues (no error raised).
- Files changed: `src/voxera/core/queue_reconcile.py` (new), `src/voxera/cli.py`, `docs/ops.md`.

## 2026-03-01 — PR #77 — Config path standardization (config.json)
- Summary:
  - Standardized all CLI help text, log messages, and documentation to consistently reference
    `~/.config/voxera/config.json` (not `config.yml` or ambiguous paths) for the runtime ops config.
  - Updated `docs/ops.md`, `README.md`, and affected CLI modules for consistency.
- Validation:
  - `make merge-readiness-check` — clean.
- Follow-ups:
  - None.
- Risks/notes:
  - Documentation-only change + CLI string cleanup; no runtime behavior changed.
- Files changed: `src/voxera/cli.py`, `README.md`, `docs/ops.md`.

## 2026-03-01 — PR #78 — Queue reconcile quarantine-first fix mode
- Summary:
  - Extended `voxera queue reconcile` with `--fix` flag enabling quarantine-first fix mode.
  - Without `--yes`: fix mode is a dry-run preview — prints what *would* be quarantined, exits 0.
  - With `--yes`: orphan sidecars in terminal buckets and orphan approvals are *moved* (not deleted)
    into `<queue-dir>/quarantine/reconcile-YYYYMMDD-HHMMSS/` preserving relative paths.
  - `--quarantine-dir` override supported (must remain within `--queue-dir`).
  - Stable JSON output schema extended with `mode`, `fix_counts`, and `quarantined_paths` fields.
  - Artifact candidates and duplicates remain report-only (too ambiguous for auto-fix).
- Validation:
  - `pytest -q tests/test_cli_queue.py` — passes.
  - `make merge-readiness-check` — clean.
- Follow-ups:
  - Symlink safety in quarantine paths (tracked in PR #79).
- Risks/notes:
  - No data is ever deleted; quarantined files can be restored manually.
- Files changed: `src/voxera/core/queue_reconcile.py`, `src/voxera/cli.py`, `docs/ops.md`.

## 2026-03-01 — PR #79 — Reconcile symlink orphan fix (safe relative path for quarantine)
- Summary:
  - Fixed reconcile fix mode to never follow symlinks when computing the safe relative path for
    quarantine destination. Prevents symlink traversal outside the queue root.
  - Resolves edge case where orphan sidecar is itself a symlink pointing outside `queue-dir`.
- Validation:
  - `pytest -q tests/test_cli_queue.py` — passes.
  - `make merge-readiness-check` — clean.
- Follow-ups:
  - None.
- Risks/notes:
  - Security-adjacent fix; no user-visible behavior change for normal (non-symlink) orphans.
- Files changed: `src/voxera/core/queue_reconcile.py`.

## 2026-03-01 — PR #80 — Daemon lock hardening + graceful SIGTERM shutdown
- Summary:
  - Hardened daemon lock: `flock`-based exclusive lock with PID validation, stale-window detection
    (configurable via `VOXERA_QUEUE_LOCK_STALE_S`), and structured audit event on contention.
  - Added explicit `SIGTERM`/`SIGINT` handler: sets shutdown flag immediately, stops intake of new
    inbox jobs, and handles any in-flight job deterministically as `failed/` with
    `error="shutdown: daemon shutdown requested"` plus a structured sidecar payload.
  - Health snapshot records `last_shutdown_ts`, `last_shutdown_reason`, and (if affected)
    `last_shutdown_job` + `last_shutdown_outcome=failed_shutdown`.
  - Concurrent daemon startup exits cleanly (non-zero) without disrupting the running daemon.
- Validation:
  - `pytest -q tests/test_queue_daemon.py` — passes (new lock + shutdown tests).
  - `make merge-readiness-check` — clean.
- Follow-ups:
  - Deterministic startup recovery for jobs that were in-flight at shutdown (PR #81).
- Risks/notes:
  - Fixes SECURITY.md known gap: "No SIGTERM handler — crash or stop leaves jobs in ambiguous state".
- Files changed: `src/voxera/core/queue_daemon.py`, `src/voxera/health.py`,
  `tests/test_queue_daemon.py`.

## 2026-03-01 — PR #81 — Deterministic daemon startup recovery
- Summary:
  - Added startup recovery pass that runs before any inbox intake on daemon start.
  - Policy: fail-fast. Any `pending/` job with in-flight state markers (`*.pending.json`,
    `*.state.json`) is moved to `failed/` with a structured sidecar:
    `reason="recovered_after_restart"`, includes `original_bucket`, `detected_state_files`,
    and best-effort `detected_artifacts_paths`.
  - Orphan approvals (`pending/approvals/*.approval.json` with no matching pending job) are
    quarantined under `recovery/startup-<ts>/pending/approvals/` (never deleted).
  - Orphan state files are quarantined under `recovery/startup-<ts>/...`.
  - Recovery emits audit event `daemon_startup_recovery` and increments health counters
    (`startup_recovery_runs`, `startup_recovery_jobs_failed`, `startup_recovery_orphans_quarantined`).
  - Health fields updated: `last_startup_recovery_ts`, `last_startup_recovery_counts`,
    `last_startup_recovery_summary`.
- Validation:
  - `pytest -q tests/test_queue_daemon.py` — passes (new recovery scenario tests).
  - `make merge-readiness-check` — clean.
- Follow-ups:
  - Surface `last_startup_recovery_counts` in panel dashboard (tracked in Ops visibility milestone).
- Risks/notes:
  - Recovery is deterministic and conservative: orphans are quarantined not deleted.
  - Double-execution risk for non-idempotent skills is eliminated for the shutdown-then-restart path.
- Files changed: `src/voxera/core/queue_daemon.py`, `src/voxera/health.py`,
  `src/voxera/audit.py`, `tests/test_queue_daemon.py`, `docs/ops.md`.

## 2026-03-01 — PR #82 — `voxera demo` guided checklist + modernized setup wizard
- Summary:
  - Added `voxera demo` CLI command: guided onboarding checklist that exercises queue + approval flows
    without destructive actions. Creates jobs with deterministic prefixes (`demo-basic-*`,
    `demo-approval-*`). Offline by default (provider readiness marked `SKIPPED`).
  - `voxera demo --online` opts into provider readiness checks; missing keys remain `SKIPPED`
    (not failure) so demo always completes.
  - Modernized setup wizard UX: auth prompt choices rendered with explicit labels
    (Keep current / Skip for now / Enter new / replace key) to avoid terminal rendering ambiguity.
  - Setup choices are intentionally non-destructive: existing credentials are never overwritten
    without an explicit "Enter new" selection.
  - Fixed: demo overall status aggregation for skipped online checks (skipped ≠ failed).
- Validation:
  - `pytest -q tests/test_demo_cli.py tests/test_setup_wizard.py` — passes (new demo + wizard tests).
  - `make merge-readiness-check` — clean.
- Follow-ups:
  - Replace PR #N/A with the merged PR number.
  - Add `voxera demo` to UBUNTU_TESTING.md validation checklist.
- Risks/notes:
  - Demo creates real queue jobs; operators should run `voxera queue prune` after extended demo sessions.
- Files changed: `src/voxera/demo.py` (new), `src/voxera/setup_wizard.py`, `src/voxera/cli.py`,
  `tests/test_demo_cli.py`, `tests/test_setup_wizard.py`, `README.md`, `docs/ops.md`.

## 2026-03-02 — PR #TBD — OpenRouter invisible default attribution headers
- Summary:
  - Removed setup wizard prompts for OpenRouter attribution headers; OpenRouter setup now asks only for model tiering + key reference.
  - Added central OpenRouter detection in `OpenAICompatBrain` and automatic default attribution headers for all OpenRouter requests:
    - `HTTP-Referer=https://voxeraos.ca`
    - `X-OpenRouter-Title=VoxeraOS`
    - `X-Title=VoxeraOS` (compatibility)
  - Added optional environment overrides: `VOXERA_APP_URL`, `VOXERA_APP_TITLE`.
  - Ensured `extra_headers` cannot override `Authorization` or `Content-Type`.
  - Added tests for default injection, user override behavior, and non-OpenRouter behavior.
  - Updated README/SECURITY/ROADMAP docs to document behavior and shipped provider UX improvement.
- Validation:
  - `ruff format .`
  - `ruff check .`
  - `pytest`
  - `make merge-readiness-check`
- Follow-ups:
  - Replace PR placeholder with merged PR number.


## PR: security(panel) rate limit failed Basic auth attempts per IP (10/60s) with 429 + Retry-After + health/audit surfaces (P1.3)
- **What changed:** Added per-IP panel auth failure tracking and lockout enforcement in panel Basic auth. After 10 failed attempts within 60s, requests return `429` with `Retry-After: 60`. Added structured audit event `panel_auth_lockout`.
- **Health/ops visibility:** Added `panel_auth` state (`failures_by_ip`, `lockouts_by_ip`) to `health.json` with pruning and bounded IP tracking; surfaced lockout summary in `voxera queue health` (human + `--json`) and `voxera doctor --quick`.
- **Robustness:** Health snapshot writer now ensures parent directories exist before atomic replace.
- **Tests:** Added panel auth lockout tests for threshold trigger, subsequent block, reset behavior, and health snapshot state.
- **Commands run:** `ruff format .`, `ruff check .`, `pytest`, `make merge-readiness-check`.


## 2026-03-03 — PR #TBD — feat(P3.1): daemon_state degraded after 3 consecutive brain fallbacks
- Summary:
  - Added degradation state machine in `src/voxera/health.py` (`update_degradation_state`) and normalized health snapshot defaults so `consecutive_brain_failures` + `daemon_state` are always present, with nullable `degraded_since_ts`/`degraded_reason`.
  - Wired fallback streak increments into planner fallback transition handling (`record_brain_fallback_attempt`) and reset-on-success into queue DONE transitions (`record_mission_success`) including approval-resume completion path.
  - Expanded deterministic tests in `tests/test_brain_fallback.py` for threshold, reset, persistence, timestamp semantics, and snapshot integration.
  - Updated `docs/ROADMAP.md`, `docs/ROADMAP_0.1.6.md`, and `docs/ops.md` to mark/document shipped P3.1 behavior and operator interpretation.
- Validation:
  - `source .venv/bin/activate`
  - `python -m pip install -e .`
  - `ruff format .`
  - `ruff check . --fix`
  - `make merge-readiness-check`
  - `pytest`
- Follow-ups:
  - None.
- Risks/notes:
  - Fallback streak increments once per fallback transition event recorded by planner attempts; mission success clears state only when a job reaches `done/`.

## 2026-03-04 — P3.3 shipped: persisted last shutdown outcome across daemon/CLI/panel

- Added deterministic health snapshot keys: `last_shutdown_outcome`, `last_shutdown_ts`, `last_shutdown_reason`, `last_shutdown_job` with always-present normalization defaults (`null`).
- Added `record_last_shutdown(...)` helper in `src/voxera/health.py` (bounded reason text, explicit outcome allowlist: `clean`, `failed_shutdown`, `startup_recovered`, injectable `now_fn` for deterministic tests).
- Daemon stop-path hooks now write persisted shutdown context for graceful stops and failure paths where state write remains possible; in-flight shutdown failures continue to mark jobs failed deterministically and now persist via shared helper.
- Operator surfaces updated to read from `health.json`: `voxera queue health` (new Last Shutdown block + JSON parity), `voxera doctor --quick` (last shutdown one-line summary), panel home Daemon Health widget (adds shutdown reason/job display).
- Added/updated tests for normalization defaults, shutdown recording helper behavior, queue health output, quick doctor summary line, and panel rendering of shutdown reason/job.
- Validation commands: `ruff format .`, `ruff check . --fix`, targeted `pytest` for touched suites, and `make merge-readiness-check`.

## 2026-03-04 — add `brain_backoff_active` for operator clarity

- Added `brain_backoff_active` to health snapshot normalization in `src/voxera/health.py`.
- Semantics are deterministic: `brain_backoff_active = (brain_backoff_wait_s > 0)`.
- This clarifies “active now” (`brain_backoff_active`) vs “last applied historically” (`brain_backoff_last_applied_*`), which intentionally persists across healthy/idle periods.
- Extended backoff snapshot tests in `tests/test_brain_fallback.py` to assert default false, true when computed wait is non-zero, and backward-compatible normalization for older snapshots missing the field.
- Validation: `ruff format .`, `ruff check . --fix`, `pytest`, `make merge-readiness-check`.


## 2026-03-05 — observability(operator-health): queue health sectioning/watch + panel performance tab
- Tightened health snapshot normalization defaults for operator-facing observability fields (`daemon_*`, `updated_at_ms`, fallback fields, counters/auth maps, OK/error timestamps) for deterministic JSON semantics.
- `voxera queue health` now renders sectioned output (**Current State**, **Recent History**, **Counters**) and adds `--watch` + `--interval`; `--json` includes parity objects (`current_state`, `recent_history`, `counters`).
- Panel home adds a read-only **Performance Stats** tab with queue counts, degradation/backoff, fallback/error/shutdown context, and auth/runtime counters sourced from `health.json`.
- Added regression tests for normalization defaults, CLI section/parity/watch behavior, and panel performance tab rendering.



## 2026-03-06 — PR #TBD — strengthen mission execution semantics and persisted job state
- Summary:
  - Added explicit mission/queue lifecycle semantics with persisted per-job `*.state.json` sidecars that track `lifecycle_state`, step progress, transition timestamps, approval status, and terminal outcomes.
  - Expanded mission runner result metadata to persist reusable step outcomes (`succeeded`, `awaiting_approval`, `failed`, `blocked`) and terminal outcome primitives for downstream queue logic.
  - Updated queue daemon lifecycle handling to persist state transitions across planning, running, approval pause/resume, failure, deny/blocked, cancel, and done paths.
  - Surfaced lifecycle truth in operator views: `voxera queue status` now prints a Job Lifecycle Snapshot table, panel jobs list includes lifecycle/progress/outcome, and job detail exposes Execution State fields.
  - Added/updated tests for lifecycle sidecar persistence and CLI/panel rendering.
- Validation:
  - `source .venv/bin/activate`
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`
- Follow-ups:
  - None.


## Operator assistant surface
- Panel now includes `/assistant` (Ask Voxera): grounded, advisory-only operator Q&A over current queue/health/approvals/failure/audit context.
- Scope is intentionally narrow: explain state and likely next steps only; no direct execution or approval actions from chat.

- Operator assistant now traverses Voxera Queue via dedicated `assistant_question` advisory jobs; panel submit enqueues, daemon answers through deterministic dual-brain primary→fallback advisory attempts (fallback only for explicit retryable classes), and panel polls status/results from queue/artifacts with compact metadata (`provider`/`model`, fallback usage/reason, advisory mode/degraded reason).
- Assistant threads now persist compact multi-turn history (`artifacts/assistant_threads/<thread>.json`) so follow-up questions retain continuity while refreshing live runtime context.

## 2026-03-06 — PR #TBD — extract queue daemon state persistence + transition helpers
- Structural extraction only (no daemon semantic changes):
  - Added `src/voxera/core/queue_state.py` for persisted job-state sidecar path/read/write logic and snapshot normalization/update helper.
  - Added `src/voxera/core/queue_paths.py` for deterministic job move/bucket-transition helpers, including sidecar co-move and collision-safe destination naming.
  - Kept orchestration in `src/voxera/core/queue_daemon.py`; it now delegates persisted-state and transition mechanics to focused helpers.
- Semantics explicitly preserved during extraction: sidecar co-location with active bucket, `.state.json` naming, collision rename behavior, lifecycle transition timestamps, schema version, approval/deny/cancel/retry/recovery paths, and health/audit continuity.



## 2026-03-06 — PR TBD — refactor(queue): extract approval workflow + pending-approval artifacts
- Summary:
  - Extracted queue approval-lane mechanics from `src/voxera/core/queue_daemon.py` into `src/voxera/core/queue_approvals.py`.
  - Moved approval prompts/grants, pending approval artifact helpers, approval ref normalization + canonicalization, approval artifact parsing/list snapshots, and approve/deny resolution flow into the new module.
  - Kept `queue_daemon.py` focused on main process-loop orchestration, startup recovery, lifecycle transitions, and invoking extracted approval helpers.
  - Updated README/ops/architecture docs to reflect the split and future-slice guidance.
- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`
  - `pytest -q tests/test_queue_daemon.py tests/test_queue_daemon_contract_snapshot.py`
  - `bash scripts/e2e_golden4.sh`
- Follow-ups:
  - Continue daemon slimming with similarly mechanical extractions while preserving state-machine semantics exactly.



## 2026-03-06 — PR TBD — refactor(queue): extract assistant advisory lane
- Summary:
  - Extracted queue-backed assistant/advisory lane mechanics from `src/voxera/core/queue_daemon.py` into `src/voxera/core/queue_assistant.py`.
  - Moved assistant provider construction/candidate ordering, deterministic primary→fallback advisory answering, assistant response artifact persistence, assistant failure artifact path, and assistant lifecycle/action-event updates.
  - Kept `queue_daemon.py` focused on main orchestration loop, lock/recovery/lifecycle control, and lane routing (`assistant_question` jobs vs mission jobs).
- Semantics explicitly preserved:
  - Assistant job detection (`kind=assistant_question`), advisory read-only contract, queue-backed transport states, fallback/degraded metadata fields, artifact naming/location (`artifacts/<job_stem>/assistant_response.json`), thread continuity persistence, and failed-bucket handling with sidecar/lifecycle consistency.
- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`
  - `pytest -q tests/test_queue_daemon.py tests/test_queue_daemon_contract_snapshot.py tests/test_operator_assistant_queue.py tests/test_panel.py -k "assistant"`
  - `bash scripts/e2e_golden4.sh`


## 2026-03-06 — PR TBD — refactor(queue): extract mission execution pipeline
- Summary:
  - Extracted mission execution/process pipeline mechanics from `src/voxera/core/queue_daemon.py` into `src/voxera/core/queue_execution.py` via `QueueExecutionMixin`.
  - Kept `MissionQueueDaemon` as composition/orchestration root with thin delegation preserved for compatibility-sensitive entry points (`process_job_file`, `process_pending_once`, and planner/backoff/parse-hook module symbols used by monkeypatch/contract tests).
  - Updated queue module ownership docs in README/ARCHITECTURE/ops for the new boundary.
- Semantics explicitly preserved:
  - Lifecycle sidecars and transitions (`queued`, `planning`, `awaiting_approval`, `resumed`, `advisory_running`, `running`, `done`, `step_failed`, `blocked`, `canceled`), pending/approval artifacts, failed sidecar schema, bucket moves (including missing-source behavior), action/audit emission order, health/stat counters, approval status + terminal outcome propagation, step-outcome bookkeeping, and assistant lane isolation.
- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`
  - `pytest -q tests/test_queue_daemon.py tests/test_queue_daemon_contract_snapshot.py -vv`
  - `pytest -q tests/test_queue_daemon.py -k "planning or running or pending or approval or done or failed or canceled or blocked or lifecycle or retry or recovery" -vv`
  - `pytest -q tests/test_operator_assistant_queue.py -vv`
  - `pytest --collect-only | grep -Ei "planning|running|pending|approval|done|failed|canceled|blocked|lifecycle|retry|recovery|assistant"`
  - `voxera doctor --quick`
  - `voxera queue status`

## 2026-03-07 — PR TBD — runtime capability enforcement (fail-closed) before step invocation
- Summary:
  - Added fail-closed runtime capability enforcement at the skill dispatch boundary (`src/voxera/skills/runner.py`) so no step can execute unless capability metadata is valid and policy outcome permits execution.
  - Enforcement now blocks execution when capability metadata is missing, malformed, ambiguous (duplicate declarations), or unknown to the canonical capability/effect catalog.
  - Policy `ask` stays in approval path (pending artifact + no side effects), `deny` is blocked, and all blocked/pending outcomes emit structured canonical skill-result payload fields that flow into `step_results.json` and `execution_result.json`.
  - Updated built-in skill manifests to declare explicit capabilities for previously undeclared skills so safe/read paths continue to run under strict enforcement.
- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`
  - `make golden-check`
  - `make validation-check`


## 2026-03-07 — PR TBD — skill manifest strictness + doctor skill-health visibility
- **Manifest contract hardening:** `SkillManifest` now forbids unknown fields and validates non-empty core identifiers (`id`, `name`, `description`, `entrypoint`), entrypoint shape (`module:function`), normalized unique string-lists (`capabilities`, `output_artifacts`), and optional `output_schema` non-empty semantics.
- **Discovery/reporting model:** `SkillRegistry` now centralizes strict classification through `discover_with_report()` with deterministic status buckets (`valid`, `invalid`, `incomplete`, `warning`) and reason codes/hints.
  - `invalid`: malformed schema or unknown capability metadata.
  - `incomplete`: missing required governance metadata (`capabilities`).
  - `warning`: recommended metadata missing (`output_schema`).
  - `discover()` remains fail-closed on invalid manifests while incomplete manifests remain visible in report surfaces and excluded from runtime set.
- **Doctor operator surface:** `voxera doctor` now includes `skills.registry` with stable counts (`valid/invalid/incomplete/warning/total`), partial-load signal, and top failing reason codes for rapid remediation.
- **Tests:** Added focused registry classification tests (malformed capabilities, missing capability metadata, mixed valid+invalid stability) and doctor summary tests for skill registry visibility.


## PR 5 memory note: canonical skill result fields

Contract fields to rely on across built-in skills: `summary`, `machine_payload`, `output_artifacts`, `operator_note`, `next_action_hint`, `retryable`, `blocked`, `approval_status`, `error`, `error_class`.
## 2026-03-08 — PR #TBD — feat(queue): add fail-closed read-only assistant fast lane
- Added conservative fast-lane eligibility gate for assistant advisory queue jobs in `queue_execution.py` + `queue_assistant.py`.
  - Eligible lane: `execution_lane=fast_read_only` for explicit read-only advisory payloads only.
  - Fail-closed fallback: all non-eligible/uncertain payloads remain on normal `execution_lane=queue`.
- Preserved trust/governance guarantees:
  - No policy/capability bypasses; fast lane remains inside queue control plane.
  - Canonical artifacts are still written for both lanes.
- Added explicit operator/audit evidence fields:
  - `execution_result.json.execution_lane`
  - `execution_result.json.fast_lane` (`used`, `eligible`, `eligibility_reason`, `request_kind`)
  - mirrored lane metadata in `assistant_response.json`.
- Added focused tests for eligibility, canonical artifact evidence, and fail-closed fallback cases (approval-flagged, mutating hint, malformed payload, non-eligible hint set).
- Follow-up fix (PR #143 regression): assistant lane routing now keys off canonical request kind (`detect_request_kind`, including `job_intent.request_kind`) rather than raw `payload.kind` only, preventing mission-path misclassification (`ValueError: job must contain mission_id ...`) for valid assistant-shaped jobs and restoring CLI/panel outcome consistency for original queue jobs.
- Follow-up contract gap fix: assistant jobs now emit canonical `execution_envelope.json` with assistant-shaped context and aligned lane metadata (`execution.lane`, `execution.fast_lane`) for both `fast_read_only` and `queue` advisory paths; envelope/result/assistant-response lane fields now agree.

## PR 7 — Real-time assistant/job progress UX

- Added additive JSON polling endpoints for live panel progress:
  - `/jobs/{job_id}/progress`
  - `/assistant/progress/{request_id}`
- Added progressive-enhancement client polling on `job_detail.html` and `assistant.html` (no-JS fallback preserved).
- Progress payloads are shaped from canonical artifacts/sidecars only; no optimistic synthetic completion values.
- Surfaced lifecycle + step progress + approval status + lane metadata (`execution_lane`, `fast_lane`, `intent_route`) + terminal stop/failure summaries when available.
- Added panel tests covering assistant running/done path, mission awaiting approval path, terminal failed path, and endpoint behavior.


- Queue lineage metadata is now carried as descriptive-only fields (`parent_job_id`, `root_job_id`, `orchestration_depth`, `sequence_index`, `lineage_role`) through canonical artifacts and panel/progress shaping. No child enqueue/dependency behavior was introduced in this phase.


- PR 9B-lite introduced a constrained `enqueue_child` queue payload primitive: one explicit child enqueue per parent execution, deterministic/sanitized lineage propagation, and auditable evidence (`child_job_refs.json`, `queue_child_enqueued` action event, `child_refs` in result/progress/panel). No DAG/dependency/wait/result-passing behavior was added.

## 2026-03-09 — GitHub PR #158 — feat(vera): persist and replace active previews across follow-up turns

- Vera now keeps one active structured preview draft per session and replaces it when follow-up revisions produce a newer structured preview.
- Added follow-up draft replacement handling for common conversational edits (URL replacement, filename rename, and content refinement) while keeping explicit submit-only behavior.
- Lightweight acknowledgements keep the active preview intact; explicit submit always uses latest active preview; preview clears only after confirmed handoff success.
- Added focused Vera web coverage for replacement lifecycle, latest-preview submit semantics, and clear-on-success behavior.


## 2026-03-09 — GitHub PR #159 — feat(vera/ui): make active preview authoritative and directly submittable
- Fixed trust boundary mismatch by making the visible preview pane authoritative state: displayed JSON is always the active session draft and the submit target.
- Added explicit preview-pane submit affordance wired to existing trusted handoff path; successful submit clears active preview/pane state.
- Added natural active-preview approval phrase routing (`use this preview`, `that looks good now use it`, etc.) that submits only when an active preview exists; no-preview cases fail closed.
- Kept queue/execution semantics unchanged: Vera submits to VoxeraOS, execution remains VoxeraOS-owned.
- Added focused tests for authoritative pane rendering, pane-submit behavior, natural phrase routing, fail-closed behavior, and post-submit preview clearing.


## 2026-03-10 — PR #161 — feat(setup/demo): bump 0.1.7 and guided OpenRouter setup flow
- Summary:
  - Bumped package/version-facing truth to `0.1.7` in `pyproject.toml` and onboarding docs.
  - Refactored `voxera setup` cloud flow into explicit sequential brain-slot configuration (`primary`, `fast`, `reasoning`, `fallback`).
  - Added provider selection from supported catalog for each slot with per-slot confirmation summaries.
  - Added live OpenRouter models retrieval from `https://openrouter.ai/api/v1/models` and exposed metadata-driven selection (`id`, `name`, context length, pricing hints, supported params when available).
  - Added OpenRouter graceful degradation path: retry fetch or manual model-id entry when API fetch fails.
  - Added explicit finish-step launch options after successful setup save: open Voxera panel, Vera panel, both, or none.
  - Updated onboarding/docs surfaces (`README.md`, `docs/ARCHITECTURE.md`, `docs/ops.md`, `docs/UBUNTU_TESTING.md`, `docs/ROADMAP.md`) for setup/demo vocabulary alignment.
- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`
  - `make security-check`
  - `make golden-check`
  - `make validation-check`
  - `make merge-readiness-check`


## 2026-03-10 — PR #162 — feat(setup): curated grouped OpenRouter setup catalog + slot defaults
- Summary:
  - Replaced raw live-table OpenRouter setup UX with curated static catalog (`src/voxera/data/openrouter_catalog.json`) grouped by vendor/maker for menu-style setup.
  - Kept sequential brain-slot setup (`primary`, `fast`, `reasoning`, `fallback`) and added explicit strong default recommendations:
    - `primary=google/gemini-3-flash-preview`
    - `fast=google/gemini-3.1-flash-lite-preview`
    - `reasoning=anthropic/claude-3.5-sonnet`
    - `fallback=meta-llama/llama-3.3-70b-instruct`
  - Preserved advanced manual model-id path and post-setup panel launch options.
  - Added maintainer refresh helper from live endpoint: `scripts/refresh_openrouter_catalog.py` + normalization/refresh logic in `src/voxera/openrouter_catalog.py`.
  - Added focused tests for curated catalog load/grouping/recommendation and refresh normalization path.


## 2026-03-10 — PR #163 — fix(setup): ensure runtime services before finish-panel launch
- Summary:
  - Updated setup finish path to ensure runtime stack services start before panel launch choices are used: `voxera-daemon.service`, `voxera-panel.service`, `voxera-vera.service`.
  - Added systemd user-service helper flow in setup wizard: daemon-reload, enable/start, and active checks with honest per-service failure reporting.
  - Kept explicit optional finish choices (open Voxera panel, Vera panel, both, none), but now skip panel auto-open when corresponding service failed to start.
  - Corrected Vera panel launch URL to match runtime default (`http://127.0.0.1:8790`).
  - Added focused tests for service-start helper behavior, failure handling, and setup finish ordering (ensure services before launch).
