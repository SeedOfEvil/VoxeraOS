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
- `test_panel.py`
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

### Automation object model, runner, and operator CLI
- `test_automation_object_model.py` — covers the Pydantic model in `src/voxera/automation/models.py` and the file-backed store in `src/voxera/automation/store.py`.
- `test_automation_runner.py` — covers the runner surface in `src/voxera/automation/runner.py` and the history records in `src/voxera/automation/history.py`: due `once_at`, `delay`, and `recurring_interval` definitions emit normal canonical queue jobs via the existing inbox path; non-due / disabled / malformed / unsupported-trigger-kind definitions are skipped; history records carry queue job linkage; updated definition fields (`last_run_at_ms`, `last_job_ref`, `run_history_refs`, `enabled`, `next_run_at_ms`) are saved; one-shot semantics prevent double-submit on repeated runner passes; recurring semantics re-arm `next_run_at_ms` and allow repeated fires; emitted payload matches the saved `payload_template`.
- `test_automation_operator_cli.py` — covers the operator CLI commands in `src/voxera/cli_automation.py`: `list` shows saved definitions; `show` renders a detailed JSON view; `enable` / `disable` flip the enabled flag and persist without rewriting unrelated fields; `history` shows linked run history entries; `run-now` processes through the existing runner and submits via the queue; missing ids return clean errors; malformed definitions and history files are handled safely; `list_history_records` helper returns records filtered by automation id, newest first.

### Misc
- `test_inbox.py`
- `test_demo_cli.py`
- `test_e2e_smoke_script.py`
- `test_prompts.py`
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
| Vera preview / submit | `test_vera_preview_materialization.py`, `test_vera_preview_submission.py`, `test_vera_draft_revision.py`, `test_draft_content_binding.py` |
| Vera review / linked completions | `test_evidence_review.py`, `test_linked_job_review_continuation.py`, `test_result_surfacing.py` |
| Vera investigation / hidden compiler | `test_vera_investigation_derivations.py`, `test_vera_hidden_compiler.py`, `test_vera_compiler_leakage.py`, `test_vera_brave_search.py` |
| CLI surface / golden | `test_cli_*.py`, `test_golden_surfaces.py`, `tests/golden/*` |
| Config / runtime / secrets | `test_config_settings.py`, `test_dev_contract_config_integration.py`, `test_secrets.py`, `test_setup_wizard.py` |
| Ops / incident bundle | `test_ops_bundle.py`, `test_ops_bundle_includes_config_snapshot.py`, `test_diagnostics_pack.py` |
| Automation object model / storage | `test_automation_object_model.py` |
| Automation runner / history | `test_automation_runner.py` |
| Automation operator CLI | `test_automation_operator_cli.py` |
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
