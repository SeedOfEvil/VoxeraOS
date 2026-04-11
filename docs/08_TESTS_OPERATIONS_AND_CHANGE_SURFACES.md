# 08 — Tests, Operations, and Change Surfaces

This document is the change-surface map for the repo. It groups the test suite into themes, lists the canonical validation targets, and points to the files you'll actually touch when extending each area.

## Validation ladder (Make targets)

From `Makefile`:

```
ruff format --check .       # formatting gate
ruff check .                # lint gate
mypy src/voxera             # type gate (ratchet enforced)
pytest -q                   # full test suite
make golden-check           # CLI contract baselines (tools/golden_surfaces.py)
make security-check         # red-team regression suite
make validation-check       # fmt/lint/type + golden + security + targeted pytest
make merge-readiness-check  # quality-check + release-check + security-check
make full-validation-check  # validation + merge-readiness + full pytest + e2e_golden4.sh
```

The CI required merge gate is **`merge-readiness / merge-readiness`**. The GitHub workflow job name is fixed — do not rename it without updating the workflow.

Lightweight quick loops:

- `make fmt` — auto-format
- `make lint` — ruff check
- `make type` — mypy
- `make test` — pytest `-q`
- `make check` — fmt-check + lint + type + test (and `e2e` when `CHECK_E2E=1`)

Typing ratchet:

- `make type-check` — ratchet checker via `scripts/mypy_ratchet.py`
- `make update-mypy-baseline` — update `tools/mypy-baseline.txt` (intentional updates only)
- `make type-check-strict` — full `mypy src/voxera`

## Test themes

From `tests/` (110 files at regeneration time). Grouped by area; every test listed exists in the current tree.

### Queue daemon, contracts, lifecycle
- `test_queue_daemon.py`
- `test_queue_daemon_config_drift.py`
- `test_queue_daemon_contract_snapshot.py`
- `test_queue_constitution_contracts.py`
- `test_queue_execution_contracts.py`
- `test_queue_artifact_minimum_regression.py`
- `test_queue_job_intent.py`
- `test_queue_result_consumers.py`

### CLI surface + contract snapshot + golden surfaces
- `test_cli_version.py`
- `test_cli_run_args.py`
- `test_cli_config_snapshot.py`
- `test_cli_contract_snapshot.py`
- `test_cli_queue.py`
- `test_cli_queue_prune.py`
- `test_cli_queue_reconcile.py`
- `test_cli_queue_remaining_surfaces.py`
- `test_cli_secrets.py`
- `test_cli_artifacts.py`
- `test_golden_surfaces.py`
- `test_update_script.py`

### Skills, runner, arg normalization, policy
- `test_registry.py`
- `test_runner.py`
- `test_execution.py`
- `test_execution_capabilities.py`
- `test_execution_evaluator.py`
- `test_arg_normalizer.py`
- `test_skill_metadata_and_runners.py`
- `test_skill_result_payloads.py`
- `test_capability_semantics.py`
- `test_capabilities_snapshot.py`
- `test_policy.py`
- `test_direct_mutation_gate.py`
- `test_dry_run_contract.py`
- `test_dryrun_determinism.py`
- `test_sandbox_exec_args.py`
- `test_sandbox_exec_integration.py`

### Files skill family + path boundaries
- `test_files_control_plane_boundaries.py`
- `test_files_copy_file.py`
- `test_files_delete_file.py`
- `test_files_list_dir.py`
- `test_files_move_file.py`
- `test_files_read_text.py`
- `test_files_wave2_skills.py`
- `test_files_workspace_expansion.py`
- `test_files_write_text.py`
- `test_file_intent.py`

### System inspection + open_app
- `test_system_inspect.py`
- `test_open_app.py`
- `test_builtin_skills_terminal_run_once.py`

### Missions + mission planner + intent routing
- `test_missions.py`
- `test_mission_planner.py`
- `test_simple_intent.py`
- `test_planner_context.py`
- `test_code_draft_intent.py`

### Panel + panel contract snapshot
- `test_panel.py` — operator Basic-auth 401 paths, CSRF 403 guard, per-IP lockout 429 behavior, panel security counters, and lockout/window semantics are exercised end-to-end through the FastAPI `TestClient`. These tests pin the auth-enforcement behavior that is now implemented in `src/voxera/panel/auth_enforcement.py` and consumed by `panel/app.py` via `require_operator_basic_auth` / `require_mutation_guard`. Lockout tests monkeypatch `panel_module._now_ms`; because `auth_enforcement` reaches back through the `panel.app` module for the shared wrappers (`_now_ms`, `_health_queue_root`, `_panel_security_counter_incr`), the patches still drive the auth flow exactly as before.
- `test_panel_auth_enforcement_extraction.py` — narrow extraction-contract tests (6) that pin the shape of PR A: `auth_enforcement.py` owns the two documented entry points `require_operator_basic_auth(request)` and `require_mutation_guard(request)`; `panel.app._require_mutation_guard` is a literal alias for `auth_enforcement.require_mutation_guard`; `panel.app._require_operator_auth_from_request` is a thin wrapper that forwards to `require_operator_basic_auth`; `panel.app._operator_credentials` is the re-exported `auth_enforcement._operator_credentials` (for the existing `test_dev_contract_config_integration` contract test); `panel.app` does not re-define the extracted private helpers (`_client_ip`, `_panel_auth_state_update`, `_panel_auth_state_prune`, `_active_lockout_until_ms`, `_log_panel_security_event`, `_request_meta`, `_PanelSecurityRequestLike`); the reach-back pattern (`auth_enforcement._now_ms` / `_health_queue_root` / `_panel_security_counter_incr` looking up the attribute on `panel.app` at call time) is exercised directly via `monkeypatch`; and the fail-closed 401 path on `require_operator_basic_auth` with a missing `Authorization` header is asserted at the unit level. A future panel-decomposition PR that silently reintroduces any of those helpers locally in `panel/app.py` will fail this file loudly.
- `test_panel_queue_mutation_bridge_extraction.py` — narrow extraction-contract tests (11) that pin the shape of **PR B** (second small panel extraction — hygiene / queue mutation bridge only): `queue_mutation_bridge.py` owns the two documented entry points `run_queue_hygiene_command(queue_root, args)` and `write_panel_mission_job(queue_root, *, prompt, approval_required)` plus the two bridge helpers `write_queue_job(queue_root, payload)` and `write_hygiene_result(queue_root, key, result, *, now_ms)`; `panel.app` still exposes the thin wrapper callbacks `_write_queue_job`, `_write_panel_mission_job`, `_run_queue_hygiene_command`, `_write_hygiene_result` and each wrapper's source visibly forwards to the extracted bridge function; `panel.app` does not re-define `_trim_tail` or `_repo_root_for_panel_subprocess`; queue-truth semantics are preserved (`write_queue_job` writes `source_lane=panel_queue_create` and leaves no tmp files behind; `write_panel_mission_job` writes `source_lane=panel_mission_prompt`, preserves `expected_artifacts` and `approval_hints`, and the mission-id matches the stored `id`); `run_queue_hygiene_command` is fail-closed for non-zero rc and invalid-JSON stdout (returns `ok=False` + populated `error` / `stderr_tail` / `stdout_tail` — never raises); `write_hygiene_result` uses the injected `now_ms` callable for `updated_at_ms`; the reach-back-via-wrapper pattern works — `panel.app._write_hygiene_result` reads `_now_ms` from its module globals at call time, so `monkeypatch.setattr(panel_module, "_now_ms", ...)` still drives the `updated_at_ms` stamp through the thin wrapper; `panel.app.subprocess is subprocess` and `panel.app.sys is sys` — pins the `# noqa: F401` re-export surface so a later PR can't silently drop those imports and break every `test_panel.py::test_hygiene_*` monkeypatch; and `queue_mutation_bridge.py` does NOT reach back into `panel.app` via any import (AST-level check rules out `from . import app` / `from .app import …` / `from .routes_* import …`), pinning the explicit-args architecture invariant that distinguishes PR B from PR A's deliberate reach-back pattern. A future panel-decomposition PR that silently reintroduces any of the extracted bridge logic locally in `panel/app.py` — or sneaks a circular dependency back into the bridge — will fail this file loudly.
- `test_panel_automations.py` — automation dashboard routes: list page, detail page, enable/disable, run-now (queue-submitting only), history display, missing/malformed handling, auth/mutation guard.
- `test_panel_contract_snapshot.py`
- `test_operator_assistant_queue.py`
- `test_operator_contract_guardrails.py`

### Vera service, session, preview, submission, review
- `test_vera_chat_reliability.py`
- `test_vera_contextual_flows.py`
- `test_vera_session_characterization.py`
- `test_vera_live_path_characterization.py`
- `test_vera_runtime_validation_fixes.py`
- `test_vera_web.py`
- `test_vera_panel.py`
- `test_vera_draft_bug_fix.py`
- `test_vera_draft_revision.py`
- `test_vera_preview_materialization.py`
- `test_vera_preview_stabilization.py` — covers the centralized preview state transitions in `src/voxera/vera/preview_ownership.py` (create/revise/follow-up/clear/submit-success), the canonical routing lane order in `src/voxera/vera_web/preview_routing.py`, the conservative revision-turn gate (`is_active_preview_revision_turn`), and integration-level regressions ensuring that lifecycle/review lanes no longer hijack active-preview revision turns.
- `test_vera_web_lanes_extraction.py` — behavior-preserving coverage for the small automation/review lane extraction out of `src/voxera/vera_web/app.py` into `src/voxera/vera_web/lanes/`. Asserts that `chat()` still visibly calls each extracted lane entry point (`try_submit_automation_preview_lane`, `try_automation_draft_or_revision_lane`, `try_automation_lifecycle_lane`, `try_materialize_automation_shell`, `apply_early_exit_state_writes`, `compute_active_preview_revision_in_flight`); verifies the `AutomationLaneResult` contract; exercises `compute_active_preview_revision_in_flight` for the narrow gate plus the review/evidence belt-and-suspenders; pins `apply_early_exit_state_writes` write choreography (noop when unmatched, follow-up helper for source-job previews, review shortcut for single-key context updates); enforces preview ownership discipline (no `write_session_preview` in the lane modules); and runs end-to-end smoke through `/chat` for automation draft + save and the normal-preview lifecycle-lane step-aside path.
- `test_vera_preview_submission.py`
- `test_draft_content_binding.py`
- `test_authored_draft_body_fidelity.py`
- `test_session_aware_authored_drafting.py`
- `test_shared_session_context.py`
- `test_shared_session_context_integration.py`
- `test_session_routing_debug.py`
- `test_context_lifecycle.py`
- `test_response_shaping.py`
- `test_chat_early_exit_dispatch.py`
- `test_vera_web_markdown_render.py` — safe bounded markdown renderer for assistant messages: headings, bold, inline code, lists, fenced code blocks, blockquotes, paragraph breaks, XSS prevention, combined realistic samples.
- `test_reference_resolver.py`
- `test_evidence_review.py`
- `test_result_surfacing.py`
- `test_linked_job_review_continuation.py`

### Vera investigation, brave search, hidden compiler
- `test_vera_brave_search.py`
- `test_vera_investigation_derivations.py`
- `test_vera_hidden_compiler.py`
- `test_vera_compiler_leakage.py`

### Health, doctor, ops bundle, diagnostics
- `test_health.py`
- `test_health_snapshot_isolation.py`
- `test_doctor.py`
- `test_ops_bundle.py`
- `test_ops_bundle_includes_config_snapshot.py`
- `test_diagnostics_pack.py`

### Config, setup wizard, secrets
- `test_config_settings.py`
- `test_config_snapshot.py`
- `test_dev_contract_config_integration.py`
- `test_models_config_strictness.py`
- `test_setup_wizard.py`
- `test_secrets.py`

### Brain / providers
- `test_brain_fallback.py`
- `test_openai_compat_headers.py`
- `test_openrouter_catalog.py`

### Security / red-team
- `test_security_redteam.py`

### Voice foundation
- `test_voice_foundation.py`

### Automation object model, runner, operator CLI, and Vera preview
- `test_automation_object_model.py` — covers the Pydantic model in `src/voxera/automation/models.py` and the file-backed store in `src/voxera/automation/store.py`.
- `test_automation_runner.py` — covers the runner surface in `src/voxera/automation/runner.py` and the history records in `src/voxera/automation/history.py`: due `once_at`, `delay`, and `recurring_interval` definitions emit normal canonical queue jobs via the existing inbox path; non-due / disabled / malformed / unsupported-trigger-kind definitions are skipped; history records carry queue job linkage; updated definition fields (`last_run_at_ms`, `last_job_ref`, `run_history_refs`, `enabled`, `next_run_at_ms`) are saved; one-shot semantics prevent double-submit on repeated runner passes; recurring semantics re-arm `next_run_at_ms` and allow repeated fires; emitted payload matches the saved `payload_template`.
- `test_automation_operator_cli.py` — covers the operator CLI commands in `src/voxera/cli_automation.py`: `list` shows saved definitions; `show` renders a detailed JSON view; `enable` / `disable` flip the enabled flag and persist without rewriting unrelated fields; `history` shows linked run history entries; `run-now` processes through the existing runner and submits via the queue; missing ids return clean errors; malformed definitions and history files are handled safely; `list_history_records` helper returns records filtered by automation id, newest first.
- `test_vera_automation_preview.py` — covers the Vera-side automation preview drafting, revision, and submit flow in `src/voxera/vera/automation_preview.py`: intent detection for schedule/deferred requests; trigger parsing (`delay`, `recurring_interval`, `once_at`); payload parsing (run commands, write-file notes, diagnostics); full preview drafting lifecycle; focused clarification when trigger or payload is incomplete; revision of active automation previews (change trigger, rename, update content, enable/disable); submit saves a durable definition to the automation store without emitting a queue job; submit acknowledgment is truthful (saved, not executed); post-submit continuity describes the saved automation; non-automation preview flows remain unchanged; ambiguous requests fail closed.
- `test_vera_automation_lifecycle.py` — covers conversational lifecycle management of saved automation definitions via `src/voxera/vera/automation_lifecycle.py`: intent classification for show/enable/disable/delete/run-now/history requests; reference resolution from session context, explicit id, title match, and single-definition fallback; ambiguous references fail closed with clarification; show describes a saved definition truthfully from the canonical store; enable/disable persist the change; delete removes the definition but preserves history; "did it run?" answers truthfully when no history exists; history surfaces canonical run records; run-now uses the existing runner path and does not bypass the queue; ordinary automation authoring and non-automation flows remain unchanged; context lifecycle integration tracks active topic.
- `test_automation_lock.py` — covers the automation runner single-writer lock (`src/voxera/automation/lock.py`) and locked runner wrapper (`run_due_automations_locked`): lock acquisition succeeds on first try; second concurrent attempt returns busy; release allows reacquisition; locked runner returns busy with empty results when lock is held; locked runner submits normally when lock is available; summary message reflects outcomes; empty queue returns ok; systemd unit files exist with correct shape, command, and cadence wiring.

### Time-aware context
- `test_time_context.py` — covers the time-context helpers in `src/voxera/vera/time_context.py`: current time context returns structured data; deterministic snapshot with fixed `now`; UTC offset formatting for zero/negative/positive-with-minutes (UTC+05:30); single-digit-day natural phrasing; elapsed-time formatting for recent timestamps including boundary cases; time-until formatting for future timestamps; past/future flagging in the `_since_ms` / `_until_ms` wrappers; relative-day classification (today/yesterday/tomorrow/explicit date); automation timing descriptions for past, future, crossing-midnight-tomorrow, and crossing-midnight-yesterday cases; time question detection and direct answers from the system clock; false-positive guards for lifecycle/drafting hijacks ("what date did you save that?", "what time did that run?", "current time since last run"); no fabricated execution history when timestamps are absent; prompt/instruction surfaces reflect time-aware capability; time context block for prompt injection; operator assistant system prompt includes time context; early exit dispatch handles time questions.

### Prompt surface integrity
- `test_prompts.py` — prompt doc loading, composition ordering, role-capability wiring, output-quality-defaults presence across all roles, automation awareness in shared prompts, unsupported features not marked active, save-vs-execute wording, non-empty structured output from all composed prompts.

### Misc
- `test_inbox.py`
- `test_demo_cli.py`
- `test_e2e_smoke_script.py`
- `test_docs_consistency.py`
- `test_mypy_ratchet.py`
- `test_version_source.py`

### Helpers + fixtures
- `conftest.py`
- `vera_session_helpers.py`

### Golden baselines
`tests/golden/` contains CLI help baselines (`voxera_help.txt`, `voxera_queue_help.txt`, etc.) and the empty-queue health snapshot. Updated through `make golden-update` (intentional) and verified by `make golden-check`.

## Red-team regression suite

`tests/test_security_redteam.py` holds the 17-test adversarial suite covering:

- intent hijack resistance
- planner mismatch enforcement
- traversal metadata rejection
- approval-state integrity
- progress-evidence consistency
- prompt boundary controls
- direct mutation gate
- queue path allowlist enforcement

This file is part of the merge-readiness gate. Any security regression blocks merge.

## Runtime validation (STV pattern)

The "sync, test, validate" method used for meaningful PRs (see `Testing-Method.txt` for the full ladder):

1. **Sync + reinstall** — `git fetch / pull / pip install -e .`
2. **Full validation** — `ruff / mypy / pytest / security / golden / validation / merge-readiness`
3. **Service bring-up** — restart `voxera-daemon`, `voxera-panel`, `voxera-vera` user services; `voxera doctor --quick`; `voxera queue status`; `voxera queue health`.
4. **CLI-primitive test** — exercise the feature through `voxera run`, `voxera missions`, `voxera queue files`, etc.
5. **Vera / panel test** — exercise the feature via the actual operator UX.
6. **Queue-truth test** — drop raw JSON into `inbox/`, inspect panel and state sidecar.
7. **Artifact inspection** — confirm canonical artifacts exist and are coherent.
8. **Fail-closed variant** — exercise an approval-blocked, boundary-blocked, or missing-input path and confirm canonical failure.
9. **Regression check** — verify at least one adjacent previously-working behavior still works.

## Change-surface quick map

| You're changing… | Also run / touch |
|---|---|
| Queue daemon loop / buckets | `test_queue_daemon.py`, `test_queue_daemon_contract_snapshot.py`, `test_queue_execution_contracts.py` |
| Canonical payload contract | `test_queue_constitution_contracts.py`, `test_queue_artifact_minimum_regression.py` |
| Result consumer / review | `test_queue_result_consumers.py`, `test_evidence_review.py` |
| Mission templates / runner | `test_missions.py`, `test_system_inspect.py`, `test_mission_planner.py` |
| Skill registry / manifest | `test_registry.py`, `test_skill_metadata_and_runners.py`, `test_skill_result_payloads.py` |
| Path boundaries | `test_files_control_plane_boundaries.py`, `test_file_intent.py`, `test_security_redteam.py` |
| Policy / mutation gate | `test_policy.py`, `test_direct_mutation_gate.py`, `test_execution_evaluator.py`, `test_capability_semantics.py` |
| Panel routes | `test_panel.py`, `test_panel_automations.py`, `test_panel_contract_snapshot.py` |
| Vera reply surface | `test_vera_chat_reliability.py`, `test_vera_contextual_flows.py`, `test_vera_runtime_validation_fixes.py` |
| Vera preview / submit | `test_vera_preview_materialization.py`, `test_vera_preview_stabilization.py`, `test_vera_preview_submission.py`, `test_vera_draft_revision.py`, `test_draft_content_binding.py` |
| Vera preview ownership / routing lanes | `test_vera_preview_stabilization.py`, `src/voxera/vera/preview_ownership.py`, `src/voxera/vera_web/preview_routing.py` |
| Vera review / linked completions | `test_evidence_review.py`, `test_linked_job_review_continuation.py`, `test_result_surfacing.py` |
| Vera investigation / hidden compiler | `test_vera_investigation_derivations.py`, `test_vera_hidden_compiler.py`, `test_vera_compiler_leakage.py`, `test_vera_brave_search.py` |
| Vera web markdown rendering | `test_vera_web_markdown_render.py` |
| CLI surface / golden | `test_cli_*.py`, `test_golden_surfaces.py`, `tests/golden/*` |
| Config / runtime / secrets | `test_config_settings.py`, `test_dev_contract_config_integration.py`, `test_secrets.py`, `test_setup_wizard.py` |
| Ops / incident bundle | `test_ops_bundle.py`, `test_ops_bundle_includes_config_snapshot.py`, `test_diagnostics_pack.py` |
| Automation object model / storage | `test_automation_object_model.py` |
| Automation runner / history | `test_automation_runner.py` |
| Automation operator CLI | `test_automation_operator_cli.py` |
| Vera automation lifecycle management | `test_vera_automation_lifecycle.py` |
| Time-aware context / timing helpers | `test_time_context.py`, `src/voxera/vera/time_context.py` |
| AI instruction prompts / system prompt docs | `test_prompts.py`, `docs/prompts/**/*.md`, `src/voxera/prompts.py` |
| Docs consistency | `test_docs_consistency.py` |

## E2E scripts

- `scripts/e2e_smoke.sh` — lightweight smoke loop.
- `scripts/e2e_opsconsole.sh` — ops-console e2e (used by `make e2e`).
- `scripts/e2e_golden4.sh` — full-validation e2e (used by `make full-validation-check`).

## Observability and operator surfaces during tests

- `voxera queue health --json` — machine-readable queue health; ideal for scripted assertions.
- `voxera doctor --quick` / `voxera doctor --self-test` — diagnostic snapshots.
- `voxera ops capabilities` — deterministic capabilities snapshot used by tests and the panel.
- `voxera ops bundle system` / `voxera ops bundle job <ref>` — incident bundles for post-mortem.
- Panel `/hygiene`, `/recovery`, and `/jobs/{id}/bundle` — operator-facing equivalents.

## Docs consistency gate

`tests/test_docs_consistency.py` enforces simple consistency between documented surfaces and code (for example, that every referenced mission id actually exists, that CLI help still matches the golden surface baselines, etc.). If you change a user-facing doc that this test is aware of, run the docs consistency test alongside golden-check.
