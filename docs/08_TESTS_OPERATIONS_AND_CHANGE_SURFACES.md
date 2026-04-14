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
- `test_cli_config_guard.py`
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

### First-run walkthrough
- `test_first_run_tour.py` — focused tests for the interactive Vera walkthrough: tour request detection (positive matches + false-positive rejection), fresh session detection (fresh/suppressed), walkthrough start (creates preview + state), step advancement (preview updates at each step, rename changes path), final-step non-auto-submit invariant (advance returns None, preview still exists, walkthrough still active until submit), walkthrough clear, landing-page hint rendering (shown for fresh, hidden after chat, conditional on flag, not in persisted turns), and end-to-end /chat integration (tour request creates preview, refinement steps update preview, submit uses governed path and clears walkthrough state, non-tour messages don't trigger walkthrough).

### Panel + panel contract snapshot
- `test_panel.py` — operator Basic-auth 401 paths, CSRF 403 guard, per-IP lockout 429 behavior, panel security counters, lockout/window semantics, **dashboard zone hierarchy** (`test_home_dashboard_zone_hierarchy` — pins the five-zone layout ordering: KPI → Primary → Secondary → Tertiary → Bottom, verifies key sections render and Approvals/Active Work appear before Daemon Health/Queue Details; `test_home_kpi_cards_highlight_nonzero_counts` — verifies `kpi-danger` class appears when failed > 0), and **job detail page hierarchy** (`test_job_detail_page_hierarchy_and_section_ordering` — pins section ordering: status banner → evidence → execution → timeline → audit, verifies `jd-status-banner` / `jd-status-done` / `jd-badge-lg` CSS classes render; `test_job_detail_approval_renders_before_evidence` — pins approval card appearing before evidence sections for fast operator action; `test_job_detail_actions_section_renders_retry_cancel_bundle` — pins actions bar rendering before evidence with retry/cancel/bundle controls) are exercised end-to-end through the FastAPI `TestClient`. These tests pin the auth-enforcement behavior that is now implemented in `src/voxera/panel/auth_enforcement.py` and consumed by `panel/app.py` via `require_operator_basic_auth` / `require_mutation_guard`. Lockout tests monkeypatch `panel_module._now_ms`; because `auth_enforcement` reaches back through the `panel.app` module for the shared wrappers (`_now_ms`, `_health_queue_root`, `_panel_security_counter_incr`), the patches still drive the auth flow exactly as before. After PR C, `_health_queue_root` / `_panel_security_counter_incr` / `_panel_security_snapshot` / `_auth_setup_banner` are thin wrappers in `panel/app.py` that forward to `voxera.panel.security_health_helpers`; the wrapper-based reach-back pattern is preserved, so monkeypatching any of them on `panel.app` still drives the auth flow and the home/jobs/hygiene/automations pages through the extracted helpers.
- `test_panel_auth_enforcement_extraction.py` — narrow extraction-contract tests (6) that pin the shape of PR A: `auth_enforcement.py` owns the two documented entry points `require_operator_basic_auth(request)` and `require_mutation_guard(request)`; `panel.app._require_mutation_guard` is a literal alias for `auth_enforcement.require_mutation_guard`; `panel.app._require_operator_auth_from_request` is a thin wrapper that forwards to `require_operator_basic_auth`; `panel.app._operator_credentials` is the re-exported `auth_enforcement._operator_credentials` (for the existing `test_dev_contract_config_integration` contract test); `panel.app` does not re-define the extracted private helpers (`_client_ip`, `_panel_auth_state_update`, `_panel_auth_state_prune`, `_active_lockout_until_ms`, `_log_panel_security_event`, `_request_meta`, `_PanelSecurityRequestLike`); the reach-back pattern (`auth_enforcement._now_ms` / `_health_queue_root` / `_panel_security_counter_incr` looking up the attribute on `panel.app` at call time) is exercised directly via `monkeypatch`; and the fail-closed 401 path on `require_operator_basic_auth` with a missing `Authorization` header is asserted at the unit level. A future panel-decomposition PR that silently reintroduces any of those helpers locally in `panel/app.py` will fail this file loudly.
- `test_panel_security_health_helpers_extraction.py` — narrow extraction-contract tests (16) that pin the shape of **PR C** (third small panel extraction — panel security / health snapshot helper cluster only): `security_health_helpers.py` owns the four documented entry points `health_queue_root(queue_root)`, `panel_security_counter_incr(queue_root, key, *, last_error)`, `panel_security_snapshot(queue_root)`, and `auth_setup_banner(settings)`; `panel.app` still exposes the thin wrapper callbacks `_health_queue_root`, `_panel_security_counter_incr`, `_panel_security_snapshot`, `_auth_setup_banner` and each wrapper's source visibly forwards to the extracted helper (`_health_queue_root_impl(...)`, `_panel_security_counter_incr_impl(...)`, `_panel_security_snapshot_impl(...)`, `_auth_setup_banner_impl(...)`); `panel.app._auth_setup_banner`'s wrapper body no longer contains the inline banner body strings (`"Setup required"`, `"VOXERA_PANEL_OPERATOR_PASSWORD"`, `"systemctl --user edit voxera-panel.service"`) — the delegation is visible; `panel.app` no longer imports `increment_health_counter` / `read_health_snapshot` directly (`hasattr` check), enforcing that the helper module is the single panel-side caller of those health primitives; `security_health_helpers.py` does NOT reach back into `panel.app` via any import (AST-level check rules out `from . import app` / `from .app import …` / `from .routes_* import …`), pinning the explicit-args architecture invariant matching PR B's `queue_mutation_bridge`; the PR A reach-back-via-wrapper pattern still works — `monkeypatch.setattr(panel_module, "_health_queue_root", ...)` and `monkeypatch.setattr(panel_module, "_panel_security_counter_incr", ...)` are still visible through `auth_enforcement._health_queue_root` / `_panel_security_counter_incr` at call time; `health_queue_root` semantics preserved exactly (no `VOXERA_HEALTH_PATH` → configured queue root; `VOXERA_HEALTH_PATH` + explicit `VOXERA_QUEUE_ROOT` → configured queue root; `VOXERA_HEALTH_PATH` + default repo queue → `None`; `VOXERA_HEALTH_PATH` + non-default queue → configured queue root); `panel_security_counter_incr` writes land in the snapshot counters and `panel_security_snapshot` reads them back (round-trip); `panel_security_snapshot` returns `{}` for an empty queue root; `auth_setup_banner` returns `None` when `panel_operator_password` is set and returns the full four-key dict (`title`, `detail`, `path_hint`, `commands`) when empty or `None`; and the thin wrappers in `panel.app` resolve `_queue_root()` / `_settings()` at call time so monkeypatching either of those on `panel.app` drives the forwarded helper call exactly as before. HTTP-level behavior is still covered by `test_panel.py::test_panel_security_*`, `test_panel.py::test_panel_hygiene_*`, and the templated pages that render the auth banner; this file pins the *shape* of the extraction.
- `test_panel_health_view_helpers_extraction.py` — narrow extraction-contract tests (20) that pin the shape of **PR E** (fifth small panel extraction — health-view / formatting helper cluster only): `health_view_helpers.py` owns the two documented view-builder entry points `daemon_health_view(health)` and `performance_stats_view(queue, health)`, plus the five tiny formatting / history-line helpers that only exist to support those two views (`format_ts`, `format_ts_seconds`, `format_age`, `history_value`, `history_pair`); `panel.app` still exposes the thin wrapper callbacks `_daemon_health_view`, `_performance_stats_view`, `_format_ts` and each wrapper's source visibly forwards to the extracted helper (`_daemon_health_view_impl(...)`, `_performance_stats_view_impl(...)`, `_format_ts_impl(...)`); `panel.app` no longer defines the extracted private helper bodies `_format_ts_seconds`, `_format_age`, `_history_value`, `_history_pair` (`hasattr` check); `panel.app` no longer imports `build_health_semantic_sections` or `datetime` directly, enforcing that the helper module is the single panel-side caller of those primitives; `panel.app._performance_stats_view` wrapper body no longer contains the inline `historical_counters` / `brain_fallback_reason_timeout` literals and `panel.app._daemon_health_view` wrapper body no longer contains the `last_brain_fallback` / `lock_stale_age_label` literals — the delegation is visible; `health_view_helpers.py` does NOT reach back into `panel.app` via any import (AST-level check rules out `from . import app` / `from .app import …` / `from .routes_* import …`), pinning the explicit-args architecture invariant matching PR B's `queue_mutation_bridge`, PR C's `security_health_helpers`, and PR D's `job_detail_sections`; formatting semantics are preserved exactly across the edge cases the home-page rendering depends on (`format_ts(None)` / `format_ts(0)` / `format_ts(-1)` return em-dash; `format_ts(1700000000000) == "2023-11-14 22:13:20 UTC"`; `format_ts_seconds` mirrors the seconds variant; `format_age` returns em-dash for `None`/negative, `"{n}s"` under a minute, `"2m 5s"` for 125, `"1m"` for 60, `"60m"` for 3600; `history_value` returns `"-"` for `None`/empty/whitespace; `history_pair` returns `"-"` only when both sides are empty, otherwise `"{val} @ {ts}"`); `daemon_health_view` preserves the lock-status precedence (`lock_status` dict sub-key > `lock_state` fallback → `active`/`locked_by_other` → `held`, `stale`/`reclaimed` → `stale`, else `clear`), the fallback/recovery/shutdown `present` booleans, and the same field-for-field render dict used by `home.html`'s Daemon Health widget on both empty and populated inputs; `performance_stats_view` preserves the queue counts sub-dict, the `build_health_semantic_sections` composition, and every historical counter key (including the six `brain_fallback_reason_*` counters); and **two payload key-set shape locks** freeze the top-level key sets returned by `daemon_health_view` (8 keys: `lock_status` / `lock_pid` / `lock_stale_age_s` / `lock_stale_age_label` / `last_brain_fallback` / `last_startup_recovery` / `last_shutdown` / `daemon_state`) and `performance_stats_view` (4 keys: `queue_counts` / `current_state` / `recent_history` / `historical_counters`) so a later PR that silently adds, renames, or removes a payload key must update the pins in the same commit. HTTP-level behavior is still covered by `test_panel.py::test_home_renders_daemon_health_widget_*` / `test_home_renders_performance_stats_tab` / `test_home_performance_history_missing_shows_dash`; this file pins the *shape* of the extraction.
- `test_panel_session_context.py` — focused tests (49) for the read-only shared Vera session context surfaces on the panel: the job-detail `vera_context` block AND the home-page "Vera Activity" strip. **Job-detail coverage:** asserts that `build_job_detail_payload` attaches an optional `vera_context` dict to the job-detail payload when the job belongs to a Vera session with any usable continuity signal (`active_topic` / `active_draft_ref` / `last_saved_file_ref` / `last_submitted_job_ref` / `last_completed_job_ref`, plus `session_id` / `updated_at_ms` / `is_stale`); context-present / partial cases surface correctly; the real-world post-submit shape — `active_topic` / `active_draft_ref` cleared, `last_submitted_job_ref` and `last_saved_file_ref` populated — still surfaces the strip; single-field fallbacks (only `last_saved_file_ref`, only `last_submitted_job_ref`, only `last_completed_job_ref`) also surface; missing session, missing sessions directory, empty/canonical-empty shared context, malformed session files, context whose only non-empty field is `updated_at_ms` (no ref signal), and whitespace-only values all produce `vera_context: None` fail-soft without raising; wrong-session isolation is verified across every continuity field — a loud unrelated session with all five ref fields populated never bleeds into the owning session's surfaced context; staleness is conservative — `is_stale=True` when context `updated_at_ms` is strictly before the job's state-sidecar `completed_at_ms`, `is_stale=False` when at or after (same-millisecond boundary counts as fresh), `is_stale=None` when the job is not terminal or either timestamp is missing / non-positive / a bool; bool-is-int defense — a `True` value masquerading as an `updated_at_ms` int collapses to 0 via `_coerce_positive_int` so staleness stays `None` rather than `True` / `False`; panel remains strictly read-only w.r.t. shared context (repeated `build_job_detail_payload` calls leave the stored context unchanged, and `job_detail_sections` only imports `read_session_context` — never `write_session_context` / `update_session_context` / `clear_session_context`); and three end-to-end template tests via `TestClient` confirm the "Vera Activity" strip renders with the `active_topic` / `active_draft_ref` / freshness label when present, renders with the fallback `Last submitted job` / `Last saved file` rows when only those are populated (no em-dash placeholder rows for absent topic/draft), and is hidden entirely when absent. **Home-page coverage (new):** `build_home_vera_activity(queue_root, *, now_ms=None)` in `src/voxera/panel/home_vera_activity.py` scans `queue_root/artifacts/vera_sessions/*.json` fail-soft, picks the most-recently-updated session carrying a usable continuity signal (same gate as the job-detail helper), and returns a small dict with those fields plus `session_id` / `updated_at_ms` / `freshness`. Freshness is an operator-visible continuity hint only — `fresh` (≤1h), `aging` (≤24h), `stale` (>24h), or `unknown` (no/zero/bool `updated_at_ms`) — and is NEVER read as authority over canonical queue / health / artifact truth. Unit tests cover: missing / empty sessions directory → `None`; session with no shared context → `None`; gate — context with only `updated_at_ms > 0` and no ref signals → `None`; single-session present with topic/draft surfaces correctly; fallback post-submit shape (only `last_submitted_job_ref` / `last_saved_file_ref`) surfaces; multiple sessions → freshest signal-bearing session wins; malformed session file is ignored fail-soft; freshness buckets (fresh/aging/stale) against an injected `now_ms`; freshness is `unknown` when `updated_at_ms == 0` and bool-is-int defense coerces `True` → 0 → `unknown`; helper is strictly read-only (repeated calls leave stored context unchanged); AST check pins `home_vera_activity` imports `read_session_context` only — never `write_session_context` / `update_session_context` / `clear_session_context`. **End-to-end home-render coverage (new)** via `TestClient(panel_module.app)` with `monkeypatch.setattr(panel_module.Path, "home", ...)`: the "Vera Activity" strip renders when context is present with the `Read-only shared Vera session context` note; is hidden when no context / no sessions / only-`updated_at_ms` / malformed session files; renders only the populated ref rows in the real-world post-submit shape and does NOT emit em-dash placeholder rows for absent fields; does NOT override canonical queue/health truth — with an empty queue and a Vera context referencing a ghost `last_submitted_job_ref`, canonical KPI cards and "No pending queue approvals" / "No active jobs currently" still render authoritatively and the strip is clearly labeled "Supplemental only"; the canonical Daemon Health widget and queue sections are placed visually before the strip (substring-index ordering check: `Daemon Health` / `Queue Summary` / `Approval Command Center` all appear before `Vera Activity`); stale contexts are surfaced with a `stale` badge rather than hidden; repeated home renders leave shared context byte-for-byte unchanged; AST check pins `routes_home` imports `build_home_vera_activity` and no mutation helpers. A **return-shape lock** test pins the exact 8-key set returned by `build_home_vera_activity` (`session_id` / `active_topic` / `active_draft_ref` / `last_saved_file_ref` / `last_submitted_job_ref` / `last_completed_job_ref` / `updated_at_ms` / `freshness`) so a later change that silently adds, renames, or removes a key must update the pin in the same commit.
- `test_panel_job_detail_shaping_extraction.py` — narrow extraction-contract tests (14) that pin the shape of **PR D** (fourth small panel extraction — job-detail shaping cluster only): `job_detail_sections.py` owns the three documented entry points `build_job_detail_payload(queue_root, job_id)`, `build_job_progress_payload(queue_root, job_id)`, and `build_job_detail_sections(...)`; `job_presentation.py` owns the tiny `job_artifact_flags(queue_root, job_id)` helper that powers the per-row artifact chips on `GET /jobs`; `panel.app` still exposes the thin wrapper callbacks `_job_detail_payload`, `_job_progress_payload`, `_job_artifact_flags` and each wrapper's source visibly forwards to the extracted builder (`_build_job_detail_payload_impl(...)`, `_build_job_progress_payload_impl(...)`, `_job_artifact_flags_impl(...)`) with the same `(queue_root, job_id) -> dict` route-callback signature `register_job_routes` expects; `panel.app` no longer defines the extracted private loaders (`_artifact_text`, `_safe_json`, `_load_actions`, `_read_generated_files`, `_payload_lineage`) — those live behind `job_detail_sections` now; `panel.app` no longer imports `tail` / `lookup_job` / `queue_snapshot` / `resolve_structured_execution` directly (`hasattr` check), enforcing that the builder module is the single panel-side caller of those primitives; `job_detail_sections.py` does NOT reach back into `panel.app` via any import (AST-level check rules out `from . import app` / `from .app import …` / `from .routes_* import …`), pinning the explicit-args architecture invariant matching PR B's `queue_mutation_bridge` and PR C's `security_health_helpers`; 404 semantics are preserved exactly (`build_job_detail_payload` raises `HTTPException(404, "job not found")` when the job cannot be located and the artifacts directory does not exist); `job_artifact_flags` reports the four canonical artifact presence flags (`plan`, `actions`, `stdout`, `stderr`) with file-existence checks under `queue_root/artifacts/{stem}`; queue-truth precedence is preserved (structured execution > state sidecar > raw bucket for `lifecycle_state` / `terminal_outcome`, success-terminal recent-timeline filter drops stale `assistant_advisory_failed` / `queue_job_failed` events); `build_job_progress_payload` still derives from `build_job_detail_payload` and agrees on `job_id` / `bucket` / `lineage` passthrough (composition sanity check); the thin wrappers in `panel.app` forward `(queue_root, job_id)` through to the extracted builders byte-for-byte; and two **payload key-set shape locks** freeze the exact top-level key sets returned by `build_job_detail_payload` (32 keys: `job_id` / `bucket` / `job` / `approval` / `state` / `failed_sidecar` / `lock` / `paused` / `plan` / `actions` / `stdout` / `stderr` / `generated_files` / `artifact_files` / `artifact_inventory` / `artifact_anomalies` / `job_context` / `lineage` / `child_refs` / `child_summary` / `execution` / `operator_summary` / `policy_rationale` / `evidence_summary` / `why_stopped` / `recent_timeline` / `artifacts_dir` / `audit_timeline` / `has_approval` / `can_cancel` / `can_retry` / `can_delete`) and `build_job_progress_payload` (28 keys: `ok` / `job_id` / `bucket` / `lifecycle_state` / `terminal_outcome` / `current_step_index` / `total_steps` / `last_attempted_step` / `last_completed_step` / `approval_status` / `execution_lane` / `fast_lane` / `intent_route` / `lineage` / `child_refs` / `child_summary` / `parent_job_id` / `root_job_id` / `orchestration_depth` / `sequence_index` / `latest_summary` / `operator_note` / `operator_summary` / `failure_summary` / `stop_reason` / `artifacts` / `step_summaries` / `recent_timeline`) so a later PR that silently adds, renames, or removes a payload key must update the pins in the same commit. HTTP-level behavior is still covered by `test_panel.py::test_job_progress_*` and the templated `/jobs/{job_id}` pages; this file pins the *shape* of the extraction so a later decomposition PR can't silently reinline the builder logic back into `panel/app.py`.
- `test_panel_queue_mutation_bridge_extraction.py` — narrow extraction-contract tests (11) that pin the shape of **PR B** (second small panel extraction — hygiene / queue mutation bridge only): `queue_mutation_bridge.py` owns the two documented entry points `run_queue_hygiene_command(queue_root, args)` and `write_panel_mission_job(queue_root, *, prompt, approval_required)` plus the two bridge helpers `write_queue_job(queue_root, payload)` and `write_hygiene_result(queue_root, key, result, *, now_ms)`; `panel.app` still exposes the thin wrapper callbacks `_write_queue_job`, `_write_panel_mission_job`, `_run_queue_hygiene_command`, `_write_hygiene_result` and each wrapper's source visibly forwards to the extracted bridge function; `panel.app` does not re-define `_trim_tail` or `_repo_root_for_panel_subprocess`; queue-truth semantics are preserved (`write_queue_job` writes `source_lane=panel_queue_create` and leaves no tmp files behind; `write_panel_mission_job` writes `source_lane=panel_mission_prompt`, preserves `expected_artifacts` and `approval_hints`, and the mission-id matches the stored `id`); `run_queue_hygiene_command` is fail-closed for non-zero rc and invalid-JSON stdout (returns `ok=False` + populated `error` / `stderr_tail` / `stdout_tail` — never raises); `write_hygiene_result` uses the injected `now_ms` callable for `updated_at_ms`; the reach-back-via-wrapper pattern works — `panel.app._write_hygiene_result` reads `_now_ms` from its module globals at call time, so `monkeypatch.setattr(panel_module, "_now_ms", ...)` still drives the `updated_at_ms` stamp through the thin wrapper; `panel.app.subprocess is subprocess` and `panel.app.sys is sys` — pins the `# noqa: F401` re-export surface so a later PR can't silently drop those imports and break every `test_panel.py::test_hygiene_*` monkeypatch; and `queue_mutation_bridge.py` does NOT reach back into `panel.app` via any import (AST-level check rules out `from . import app` / `from .app import …` / `from .routes_* import …`), pinning the explicit-args architecture invariant that distinguishes PR B from PR A's deliberate reach-back pattern. A future panel-decomposition PR that silently reintroduces any of the extracted bridge logic locally in `panel/app.py` — or sneaks a circular dependency back into the bridge — will fail this file loudly.
- `test_panel_degraded_assistant_bridge_extraction.py` — narrow extraction-contract tests (18) that pin the shape of the degraded-assistant bridge extraction (panel decomposition — degraded-assistant bridge / messaging cluster only): `degraded_assistant_bridge.py` owns the five documented entry points `assistant_stalled_degraded_reason(context, request_result, *, now_ms)`, `create_panel_assistant_brain(provider)`, `generate_degraded_assistant_answer_async(...)`, `generate_degraded_assistant_answer(...)`, and `persist_degraded_assistant_result(...)`; `panel.app` still exposes the module-level aliases `_assistant_stalled_degraded_reason`, `_create_panel_assistant_brain`, `_persist_degraded_assistant_result` (identity-equal to the bridge functions) and thin wrappers `_generate_degraded_assistant_answer` / `_generate_degraded_assistant_answer_async` whose source visibly delegates to the bridge; `panel.app.load_app_config` is identity-equal to `degraded_assistant_bridge.load_app_config`; `routes_assistant` imports the bridge entry points and does not locally re-define any of the extracted functions (AST-level check for top-level function definitions); `panel.app` no longer defines the extracted private helpers (`_degraded_mode_disclosure`, `_coerce_int`, `_assistant_request_ts_ms`, `_ASSISTANT_STALL_TIMEOUT_MS`, `_ASSISTANT_FALLBACK_REASONS`, `_ASSISTANT_UNAVAILABLE_STATES`); `degraded_assistant_bridge.py` does NOT reach back into `panel.app` via any import (AST-level check rules out `from . import app` / `from .app import …` / `from .routes_* import …`); stall-detection semantics are preserved (returns `None` for empty result / already-answered / already-degraded; returns `daemon_paused` / `daemon_unavailable` / `queue_processing_timeout` / `advisory_transport_stalled` for the expected contexts); persistence semantics are preserved (`persist_degraded_assistant_result` writes the canonical `assistant_response.json` artifact with `advisory_mode=degraded_brain_only`, `schema_version=2`, `kind=assistant_question`); the bridge-patching pattern works — `panel.app._generate_degraded_assistant_answer_async` source contains the `_degraded_assistant_bridge.load_app_config = load_app_config` and `_degraded_assistant_bridge.create_panel_assistant_brain = _create_panel_assistant_brain` assignments; and an end-to-end monkeypatch flow test proves that `monkeypatch.setattr(panel_module, "load_app_config", ...)` / `monkeypatch.setattr(panel_module, "_create_panel_assistant_brain", ...)` still drives the degraded-answer generation through the bridge module. HTTP-level behavior is still covered by `test_panel.py::test_degraded_assistant_*` and `test_panel.py::test_operator_assistant_page_degrades_*`; this file pins the *shape* of the extraction.
- `test_panel_voice_status.py` — operator-facing voice status panel page and JSON endpoint (17 tests): HTML page renders, foundation/STT/TTS sections present, disabled badges when foundation off, JSON link present, enabled+configured state shows available/backend names, no fake ready badges when disabled, JSON endpoint ok/shape/stt-fields/tts-fields/disabled-state/configured-state/serializable, auth required for both HTML and JSON endpoints.
- `test_panel_automations.py` — automation dashboard routes: list page, detail page, enable/disable, run-now (queue-submitting only), history display, missing/malformed handling, auth/mutation guard.
- `test_panel_table_usability.py` — focused tests (14) for panel table usability improvements: sticky-header `table-scroll` class presence on Approvals/Completed Jobs/Job Browser tables, consistent `empty-state` container rendering for Active Work/Completed Jobs/Mission Library/Approvals/Failed Jobs/Audit/Job Browser empty conditions, `cell-count` right-alignment class on Daemon Lock History/Panel Security Counters/Mission Library count columns, and CSS class existence pins (`table-scroll`, `cell-count`, `empty-state`, `lifecycle-cell`, `step-progress`) in the static `panel.css`.
- `test_panel_accessibility.py` — focused tests (26) for the panel accessibility and mobile-responsiveness hardening pass: skip-link and `<main>` landmark presence (home, jobs, job detail), page-nav `aria-label`, nav separator `aria-hidden`, ARIA tablist/tab/tabpanel pattern with `aria-selected`/`aria-controls`/`aria-labelledby`, keyboard navigation script, queue status badge `role="status"`, approval/retry/cancel button `aria-label` (home, jobs), queue controls `role="group"`, KPI status text hints (warn/danger/zero), artifact pill check/cross marks, jobs filter `aria-label` and responsive classes, CSS `:focus-visible` rules, skip-link CSS, `.kpi-status-hint` CSS, tablet breakpoint, `.jobs-filter-form`/`.jobs-filter-row` CSS, detail-grid responsive rule, mobile tap-target improvements, job detail skip-link/main/action-bar `role="group"`. Does not claim full WCAG compliance — pins the specific improvements shipped.
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
- `test_vera_chat_polish.py` — CSS and template structure pins for the Vera chat interface polish (11 tests): thinking-indicator CSS rules, consecutive same-role grouping, shared accent-dot rule, keyboard hint pseudo-element, tour hint styling, responding-indicator removal, page structure (thread/composer/send-btn), JS thinking indicator creation on submit, user-echo bubble on submit, poll skip during submit, static vera.css serving.
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
- `test_setup_wizard.py` — provider key choice flows, OpenRouter model selection, cloud brain slot sequencing, launch/service lifecycle, web investigation config, post-setup validation (brain config checks, traffic-light summary rendering, quick doctor integration, graceful failure handling, mixed result truthfulness, setup completion with warnings), and post-setup next-steps output (compact 3-step default, verbose full-list path, explanatory text, output compactness).
- `test_secrets.py`

### Brain / providers
- `test_brain_fallback.py`
- `test_openai_compat_headers.py`
- `test_openrouter_catalog.py`

### Security / red-team
- `test_security_redteam.py`

### Voice foundation, protocol, and status
- `test_voice_foundation.py`
- `test_voice_stt_protocol.py` — pins the STT request/response protocol contract: request construction (all valid sources, case normalization, unknown source rejection, auto-generated ids, explicit ids/timestamps), success response shape (transcript whitespace normalization, empty transcript → None), failure response shape (error + error_class carriage), unavailable response convenience builder (required error_class, backend passthrough), fail-closed status normalization (unknown/empty/None status → unavailable), error_class passthrough policy (arbitrary strings accepted), serialization helpers (request/response as_dict roundtrip, field-count guard, JSON serializability), frozen immutability.
- `test_voice_stt_status.py` — pins the STT status surface contract: available when fully configured, disabled when foundation or input off, unconfigured when no backend, fully-disabled defaults, frozen immutability, truthful unavailable handling (available ≠ transcription proven), dict serialization roundtrip (field-count guard, JSON serializability), integration with flags loader (config file, empty config), doctor integration (check presence, ok-when-disabled, warn-when-enabled-but-unconfigured).
- `test_voice_tts_protocol.py` — pins the TTS request/response protocol contract: request construction (all valid formats, case normalization, unknown format rejection, empty text rejection, whitespace-only text rejection, text stripping, speed clamping, auto-generated ids, explicit ids/timestamps, frozen immutability, optional field defaults), success response shape (audio_path stripping, empty audio_path → None, timing fields), audio_path semantics (present on success, None on failure/unavailable, audio_duration_ms None on failure), failure response shape (error + error_class carriage, unsupported status), unavailable response convenience builder (required error_class, backend passthrough), nullable fields on failure/unavailable, fail-closed status normalization (unknown/empty/None/uppercase status → unavailable or correct lowercase, all valid statuses pass through), error_class passthrough policy (arbitrary strings accepted, None passes through, all six canonical constants pass through), serialization helpers (request/response as_dict roundtrip, field-count guard, JSON serializability), schema version correctness (constant is 1, request/response/unavailable carry version).
- `test_voice_tts_status.py` — pins the TTS status surface contract: available when fully configured, disabled when foundation or output off, unconfigured when no backend, fully-disabled defaults, frozen immutability, truthful unavailable handling (available ≠ synthesis proven), last_error passthrough/stripping/None, dict serialization roundtrip (field-count guard, JSON serializability), integration with flags loader (config file, empty config, env vars), doctor integration (check presence, ok-when-disabled, warn-when-enabled-but-unconfigured).
- `test_voice_stt_adapter.py` — pins the STT backend adapter boundary and fail-soft transcription path: STTAdapterResult frozen immutability, defaults, and timing fields (inference_ms, audio_duration_ms), NullSTTBackend truthful behavior (backend_name, supports_source returns False for all, unavailable result, protocol conformance), STTBackend protocol structural conformance (stub adapters satisfy protocol), supports_source behavior pinned, transcribe_stt_request no-adapter path (unavailable + backend_missing), NullSTTBackend transcription path (unavailable + backend_missing — availability problem, not runtime failure), successful adapter (transcript, language, timing, whitespace normalization), timing fields pass-through from adapter to response, unsupported input source (STTBackendUnsupportedError → unsupported + unsupported_source, empty message fallback), backend exception (RuntimeError → failed + backend_error, never raises), adapter availability-class error (disabled/backend_missing error_class → unavailable status), adapter runtime error (custom/unknown/None error_class → failed status), empty/None transcript (failed + empty_audio), normalization consistency with input.py, all valid input sources succeed with working backend, async entry point (transcribe_stt_request_async preserves success/fail-soft/exception semantics, returns STTResponse).
- `test_voice_whisper_backend.py` — pins the WhisperLocalBackend STT adapter: protocol conformance (satisfies STTBackend), lazy model loading (model_loaded=False at construction), supports_source behavior (True for audio_file, False for microphone/stream/unknown), missing faster-whisper dependency handling (returns backend_missing, not crash), unsupported source handling (microphone/stream raise STTBackendUnsupportedError, map to unsupported through entry point), audio_path requirements (missing path returns error, nonexistent file returns error), successful transcription with mocked model (transcript, language, timing fields, pass-through to STTResponse), empty transcript after normalization (failed + empty_audio), transcription failure (backend exception handled), configuration (default values, explicit overrides, env var overrides), async entry point (success, missing dep, unsupported source), STTRequest.audio_path field (default None, set, whitespace stripping, serialization).
- `test_voice_tts_adapter.py` — pins the TTS backend adapter boundary and fail-soft synthesis path: TTSAdapterResult frozen immutability, defaults, and timing fields (inference_ms, audio_duration_ms), NullTTSBackend truthful behavior (backend_name, supports_voice returns False for all, unavailable result, custom reason, protocol conformance), TTSBackend protocol structural conformance (stub adapters satisfy protocol), supports_voice behavior pinned, synthesize_tts_request no-adapter path (unavailable + backend_missing), NullTTSBackend synthesis path (unavailable + backend_missing — availability problem, not runtime failure), successful adapter (audio_path, audio_duration_ms, inference_ms, timing), missing audio_path does not fake success (None audio_path → failed, whitespace-only audio_path → failed), unsupported voice/format (TTSBackendUnsupportedError → unsupported + unsupported_format, empty message fallback), backend exception (RuntimeError → failed + backend_error, never raises), adapter availability-class error (disabled/backend_missing error_class → unavailable status), adapter runtime error (custom/unknown/None error_class → failed status), backend name propagation (success, failure, unsupported, null, no-adapter), error_class passthrough preserved, async entry point (synthesize_tts_request_async preserves success/fail-soft/exception semantics, returns TTSResponse).
- `test_voice_piper_backend.py` — pins the PiperLocalBackend TTS adapter: protocol conformance (satisfies TTSBackend), backend_name stability, lazy voice loading (model_loaded=False at construction), supports_voice behavior (True for all voices), missing piper-tts dependency handling (returns backend_missing, not crash), unsupported format handling (mp3/ogg raise TTSBackendUnsupportedError, map to unsupported through entry point), voice load failure (clean error result, not crash), successful synthesis with mocked voice (audio_path exists, valid WAV file, timing fields, audio duration, entry point pass-through), no fake success (empty audio data returns error, failed synthesis returns no path), synthesis failure (backend exception handled, no exception leakage through entry point), speaker handling (default none, explicit, env var, override, numeric coercion, no-speaker omits kwarg, non-numeric string passthrough), configuration (defaults, explicit overrides, env var overrides, explicit overrides env), multi-chunk audio assembly, timing fields (inference_ms nonnegative), async entry point (success, missing dep, exception fail-soft), export surface (PiperLocalBackend exported from voice package).
- `test_voice_stt_pipeline.py` — pins STT backend selection factory, voice input pipeline wiring, and async entry point (44 tests): build_stt_backend returns NullSTTBackend for no-backend/empty/whitespace/unrecognized/disabled-foundation/disabled-input, returns WhisperLocalBackend for whisper_local (case-insensitive, whitespace-trimmed), unrecognized backend error mentions rejected name, unconfigured backend uses default message, NullSTTBackend default reason preserved, canonical backend identifier constant; transcribe_audio_file unconfigured/disabled/unknown returns unavailable, successful mocked transcription through full pipeline (transcript, backend name, timing), request carries audio_path and input_source=audio_file, language and session_id pass-through, missing dependency returns unavailable, nonexistent file returns failed, empty transcript returns failed+empty_audio, backend crash is fail-soft (never raises); pipeline uses canonical STT request/adapter path (schema_version, request_id); source truthfulness (only audio_file); export surface (build_stt_backend, transcribe_audio_file, transcribe_audio_file_async exported from voice package); config-driven integration (flags from config file, flags from env var, env overrides config); distinct request_ids per call; pre-built backend reuse (bypasses factory, same instance reused across calls, None falls through to factory); async entry point (success, unavailable when unconfigured, fail-soft on crash, returns STTResponse, accepts pre-built backend).
- `test_voice_status_summary.py` — pins the combined voice status summary surface contract (18 tests): all-disabled defaults, fully configured STT+TTS, foundation-disabled overrides backend config, STT/TTS enabled-but-unconfigured, JSON serializability, last_tts_error passthrough/None, dependency checks for whisper_local/piper_local (checked + package + hint when missing), no-backend-not-checked, unknown-backend-not-checked, no fake ready when disabled/backend missing, available does not imply runtime success, reason strings present for all unavailable states.
- `test_voice_tts_pipeline.py` — pins TTS backend selection factory, voice output pipeline wiring, and async entry point (50 tests): build_tts_backend returns NullTTSBackend for no-backend/empty/whitespace/unrecognized/disabled-foundation/disabled-output, returns PiperLocalBackend for piper_local (case-insensitive, whitespace-trimmed), unrecognized backend error mentions rejected name, unconfigured backend uses default message, NullTTSBackend default reason preserved, canonical backend identifier constant; synthesize_text unconfigured/disabled/unknown returns unavailable, successful synthesis through full pipeline (audio_path, backend name, timing), request carries text, voice_id/language/session_id/speed/output_format pass-through, empty text raises ValueError (request validation), missing dependency returns unavailable, backend crash is fail-soft (never raises); pipeline uses canonical TTS request/adapter path (schema_version, request_id); backend name propagation (success, failure); export surface (build_tts_backend, synthesize_text, synthesize_text_async exported from voice package); config-driven integration (flags from config file, flags from env var, env overrides config); distinct request_ids per call; pre-built backend reuse (bypasses factory, same instance reused across calls, None falls through to factory); async entry point (success, unavailable when unconfigured, fail-soft on crash, returns TTSResponse, accepts pre-built backend); no-fake-synthesis invariants (None audio_path → failed, whitespace audio_path → failed).

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
| Vera first-run walkthrough | `test_first_run_tour.py`, `test_chat_early_exit_dispatch.py` |
| Vera preview / submit | `test_vera_preview_materialization.py`, `test_vera_preview_stabilization.py`, `test_vera_preview_submission.py`, `test_vera_draft_revision.py`, `test_draft_content_binding.py` |
| Vera preview ownership / routing lanes | `test_vera_preview_stabilization.py`, `src/voxera/vera/preview_ownership.py`, `src/voxera/vera_web/preview_routing.py` |
| Vera review / linked completions | `test_evidence_review.py`, `test_linked_job_review_continuation.py`, `test_result_surfacing.py` |
| Vera investigation / hidden compiler | `test_vera_investigation_derivations.py`, `test_vera_hidden_compiler.py`, `test_vera_compiler_leakage.py`, `test_vera_brave_search.py` |
| Vera web markdown rendering | `test_vera_web_markdown_render.py` |
| Vera chat interface polish | `test_vera_chat_polish.py` |
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
