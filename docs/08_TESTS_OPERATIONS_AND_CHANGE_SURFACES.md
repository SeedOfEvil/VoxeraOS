# 08 — Tests, Operations and Change Surfaces

## Test suite

The pytest suite lives entirely under `tests/` (no nested package). Total
test files in the current tree: ~110, plus `tests/golden/` snapshot
baselines and `tests/conftest.py` shared fixtures.

The Makefile keeps tests deterministic via the `TEST_ENV_PREFIX` that
unsets every `VOXERA_*` env var before invoking pytest and forces
`VOXERA_LOAD_DOTENV=0`.

### Major test groups

**Queue daemon and contracts**

- `test_queue_daemon.py`
- `test_queue_daemon_config_drift.py`
- `test_queue_daemon_contract_snapshot.py`
- `test_queue_constitution_contracts.py`
- `test_queue_execution_contracts.py`
- `test_queue_artifact_minimum_regression.py`
- `test_queue_job_intent.py`
- `test_queue_result_consumers.py`

**CLI surfaces**

- `test_cli_queue.py`
- `test_cli_queue_prune.py`
- `test_cli_queue_reconcile.py`
- `test_cli_queue_remaining_surfaces.py`
- `test_cli_run_args.py`
- `test_cli_secrets.py`
- `test_cli_version.py`
- `test_cli_artifacts.py`
- `test_cli_config_snapshot.py`
- `test_cli_contract_snapshot.py`

**Vera control layer / Vera web**

- `test_vera_web.py`
- `test_vera_chat_reliability.py`
- `test_vera_contextual_flows.py`
- `test_vera_panel.py`
- `test_vera_session_characterization.py`
- `test_vera_runtime_validation_fixes.py`
- `test_vera_live_path_characterization.py`
- `test_vera_investigation_derivations.py`
- `test_vera_brave_search.py`
- `test_vera_compiler_leakage.py`
- `test_vera_hidden_compiler.py`
- `test_vera_draft_revision.py`
- `test_vera_draft_bug_fix.py`
- `test_vera_preview_materialization.py`
- `test_vera_preview_submission.py`
- `test_evidence_review.py`
- `test_linked_job_review_continuation.py`
- `test_session_routing_debug.py`
- `test_session_aware_authored_drafting.py`
- `test_shared_session_context.py`
- `test_shared_session_context_integration.py`
- `test_context_lifecycle.py`
- `test_reference_resolver.py`
- `test_response_shaping.py`
- `test_chat_early_exit_dispatch.py`
- `test_draft_content_binding.py`
- `test_authored_draft_body_fidelity.py`

**Skills + runtime**

- `test_registry.py`
- `test_runner.py`
- `test_execution.py`
- `test_arg_normalizer.py`
- `test_skill_metadata_and_runners.py`
- `test_skill_result_payloads.py`
- `test_files_*` (control plane boundaries, copy/move/delete, list_dir, read/write text, workspace expansion, wave2 skills)
- `test_builtin_skills_terminal_run_once.py`
- `test_open_app.py`
- `test_sandbox_exec_args.py`
- `test_sandbox_exec_integration.py`

**Operator / panel**

- `test_panel.py`
- `test_panel_contract_snapshot.py`
- `test_operator_assistant_queue.py`
- `test_operator_contract_guardrails.py`

**Doctor / health / config / misc**

- `test_doctor.py`
- `test_health.py`
- `test_health_snapshot_isolation.py`
- `test_diagnostics_pack.py`
- `test_config_settings.py`
- `test_config_snapshot.py`
- `test_models_config_strictness.py`
- `test_dev_contract_config_integration.py`
- `test_setup_wizard.py`
- `test_secrets.py`
- `test_policy.py`
- `test_prompts.py`
- `test_planner_context.py`
- `test_mission_planner.py`
- `test_missions.py`
- `test_inbox.py`
- `test_capabilities_snapshot.py`
- `test_capability_semantics.py`
- `test_execution_evaluator.py`
- `test_execution_capabilities.py`
- `test_file_intent.py`
- `test_simple_intent.py`
- `test_code_draft_intent.py`
- `test_demo_cli.py`
- `test_doctor.py`
- `test_docs_consistency.py`
- `test_voice_foundation.py`
- `test_brain_fallback.py`
- `test_openai_compat_headers.py`
- `test_openrouter_catalog.py`
- `test_ops_bundle.py`
- `test_ops_bundle_includes_config_snapshot.py`
- `test_system_inspect.py`
- `test_dry_run_contract.py`
- `test_dryrun_determinism.py`
- `test_direct_mutation_gate.py`
- `test_e2e_smoke_script.py`
- `test_update_script.py`
- `test_version_source.py`

**Golden / red team / type ratchet**

- `test_golden_surfaces.py`
- `test_security_redteam.py`
- `test_mypy_ratchet.py`

A small helper module `tests/vera_session_helpers.py` is shared by the
Vera test files.

## Make targets

`Makefile` exposes the following composite targets:

| Target | Action |
| ------ | ------ |
| `make venv` / `install` | Create venv / `pip install -e .`. |
| `make dev` | `pip install -e ".[dev]"` plus pre-commit hook install. |
| `make fmt` / `fmt-check` | `ruff format .` (apply / check). |
| `make lint` | `ruff check .`. |
| `make type` | `mypy src/voxera`. |
| `make type-check` | `python scripts/mypy_ratchet.py`. |
| `make type-check-strict` | Alias for `type` (full mypy). |
| `make update-mypy-baseline` | `python scripts/mypy_ratchet.py --write-baseline`. |
| `make test` | Full pytest with deterministic env unset. |
| `make e2e` | `bash scripts/e2e_opsconsole.sh`. |
| `make check` | `fmt-check + lint + type + test` (and `e2e` if `CHECK_E2E=1`). |
| `make panel` | `voxera panel --host 127.0.0.1 --port 8787`. |
| `make vera` | `uvicorn voxera.vera_web.app:app --host $VERA_HOST --port $VERA_PORT`. |
| `make daemon-restart` | `systemctl --user restart voxera-daemon.service`. |
| `make release-check` | Version source + panel version + docs consistency + cli version. |
| `make merge-readiness-check` | `quality-check + release-check + security-check`. |
| `make security-check` | `pytest tests/test_security_redteam.py`. |
| `make golden-update` / `golden-check` | Drives `tools/golden_surfaces.py`. |
| `make validation-check` | Format + lint + type + golden + security + targeted contract tests. |
| `make full-validation-check` | `validation-check + merge-readiness-check + test-failed-sidecar` plus full pytest plus `bash scripts/e2e_golden4.sh`. |
| `make services-install / services-restart / services-status / services-stop / services-disable` | Manage `voxera-{daemon,panel,vera}.service` user units. |
| `make vera-start / vera-stop / vera-restart / vera-status / vera-logs` | Vera systemd helpers. |
| `make update` / `update-fast` | `bash scripts/update.sh [--smoke|--skip-tests]`. |
| `make premerge` | Convenience pre-merge gate. |

## End-to-end scripts

- `scripts/e2e_smoke.sh` — minimal smoke run.
- `scripts/e2e_opsconsole.sh` — operator console smoke (`make e2e`).
- `scripts/e2e_golden4.sh` — golden bundle smoke (used by
  `make full-validation-check`).
- `scripts/update.sh` — wraps git pull + venv refresh + optional smoke.
- `scripts/refresh_openrouter_catalog.py` — refreshes
  `src/voxera/data/openrouter_catalog.json`.
- `scripts/mypy_ratchet.py` — keeps `tools/mypy-baseline.txt` honest.

## Operations surfaces

- `voxera doctor` — runs `doctor.doctor_sync` end-to-end.
- `voxera ops capabilities` — capabilities snapshot.
- `voxera ops bundle system` — system ops bundle.
- `voxera ops bundle job` — per-job incident bundle (also exposed via
  `voxera queue bundle`).
- `voxera audit` — tail of `audit.jsonl`.
- `voxera queue health` — daemon health snapshot.
- `voxera queue health-reset` — reset health snapshot.
- `voxera queue prune / reconcile` — hygiene operations.
- `voxera artifacts prune` — artifact retention pass.
- Panel `/hygiene`, `/recovery`, `/bundle/system`, `/jobs/{id}/bundle`.

## Change surfaces (where to look when X changes)

- **Adding a new built-in skill**: drop a manifest in `skills/<group>/<id>/manifest.yml`,
  add a `voxera_builtin_skills/<entry>.py` module, register a
  `manifest_capability_semantics` mapping in `core/capability_semantics.py`,
  add a test under `tests/test_<skill>.py`, refresh `golden_surfaces.py`
  if the operator surface changes.
- **Changing queue lifecycle vocabulary**: edit
  `core/queue_object_model.py` plus the matching tests in
  `tests/test_queue_constitution_contracts.py` and
  `tests/test_queue_daemon_contract_snapshot.py`.
- **Changing payload contract**: edit `core/queue_contracts.py` and
  refresh `tests/test_queue_constitution_contracts.py` plus any
  consumers in `core/queue_result_consumers.py` and `cli_queue.py`.
- **Adding a panel route**: add a `panel/routes_<surface>.py` module,
  register it in `panel/app.py`, add a template under
  `panel/templates/<surface>.html`, and extend `tests/test_panel.py` /
  `tests/test_panel_contract_snapshot.py`.
- **Adding a Vera web route**: extend `vera_web/app.py` and add a test
  under `tests/test_vera_web.py`.
- **Changing the Vera session schema**: update `vera/session_store.py`,
  refresh the `vera/context_lifecycle.py` update points, and extend
  `tests/test_shared_session_context*.py` and
  `tests/test_session_routing_debug.py`.
- **Adding a CLI subcommand**: extend the appropriate `cli_*` module and
  refresh `tests/test_cli_contract_snapshot.py`,
  `tests/test_cli_queue.py`, and `golden_surfaces.py`.
