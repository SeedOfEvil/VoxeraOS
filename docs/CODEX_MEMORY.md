## 2026-04-12 — feat(voice): add Whisper local STT backend

- **Motivation**: first real STT backend implementation behind the `STTBackend` adapter boundary established in #323. Resolves the three intentionally deferred gaps from the adapter PR: async entry point, explicit `supports_source()`, and adapter-reported timing fields.
- **Scope (deliberately bounded)**:
  - Add `WhisperLocalBackend` (`src/voxera/voice/whisper_backend.py`): local Whisper STT via `faster-whisper`, supports `audio_file` only, lazy model loading, environment-driven configuration.
  - Extend `STTBackend` protocol with `supports_source(input_source) -> bool`. `WhisperLocalBackend` returns `True` for `audio_file`, `False` for `microphone`/`stream`. `NullSTTBackend` returns `False` for all sources.
  - Add optional timing fields to `STTAdapterResult`: `inference_ms`, `audio_duration_ms`. Carried through to `STTResponse`.
  - Add `audio_path: str | None` to `STTRequest` (additive optional field, schema version stays at 1).
  - Add `transcribe_stt_request_async()` async entry point via `asyncio.to_thread()`.
  - Add `faster-whisper` as optional dependency: `pip install voxera-os[whisper]`.
  - Update voice `__init__.py` exports.
  - Do NOT add voice UI, streaming UX, microphone capture, or panel changes.
- **New module — `src/voxera/voice/whisper_backend.py`**:
  - `WhisperLocalBackend`: satisfies `STTBackend` protocol. `backend_name="whisper_local"`. Lazy model loading on first `transcribe()`. Supports `audio_file` only. `microphone`/`stream` raise `STTBackendUnsupportedError`. Missing `faster-whisper` returns truthful `backend_missing`. Reports `inference_ms` and `audio_duration_ms` on success.
  - Configuration: `VOXERA_VOICE_STT_WHISPER_MODEL` (default: `base`), `VOXERA_VOICE_STT_WHISPER_DEVICE` (default: `auto`), `VOXERA_VOICE_STT_WHISPER_COMPUTE_TYPE` (default: `int8`).
- **Protocol changes**:
  - `STTRequest.audio_path: str | None` (optional, additive — schema version 1 preserved).
  - `STTResponse.inference_ms: int | None`, `STTResponse.audio_duration_ms: int | None` (optional timing fields).
  - `STTAdapterResult.inference_ms: int | None`, `STTAdapterResult.audio_duration_ms: int | None`.
  - `build_stt_request(...)` accepts `audio_path` parameter.
  - `build_stt_response(...)` accepts `inference_ms` and `audio_duration_ms` parameters.
  - Serialization helpers updated for new fields.
- **Adapter boundary changes**:
  - `STTBackend.supports_source(input_source: str) -> bool` added to protocol.
  - `NullSTTBackend.supports_source()` returns `False` for all sources.
  - `transcribe_stt_request()` passes timing fields through on success.
  - `transcribe_stt_request_async()` added — async wrapper via `asyncio.to_thread()`.
- **Test coverage** — `tests/test_voice_whisper_backend.py` (40 tests): protocol conformance, lazy loading, missing dependency handling (backend_missing), supports_source behavior, unsupported source handling, audio_path requirements, multi-segment transcription (joined and normalized), empty segments list (truthful empty_audio), model load failure (clean error result, not leaked exception), successful transcription (mocked), timing field pass-through, empty transcript truthfulness, transcription failure, configuration (defaults, explicit, env), async entry point, STTRequest.audio_path field. `tests/test_voice_stt_adapter.py` updated with supports_source tests, timing field tests, and async entry point tests. `tests/test_voice_stt_protocol.py` updated with timing field serialization coverage and audio_path default assertion.
- **Docs updated**: `docs/09_CORE_OBJECTS_AND_SCHEMA_REFERENCE.md`, `docs/02_CONFIGURATION_AND_RUNTIME_SURFACES.md`, `docs/08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md`, `docs/CODEX_MEMORY.md`.
- **What this does NOT do**: no voice UI, no streaming UX, no microphone capture, no panel changes, no assistant refactors. `microphone` and `stream` are future work. This is one bounded backend PR.
- **Invariants preserved**: existing voice protocol/adapter/status tests updated for new protocol shape; fail-soft semantics preserved; truthful behavior maintained (no fake transcripts, no fake real-time capture); schema version stays at 1 (additive optional fields only).
- **Next safe step**: wire `WhisperLocalBackend` into the voice input pipeline via flags/config, or add microphone/stream support as a subsequent backend.

## 2026-04-12 — feat(voice): add STT backend adapter interface and fail-soft transcription path

- **Motivation**: next bounded step in the voice track — bridge the STT protocol shapes from #322 to a runtime adapter boundary. Defines the smallest credible adapter interface and a fail-soft transcription entry point, without overreaching into full voice UI or streaming UX.
- **Scope (deliberately bounded)**:
  - Add STT backend adapter protocol (`src/voxera/voice/stt_adapter.py`): `STTBackend` structural interface mirroring `Brain` protocol pattern, `STTAdapterResult` frozen dataclass, `STTBackendUnsupportedError` exception, `NullSTTBackend` truthful no-op adapter, `transcribe_stt_request()` fail-soft entry point.
  - Update voice `__init__.py` exports with new public symbols.
  - Add focused contract-pinning tests (`tests/test_voice_stt_adapter.py`, 26 tests).
  - Do NOT build a voice UI, streaming UX, or wire a production backend.
  - Do NOT mix in panel/assistant refactors or broad voice lane logic.
- **New module — `src/voxera/voice/stt_adapter.py`**:
  - `STTBackend(Protocol)`: structural interface with `backend_name` property and `transcribe(request) -> STTAdapterResult` method. Mirrors `brain/base.py::Brain` pattern — implementations do not inherit.
  - `STTAdapterResult`: frozen dataclass with `transcript`, `language`, `error`, `error_class`. Adapter-internal shape wrapped by the entry point.
  - `NullSTTBackend`: default adapter when unconfigured. `backend_name="null"`, always returns `error_class=backend_missing`. Never pretends transcription occurred.
  - `STTBackendUnsupportedError`: exception for adapters to reject unsupported input sources.
  - `transcribe_stt_request(request, adapter=None) -> STTResponse`: canonical fail-soft entry point. Never raises. Handles: no adapter (unavailable/backend_missing), unsupported source (unsupported/unsupported_source), backend exception (failed/backend_error), adapter availability error — `disabled`/`backend_missing` error_class (unavailable/passthrough), adapter runtime error — any other error_class (failed/passthrough), empty transcript (failed/empty_audio), success (succeeded with normalized transcript). Reuses `normalize_transcript_text` from `voice/input.py`.
- **Test coverage** — `tests/test_voice_stt_adapter.py` (31 tests): STTAdapterResult frozen/defaults/all-fields, NullSTTBackend truthful behavior and protocol conformance, STTBackend structural conformance (multiple stub adapters), transcribe_stt_request across all fail-soft paths (no adapter, null backend → unavailable, success with timing/normalization, unsupported with empty message fallback, exception, adapter availability-class error → unavailable, adapter runtime error → failed, custom/unknown/None error_class → failed, empty/None transcript), normalization consistency with input.py, all valid input sources.
- **Docs updated**: `docs/09_CORE_OBJECTS_AND_SCHEMA_REFERENCE.md` (adapter boundary schema), `docs/02_CONFIGURATION_AND_RUNTIME_SURFACES.md` (adapter boundary description), `docs/08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md` (test listing), `docs/CODEX_MEMORY.md`.
- **What this does NOT do**: no production STT backend, no voice UI, no streaming UX, no panel changes, no assistant refactors. The adapter boundary exists and is tested — real backends are a subsequent PR.
- **Invariants preserved**: existing voice foundation, protocol, and status tests unchanged; no new runtime side effects; no network calls; no UI changes; adapter boundary is truthful (NullSTTBackend never overclaims); fail-soft entry point never raises.
- **Known gaps (intentionally deferred)**:
  - `transcribe_stt_request` is synchronous. Real STT backends will be async. An async counterpart (`transcribe_stt_request_async` or making the protocol async) should arrive with the first real backend — adding it now would be speculative.
  - No `STTBackend.supports_source()` method. Source support is discovered via `STTBackendUnsupportedError`. An upfront check method is warranted when a UI needs to show source availability before the user starts speaking.
  - `STTAdapterResult` has no adapter-reported timing fields. Wall-clock timing from the entry point is sufficient for now. If a backend reports server-side timing, the result shape can gain optional timing fields then.
- **Next safe step**: wire a real STT backend adapter (e.g. Whisper, Google STT) behind the `STTBackend` protocol, or integrate `transcribe_stt_request` into the voice input pipeline.

## 2026-04-12 — feat(voice): add STT request/response protocol and TTS status surfaces

- **Motivation**: protocol-first start of the voice capability track. Defines the minimum trustworthy contract surfaces for speech-to-text and text-to-speech without overreaching into a full voice UI. Follows the same VoxeraOS philosophy: explicit contracts, durable truth surfaces, fail-soft when unavailable, no overclaiming.
- **Scope (deliberately bounded)**:
  - Add STT request/response protocol definition (`src/voxera/voice/stt_protocol.py`).
  - Add symmetric STT and TTS status surfaces (`src/voxera/voice/stt_status.py`, `src/voxera/voice/tts_status.py`).
  - Wire both STT and TTS status into `voxera doctor --quick` checks.
  - Add focused contract-pinning tests.
  - Update voice `__init__.py` exports.
  - Do NOT build a voice UI, runtime transcription backend, or synthesis engine.
  - Do NOT mix in panel/assistant refactors.
- **New module — `src/voxera/voice/stt_protocol.py`**:
  - `STTRequest` frozen dataclass: `request_id`, `input_source` (microphone|audio_file|stream), `language`, `session_id`, `created_at_ms`, `schema_version`.
  - `STTResponse` frozen dataclass: `request_id`, `status` (succeeded|failed|unavailable|unsupported), `transcript`, `language`, `error`, `error_class`, `backend`, `started_at_ms`, `finished_at_ms`, `schema_version`.
  - Factory functions: `build_stt_request(...)`, `build_stt_response(...)`, `build_stt_unavailable_response(...)`.
  - Serialization helpers: `stt_request_as_dict(...)`, `stt_response_as_dict(...)`.
  - Unknown `input_source` rejected fail-closed (`ValueError`). Unknown `status` normalized fail-closed to `"unavailable"`. `error_class` intentionally not validated — backends may define their own error classes (matches `CanonicalSkillResult` passthrough policy).
  - `build_stt_unavailable_response` requires explicit `error_class` — no misleading default.
  - Schema version: `STT_PROTOCOL_SCHEMA_VERSION = 1`.
- **New module — `src/voxera/voice/stt_status.py`**:
  - `STTStatus` frozen dataclass: `configured`, `available`, `enabled`, `backend`, `status`, `reason`, `schema_version`.
  - `build_stt_status(flags)` — truthful status from `VoiceFoundationFlags`. `available=True` means configured + enabled, NOT proven transcription.
  - `stt_status_as_dict(status)` — plain dict serialization for health/JSON payloads.
  - Status labels: `available`, `unconfigured`, `disabled`.
- **New module — `src/voxera/voice/tts_status.py`**:
  - `TTSStatus` frozen dataclass: `configured`, `available`, `enabled`, `backend`, `status`, `reason`, `last_error`, `schema_version`.
  - `build_tts_status(flags, *, last_error=None)` — truthful status from `VoiceFoundationFlags`. `available=True` means configured + enabled, NOT proven synthesis.
  - `tts_status_as_dict(status)` — plain dict serialization for health/JSON payloads.
  - Status labels: `available`, `unconfigured`, `disabled`, `unavailable`.
  - Schema version: `TTS_STATUS_SCHEMA_VERSION = 1`.
- **Doctor integration — `src/voxera/doctor.py`**:
  - `run_quick_doctor()` now includes symmetric `voice: stt status` and `voice: tts status` checks. Voice flags loaded once, both checks derived. Disabled-by-config is `ok` (intentional state); enabled-but-unconfigured is `warn` with actionable hints. Fail-soft: if flag loading fails, both checks report a warning.
- **Test coverage**:
  - `tests/test_voice_stt_protocol.py` (30 tests): request shape, all valid sources, case normalization, unknown source rejection, auto-generated/explicit ids, explicit timestamps, frozen immutability, optional field defaults, success response shape, transcript whitespace normalization, empty transcript → None, failure response with error/error_class, unsupported response, unavailable convenience builder (required error_class, backend passthrough), fail-closed status normalization (unknown/empty/None → unavailable), all valid statuses pass through, error_class passthrough policy (arbitrary strings accepted, None passthrough), serialization helpers (request/response as_dict roundtrip, field-count guards, JSON serializability).
  - `tests/test_voice_stt_status.py` (14 tests): available when fully configured, disabled when foundation or input off, unconfigured when no backend, fully-disabled defaults, frozen immutability, truthful unavailable handling (available ≠ transcription proven), dict serialization roundtrip (field-count guard, JSON serializability), integration with flags loader (config file, empty config), doctor integration (check presence, ok-when-disabled, warn-when-enabled-but-unconfigured).
  - `tests/test_voice_tts_status.py` (19 tests): available when fully configured, disabled when foundation or output off, unconfigured when no backend, fully-disabled defaults, frozen immutability, truthful unavailable handling (available ≠ synthesis proven), last_error passthrough/stripping/None, dict serialization roundtrip (field-count guard, JSON serializability), disabled dict state, integration with flags loader (config file, empty config, env vars), doctor integration (check presence, ok-when-disabled, warn-when-enabled-but-unconfigured).
- **Docs updated**: `docs/09_CORE_OBJECTS_AND_SCHEMA_REFERENCE.md` (STT protocol + STT/TTS status schema shapes, error_class passthrough note, serialization helpers), `docs/02_CONFIGURATION_AND_RUNTIME_SURFACES.md` (voice subsystem status section, env vars), `docs/08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md` (test listings), `docs/CODEX_MEMORY.md`.
- **Files touched**: `src/voxera/voice/stt_protocol.py` (new), `src/voxera/voice/stt_status.py` (new), `src/voxera/voice/tts_status.py` (new), `src/voxera/voice/__init__.py` (updated exports), `src/voxera/doctor.py` (STT + TTS status checks), `tests/test_voice_stt_protocol.py` (new, 30 tests), `tests/test_voice_stt_status.py` (new, 14 tests), `tests/test_voice_tts_status.py` (new, 19 tests), `docs/09_CORE_OBJECTS_AND_SCHEMA_REFERENCE.md`, `docs/02_CONFIGURATION_AND_RUNTIME_SURFACES.md`, `docs/08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md`, `docs/CODEX_MEMORY.md`.
- **Invariants preserved**: existing voice foundation behavior unchanged; no new runtime side effects; no network calls; no UI changes; protocol is definition-only (no transcription/synthesis execution); status surfaces are truthful (available ≠ feature works); doctor checks are fail-soft; disabled-by-config is ok not warn.

## 2026-04-12 — refactor(panel): extract degraded assistant bridge and messaging helpers

- **Motivation**: continuing the bounded panel decomposition. The degraded-assistant bridge / messaging cluster (stall detection, provider-tier traversal, degraded-mode disclosure, async/sync bridge, result persistence) lived inline in `routes_assistant.py` with thin wrappers in `app.py`. This PR extracts that cluster into a dedicated `degraded_assistant_bridge.py` module, keeping `app.py` as the composition root and preserving behavior exactly.
- **Scope (deliberately bounded)**:
  - Move the degraded-assistant bridge / messaging helpers from `routes_assistant.py` to `src/voxera/panel/degraded_assistant_bridge.py`.
  - Update `routes_assistant.py` to import entry points from the new module.
  - Update `app.py` aliases and wrappers to reference the new module.
  - Preserve the bridge-patching pattern: `app.py` pushes monkeypatched `load_app_config` and `create_panel_assistant_brain` into the bridge module globals before calling the async entry point.
  - Add narrow extraction-contract tests in `tests/test_panel_degraded_assistant_bridge_extraction.py`.
  - Do NOT change route contracts, degraded-answer semantics, or assistant behavior.
  - Do NOT expand into voice/STT/TTS or unrelated panel routes.
- **New module — `src/voxera/panel/degraded_assistant_bridge.py`**:
  - Public entry points: `assistant_stalled_degraded_reason(context, request_result, *, now_ms)`, `create_panel_assistant_brain(provider)`, `generate_degraded_assistant_answer_async(question, context, *, thread_turns, degraded_reason)`, `generate_degraded_assistant_answer(...)` (sync bridge via `asyncio.run`), `persist_degraded_assistant_result(queue_root, *, request_id, thread_id, question, degraded_answer, degraded_reason, context, ts_ms)`.
  - Private helpers: `_assistant_request_ts_ms`, `_degraded_mode_disclosure`, `_coerce_int`, plus the three module-level constants `_ASSISTANT_STALL_TIMEOUT_MS`, `_ASSISTANT_FALLBACK_REASONS`, `_ASSISTANT_UNAVAILABLE_STATES`.
  - Architecture invariant: explicit-args, does NOT reach back into `panel.app` via any import.
- **`routes_assistant.py` changes**: removed inline implementations of the 8 extracted functions/constants; imports the bridge entry points as `_assistant_stalled_degraded_reason`, `_generate_degraded_assistant_answer`, `_generate_degraded_assistant_answer_async`, `_persist_degraded_assistant_result` from `.degraded_assistant_bridge`; removed unused imports (`asyncio`, `json`, `re`, `brain.*`, `config.load_app_config`, `operator_assistant.build_assistant_messages`, `operator_assistant.fallback_operator_answer`).
- **`app.py` changes**: new import `from . import degraded_assistant_bridge as _degraded_assistant_bridge`; module-level aliases now source from the bridge module (`load_app_config`, `_assistant_stalled_degraded_reason`, `_create_panel_assistant_brain`, `_persist_degraded_assistant_result`); async wrapper patches `_degraded_assistant_bridge.load_app_config` and `_degraded_assistant_bridge.create_panel_assistant_brain` instead of `_routes_assistant.*`.
- **Test coverage — `tests/test_panel_degraded_assistant_bridge_extraction.py` (18 tests)**: bridge module exposes documented entry points with expected signatures; `panel.app` aliases are identity-equal to bridge functions; async/sync wrappers delegate to bridge; `routes_assistant` does not locally re-define extracted functions (AST check); `panel.app` does not own extracted private helpers; bridge module does not reach back into `panel.app` (AST check); stall-detection semantics preserved (7 cases); persistence semantics preserved (artifact on disk matches expected shape); bridge-patching pattern works (source inspection + end-to-end monkeypatch flow).
- **Validation run**: `tests/test_panel.py` (92), `tests/test_panel_degraded_assistant_bridge_extraction.py` (18), `ruff format --check .`, `ruff check .`, `mypy src/voxera`, `pytest -q` — all green.
- **Files touched**: `src/voxera/panel/degraded_assistant_bridge.py` (new), `src/voxera/panel/routes_assistant.py` (removed inline implementations, imports from bridge), `src/voxera/panel/app.py` (aliases/wrappers now reference bridge module), `tests/test_panel_degraded_assistant_bridge_extraction.py` (new, 18 tests), `docs/01_REPOSITORY_STRUCTURE_MAP.md`, `docs/08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md`, `docs/CODEX_MEMORY.md`.
- **Invariants preserved**: route contracts unchanged; async/sync bridge behavior unchanged; degraded-answer generation semantics unchanged; enqueue behavior unchanged; failure/fallback behavior unchanged; `app.py` remains the composition root; no new globals; no hidden state drift; no new network/runtime side-effect paths; monkeypatch bridge-patching pattern preserved for test compatibility.

## 2026-04-12 — feat(panel): show shared Vera activity on home page with precedence guards

- **Motivation**: PR #319 landed a read-only shared Vera session context block on the panel *job-detail* surface. This PR is the next bounded, read-only step: surface a small "Vera Activity" strip on the panel *home* page so operators see the most recent Vera session's active topic / draft / last-saved-file / last-submitted-job / last-completed-job references alongside the canonical queue / health / approvals sections. Shared Vera context remains a continuity aid only — it must never override or obscure canonical queue / daemon-health / approvals / artifact truth. This PR pins that precedence rule with focused tests.
- **Scope (deliberately bounded)**:
  - Add a small read-only Vera activity strip to `src/voxera/panel/templates/home.html`.
  - Add a new bounded helper `src/voxera/panel/home_vera_activity.py::build_home_vera_activity(queue_root, *, now_ms=None)` that scans `queue_root/artifacts/vera_sessions/*.json` fail-soft and returns the most-recently-updated signal-bearing session, or `None`.
  - Wire the helper into `routes_home.py`'s `home` route so the strip renders only when useful context is present.
  - Add precedence-focused tests to `tests/test_panel_session_context.py` asserting that canonical queue/health truth is never obscured or overridden by shared context.
  - Do NOT add any editing / mutation of shared context. Panel stays strictly read-only w.r.t. shared session context.
  - Do NOT expand into other panel pages (job browser, hygiene, recovery, automations, vera chat) or into the `/jobs/{id}/progress` payload.
  - Do NOT refactor unrelated panel modules or broaden dashboard layout.
  - Do NOT change Vera service behavior.
- **New helper — `src/voxera/panel/home_vera_activity.py`**:
  - Single public entry point `build_home_vera_activity(queue_root, *, now_ms=None) -> dict | None`.
  - Scans `queue_root/artifacts/vera_sessions/*.json` (fail-soft: missing dir / OSError / malformed files → `None` or silently skipped).
  - Uses the existing read-only `voxera.vera.session_store.read_session_context` API. Imports NO write/update/clear helper — belt-and-suspenders pinned by an AST test.
  - "Usable" gate mirrors the job-detail helper: any of `active_topic` / `active_draft_ref` / `last_saved_file_ref` / `last_submitted_job_ref` / `last_completed_job_ref` must be a non-empty string. A context with only `updated_at_ms > 0` is treated as absent so the strip does not render as an empty shell.
  - When multiple sessions exist, picks the freshest signal-bearing session by `updated_at_ms`. Tie-breaker is sorted filename order.
  - Freshness label is bucketed against wall-clock time (injectable `now_ms` callable for deterministic tests): `fresh` (≤1h), `aging` (≤24h), `stale` (>24h), or `unknown` (no / zero / bool `updated_at_ms`). Future-stamped contexts (clock skew) collapse to `fresh` rather than guessing.
  - Freshness is an operator-visible continuity hint ONLY and is NEVER read as authority over canonical truth.
  - Returned dict shape: `{session_id, active_topic, active_draft_ref, last_saved_file_ref, last_submitted_job_ref, last_completed_job_ref, updated_at_ms, freshness}`.
  - Defensive bool-is-int: `_coerce_positive_int(True)` returns 0, matching the job-detail helper's guard.
- **Route wiring — `src/voxera/panel/routes_home.py`**:
  - New import: `from .home_vera_activity import build_home_vera_activity`.
  - Inside the `home` handler, after `performance_stats_view` resolves, calls `vera_activity = build_home_vera_activity(root)` (wall-clock `now_ms`).
  - `vera_activity` is passed into the template context alongside the canonical payload keys. No other route behavior changes.
- **Template — `src/voxera/panel/templates/home.html`**:
  - New "Vera Activity" section placed under the History (Completed Jobs) card and above the Mission Library. Placement is deliberately modest: well below the Queue Summary KPI row, the Approval Command Center, Active Work, Failed Jobs, Queue Status, Daemon Lock History, and Panel Security Counters — all canonical truth sections render first.
  - Renders only when `vera_activity` is truthy (`{% if vera_activity %}` gate). Hidden when absent; no empty shell.
  - Explicit note inside the card: *"Read-only shared Vera session context. Supplemental only — canonical queue, daemon health, and approvals above remain primary."*
  - Each ref field renders as a conditional `<dt>`/`<dd>` row so absent fields simply do not render (no em-dash placeholder junk).
  - Freshness label renders as a color-coded `<span class="badge ...">` — `badge-done` for `fresh`, `badge-pending` for `aging` / `stale`, muted plain-text for `unknown`. Same visual language the job-detail strip uses.
  - `data-testid="home-vera-activity-strip"` on the section for future selector stability.
- **Test coverage expansion — `tests/test_panel_session_context.py` (24 → 49 tests)**:
  - New section 7: helper-level unit tests for `build_home_vera_activity`. Cover missing sessions dir, empty sessions dir, session-without-context, `updated_at_ms`-only gate, single-session topic+draft, single-session fallback shape (only `last_submitted_job_ref` / `last_saved_file_ref`), multi-session freshness pick, malformed-file fail-soft, fresh / aging / stale freshness buckets against injected `now_ms`, `freshness=unknown` when `updated_at_ms=0`, bool-is-int defense, **return-shape lock** (pins the exact 8-key set: `session_id` / `active_topic` / `active_draft_ref` / `last_saved_file_ref` / `last_submitted_job_ref` / `last_completed_job_ref` / `updated_at_ms` / `freshness`), read-only invariant (repeated calls leave stored context byte-for-byte unchanged), and AST check that `home_vera_activity` imports `read_session_context` and NOT any mutation helper.
  - New section 8: end-to-end home-page render tests via `TestClient(panel_module.app)` with `monkeypatch.setattr(panel_module.Path, "home", ...)`. Cover strip-present, strip-absent (no context), strip-absent (only `updated_at_ms`), strip renders only populated fields (real-world post-submit shape with no em-dash rows), **precedence against queue truth** (empty queue + ghost `last_submitted_job_ref` → canonical KPI cards still show empty and "No pending queue approvals" / "No active jobs currently" still render authoritatively; strip is clearly labeled `Supplemental only`), **precedence against daemon health** (loud Vera context with every ref field populated does not obscure `Daemon Health` / `Queue Summary` / `Approval Command Center`; substring-index ordering check pins visual primacy), fail-soft when `vera_sessions` directory missing, fail-soft when session file is corrupt JSON, stale contexts surface with a `stale` badge rather than being hidden, repeated home renders leave shared context byte-for-byte unchanged, and AST check that `routes_home` imports `build_home_vera_activity` and no mutation helpers.
- **Precedence rule pinned by the new tests**:
  - Canonical home-page truth sections — queue counts / daemon health / approvals / active jobs / failed jobs / queue details / runtime status — remain primary and render above the Vera activity strip.
  - Shared Vera activity is clearly labeled as supplemental-only continuity context. It never drives queue counts, never inflates the KPI cards, never replaces the Daemon Health widget, and never hides canonical empty-state messages.
  - Stale or unknown-freshness contexts are labeled conservatively (`stale` / `unknown`) rather than silently dropped, so operators see the signal but understand its weight.
  - If the context suggests recent activity but the queue is empty, the canonical queue-empty render wins.
- **Validation run**: focused tests `tests/test_panel_session_context.py` (49), `tests/test_panel.py` (92), `tests/test_panel_job_detail_shaping_extraction.py` (14), `tests/test_shared_session_context.py`, `tests/test_shared_session_context_integration.py` — all green. Full ladder (`ruff format --check .`, `ruff check .`, `mypy src/voxera`, `pytest -q`, `make golden-check`, `make security-check`, `make validation-check`, `make merge-readiness-check`) all green.
- **Files touched**: `src/voxera/panel/home_vera_activity.py` (new), `src/voxera/panel/routes_home.py` (new helper import + one-line call + one template-context key), `src/voxera/panel/templates/home.html` (new conditional "Vera Activity" card placed below History / above Mission Library), `tests/test_panel_session_context.py` (+24 tests across sections 7–8; existing job-detail tests unchanged), `docs/08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md` (home coverage added to `test_panel_session_context.py` entry), `docs/CODEX_MEMORY.md`.
- **Invariants preserved**: panel reads shared context only — no panel code under `src/voxera/panel/` imports `write_session_context` / `update_session_context` / `clear_session_context`; canonical queue / daemon-health / approvals / artifact truth remains primary; missing / malformed / empty context is non-fatal (fail-soft → strip is hidden); stale context is surfaced but clearly labeled; no changes to Vera service behavior, no changes to other panel pages, no changes to the `/jobs/{id}/progress` payload, no changes to queue-truth precedence, no new top-level job-detail payload keys.
- **Follow-up**: none required. The helper is intentionally minimal — it does not try to correlate a surfaced session with a specific queue job, does not surface multiple sessions, and does not attempt any staleness comparison against a specific job's terminal timestamp (that's what the job-detail helper is for). Any future expansion to additional panel pages or multi-session surfacing is a separate product decision and a separate PR.

## 2026-04-11 — review(panel): broaden vera_context visibility gate to surface post-submit continuity signals

- **Motivation**: live testing against a real Vera-submitted job proved the session/job lookup path is correct (the session file exists, the job is correctly linked in `linked_queue_jobs.tracked`, and `_find_vera_session_id_for_job` returns the right session), but the Vera Activity strip was still hidden because the initial visibility gate required `active_topic` or `active_draft_ref` to be a non-empty string. Real Vera sessions after submit commonly have both of those cleared — the draft has been handed off to the queue — and only `last_submitted_job_ref` / `last_saved_file_ref` remain as operator-visible continuity signals. The strip must appear in that state; that is the whole point of the continuity aid.
- **Scope (deliberately bounded)**:
  - Broaden the "usable" gate in `_build_vera_context` to accept any of `active_topic`, `active_draft_ref`, `last_saved_file_ref`, `last_submitted_job_ref`, or `last_completed_job_ref` as a non-empty string.
  - Expand the returned `vera_context` payload to carry `last_saved_file_ref` / `last_submitted_job_ref` / `last_completed_job_ref` alongside the existing fields.
  - Teach the template to render those fields as conditional rows — no em-dash placeholder rows for absent fields, strip stays modest.
  - Extend `tests/test_panel_session_context.py` to cover the real-world post-submit shapes, tighten wrong-session isolation across the new fields, and add a template render test for the fallback case.
  - Do NOT change the read-only invariant, the wrong-session isolation rule, the conservative staleness rule, the 33-key top-level detail payload shape lock, queue-truth precedence, or any panel route.
  - Do NOT widen `_find_vera_session_id_for_job` or add any mutation helper.
- **Root cause**: the original gate was `if active_topic is None and active_draft_ref is None: return None`. This correctly avoided rendering an empty strip for a context carrying only `updated_at_ms`, but it also hid the strip for the dominant real-world shape where the session has successfully handed off the draft and now holds only the last-submitted / last-saved refs.
- **Fix — `src/voxera/panel/job_detail_sections.py`**: the gate now checks all five documented continuity fields (`active_topic`, `active_draft_ref`, `last_saved_file_ref`, `last_submitted_job_ref`, `last_completed_job_ref`) and returns `None` only if every one of them is `None` after normalization. A local `_clean_ref(key)` helper normalizes each field to `None` or a stripped non-empty string, so whitespace-only values still fall through to absent. The module docstring now lists all five signal fields and notes explicitly that real Vera sessions after submit commonly have only the last-submitted / last-saved fields populated, so the gate must not require a "live" draft.
- **Expanded `vera_context` payload shape** (now 8 fields, all reads only):
  ```
  {
      "session_id": str,
      "active_topic": str | None,
      "active_draft_ref": str | None,
      "last_saved_file_ref": str | None,
      "last_submitted_job_ref": str | None,
      "last_completed_job_ref": str | None,
      "updated_at_ms": int,
      "is_stale": bool | None,
  }
  ```
  The top-level job-detail payload shape lock (`_EXPECTED_JOB_DETAIL_KEYS` = 33 keys) still holds — `vera_context` is the same single top-level key, only its inner shape grew.
- **Template — `src/voxera/panel/templates/job_detail.html`**: each ref field renders as a conditional `<dt>`/`<dd>` row (`{% if payload.vera_context.active_topic %}...{% endif %}`) so absent fields simply do not render. The freshness label always renders when the strip is visible. No em-dash placeholder rows leak into the rendered page. The "Read-only shared Vera session context. Supplemental only — canonical queue/artifact truth remains primary." note is unchanged.
- **Test coverage expansion — `tests/test_panel_session_context.py` (19 → 24 tests)**:
  - `test_vera_context_surfaces_when_only_last_saved_file_ref_is_set` — session with only `last_saved_file_ref` populated → strip appears.
  - `test_vera_context_surfaces_when_only_last_submitted_job_ref_is_set` — session with only `last_submitted_job_ref` populated → strip appears. Mirrors the exact live-testing shape.
  - `test_vera_context_surfaces_when_only_last_completed_job_ref_is_set` — session with only `last_completed_job_ref` populated → strip appears.
  - `test_vera_context_surfaces_real_world_post_submit_shape` — end-to-end with the exact shared_context excerpt observed in live testing (`active_topic=None`, `active_draft_ref=None`, `last_submitted_job_ref="1775943976577-19a07abc"`, `last_saved_file_ref="~/VoxeraOS/notes/audit-note.txt"`, `updated_at_ms=1775943976580`) asserts the strip surfaces and correctly computes fresh vs. stale against the terminal timestamp.
  - `test_job_detail_template_renders_fallback_fields_without_topic_or_draft` — end-to-end `TestClient` render: a post-submit session renders the strip with "Last submitted job" / "Last saved file" rows and does NOT render "Active topic" / "Active draft" rows.
  - `test_vera_context_does_not_leak_from_unrelated_session` — hardened: the unrelated session now populates every ref field (`active_topic`, `active_draft_ref`, `last_saved_file_ref`, `last_submitted_job_ref`, `last_completed_job_ref`) with distinctive "B-*" strings, and the test asserts that none of them appear in the owning session's surfaced context. Wrong-session isolation continues to hold across the broadened gate.
  - `test_vera_context_present_surfaces_active_topic_and_draft` and `test_vera_context_present_partial_topic_only` — updated to assert that the new ref fields are `None` when unpopulated (no placeholder junk).
  - `test_vera_context_absent_when_only_updated_at_ms_is_set` — still holds: a context with no ref signals still returns `None` regardless of `updated_at_ms`. The tightening the review commit added is preserved.
- **Validation run**: focused tests `tests/test_panel_session_context.py` (24), `tests/test_panel_job_detail_shaping_extraction.py` (14), `tests/test_panel.py` (full panel suite), `tests/test_shared_session_context.py`, `tests/test_shared_session_context_integration.py` — all green. Full ladder (`ruff format --check .`, `ruff check .`, `mypy src/voxera`, `pytest -q`, `make golden-check`, `make security-check`, `make validation-check`, `make merge-readiness-check`) all green.
- **Files touched**: `src/voxera/panel/job_detail_sections.py` (broadened gate + expanded payload + docstring), `src/voxera/panel/templates/job_detail.html` (conditional rows for the new fields), `tests/test_panel_session_context.py` (+5 tests; 1 existing test hardened), `docs/08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md`, `docs/CODEX_MEMORY.md`.
- **Invariants preserved**: panel reads shared context only; panel never mutates shared context; `vera_context` is supplemental, not execution truth; missing context is non-fatal; wrong-session context does not leak (now verified across all five ref fields); staleness labeling stays conservative (strict `<` against state-sidecar `completed_at_ms`, `None` when undecidable).

## 2026-04-11 — feat(panel): show shared Vera session context on job detail

- **Motivation**: now that PR D extracted the job-detail payload builder into `src/voxera/panel/job_detail_sections.py`, the panel has a clean surface to attach a small, read-only shared Vera session context block to the job-detail page. This is a tight, product-facing enhancement that helps operators see which active topic / active draft Vera is tracking when they land on a job. The panel reads shared context only and never mutates it — canonical queue / artifact truth remains primary, and `vera_context` is supplemental continuity only.
- **Scope (deliberately bounded)**:
  - Add a read-only session-context lookup to `build_job_detail_payload(queue_root, job_id)` that attaches an optional `vera_context` dict to the job-detail payload.
  - Render a small "Vera activity" strip in `src/voxera/panel/templates/job_detail.html` showing `active_topic`, `active_draft_ref`, and a staleness label.
  - Add focused tests covering context present / absent / stale / wrong-session isolation / panel read-only discipline / template render.
  - Do NOT add editing or mutation of shared context from panel code.
  - Do NOT add broader session-context features.
  - Do NOT touch Vera behavior.
  - Do NOT change queue / job semantics.
  - Do NOT broaden into other panel pages.
- **Payload change — `src/voxera/panel/job_detail_sections.py`**:
  - New private helper `_find_vera_session_id_for_job(queue_root, job_name)` — fail-soft, read-only scan of `queue_root/artifacts/vera_sessions/*.json` for a session whose `linked_queue_jobs.tracked[].job_ref` matches the job filename. Returns `None` when no session tracks the job, when the sessions directory is missing, or when any session file is unreadable / malformed. Never raises, never writes. The scan strictly enforces wrong-session isolation: only a session that has explicitly registered this job via `register_session_linked_job` is a valid match.
  - New private helper `_coerce_positive_int(value)` — defensive coercion that treats `bool` values (which are a subclass of `int` in Python) as 0 so a stray boolean in a session file or state sidecar never masquerades as a millisecond timestamp. Returns the value unchanged only when it is a strict positive `int`.
  - New private helper `_build_vera_context(queue_root, job_name, *, state_sidecar)` — shapes the optional read-only `vera_context` block from the owning session's shared context (via the existing `voxera.vera.session_store.read_session_context` API). Returns `None` when there is no owning Vera session, when the session has no shared context yet, or when the stored context has no visible operator-facing signal (the gate requires at least one of `active_topic` / `active_draft_ref` to be a non-empty string — a context carrying only `updated_at_ms > 0` with no topic/draft is treated as absent so the strip does not render as empty noise). Computes staleness conservatively against the state-sidecar `completed_at_ms`:
    - `is_stale=True` when context `updated_at_ms` is strictly before the job's terminal completion time;
    - `is_stale=False` when at or after the terminal time (same-millisecond counts as fresh — not strictly before);
    - `is_stale=None` when the job has not reached a terminal state yet, or either timestamp is missing / non-positive / a bool — we deliberately do not invent a timestamp or guess.
  - `build_job_detail_payload` now attaches `"vera_context": vera_context` as a top-level payload key (value is `None` when there is no usable context). The lookup is wired in right after lineage resolution so the state sidecar's `completed_at_ms` is already loaded. The module's docstring documents the new block and pins the panel-read-only invariant.
  - New import: `from ..vera.session_store import read_session_context`. No write/update/clear helper is imported — the belt-and-suspenders test `test_job_detail_sections_module_does_not_import_mutation_helpers` pins this via AST. A broader grep confirms that **no** file under `src/voxera/panel/` imports `write_session_context`, `update_session_context`, or `clear_session_context` — the panel package is entirely read-only w.r.t. shared session context.
- **Template change — `src/voxera/panel/templates/job_detail.html`**: a new, visually modest "Vera Activity" strip rendered only when `payload.vera_context` is truthy. Shows:
  - `active_topic` (or em-dash);
  - `active_draft_ref` (mono-text, or em-dash);
  - freshness label — `stale` / `fresh` / `unknown` badge driven by `payload.vera_context.is_stale`.
  - The strip carries an explicit note: *"Read-only shared Vera session context. Supplemental only — canonical queue/artifact truth remains primary."* The section is hidden entirely when `vera_context` is `None` — no placeholder junk leaks.
- **Shape-lock test update — `tests/test_panel_job_detail_shaping_extraction.py`**: adds `vera_context` to the frozen `_EXPECTED_JOB_DETAIL_KEYS` set (now 33 keys). The corresponding `test_job_detail_payload_key_set_shape_lock` test continues to enforce that any further top-level key change must update the pin in the same commit.
- **New test file — `tests/test_panel_session_context.py` (19 tests)**:
  - 1. **context present** — seeded shared context with `active_topic` / `active_draft_ref` (plus a partial-topic-only variant) surfaces in the payload with `session_id` / `updated_at_ms` and the correct `is_stale` value.
  - 2. **context absent** — no session tracks the job → `vera_context` key present as `None`; owning session exists but context is the canonical empty shape → `None`; no `vera_sessions` directory → `None`; malformed session file is ignored fail-soft; context whose only non-empty field is `updated_at_ms` (no topic, no draft) → `None` (gate tightening: no empty-strip noise); context whose topic / draft are whitespace-only → `None` (normalizer + gate).
  - 3. **context stale** — context `updated_at_ms` forced strictly before the job's `completed_at_ms` → `is_stale=True`. Pending (non-terminal) job → `is_stale=None`. Context bumped past terminal → `is_stale=False`. **Boundary**: context `updated_at_ms == terminal_at_ms` → `is_stale=False` (same-millisecond is fresh, not strictly before).
  - 4. **wrong-session isolation** — multiple sessions seeded; only the owning session's context surfaces, unrelated session contexts are strictly excluded (belt-and-suspenders `"B-topic"` / `"draft://B.md"` JSON substring check). Orphaned job (no tracking session) → `vera_context` is `None`.
  - 5. **panel read-only** — repeated `build_job_detail_payload` / `_build_vera_context` calls leave the stored context unchanged byte-for-byte. AST check asserts `job_detail_sections` imports `read_session_context` only — never `write_session_context` / `update_session_context` / `clear_session_context`.
  - 6. **template render** — end-to-end `TestClient` tests: one renders a job with seeded context and asserts the "Vera Activity" strip appears with `render-topic` / `draft://render.md` and the read-only note; one renders a job with no owning session and asserts the strip is hidden.
  - 7. **bool-is-int defense** — a `True` value masquerading as an `updated_at_ms` int collapses to 0 via `_coerce_positive_int`, and the resulting `is_stale` is `None` (undecidable) rather than `True` / `False`. Guards against `isinstance(True, int) == True` in Python.
- **Does NOT**: add any write / update / clear path for shared session context from panel code, change any route's public API, alter queue-truth precedence / 404 semantics / lineage resolution / terminal-outcome timeline filtering, introduce new top-level payload keys beyond `vera_context`, alter the sidebar layout, modify Vera service behavior, or broaden shared-context support into other panel pages or progress payload.
- **Validation run**: focused tests `tests/test_panel_session_context.py` (15), `tests/test_panel_job_detail_shaping_extraction.py` (14), `tests/test_panel.py` (full panel suite), `tests/test_shared_session_context.py`, `tests/test_shared_session_context_integration.py` — all green. Full ladder (`ruff format --check .`, `ruff check .`, `mypy src/voxera`, `pytest -q`, `make golden-check`, `make security-check`, `make validation-check`, `make merge-readiness-check`) all green.
- **Files touched**: `src/voxera/panel/job_detail_sections.py` (new `_find_vera_session_id_for_job` + `_build_vera_context` helpers, wired into `build_job_detail_payload`, new `read_session_context` import, docstring addendum), `src/voxera/panel/templates/job_detail.html` (new "Vera Activity" strip, rendered only when `payload.vera_context` is truthy), `tests/test_panel_job_detail_shaping_extraction.py` (adds `vera_context` to the frozen `_EXPECTED_JOB_DETAIL_KEYS` shape lock), `tests/test_panel_session_context.py` (new, 15 tests), `docs/08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md`, `docs/CODEX_MEMORY.md`.
- **Follow-up**: none required. Intentionally leaves the progress payload (`build_job_progress_payload`) untouched — `vera_context` is a detail-page continuity aid only and has no place in the polling progress endpoint. The panel remains strictly read-only w.r.t. shared context; any future progress-page or listing-page surfacing is a separate product decision and a separate PR.

## 2026-04-11 — refactor(panel): extract health view and formatting helpers from panel/app.py (PR E)

- **Motivation**: continuing the gentle, bounded decomposition of `src/voxera/panel/app.py` started by PR A (auth enforcement), PR B (hygiene / queue mutation bridge), PR C (security / health snapshot helpers), and PR D (job-detail shaping). PR E moves the next cohesive cluster — the health-view / formatting helper cluster that previously lived around lines 199–417 of `panel/app.py` (`_format_ts`, `_format_ts_seconds`, `_format_age`, `_history_value`, `_history_pair`, `_daemon_health_view`, `_performance_stats_view`) — into a new sibling module. This PR is **PR E of a multi-PR decomposition plan** and remains just as bounded and behavior-preserving as PR A, PR B, PR C, and PR D.
- **Scope (deliberately bounded)**:
  - Extract ONLY the health-view and formatting helper cluster from `panel/app.py`.
  - Move it to a new sibling module `src/voxera/panel/health_view_helpers.py`.
  - Do not refactor auth enforcement (PR A), queue mutation bridge (PR B), security / health snapshot helpers (PR C), or job-detail shaping (PR D) again.
  - Do not refactor degraded-assistant wiring or the activity/job-listing helpers.
  - Do not change the FastAPI app object structure, route registration order, or any route contract.
  - Do not add features.
- **New module — `src/voxera/panel/health_view_helpers.py`**: owns the narrow cluster that shapes the two read-only views the operator home page renders — the Daemon Health widget payload and the Performance Stats tab payload — plus the five tiny formatting / history-line helpers those views depend on. Exposes two documented view-builder entry points plus the five small formatters:
  - `daemon_health_view(health) -> dict` — shapes the Daemon Health widget payload (`lock_status` / `lock_pid` / `lock_stale_age_s` / `lock_stale_age_label` / `last_brain_fallback` / `last_startup_recovery` / `last_shutdown` / `daemon_state`) from a raw health snapshot.
  - `performance_stats_view(queue, health) -> dict` — shapes the Performance Stats tab payload (`queue_counts` / `current_state` / `recent_history` / `historical_counters`) from the queue snapshot plus a raw health snapshot. Composes via `voxera.health_semantics.build_health_semantic_sections`.
  - `format_ts(ts_ms)` / `format_ts_seconds(ts_s)` / `format_age(age_s)` — formatters that return the em-dash `"—"` fallback for `None` / non-positive / negative inputs and format the UTC timestamp as `"%Y-%m-%d %H:%M:%S UTC"`; `format_age` emits `"{n}s"` under a minute, `"{m}m {s}s"` when seconds remain, `"{m}m"` on a clean boundary.
  - `history_value(value)` / `history_pair(value, ts_label)` — history-line renderers used by `performance_stats_view` to build the `last_fallback_line` / `last_error_line` / `last_shutdown_line` strings with the `"-"` fallback guard when both sides are empty.
- **Explicit-args design (matches PR B / PR C / PR D, not PR A)**: every function in `health_view_helpers.py` takes its inputs as explicit positional arguments (`health`, `queue`, `ts_ms`, `value`, `ts_label`). There is no hidden module-level state, no import of `panel.app` from the helper module's side, and the module is easy to unit-test in isolation. The thin wrappers in `panel/app.py` (`_daemon_health_view`, `_performance_stats_view`, `_format_ts`) close over the extracted entry points so the route-registration callback signatures stay identical to their pre-extraction shapes (`daemon_health_view=_daemon_health_view`, `performance_stats_view=_performance_stats_view`, `format_ts_ms=_format_ts`).
- **`panel/app.py` after extraction**: still visibly the composition root. It still defines the `FastAPI(title="Voxera Panel")` app, mounts `/static`, constructs the Jinja environment, owns the shared `_settings` / `_now_ms` / `_queue_root` / `_health_queue_root` / `_panel_security_counter_incr` / `_panel_security_snapshot` / `_auth_setup_banner` wrappers, and registers every route family in the same order as before. The three health-view helpers are now thin wrappers that forward to the extracted builders: `_daemon_health_view` → `daemon_health_view`, `_performance_stats_view` → `performance_stats_view`, `_format_ts` → `format_ts`. The now-unused private helpers `_format_ts_seconds`, `_format_age`, `_history_value`, `_history_pair` are fully removed from `panel.app` since they only supported the extracted views. The now-unused `from datetime import datetime, timezone`, `from ..health_semantics import build_health_semantic_sections`, and `from .helpers import coerce_int as _coerce_int` imports are dropped.
- **Preserves (payload shape + formatting semantics exactly)**:
  - `daemon_health_view` preserves the lock-status precedence (`lock_status` dict sub-key > `lock_state` fallback → `active`/`locked_by_other` → `held`, `stale`/`reclaimed` → `stale`, else `clear`), the `lock_pid` fallback (`lock.pid` > `health.lock_holder_pid`), the `has_fallback` / `has_recovery` / `last_shutdown.present` booleans computed from the same source fields in the same order, the startup-recovery nested-dict vs. flat-field fallback, the `stale_age_s` label via `format_age`, and the 8-key payload shape (`lock_status`, `lock_pid`, `lock_stale_age_s`, `lock_stale_age_label`, `last_brain_fallback`, `last_startup_recovery`, `last_shutdown`, `daemon_state`).
  - `performance_stats_view` preserves the queue counts sub-dict (`inbox` / `pending` / `pending_approvals` / `done` / `failed` / `canceled`), the `build_health_semantic_sections` composition with its `queue_context` / `lock_status` / `daemon_lock_counters` inputs, the `last_fallback_line` / `last_error_line` / `last_shutdown_line` string templates (including the `"-"` empty-fallback guard), the `degraded_since_ts` / `brain_backoff_last_applied_s` / `brain_backoff_last_applied_ts` recent-history fields, and every single historical counter key: `panel_auth_invalid`, `panel_401_count`, `panel_403_count`, `panel_429_count`, `panel_csrf_missing`, `panel_csrf_invalid`, `panel_mutation_allowed`, `brain_fallback_count`, and the six `brain_fallback_reason_*` counters.
  - Formatting: `format_ts(None)` / `format_ts(0)` / `format_ts(-1)` return em-dash `"—"`; `format_ts(1700000000000) == "2023-11-14 22:13:20 UTC"`; `format_ts_seconds` mirrors the seconds variant; `format_age(None)` / `format_age(-5)` return `"—"`, `format_age(45) == "45s"`, `format_age(60) == "1m"`, `format_age(125) == "2m 5s"`, `format_age(3600) == "60m"`; `history_value(None)` / `history_value("")` / `history_value("   ")` return `"-"`, `history_value(42) == "42"`; `history_pair` returns `"-"` only when both sides are empty, otherwise `"{val} @ {ts}"`.
  - Route contracts: every `register_*_routes` call in `panel/app.py` is unchanged. Route paths, HTTP methods, response shapes, and callback injection are identical. `test_panel_contract_snapshot.py` continues to pass without modification.
- **Does NOT**: introduce new globals, change any route's public API, change daemon-health or performance-stats payload shape or truth precedence, alter formatting strings, widen or narrow the em-dash fallback, add features, refactor any other panel cluster, or touch auth / bridge / security-health / job-detail / degraded-assistant / activity-builder code.
- **Extraction-contract test added — `tests/test_panel_health_view_helpers_extraction.py` (20 tests)**: narrow, fast pins of the PR E shape so a later decomposition PR can't silently undo the extraction. Asserts (1) `health_view_helpers.py` exposes `daemon_health_view`, `performance_stats_view`, `format_ts`, `format_ts_seconds`, `format_age`, `history_value`, `history_pair` with the documented signatures; (2) `panel.app` still exposes the thin wrapper callbacks `_daemon_health_view`, `_performance_stats_view`, `_format_ts` and each wrapper's source visibly forwards to the extracted helper (`_daemon_health_view_impl(...)`, `_performance_stats_view_impl(...)`, `_format_ts_impl(...)`); (3) `panel.app` no longer defines the extracted private helper bodies `_format_ts_seconds`, `_format_age`, `_history_value`, `_history_pair` (`hasattr` check); (4) `panel.app` no longer imports `build_health_semantic_sections` or `datetime` directly — the helper module is the single panel-side caller of those primitives; (5) `panel.app._performance_stats_view` wrapper body no longer contains the inline `historical_counters` / `brain_fallback_reason_timeout` literals and `panel.app._daemon_health_view` wrapper body no longer contains the `last_brain_fallback` / `lock_stale_age_label` literals — the delegation is visible; (6) `health_view_helpers.py` does NOT reach back into `panel.app` via any import (AST-level check rules out `from . import app` / `from .app import …` / `from .routes_* import …`), pinning the explicit-args architecture invariant; (7) `format_ts` / `format_ts_seconds` / `format_age` / `history_value` / `history_pair` semantics preserved exactly across the em-dash / boundary / empty-string edge cases; (8) `daemon_health_view({})` returns the documented clear-defaults shape; (9) `daemon_health_view` populated input preserves the lock-status / fallback / recovery / shutdown semantics byte-for-byte; (10) `daemon_health_view` lock-state fallback rules map `active`/`reclaimed`/other to `held`/`stale`/`clear`; (11) `performance_stats_view({}, {})` returns the full 4-key payload with `"-"` history-line fallbacks; (12) `performance_stats_view` counts / historical counters round-trip through the view correctly; (13) populated-path f-string templates for all three recent-history lines (`last_fallback_line`, `last_error_line`, `last_shutdown_line`) are byte-frozen so a later PR cannot silently tweak the operator-visible format strings (covers the populated branch the HTTP test does not); (14) two **payload key-set shape locks** freeze the top-level key sets returned by `daemon_health_view` (8 keys) and `performance_stats_view` (4 keys, including the historical-counter 14-key sub-set) so a later PR that silently adds, renames, or removes a payload key must update the pins in the same commit; (15) the thin wrappers in `panel.app` produce byte-for-byte identical results to the extracted helpers when called with the same inputs. HTTP-level behavior is still covered by `test_panel.py::test_home_renders_daemon_health_widget_*` / `test_home_renders_performance_stats_tab` / `test_home_performance_history_missing_shows_dash`; this file pins the *shape* of the extraction so a later decomposition PR can't silently reinline the builder logic back into `panel/app.py`.
- **Validation run**: `ruff format --check .`, `ruff check .`, `mypy src/voxera`, focused panel suite (`tests/test_panel.py`, `tests/test_panel_contract_snapshot.py`, `tests/test_panel_automations.py`, `tests/test_panel_auth_enforcement_extraction.py`, `tests/test_panel_queue_mutation_bridge_extraction.py`, `tests/test_panel_security_health_helpers_extraction.py`, `tests/test_panel_job_detail_shaping_extraction.py`, `tests/test_panel_health_view_helpers_extraction.py`), `pytest -q` full suite, `make golden-check`, `make security-check`, `make validation-check`, `make merge-readiness-check` — all green. No existing test required any modification; the explicit-args design plus the preserved wrapper signatures made the extraction transparent to existing tests, and the new extraction-contract file is purely additive.
- **Files touched**: `src/voxera/panel/health_view_helpers.py` (new), `src/voxera/panel/app.py` (imports health_view_helpers, replaces the health-view cluster with three thin wrappers, removes the four private formatting helpers, drops the now-unused `datetime` / `build_health_semantic_sections` / `coerce_int as _coerce_int` imports), `tests/test_panel_health_view_helpers_extraction.py` (new, 20 tests including two payload key-set shape locks and the populated-history-line format lock), `docs/01_REPOSITORY_STRUCTURE_MAP.md`, `docs/08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md`, `docs/CODEX_MEMORY.md`.
- **Next safe PR step**: PR F should target the next cohesive panel cluster — candidates include (a) the degraded-assistant wiring (`_generate_degraded_assistant_answer`, `_generate_degraded_assistant_answer_async`, `_enqueue_assistant_question`) which already forwards to `routes_assistant.py` but still has the async-sync bridge living inline in `panel/app.py`; or (b) the activity builder (`_build_activity`) + the small job-listing helpers (`_last_activity`, `_job_ref_bucket`) which power `GET /jobs` row enrichment. Keep PRs small and preserve the "app.py is the composition root" invariant.

## 2026-04-11 — refactor(panel): extract job detail payload builders from panel/app.py (PR D)

- **Motivation**: continuing the gentle, bounded decomposition of `src/voxera/panel/app.py` started by PR A (auth enforcement), PR B (hygiene / queue mutation bridge), and PR C (security / health snapshot helpers). PR D moves the next cohesive cluster — the job-detail shaping cluster that previously lived around lines 453–840 of `panel/app.py` (`_job_detail_payload`, `_job_progress_payload`, `_job_artifact_flags`, plus the small private loaders `_artifact_text`, `_safe_json`, `_load_actions`, `_read_generated_files`, `_payload_lineage`) — into the existing presentation modules (`job_detail_sections.py` and `job_presentation.py`). This PR is **PR D of a multi-PR decomposition plan** and remains just as bounded and behavior-preserving as PR A, PR B, and PR C. It is the prerequisite for a future shared session-context / `vera_context` block in the panel job-detail surface — that future step needs the job-detail payload builder to live in a clean, dedicated builder surface rather than inline in `panel/app.py`.
- **Scope (deliberately bounded)**:
  - Extract ONLY the job-detail shaping cluster from `panel/app.py`.
  - Thread it into the existing `job_detail_sections.py` and `job_presentation.py` modules — do NOT invent a new sibling module.
  - Do not refactor auth enforcement (PR A), queue mutation bridge (PR B), or security / health snapshot helpers (PR C) again.
  - Do not refactor degraded-assistant wiring or the health-view builders (`_daemon_health_view`, `_performance_stats_view`).
  - Do not change the FastAPI app object structure, route registration order, or any route contract.
  - Do not add features.
- **Builder surface — `src/voxera/panel/job_detail_sections.py`** (expanded): now owns the big `build_job_detail_payload(queue_root, job_id)` and `build_job_progress_payload(queue_root, job_id)` builders that power `GET /jobs/{job_id}` and `GET /jobs/{job_id}/progress`, plus the pre-existing `build_job_detail_sections(...)` composition helper (unchanged). Also owns the small private loaders coupled to the builder: `_artifact_text(path, *, max_chars)`, `_safe_json(path)`, `_load_actions(path, *, limit)`, `_read_generated_files(artifacts_dir)`, `_payload_lineage(payload)`. The module imports `tail` from `..audit`, `lookup_job` / `queue_snapshot` from `..core.queue_inspect`, `resolve_structured_execution` from `..core.queue_result_consumers`, and the per-section helpers (`job_context_summary`, `operator_outcome_summary`, `policy_rationale_rows`, `evidence_summary_rows`, `why_stopped_rows`, `job_recent_timeline`, `job_artifact_inventory`) from `job_presentation`. Every entry point takes `queue_root: Path` as an explicit positional arg, matching the explicit-args architecture invariant of PR B's `queue_mutation_bridge` and PR C's `security_health_helpers`. The module never imports `panel.app`.
- **Presentation surface — `src/voxera/panel/job_presentation.py`** (expanded): now owns the tiny `job_artifact_flags(queue_root, job_id) -> dict[str, bool]` helper that powers the per-row artifact chips on `GET /jobs`. It's a pure filesystem check over the four canonical artifact filenames (`plan.json`, `actions.jsonl`, `stdout.txt`, `stderr.txt`) under `queue_root/artifacts/{stem}`. The rest of `job_presentation.py` (per-section shaping helpers like `job_context_summary`, `operator_outcome_summary`, etc.) is unchanged.
- **`panel/app.py` after extraction**: still visibly the composition root. It still defines the `FastAPI(title="Voxera Panel")` app, mounts `/static`, constructs the Jinja environment, owns the shared `_settings` / `_now_ms` / `_queue_root` / `_health_queue_root` / `_panel_security_counter_incr` / `_panel_security_snapshot` / `_auth_setup_banner` wrappers, and registers every route family in the same order as before. The three job-detail helpers are now thin wrappers that forward to the extracted builders: `_job_detail_payload` → `build_job_detail_payload`, `_job_progress_payload` → `build_job_progress_payload`, `_job_artifact_flags` → `job_artifact_flags`. Each wrapper preserves the exact `(queue_root: Path, job_id: str) -> dict` route-callback signature that `register_job_routes(job_detail_payload=..., job_progress_payload=..., job_artifact_flags=...)` already expects, so the `register_job_routes` call is unchanged. The now-unused `import json`, `from ..audit import tail`, `from ..core.queue_inspect import lookup_job, queue_snapshot`, `from ..core.queue_result_consumers import resolve_structured_execution`, and `from .job_presentation import job_artifact_inventory as _job_artifact_inventory` / `from .job_detail_sections import build_job_detail_sections as _build_job_detail_sections` imports are dropped. `from ..audit import log, tail` narrows to `from ..audit import log` since `log` is still needed by the hygiene route wiring (`audit_log=lambda event: log(event)`). The existing `from .job_presentation import operator_outcome_summary as _operator_outcome_summary` re-export stays (still used by `tests/test_panel.py::test_operator_outcome_summary_semantics_precedence_characterization`).
- **Preserves (queue-truth precedence + payload shape exactly)**:
  - Queue-truth precedence in `build_job_progress_payload` is identical byte-for-byte: `lifecycle_state` prefers `execution.lifecycle_state` → `state_sidecar.lifecycle_state` → `bucket` → `"unknown"`; `terminal_outcome` prefers `execution.terminal_outcome` → `state_sidecar.terminal_outcome` → `""`; `current_step_index` / `total_steps` / `last_attempted_step` / `last_completed_step` all prefer `execution.*` → `state_payload.*` → `0`; `approval_status` prefers `execution.approval_status` → `job_context.approval_status` → `"pending"` when approval exists else `"none"`.
  - Lineage precedence in `build_job_detail_payload`: `structured_execution.lineage` (when dict) > `_payload_lineage(primary)` > `_payload_lineage(state_sidecar.payload)` > `None`.
  - Terminal-outcome filtering of `recent_timeline` in progress payload: success-terminal jobs drop `queue_job_failed` / `assistant_advisory_failed` events; failed-terminal jobs (terminal_outcome in {failed, blocked, canceled} or bucket in {failed, canceled}) drop `queue_job_done` / `assistant_job_done` events. `filtered_timeline[:12]` cap preserved.
  - 404 semantics in `build_job_detail_payload`: raises `HTTPException(status_code=404, detail="job not found")` when `lookup_job` returns `None` AND the artifacts directory does not exist. When the artifacts directory exists but the job file is gone, uses the fallback `bucket="unknown"`, synthesized `approval_path` / `failed_sidecar_path` under `pending/approvals/` / `failed/`, and continues to build the payload from artifacts.
  - Payload keys present in the job-detail payload (`job_id`, `bucket`, `job`, `approval`, `state`, `failed_sidecar`, `lock`, `paused`, `plan`, `actions`, `stdout`, `stderr`, `generated_files`, `artifact_files`, `artifact_inventory`, `artifact_anomalies`, `job_context`, `lineage`, `child_refs`, `child_summary`, `execution`, `operator_summary`, `policy_rationale`, `evidence_summary`, `why_stopped`, `recent_timeline`, `artifacts_dir`, `audit_timeline`, `has_approval`, `can_cancel`, `can_retry`, `can_delete`) are identical, in identical order, with identical value semantics.
  - Payload keys present in the job-progress payload (`ok`, `job_id`, `bucket`, `lifecycle_state`, `terminal_outcome`, `current_step_index`, `total_steps`, `last_attempted_step`, `last_completed_step`, `approval_status`, `execution_lane`, `fast_lane`, `intent_route`, `lineage`, `child_refs`, `child_summary`, `parent_job_id`, `root_job_id`, `orchestration_depth`, `sequence_index`, `latest_summary`, `operator_note`, `operator_summary`, `failure_summary`, `stop_reason`, `artifacts`, `step_summaries`, `recent_timeline`) are identical.
  - Four-artifact presence flags in `job_artifact_flags`: `plan` / `actions` / `stdout` / `stderr` file-existence checks under `queue_root / "artifacts" / Path(job_id).stem` are byte-for-byte identical to the original in-app implementation.
  - Route contracts: every `register_*_routes` call in `panel/app.py` is unchanged. Route paths, HTTP methods, response shapes, and callback injection are identical. `test_panel_contract_snapshot.py` continues to pass without modification.
- **Does NOT**: introduce new globals, change any route's public API, change payload shape or truth precedence, loosen 404 semantics on missing jobs, alter artifact presence-flag semantics, add features, refactor any other panel cluster, or touch auth / bridge / security-health / degraded-assistant / health-view / hygiene / route-registration code.
- **Extraction-contract test added — `tests/test_panel_job_detail_shaping_extraction.py` (14 tests)**: narrow, fast pins of the PR D shape so a later decomposition PR can't silently undo the extraction. Asserts (1) `job_detail_sections.py` exposes `build_job_detail_payload`, `build_job_progress_payload`, `build_job_detail_sections` with the documented signatures (explicit `queue_root` / `job_id` positional); (2) `job_presentation.py` exposes `job_artifact_flags(queue_root, job_id)`; (3) `panel.app` still exposes the thin wrapper callbacks `_job_detail_payload`, `_job_progress_payload`, `_job_artifact_flags` and each wrapper's source visibly forwards to the extracted builder (`_build_job_detail_payload_impl(...)`, `_build_job_progress_payload_impl(...)`, `_job_artifact_flags_impl(...)`); (4) each thin wrapper preserves the `(queue_root, job_id) -> dict` route-callback signature that `register_job_routes` expects; (5) `panel.app` no longer defines the extracted private loaders (`_artifact_text`, `_safe_json`, `_load_actions`, `_read_generated_files`, `_payload_lineage`) — those live behind `job_detail_sections` now (`hasattr` check); (6) `panel.app` no longer imports `tail` / `lookup_job` / `queue_snapshot` / `resolve_structured_execution` directly — the builder module is the single panel-side caller of those primitives; (7) `job_detail_sections.py` does NOT reach back into `panel.app` via any import (AST-level check rules out `from . import app` / `from .app import …` / `from .routes_* import …`), pinning the explicit-args architecture invariant matching PR B's `queue_mutation_bridge` and PR C's `security_health_helpers`; (8) 404 semantics are preserved exactly — `build_job_detail_payload` raises `HTTPException(404, "job not found")` when the job cannot be located and the artifacts directory does not exist; (9) `job_artifact_flags` reports the four canonical artifact presence flags with file-existence checks; (10) queue-truth precedence preserved — structured execution wins over state sidecar for `lifecycle_state` / `terminal_outcome`, and the success-terminal recent-timeline filter drops stale `assistant_advisory_failed` events; (11) `build_job_progress_payload` still derives from `build_job_detail_payload` and agrees on `job_id` / `bucket` / `lineage` passthrough (composition sanity check); (12) the thin wrappers in `panel.app` forward `(queue_root, job_id)` through to the extracted builders byte-for-byte; (13) the full 32-key top-level set of `build_job_detail_payload` is frozen against a documented `frozenset` shape lock so a later PR can't silently add / rename / drop a payload key; (14) the full 28-key top-level set of `build_job_progress_payload` is frozen against a documented `frozenset` shape lock for the same reason. HTTP-level behavior is still covered by `test_panel.py::test_job_progress_*` (7 tests) and the templated `/jobs/{job_id}` pages; this file pins the *shape* of the extraction.
- **Validation run**: `ruff format --check .`, `ruff check .`, `mypy src/voxera`, focused panel suite `tests/test_panel.py` + `tests/test_panel_contract_snapshot.py` + `tests/test_panel_automations.py` + `tests/test_panel_auth_enforcement_extraction.py` + `tests/test_panel_queue_mutation_bridge_extraction.py` + `tests/test_panel_security_health_helpers_extraction.py` + `tests/test_panel_job_detail_shaping_extraction.py` (178 tests — 93 + 1 + 38 + 6 + 11 + 15 + 14), `pytest -q` full suite, `make golden-check`, `make security-check`, `make validation-check`, `make merge-readiness-check` — all green. No existing test required any modification; the explicit-args design plus the preserved wrapper signature made the extraction transparent to existing tests, and the new extraction-contract file is purely additive.
- **Files touched**: `src/voxera/panel/job_detail_sections.py` (expanded with the two big builders plus the five private loaders; retains `build_job_detail_sections`), `src/voxera/panel/job_presentation.py` (adds the tiny `job_artifact_flags` helper), `src/voxera/panel/app.py` (imports the extracted entry points, replaces the job-detail cluster with three thin wrappers, drops now-unused `json` / `tail` / `lookup_job` / `queue_snapshot` / `resolve_structured_execution` / `job_artifact_inventory` / `build_job_detail_sections` imports), `tests/test_panel_job_detail_shaping_extraction.py` (new, 14 tests including two payload key-set shape locks), `docs/01_REPOSITORY_STRUCTURE_MAP.md`, `docs/08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md`, `docs/CODEX_MEMORY.md`.
- **Next safe PR step**: PR E should target the next cohesive panel cluster — candidates include (a) the health-view builders (`_daemon_health_view`, `_performance_stats_view`) which are the next-biggest remaining cluster in `panel/app.py` and are cleanly composable into a dedicated `panel_health_view.py` sibling; (b) the degraded-assistant wiring (`_generate_degraded_assistant_answer`, `_generate_degraded_assistant_answer_async`, `_enqueue_assistant_question`) which already forwards to `routes_assistant.py` but still has the async-sync bridge living inline in `panel/app.py`; or (c) the activity builder (`_build_activity`) + the small job-listing helpers (`_last_activity`, `_job_ref_bucket`) which power `GET /jobs` row enrichment. Keep PRs small and preserve the "app.py is the composition root" invariant. The shared session-context / `vera_context` block in the job-detail surface — which PR D unblocks — should NOT be bundled with any of these: it's a separate product change that should ride on top of the now-clean builder surface in a dedicated PR.

## 2026-04-11 — refactor(panel): extract security and health snapshot helpers from panel/app.py (PR C)

- **Motivation**: continuing the gentle, bounded decomposition of `src/voxera/panel/app.py` started by PR A (auth enforcement) and continued by PR B (hygiene / queue mutation bridge). PR C moves the next cohesive cluster — the panel security / health snapshot helpers that previously lived around lines 152–207 of `panel/app.py` (`_health_queue_root`, `_panel_security_counter_incr`, `_panel_security_snapshot`, `_auth_setup_banner`) — into a new sibling module. This PR is **PR C of a multi-PR decomposition plan** and remains just as bounded and behavior-preserving as PR A and PR B.
- **Scope (deliberately bounded)**:
  - Extract ONLY the panel security / health snapshot helper cluster from `panel/app.py`.
  - Move it to a new sibling module `src/voxera/panel/security_health_helpers.py`.
  - Do not refactor auth enforcement (PR A) or queue mutation bridge (PR B) again.
  - Do not refactor job-detail shaping, degraded-assistant handling, presentation helpers, or any route module.
  - Do not change the FastAPI app object structure, route registration order, or any route contract.
  - Do not add features.
- **New module — `src/voxera/panel/security_health_helpers.py`**: owns the narrow wiring between the panel composition root and the health snapshot file: deriving the health-file queue root from the configured panel queue root (with the `VOXERA_HEALTH_PATH` isolation escape hatch), incrementing panel security counters, reading the panel security snapshot, and rendering the auth-setup banner shown at the top of operator pages when `VOXERA_PANEL_OPERATOR_PASSWORD` is not configured. Exposes four narrow documented entry points:
  - `health_queue_root(queue_root)` — derives the health-file queue root from the panel's configured queue root. Returns the configured queue root unchanged in production / when `VOXERA_QUEUE_ROOT` is explicitly set; returns `None` only in the test-only safety net (isolated `VOXERA_HEALTH_PATH` + no explicit `VOXERA_QUEUE_ROOT` + configured queue root matches the repo default `$CWD/notes/queue`) so the caller falls through to the isolated health path.
  - `panel_security_counter_incr(queue_root, key, *, last_error)` — thin forwarder to `voxera.health.increment_health_counter` preserving the original `(key, *, last_error)` call shape.
  - `panel_security_snapshot(queue_root)` — reads the health snapshot and returns its `counters` sub-dict (or `{}` when absent / wrong type).
  - `auth_setup_banner(settings)` — returns `None` when `settings.panel_operator_password` is a non-empty string; otherwise returns the same four-key banner dict (`title`, `detail`, `path_hint`, `commands`) used by `home.html`, `jobs.html`, `automations.html`, and `automation_detail.html`.
- **Explicit-args design (matches PR B, not PR A)**: unlike PR A's `auth_enforcement` (which deliberately reaches back to `panel.app` for shared wrappers via a lazy import), PR C's `security_health_helpers` takes `queue_root` / `settings` as explicit positional arguments on every entry point. This is the cleaner pattern for a helper cluster: every input is visible in the signature, there's no hidden module-level state, no import of `panel.app` from the helper module's side, and the module is easy to unit-test in isolation. The thin wrappers in `panel/app.py` (`_health_queue_root`, `_panel_security_counter_incr`, `_panel_security_snapshot`, `_auth_setup_banner`) close over `_queue_root()` / `_settings()` so the route-registration callback signatures stay identical to their pre-extraction shapes, AND the `auth_enforcement` reach-back pattern (`panel_module._health_queue_root` / `_panel_security_counter_incr`) remains valid because those wrapper names still exist as module-level callables on `panel.app`.
- **`panel/app.py` after extraction**: still visibly the composition root. It still defines the `FastAPI(title="Voxera Panel")` app, mounts `/static`, constructs the Jinja environment, owns the shared `_settings` / `_now_ms` / `_queue_root` wrappers, and registers every route family by calling `register_home_routes(...)`, `register_job_routes(...)`, etc. in the same order as before. Each `register_*_routes` call still receives `health_queue_root=_health_queue_root` / `panel_security_counter_incr=_panel_security_counter_incr` / `panel_security_snapshot=_panel_security_snapshot` / `auth_setup_banner=_auth_setup_banner` as before — `panel/app.py` keeps the thin wrappers to provide the exact same callback signature. The now-unused `import os` and the `from ..health import increment_health_counter, read_health_snapshot` imports are dropped since they only served the extracted helpers.
- **Preserves (semantics exactly)**:
  - `health_queue_root` semantics: identical branching on `VOXERA_HEALTH_PATH` / `VOXERA_QUEUE_ROOT` / `$CWD/notes/queue` resolution, same `Path.expanduser().resolve()` comparison, same `None` vs. configured-queue-root return shape.
  - Panel security counter writes: still route through `voxera.health.increment_health_counter` with the exact same `(queue_root, key, last_error)` arguments.
  - Panel security snapshot reads: still call `voxera.health.read_health_snapshot` on the same derived health queue root and still return the `counters` sub-dict (or `{}` when missing / wrong type).
  - Auth-setup banner: same decision (`panel_operator_password in {None, ""}`), same four-key dict body (byte-for-byte identical `title`, `detail`, `path_hint`, `commands` strings), same template contract.
  - Route contracts: every `register_*_routes` call in `panel/app.py` is unchanged. Route paths, HTTP methods, response shapes, and callback injection are identical. `test_panel_contract_snapshot.py` continues to pass without modification.
  - Reach-back for auth enforcement: `panel.app._health_queue_root` and `panel.app._panel_security_counter_incr` are still module-level callables, so `auth_enforcement._health_queue_root` / `_panel_security_counter_incr` (which lazily look up the attribute on `panel.app` via `_panel_app()`) continue to drive the auth flow exactly as before. Existing `monkeypatch.setattr(panel_module, "_health_queue_root", ...)` / `_panel_security_counter_incr` in `tests/test_panel_auth_enforcement_extraction.py` and `tests/test_panel.py::test_panel_security_*` keep working unchanged.
- **Does NOT**: introduce new globals, change any route's public API, change the health snapshot file path semantics, alter the banner text or template fields, change queue-root/health-path environment variable interpretation, add features, refactor any other panel cluster, or touch auth / bridge / job-detail / degraded-assistant code.
- **Extraction-contract test added — `tests/test_panel_security_health_helpers_extraction.py` (16 tests)**: narrow, fast pins of the PR C shape so a later decomposition PR can't silently undo the extraction. Asserts (1) `security_health_helpers.py` exposes `health_queue_root`, `panel_security_counter_incr`, `panel_security_snapshot`, `auth_setup_banner` with the documented signatures (explicit `queue_root` / `settings` positional, keyword-only `last_error`); (2) `panel.app` still exposes the thin wrapper callbacks `_health_queue_root`, `_panel_security_counter_incr`, `_panel_security_snapshot`, `_auth_setup_banner` and each wrapper's source visibly forwards to the extracted helper via its `_*_impl` alias; (3) `panel.app._auth_setup_banner` no longer contains the inline banner body strings (`"Setup required"`, `"VOXERA_PANEL_OPERATOR_PASSWORD"`, `"systemctl --user edit voxera-panel.service"`) — the delegation is visible; (4) `panel.app` no longer imports `increment_health_counter` / `read_health_snapshot` directly — the extraction holds as the single panel-side caller of those health primitives; (5) `security_health_helpers.py` does NOT reach back into `panel.app` via any import (AST-level check rules out `from . import app` / `from .app import …` / `from .routes_* import …`), pinning the explicit-args architecture invariant matching PR B's `queue_mutation_bridge`; (6) the PR A reach-back-via-wrapper pattern still works — `monkeypatch.setattr(panel_module, "_health_queue_root", ...)` and `monkeypatch.setattr(panel_module, "_panel_security_counter_incr", ...)` are still visible through `auth_enforcement._health_queue_root` / `_panel_security_counter_incr` at call time; (7) `health_queue_root` semantics preserved exactly across the four documented branches (no isolated-health → configured queue root; isolated-health + explicit `VOXERA_QUEUE_ROOT` → configured queue root; isolated-health + default repo queue + no explicit `VOXERA_QUEUE_ROOT` → `None`; isolated-health + non-default queue + no explicit `VOXERA_QUEUE_ROOT` → configured queue root); (8) `panel_security_snapshot` returns `{}` for an empty queue root, `panel_security_counter_incr` writes land in the snapshot counters, and the two round-trip; (9) `auth_setup_banner` returns `None` when `panel_operator_password` is a non-empty string and returns the full four-key dict when empty or `None`; (10) `panel.app` thin wrappers resolve `_queue_root()` / `_settings()` at call time so monkeypatching either of those on `panel.app` drives the forwarded helper call exactly as before. HTTP-level behavior is still covered by `test_panel.py::test_panel_security_*`, `test_panel.py::test_panel_hygiene_health_reset_*`, and the templated pages that render the auth banner; this file pins the *shape* of the extraction.
- **Validation run**: `ruff format --check .` (309 files), `ruff check .` (all checks passed), `mypy src/voxera` (147 source files, 0 issues), focused panel suite `tests/test_panel.py` + `tests/test_panel_contract_snapshot.py` + `tests/test_panel_automations.py` + `tests/test_panel_auth_enforcement_extraction.py` + `tests/test_panel_queue_mutation_bridge_extraction.py` + `tests/test_panel_security_health_helpers_extraction.py` (164 tests — 92 + 1 + 38 + 6 + 11 + 16), `pytest -q` full suite (3349 passed / 2 skipped, +16 from the new extraction-contract file), `python tools/golden_surfaces.py --check`, `pytest -q tests/test_security_redteam.py` (15 tests), validation-check pytest subset (`test_queue_daemon.py`, `test_queue_daemon_contract_snapshot.py`, `test_cli_contract_snapshot.py`, `test_cli_queue.py`, `test_doctor.py` — 151 tests), release-check subset (`test_version_source.py` + `test_panel.py::test_panel_app_uses_shared_version_source` — 5 tests) — all green. No existing test required any modification; the explicit-args design plus the preserved wrapper reach-back pattern made the extraction transparent to existing tests, and the new extraction-contract file is purely additive.
- **Files touched**: `src/voxera/panel/security_health_helpers.py` (new), `src/voxera/panel/app.py` (imports security_health_helpers, replaces the four extracted helper bodies with thin wrappers, drops the now-unused `import os` and `from ..health import increment_health_counter, read_health_snapshot` imports), `tests/test_panel_security_health_helpers_extraction.py` (new, 15 tests), `docs/01_REPOSITORY_STRUCTURE_MAP.md`, `docs/08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md`, `docs/CODEX_MEMORY.md`.
- **Next safe PR step**: PR D should target the next cohesive panel cluster — candidates include (a) the job-detail shaping cluster (`_job_detail_payload`, `_job_progress_payload`, `_job_artifact_flags`, `_last_activity`, `_payload_lineage`, `_build_activity`, `_daemon_health_view`, `_performance_stats_view`) which is now the largest remaining cluster and has tight coupling to `job_detail_sections.py` / `job_presentation.py`; or (b) the degraded-assistant wiring (`_generate_degraded_assistant_answer`, `_generate_degraded_assistant_answer_async`, `_enqueue_assistant_question`) into a dedicated sibling. Keep PRs small and preserve the "app.py is the composition root" invariant.

## 2026-04-11 — refactor(panel): extract hygiene and queue mutation bridge from panel/app.py (PR B)

- **Motivation**: continuing the gentle, bounded decomposition of `src/voxera/panel/app.py` started by PR A (auth enforcement). PR A moved the auth / CSRF / lockout cluster into `panel/auth_enforcement.py`; PR B moves the second cohesive cluster — the hygiene / queue mutation bridge that previously lived around lines 443–704 of `panel/app.py` — into a new sibling module. This PR is **PR B of a multi-PR decomposition plan** and remains just as bounded and behavior-preserving as PR A.
- **Scope (deliberately bounded)**:
  - Extract ONLY the hygiene / queue mutation bridge cluster from `panel/app.py`.
  - Move it to a new sibling module `src/voxera/panel/queue_mutation_bridge.py`.
  - Do not refactor auth logic, job-detail shaping, degraded-assistant handling, presentation helpers, or any route module.
  - Do not change the FastAPI app object structure, route registration order, or any route contract.
  - Do not add features.
- **New module — `src/voxera/panel/queue_mutation_bridge.py`**: owns the narrow seam that turns panel mutation intents (create-job, create-panel-mission, run-queue-hygiene, persist-hygiene-result) into actual queue/job writes and CLI subprocess invocations. Exposes two narrow documented entry points plus the two bridge helpers used by the hygiene and home route registrations:
  - `run_queue_hygiene_command(queue_root, args)` — fail-closed subprocess bridge that invokes `python -m voxera.cli … --queue-dir …` (falling back to the installed `voxera` console script) with `capture_output=True`. Always returns a well-formed result dict (`ok`, `result`, `exit_code`, `stderr_tail`, `stdout_tail`, `cmd`, `cwd`, `attempted`, `error`) — never raises for CLI failures — and emits a `panel_hygiene_command_failed` audit event on every failure path. Drives `/hygiene/prune-dry-run` and `/hygiene/reconcile`.
  - `write_panel_mission_job(queue_root, *, prompt, approval_required)` — atomic inbox writer for panel-submitted mission prompts; computes the slug/mission-id exactly as before, enriches via `enrich_queue_job_payload(..., source_lane="panel_mission_prompt")`, and uses the same `tmp_path.replace(final_path)` atomic rename with the same collision-retry suffixing. Drives `POST /missions/create`.
  - `write_queue_job(queue_root, payload)` — atomic inbox writer for panel-submitted generic queue jobs; enriches via `enrich_queue_job_payload(..., source_lane="panel_queue_create")`. Drives `POST /queue/create`.
  - `write_hygiene_result(queue_root, key, result, *, now_ms)` — persists a hygiene run result into the health snapshot under `last_prune_result` / `last_reconcile_result`, stamping `updated_at_ms` from the caller-supplied `now_ms` callable. Takes `now_ms` as an explicit keyword-only arg so the thin wrapper in `panel.app` can pass `_now_ms` (which Python resolves at call time via module globals, preserving existing monkeypatch discipline).
  - Also owns the moved-but-private helpers `_trim_tail` and `_repo_root_for_panel_subprocess`.
- **Explicit-args design (vs. reach-back)**: unlike PR A's `auth_enforcement` (which reaches back to `panel.app` for shared wrappers via a lazy import), PR B's `queue_mutation_bridge` takes `queue_root` as an explicit positional argument on every entry point and takes `now_ms` as an explicit keyword-only argument on `write_hygiene_result`. This is the cleaner pattern for a mutation bridge: every input is visible in the signature, there's no hidden module-level state, and the module is easy to unit-test in isolation (no panel.app import at all from the bridge module's side). The thin wrappers in `panel/app.py` (`_write_queue_job`, `_write_panel_mission_job`, `_run_queue_hygiene_command`, `_write_hygiene_result`) close over `_queue_root()` / `_now_ms` so the route-registration callback signatures stay identical to their pre-extraction shapes.
- **`panel/app.py` after extraction**: still visibly the composition root. It still defines the `FastAPI(title="Voxera Panel")` app, mounts `/static`, constructs the Jinja environment, owns the shared `_settings` / `_now_ms` / `_queue_root` / `_health_queue_root` / `_panel_security_counter_incr` / `_panel_security_snapshot` wrappers that non-bridge clusters also use, and registers every route family by calling `register_home_routes(...)`, `register_job_routes(...)`, etc. in the same order as before. Each `register_*_routes` call still receives `write_queue_job=_write_queue_job` / `run_queue_hygiene_command=_run_queue_hygiene_command` / `write_hygiene_result=_write_hygiene_result` / `write_panel_mission_job=_write_panel_mission_job` as before — `panel/app.py` keeps the thin wrappers to provide the exact same callback signature. `import subprocess` and `import sys` remain in `panel/app.py` with `# noqa: F401` markers because `tests/test_panel.py::test_hygiene_*` monkeypatch via `panel_module.subprocess.run` and assert `panel_module.sys.executable`; Python module singletons make those monkeypatches still drive the extracted bridge's `subprocess.run` call without any test churn.
- **Preserves (queue-truth + fail-closed semantics)**:
  - Atomic job writes: `write_queue_job` and `write_panel_mission_job` still produce a `.tmp.json` in `inbox/`, write it via `write_text(..., encoding="utf-8")`, and then rename via `tmp_path.replace(final_path)` — unchanged atomic-write contract.
  - Source-lane envelopes: `panel_queue_create` and `panel_mission_prompt` are both preserved exactly via `enrich_queue_job_payload(..., source_lane=...)`. Downstream lanes that dispatch on `job_intent.source_lane` keep seeing the same values.
  - Mission-id stability: `write_panel_mission_job` still computes the mission-id as `re.sub(...)` over `f"{slug}-{suffix}-{ts}"` using the same `slug[:32]` / `sha1(prompt)[:6]` / `int(time.time())` recipe. Collision suffixing (`base-1.json`, `base-2.json`, …) is preserved.
  - Fail-closed hygiene CLI: `run_queue_hygiene_command` still tries `sys.executable -m voxera.cli …` first, falls back to the `voxera` console script on `FileNotFoundError`, still emits `panel_hygiene_command_failed` on every failure path (non-zero rc, empty stdout on rc=0, non-dict JSON, JSON parse error, both commands missing), and still returns a result dict with `_trim_tail`-bounded `stderr_tail`/`stdout_tail` (2000 chars) and the full `cmd`/`cwd`/`attempted` lineage.
  - Hygiene health-snapshot stamping: `write_hygiene_result` still writes the result under the caller-specified key (`last_prune_result` / `last_reconcile_result`) and stamps `updated_at_ms` via the caller-supplied `now_ms` callable, keeping the `_now_ms` monkeypatch discipline valid through `panel.app._write_hygiene_result`.
  - Route contracts: every `register_*_routes` call in `panel/app.py` is unchanged. Route paths, HTTP methods, response shapes, and callback injection are identical. `test_panel_contract_snapshot.py` continues to pass without modification.
- **Does NOT**: introduce new globals, change any route's public API, change queue-job envelopes or source-lane values, change atomic-write semantics, loosen fail-closed behavior on hygiene CLI failures, alter the mission-id recipe, add features, refactor any other panel cluster, or touch auth / job-detail / degraded-assistant code.
- **Extraction-contract test added — `tests/test_panel_queue_mutation_bridge_extraction.py` (11 tests)**: narrow, fast pins of the PR B shape so a later decomposition PR can't silently undo the extraction. Asserts (1) `queue_mutation_bridge.py` exposes `run_queue_hygiene_command`, `write_panel_mission_job`, `write_queue_job`, `write_hygiene_result` with the documented signatures (explicit `queue_root` positional, explicit `now_ms` keyword-only on `write_hygiene_result`); (2) `panel.app` still exposes the thin wrapper callbacks `_write_queue_job`, `_write_panel_mission_job`, `_run_queue_hygiene_command`, `_write_hygiene_result` and each wrapper's source visibly forwards to the extracted bridge function; (3) `panel.app` does NOT locally re-define `_trim_tail` or `_repo_root_for_panel_subprocess`; (4) queue-truth semantics: `write_queue_job` writes `source_lane=panel_queue_create` and leaves no tmp files behind; `write_panel_mission_job` writes `source_lane=panel_mission_prompt`, preserves the full `expected_artifacts` list and `approval_hints`, and produces a mission-id that matches the stored `id`; (5) fail-closed `run_queue_hygiene_command`: non-zero rc produces `ok=False` + non-empty `error` + `stderr_tail`; rc=0 with non-JSON stdout produces `ok=False` + `error="json parse failed"` + `stdout_tail` preserved; (6) `write_hygiene_result` uses the injected `now_ms` callable and lands the `updated_at_ms` stamp; (7) the reach-back-via-wrapper pattern works — `panel.app._write_hygiene_result` reads `_now_ms` from its module globals at call time, so `monkeypatch.setattr(panel_module, "_now_ms", ...)` still drives the `updated_at_ms` stamp through the thin wrapper; (8) `panel.app.subprocess is subprocess` and `panel.app.sys is sys` — pins the `# noqa: F401` re-export surface so a later PR can't silently drop the imports and break every `test_panel.py::test_hygiene_*` monkeypatch at once; (9) `queue_mutation_bridge.py` does NOT reach back into `panel.app` (AST-level check rules out `from . import app` / `from .app import …` / `from .routes_* import …`), pinning the explicit-args architecture invariant that distinguishes PR B from PR A's deliberate reach-back pattern. HTTP-level behavior is still covered by `test_panel.py::test_hygiene_*` and `test_panel.py::test_panel_queue_create_*`; this file pins the *shape* of the extraction.
- **Validation run**: `ruff format --check .`, `ruff check .`, `mypy src/voxera` (146 source files, 0 issues), focused panel suite `tests/test_panel.py` + `tests/test_panel_contract_snapshot.py` + `tests/test_panel_automations.py` + `tests/test_panel_auth_enforcement_extraction.py` + `tests/test_panel_queue_mutation_bridge_extraction.py` (148 tests — 92 + 1 + 38 + 6 + 11), focused hygiene/mission/queue_create subset inside `test_panel.py` (`-k "hygiene or mission or queue_create or panel_mission"`, 19 tests), `pytest -q` full suite (3333 passed / 2 skipped, +11 from the new extraction-contract file), `make golden-check`, `make security-check`, `make validation-check`, `make merge-readiness-check` — all green. No existing test required any modification; the explicit-args design plus `subprocess`/`sys` re-exports made the extraction transparent to existing tests, and the new extraction-contract file is purely additive.
- **Files touched**: `src/voxera/panel/queue_mutation_bridge.py` (new), `src/voxera/panel/app.py` (imports queue_mutation_bridge, replaces the extracted cluster with thin wrappers, drops now-unused `hashlib`/`uuid` imports and `enrich_queue_job_payload`/`update_health_snapshot` imports, keeps `import subprocess`/`import sys` with `# noqa: F401` markers for test monkeypatch compat), `tests/test_panel_queue_mutation_bridge_extraction.py` (new, 11 tests), `docs/01_REPOSITORY_STRUCTURE_MAP.md`, `docs/08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md`, `docs/CODEX_MEMORY.md`.
- **Next safe PR step**: PR C should target the next cohesive panel cluster — candidates include (a) the panel security/health snapshot integration helpers (`_panel_security_snapshot`, `_panel_security_counter_incr`, `_health_queue_root`, `_auth_setup_banner`) into a dedicated sibling; (b) the job-detail shaping cluster (`_job_detail_payload`, `_job_progress_payload`, `_job_artifact_flags`, `_last_activity`, `_payload_lineage`) which is the largest remaining cluster and has tight coupling to `job_detail_sections.py` / `job_presentation.py`; or (c) the degraded-assistant wiring (`_generate_degraded_assistant_answer`, `_generate_degraded_assistant_answer_async`, `_enqueue_assistant_question`) into a dedicated sibling. Keep PRs small and preserve the "app.py is the composition root" invariant.

## 2026-04-11 — refactor(panel): extract auth enforcement from panel/app.py (PR A)

- **Motivation**: `src/voxera/panel/app.py` is a multi-cluster composition root at ~1425 lines. This PR is **PR A of a multi-PR decomposition plan** and performs only the gentlest first extraction — the auth enforcement + CSRF mutation guard + per-IP lockout cluster that previously lived around lines 528–698 of `panel/app.py`.
- **Scope (deliberately bounded)**:
  - Extract ONLY the auth / CSRF / lockout cluster from `panel/app.py`.
  - Move it to a new sibling module `src/voxera/panel/auth_enforcement.py`.
  - Do not touch any other panel cluster (presentation helpers, job detail shaping, hygiene bridge, assistant wiring, route modules).
  - Do not change the FastAPI app object structure, route registration order, or any route contract.
  - Do not broaden scope to a full panel decomposition.
- **New module — `src/voxera/panel/auth_enforcement.py`**: owns the operator Basic-auth guard, CSRF mutation guard, per-IP failure/lockout bookkeeping, and the security-event audit logging that gates panel mutation routes. Exposes two narrow entry points:
  - `require_operator_basic_auth(request)` — fail-closed Basic auth check: reads `authorization` from `request`, enforces operator user/password from runtime config, updates the health-backed per-IP failure/lockout state via `auth_state_store`, and raises `HTTPException(401/429/503)` with the same status codes, headers, and detail strings as before.
  - `require_mutation_guard(request)` — fail-closed mutation gate: calls `require_operator_basic_auth` first, then enforces CSRF cookie/header|form double-submit with `secrets.compare_digest` (when `panel_csrf_enabled` is true), raising `HTTPException(403)` on missing or mismatched tokens.
  - Also owns the moved-but-private helpers: `_operator_credentials`, `_client_ip`, `_panel_auth_state_update`, `_panel_auth_state_prune`, `_active_lockout_until_ms`, `_request_meta`, `_log_panel_security_event`, and the `_PanelSecurityRequestLike` protocol.
- **Reach-back pattern (deliberate)**: `auth_enforcement.py` lazily imports `voxera.panel.app` inside its internal `_panel_app()` helper to look up the shared composition-root wrappers `_settings`, `_now_ms`, `_health_queue_root`, and `_panel_security_counter_incr`. This preserves the existing test monkeypatch surface — tests like `test_panel_auth_lockout_after_10_failures` do `monkeypatch.setattr(panel_module, "_now_ms", ...)`, and because `auth_enforcement` goes through the `panel.app` module object (not a captured reference), those patches continue to drive the auth flow exactly as before. Same reasoning for `_panel_security_counter_incr`, which `test_panel_security_snapshot_reads_same_default_root_as_counter_writes` calls directly on `panel_module`.
- **`panel/app.py` after extraction**: still visibly the composition root. It still defines the `FastAPI(title="Voxera Panel")` app, mounts `/static`, constructs the Jinja environment, owns the shared `_settings` / `_now_ms` / `_health_queue_root` / `_panel_security_counter_incr` / `_panel_security_snapshot` wrappers that non-auth clusters also use, and registers every route family by calling `register_home_routes(...)`, `register_job_routes(...)`, etc. in the same order as before. The route registration keyword arguments `require_operator_auth_from_request=_require_operator_auth_from_request` and `require_mutation_guard=_require_mutation_guard` are unchanged — `_require_mutation_guard` is now the `require_mutation_guard` symbol imported from `auth_enforcement.py`, and `_require_operator_auth_from_request` is a thin wrapper in `app.py` that forwards to `require_operator_basic_auth`. `_operator_credentials` is re-exported from `app.py` so the existing `tests/test_dev_contract_config_integration.py::test_panel_operator_defaults_to_admin_and_missing_password_raises` contract test keeps working without churn.
- **Preserves (fail-closed semantics)**:
  - Operator Basic auth: missing `VOXERA_PANEL_OPERATOR_PASSWORD` still returns `HTTPException(503, "VOXERA_PANEL_OPERATOR_PASSWORD must be set")` and emits `panel_operator_config_error` + `panel_401_count` counter with `last_error="operator password missing"`.
  - Missing/invalid scheme/invalid header/invalid credentials: still return `HTTPException(401)` with `WWW-Authenticate: Basic`, the same detail strings (`"operator authentication required"`, `"invalid authentication scheme"`, `"invalid authorization header"`, `"invalid operator credentials"`), and the same counter + audit event semantics (`panel_auth_missing` / `panel_auth_invalid`, `panel_401_count`, and `panel_auth_invalid` counter with the specific `last_error`).
  - Lockout: 10 failures in 60s still produces `HTTPException(429, "too many authentication attempts", headers={"Retry-After": "60"})`, still emits the `panel_auth_lockout` audit event with `attempt_count` / `window_s` / `lockout_s`, still bumps `panel_429_count`. The health-backed per-IP state still flows through `auth_state_store.apply_panel_auth_state_update` / `apply_panel_auth_state_prune` / `active_lockout_until_ms` / `auth_failure_snapshot`, and still honors the `VOXERA_HEALTH_PATH` isolated-health-path escape hatch via `_health_queue_root()`.
  - CSRF: `panel_csrf_enabled=False` still short-circuits with `panel_mutation_allowed` + `reason="auth_valid_csrf_disabled"`. When enabled, both the `voxera_panel_csrf` cookie and either the `x-csrf-token` header or the `csrf_token` form/query value are still required, still compared with `secrets.compare_digest`, and still fail-closed with `HTTPException(403, "csrf validation failed")` + `panel_403_count` + `panel_csrf_missing`/`panel_csrf_invalid` counters + `panel_csrf_missing`/`panel_csrf_invalid` audit events.
  - Route contracts: every `register_*_routes` call in `panel/app.py` is unchanged. Route paths, HTTP methods, response shapes, and callback injection (`require_mutation_guard=...`, `require_operator_auth_from_request=...`, `health_queue_root=...`, `panel_security_counter_incr=...`) are identical. `CSRF_COOKIE`/`CSRF_FORM_KEY` still live in `panel/app.py` and are still passed as route-registration constants. `test_panel_contract_snapshot.py` continues to pass without modification.
- **Does NOT**: introduce new globals, change any route's public API, change CSRF cookie/form key names, alter lockout thresholds or windows, change settings usage, add features, refactor any other panel cluster, or change the FastAPI app object.
- **Extraction-contract test added — `tests/test_panel_auth_enforcement_extraction.py` (6 tests)**: narrow, fast pins of the PR A shape so a later decomposition PR can't silently undo the extraction. Asserts (1) `auth_enforcement.require_operator_basic_auth` and `auth_enforcement.require_mutation_guard` exist with `(request)`-only signatures and the expected sync/async nature; (2) `panel.app._require_mutation_guard is auth_enforcement.require_mutation_guard` and `panel.app._require_operator_auth_from_request` is a thin wrapper that forwards to `require_operator_basic_auth`; (3) `panel.app._operator_credentials is auth_enforcement._operator_credentials` (the re-export that keeps `test_dev_contract_config_integration` passing); (4) `panel.app` does NOT locally re-define any of the extracted private helpers (`_client_ip`, `_panel_auth_state_update`, `_panel_auth_state_prune`, `_active_lockout_until_ms`, `_log_panel_security_event`, `_request_meta`, `_PanelSecurityRequestLike`); (5) the reach-back pattern works — `monkeypatch.setattr(panel_module, "_now_ms", ...)` / `_health_queue_root` / `_panel_security_counter_incr` is visible through the `auth_enforcement._now_ms` / `_health_queue_root` / `_panel_security_counter_incr` wrappers; and (6) `require_operator_basic_auth` directly raises `HTTPException(401, "operator authentication required", headers={"WWW-Authenticate": "Basic"})` on a missing `Authorization` header at the unit level. HTTP-level behavior is still covered by `test_panel.py`; this file pins the *shape* of the extraction.
- **Validation run**: `ruff format --check .`, `ruff check .`, `mypy src/voxera` (145 source files, 0 issues), `pytest -q` full suite (3322 passed / 2 skipped, +6 from the new extraction-contract file), focused panel suite `tests/test_panel.py` + `tests/test_panel_contract_snapshot.py` + `tests/test_panel_automations.py` + `tests/test_panel_auth_enforcement_extraction.py` (137 tests — 92 + 1 + 38 + 6), the 14 auth/CSRF/lockout-targeted tests inside `test_panel.py` (`-k "lockout or auth or csrf or mutation or 401 or 403 or 429 or security"`), `tests/test_dev_contract_config_integration.py` (4 tests, including the `_operator_credentials` contract test), `python tools/golden_surfaces.py --check`, `pytest -q tests/test_security_redteam.py` (15 tests), the validation-check pytest subset (`test_queue_daemon.py`, `test_queue_daemon_contract_snapshot.py`, `test_cli_contract_snapshot.py`, `test_cli_queue.py`, `test_doctor.py` — 151 tests), and the release-check subset — all green. No existing test required any modification; the monkeypatch-through-`panel.app` reach-back design made the extraction transparent to existing tests, and the new extraction-contract file is purely additive.
- **Files touched**: `src/voxera/panel/auth_enforcement.py` (new), `src/voxera/panel/app.py` (imports auth_enforcement, removes the extracted cluster, keeps `_operator_credentials` re-exported for the existing contract test, keeps `_require_operator_auth_from_request` as a thin wrapper), `tests/test_panel_auth_enforcement_extraction.py` (new, 6 tests), `docs/01_REPOSITORY_STRUCTURE_MAP.md`, `docs/08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md`, `docs/CODEX_MEMORY.md`.
- **Next safe PR step**: PR B should target the next cohesive panel cluster on the decomposition roadmap — for example, the panel security/health snapshot integration helpers (`_panel_security_snapshot`, `_panel_security_counter_incr`, `_health_queue_root`) into a dedicated sibling while preserving the callback-injection seam into the route modules that take `panel_security_counter_incr=` and `health_queue_root=` today. Keep PRs small and preserve the "app.py is the composition root" invariant.

## 2026-04-11 — refactor(vera-web): extract automation and review lanes from app.py

- **Motivation**: the automation and review lane branches added over the past PRs pushed `src/voxera/vera_web/app.py` to ~1936 lines, making the top-level dispatch harder to scan at a glance. This PR is a **small, targeted decomposition** of only those two lane areas — not a broad lane-framework refactor, not a dispatcher redesign, not a change to any runtime / queue / automation / review semantics.
- **Scope (deliberately bounded)**:
  - Extract the automation lane branches (submit, draft/revise, lifecycle, shell materialization) from `app.py`.
  - Extract the review-lane glue (`active_preview_revision_in_flight` computation with the review/evidence belt-and-suspenders, and the early-exit state-write choreography) from `app.py`.
  - Leave older / stable lanes inline in `app.py`.
  - Keep `app.py` as the visible top-level orchestrator owning lane precedence.
- **New package — `src/voxera/vera_web/lanes/`**: tiny package with an `__init__.py` that simply documents the narrow extraction intent and two modules:
  - **`lanes/automation_lane.py`** — owns the automation lane detectors (regex blocks and `_detect_automation_clarification_completion`, `_looks_like_direct_automation_request`, `_looks_like_previewable_automation_intent`, `_synthesize_direct_automation_preview`, `_PREVIEWABLE_AUTOMATION_CLARIFICATION_REPLY`) and four lane entry points: `try_submit_automation_preview_lane`, `try_automation_draft_or_revision_lane`, `try_automation_lifecycle_lane`, and `try_materialize_automation_shell`. Each entry point returns an `AutomationLaneResult` with `matched`/`assistant_text`/`status`/`dispatch_source`/`matched_early_exit` so `app.py` can perform a uniform `append_session_turn` + routing-debug + render after a matched claim. Every preview mutation flows through `voxera.vera.preview_ownership` helpers (`reset_active_preview`, `record_submit_success`); automation previews continue to use `mark_handoff_ready=False` because automation submit saves a durable definition rather than emitting a queue job. The save-vs-execute truth boundary in `vera/automation_preview.py` and `vera/automation_lifecycle.py` is unchanged.
  - **`lanes/review_lane.py`** — owns `compute_active_preview_revision_in_flight(message, pending_preview)`, which combines `preview_routing.is_active_preview_revision_turn` with the review/evidence + investigation-save belt-and-suspenders (ambiguous `is_save_followup_request` / `is_revise_from_evidence_request` / `is_investigation_save_request` / `is_investigation_derived_save_request` phrases on a normal active preview are treated as revision candidates). Also owns `apply_early_exit_state_writes(result, queue_root, session_id)`, which performs the preview installation (`record_followup_preview` for source-job-backed follow-ups; `reset_active_preview` otherwise), the `context_on_review_performed` single-key shortcut for `last_reviewed_job_ref` updates, multi-key `update_session_context` fallback, and the derived-investigation-output write. Preview mutations still flow exclusively through `preview_ownership` helpers — the module never calls `write_session_preview` directly.
- **`app.py` after extraction**: down to ~1507 lines from ~1936 (−429 lines). The `chat()` handler still visibly orchestrates the extracted lanes — each lane entry point is called inline in canonical precedence order, followed by `append_session_turn` + `append_routing_debug_entry` + `_render_page`. The lane docstring at the top of `chat()` and `preview_routing.canonical_preview_lane_order()` are unchanged; the sanity assert on lane count still fires. The module-level detectors that moved to `automation_lane.py` are re-exported from `app.py` under their original private names so existing test imports continue to work without churn (`_PREVIEWABLE_AUTOMATION_CLARIFICATION_REPLY`, `_detect_automation_clarification_completion`, `_looks_like_direct_automation_request`, `_looks_like_previewable_automation_intent`).
- **Regression coverage added — `tests/test_vera_web_lanes_extraction.py` (18 tests)**:
  - `TestAppStillVisiblyOrchestratesLanes` — asserts `app.py` imports each extracted lane entry point, that `chat()` source still literally references them, and that `canonical_preview_lane_order()` is still seven lanes.
  - `TestAutomationLaneResultContract` — pins the `AutomationLaneResult` shape.
  - `TestComputeActivePreviewRevisionInFlight` — verifies the narrow gate, the belt-and-suspenders firing on a normal preview with an ambiguous save-followup phrase, and the step-aside on automation previews.
  - `TestApplyEarlyExitStateWrites` — pins the write choreography: no-op on unmatched, `record_followup_preview` for source-job previews, `context_on_review_performed` shortcut for single-key `last_reviewed_job_ref` updates.
  - `TestLaneModulesPreviewOwnershipDiscipline` — enforces that neither lane module has a direct `write_session_preview(` call and that both modules import the approved `preview_ownership` helpers.
  - `TestAutomationLaneEndToEnd` — end-to-end smoke through `/chat` for automation draft → `go ahead` save, plus a step-aside assertion that a normal preview under revision suppresses the lifecycle lane.
- **Validation run**: `ruff format --check .`, `ruff check .`, `mypy src/voxera`, `pytest -q` (full suite), `python tools/golden_surfaces.py --check`, and `pytest tests/test_security_redteam.py` all green. Focused lane-touching tests (`test_vera_preview_stabilization.py`, `test_vera_automation_preview.py`, `test_vera_automation_lifecycle.py`, `test_evidence_review.py`, `test_linked_job_review_continuation.py`, `test_vera_preview_materialization.py`, `test_vera_preview_submission.py`, `test_vera_web_lanes_extraction.py`) all pass with no behavioral regressions.
- **Preserves**: PR #311 preview stabilization guarantees (no scattered preview-state writes, no preview hijack regressions, no blurred ownership); `app.py` as the visible top-level lane-order owner; ambiguity still fails closed; queue remains the execution boundary; automation save-vs-execute and review/evidence truth boundaries unchanged.
- **Does NOT**: introduce a generic lane framework, redesign the dispatcher, extract the older stable lanes, add features, or change any semantic truth boundary.
- **Files touched**: `src/voxera/vera_web/lanes/__init__.py` (new), `src/voxera/vera_web/lanes/automation_lane.py` (new), `src/voxera/vera_web/lanes/review_lane.py` (new), `src/voxera/vera_web/app.py`, `tests/test_vera_web_lanes_extraction.py` (new), `docs/05_VERA_CONTROL_LAYER_AND_HANDOFF.md`, `docs/08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md`, `docs/CODEX_MEMORY.md`.

## 2026-04-11 — fix(systemd): make automation runner timer-owned in the default install path

- **Motivation**: the automation subsystem was functionally working, but the install path enabled both `voxera-automation.service` and `voxera-automation.timer`. That is not the clean model for a timer-driven oneshot worker — the service should exist and be triggerable, but the timer should own scheduling. Brand-new users should get the correct topology automatically from the normal install path.
- **Scope**: clean up the automation service/timer install model and `make services-install`. No changes to automation runtime behavior, queue semantics, or any new features.
- **Unit change** (`deploy/systemd/user/voxera-automation.service`): removed `[Install] WantedBy=default.target`. The service is now timer-owned — it has no `[Install]` section and is not directly enableable. It remains a valid oneshot worker (`Type=oneshot`, `WorkingDirectory=%h/VoxeraOS`, `ExecStart=%h/VoxeraOS/.venv/bin/voxera automation run-due-once`) and remains addressable for `systemctl --user status voxera-automation.service`, `journalctl --user -u voxera-automation.service`, and manual `systemctl --user start voxera-automation.service` for debugging.
- **Timer unchanged** (`deploy/systemd/user/voxera-automation.timer`): still `OnCalendar=minutely`, `Persistent=true`, `[Install] WantedBy=timers.target`. The timer is the thing users enable in the normal install path.
- **Makefile** (`make services-install`): added a new `VOXERA_ENABLED_UNITS` subset (`voxera-daemon.service voxera-panel.service voxera-vera.service voxera-automation.timer`). `services-install` still copies all units in `VOXERA_UNITS` (including `voxera-automation.service`, which the timer needs to trigger) into `~/.config/systemd/user/`, runs `daemon-reload`, and then enables+starts the `VOXERA_ENABLED_UNITS` subset. `services-disable` was also updated to disable the same subset. `services-restart`, `services-status`, and `services-stop` continue to operate over the full `VOXERA_UNITS` set.
- **Tests updated** (`tests/test_automation_lock.py`): added `test_automation_service_is_timer_owned_not_directly_enabled` (no `[Install]` section, no `WantedBy=default.target`), `test_automation_timer_is_enableable_under_timers_target`, and `test_makefile_install_enables_timer_not_automation_service` (asserts `VOXERA_UNITS` still ships the service, `VOXERA_ENABLED_UNITS` drops it, and the install target enables the subset not the full set). The existing `test_vera_service_contract_is_present` test in `tests/test_docs_consistency.py` still asserts the full `VOXERA_UNITS` line as-is — the full set is unchanged, only the enabled subset is new.
- **Docs updated**: `docs/02_CONFIGURATION_AND_RUNTIME_SURFACES.md`, `docs/ops.md`, `docs/01_REPOSITORY_STRUCTURE_MAP.md` — describe the automation runner as timer-owned, clarify that the service is the worker and the timer owns cadence, and that `make services-install` enables the timer (not the service) while still copying the service unit so it is addressable for status/logs/manual start.
- **Non-goals**: no new services, no changes to automation runtime behavior, no queue semantics changes, no redesign of the wider systemd install flow. Scope kept small and precise — the default new-user install path is now the clean timer-owned model.
- **Files touched**: `deploy/systemd/user/voxera-automation.service`, `Makefile`, `tests/test_automation_lock.py`, `docs/02_CONFIGURATION_AND_RUNTIME_SURFACES.md`, `docs/ops.md`, `docs/01_REPOSITORY_STRUCTURE_MAP.md`, `docs/CODEX_MEMORY.md`.

## 2026-04-11 — fix(vera): close investigation-save hijack on active script previews (PR #311 follow-up)

- **Motivation**: live product reproduction on PR #311 surfaced one remaining hijack path. Turn 1: "Draft a Python script that scans a folder and lists all .txt files." → normal active script preview at `~/VoxeraOS/notes/scan.py`. Turn 2: "Make it save the results to a file." → Vera replied `"I couldn't resolve those investigation result references in this session..."` instead of mutating the active preview.
- **Root cause**: `is_investigation_save_request` uses an extremely broad detector (`(save|write|export)` + `(results?|findings?)`), so "Make it save the results to a file" matched it. The belt-and-suspenders layer from the previous review pass only covered `is_save_followup_request` and `is_revise_from_evidence_request`, so the investigation-save branch was not protected. The early-exit lane-8 investigation-save branch fired, `draft_investigation_save_preview` returned None (no investigation in session), and the branch emitted the confusing "couldn't resolve those investigation result references" error — hijacking the revision turn.
- **Fix (two-part, same PR)**:
  - **(a) Widen the revision gate** in `src/voxera/vera_web/preview_routing.py` with narrow script-enhancement patterns that require a subject pronoun (it/that/this) or an explicit "the script/code/program/draft/note" anchor: `make (it|that|this) (save|write|output|export|log|print|emit|produce|report)`, `have (it|...) ...`, `make (the|this|that) (script|code|program|draft|note|file) ...`, `add (file )?(logging|output|reporting|writing to a file)`, `add (a )?(log|output|report|result) (file|step|writer|output)`, and `make (it|...) write the (output|results|findings|report) to ...`. Bare investigation-save phrasing (no pronoun anchor, no active preview) is still routed to the investigation lane.
  - **(b) Extend the belt-and-suspenders layer** in `src/voxera/vera_web/app.py` to also mark `is_investigation_save_request` and `is_investigation_derived_save_request` matches as revision candidates when a normal active preview is present. This catches phrases the narrow gate patterns do not cover and ensures the early-exit lanes 4 / 8 and the derived-save lane all step aside.
- **Regression tests added** (`tests/test_vera_preview_stabilization.py`, now 113 tests, up from 81):
  - `TestScriptEnhancementGate` — 15 positive parametrized phrases including the exact live repro and all 7 task-listed variants, plus 4 negative "bare investigation-save" phrases that must stay in the investigation lane, plus 3 negative "no active preview" phrases.
  - `test_pr311_live_hijack_make_it_save_the_results_to_a_file` — named end-to-end regression for the exact live reproduction. Installs the scan.py preview, sends the literal live message, asserts the "couldn't resolve those investigation result references" error never reaches chat and the preview path is still `scan.py`.
  - `test_pr311_script_enhancement_does_not_hijack_active_preview` — parametrized end-to-end regression over all 7 task phrases.
  - `test_pr311_legitimate_investigation_save_still_works` — confirms that genuine investigation-save still fires when NO active preview is in play (fail-closed-with-canonical-error behavior is preserved).
  - `test_pr311_ambiguous_change_still_fails_closed` — confirms that bare "change it" / "fix it" / "make it better" still fail closed even with an active script preview present.
- **Validation run**: `ruff format --check .`, `ruff check .`, `mypy src/voxera` — all clean. Full `pytest` (3245 passed / 2 skipped, up from 3213), `make golden-check`, `make security-check`, `make validation-check`, `make merge-readiness-check` — all green.
- **Scope preserved**: no new features, no new automation runtime behavior, no routing architecture rewrite. The revision gate remains narrow (still requires a pronoun or explicit draft-subject anchor). Fail-closed behavior for vague turns is preserved. Investigation-save still works when no normal active preview is present.
- **Files touched**: `src/voxera/vera_web/preview_routing.py`, `src/voxera/vera_web/app.py`, `tests/test_vera_preview_stabilization.py`, `docs/CODEX_MEMORY.md`.

## 2026-04-11 — review(vera): tighten early-exit revision protection for preview stabilization

- **Motivation**: critical review pass on the `refactor(vera): stabilize preview routing…` PR. The first pass protected the **automation lifecycle** lane from hijacking active-preview revision turns but left the **early-exit dispatch** branches (follow-up-from-evidence, save-follow-up, revise-from-evidence, investigation derived-save, investigation save) running before any revision-lane gate. Those branches could still silently clobber an active preview when the user typed ambiguous phrases like "revise that based on the result" or "save the follow-up as a file".
- **Fix 1 — thread `active_preview_revision_in_flight` through `dispatch_early_exit_intent`**: the flag is computed once in `app.py` via `preview_routing.is_active_preview_revision_turn` (same predicate used by the automation-lifecycle gate) and passed into `chat_early_exit_dispatch.dispatch_early_exit_intent`. Inside the early-exit dispatch the flag gates the preview-writing branches: the outer `is_followup_preview_request` branch (which contains the 3a revise-from-evidence, 3b save-follow-up, and 3c general follow-up sub-branches), the investigation-derived-save branch (lane 4), and the investigation-save branch (lane 8). Non-mutating branches (time, diagnostics refusal, job review report, investigation compare/summary/expand, near-miss submit, stale-draft reference) still run — they never touch preview state.
- **Fix 2 — belt-and-suspenders layer for ambiguous phrasing**: the narrow revision-gate patterns do not match every hijack phrase. "save the follow-up as a file" and "update that based on the result" both match the evidence-review hint lists but not the conservative revision-verb patterns. `app.py` now also marks the revision as in flight when a normal active preview is present AND `is_save_followup_request(message)` OR `is_revise_from_evidence_request(message)` matches. This is the fail-closed choice: when a phrase could mean either "mutate this preview" or "spawn a new evidence follow-up", we prefer not to mutate the wrong object. Legitimate evidence-grounded follow-ups still work when no active preview is present, or when the user explicitly submits / clears the active preview first.
- **Fix 3 — tighten `is_normal_preview` defensively**: the predicate previously returned True for any non-automation dict, including empty dicts or dicts without an authoring surface. Tightened to require at least one of `goal`, `write_file`, `steps`, `file_organize`, `mission_id`, or `enqueue_child` so phantom/malformed dicts never trigger revision-lane protections.
- **Tests added** (`tests/test_vera_preview_stabilization.py`, now 81 total, up from 60):
  - 6 new integration tests proving the new gate protects against hijack scenarios: revise-from-evidence, save-follow-up (ambiguous phrase), make-it-a-follow-up-script, investigation-save, save-the-follow-up-as-a-file, update-that-based-on-the-result.
  - 5 new unit tests on `dispatch_early_exit_intent` directly (`TestEarlyExitRevisionFlag`) proving the flag gates the follow-up branch, but not the review/time/near-miss branches.
  - 5 new parametrized tests for follow-up-script phrasing coverage (`TestFollowUpScriptPhrasing`).
  - 3 new tests tightening `is_normal_preview` coverage (empty dict, no-authoring-surface dict, mission preview).
  - 2 new regression tests for non-mutating branches still firing with an active preview (`test_time_question_still_fires_with_active_preview`, `test_early_exit_follow_up_still_works_without_active_preview`).
- **Docs updated**: expanded the "Preview ownership and routing lane precedence" section in `05_VERA_CONTROL_LAYER_AND_HANDOFF.md` to document the threaded `active_preview_revision_in_flight` parameter and the belt-and-suspenders layer. Added a "Fail-closed rationale" section at the top of `src/voxera/vera_web/preview_routing.py` explaining when ambiguous phrases prefer the revision interpretation.
- **Validation run**: `ruff format --check .`, `ruff check .`, `mypy src/voxera` — all clean. Full `pytest` (3210 passed / 2 skipped), `make golden-check`, `make security-check`, `make validation-check`, `make merge-readiness-check` — all green.
- **Non-goals for the review pass**: no new Vera features, no new automation runtime features, no routing architecture rewrite. The revision gate is still deliberately narrow for the lifecycle lane; the belt-and-suspenders layer only broadens it at the specific early-exit branches that had hijack risk.
- **Files touched**: `src/voxera/vera_web/chat_early_exit_dispatch.py`, `src/voxera/vera_web/app.py`, `src/voxera/vera_web/preview_routing.py`, `tests/test_vera_preview_stabilization.py`, `docs/05_VERA_CONTROL_LAYER_AND_HANDOFF.md`, `docs/CODEX_MEMORY.md`.

## 2026-04-11 — refactor(vera): stabilize preview routing, state ownership, and follow-up semantics

- **Motivation**: live product use showed broad Vera regressions across multiple preview behaviors (creation, content handling, follow-up mutations, follow-up script creation, context continuity, accidental lane collisions). Root cause: preview-state mutation ownership had drifted — too many inline call sites in `src/voxera/vera_web/app.py` performed `write_session_preview` + `write_session_handoff_state` + `context_on_preview_created` in subtly different orders, with subtly different draft-ref derivations. Automation lifecycle and follow-up lanes could occasionally hijack turns that were clearly mutating an active normal preview.
- **Scope**: stabilization refactor, not a feature PR. Narrow the set of modules that may mutate Vera's active preview, and make the top-level dispatch lane precedence legible. No new Vera features, no new automation runtime features, no widened capability scope, no weakening of the truthfulness / queue / evidence boundaries.
- **New module — `src/voxera/vera/preview_ownership.py`**: centralizes every transition into the session's active preview slot. Exposes `reset_active_preview` (create/revise/replace), `record_followup_preview` (evidence-driven follow-up), `clear_active_preview` (guardrail cleanup), `record_submit_success` (post-submit clear), and `derive_preview_draft_ref` (shared draft-ref derivation). Each helper performs the coupled writes that used to live scattered throughout `app.py` as a single atomic unit. A `mark_handoff_ready=False` switch lets the automation-preview lane skip the `preview_ready` marker since automation submit is a definition save, not a queue handoff.
- **New module — `src/voxera/vera_web/preview_routing.py`**: records the canonical routing lane precedence. `PreviewLane` enum + `canonical_preview_lane_order()` tuple + `is_active_preview_revision_turn(message, active_preview=...)` gate. The gate is a conservative pattern set — length/conciseness verbs, content/body transformations, script-language switches, filename/path changes, revise/rewrite phrasing, checklist/operator-facing transformations. Deliberately narrow so ambiguous turns fall through to later lanes (fail-closed).
- **`src/voxera/vera_web/app.py` refactor**:
  - Every preview-state mutation site now routes through `preview_ownership` helpers. Removed 5 scattered `write_session_preview` + `write_session_handoff_state` + `context_on_preview_created` triples (early-exit dispatch, post-clarification code shell, automation clarification shell, direct automation shell, deterministic builder path, rename fallback, draft content binding).
  - Automation lifecycle lane now also steps aside when `is_active_preview_revision_turn` is true for a normal active preview — this closes the class of bugs where lifecycle phrasing could hijack a clearly-in-flight revision turn. Automation preview revisions (which have their own lane) are still protected by the existing `is_automation_preview` check.
  - Added a docstring header to `chat()` that enumerates the canonical lane order and points at `preview_routing.canonical_preview_lane_order()` so the two surfaces stay aligned. A cheap sanity `assert` fires immediately if someone reorders lanes without updating the enum.
  - Automation preview create/revise now passes `mark_handoff_ready=False` because automation submit saves a definition, not a queue job, and should not leak a stale `preview_ready` marker into the handoff state.
- **Regression coverage added — `tests/test_vera_preview_stabilization.py`** (60 tests): unit coverage of the `preview_ownership` helpers (install payload, mark/skip handoff ready, refresh context draft-ref, record follow-up source job, clear, submit-success, derive draft-ref); unit coverage of the `preview_routing` lane enum, lane order monotonicity, `is_normal_preview`, and the revision-turn gate (24 positive phrases + 11 negative phrases); integration tests proving that (1) the lifecycle lane does not hijack active preview revision turns, (2) the revision gate does not block unrelated turns, (3) an end-to-end "create writing preview, then 'make it longer'" ladder mutates the same preview in place, (4) `/clear` resets continuity refs, (5) ambiguous change requests still fail closed, (6) review hint phrases do not wipe the active preview.
- **Tests run**: full suite (`pytest tests/ --ignore=tests/test_golden_surfaces.py --ignore=tests/golden`) → 3189 passed, 2 skipped. No existing tests broken by the refactor.
- **Architectural rules preserved**:
  - One authoritative active preview at a time.
  - Preview truth > queue truth > artifact/evidence truth > session context.
  - Ambiguity fails closed — the revision-turn gate is deliberately narrow and fails closed when intent is unclear.
  - Queue remains the execution boundary.
  - Automation submit still saves a definition and does not emit a queue job.
  - Save-vs-execute distinction intact.
- **Docs updated**: `docs/05_VERA_CONTROL_LAYER_AND_HANDOFF.md` (new "Preview ownership and routing lane precedence" section + extraction map rows for the two new modules), `docs/08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md` (new test file entry + change-surface map rows for preview ownership and routing lanes), `docs/CODEX_MEMORY.md`.
- **Non-goals**: no new Vera features; no new automation runtime features; no routing architecture rewrite; no redesign of the hidden compiler seam; no change to the queue boundary; no new capabilities for follow-up generation beyond restoring predictable behavior for the existing surface.
- **Files changed**: `src/voxera/vera/preview_ownership.py` (new), `src/voxera/vera_web/preview_routing.py` (new), `src/voxera/vera_web/app.py`, `tests/test_vera_preview_stabilization.py` (new), `docs/05_VERA_CONTROL_LAYER_AND_HANDOFF.md`, `docs/08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md`, `docs/CODEX_MEMORY.md`.

## 2026-04-11 — review(ai): polish time-aware context helpers and tighten time-question detection

- **Motivation**: review pass on the time-aware context PR. Found four correctness/quality issues and closed them before merge.
- **Fix 1 — `current_time_summary` boundary skew**: the helper called `datetime.now()` twice (once via `current_time_context`, once for the formatted time string), so the context and the rendered time could straddle a second/minute boundary. Now captures `now` once and reuses it.
- **Fix 2 — zero-padded day format**: `date_human` used `%d` which produces "June 05, 2025" instead of natural "June 5, 2025". Switched to `%-d` (GNU strftime, matches the existing `%-I:%M %p` convention in the same module).
- **Fix 3 — `is_time_question` false positives**: the previous patterns were substring-matched (`\b...\b`) and would hijack lifecycle/drafting phrases like "What date did you save that automation?", "Show me what time it ran", "What time did that run?", "current time since last run", "Can you tell me the time of that event?". Since time-question detection runs FIRST in early-exit dispatch, any hijack would bypass real lifecycle intent. Patterns are now end-anchored (`\s*[.?!]*\s*$`) so they only fire on complete questions, with richer positive coverage for "what's the date", "what is today's date", "tell me the time", "what day of the week is it", "what's the timezone", etc.
- **Fix 4 — prompt wording tightening**: `roles/vera.md` Time-Aware Reasoning section now frames capabilities as concrete behaviors ("every Vera conversation includes a structured time-context block", "Simple time/date/timezone questions are answered deterministically from the system clock before the LLM path runs") instead of abstract claims. Adds explicit guidance for the "no `next_run_at_ms` yet" case: describe the schedule from trigger config and state that the runner has not yet scheduled the first fire. `capabilities/output-quality-defaults.md` Time-Aware Responses now distinguishes precise seconds from approximate minutes/hours/days and explicitly forbids reconstructing run history from a schedule.
- **Tests added** (`tests/test_time_context.py`, now 96 total):
  - `TestDescribeTimestampMs.test_describe_future_timestamp_uses_time_until` — future timestamps use "in about" not "ago"
  - `TestDescribeTimestampMs.test_describe_next_run_tomorrow` — next run crossing midnight classifies as tomorrow
  - `TestDescribeTimestampMs.test_describe_last_run_yesterday` — last run crossing midnight backward classifies as yesterday
  - `TestUtcOffsetFormatting` — UTC+00:00, UTC-04:00, UTC+05:30 (India, with minutes), and single-digit day non-padding
  - `TestFormatElapsedSinceMs.test_future_timestamp_is_flagged` / `test_boundary_at_one_minute` / `test_boundary_at_one_hour`
  - `TestFormatTimeUntilMs.test_past_timestamp_is_flagged`
  - 21 new `TestTimeQuestionDetection` cases covering false-positive guards (lifecycle hijacks) and the expanded positive coverage
- **Validation**: `ruff format --check .`, `ruff check .`, `mypy` on all 5 modified source files — all clean. 86 pure unit tests pass locally; the remaining 10 tests in `tests/test_time_context.py` require the full venv (pydantic, platformdirs, httpx) and will run in CI.
- **Files touched**: `src/voxera/vera/time_context.py`, `docs/prompts/roles/vera.md`, `docs/prompts/capabilities/output-quality-defaults.md`, `tests/test_time_context.py`, `docs/CODEX_MEMORY.md`.
- **Non-goals for the review pass**: no new runtime features, no projected-next-run inference from trigger_config (would duplicate runner logic — deferred as follow-up), no geolocation, no parser rewrites.

## 2026-04-10 — feat(ai): add time-aware conversational context and refresh instruction surfaces

- **Motivation**: with automation runner and timer in place, time-sensitive conversational questions ("how long ago did that run?", "when will it fire?", "what time is it?") are much more important. The system needed to answer these questions naturally and accurately, grounded in the system clock and canonical timestamps.
- **Scope**: time-context awareness layer and AI instruction surface refresh. No new automation runtime features, no geolocation, no queue semantics changes, no new panel/CLI features.
- **New module** (`src/voxera/vera/time_context.py`): deterministic, reusable helpers for current local/UTC time, elapsed-time formatting, time-until formatting, relative-day classification (today/yesterday/tomorrow), time-question intent detection, time-question answering, and a structured time-context block for prompt injection. All helpers operate on system-local time and UTC — no geolocation or IP-based location lookup.
- **Automation lifecycle integration** (`src/voxera/vera/automation_lifecycle.py`): `handle_show` and `handle_history` now describe timestamps with both absolute and natural relative phrasing ("today at 3:15 PM (about 47 minutes ago)") instead of raw epoch-ms values.
- **Chat early exit dispatch** (`src/voxera/vera_web/chat_early_exit_dispatch.py`): time questions ("what time is it?", "what day is it?", "what timezone?") are answered deterministically from the system clock as the first early-exit check — no LLM round-trip needed.
- **Vera system prompt** (`src/voxera/vera/service.py`): `build_vera_messages` injects a structured time-context block (current local time, UTC, timezone, day-of-week, epoch) into the system message so the LLM has accurate time information for reasoning.
- **Operator assistant** (`src/voxera/operator_assistant.py`): system prompt updated with timing-answer guidance and a time-context block, so advisory responses can reason about timing questions grounded in the system clock.
- **Prompt doc updates**: `00-system-overview.md` (Time-Aware Context section), `03-runtime-technical-overview.md` (time_context.py in Vera decomposition), `roles/vera.md` (Time-Aware Reasoning section), `capabilities/output-quality-defaults.md` (Time-Aware Responses section), `capabilities/queue-lifecycle.md` (timing phrasing guidance for automation lifecycle).
- **Truthfulness rules preserved**: known exact timestamps produce exact phrasing; inferred/projected times are framed as approximations; no fabricated timestamps or execution history; timezone/system-local time is the extent of location awareness; save-vs-execute distinction maintained.
- **Tests** (`tests/test_time_context.py`): 40+ focused tests covering current time context structure, elapsed-time formatting, time-until formatting, relative-day classification, automation timing descriptions, time question detection and answers, no fabricated history when timestamps absent, prompt surfaces reflect time-aware capability, time context block for prompt injection, operator assistant includes time context, early exit dispatch handles time questions.
- **Docs updated**: `05_VERA_CONTROL_LAYER_AND_HANDOFF.md`, `08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md`, `CODEX_MEMORY.md`.
- **Files changed**: `src/voxera/vera/time_context.py` (new), `src/voxera/vera/automation_lifecycle.py`, `src/voxera/vera/service.py`, `src/voxera/vera_web/chat_early_exit_dispatch.py`, `src/voxera/operator_assistant.py`, `docs/prompts/00-system-overview.md`, `docs/prompts/03-runtime-technical-overview.md`, `docs/prompts/roles/vera.md`, `docs/prompts/capabilities/output-quality-defaults.md`, `docs/prompts/capabilities/queue-lifecycle.md`, `docs/05_VERA_CONTROL_LAYER_AND_HANDOFF.md`, `docs/08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md`, `tests/test_time_context.py` (new).

## 2026-04-10 — fix(vera-web): render markdown-style assistant formatting correctly in chat

- **Motivation**: Vera assistant responses containing markdown-style formatting (headings, bold, lists, inline code) were displayed as raw symbols (`###`, `**`, `- `, `` ` ``), making structured answers hard to read.
- **Scope**: rendering-only change in the Vera chat UI. No prompt, queue, runtime, or AI capability changes. Assistant messages only — user messages are unaffected.
- **New module** (`src/voxera/vera_web/markdown_render.py`): safe bounded markdown-to-HTML renderer. HTML-escapes the full input first (fundamental XSS protection), then converts a fixed subset of markdown patterns to safe HTML elements. Supports: headings (`#`/`##`/`###` → `h3`/`h4`/`h5`), bold (`**text**`), inline code (`` `text` ``), fenced code blocks, unordered lists (`-`/`*`), ordered lists (`1.`), blockquotes (`>`), and paragraph breaks. Returns `markupsafe.Markup` to avoid Jinja2 double-escaping.
- **Jinja2 integration** (`src/voxera/vera_web/app.py`): registered `render_markdown` template filter. Template applies it to assistant messages only via `{% if turn.role == 'assistant' %}`.
- **Client-side parity** (`templates/index.html`): mirrored the renderer in JavaScript for the polling `renderTurns` path. Assistant turns use `renderAssistantMarkdown()`, user turns continue to use plain `escapeHtml()`.
- **CSS** (`static/vera.css`): added `.text.md-rendered` styles for headings, bold, lists, inline code, fenced code blocks, blockquotes, and paragraphs. Scoped to assistant bubbles. Overrides `white-space: pre-wrap` to `normal` for rendered content.
- **Safety**: escape-first architecture ensures no raw HTML from assistant content can reach the DOM. Inline code spans are extracted before bold processing to prevent cross-pattern interference. No new dependencies — uses `markupsafe` already present via Jinja2.
- **Hardening pass**: fixed infinite-loop bug where list markers without content (`- `, `1. `) could stall rendering — removed overly-broad start-only regexes (`_UL_START_RE`, `_OL_START_RE`) and unified on full item regexes that require content after the marker. CSS hardened with explicit `white-space: pre` and `word-break: normal` on `pre` blocks (no UA-stylesheet reliance), `display: block` on `pre code`, and universal `> :first-child` / `> :last-child` margin rules for clean edge spacing.
- **Tests** (`tests/test_vera_web_markdown_render.py`): 59 focused tests covering plain text, headings, bold, inline code, unordered/ordered lists, fenced code blocks, blockquotes, paragraph breaks, XSS prevention (script tags, HTML attributes, injections in headings/bold/code/lists, HTML inside code blocks), ampersand escaping, combined realistic sample, infinite-loop edge cases (empty list markers), unsupported syntax (`####`, `---`, `[link](url)`), whitespace-only input, mixed block elements, and template-scoping verification.
- **Files changed**: `src/voxera/vera_web/markdown_render.py` (new), `src/voxera/vera_web/app.py`, `src/voxera/vera_web/templates/index.html`, `src/voxera/vera_web/static/vera.css`, `tests/test_vera_web_markdown_render.py` (new), `docs/CODEX_MEMORY.md`, `docs/08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md`.
- **Supported formatting subset** (assistant messages only): headings, bold, inline code, fenced code blocks, unordered lists, ordered lists, blockquotes, paragraph breaks. Italic, strikethrough, tables, and images are not supported.

## 2026-04-10 — refactor(ai): refresh system prompts and instruction surfaces for current capabilities and higher-quality outputs

- **Motivation**: AI instruction surfaces across VoxeraOS had drifted from the current product reality. Automation subsystem capabilities were not reflected in shared prompts. Output quality guidance was absent from the prompt composition system. Code/writing draft hints were minimal. The operator assistant system prompt lacked automation awareness and depth-responsive advisory guidance.
- **Scope**: cross-surface prompt/instruction audit and refresh — not a single-role change. Covers Vera, hidden compiler, planner, verifier, web investigator, and the operator assistant.
- **Architectural rules preserved**: preview is not execution; saving an automation definition is not running it; queue remains the execution boundary; canonical store/history/evidence truth outranks conversational guesses; prompt refresh does not cause the system to overclaim execution, state, or capabilities.
- **New capability doc** (`docs/prompts/capabilities/output-quality-defaults.md`): cross-surface output quality guidance covering code generation, long-form writing, technical explanations, preview drafting, lifecycle/status responses, and operator advisory responses. Wired to all five model roles via the prompt composition system.
- **Shared system doc updates**: `00-system-overview.md` (automation definitions as authoritative surface, automation truth model), `01-platform-boundaries.md` (automation save/execute guardrail, automation definition truth surface), `02-role-map.md` (Vera automation lifecycle, overclaim prevention), `03-runtime-technical-overview.md` (new §4b Automation Subsystem covering store, runner, timer, history, supported/unsupported triggers, critical boundaries).
- **Role doc updates**: `roles/vera.md` (automation authoring/lifecycle responsibilities, output depth honoring, "What Vera Is Not" expanded to include automation runner), `roles/planner.md` (plan quality section, artifact declaration, step concreteness), `roles/verifier.md` (automation-definition-saved vs automation-has-run distinction), `roles/web-investigator.md` (synthesis quality, source divergence).
- **Capability doc updates**: `capabilities/queue-lifecycle.md` (automation-originated jobs enter through inbox), `capabilities/artifacts-and-evidence.md` (automation definition/trigger is context not evidence).
- **Code-level surfaces**: `vera/service.py` `_CODE_DRAFT_HINT` (code completeness, error handling, conventions), `_WRITING_DRAFT_HINT` (length/depth honoring, section structure, tone respect), `operator_assistant.py` system prompt (automation awareness, precise lifecycle terms, depth-responsive advisory).
- **Prompt composition wiring** (`src/voxera/prompts.py`): `capabilities/output-quality-defaults.md` added to all five role capability tuples.
- **Tests**: 17 new tests in `tests/test_prompts.py` covering output-quality-defaults doc existence, wiring to all roles, presence in all composed prompts, automation awareness in shared prompts, unsupported features not marked active, save-vs-execute wording, plan quality guidance, non-empty structured output from all composed prompts, code/writing draft hint quality guidance, operator assistant system prompt content, planner preamble output quality inclusion, Vera decomposition module coverage in runtime overview.
- **Docs updated**: `05_VERA_CONTROL_LAYER_AND_HANDOFF.md` (AI instruction surfaces section), `08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md` (prompt surface integrity test theme, change-surface map entry), CODEX_MEMORY.md.
- **Review pass fixes**: added `vera/automation_preview.py` and `vera/automation_lifecycle.py` to the Vera decomposition list in `03-runtime-technical-overview.md` §4a (stale after PR6/PR7); added 5 additional tests for code-level inline instruction surfaces (draft hints, operator assistant prompt, planner preamble output quality, decomposition coverage).
- **Non-goals**: no new runtime features, no queue semantics changes, no new automation triggers, no routing architecture rewrite. `recurring_cron` and `watch_path` remain stored-but-not-active.

## 2026-04-10 — fix(automation): harden runner execution with single-writer lock and periodic systemd scheduling (PR8)

- **Motivation**: saved delay/recurring automations only fire if something explicitly invokes `voxera automation run-due-once`. Without a lock, concurrent invocations can race and double-submit queue jobs or corrupt definition state. This PR closes both operational gaps.
- **Architectural rule preserved**: automation remains *deferred queue submission*, not alternate execution. The periodic systemd timer invokes the existing automation runner; the runner submits queue jobs via the existing inbox path. The queue remains the execution boundary. No second execution path is introduced.
- **New module** (`src/voxera/automation/lock.py`): single-writer POSIX advisory lock using `fcntl.flock(LOCK_EX | LOCK_NB)`. Lockfile at `<queue_root>/automations/.runner.lock`, distinct from the queue daemon lock (`.daemon.lock`). Non-blocking: if the lock is already held, the caller gets an immediate `busy` status, not a hang.
- **Runner changes** (`src/voxera/automation/runner.py`): added `RunnerPassResult` dataclass and `run_due_automations_locked()` wrapper that acquires the runner lock before evaluation. Returns `status="busy"` with empty results if the lock is held, `status="ok"` with a summary message otherwise.
- **CLI changes** (`src/voxera/cli_automation.py`): `run-due-once` (without `--id`) now uses the locked wrapper. If busy, prints a `BUSY` message and exits cleanly (code 0) so the systemd timer does not treat a concurrent-skip as a failure. Single-id mode (`--id`) remains unlocked.
- **Systemd units** (`deploy/systemd/user/`):
  - `voxera-automation.service` — `Type=oneshot`, runs `voxera automation run-due-once`. Uses `%h/VoxeraOS` directly (systemd resolves `%h` to the user's home directory) so the unit is directly valid without the `make services-install` sed render step. The other three services still use `@VOXERA_PROJECT_DIR@` placeholder substitution.
  - `voxera-automation.timer` — `OnCalendar=minutely`, `Persistent=true`, `WantedBy=timers.target`.
- **Makefile**: `VOXERA_UNITS` updated to include `voxera-automation.service` and `voxera-automation.timer`.
- **Tests** (`tests/test_automation_lock.py`): lock acquisition succeeds, second concurrent attempt returns busy, release allows reacquisition, locked runner returns busy when held, locked runner submits normally when available, summary message reflects outcomes, empty queue returns ok, systemd unit files exist with correct shape/cadence.
- **Docs updated**: `01_REPOSITORY_STRUCTURE_MAP.md`, `02_CONFIGURATION_AND_RUNTIME_SURFACES.md`, `03_QUEUE_OBJECT_MODEL_AND_LIFECYCLE.md`, `docs/ops.md` — lock semantics, systemd timer/service, queue directory layout.
- **Non-goals for PR8**: no `recurring_cron` runtime, no `watch_path` runtime, no new panel/Vera features, no Vera automation parsing rewrite, no automation scheduling folded into the queue daemon.

## 2026-04-10 — feat(vera): add conversational lifecycle management for saved automation definitions (PR7)

- **Motivation**: PR6 shipped Vera automation preview drafting and submit. The next smallest useful step is letting Vera inspect and manage already-saved automation definitions conversationally — "show me that automation", "disable it", "did it run?" — so operators can manage their automations through the same chat interface they use for everything else, without switching to the CLI or panel.
- **Architectural rule preserved**: automation remains *deferred queue submission*, not alternate execution. Vera manages saved definitions through the existing durable store. Any "run it now" action uses the existing automation runner → queue path. The queue remains the execution boundary. Vera does not execute payloads directly. No second execution path is introduced.
- **New module** (`src/voxera/vera/automation_lifecycle.py`): intent classification for lifecycle commands (show, enable, disable, delete, run-now, history/status), fail-closed reference resolution (session context → explicit id → title match → single-definition fallback → clarification), action handlers that use the existing store/runner/history. All actions produce operator-friendly, truthful responses.
- **Reference resolution**: resolves natural references like "that automation", "the reminder automation", "the one you just saved" using: session-stashed active_topic, explicit id, strong title match, or single-definition fallback. Ambiguous references fail closed with a clarification listing matching candidates.
- **Lifecycle actions**: show (describe from canonical store with trigger/payload/history summary), enable/disable (persist via existing store semantics), delete (definition removed, history preserved), run-now (force via existing runner path — queue-submitting only), history (surface canonical run records, truthful when empty).
- **Context lifecycle** (`src/voxera/vera/context_lifecycle.py`): added `context_on_automation_lifecycle_action()` — tracks active_topic for the inspected/mutated automation so follow-up references resolve to it. Clears topic on delete since the referent no longer exists.
- **App routing** (`src/voxera/vera_web/app.py`): lifecycle dispatch block placed after automation preview revision but before the LLM path. Guarded so it does not hijack active automation preview revision turns. Replaces the old post-submit-only continuity block with a full lifecycle dispatch that subsumes show/did-it-run and adds enable/disable/delete/run-now/history.
- **Intent classification hardening**: tightened patterns to prevent false positives. Bare pronoun patterns (it/that/this) require end-of-sentence position. The determiner "the" requires "automation" or "definition" after it. Prevents "delete the file", "stop the daemon", "describe the weather" from falsely matching lifecycle intent.
- **Explicit handoff fix** (`POST /handoff`): the `/handoff` endpoint was unconditionally routing through the queue-submit path, which fails on automation previews with a payload-anchor validation error. Fixed to check the active preview type first — automation definition previews route through the automation save path (same as `go ahead` in `/chat`), normal action previews continue using the queue-submit path. All submit phrases (`go ahead`, `submit it`, `hand it off`, `[explicit handoff requested]`) now behave consistently regardless of how the submit is triggered.
- **Tests** (`tests/test_vera_automation_lifecycle.py`): 104 focused tests covering intent classification, reference resolution (session context, explicit id, title match, pronoun, ambiguous, stale context), show/enable/disable/delete/history/run-now handlers, ambiguous fail-closed, ordinary authoring unchanged, non-automation flows unchanged, false positive prevention, unsupported trigger kind truthfulness, full dispatch integration scenarios, context lifecycle integration, explicit handoff routing (POST /handoff saves definition, does not queue, clears preview, stashes continuity, normal previews still queue-submit).
- **Non-goals for PR7**: no `recurring_cron` runtime, no `watch_path` runtime, no panel-side authoring forms, no new automation runtime behavior, no direct execution from Vera, no second execution path.
- **Follow-up recommendations**: (a) `recurring_cron` runtime support in the automation runner; (b) panel-side definition creation form; (c) Vera bulk operations on automations; (d) cron expression display in schedule descriptions.

## 2026-04-10 — feat(vera): add conversational automation preview drafting and submit flow (PR6)

- **Motivation**: PR5 shipped the panel/CLI for automation inspection and control. The next smallest useful step is letting Vera author automation definitions conversationally — "every hour, run system_inspect" — so operators can create scheduled tasks through the same chat interface they use for everything else.
- **Architectural rule preserved**: submit saves a durable `AutomationDefinition` to the automation store. Submit does NOT emit a queue job. Execution happens only through the automation runner and queue. The queue remains the execution boundary. No second execution path is introduced.
- **New module** (`src/voxera/vera/automation_preview.py`): intent detection for schedule/deferred requests, trigger parsing (`delay`, `recurring_interval`, `once_at`), payload parsing, governed automation preview drafting, conversational revision (change trigger, rename, update content, enable/disable), submit-to-store, post-submit continuity. The `recurring_cron` and `watch_path` trigger kinds are not offered during Vera authoring because the runner does not support them yet.
- **Context lifecycle** (`src/voxera/vera/context_lifecycle.py`): added `context_on_automation_saved()` — clears preview refs and records the automation ID in shared context so follow-up references ("that automation", "what did you save?") resolve truthfully.
- **Session store** (`src/voxera/vera/session_store.py`): added `read_session_last_automation_preview` / `write_session_last_automation_preview` for post-submit continuity. Field is preserved across turn appends.
- **App routing** (`src/voxera/vera_web/app.py`): automation preview submit handler (before normal queue handoff), automation preview drafting/revision handler (deterministic, before LLM preview builder), post-submit continuity handler for "what did you save?" / "did it run?" with truthful no-execution guard.
- **Tests** (`tests/test_vera_automation_preview.py`): 73 focused tests covering intent detection, trigger parsing, payload parsing, full drafting, revision, submit-saves-definition, submit-does-not-queue, truthful acks, post-submit continuity, non-automation flows unchanged, ambiguous fail-closed, deep-copy safety, intent edge cases, revision guards.
- **Non-goals for PR6**: no `recurring_cron` runtime, no `watch_path` runtime, no panel-side authoring forms, no broad lifecycle management (disable/enable/delete from Vera), no second execution path, no direct skill execution.
- **Follow-up recommendations**: (a) `recurring_cron` runtime support in the automation runner; (b) Vera lifecycle management (disable/enable/delete existing definitions); (c) panel-side definition creation form; (d) Vera post-submit "show it" against the canonical store (rather than stashed preview).

## 2026-04-10 — feat(panel): add automation dashboard for inspection and control (PR5)

- **Motivation**: PR4 shipped the operator CLI for automation inspection and control. The next smallest useful step is a panel/web surface for the same — giving operators browser-based visibility into saved automation definitions, their history, and simple control actions (enable/disable/run-now). This is the panel counterpart to the CLI; it does not add Vera automation authoring.
- **Architectural rule preserved**: automation remains *deferred queue submission*, not alternate execution. The `run-now` panel action processes a definition through `process_automation_definition(defn, queue_root, force=True)`, which submits via `core/inbox.add_inbox_payload(..., source_lane="automation_runner")`. The panel never bypasses the queue or executes payloads directly. Enable/disable only change the `enabled` flag; no hidden side effects. Delete removes the definition file only; history records are preserved as audit trail.
- **New CLI command** (`src/voxera/cli_automation.py`): `voxera automation delete <id>` — delete the saved definition file only; history records preserved. Missing ids exit non-zero with a clean message. Symmetric with the panel delete action.
- **New panel routes** (`src/voxera/panel/routes_automations.py`):
  - `GET /automations` — list page showing saved definitions with id, title, enabled, trigger_kind, next_run_at, last_run_at, last_job_ref. Actions: view detail, enable/disable, run-now, delete.
  - `GET /automations/{id}` — detail page showing full definition fields, trigger config, payload template (pretty JSON), timestamps, last job ref, policy posture, created_from. Includes run history table (newest first) from existing history records. Actions: enable/disable, run-now, delete.
  - `POST /automations/{id}/enable` — flip `enabled=True` and persist. Already-enabled redirects with informational flash. Uses existing `save_automation_definition` with `touch_updated`.
  - `POST /automations/{id}/disable` — flip `enabled=False` and persist. Already-disabled redirects with informational flash.
  - `POST /automations/{id}/run-now` — force immediate run through the existing runner. Queue-submitting only. Disabled definitions are still rejected (runner returns `skipped`). Flash indicates outcome: `run_submitted`, `run_skipped`, or `run_error`.
  - `POST /automations/{id}/delete` — delete the saved definition file only. History records under `automations/history/` are preserved as audit trail. Missing ids redirect with `not_found` flash.
- **Panel navigation**: `home.html` `<nav class="page-nav">` now includes an `Automations` link to `/automations`, placed after `Job Browser` and before `Queue Hygiene`, matching the existing nav-bar pattern. This is the only home-template change.
- **Templates**: `automations.html` (list page), `automation_detail.html` (detail page). Server-rendered Jinja2, consistent with existing panel style. CSRF tokens in all forms. Flash messages for action feedback.
- **Security**: all mutation routes go through the existing `require_mutation_guard` (Basic auth + CSRF). GET routes are unauthenticated (read-only, consistent with other panel GET routes). Missing automation ids redirect cleanly. Malformed definitions and history files are handled gracefully (silently skipped by existing store/history helpers).
- **Route registration**: `register_automation_routes(...)` in `panel/app.py` follows the same callable-injection pattern as all other route families.
- **Files changed**: `src/voxera/panel/routes_automations.py` (new), `src/voxera/panel/templates/automations.html` (new), `src/voxera/panel/templates/automation_detail.html` (new), `src/voxera/panel/templates/home.html` (nav link added), `src/voxera/panel/app.py` (import + registration), `src/voxera/cli_automation.py` (delete command added), `tests/test_panel_automations.py` (new), `tests/test_panel_contract_snapshot.py`, `tests/test_automation_operator_cli.py` (delete tests added), `docs/01_REPOSITORY_STRUCTURE_MAP.md`, `docs/02_CONFIGURATION_AND_RUNTIME_SURFACES.md`, `docs/08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md`, `docs/ops.md`, `docs/CODEX_MEMORY.md`.
- **Tests** (`tests/test_panel_automations.py`): 38 focused panel tests. (`tests/test_automation_operator_cli.py`): 3 new CLI delete tests. Panel coverage: (0) home nav link; (1-2) list page; (3) detail page; (4-7) enable/disable; (8) history on detail; (9-12) missing id handling; (13-14) malformed file resilience; (15-17) run-now queue path; (18-20) auth guards; (21-22) flash messages; (23-24) field preservation on enable/disable; (25-26) run-now source_lane and state update; (27-29) id validation; (30) bad timestamp survival; (31) delete removes definition; (32) deleted not on list; (33) delete preserves history; (34) delete missing id; (35) delete auth required; (36) detail redirects after delete; (37) delete flash message. CLI coverage: delete removes definition, delete preserves history, delete missing id fails.
- **Non-goals for PR5**: no Vera automation authoring, no `recurring_cron` runtime, no `watch_path` runtime, no new automation daemon/service, no second execution path, no direct skill execution.
- **Follow-up recommendations**: (a) `recurring_cron` with a cron parser; (b) Vera automation authoring with natural-language definition creation; (c) automation definition creation from the panel (currently inspect/control only).

## 2026-04-10 — feat(automation): add operator CLI for automation inspection and control (PR4)

- **Motivation**: PR3 shipped recurring_interval support. The next smallest useful step is an operator CLI surface for inspecting and controlling saved automation definitions — listing definitions, viewing details, enabling/disabling, reviewing history, and triggering immediate runs through the existing runner. This gives operators visibility and control without opening a second execution path, adding panel routes, or introducing Vera authoring.
- **Architectural rule preserved**: automation remains *deferred queue submission*, not alternate execution. The `run-now` command processes a definition through the existing `process_automation_definition` runner path with `force=True`, which submits via `core/inbox.add_inbox_payload(..., source_lane="automation_runner")`. The `force` flag bypasses the due-time check and the one-shot "already fired" guard so the definition fires immediately, but the disabled and unsupported-trigger-kind guards are still enforced. The queue remains the execution boundary. `enable` / `disable` only change the `enabled` flag on the saved definition; no hidden side effects, no queue submission, no state rewriting beyond `enabled` and `updated_at_ms`.
- **`run-now` force semantics** (`src/voxera/automation/runner.py`): a bounded `force` parameter was added to `evaluate_due_automation` and `process_automation_definition`. When `force=True`: (a) disabled definitions are still rejected; (b) unsupported trigger kinds (`recurring_cron`, `watch_path`) are still rejected; (c) the due-time anchor check is bypassed; (d) the one-shot "already fired" guard is bypassed. All emit/history/state-update logic runs unchanged — one-shot definitions are disabled after firing, recurring definitions re-arm `next_run_at_ms` from the actual fire time. When `force=False` (the default, used by `run-due-once` and `run_due_automations`), behavior is completely unchanged.
- **New operator CLI commands** (`src/voxera/cli_automation.py`):
  - `voxera automation list` — list saved definitions with id, enabled, trigger_kind, next_run_at_ms, last_run_at_ms, last_job_ref. Malformed files are silently skipped (best-effort `list_automation_definitions`).
  - `voxera automation show <id>` — detailed JSON view of a single definition via `load_automation_definition`.
  - `voxera automation enable <id>` — set `enabled=True` and persist via `save_automation_definition`. No-op with a message if already enabled. Preserves all unrelated fields.
  - `voxera automation disable <id>` — set `enabled=False` and persist via `save_automation_definition`. No-op with a message if already disabled. Preserves all unrelated fields.
  - `voxera automation history <id>` — show run history records for a definition, newest first. Validates the definition exists, then delegates to `list_history_records`. Malformed history files are silently skipped.
  - `voxera automation run-now <id>` — force an immediate run of a single definition through `process_automation_definition(defn, queue_root, force=True)`. Bypasses the due-time check; disabled definitions and unsupported trigger kinds are still rejected. Submits through the canonical inbox path; the queue remains the execution boundary.
  - `voxera automation run-due-once` — existing command, unchanged. Still respects due-time evaluation.
- **New history helper** (`src/voxera/automation/history.py`): `list_history_records(queue_root, automation_id)` returns all history records for a given automation id, newest first by `triggered_at_ms`. Malformed files are silently skipped. The `automation_id` is validated against `AUTOMATION_ID_PATTERN` before the glob is constructed so traversal-looking ids are rejected fail-closed.
- **CLI help text updated**: the `automation` Typer sub-app help string now reads "Automation operator CLI. Inspect, control, and run saved automation definitions. Queue remains the execution boundary." — reflecting the expanded command family.
- **Files changed**: `src/voxera/cli_automation.py`, `src/voxera/automation/runner.py`, `src/voxera/automation/history.py`, `src/voxera/automation/__init__.py`, `tests/test_automation_operator_cli.py` (new), `tests/test_cli_contract_snapshot.py`, `tests/golden/voxera_help.txt`, `docs/01_REPOSITORY_STRUCTURE_MAP.md`, `docs/02_CONFIGURATION_AND_RUNTIME_SURFACES.md`, `docs/08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md`, `docs/09_CORE_OBJECTS_AND_SCHEMA_REFERENCE.md`, `docs/ops.md`, `docs/CODEX_MEMORY.md`.
- **Tests** (`tests/test_automation_operator_cli.py`): 35 focused tests covering: (1) `list` with no definitions shows placeholder; (2) `list` shows saved definitions with key fields; (3) `list` skips malformed definition files; (4) `show` renders correct definition; (5) `show` with missing id exits non-zero; (6) `show` with malformed file exits non-zero; (7) `enable` flips enabled to true and persists; (8) `enable` already-enabled is a no-op; (9) `enable` missing id exits non-zero; (10) `enable` preserves unrelated fields; (11) `enable` save failure exits non-zero with clean message; (12) `enable` malformed file exits non-zero; (13) `disable` flips enabled to false and persists; (14) `disable` already-disabled is a no-op; (15) `disable` missing id exits non-zero; (16) `disable` preserves unrelated fields; (17) `disable` malformed file exits non-zero; (18) `history` shows linked records; (19) `history` with no records shows placeholder; (20) `history` missing id exits non-zero; (21) `history` skips malformed history files; (22) `run-now` submits through the runner; (23) `run-now` missing id exits non-zero; (24) `run-now` does not bypass queue (inbox file has `source_lane=automation_runner`); (25) `run-now` updates definition state after fire; (26) `run-now` fires far-future `once_at` immediately; (27) `run-now` fires not-yet-due `delay` immediately; (28) `run-now` fires not-yet-due `recurring_interval` immediately and re-arms `next_run_at_ms`; (29) `run-now` still rejects disabled definitions; (30) `run-now` far-future goes through canonical queue path; (31) `run-due-once` still respects due time (regression guard); (32–35) `list_history_records` helper: returns records for id, newest first ordering, empty when no history, rejects traversal ids.
- **Non-goals for PR4**: no panel routes, no Vera automation authoring, no `recurring_cron` runtime, no `watch_path` runtime, no long-running daemon, no second execution path, no direct skill execution.
- **Follow-up recommendations**: (a) `recurring_cron` with a cron parser; (b) panel automation dashboard; (c) Vera automation authoring.

## 2026-04-10 — feat(automation): add recurring_interval trigger support to the minimal runner (PR3)

- **Motivation**: PR2 shipped a minimal runner that fires `once_at` and `delay` triggers as one-shots. The next smallest useful step is `recurring_interval` — a definition that fires repeatedly by re-arming `next_run_at_ms` after each successful queue submission, without catch-up bursts or hidden looping.
- **Architectural rule preserved**: automation remains *deferred queue submission*, not alternate execution. The runner still calls `core/inbox.add_inbox_payload(..., source_lane="automation_runner")` to submit; it never executes skills, never writes into `pending/` / `done/` / `failed/`, and never invents a second execution path. The queue remains the execution boundary.
- **Runner changes** (`src/voxera/automation/runner.py`):
  - `SUPPORTED_TRIGGER_KINDS` expanded to `{"once_at", "delay", "recurring_interval"}`.
  - New `ONE_SHOT_TRIGGER_KINDS = {"once_at", "delay"}` constant for branching one-shot vs. recurring behavior.
  - `_compute_due_anchor_ms`: for `recurring_interval`, returns `next_run_at_ms` if set, otherwise `created_at_ms + interval_ms`.
  - `evaluate_due_automation`: the "already fired" guard now only applies to one-shot kinds. A `recurring_interval` definition with `last_run_at_ms` set is still eligible if `now_ms >= next_run_at_ms`.
  - `process_automation_definition`: after a successful fire, one-shot kinds set `enabled=False` and `next_run_at_ms=None` (unchanged). `recurring_interval` keeps `enabled=True` and sets `next_run_at_ms = fired_at_ms + interval_ms`.
- **Recurring-interval semantics**: if `next_run_at_ms` is set, use it as the due anchor. Otherwise initialize from `created_at_ms + interval_ms`. After a successful fire, set `next_run_at_ms = fired_at_ms + interval_ms`. If the runner wakes up late, emit at most one queue job and schedule the next interval from the actual fire time — no catch-up bursts. Boring, deterministic, operator-friendly.
- **Error semantics**: on submit failure, definition state is unchanged (including `next_run_at_ms`), consistent with PR2 fail-closed behavior. On save failure after emit, a second error history record is written. No state advancement on failure.
- **Files changed**: `src/voxera/automation/runner.py`, `src/voxera/automation/__init__.py`, `src/voxera/automation/history.py`, `src/voxera/cli_automation.py`, `tests/test_automation_runner.py`, `tests/golden/voxera_help.txt`, `docs/01_REPOSITORY_STRUCTURE_MAP.md`, `docs/02_CONFIGURATION_AND_RUNTIME_SURFACES.md`, `docs/03_QUEUE_OBJECT_MODEL_AND_LIFECYCLE.md`, `docs/08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md`, `docs/09_CORE_OBJECTS_AND_SCHEMA_REFERENCE.md`, `docs/ops.md`, `docs/CODEX_MEMORY.md`.
- **Tests** (`tests/test_automation_runner.py`): 15 new focused tests (51 total, up from 36 before PR3): (1) `recurring_interval` not due before first interval; (2) due at `created_at_ms + interval_ms` when `next_run_at_ms` unset; (3) emits exactly one queue job; (4) state after successful fire — `enabled=True`, `last_run_at_ms` updated, `last_job_ref` updated, `run_history_refs` appended, `next_run_at_ms` re-armed; (5) no double-submit before next interval; (6) fires again after next interval elapses; (7) `mission_id` payload template works with recurring; (8) emit failure preserves state and does not advance `next_run_at_ms`; (9) `once_at` and `delay` semantics unchanged (regression); (10) late wake-up emits one job only, no catch-up burst, next_run anchored on actual fire time; (11) pre-set `next_run_at_ms` used as due anchor; (12) mixed inventory pass with recurring alongside one-shot and unsupported kinds; (13) disabled `recurring_interval` is skipped; (14) save failure after emit for recurring preserves `enabled=True` and does not advance `next_run_at_ms`; existing unsupported-trigger-kind tests updated to only cover `recurring_cron` and `watch_path`.
- **Non-goals for PR3**: no `recurring_cron` support, no `watch_path` runtime, no panel routes, no Vera automation authoring, no background daemon, no catch-up burst logic.
- **Follow-up recommendations**: (a) `recurring_cron` with a cron parser; (b) operator `voxera automation list` / `voxera automation disable` CLI surface; (c) panel/Vera authoring.

## 2026-04-09 — fix(automation): broaden add_inbox_payload to accept every canonical request anchor (PR2 live-validation bug)

- **Motivation**: live local validation on the PR2 branch found that a valid automation definition with `payload_template={"mission_id": "system_inspect"}` failed through `voxera automation run-due-once --id local-once-at-test` with `failed to emit queue job: job payload requires a non-empty goal`. The automation runner was emitting via `core/inbox.add_inbox_payload`, which hard-required a non-empty `goal` field on every submission — effectively making the inbox helper goal-only, even though the queue execution layer accepts `mission_id`, `goal`, inline `steps`, `write_file`, and `file_organize` at intake (`core/queue_execution.py:270`). This contradicts PR1/PR2 intent, which is that an `AutomationDefinition.payload_template` must look like any valid canonical queue payload (the object model already validates against the full canonical set via `AUTOMATION_CANONICAL_REQUEST_FIELDS`). Before the fix, the panel's `_write_queue_job` was already bypassing `add_inbox_payload` for exactly this reason — it called `enrich_queue_job_payload` directly and wrote the file itself — which is itself an anti-pattern the PR2 runner explicitly does not want to replicate.
- **Root cause**: `core/inbox.py::add_inbox_payload` contained `payload_goal = str(payload.get("goal") or "").strip(); if not payload_goal: raise ValueError(...)`. This gate predated the broader canonical request-kind surface and never got broadened alongside `core/queue_contracts.py::detect_request_kind` or the queue execution layer.
- **Fix** (`src/voxera/core/inbox.py`): replace the goal-only gate with a canonical-anchor gate. New private helper `_canonical_request_anchor_text(payload)` returns a short identifying string — one of `mission:<id>`, `<goal text>`, `inline_steps:<n>`, `write_file:<path>`, `file_organize:<source_path>` — whichever canonical anchor the payload carries first, or `None` if none are present. `add_inbox_payload` now: (a) computes the anchor text, (b) fails closed with a precise message (`job payload requires at least one canonical request anchor (mission_id, goal, steps, write_file, or file_organize)`) when no anchor is present, (c) generates the inbox id from the anchor text instead of from `goal` specifically. The helper still enriches the payload via `enrich_queue_job_payload`, still writes exactly one atomic `inbox-<id>.json` file, and still rejects duplicates via `FileExistsError`. `add_inbox_job(queue_root, goal)` is unchanged — it still passes through `add_inbox_payload({"goal": goal})` and works exactly as before. No change to the automation runner, the panel, Vera, or the CLI — they all now share a single canonical submission helper and the runner stops needing to care which canonical family its saved definitions use.
- **Contract preserved**: queue remains the execution boundary; automation is still deferred queue submission, not alternate execution; the runner still submits through `add_inbox_payload` with the `automation_runner` source lane; no new file-drop path is introduced; no skill execution; no panel/Vera/cron/watch work. The fail-closed surface is *narrower* than before (empty/missing goal is no longer a universal rejection reason, but every other non-canonical junk payload is still rejected with a clear message).
- **Files changed**: `src/voxera/core/inbox.py`, `tests/test_automation_runner.py`, `docs/CODEX_MEMORY.md`.
- **Tests** (`tests/test_automation_runner.py`): 8 new regression cases lock in the fix. Each canonical family — `mission_id`, `goal`, `write_file`, inline `steps`, `file_organize` — has a runner-path test that (a) fires through `process_automation_definition`, (b) confirms exactly one `inbox-*.json` file is written, (c) confirms `job_intent.source_lane == "automation_runner"` so the submission went through the canonical path and not a private file drop, (d) confirms the saved template survives verbatim into the emitted payload, and (e) confirms the one-shot definition state (`enabled=False`, `last_run_at_ms`, `last_job_ref`, one `run_history_refs` entry) is advanced correctly. A deeper end-to-end test (`test_mission_id_one_shot_does_not_double_submit`) runs two consecutive `run_due_automations` passes over a `mission_id`-only definition and asserts the second pass emits nothing and writes no new history — the one-shot guard holds for non-goal payloads too. Two helper-layer tests (`test_add_inbox_payload_rejects_payload_with_no_canonical_anchor`, `test_add_inbox_payload_accepts_every_canonical_anchor`) pin the `add_inbox_payload` contract directly so a future refactor of the runner cannot mask a helper-layer regression.
- **Live-validation repro**: after the fix, the exact task scenario — `AutomationDefinition(id="local-once-at-test", trigger_kind="once_at", trigger_config={"run_at_ms": 1}, payload_template={"mission_id": "system_inspect"})` followed by `voxera automation run-due-once --id local-once-at-test` — produces `outcome=submitted`, `request_kind=mission_id`, `source_lane=automation_runner`, and a canonical `inbox-<run_id>.json` file with the normal `expected_artifacts` list.
- **Validation**: `ruff format --check .`, `ruff check .`, `mypy src/voxera`, `pytest -q` (2662 passed, 2 skipped — up from 2654 with +8 new tests), `golden-check`, `security-check`, `release-check` tests all green.
- **Follow-up recommendation**: no scope expansion needed. The next smallest PR remains what it was before this fix landed — either a recurring-interval trigger variant, a cron parser layer, or panel/Vera authoring surfaces.

## 2026-04-09 — feat(automation): add minimal runner for due queue-submission automations (PR2)

- **Motivation**: PR1 shipped the durable `AutomationDefinition` object model and file-backed storage. PR2 is the smallest correct runner on top: it consumes saved definitions and emits normal canonical queue jobs through the existing inbox submit path when they become due, without opening a second execution path around the queue. Strictly scoped to `once_at` and `delay` triggers — `recurring_interval`, `recurring_cron`, and `watch_path` are persisted but explicitly skipped by the runner. No cron parsing, no watch-path loop, no panel routes, no Vera authoring, no daemon.
- **Architectural rule preserved**: automation remains *deferred queue submission*, not alternate execution. The runner calls `core/inbox.add_inbox_payload(..., source_lane="automation_runner")` to submit; it never executes skills, never writes into `pending/` / `done/` / `failed/`, and never invents a second execution schema. The queue remains the execution boundary.
- **New modules** (`src/voxera/automation/`):
  - `runner.py` — library-first surface: `evaluate_due_automation(definition, *, now_ms)` (pure decision helper; returns `(due, reason)`), `process_automation_definition(definition, queue_root, *, now_ms)` (emits one inbox job, writes one history record, saves updated definition on submit), `run_automation_once(automation_id, queue_root, *, now_ms)` (load-one convenience), `run_due_automations(queue_root, *, now_ms)` (inventory walk, best-effort over malformed files). Supported kinds: `SUPPORTED_TRIGGER_KINDS = {"once_at", "delay"}`. Due semantics: `once_at` is due at `now_ms >= trigger_config.run_at_ms`; `delay` is due at `now_ms >= created_at_ms + trigger_config.delay_ms`. Both are one-shot in PR2: on successful submit the saved definition is updated with `enabled=False`, `last_run_at_ms`, `last_job_ref` (the `inbox-*.json` filename), an appended `run_history_refs` entry, and `next_run_at_ms=None`. On `add_inbox_payload` failure the runner writes an `error` history record but does not advance definition state, so a transient failure cannot leave a one-shot in a half-fired limbo. If the queue emission *succeeds* but the follow-up save of the updated definition fails (e.g. disk full, permission error), the runner catches the save exception, writes a second `error` history record that references the successful `queue_job_ref` plus the save error text, and returns an `error` result — the operator then has a clear mixed-state signal to reconcile, and the runner never silently propagates the raw save exception. The inbox `job_id` is pinned to the runner's `run_id` (`<epoch_ms>-<sha1[:8]>`) so two definitions emitting identical payload templates in the same millisecond cannot collide on the default goal-hash-based inbox id.
  - `history.py` — durable run-history records: `AUTOMATION_HISTORY_SCHEMA_VERSION=1`, `generate_run_id(automation_id, *, now_ms)` (mirrors `core/inbox.generate_inbox_id` style: `<epoch_ms>-<sha1[:8]>`), `build_history_record(...)`, `write_history_record(queue_root, record)` (atomic `.json.tmp` → `Path.replace` into `<queue_root>/automations/history/`), `history_record_ref(automation_id, run_id)` (the `history/auto-<automation_id>-<run_id>.json` form stored on the definition). Record shape: `automation_id`, `run_id`, `triggered_at_ms`, `trigger_kind`, `outcome` (`submitted` | `skipped` | `error`), `queue_job_ref`, `message`, `payload_summary` (short), `payload_hash` (sha256 of the canonical-serialized saved `payload_template`). History is intentionally write-once and carries a summary + hash, not a second copy of the queue payload. Both `automation_id` and `run_id` are re-validated against the shared `AUTOMATION_ID_PATTERN` from `models.py` before the history filename is constructed, so a direct caller that hand-builds a record dict cannot use a traversal-looking id to escape the history directory. Only `submitted` and `error` outcomes ever produce history rows on disk; routine `skipped` passes (non-due, disabled, unsupported kind, already-fired one-shot) do not write history so the audit directory stays signal-dense.
  - `__init__.py` — re-exports the full runner + history public surface alongside the PR1 model/store.
- **Minimal CLI entrypoint**: `src/voxera/cli_automation.py` adds `voxera automation run-due-once` with an optional `--id <automation_id>` narrower. This is the smallest operator-oriented hook needed for manual testing; there is no daemon mode, no watch, no loop. Wired from `cli.py` alongside the existing command families.
- **Contract preserved**: queue remains the execution boundary; emitted payloads are the saved `payload_template` verbatim (copied so the durable definition is never mutated by inbox enrichment); inbox intake enrichment adds the canonical `job_intent`, `id`, and `expected_artifacts` as it does for every other lane; no bypass, no second file-drop path. Fail-closed: malformed definition files on disk are skipped (best-effort `list_automation_definitions`), unsupported trigger kinds are skipped with a named reason, disabled definitions are skipped, already-fired one-shots are skipped, and any exception during emission becomes an `error` history row.
- **Files changed**: `src/voxera/automation/__init__.py`, `src/voxera/automation/runner.py` (new), `src/voxera/automation/history.py` (new), `src/voxera/cli_automation.py` (new), `src/voxera/cli.py`, `tests/test_automation_runner.py` (new), `tests/test_cli_contract_snapshot.py`, `tests/golden/voxera_help.txt`, `docs/01_REPOSITORY_STRUCTURE_MAP.md`, `docs/02_CONFIGURATION_AND_RUNTIME_SURFACES.md`, `docs/03_QUEUE_OBJECT_MODEL_AND_LIFECYCLE.md`, `docs/08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md`, `docs/09_CORE_OBJECTS_AND_SCHEMA_REFERENCE.md`, `docs/ops.md`, `docs/CODEX_MEMORY.md`.
- **Tests**: `tests/test_automation_runner.py` adds 31 focused cases covering: `evaluate_due_automation` semantics for `once_at` (not-yet-due, due-at-anchor), `delay` (anchor = `created_at + delay_ms`, not-yet-due and due-at-anchor), disabled, already-fired one-shot, and each unsupported trigger kind (`recurring_interval`, `recurring_cron`, `watch_path`) parametrized; due `once_at` emits one normal queue job with canonical `job_intent.source_lane="automation_runner"` and `id`; due `delay` emits one normal queue job; non-due definition does not emit and does not mutate on-disk state; disabled definition does not emit; unsupported trigger kinds do not emit (parametrized); history record includes queue job linkage and payload hash; updated definition fields (`enabled=False`, `last_run_at_ms`, `last_job_ref`, `run_history_refs`, `next_run_at_ms=None`) are saved; two successive `run_due_automations` passes do not double-submit; emitted payload keys (`goal`, `title`, `steps`) match the saved `payload_template` verbatim; definition ↔ history ↔ queue job ref linkage is preserved; `run_due_automations` processes every valid definition in one pass and writes one history row only for submits; malformed definition files on disk are silently skipped by the runner; `run_automation_once` loads by id and submits; `skipped` runs never write history records; `add_inbox_payload` failure is caught and recorded as an `error` history row without advancing definition state; a save-failure-after-emit scenario writes a second `error` history row that carries the successful `queue_job_ref` and leaves the stored definition unchanged so the mixed state is visible to the operator; the runner does not mutate the in-memory or on-disk `payload_template` during submission; `write_history_record` and `history_record_ref` reject traversal-looking automation/run ids; `voxera automation run-due-once` CLI entrypoint runs end-to-end and exits non-zero on a missing `--id`; `generate_run_id` output matches `AUTOMATION_ID_PATTERN` so the shared validation never rejects a runner-generated id.
- **Non-goals for PR2**: no cron parsing, no watch-path runtime, no recurring interval firing, no panel routes, no Vera automation authoring, no background daemon, no retries, no approvals coupling (approvals/policy/artifacts all happen through the normal queue lifecycle once the inbox job is in place).
- **Validation**: `ruff format --check .`, `ruff check .`, `mypy src/voxera`, `pytest -q`, `make golden-check`, `make security-check`, `make validation-check`, `make merge-readiness-check`.
- **Import guidance**: operator-facing library entrypoints are `voxera.automation.run_due_automations` and `voxera.automation.run_automation_once`. CLI operators use `voxera automation run-due-once`. Direct low-level hooks (`process_automation_definition`, `evaluate_due_automation`, `build_history_record`) are also re-exported from the `voxera.automation` package for tests and tooling.
- **Recommended next PR**: either (a) a recurring-interval trigger variant that re-arms `next_run_at_ms` instead of one-shotting, or (b) a cron parser + scheduler layer, or (c) panel/Vera authoring surfaces — each a separate, bounded PR so the queue remains the only visible execution path in the diff.

## 2026-04-08 — feat(automation): add canonical automation object model and storage (PR1)

- **Motivation**: VoxeraOS needs a governed way for Vera or an operator to author an automation definition that later causes a normal queue job to be submitted, without opening a second execution path around the queue. PR1 establishes the durable object model and storage layer only — no runner, no scheduler, no submitter. Everything still flows through `inbox/` when a future PR2 wires a runner on top.
- **Design intent**: An `AutomationDefinition` describes *deferred or triggered queue submission*. Its `payload_template` must look like a normal canonical queue payload (anchored on `mission_id`, `goal`, `steps`, `file_organize`, or `write_file`), so automation can never drift into an automation-only execution schema. Unknown trigger kinds, malformed trigger config, and payload templates that do not look like canonical queue requests all fail closed at validation time.
- **New package** (`src/voxera/automation/`):
  - `models.py` — `AutomationDefinition` Pydantic model with `extra="forbid"`; id validation anchored on a module-level `AUTOMATION_ID_PATTERN` (`^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$`) so the model and the store enforce the same legal-id set; supported trigger kinds `once_at` / `delay` / `recurring_interval` / `recurring_cron` / `watch_path`; per-kind `trigger_config` validator that rejects unknown keys and enforces strict positive ints (`bool` and `float` are rejected explicitly via `_is_strict_positive_int`, which matters because `True` is otherwise a legal `int` in Python); payload-template gating that delegates to the existing `extract_write_file_request` / `extract_file_organize_request` helpers from `core/queue_contracts.py`; `policy_posture` (`standard` | `strict_review`); `created_from` (`vera` | `panel` | `cli`); `last_job_ref` must be a non-empty string when set; `run_history_refs` items must be non-empty strings; description is stripped so round-trip saves do not flip between `"  "` and `""`; and a `touch_updated()` helper that returns a copy with a refreshed `updated_at_ms` clamped to `max(now, created_at_ms, current updated_at_ms)` — clock skew cannot regress an update time.
  - `store.py` — file-backed CRUD: `ensure_automation_dirs`, `save_automation_definition` (atomic `.json.tmp` → `Path.replace`, deterministic sorted JSON), `load_automation_definition`, `list_automation_definitions` (best-effort by default — malformed files are skipped so one bad file cannot hide the rest of the inventory; `strict=True` is available for tooling), `delete_automation_definition` (`missing_ok` option), plus path helpers and custom `AutomationStoreError` / `AutomationNotFoundError` exceptions. `definition_path` runs the caller-provided id through `AUTOMATION_ID_PATTERN` before joining it to the definitions directory, so traversal segments, null bytes, leading dots, whitespace, and path separators are all rejected fail-closed before the filesystem is touched. `list_*` only globs `*.json`, so a `.json.tmp` sidecar left behind by an interrupted save is naturally ignored.
  - `__init__.py` — re-exports the public surface including `AUTOMATION_ID_PATTERN`. No CLI command family was added; PR1 is a library-only surface until a runner exists.
- **Storage layout**: `<queue_root>/automations/definitions/<id>.json` (one file per definition) plus a sibling `history/` directory created now but not written in PR1 — it is reserved for the future runner's run history. Both live under the existing `~/VoxeraOS/notes/queue` queue root so everything stays in one audit-visible location, matching the repo's queue-root-centered storage pattern.
- **Contract preserved**: queue remains the execution boundary; no code in this PR submits anything to `inbox/`, executes a skill, or spins a daemon thread. `payload_template` validation is intentionally routed through the same extractors the queue daemon uses at intake, so an automation definition that validates here would also survive the intake contract if a future runner emitted it verbatim.
- **Files changed**: `src/voxera/automation/__init__.py`, `src/voxera/automation/models.py`, `src/voxera/automation/store.py`, `tests/test_automation_object_model.py`, `docs/01_REPOSITORY_STRUCTURE_MAP.md`, `docs/03_QUEUE_OBJECT_MODEL_AND_LIFECYCLE.md`, `docs/08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md`, `docs/09_CORE_OBJECTS_AND_SCHEMA_REFERENCE.md`, `docs/CODEX_MEMORY.md`.
- **Tests**: `tests/test_automation_object_model.py` adds 52 focused cases covering: valid round-trip save/load; listing; disabled/enabled round-trip; every supported trigger kind; unknown trigger kind rejected; per-kind parametrized malformed `trigger_config` (missing key, zero, negative, wrong type, extra key); explicit rejection of `bool` and `float` in numeric trigger-config fields; `watch_path` `event` defaulting to `"created"` when omitted; empty / non-canonical / malformed `payload_template` rejections; `write_file` and `file_organize` templates delegating to the queue contract extractors; bad id shapes rejected; `AUTOMATION_ID_PATTERN` positive/negative coverage; description stripping; `last_job_ref` empty-string rejection; `run_history_refs` empty-item rejection; `updated_at_ms < created_at_ms` rejected; `touch_updated` copy-semantics, forward advance, created-at clamp, non-regression past current `updated_at_ms` under clock skew, and rejection of non-positive-int `now_ms`; deterministic sorted-JSON on-disk format; atomic save refreshing `updated_at_ms` by default; import-friendly `touch_updated=False` path; wall-clock refresh when `now_ms` omitted; idempotent save overwriting an existing id; best-effort list skipping broken JSON / wrong-shape files; `.json.tmp` sidecar ignored even in `strict=True` mode; `strict=True` raising on broken JSON; `load_automation_definition` raising `AutomationNotFoundError` vs. `AutomationStoreError`; `delete_automation_definition` with and without `missing_ok`; traversal-looking id rejection at the store boundary (including null byte, newline, leading dot, leading `-` / `_`); `definition_path` accepting valid ids; `automations_root` / `definitions_dir` / `history_dir` layout anchoring; and `load_automation_definition` rejecting bad id shapes before it touches the filesystem.
- **Non-goals for PR1**: no automation daemon, no queue submission, no panel routes, no Vera authoring, no CLI command family. A future PR can layer a runner that reads these definitions and emits normal queue jobs; it must not bypass the queue.
- **Validation**: `ruff format --check .`, `ruff check .`, `mypy src/voxera`, `pytest -q`, `make golden-check`, `make security-check`, `make merge-readiness-check`.
- **Import guidance**: consumers should import from `voxera.automation` directly (`AutomationDefinition`, `AUTOMATION_ID_PATTERN`, `save_automation_definition`, `load_automation_definition`, `list_automation_definitions`, `delete_automation_definition`, `ensure_automation_dirs`, `AutomationStoreError`, `AutomationNotFoundError`). `models._validate_trigger_config` and `models._validate_payload_template` are private helpers and should stay that way — the public validation contract is the Pydantic model itself.
- **Recommended next PR**: a minimal, read-only automation runner that consumes `once_at` / `delay` definitions, emits a normal canonical queue payload into `inbox/` via `add_inbox_payload`, and appends a `history/` record. Anything beyond that (recurring schedules, cron parsing, watch-path event loops, panel routes, Vera authoring) should be further PRs so the execution boundary stays visible in the diff.

## 2026-04-06 — polish(vera): reduce first-turn over-conservative refusal for previewable automation and script requests

- **Motivation**: After PR #296 fixed the broken clarification → preview seam, live testing still showed an occasional first-turn failure pattern. Clearly previewable automation/process requests phrased without explicit path tokens (e.g. "I need a process that identifies a new folder copied into a specific folder and it does something with it and then it copies it to another folder can you help me?") could still produce the blanket "I was not able to prepare a governed preview for this request" reply on the very first turn — too aggressive for a request class Vera can clearly handle.
- **Root cause**: When the LLM produced a preview-pane claim with no fenced code block, `guardrail_false_preview_claim` collapsed the reply to the blanket refusal. There was no narrower routing seam to substitute a focused clarification question for previewable automation requests, so the user got the refusal even though the right first-turn behavior was either to clarify or to draft. The existing `_looks_like_direct_automation_request` detector required all four signals (including an explicit path token), so naturally phrased first-turn requests were not eligible for direct draft.
- **Fix** (`vera_web/app.py`, `vera_web/response_shaping.py`):
  - Extracted the blanket refusal text into `BLANKET_PREVIEW_REFUSAL_TEXT` in `response_shaping.py` so the call site and tests can refer to one source of truth.
  - Added `_PREVIEWABLE_AUTOMATION_INTENT_RE`, `_PREVIEWABLE_AUTOMATION_SUBJECT_RE`, and `_PREVIEWABLE_AUTOMATION_ACTION_HINT_RE` plus `_looks_like_previewable_automation_intent()` — a structural detector that requires an automation intent verb (process/automation/script/workflow/monitor/watch/detect/identify/poll), a file/folder/directory subject, and an action-on-arrival/source-destination hint. This is broader than `_looks_like_direct_automation_request` (no explicit path token required) but still narrow enough to fail closed on weather, writing, simple file ops, and informational queries.
  - Added `_PREVIEWABLE_AUTOMATION_CLARIFICATION_REPLY` — a focused clarification question asking for source folder, destination folder, and action-on-arrival.
  - Wired into `chat()` immediately after `guardrail_false_preview_claim`: when the guardrail collapsed the reply to `BLANKET_PREVIEW_REFUSAL_TEXT`, no preview is active, and the user message matches the previewable automation intent detector, the visible reply is replaced with the clarification question. No preview is materialized; the trust model is unchanged.
- **Contract preserved**: queue boundary unchanged; preview truth unchanged; no fake preview claims; fail-closed for genuinely unsupported requests (the substitution requires three structural signals); does not hijack weather/writing/simple file requests; does not interact with active previews. The change is bounded to first-turn refusal rerouting only.
- **Files changed**: `src/voxera/vera_web/app.py`, `src/voxera/vera_web/response_shaping.py`, `tests/test_vera_preview_materialization.py`, `docs/ARCHITECTURE.md`, `docs/CODEX_MEMORY.md`.
- **Tests**: 7 new test functions in `tests/test_vera_preview_materialization.py` (26 cases via parametrize): 2 parametrized unit methods on `TestLooksLikePreviewableAutomationIntent` covering 5 positive and 16 negative cases for `_looks_like_previewable_automation_intent`; integration test for the first-turn previewable automation no longer hitting the blanket refusal; integration test for the first-turn fully specified direct draft path remaining unchanged; 3 regression tests verifying weather, writing, and simple file ops are not hijacked.
- **Validation**: `ruff format --check .`, `ruff check .`, `mypy src/voxera`, `pytest -q`, `make merge-readiness-check`, `make golden-check` — all pass (2571 passed, 2 skipped).
- **Import guidance**: detector and clarification reply are app-internal helpers in `vera_web/app.py`. Only `BLANKET_PREVIEW_REFUSAL_TEXT` is a new public symbol in `vera_web/response_shaping.py` (factored out to share between the guardrail and the substitution call site).
- **Recommended next PR**: improve script fidelity for the resulting governed previews (this PR is about routing quality only), or generalize the same clarification substitution pattern for writing-draft refusals if that turn class shows the same first-turn over-conservative pattern.

## 2026-04-06 — hotfix(vera): materialize governed preview for direct fully specified automation/script requests

- **Motivation**: Live validation of PR #296 found that direct, fully specified automation requests still failed. The prompt "I need a process that continuously watches ./incoming. When a folder is fully copied in, add a status.txt file containing processed! and then move it to ./processed." produced "I was not able to prepare a governed preview for this request" repeatedly, even though the request has all the needed detail (automation intent, source path, destination path, action, concrete artifact). Both prior recovery paths required a clarification exchange or a matching `is_code_draft_request` on a prior turn — neither fires for a single-turn direct request.
- **Root cause**: `is_code_draft_request()` requires an explicit language keyword (e.g. "python") or a code filename. Direct automation phrasing using "process/workflow/watch/monitor" with relative paths and action verbs did not match, so no preview shell was ever created on Turn 1. The `_detect_automation_clarification_completion` helper required a prior assistant clarification question, which does not exist on Turn 1 of a direct request.
- **Hotfix** (`vera_web/app.py`):
  - Added `_DIRECT_AUTOMATION_VERB_RE` matching watch/monitor/detect/automate/poll (and tense forms).
  - Added `_DIRECT_AUTOMATION_PATH_TOKEN_RE` matching `~/X`, `./X`, or `/X` path tokens.
  - Added `_DIRECT_AUTOMATION_ACTION_RE` matching add/write/move/copy/create/append/rename/delete (and tense forms).
  - Added `_DIRECT_AUTOMATION_SUBJECT_RE` matching folder/directory/file/path (and plurals).
  - Added `_looks_like_direct_automation_request()` requiring **all four** structural signals present — narrow by design: simple file ops ("move ./a.txt to ./b.txt"), informational queries, writing drafts, and weather questions do not match.
  - Added `_synthesize_direct_automation_preview()` that returns an empty Python-script preview shell (`~/VoxeraOS/notes/automation.py`).
  - Wired into `chat()` after the clarification-completion path. Same false-positive guards as the prior hotfix (`is_info_query`, `is_explicit_writing_transform`, `conversational_answer_first_turn`, `_is_voxera_control_turn`), minus `_looks_like_new_unrelated_query` because the four-signal requirement is stricter than the question-word heuristic.
- **Contract preserved**: real preview materializes when a direct request has all four signals; fail-closed when any signal is missing; no fake preview claims; no truth-boundary drift; queue remains the execution boundary; preview truth unchanged.
- **Files changed**: `src/voxera/vera_web/app.py`, `tests/test_vera_preview_materialization.py`, `docs/CODEX_MEMORY.md`. Hotfix applied directly to the PR #296 branch (`claude/fix-vera-preview-materialization-YACSt`); no new branch, no new PR.
- **Tests**: 29 new tests added to `tests/test_vera_preview_materialization.py` (81 total in this file): 5 positive + 13 negative parametrized unit tests for `_looks_like_direct_automation_request`, integration test for the exact failing prompt creating a real preview, `go ahead` after direct automation, 3 regression tests (weather/simple file op/writing draft not hijacked), repeated-failure regression test.
- **Validation**: `ruff format --check .`, `ruff check .`, `mypy src/voxera`, `pytest -q`, `golden-check` — all pass (2542 passed, 2 skipped). No runtime behavior change to unrelated flows.
- **Import guidance**: direct automation detection is internal to `app.py`; no new public exports. `_looks_like_direct_automation_request`, `_synthesize_direct_automation_preview`, and the four supporting regexes are app-internal helpers.
- **Recommended next PR**: extend reliability for writing-draft clarifications; consider promoting clarification/direct-request detection into a small reusable module if this pattern recurs; explore LLM-side preview builder improvements that use conversation history so deterministic detectors are not needed per lane.

## 2026-04-06 — hotfix(vera): extend clarification-complete preview materialization to broader automation/process requests

- **Motivation**: Live validation of PR #296 found that the narrower code-draft clarification recovery improved some paths, but the broader process/automation flow still failed. When users phrased automation requests as "I want a process that detects a new folder copied into a workspace location and moves it elsewhere" (no language keyword, no code filename), `is_code_draft_request()` returned False, no preview shell was ever created, and after a clarification answer Vera failed with "I was not able to prepare a governed preview for this request."
- **Root cause**: The previous fix only handled cases where Turn 1 created an empty code preview shell (requiring `is_code_draft_request` to match — i.e., explicit language keyword or code filename). Process/automation phrasing using "process", "workflow", "monitor", "watch", "detect" with "folder/directory/file" never triggered the code-draft lane, so neither `_post_clarification_code_draft` nor `_recover_code_draft_from_history` could fire.
- **Hotfix** (`vera_web/app.py`):
  - Added `_AUTOMATION_INTENT_RE`: matches automation/process phrasing (process|automation|workflow|automate|monitor|watch|detect + folder|directory|file|files|path).
  - Added `_AUTOMATION_CLARIFICATION_QUESTION_RE`: matches assistant clarification questions (`?` or source/destination/where/which folder/what should/what action/how often).
  - Added `_AUTOMATION_DETAIL_SIGNAL_RE`: matches structured clarification details (path-like tokens or `key:` / `key=` patterns for source/destination/action/trigger/etc.).
  - Added `_detect_automation_clarification_completion()`: when no preview exists, the most recent assistant turn looks like a clarification question, a recent user turn matches `_AUTOMATION_INTENT_RE`, and the current message provides specific clarification details, synthesizes a Python-script preview shell (`~/VoxeraOS/notes/automation.py`) so the standard code-draft flow can inject the actual generated code.
  - Wired into `chat()` after `_recover_code_draft_from_history`, with the same false-positive guards (`is_info_query`, `is_explicit_writing_transform`, `conversational_answer_first_turn`, `_is_voxera_control_turn`, `_looks_like_new_unrelated_query`).
- **Contract preserved**: real preview materializes when clarification is sufficient; honest fail-closed when clarification is vague or missing detail signals; no fake preview claims; no truth-boundary drift; queue remains the execution boundary; preview truth unchanged.
- **Files changed**: `src/voxera/vera_web/app.py`, `tests/test_vera_preview_materialization.py`, `docs/CODEX_MEMORY.md`. Hotfix applied directly to PR #296 branch (`claude/fix-vera-preview-materialization-YACSt`); no new branch.
- **Tests**: 12 new tests added to `tests/test_vera_preview_materialization.py` (52 total in this file): `_detect_automation_clarification_completion` unit tests (8 cases — preview-exists/no-turns/no-question/no-intent/no-details/synthesis/already-code-draft/path-token-only); integration test for the exact failing flow (process request → clarification → answer → real preview); integration test for `go ahead` after automation preview; regression test for unrelated follow-up not hijacked; regression test for vague answer fails closed.
- **Validation**: `ruff format --check .`, `ruff check .`, `mypy src/voxera`, `pytest -q`, `golden-check` — all pass (2513 passed, 2 skipped). No runtime behavior change to unrelated flows.
- **Import guidance**: automation clarification detection is internal to `app.py`; no new public exports. `_detect_automation_clarification_completion`, `_AUTOMATION_INTENT_RE`, `_AUTOMATION_CLARIFICATION_QUESTION_RE`, and `_AUTOMATION_DETAIL_SIGNAL_RE` are app-internal helpers.
- **Recommended next PR**: extend the same clarification-to-preview reliability pattern to writing-draft requests (prose/article clarifications), and consider promoting the clarification-completion detection into a small reusable helper module if the pattern recurs in other lanes.

## 2026-04-06 — fix(vera): materialize governed previews reliably after clarification-complete script and automation requests

- **Motivation**: Live testing found a real flow break in script/automation-style requests. When Vera asked a useful clarification and the user answered, Vera claimed a preview was being prepared but no real preview existed. Follow-up phrasing like "go ahead" then failed with "no preview to submit", and recovery prompts like "please prepare that script" also failed.
- **Root cause**: After a clarification exchange, the user's answer lacks explicit code-draft signals (creation verb + language keyword + subject noun). `is_code_draft_turn` was False on the answer turn, so: (1) the LLM did not receive the `_CODE_DRAFT_HINT` to generate code, and (2) the existing empty-content preview shell from the original request was either left unfilled or cleared by `should_clear_stale_preview`. The result: no preview existed when the user said "go ahead".
- **Fix** (`vera_web/app.py`):
  - Added `_is_empty_code_preview_shell()`: detects an empty-content code/script file preview shell (created when the LLM asked for clarification instead of generating code on the initial code-draft turn).
  - Added `_post_clarification_code_draft` flag: when an empty code shell exists and the current message is not itself a code-draft request, this flag re-engages the code draft flow — the LLM receives the code generation hint and the reply code is injected into the existing shell via the standard draft content binding path. Guarded against false positives: does not fire for informational queries, writing-draft requests, conversational-artifact turns, Voxera control turns, or new unrelated questions (detected by `_looks_like_new_unrelated_query`).
  - Added `_recover_code_draft_from_history()`: when no preview exists but conversation history contains a code-draft request and the current message references it (e.g. "please prepare that script"), re-creates the preview shell from the historical intent so the code draft flow can re-engage. Bounded to `_CODE_DRAFT_RECOVERY_RE` pattern (script/code/program, not generic "file") + `is_code_draft_request()` on prior turns.
  - Added `_looks_like_new_unrelated_query()`: detects messages starting with question/interrogative words (what, who, how, tell, etc.) that signal a new topic rather than a clarification answer.
  - `is_code_draft_turn` now includes `_post_clarification_code_draft` alongside existing `is_code_draft_request()` and `explicit_targeted_content_refinement`.
- **False-positive guards**: `_post_clarification_code_draft` is gated by 6 exclusion conditions to prevent hijacking informational queries, weather questions, writing drafts, checklists, submit phrases, or new unrelated questions while an empty code shell exists. `_CODE_DRAFT_RECOVERY_RE` excludes the generic "file" noun to prevent matching "create a file called notes.txt". Regression tests verify that informational and writing-draft queries are not hijacked.
- **Contract preserved**: no fake preview claims; real preview when sufficient detail exists; `go ahead` works correctly when a preview really exists; honest fail-closed behavior when preview cannot be produced; no truth-boundary drift; no routing regressions. Queue remains the execution boundary; preview truth unchanged.
- **Files changed**: `src/voxera/vera_web/app.py`, `tests/test_vera_preview_materialization.py` (new), `docs/CODEX_MEMORY.md`.
- **Tests**: 40 tests in `tests/test_vera_preview_materialization.py` covering: `_is_empty_code_preview_shell` (7 unit), `_CODE_DRAFT_RECOVERY_RE` pattern matching (14 parametrized), `_recover_code_draft_from_history` (4 unit), post-clarification creates real preview (integration), post-clarification go-ahead submits (integration), no false preview claim without real preview (integration), honest fail-closed when no code generated (integration), recovery via "please prepare that script" (integration), workspace-rooted script path (integration), informational query not hijacked by empty shell (regression), writing draft not hijacked by empty shell (regression).
- **Validation**: `ruff format --check .`, `ruff check .`, `mypy src/voxera`, `pytest -q` — all pass (2501 passed, 2 skipped). No runtime behavior change to unrelated flows.
- **Import guidance**: post-clarification code draft detection is internal to `app.py`; no new public exports. `_is_empty_code_preview_shell`, `_recover_code_draft_from_history`, and `_looks_like_new_unrelated_query` are app-internal helpers.
- **Recommended next PR**: extend the same clarification-to-preview reliability pattern to writing-draft requests (prose/article clarifications), and strengthen the preview builder LLM to use conversation history for post-clarification preview creation (reducing reliance on the deterministic fallback).

## 2026-04-06 — refactor(vera): extract linked completion delivery and autosurface subsystem from service

- **Motivation**: `vera/service.py` combined top-level conversation orchestration (LLM reply, preview builder) with the full linked-completion delivery and autosurface subsystem (~600 lines). Extracting the cohesive completion subsystem improves navigability and ownership clarity without changing behavior.
- **Extraction**: Created `src/voxera/vera/linked_completions.py` containing all linked-completion functions: `_completion_delivery_eligible`, `_is_true_terminal_completion`, `_classify_surfacing_policy`, `_normalize_result_highlights`, `_extract_step_machine_payload`, `_format_diagnostics_values`, `_format_completion_autosurface_message`, `_build_completion_notification`, `_upsert_completion_notification`, `_attempt_live_delivery`, `_is_terminal_queue_state`, `_build_completion_payload`, `ingest_linked_job_completions`, `maybe_auto_surface_linked_completion`, `maybe_deliver_linked_completion_live`, `maybe_deliver_linked_completion_live_for_job`.
- **Callers updated**: `vera_web/app.py` and `core/queue_execution.py` now import from `vera.linked_completions` directly. `service.py` re-exports the 4 public functions for backward compatibility.
- **No behavioral change**: all delivery eligibility, autosurface policy, formatting, ingestion, and live delivery logic is byte-for-byte identical. Truth boundaries, queue semantics, session semantics, and fail-closed behavior are preserved.
- **Stale imports removed**: `time`, `Path`, `lookup_job`, `resolve_structured_execution`, `extract_value_forward_text` removed from `service.py` (only used by extracted functions). `_read_json_dict` moved to the new module.
- **Files changed**: `src/voxera/vera/linked_completions.py` (new), `src/voxera/vera/service.py`, `src/voxera/vera_web/app.py`, `src/voxera/core/queue_execution.py`, `docs/ARCHITECTURE.md`, `docs/ops.md`, `docs/HOTSPOT_AUDIT_EXTRACTION_ROADMAP.md`, `docs/CODEX_MEMORY.md`.
- **Tests**: all existing tests pass unchanged — test files import `vera.service` as `vera_service` and the re-exports preserve compatibility.
- **Import guidance for new code**: linked completion delivery → `vera.linked_completions`; conversation orchestration → `vera.service`; session state → `vera.session_store`. Do not re-introduce linked completion logic in `service.py`.
- **Recommended next PR**: extract the preview builder subsystem (`generate_preview_builder_update`, `_build_preview_builder_messages`, `_extract_hidden_compiler_decision`, `_apply_preview_patch`, `HiddenCompilerDecision`) from `service.py` into a dedicated module, further reducing `service.py` to a thin orchestration root.

## 2026-04-06 — improve(vera): tighten evidence-grounded follow-up continuity

- **Motivation**: Review-to-follow-up transitions were correct but stiff — replies used operator-heavy boilerplate like "grounded in canonical evidence from", bullet-prefix evidence details, and verbose fail-closed messages. The goal is smoother, more natural follow-up continuity while preserving canonical grounding and fail-closed behavior.
- **Follow-up reply shape** (`vera_web/chat_early_exit_dispatch.py`):
  - General follow-up (3c): "I've prepared a follow-up preview grounded in canonical evidence from..." → "Here's a follow-up preview based on..." with "Review or refine it — this is preview-only" closing.
  - Revise from evidence (3a): "I've prepared a revised preview grounded in canonical evidence from..." → "Here's a revised preview based on..."
  - Save follow-up (3b): "I've prepared a saveable follow-up draft grounded in canonical evidence from..." → "Here's a saveable follow-up based on..." with "It's ready as a file draft in the preview" closing.
  - Evidence detail helper: "- Prior job outcome: succeeded — {summary}" → "Prior result: succeeded — {summary}" (no bullet prefix).
- **Fail-closed messages** (`vera_web/chat_early_exit_dispatch.py`):
  - Review missing job: shortened from 3 sentences with "canonical queue evidence" to 2 concise sentences.
  - Follow-up missing evidence: shortened from 3 sentences with "canonical evidence to ground" to 2 concise sentences.
- **Saveable follow-up content templates** (`vera/evidence_review.py`):
  - Removed operator-heavy "(Operator: describe...)" placeholders in favor of natural "Describe the follow-up action..." prompts.
  - Section headers: "Proposed next step" → "Next step", "Proposed correction" → "Correction".
  - Labels: "Result summary" → "Result", "Failure summary" → "Failure".
- **Presentation change only**: no changes to truth ownership, queue boundaries, evidence grounding logic, routing, or result surfacing. Preview truth, queue truth, and artifact/evidence truth remain distinct and authoritative. Bounded excerpting limits unchanged. Fail-closed behavior preserved — all paths that previously failed closed still fail closed.
- **Files changed**: `src/voxera/vera_web/chat_early_exit_dispatch.py`, `src/voxera/vera/evidence_review.py`, `tests/test_linked_job_review_continuation.py`, `tests/test_chat_early_exit_dispatch.py`, `tests/test_vera_web.py`, `tests/test_vera_live_path_characterization.py`, `docs/CODEX_MEMORY.md`.
- **Tests**: Updated existing assertions to match new wording. Added 12 new regression tests in `TestFollowupContinuityReplyShape`: general followup conversational shape, revise conversational shape, update-based-on-output shape, save followup shape, no redundant layering, fail-closed review no boilerplate, fail-closed followup no boilerplate, no hallucinated evidence, evidence detail for succeeded/failed jobs. Added 2 new tests in `TestSaveableFollowupContentShape`: succeeded/failed template naturalness.
- **Import guidance**: no new exports. No signature changes.
- **Recommended next PR**: consider adding "make that follow-up more operator-facing" as a recognized refinement pattern for active follow-up previews, or add disambiguation to reduce overbroad followup hint matching in fresh sessions.

## 2026-04-05 — improve(vera): condense evidence metadata in content-first review replies

- **Motivation**: Content-first review replies (from the prior PR) correctly led with canonical output content, but the secondary evidence metadata section was still verbose — multiple bullets repeating lifecycle state, approval status, artifact families, refs, evidence trace, and execution capabilities. This made conversational review replies feel operator-heavy rather than conversational.
- **Fix** (`vera/evidence_review.py`): Split `review_message()` evidence presentation into two paths:
  - **Content-first condensed** (when `value_forward_text` present): canonical content leads, followed by a single compact state line (`State: \`succeeded\` · Outcome: \`succeeded\` · Class: \`success\``) plus only important anomaly details (failure summary, capability boundary violation, artifact observation). Verbose metadata (lifecycle state, approval status, artifact families/refs, evidence trace, execution capabilities, child summary, latest summary, expected artifact lists) is omitted.
  - **Fallback verbose** (when no canonical content): unchanged from original format — full bullet-point evidence metadata preserved as honest fallback.
- **Two new helpers**: `_condensed_evidence_lines()` (compact path) and `_verbose_evidence_lines()` (extracted original verbose path). No changes to `review_message()` signature or `ReviewedJobEvidence` dataclass.
- **Presentation change only**: no changes to truth ownership, queue boundaries, evidence grounding, routing logic, or result surfacing. Preview truth, queue truth, and artifact/evidence truth remain distinct. Bounded excerpting limits unchanged.
- **Files changed**: `src/voxera/vera/evidence_review.py`, `tests/test_evidence_review.py`, `docs/CODEX_MEMORY.md`.
- **Tests**: Updated 4 existing content-first tests to match condensed output shape. Added 6 new condensed-evidence regression tests: `test_condensed_evidence_compact_state_line_format`, `test_condensed_evidence_omits_class_when_unknown`, `test_condensed_evidence_preserves_failure_and_violation_details`, `test_condensed_evidence_preserves_artifact_observation`, `test_fallback_verbose_mode_unchanged_when_no_content`, plus existing fallback/routing tests confirmed unchanged.
- **Import guidance**: no new exports. `review_message()` signature unchanged.
- **Recommended next PR**: consider condensing fallback (evidence-first) mode as well for consistency, or adding operator-facing detail-level toggle for evidence depth.

## 2026-04-05 — fix(vera): make output review content-first when canonical content is available

- **Root cause** (`vera/evidence_review.py`): `review_message()` always led with operator-oriented metadata (state, lifecycle, approval status, artifact families, evidence trace), with the actual canonical output content buried mid-message as a `- Result:` bullet. For prompts like "What was the output?", the best first answer is the actual content, not metadata.
- **Fix** (`vera/evidence_review.py`): When `value_forward_text` is present (canonical output content safely available from `result_surfacing.py`), `review_message()` now leads with the content, then shows evidence metadata under an `Evidence for \`{job_id}\`:` header. When no canonical content is available, the original evidence-first format is preserved as an honest fallback.
- **Deduplication** (`vera/evidence_review.py`): When `latest_summary` text is already contained within `value_forward_text` (happens when `result_surfacing.py` Strategy 2 uses `latest_summary` as its content source), the `- Latest summary:` bullet is suppressed to avoid near-duplicate content. When they differ, both are shown.
- **Presentation change only**: no changes to truth ownership, queue boundaries, evidence grounding, or routing logic. Preview truth, queue truth, and artifact/evidence truth remain distinct and authoritative.
- **Bounded excerpting preserved**: `result_surfacing.py` truncation limits (_MAX_TEXT_EXCERPT_CHARS=480, _MAX_LOG_LINES_SHOWN=8, _MAX_LIST_DIR_ENTRIES=12) are unchanged. Content-first presentation does not bypass bounded excerpting.
- **Files changed**: `src/voxera/vera/evidence_review.py`, `tests/test_evidence_review.py`, `docs/CODEX_MEMORY.md`.
- **Tests**: Updated 3 existing assertions (from `"- Result:"` to content-first format checks). Added 8 new content-first regression tests: `test_content_first_file_write_leads_with_written_content`, `test_content_first_does_not_hallucinate_alternate_content`, `test_content_first_fallback_when_content_unavailable`, `test_content_first_file_read_leads_with_file_content`, `test_content_first_preserves_next_step_and_compact_state`, `test_content_first_condensed_omits_latest_summary`, `test_content_first_condensed_omits_latest_summary_even_when_different`, `test_no_behavior_drift_in_linked_job_review_routing`. (Test names updated by the condensed-evidence PR to reflect new condensed behavior.)
- **Import guidance**: no new exports. `review_message()` signature unchanged.
- **Recommended next PR**: ~~condense evidence metadata section further when content-first~~ → completed (see entry above).

## 2026-04-05 — feat(vera): add session context and routing debug surfaces for seamless continuity

- Added bounded routing debug persistence in `vera/session_store.py`: `append_routing_debug_entry`, `read_session_routing_debug`, `clear_session_routing_debug`. Entries track `route_status`, `dispatch_source`, `matched_early_exit`, `turn_index`, `timestamp_ms`. History bounded to 8 entries.
- Added `session_debug_snapshot` in `vera/session_store.py`: combines existing `session_debug_info` with shared context ref values and routing debug entries into a single operator-safe snapshot.
- Instrumented all return paths in `vera_web/app.py` `chat()` to record routing debug entries before rendering: `early_exit_dispatch`, `weather_pending_lookup`, `submit_no_preview`, `submit_active_preview`, `blocked_file_intent`, `llm_orchestration`.
- Added `GET /vera/debug/session.json` JSON endpoint in `vera_web/app.py` for operator-facing session debug inspection. Read-only, does not alter session state.
- Updated HTML template `index.html` to display session context ref values (active draft, preview, submitted/completed/reviewed jobs, saved file, topic, ambiguity flags) and routing debug entries in the DEV diagnostics panel.
- Routing debug is cleared on session clear alongside context and turns.
- Routing debug field is preserved across turn appends (added to `append_session_turn` preserved keys).
- **Trust model preserved**: session context and routing debug remain continuity aids only. Preview truth, queue truth, and artifact/evidence truth remain authoritative. The debug surface reveals state, does not change truth boundaries.
- **Files changed**: `src/voxera/vera/session_store.py`, `src/voxera/vera_web/app.py`, `src/voxera/vera_web/templates/index.html`, `tests/test_session_routing_debug.py`, `docs/ARCHITECTURE.md`, `docs/ops.md`, `docs/CODEX_MEMORY.md`.
- **Tests added**: 25 tests in `tests/test_session_routing_debug.py` covering routing debug persistence, normalization, preservation across turns, session debug snapshot, JSON endpoint, chat flow integration, no-behavior-drift, and bounded output shape.
- Validation: `ruff format --check .`, `ruff check .`, `mypy src/voxera`, `pytest -q`, `make merge-readiness-check`, `make golden-check` — all pass (2428 passed, 2 skipped). No runtime behavior change.
- **Import guidance**: routing debug helpers → `vera.session_store`. Debug snapshot → `session_debug_snapshot`. JSON endpoint → `GET /vera/debug/session.json?session_id=...`.
- **Recommended next PR**: strengthen routing debug with execution-mode classification trace (which `ExecutionMode` was chosen and why) and enrich the debug surface with preview content hash for operator-safe preview identity verification.

## 2026-04-05 — docs(vera): document retained dependency-binding wrappers in app.py

- Evaluation pass confirmed all 4 dependency-binding wrappers in `vera_web/app.py` should remain.  They are intentional boundary glue, not leftover indirection.
- `execution_mode.py` is deliberately pure (stdlib-only imports); these wrappers bind concrete Vera module dependencies into its pure functions at the app boundary.
- Per-wrapper rationale:
  - `_is_voxera_control_turn`: binds 5 dependencies; lambda-wraps `maybe_draft_job_payload` to default `active_preview=None`.
  - `_is_refinable_prose_preview`: binds 1 dependency (`is_text_draft_preview`); weakest case but maintains pattern consistency and `execution_mode.py` purity.
  - `_looks_like_active_preview_content_generation_turn`: binds 2 dependencies from separate modules (`draft_revision`, `saveable_artifacts`).
  - `_classify_execution_mode`: binds 1 dependency + evaluates `is_recent_assistant_content_save_request(message)` (function→value conversion).
- Added explanatory block comment above the wrapper cluster in `app.py`.
- No code logic changes. No test changes.

## 2026-04-05 — refactor(vera): remove app.py compatibility shims and thin wrapper indirection

- Removed `inspect.signature()` compatibility shims around `generate_vera_reply` and `generate_preview_builder_update` in `vera_web/app.py`. Both function signatures are now stable (`code_draft`, `writing_draft`, `weather_context`, `recent_assistant_artifacts` all present). `app.py` now calls both functions directly with the full kwargs.
- Removed 15 pure pass-through wrapper functions in `app.py` that only forwarded to imported functions with no added logic: `_looks_like_voxera_preview_dump`, `_looks_like_preview_update_claim`, `_strip_internal_control_blocks`, `_is_governed_writing_preview`, `_is_relative_writing_refinement_request`, `_message_has_explicit_content_literal`, `_looks_like_ambiguous_active_preview_content_replacement_request`, `_extract_save_as_text_target`, `_guardrail_false_preview_claim`, `_sanitize_false_preview_claims_from_answer`, `_enforce_conversational_checklist_output`, `_is_conversational_answer_first_request`, `_is_active_preview_submit_intent`, `_is_explicit_json_content_request`, `_conversational_preview_update_message`.
- Retained 4 dependency-binding wrappers that curry module-level dependencies into the underlying functions: `_is_voxera_control_turn`, `_is_refinable_prose_preview`, `_looks_like_active_preview_content_generation_turn`, `_classify_execution_mode`. These add real partial-application logic.
- Cleaned up `_cc_`/`_em_` alias imports that only existed to support the removed wrappers. The remaining `_em_` aliases serve the kept binding wrappers.
- Removed unused `_CODE_DRAFT_HINT` / `_WRITING_DRAFT_HINT` imports (only used by the removed shims) and unused `strip_internal_control_blocks` import.
- **Hidden test coupling discovered and fixed**: The `inspect.signature()` shims were not merely dead production code — they had a hidden runtime effect on tests. When tests monkeypatched `generate_vera_reply` with simple fakes like `_fake_reply(*, turns, user_message)`, the shim detected via `inspect.signature()` that the patched fake lacked `code_draft`/`writing_draft`/`weather_context` kwargs and fell back to a minimal-kwargs call path. With the shims removed, `app.py` passes the full kwargs directly, so all test fakes must accept them. Fixed by adding `**_kw` to 152 `_fake_reply` definitions across 5 test files and 15 `_fake_builder` definitions in `test_vera_web.py`.
- **Code-draft hint test contract updated**: Two tests (`test_code_draft_hint_injected_into_user_message_for_code_draft`, `test_code_draft_hint_not_injected_for_non_code_draft`) previously asserted that the `_CODE_DRAFT_HINT` text appeared in `user_message` — this was the shim's behavior (it baked the hint into the message before calling the fake). With direct calls, `app.py` passes `code_draft=True` to `generate_vera_reply` and the hint is injected by `service.py`'s `build_vera_messages`. Updated both tests to verify `code_draft=True` is passed via kwargs instead of checking hint text in `user_message`.
- **Test patch targets updated**: `test_vera_web.py` patches `generate_preview_builder_update` instead of the removed shim; `_is_active_preview_submit_intent` tests now call `should_submit_active_preview` from `preview_submission` directly; `_looks_like_ambiguous_active_preview_content_replacement_request` tests import from `execution_mode` directly; `test_vera_session_characterization.py` and `test_file_intent.py` import from `conversational_checklist` instead of `app`.
- **Import guidance for new code**: pure predicates from `execution_mode.py` and `conversational_checklist.py` should be imported from those modules directly, not from `app.py`. Only the 4 kept dependency-binding wrappers live in `app.py`. Test fakes for `generate_vera_reply` and `generate_preview_builder_update` must accept `**_kw` (or the full kwargs) to be forward-compatible with future signature additions.
- **Files changed**: `src/voxera/vera_web/app.py`, `tests/test_vera_web.py`, `tests/test_file_intent.py`, `tests/test_vera_session_characterization.py`, `tests/test_vera_runtime_validation_fixes.py`, `tests/test_shared_session_context_integration.py`, `tests/test_vera_contextual_flows.py`, `docs/ARCHITECTURE.md`, `docs/CODEX_MEMORY.md`, `docs/HOTSPOT_AUDIT_EXTRACTION_ROADMAP.md`.
- Validation: `ruff format --check .`, `ruff check .`, `mypy src/voxera`, `pytest -q` (2403 passed, 2 skipped), `make golden-check`, `make merge-readiness-check` — all pass.
- No runtime behavior change. No architecture redesign.

## 2026-04-05 — refactor(vera): remove weather and investigation compatibility aliases from service

- Removed all weather-flow and investigation-flow compatibility aliases from `vera/service.py`. These underscore-prefixed module-level names (`_is_weather_investigation_request`, `_is_weather_question`, `_normalize_weather_location_candidate`, `_extract_weather_location_from_message`, `_extract_weather_followup_kind`, `_weather_followup_is_active`, `_weather_context_has_pending_lookup`, `_weather_context_is_waiting_for_location`, `_weather_answer_for_followup`, `_is_informational_web_query`, `_normalize_web_query`, `_build_structured_investigation_results`, `_format_web_investigation_answer`, `run_web_enrichment`) were replaced by direct use of the imported names from `weather_flow.py` and `investigation_flow.py`.
- `vera_web/app.py` now imports `is_informational_web_query` and `run_web_enrichment` from `vera.investigation_flow` and `weather_context_has_pending_lookup` from `vera.weather_flow` instead of compatibility aliases from `vera.service`.
- Test patch targets updated: `test_vera_brave_search.py` calls investigation helpers from `investigation_flow` directly and patches non-underscore names on `vera.service`; `test_vera_contextual_flows.py` patches the imported name `weather_context_has_pending_lookup` on `vera.service` instead of the old `_weather_context_has_pending_lookup` alias.
- Removed the now-unused `vera_weather_flow` import from `test_vera_contextual_flows.py`.
- **Import guidance for new code**: weather helpers → `vera.weather_flow`; investigation helpers → `vera.investigation_flow`. Tests that need to control behavior through `generate_vera_reply` should patch the imported name in `vera.service`. Tests exercising helpers directly should import from the true source module.
- No runtime behavior change. No architecture redesign.

## 2026-04-04 — refactor(vera): remove session-store re-export indirection and thin handoff facade usage

- **Production callers migrated**: `vera_web/app.py`, `panel/routes_vera.py`, and `vera_web/chat_early_exit_dispatch.py` now import session helpers directly from `vera/session_store.py` instead of through `vera/service.py`.
- **service.py internal cleanup**: internal usages of session-store functions now reference `vera_session_store.*` directly instead of module-level aliases.  Deleted 11 clearly unused re-exports that had no external callers (`_read_session_payload`, `_write_session_payload`, `_write_session_saveable_assistant_artifacts`, `_session_path`, `_read_linked_job_registry`, `_write_linked_job_registry`, `_MAX_LINKED_JOB_TRACK`, `_MAX_LINKED_COMPLETIONS`, `_MAX_LINKED_NOTIFICATIONS`, `write_session_context`, `clear_session_context`).
- **Test backward-compat block removed**: the temporary session-store re-exports in `service.py` have been removed.  All ~300 test call sites now import session helpers directly from `vera.session_store` (or via the shared `tests/vera_session_helpers.py` harness).
- **Handoff facade gutted**: `vera/handoff.py` reduced from a 50-line re-export facade to a deprecation stub.  All production callers and tests now import from the true source modules: `preview_drafting.py`, `preview_submission.py`, `investigation_derivations.py`.
- `service.py` itself now imports `drafting_guidance` / `maybe_draft_job_payload` from `preview_drafting` and `normalize_preview_payload` from `preview_submission` instead of through the handoff facade.
- Removed `tests/test_vera_handoff_compat.py` (existed solely to verify the now-removed facade re-exports).
- Migrated shared test harness `tests/vera_session_helpers.py` to import from `vera.session_store` directly as the reference pattern for future test migration.
- Updated `docs/ARCHITECTURE.md`, `docs/ops.md`, `docs/HOTSPOT_AUDIT_EXTRACTION_ROADMAP.md`, and this file to reflect the new ownership boundaries.
- **Import guidance for new code**: session state → `vera.session_store`; preview drafting → `vera.preview_drafting`; preview submission → `vera.preview_submission`; investigation derivations → `vera.investigation_derivations`.  Do not re-introduce re-exports through `handoff.py`.
- **Follow-up completed**: weather/investigation compatibility aliases removed from `service.py` (see entry below).

## 2026-04-04 — PR #284 hotfix — fix(vera): ground linked-job output review in canonical evidence

- Summary: Hotfix on PR #284 branch. After the session-context gating fix resolved
  the hijack bug, live testing revealed that completed-job output review still was not
  truth-grounded — "What was the output?" for a file-writing job fell through to the LLM,
  which fabricated different content instead of surfacing the actual written file text.
- **Root cause 1** (`vera/evidence_review.py`):
  - `_REVIEW_HINTS` was missing output-oriented phrases: `"what was the output"`,
    `"what did it output"`, `"show me the output"`, `"show the output"`. These phrases
    did not trigger the evidence-review early-exit path, so they fell through to the
    LLM which hallucinated an answer unconstrained by queue evidence.
- **Root cause 2** (`vera/result_surfacing.py`):
  - The result surfacing layer had extractors for `files.read_text`, `files.exists`,
    `files.stat`, `files.list_dir`, and various system skills — but NO extractor for
    `files.write_text`. File-writing jobs therefore produced `value_forward_text=None`,
    meaning even when review DID fire, the actual written content was absent from the
    review message, leaving the LLM to fill in prose.
- **Fix 1** (`vera/evidence_review.py`):
  - Added 4 output-oriented phrases to `_REVIEW_HINTS` so "What was the output?" and
    variants correctly dispatch to the evidence-review path.
- **Fix 2** (`vera/result_surfacing.py`):
  - Added `_extract_file_write` extractor and `_classify_file_write` classifier for
    `files.write_text` skill. Surfaces actual written content from `machine_payload.content`
    answer-first (same pattern as `files.read_text`). Falls back to path+bytes metadata
    when content is not in evidence. Failed writes return None (no false-success text).
  - Added `RESULT_CLASS_WRITTEN_CONTENT` constant.
  - Registered both in `_EXTRACTORS` and `_CLASSIFIERS` registries.
- **Trust model preserved**: Review stays evidence-grounded. Written content comes from
  canonical `machine_payload`, not from LLM generation. If content is not available in
  evidence, the fallback is honest metadata, never fabricated prose.
- **Regression tests added**:
  - `test_result_surfacing.py`: 7 new tests for `files.write_text` surfacing (actual
    content, no hallucination, classification, bytes-only fallback, empty file, truncation,
    failed step). Updated 1 existing test that was asserting `files.write_text` returns None.
  - `test_chat_early_exit_dispatch.py`: 3 new tests for "What was the output?" dispatch
    (with context → review, without context → fallthrough, "show me the output" variant).
  - `test_vera_chat_reliability.py`: Added 4 new output-oriented phrases to the
    parametrized review-phrase recognition test.

## 2026-04-04 — PR #TBD — fix(vera): gate linked-job continuation matching behind valid session context

- Summary: Fixes blocking regression from PR #283 where overbroad review/followup
  hint matching intercepted normal authored drafting requests.
- **Root cause** (`vera/evidence_review.py`):
  - `"why did"` (2-word substring) in `_REVIEW_HINTS` matched "Why did the queue cross the
    road?" inside a file-creation prompt, causing the review branch to fire and block
    normal authored-preview drafting.
  - Additional overbroad 1-word hint `"stuck"` was redundant with `"why is it stuck"`.
- **Structural fix** (`vera_web/chat_early_exit_dispatch.py`):
  - Review dispatch (step 2): hint-based matches now require a resolvable job target
    (handoff state or session context) before entering the review branch. Explicit job IDs
    in the message always enter review (fail-closed if evidence is missing).
  - Follow-up dispatch (step 3): same gating — requires resolvable job target before
    entering. Without job context, falls through to normal authored-drafting flow.
  - This prevents ALL overbroad substring matches (not just `"why did"`) from hijacking
    normal drafting when no job is in play.
- **Hint cleanup** (`vera/evidence_review.py`):
  - Removed `"why did"` (redundant with `"why did it fail"`).
  - Removed `"stuck"` (redundant with `"why is it stuck"`).
- **Behavioral change**: On a fresh session with no job context, review/followup hint
  phrases fall through to the LLM instead of returning `review_missing_job` or
  `followup_missing_evidence`. This is correct: without a job in play, these phrases
  are not genuine review requests. Explicit job IDs (e.g. `job-123.json` in the message)
  still enter review and fail closed if evidence is missing.
- **Trust model preserved**: Session context remains a continuity aid only. Review stays
  evidence-grounded. Queue remains the execution boundary. No truth-boundary drift.
- **Recommended next PR**: hint-matching disambiguation to reduce remaining false positives
  across all hint families (pre-existing patterns like `"status"`, `"what happened"`,
  `"last job"` still match broadly but are gated behind context now).

## 2026-04-03 — PR #TBD — fix(vera): preserve full authored draft body in preview content

- Summary: Fixes preview-content mismatch where authored draft body was truncated,
  heading formatting collapsed, and wrapper/trailer text leaked into `write_file.content`.
- **Root causes** (`core/writing_draft_intent.py`):
  - `_extract_prose_body` splits on `\n{2,}` only, so compact LLM output with single-newline
    heading boundaries treats the entire reply as one block — wrapper text leaks, heading
    spacing collapses, and trailing wrapper text survives.
  - LLMs sometimes produce **inline headings** where a heading marker appears mid-line after
    a sentence boundary (e.g. `...OS runtime. ### 2. Guarded Execution Lifecycle`). The
    original `_normalize_markdown_spacing` only handled headings that started a line.
  - `_WRAPPER_PREFIX_RE` did not match "Here's a short markdown note..." pattern.
  - `_looks_like_trailing_wrapper_block` did not match "I've prepared a preview" or
    "preview-only" phrases.
- **Fixes applied**:
  - `_normalize_markdown_spacing()` Phase 1a: regex-split inline headings after sentence-ending
    punctuation (`[.!?:;]`) with optional whitespace (`\s*`) onto their own lines. Handles
    both `safe. ### 1.` (space) and `safe.### 1.` (zero-space) patterns.
  - `_normalize_markdown_spacing()` Phase 1b: regex-split `##`+ headings jammed against
    preceding word with no punctuation (e.g. `metadata### 4.`). Uses `#{2,6}` to avoid
    splitting non-heading `#` uses like `C#`.
  - `_normalize_markdown_spacing()` Phase 2: inserts blank lines around heading boundaries for
    proper block-splitting by the prose-body extractor.
  - Extended `_WRAPPER_PREFIX_RE` with `here's a [short/brief] [markdown] note/draft/summary`.
  - Extended trailing wrapper phrases with "i've prepared a preview", "preview-only",
    "this is preview-only", "let me know when you'd like to send".
- **Behavioral invariant**: authored draft body in preview content must match the visible
  drafted artifact — no truncation, no collapsed heading spacing, no wrapper/trailer leakage,
  no inline heading corruption. A 5-section zero-space regression test anchors this.

## 2026-04-03 — PR #TBD — fix(vera): preserve authored preview identity during session-aware drafting follow-ups

- Summary: Fixes live regression from the session-aware authored drafting PR where
  transformation words ("more", "shorter", "formal") were extracted as filenames,
  corrupting preview path and goal identity.
- **Root causes fixed** (`vera/draft_revision.py`):
  - `make\s+that` in the rename/save-as detection regex matched transformation
    phrases like "make that more concise", entering the rename branch.
  - `extract_named_target` tail regex extracted "more" from "make that more concise"
    as a filename target → path became `~/VoxeraOS/notes/more`.
  - `content_refinement_intent` verb list did not include `turn`/`convert`/`transform`/`keep`,
    so "turn that into a checklist" and "keep the same tone" were not handled as
    content refinement against the active preview.
- **Fixes applied**:
  - Added transformation-word guard before the rename detection regex: when the
    message matches `make that` + transformation adjective, the rename block is skipped.
  - Added transformation-word rejection in `extract_named_target`: words like "more",
    "concise", "formal", "shorter" etc. are never returned as filename targets.
  - Extended `content_refinement_intent` with `turn|convert|transform|keep` verbs
    and `checklist|list|outline|tone|style|format` objects.
- **Behavioral invariant**: Transformation follow-ups preserve the active preview's
  path, goal, and file identity. Only explicit rename/save-as requests may change
  file identity. This is now regression-tested.

## 2026-04-03 — PR #TBD — feat(vera): use shared session context in authored drafting and planning workflows

- Summary: Makes authored drafting and planning flows feel naturally session-aware
  without weakening VoxeraOS truth boundaries.
- **Authored-content transformation patterns** (`vera/draft_revision.py`):
  - `refined_content_from_active_preview` now supports: concise compression, checklist/bullet-list
    conversion, operator-facing/user-facing tone shifts, and same-tone preservation.
  - These patterns work both in the active-preview refinement path and the session-context-aware
    follow-up path.
- **Session-context-aware follow-up resolution** (`vera/preview_drafting.py`):
  - `_is_session_aware_authored_followup` detects bounded transformation requests
    (concise, checklist, tone shifts, formal — not ambiguous "change it" or
    content-generation patterns like "continue that plan").
  - `_resolve_authored_followup_from_session_context` resolves against the most recent saveable
    assistant artifact when session context has `active_draft_ref`.
  - Fail-closed when no active draft ref, no artifacts, or empty artifact content.
  - `maybe_draft_job_payload` now accepts `session_context` parameter.
  - `_looks_like_contextual_refinement` extended with transformation patterns.
- **app.py wiring**: `session_context` passed to `maybe_draft_job_payload` calls in
  the deterministic fallback and rename-mutation fallback paths.
- **Trust model preserved**: Session context remains a continuity aid only. Preview truth,
  queue truth, and artifact truth remain authoritative. Ambiguous references fail closed.
- **Recommended next PR**: linked-job review/continuation evidence-grounded follow-ups,
  `active_topic` tracking for richer planning continuity.

## 2026-04-03 — PR #TBD — feat(vera): update shared session context from preview, handoff, and job completion events

- Summary: Lifecycle freshness PR that makes shared session context stay current automatically
  as Vera moves through preview creation, revision, rename/save-as, stale preview cleanup,
  explicit handoff, linked job registration, completion ingestion, review, follow-up preparation,
  revised-from-evidence, save-follow-up, and session clear.
- **New module** (`src/voxera/vera/context_lifecycle.py`):
  - Explicit, named lifecycle update functions for each workflow event.
  - `context_on_preview_created` — sets active_draft_ref and active_preview_ref.
  - `context_on_preview_cleared` — clears preview-related refs (new: previously missing).
  - `context_on_handoff_submitted` — clears preview refs, sets submitted job + file ref.
  - `context_on_completion_ingested` — sets completed job ref from actual completion.
  - `context_on_review_performed` — sets reviewed job ref. Wired into app.py for review early-exits.
  - `context_on_followup_preview_prepared` — sets preview refs and source job. Wired into app.py for follow-up early-exits.
  - `context_on_session_cleared` — resets context to empty defaults.
- **Gap fixes** (`src/voxera/vera_web/app.py`):
  - Stale preview cleanup (`should_clear_stale_preview`) now clears context refs via
    `context_on_preview_cleared` — previously left phantom refs in context.
  - Completion ingestion now tracks the actual completed job ID from the linked registry,
    not just the handoff state job_id — fixes stale completion ref when different jobs complete.
  - All inline `update_session_context` calls for preview/handoff/completion replaced with
    named lifecycle helpers for coherence and auditability.
  - `clear_session_context` import replaced by `context_on_session_cleared`.
- **Early-exit dispatch** (`src/voxera/vera_web/chat_early_exit_dispatch.py`):
  - Follow-up (3c), revised-from-evidence (3a), and save-follow-up (3b) early exits now
    include `context_updates={"last_reviewed_job_ref": evidence.job_id}` so the evidence
    source job stays fresh for later reference resolution.
- **Stale draft fail-closed** (`src/voxera/vera_web/chat_early_exit_dispatch.py`):
  - New check (10): when message contains explicit draft reference phrase ("save that draft",
    "the draft") but session context has no active draft/preview, fail closed instead of
    letting the builder silently create a preview from recent assistant content.
  - Root cause: after follow-up handoff + failed continuation job, draft refs are correctly
    cleared, but the "save that" phrase was matching `message_requests_referenced_content`
    and falling through to the builder, which created a phantom preview from stale artifacts.
- **Tests**: 30+ new tests in `test_context_lifecycle.py` covering all lifecycle helpers,
  full sequences, rename/handoff, completion/review chains, follow-up preparation, stale
  preview cleanup, session clear, fail-closed behavior, failed-followup stale-draft regression,
  and repeated flows.
  Extended `test_shared_session_context_integration.py` with lifecycle-through-web tests:
  rename→handoff, handoff→completion→review, review→resolution, fail-closed after clear,
  failed-followup draft reference fail-closed.
  9 new tests in `test_chat_early_exit_dispatch.py` for stale draft reference fail-closed.
- **Non-authored content filtering** (`src/voxera/vera/saveable_artifacts.py`):
  - `looks_like_non_authored_assistant_message` expanded with patterns for surfaced runtime
    output: file stat lines (`type=file size=...`), file existence checks, directory listings,
    evidence review messages, auto-surface completion messages, diagnostics snapshots, and
    stale-draft refusals. Prevents surfaced runtime/result content from being stored as a
    saveable assistant artifact and later reified as a preview by "save that" requests.
  - Root cause: `build_saveable_assistant_artifact` treated file stat output and evidence
    review text as authored content because no filter pattern matched them. The "save that"
    code path then picked up this stale artifact and created a phantom preview.
- **Rename-mutation fallback fix** (`src/voxera/vera_web/app.py`):
  - Moved the rename-mutation fallback out of the `if builder_preview is not None:` block so
    it fires regardless of whether the builder LLM returned a result. Previously the fallback
    only ran when the builder returned a non-None result that was then normalized to None or
    detected as a no-op. When the builder returned None outright (common for rename requests),
    the deterministic rename path was unreachable, causing "I couldn't safely apply that naming
    update" for legitimate rename/save-as on active authored previews.
- **Trust boundaries preserved**: all canonical truth precedence unchanged. Lifecycle helpers
  update continuity refs only; preview/queue/artifact truth remain authoritative.
- **Docs updated**: ARCHITECTURE.md (lifecycle update points section), QUEUE_OBJECT_MODEL.md,
  CODEX_MEMORY.md, prompt docs (runtime-technical-overview, vera role, platform-boundaries).
- **Non-goals preserved**: No cross-session memory, no fuzzy continuity intelligence,
  no redesign of app.py orchestration, no operator-console surfaces, no truth-boundary drift.

## 2026-04-03 — PR #TBD — feat(vera): resolve session-scoped references for drafts, files, and job results

- Summary: Bounded PR that builds session-scoped reference resolution on the shared session
  context foundation, making in-session continuity phrases ("that draft", "that file",
  "the result", "the follow-up") resolve safely without weakening truth boundaries.
- **New module** (`src/voxera/vera/reference_resolver.py`):
  - Bounded reference-resolution layer with four reference classes: DRAFT, FILE, JOB_RESULT, CONTINUATION.
  - Phrase → class mapping with conservative keyword matching.
  - Priority-ordered resolution per class using shared session context refs.
  - Returns `ResolvedReference` (string value + source) or `UnresolvedReference` (fail-closed).
  - `resolve_job_id_from_context()` provides job-ID fallback for early-exit dispatch.
- **Integration** (`src/voxera/vera_web/chat_early_exit_dispatch.py`):
  - `dispatch_early_exit_intent()` accepts optional `session_context` parameter.
  - Job review (check 2) and follow-up (check 3) use session context as fallback when
    handoff state has no job_id.
  - Successful reviews return `context_updates` with `last_reviewed_job_ref`.
  - `EarlyExitResult` gains a `context_updates` field for session-context write instructions.
- **Lifecycle integration** (`src/voxera/vera_web/app.py`):
  - Reads session context before early-exit dispatch and passes it through.
  - Applies `context_updates` returned by early-exit handlers.
  - File-save submissions now set `last_saved_file_ref` in session context.
  - Job reviews now set `last_reviewed_job_ref` via context_updates.
- **Trust boundaries preserved**: all existing truth precedence rules unchanged. Resolver
  returns string ref hints — callers validate against canonical preview/queue/artifact truth.
- **Tests**: 53 new tests in `test_reference_resolver.py` covering all reference classes,
  happy paths, fail-closed paths, priority ordering, truth-boundary invariants, and edge cases.
  7 new tests in `test_chat_early_exit_dispatch.py` covering session-context fallback
  resolution, context_updates propagation, and explicit-ID precedence.
- **Docs updated**: ARCHITECTURE.md, QUEUE_OBJECT_MODEL.md, ROADMAP.md, ops.md,
  CODEX_MEMORY.md, and prompt docs (system-overview, platform-boundaries, role-map,
  runtime-technical-overview, vera role, queue-object-model capability).
- **Non-goals preserved**: No cross-session memory, no speculative resolution, no truth-boundary
  drift, no redesign of app.py orchestration, no operator-console work.

## 2026-04-03 — PR #TBD — feat(vera): add shared session context model for workflow continuity

- Summary: Foundation PR that introduces the canonical shared session context model for Vera.
  Session continuity becomes an explicit product capability rather than a fragile side effect
  of ad hoc session state.
- **New model** (`src/voxera/vera/session_store.py`):
  - Bounded `shared_context` dict with explicit vocabulary: `active_draft_ref`,
    `active_preview_ref`, `last_submitted_job_ref`, `last_completed_job_ref`,
    `last_reviewed_job_ref`, `last_saved_file_ref`, `active_topic`, `ambiguity_flags`.
  - Normalized on every read/write: unknown keys dropped, missing keys filled from defaults.
  - `ambiguity_flags` bounded to 8 entries.
  - Preserved across turn appends like other session fields.
- **API surface**: `read_session_context`, `write_session_context`, `update_session_context`,
  `clear_session_context`. Update merges (not replaces) and normalizes.
- **Lifecycle integration** (`src/voxera/vera_web/app.py`):
  - Preview creation/update → sets `active_draft_ref` + `active_preview_ref`.
  - Submit/handoff → clears preview refs, records `last_submitted_job_ref`.
  - Completion ingestion → records `last_completed_job_ref`.
  - Session clear → resets shared context to empty.
- **Trust boundaries preserved**:
  - Context is a continuity aid, NOT a trust-surface replacement.
  - Preview truth, queue truth, artifact/evidence truth remain authoritative in their layers.
  - If context conflicts with canonical truth, canonical truth wins.
  - If continuity is ambiguous, fail closed.
- **Tests**: 43 new tests covering schema coherence, normalization, persistence, turn
  preservation, truth-precedence enforcement, conservative behavior on corrupt/missing data,
  and lifecycle update semantics.
- **Docs updated**: ARCHITECTURE.md (shared session context section), QUEUE_OBJECT_MODEL.md
  (additive session context note), CODEX_MEMORY.md, prompt docs (system-overview,
  platform-boundaries, vera role, queue-object-model capability).
- **Non-goals preserved**: No reference-resolution behavior, no cross-session memory, no
  preview/queue/artifact truth changes, no panel UX changes.
- **Follow-up**: Future PRs can build reference resolution ("that file", "that draft",
  "that result") and richer continuity behavior on top of this foundation.

## 2026-04-03 — PR #TBD — fix(vera): prevent internal payload leakage on authored planning requests

- Summary: Bounded bug-fix PR that prevents internal draft-compiler / structured payload
  leakage into visible assistant chat on authored planning requests (workout plans, study
  plans, routines, schedules, etc.).
- **Root cause**: Three gaps combined to produce the observed leakage:
  1. `_CONVERSATIONAL_PLANNING_RE` did not cover workout/training/fitness/study/meal/exercise
     planning requests, so they bypassed the nuclear conversational sanitizer.
  2. `guardrail_false_preview_claim` preserved ALL fenced code blocks — including those
     containing internal compiler JSON payloads (intent, reasoning, decisions, write_file).
  3. No defense-in-depth existed in GOVERNED_PREVIEW mode to strip bare internal payloads.
- **Conversational planning coverage widened** (`src/voxera/vera_web/conversational_checklist.py`):
  - Added `help\s+me\s+get\s+...\s+(?:going|started|set\s+up)` to catch "help me get X going"
    phrasing.
  - Added explicit `(?:workout|training|fitness|exercise|study|meal|revision|review)\s+
    (?:plan|routine|program|course|schedule|regimen)` to catch planning-domain requests.
  - These route to CONVERSATIONAL_ARTIFACT mode with the nuclear six-phase sanitizer.
- **Internal compiler payload stripping** (`src/voxera/vera_web/response_shaping.py`):
  - New `_looks_like_internal_compiler_payload()`: detects fenced code blocks containing
    2+ internal markers (intent, reasoning, decisions, write_file, tool, action, enqueue_child).
  - `guardrail_false_preview_claim` now filters out internal compiler payloads from preserved
    code blocks — only user-facing code is preserved.
  - New `strip_internal_compiler_leakage()`: defense-in-depth guardrail for GOVERNED_PREVIEW
    mode that strips both fenced and bare internal compiler JSON payloads.  Bare JSON
    stripping uses brace-depth tracking to handle nested objects without residue.
- **Defense-in-depth integration** (`src/voxera/vera_web/app.py`):
  - `strip_internal_compiler_leakage` applied to sanitized_answer in the GOVERNED_PREVIEW
    path before downstream guardrails run.
- **Behavioral guidance**:
  - Internal system-facing JSON / compiler payloads / tool-planning structures must NEVER
    leak into visible assistant chat as the user-facing answer.
  - If Vera understands an authored request well enough to propose a structured document,
    it must either create a proper preview or answer cleanly in prose.
  - Planning-style requests (workout plans, study plans, routines, checklists, etc.) are
    conversational artifacts unless the user has explicit save/file intent.
  - Multi-turn continuation: when the first turn is classified as CONVERSATIONAL_ARTIFACT,
    follow-up detail turns stay conversational via `prior_planning_active`.
  - Fail-closed behavior is preserved: when truly unresolved, Vera refuses cleanly without
    leaking internals.
- **Recommended live-path regression anchors** (added to existing set):
  - "can you help me get a training workout course going?" → conversational mode
  - follow-up details (bodybuilding, 3-4x/week, 1hr, sets to failure) → stays conversational
  - "I need a workout plan for building muscle" → conversational mode
  - "give me a study plan for my exams" → conversational mode
  - Internal JSON payloads in code blocks → stripped, never preserved in visible chat
- Non-goals preserved:
  - No architecture redesign or app.py orchestration changes.
  - No submit/handoff ownership changes.
  - No queue-boundary behavior changes.
  - No weakening of fail-closed behavior.
  - No broad domain-specific fitness logic.

## 2026-04-03 — PR #TBD — feat(vera): improve revise and save-follow-up workflows from completed job evidence

- Summary: Bounded workflow feature PR that makes Vera able to revise prior output and save
  evidence-grounded follow-up drafts more reliably after a completed linked job. Distinguishes
  three follow-up sub-intents (revise/update, save-follow-up, general follow-up) in the
  dispatch layer, adds specialized preview builders for each, and preserves preview-only
  semantics and fail-closed behavior.
- **New intent classifiers** (`src/voxera/vera/evidence_review.py`):
  - `is_revise_from_evidence_request()`: detects revise/update-from-evidence phrases.
  - `is_save_followup_request()`: detects save-follow-up phrases.
  - Both are strict subsets of `is_followup_preview_request()` — the superset routing
    continues to catch all follow-up intents.
- **New preview builders** (`src/voxera/vera/evidence_review.py`):
  - `draft_revised_preview()`: produces a revision-oriented goal referencing the evidence
    and naming "revise" intent explicitly so downstream preview handling and the operator
    can distinguish it from a generic follow-up.
  - `draft_saveable_followup_preview()`: produces a structured preview with `write_file`
    containing evidence-grounded markdown content (job ID, outcome summary, next-step
    template). The file path is `~/VoxeraOS/notes/followup-{job_stem}.md`.
- **Dispatch sub-intent routing** (`src/voxera/vera_web/chat_early_exit_dispatch.py`):
  - Follow-up dispatch branch (§3) now distinguishes three sub-intents:
    - §3a: Revise/update from evidence → `revised_preview_ready` status, revision-oriented goal.
    - §3b: Save follow-up → `save_followup_preview_ready` status, saveable write_file preview.
    - §3c: General follow-up → unchanged `followup_preview_ready` behavior.
  - Fail-closed behavior is shared: all three sub-intents return `followup_missing_evidence`
    when no resolvable completed job exists.
  - Assistant text for each sub-intent explicitly communicates preview-only semantics and
    the evidence source.
- **Behavioral guidance**:
  - Revise/update workflows are only correct when grounded in canonical queue evidence from
    a resolvable completed job. The revision goal must name the prior job and its evidence.
  - Save-follow-up workflows must produce a concrete saveable preview (write_file with content),
    not a bare conversational promise. The content must be evidence-grounded.
  - All three follow-up sub-intents remain preview-only unless explicitly submitted.
  - Fail-closed behavior is preserved: when no resolvable completed job exists, Vera refuses
    and tells the user what is needed.
  - Chat prose and preview payload truth must remain materially aligned: if the chat says
    "revised preview", the preview goal must say "revise"; if the chat says "saveable
    follow-up draft", the preview must contain a write_file.
- **Recommended live-path regression anchors** (added to existing set):
  - "revise that based on the result" → revised preview (or fail-closed)
  - "revise that based on the evidence" → revised preview (or fail-closed)
  - "update that based on the result" → revised preview (or fail-closed)
  - "save the follow-up" → saveable write_file preview (or fail-closed)
  - "save that follow-up" → saveable write_file preview (or fail-closed)
  - "save the follow-up as a file" → saveable write_file preview (or fail-closed)
- Non-goals preserved:
  - No architecture redesign or app.py orchestration changes.
  - No submit/handoff ownership changes.
  - No queue-boundary behavior changes.
  - No weakening of fail-closed behavior.
  - No direct execution shortcuts from chat.
  - No speculative continuation when no completed linked job exists.

## 2026-04-02 — PR #275 — test(vera): expand evidence-grounded review and follow-up live-path characterization

- Summary: Bounded stabilization/characterization PR that adds regression coverage for
  the strongest real user paths across drafting, preview-state UX, linked-job review,
  and evidence-grounded follow-up. No runtime behavior changes.
- **New test file**: `tests/test_vera_live_path_characterization.py` (43 tests) covering:
  1. Natural drafting → preview truth binding ("write me a note about..." → content reaches preview)
  2. Preview prepared / updated / unchanged wording clarity (truthful state transitions)
  3. Linked-job result review via dispatch and session (evidence-grounded, not LLM-fabricated)
  4. Evidence-grounded follow-up preview preparation (goal references prior job, preview-only)
  5. Fail-closed behavior when no resolvable completed job exists (review + follow-up)
  6. Session-level integration: full chat → dispatch → reply path for review and follow-up
- **Recommended live-path regression set** (for future Vera stabilization):
  - "write me a note about the artifact evidence model" → preview truth
  - "make it shorter and more operator-facing" → preview updated
  - "save it as artifact-evidence-operator-note.md" → path preserved, content survives
  - "submit it" → queue job, preview cleared
  - "summarize the result" → evidence-grounded review (or fail-closed)
  - "inspect output details" → evidence-grounded review
  - "now prepare the follow-up" → evidence-grounded preview (or fail-closed)
  - "what should we do next based on that" → follow-up preview
- **Why no behavior change**: All tests characterize existing behavior. No source files
  were modified. The test file asserts on truth surfaces (preview payload content, dispatch
  status, write flags, session turns) rather than incidental strings.

## 2026-04-02 — PR #TBD — improve(vera): polish linked-job review and evidence-grounded follow-up workflows

- Summary: Bounded workflow polish PR that strengthens linked-job review and follow-up
  drafting, plus a writing-draft preview truth guardrail. Widens phrase coverage for result
  inspection, follow-up generation, and revise-from-evidence flows without changing
  architecture, queue boundaries, or submission ownership.
- **Review hint coverage widened** (`src/voxera/vera/evidence_review.py`):
  - Added 12 new `_REVIEW_HINTS` phrases: "summarize the result", "summarize that result",
    "summarize the job result", "inspect output", "inspect output details", "inspect the
    output", "review the result", "review that result", "show me the result", "show the
    result", "what was the outcome", "what was the result".
  - These route to the existing evidence review dispatch, producing grounded review
    messages from canonical queue evidence.
  - Note: bare "summarize result" was intentionally excluded to avoid collisions with
    investigation summary references like "summarize result 1".
- **Follow-up hint coverage widened** (`src/voxera/vera/evidence_review.py`):
  - Added 10 new `_FOLLOWUP_HINTS` phrases for revise-from-evidence ("revise that based on
    the result", "update that based on the result", etc.) and save-follow-up ("save the
    follow-up", "save that follow-up", "save the follow-up as a file").
  - These route to the existing follow-up preview dispatch, producing preview-only drafts
    grounded in completed job evidence.
- **Follow-up preview goal text improved** (`src/voxera/vera/evidence_review.py`):
  - Succeeded jobs: goal changed from "inspect output details from {job_id}" to
    "draft a follow-up step grounded in completed evidence from {job_id}".
  - Canceled jobs: now produce "draft a replacement step for canceled job {job_id}".
- **Follow-up dispatch detail** (`src/voxera/vera_web/chat_early_exit_dispatch.py`):
  - Follow-up preview replies now include an evidence-grounded detail line showing the
    prior job outcome (state + summary), so the user sees what evidence grounds the draft.
  - Fail-closed message improved: explicitly states no completed linked job was resolved.
  - Review missing-job message improved: clarifies that canonical queue evidence is needed.
- **Behavioral guidance**:
  - Linked-job follow-up behavior is only correct when grounded in canonical queue evidence.
    Conversational fluency must not bypass the requirement for a resolvable job outcome.
  - Follow-up drafting stays preview-only unless explicitly submitted.
  - Fail-closed behavior preserved: when no resolvable completed job exists, Vera refuses
    to draft a follow-up and tells the user what is needed.
  - Review phrases that inspect results are routed to the existing review dispatch, not to
    the LLM, ensuring answers come from persisted queue evidence.
- **Writing-draft preview truth guardrail** (`src/voxera/vera_web/draft_content_binding.py`):
  - Added a post-binding safety check in `resolve_draft_content_binding`: when
    `is_writing_draft_turn` is True and the best available authored content is 8+ words,
    verify that the final `builder_payload` write_file.content is not a short fragment
    (less than half the word count of the authored text). If it is, override the builder
    content with the authored text.
  - The guardrail selects the best available content source: `reply_text_draft` first,
    then `sanitized_answer` as a fallback when text extraction missed but the LLM
    produced good content visible in chat. `sanitized_answer` is now threaded to
    `resolve_draft_content_binding` via `app.py` for this purpose.
  - Root cause: the builder LLM may produce a fragmentary content snippet (e.g.
    "what is happening now." or "hallucination of success,") from the authored text.
    The writing-draft injection should override it, but pathological LLM response
    structures can cause `reply_text_draft` to be None while the chat still shows
    good content via `sanitized_answer`. Without the fallback, the builder fragment
    survives into the authoritative preview payload.
  - The guardrail only fires for `is_writing_draft_turn=True` turns and only when
    the final content is much shorter than the authored text. It does not fire for
    non-writing-draft turns, code drafts, or when the builder content already matches.
- Non-goals preserved:
  - No architecture redesign.
  - No submit/handoff ownership changes.
  - No queue-boundary behavior changes.
  - No weakening of fail-closed behavior.
  - No direct execution shortcuts from chat.

## 2026-04-02 — PR #TBD — improve(vera_web): clarify preview-only vs submitted reply UX

- Summary: Bounded UX-wording PR that improves how Vera communicates preview state
  transitions to users. Reply wording is part of the governed UX contract — the
  assistant must not imply submission when none occurred, and must not sound vague
  or control-plane-ish when a real preview was prepared.
- **Wording changes** (`src/voxera/vera_web/conversational_checklist.py`):
  - New preview prepared: "I've prepared a preview of your request. This is preview-only — nothing has been submitted yet."
  - Existing preview updated: "I've updated the preview with your changes. This is still preview-only — nothing has been submitted yet."
  - Rename/save-as with path: "Updated the draft destination to `{path}`. This is preview-only — nothing has been submitted yet."
  - Stale/unchanged preview: "The current draft is still in the preview, unchanged. Nothing has been submitted."
  - Empty/failed preview: "I wasn't able to prepare a preview for this request."
  - Save-without-content: "I couldn't find a recent response to save in this session."
  - Follow-up preview: "I've prepared a follow-up preview based on evidence from `{job_id}`. This is preview-only — nothing has been submitted yet."
- **New parameter**: `preview_already_existed: bool` threaded through
  `conversational_preview_update_message` → `response_shaping._conversational_preview_update_message`
  → `assemble_assistant_reply` to distinguish "prepared" from "updated" wording.
- **Writing-draft preview notice** (`src/voxera/vera_web/response_shaping.py`):
  Writing-draft and refinement turns show authored content in chat (not a control
  message). When a preview was prepared or updated on such a turn, a preview-state
  notice is now appended to the authored content so the user clearly sees preview state.
  This closes the gap where writing-draft turns bypassed all three controlled-wording
  paths in `assemble_assistant_reply`.
- **Refinement content binding fix** (`src/voxera/vera_web/draft_content_binding.py`):
  Refinement turns on active prose previews ("make it shorter") now bypass the
  `looks_like_non_authored_assistant_message` filter in `extract_reply_drafts`.
  Root cause: authored content about VoxeraOS concepts (queue state, approval status,
  expected artifacts) was rejected as non-authored, causing `reply_text_draft=None`,
  which prevented the late writing-draft refinement detection from firing and left
  stale/truncated builder content in the authoritative preview.
  The bypass is gated on `active_preview_is_refinable_prose AND is_writing_refinement_request
  AND NOT looks_like_preview_rename_or_save_as_request` to prevent rename mutations
  from accidentally overwriting preview content with narration text.
- **Behavioral guidance**: Reply wording for preview-state transitions is governed UX.
  Every preview reply must clearly state: (a) whether the preview is new or updated,
  (b) that it is preview-only, and (c) that nothing has been submitted. This applies
  to all paths through `assemble_assistant_reply` and `chat_early_exit_dispatch`,
  including writing-draft turns that show authored content with an appended notice.
  Preview payload truth must also be preserved: the authoritative preview content
  must materially match the authored chat content, not be truncated or stale.
- Non-goals preserved:
  - No architecture redesign.
  - No submit/handoff ownership changes.
  - No queue-boundary behavior changes.
  - No weakening of fail-closed behavior.

## 2026-04-01 — PR #TBD — fix(vera): improve reliability of natural drafting and follow-up conversational paths

- Summary: Bounded product-stabilization PR that widens Vera's recognition of natural user
  drafting prompts, post-job follow-up phrasings, and LLM wrapper stripping patterns —
  without changing architecture, ownership boundaries, or execution mode classification.
- **Root cause 1 — `is_writing_draft_request` missed natural phrasing variants**
  (`src/voxera/core/writing_draft_intent.py`):
  - "write up a quick explanation of X" — `_DIRECT_WRITING_RE` now matches `write\s+up\s+(?:a|an)`.
  - "put together a short writeup about X" — `_DIRECT_WRITING_RE` now matches
    `put\s+together\s+(?:a|an)\s+(?:short\s+)?(?:writeup|write-up|summary|note|explanation)`.
  - "draft a brief summary of X" — `_DIRECT_WRITING_RE` now matches
    `brief\s+(?:summary|writeup|write-up|explanation)`.
  - `_SHORT_NEW_FILE_DRAFT_RE` widened to accept `put\s+together` as a verb and `summary` as
    a file-shape target, and `of\s+\w` as a topic signal.
  - `_WRITING_VERB_RE` now includes `put` for "put together" patterns.
- **Root cause 2 — `_FOLLOWUP_HINTS` missed conversational follow-up variants**
  (`src/voxera/vera/evidence_review.py`):
  - Added 16 new hint phrases: "now prepare/draft the follow-up", "queue the next step",
    "queue a/the follow-up", "do the next step", "do the follow-up", "let's do the next step",
    "based on that/the outcome", "what should we do next based on that", "what's the next step
    based on that", "draft/prepare/write the follow-up".
- **Root cause 3 — content-shape signal gaps in generation turn detection**
  (`src/voxera/vera_web/execution_mode.py`):
  - `_looks_like_active_preview_content_generation_turn` now recognizes "note", "writeup",
    and "write-up" as content shape signals.
- **Root cause 4 — wrapper stripping missed newer LLM reply patterns**
  (`src/voxera/core/writing_draft_intent.py`):
  - `_WRAPPER_PREFIX_RE` now matches "I've put together", "Here's what I came up with",
    "Here's what I wrote/drafted/put together", and "Here's the note/summary".
  - `_looks_like_wrapper_block` now detects "put together a draft/note/summary",
    "here's what I came up with/wrote".
  - `_looks_like_trailing_wrapper_block` now strips "let me know if you'd like any changes",
    "let me know if you want to change", "would you like me to save", "want me to save".
- **Root cause 5 — `looks_like_non_authored_assistant_message` false-positives on writing-draft
  content** (`src/voxera/vera_web/draft_content_binding.py`):
  - When the user asked for a writeup about VoxeraOS concepts (e.g. "operator truth surfaces"),
    the authored prose naturally mentioned system terms like "queue state" or "approval status".
    The `looks_like_non_authored_assistant_message` filter matched these terms and set
    `reply_text_draft=None`, preventing the writing-draft injection from binding the authored
    content into the preview payload. The preview was left with the builder's junk/empty content.
  - Fix: skip the `looks_like_non_authored_assistant_message` check in `extract_reply_drafts`
    when `is_writing_draft_request(message)` is True. Writing-draft turns produce authored
    document content that may legitimately contain any terms. Added a parallel fallback that
    uses the full `sanitized_answer` when normal prose extraction also fails.
  - **Important behavioral guidance**: for writing-draft turns, the non-authored-message filter
    is bypassed because the user's intent is unambiguously to produce prose. For all other turn
    types, the filter continues to operate as before.
- **No architecture/ownership changes.** Queue boundary, truth-sensitive submit/handoff, and
  execution mode classification are all unchanged. All fixes are pattern-level widening and
  one conditional filter bypass for explicit writing-draft turns.
- Characterization tests added in `tests/test_vera_chat_reliability.py` (59 tests):
  writing-draft recognition (positive + negative regression guards for "put"/save-only),
  follow-up phrasing (positive + negative regression guards for similar non-followup phrases),
  wrapper stripping, session-level drafting flows, content-shape signal coverage, and
  preview-truth binding regression tests for the two exact live-failing prompts.
- Regression tests added in `tests/test_draft_content_binding.py` (5 tests):
  system-term content extraction, non-writing-draft filter preservation, and preview binding
  over junk/empty builder content.
- Validation: ruff format, ruff check, mypy, pytest, merge-readiness-check, golden-check.

## 2026-04-01 — PR #TBD — refactor(vera_web): extract early-exit intent handler dispatch from giant chat()

- Extracted the early-exit intent handler dispatch cluster (~290 inline lines, 9 independent
  short-circuit branches) from the `chat()` function in `src/voxera/vera_web/app.py` into
  `src/voxera/vera_web/chat_early_exit_dispatch.py`. This is the **third decomposition strike**
  against the Vera web `chat()` hotspot.
- The extracted module owns `EarlyExitResult` (dataclass with write instructions and result
  fields) and `dispatch_early_exit_intent()` (evaluates 9 ordered early-exit conditions:
  diagnostics refusal, job review, follow-up preview, investigation derived-save, investigation
  compare, investigation summary, investigation expand invalid-reference error path,
  investigation save, near-miss submit phrase).
- All truth-sensitive ownership stays in `app.py`: `write_session_preview`,
  `write_session_handoff_state`, `write_session_derived_investigation_output`,
  `append_session_turn`, `_render_page`, all submit/handoff decisions (`_submit_handoff`),
  the weather-context LLM lookup (async I/O, stays in app.py), and the blocked-file guard
  (ordering constraint: must follow submit checks, stays in app.py).
- `app.py` reduced from ~1,405 to ~1,182 lines (third strike; ~223 lines removed this PR).
  The `chat()` function reduced from ~737 to ~445 lines.
- Updated `docs/ARCHITECTURE.md` (directory tree, refactor ownership notes), `docs/ops.md`
  (Vera web contributor guidance), and `docs/HOTSPOT_AUDIT_EXTRACTION_ROADMAP.md` (third-strike
  completion entry, next-seam recommendation).
- Characterization tests added in `tests/test_chat_early_exit_dispatch.py` (43 tests covering
  all 9 dispatch branches, EarlyExitResult defaults, write-flag integrity invariants, no-match
  fallthrough, and fail-closed behavior across near-miss submit and missing-evidence paths).
- **Current vera_web extraction state:** six focused modules now own pure derivation/dispatch
  seams: `conversational_checklist.py`, `execution_mode.py`, `preview_content_binding.py`,
  `draft_content_binding.py`, `response_shaping.py`, and `chat_early_exit_dispatch.py`.
  `app.py` retains route handlers, truth-sensitive session writes, queue-boundary decisions,
  and submit/handoff orchestration.
- **Recommended next seam:** (a) targeted test hardening for the remaining inline `chat()`
  orchestration body, or (b) extraction of the preview builder update / LLM call cluster
  (largest remaining self-contained inline block). The vera_web hotspot is substantially
  reduced; further extraction is optional based on concrete need.
- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`
  - `make merge-readiness-check`
  - `make golden-check`

## 2026-04-01 — PR #TBD — fix(vera_web): pre-handoff draft-creation / wrong-mode reply path

- Summary:
  - Four live prompts that should create authoritative writing-draft previews were misfiring: fail-closed reply ("I was not able to prepare a governed preview"), false submission-not-confirmed message, or path-only mutation without fresh authored content.
  - **Root cause 1 — `is_writing_draft_request` too narrow** (`src/voxera/core/writing_draft_intent.py`):
    - "Draft a short markdown note explaining..." — "note" was absent from `_DIRECT_WRITING_RE`; `_SHORT_NEW_FILE_DRAFT_RE` now catches "draft/write/create ... note/file ... explaining/about/describing/regarding" patterns.
    - "Write a short markdown file explaining..." — `_SAVE_ONLY_RE` blocked it (write + file) and `_TRANSFORM_SIGNAL_RE` did not match; the new `_SHORT_NEW_FILE_DRAFT_RE` check runs first and returns True for clear new-content creation.
    - "Draft a short note...save it as explanation.txt." — `_SAVE_ONLY_RE` matched on "save...\.txt"; `_SHORT_NEW_FILE_DRAFT_RE` (draft + note + about) takes priority.
    - "Create a draft explanation as explanation.txt." — "create" was absent from `_WRITING_VERB_RE`; added. "explanation" was absent from `_DIRECT_WRITING_RE`; added.
  - **Root cause 2 — `_guardrail_submission_claim` misfiring on authored content** (`src/voxera/vera_web/app.py`):
    - LLM content explaining VoxeraOS queue semantics legitimately contains words like "queued"; the guardrail replaced the entire drafted note with "I have not submitted anything to VoxeraOS yet."
    - Fix: skip `_guardrail_submission_claim` when `is_writing_draft_turn=True`. Writing draft turns author document content, not system-state claims.
  - **Root cause 3 — naming-mutation reply override fired on writing draft turns** (`src/voxera/vera_web/response_shaping.py`):
    - "Draft a short note...save it as explanation.txt." contains "save...as"; `looks_like_preview_rename_or_save_as_request` returned True, so `assemble_assistant_reply` replaced the LLM-generated content with "Updated the draft destination to ~/VoxeraOS/notes/explanation.txt."
    - Fix: guard the naming-mutation override with `and not is_writing_draft_turn`. Writing draft turns generate new content — the reply should not be a path-mutation control message.
  - Characterization tests added in `tests/test_vera_draft_bug_fix.py` (23 tests): parametrized classification unit tests for 9 prompts that should be writing drafts and 8 that should not (including "make a note for later about buying milk" regression guard); `assemble_assistant_reply` unit tests for naming-mutation override exemption and regression; integration tests for all 4 observed failing prompts.
  - Queue boundary / truth-sensitive handoff logic unchanged. Final `write_session_preview` / `write_session_handoff_state` ownership stays in `app.py`.
- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`
  - `make merge-readiness-check`
  - `make golden-check`

## 2026-03-31 — PR #TBD — refactor(vera_web): extract response-shaping / reply-assembly seam from giant chat() function

- Extracted the response-shaping / reply-assembly tail cluster (~127 lines) from the giant `chat()` orchestration function in `src/voxera/vera_web/app.py` into `src/voxera/vera_web/response_shaping.py`. This is the second decomposition strike against the Vera web `chat()` hotspot.
- The extracted module owns: `derive_preview_has_content()` (pure derivation — does the effective preview contain real authored content?), `guardrail_false_preview_claim()` (pure text guardrail for false preview-existence claims, with fenced-code preservation), `should_clear_stale_preview()` (pure predicate — should an orphaned empty write_file shell be cleared after guardrailing?), `AssistantReplyResult` (dataclass), and `assemble_assistant_reply()` (the full post-guardrail assistant reply assembly pipeline: naming-mutation control replies, explicit-refinement control replies, voxera-control-turn suppression, preview-dump suppression, ambiguous-request messaging, generation-refresh fail-closed appends, and reply-status derivation).
- All I/O stays in `app.py`: `read_session_preview`, `_guardrail_submission_claim` (reads session handoff state — truth-sensitive, stays), conditional `write_session_preview` for stale empty-shell cleanup, `append_session_turn`, `_render_page`. Route orchestration, submit/handoff truth, queue-boundary decisions, and session persistence ownership are unchanged.
- `app.py` reduced from ~1,490 to ~1,400 lines. The `chat()` function reduced from ~805 to ~737 lines. The inline Phase G/H response-shaping block shrank from ~127 lines to ~35 lines.
- Updated `docs/HOTSPOT_AUDIT_EXTRACTION_ROADMAP.md` (added second-strike completion entry, updated next-seam recommendation).
- Characterization tests added in `tests/test_response_shaping.py` (32 tests covering preview-content derivation, false-claim guardrailing, stale-preview cleanup predicate, and reply assembly paths including code-draft pass-through, voxera-control-turn suppression, json-content bypass, naming-mutation control message, fail-closed generation refresh, ambiguous-change-request messaging, and status derivation).
- **Recommended next seam:** early-exit intent handler dispatch (~337 lines of well-bounded independent early-return paths in `chat()` — review, followup, investigation, near-miss submit, blocked file, diagnostics). This is the largest remaining coherent seam and the next logical step to further reduce `chat()` without touching truth-sensitive state writes.

## 2026-03-30 — PR #TBD — refactor(vera_web): extract draft content binding from giant chat() function

- Extracted the post-LLM draft content binding cluster (~385 lines) from the giant `chat()` orchestration function in `src/voxera/vera_web/app.py` into `src/voxera/vera_web/draft_content_binding.py`.
- The extracted module owns: `strip_internal_control_blocks()` (pure control-markup removal), `extract_reply_drafts()` (pure extraction of code/text drafts from LLM replies), and `resolve_draft_content_binding()` (the full post-LLM draft binding pipeline: late code/writing-draft detection, code draft injection, writing draft injection, generation content binding, content refresh fallback, and create-and-save fallback).
- All final `write_session_preview` and `write_session_handoff_state` calls remain in `app.py`. Route orchestration, submit/handoff truth, queue-boundary decisions, and session persistence ownership are unchanged.
- `app.py` reduced from ~1,864 to ~1,490 lines. The `chat()` function reduced from ~1,153 to ~797 lines.
- Updated `docs/ARCHITECTURE.md` (directory tree), `docs/HOTSPOT_AUDIT_EXTRACTION_ROADMAP.md` (Hotspot 1 extraction progress, next-seam recommendation), `docs/ops.md` (contributor guidance).
- Characterization tests added in `tests/test_draft_content_binding.py` (25 tests covering control-block stripping, reply draft extraction, code/writing draft binding, late-detection paths, create-and-save fallback, degraded-status skip, generation-refresh fail-closed, enrichment-turn blocking, and write-signal correctness).
- **This is the first decomposition strike against the Vera web chat() hotspot.** The giant function is now meaningfully safer and more readable. Truth ownership is unchanged.
- **Recommended next seam:** response shaping / reply assembly cluster (~138 lines at tail of chat()) or early-exit intent handler dispatch (~337 lines). Both are coherent seams that could further reduce `chat()` without touching truth-sensitive write ownership.

## 2026-03-30 — PR #TBD — docs(architecture): formally close CLI queue extraction series

- **Decision: the CLI queue extraction series is considered complete.** `cli_queue.py` is now the intentional root CLI composition/truth surface for the queue command family. No further extraction PRs are planned for this series.
- Eight command-family modules were successfully extracted: `cli_queue_payloads.py`, `cli_queue_files.py`, `cli_queue_health.py`, `cli_queue_hygiene.py`, `cli_queue_bundle.py`, `cli_queue_approvals.py`, `cli_queue_inbox.py`, `cli_queue_lifecycle.py`.
- Remaining in `cli_queue.py` by design: `queue status` (dense operator-truth rendering), `queue init` (root-level operational surface), `queue lock status` + `_render_lock_status` (lock display), top-level Typer app definitions (`queue_app`, `queue_approvals_app`, `queue_lock_app`, `inbox_app`, `artifacts_app`), `register()` composition root, and all command registration wiring.
- Rationale: `queue status` is truth-sensitive operator-facing rendering, not a mechanical handler. `queue init` and `queue lock status` are naturally root-level. Top-level Typer registration and public CLI contract ownership belong in the composition root. Further extraction is no longer high-value relative to complexity/risk. `cli_queue.py` (~315 lines) is no longer an uncontrolled hotspot.
- **Future extraction of `queue status` is optional, not presumed.** Only consider if a strong concrete need emerges (e.g., significant growth from new root-level commands).
- Updated `docs/ARCHITECTURE.md` (module map, CLI ownership notes), `docs/HOTSPOT_AUDIT_EXTRACTION_ROADMAP.md` (completion decision, PR-8 status), and `docs/ops.md` (CLI contributor note).
- **Recommended next work:** instead of continuing automatic CLI extraction, focus on other high-value areas — Vera web/panel hotspot extraction, expanded characterization coverage for other modules, or feature work.

## 2026-03-30 — PR #TBD — refactor(cli): extract CLI lifecycle command-family handlers into cli_queue_lifecycle.py

- Extracted `queue_cancel`, `queue_retry`, `queue_unlock`, `queue_pause`, and `queue_resume` handler functions from `src/voxera/cli_queue.py` into `src/voxera/cli_queue_lifecycle.py`. The new module owns the full lifecycle handler implementations: job-move dispatch, fail-closed `FileNotFoundError` and `QueueLockError` handling, force-unlock path, stale-lock detection and output, pause/resume dispatch, and all console output.
- Registration of all five commands remains in `cli_queue.py` via `queue_app.command(...)(fn)`. Top-level CLI wiring, `queue_app` ownership, `register()`, and public contract ownership are unchanged.
- `_render_lock_status` and `queue_lock_status` intentionally remain in `cli_queue.py` — they are lock-status display surfaces, not lifecycle mutation handlers, and are more naturally paired with the lock sub-app wiring that lives there.
- `QueueLockError` import removed from `cli_queue.py` (now owned by `cli_queue_lifecycle.py`); `MissionQueueDaemon` import remains in `cli_queue.py` (still used by `queue_init`, `queue_status`, and `queue_lock_status`).
- CLI contracts (command names, option names, defaults, help text) are preserved exactly. No behavioral change.
- Updated `docs/ARCHITECTURE.md` (directory tree, module map, CLI command tree), `docs/ops.md` (CLI contributor guidance), and `docs/HOTSPOT_AUDIT_EXTRACTION_ROADMAP.md` (extraction progress notes, next PR recommendation).
- **Current cli_queue.py extraction state:** payload helpers (`cli_queue_payloads.py`), queue files (`cli_queue_files.py`), health (`cli_queue_health.py`), hygiene (`cli_queue_hygiene.py`), bundle (`cli_queue_bundle.py`), approvals (`cli_queue_approvals.py`), inbox (`cli_queue_inbox.py`), and lifecycle (`cli_queue_lifecycle.py`) are all extracted. Remaining in `cli_queue.py`: top-level app wiring, init, status, lock status (`queue lock status` + `_render_lock_status`), and all registration/contract ownership.
- **Extraction series closed:** see the docs(architecture) entry above for the formal completion decision.

## 2026-03-30 — PR #TBD — refactor(cli): extract CLI approvals and inbox command-family handlers into focused modules

- Extracted `queue_approvals_list`, `queue_approvals_approve`, and `queue_approvals_deny` handler functions from `src/voxera/cli_queue.py` into `src/voxera/cli_queue_approvals.py`. The new module owns the full approvals handler implementations: approval list rendering, resolve-approval dispatch, fail-closed `FileNotFoundError` handling, and console output.
- Extracted `inbox_add` and `inbox_list` handler functions from `src/voxera/cli_queue.py` into `src/voxera/cli_queue_inbox.py`. The new module owns the full inbox handler implementations: atomic job creation, goal validation, fail-closed error handling, and rich table rendering with missing-dir hints.
- Registration of all five commands remains in `cli_queue.py` via `queue_approvals_app.command(...)(fn)` / `inbox_app.command(...)(fn)`. Top-level CLI wiring, `queue_approvals_app` and `inbox_app` ownership, `register()`, and public contract ownership are unchanged.
- `from .core.inbox import add_inbox_job, list_inbox_jobs` moved from `cli_queue.py` to `cli_queue_inbox.py`; the `json` import remains in `cli_queue.py` (used by `queue_status`).
- CLI contracts (command names, option names, defaults, help text) are preserved exactly. No behavioral change.
- Updated `docs/ARCHITECTURE.md` (directory tree, module map, CLI command tree), `docs/ops.md` (CLI contributor guidance), and `docs/HOTSPOT_AUDIT_EXTRACTION_ROADMAP.md` (extraction progress notes, next PR recommendation).
- **Current cli_queue.py extraction state:** payload helpers (`cli_queue_payloads.py`), queue files (`cli_queue_files.py`), health (`cli_queue_health.py`), hygiene (`cli_queue_hygiene.py`), bundle (`cli_queue_bundle.py`), approvals (`cli_queue_approvals.py`), and inbox (`cli_queue_inbox.py`) are all extracted. Remaining in `cli_queue.py`: top-level app wiring, init, status, lifecycle (cancel/retry/unlock/pause/resume), lock status, and all registration/contract ownership.
- **Next safe extraction candidate:** lifecycle commands (cancel/retry/unlock/pause/resume) as a bounded family, then `queue status` last (densest operator-truth rendering surface).

## 2026-03-30 — PR #TBD — test(cli_queue): characterize remaining truth-sensitive queue CLI surfaces pre-extraction

- Added bounded characterization coverage for the remaining operator-facing surfaces still living in `src/voxera/cli_queue.py`: `queue status`, lifecycle commands (`cancel`, `retry`, `pause`, `resume`, `unlock`), approvals commands (`list`, `approve`, `deny`), inbox commands (`add`, `list`), and root CLI registration/contract shape checks.
- Scope is test-only (`tests/test_cli_queue_remaining_surfaces.py` + expanded assertions in `tests/test_cli_contract_snapshot.py`); no runtime command handlers, contracts, or queue trust boundaries changed.
- Coverage now anchors both positive and fail-closed behavior on truth-sensitive seams (including missing refs, malformed inbox input, unlock refusal path, and approvals deny behavior).
- **Durable next step updated:** characterization precondition for remaining `cli_queue.py` extraction is now satisfied; next safe extraction PR can split one remaining command family (recommended order: approvals/inbox first, then lifecycle/status with root registration kept in `cli_queue.py`).

## 2026-03-30 — PR #TBD — refactor(cli): extract CLI bundle command handler into cli_queue_bundle.py

- Extracted `queue_bundle` handler function from `src/voxera/cli_queue.py` into `src/voxera/cli_queue_bundle.py`. The new module owns the full `queue bundle` handler implementation: job/system bundle dispatch, `BundleError` handling, output file writing, and console reporting.
- Registration of `queue bundle` remains in `cli_queue.py` via `queue_app.command("bundle")(queue_bundle)`, placed before the first `@queue_app.command` decorator to preserve subcommand help ordering. Top-level CLI wiring, `register()`, and public contract ownership are unchanged.
- `cli_queue.py` reduced from ~542 to ~514 lines. CLI contracts (command name, option names, defaults, help text) are preserved exactly.
- The `from .incident_bundle import ...` import was removed from `cli_queue.py` (now owned by `cli_queue_bundle.py`); unused `Path` and `OUT_PATH_OPTION` imports were also removed.
- Updated `docs/ARCHITECTURE.md` (module map), `docs/ops.md` (CLI contributor guidance), and `docs/HOTSPOT_AUDIT_EXTRACTION_ROADMAP.md` (PR-7c bundle status, Hotspot 3 extraction progress notes).
- **Current cli_queue.py extraction state:** payload helpers (`cli_queue_payloads.py`), queue files commands (`cli_queue_files.py`), health commands (`cli_queue_health.py`), hygiene commands (`cli_queue_hygiene.py`), and bundle command (`cli_queue_bundle.py`) are all extracted. Remaining in `cli_queue.py`: top-level app wiring, init, status, lifecycle (cancel/retry/unlock/pause/resume), approvals (list/approve/deny), inbox (add/list).
- **Next safe extraction candidate:** The remaining commands in `cli_queue.py` (status, lifecycle, approvals, inbox) are operator-lifecycle and truth-critical. These should wait for expanded characterization coverage before extraction. No immediate safe seam is as bounded as bundle was.

## 2026-03-29 — PR #TBD — refactor(cli): extract CLI hygiene command family into cli_queue_hygiene.py

- Extracted `queue_prune`, `queue_reconcile`, and `artifacts_prune` handler functions from `src/voxera/cli_queue.py` into `src/voxera/cli_queue_hygiene.py`. The new module owns all three handler implementations including reporting, config-override resolution, and JSON output formatting.
- Registration of all three commands remains in `cli_queue.py` via `queue_app.command()(fn)` / `artifacts_app.command()(fn)` — top-level CLI wiring, `register()`, and public contract ownership are unchanged.
- `cli_queue.py` reduced from 909 to ~540 lines. CLI contracts (command names, option names, defaults, help text, JSON output schemas) are preserved exactly.
- Registration calls placed after `@`-decorated commands to preserve subcommand ordering in `--help` output.
- Updated `docs/ARCHITECTURE.md` (directory tree, module map, CLI command tree), `docs/ops.md` (contributor guidance), and `docs/HOTSPOT_AUDIT_EXTRACTION_ROADMAP.md` (PR-7 hygiene status, Hotspot 3 progress notes).
- **Current cli_queue.py extraction state:** payload helpers (`cli_queue_payloads.py`), queue files commands (`cli_queue_files.py`), health commands (`cli_queue_health.py`), and hygiene commands (`cli_queue_hygiene.py`) are all extracted. Remaining in `cli_queue.py`: top-level app wiring, bundle, init, status, lifecycle (cancel/retry/unlock/pause/resume), approvals, inbox.
- **Next safe extraction candidate:** `queue bundle` (incident-bundle tooling), which is self-contained. After that, remaining commands are operator-lifecycle which are truth-critical and should wait for expanded characterization coverage.

## 2026-03-24 — PR #TBD — fix(vera): preserve user-provided checklist items in deterministic conversational rendering

- **Root cause:** The previous hard-lock renderer over-prioritized generic fallback templates when sanitized output had no valid list content, but did not give first-class priority to extracting checklist items directly from the user message. This caused real user-provided wedding/grocery/planning items to be replaced by generic boilerplate.
- **Fix — source priority correction:** Updated conversational checklist rendering priority in `src/voxera/vera_web/app.py` to: (1) explicit user-message item extraction, (2) extracted model list/JSON items, (3) generic fallback only when both fail.
- **Fix — user-message item extraction:** Added deterministic user-item extraction for `following:` lists, repeated `I need to ...` task phrasing, and comma-separated `I need ...` list phrasing; normalized/deduped items before rendering.
- **Fix — file-residue filtering:** Added conversational-mode item normalization filters to drop file/payload/system residue (`create_file`, `write_file`, `goal`, `intent`, `action`, and `.md/.txt/.json` artifacts) from final checklist output.
- **Tests:** Added focused characterization coverage for wedding item preservation, grocery item preservation, two-turn follow-up detail preservation, and no file-residue leakage in conversational checklist mode.

## 2026-03-24 — PR #TBD — fix(vera): hard-lock conversational checklist/planning rendering to deterministic in-chat artifacts

- **Root cause:** The conversational checklist lane still allowed freeform post-classification output to pass when no list artifact was present. Sanitization removed preview/workflow/meta text, but remaining non-list output (or empty output) could still avoid deterministic checklist rendering.
- **Fix — authoritative final renderer:** Strengthened `src/voxera/vera_web/app.py` so `CONVERSATIONAL_ARTIFACT` mode always passes through a deterministic final checklist renderer. If no list survives sanitization, Vera now deterministically re-renders from extracted list/JSON items or falls back to a domain-specific checklist template (wedding/grocery/general planning), guaranteeing actual in-chat checklist content.
- **Fix — JSON-to-checklist normalization:** Added JSON item extraction for fenced and bare JSON payloads (`items`, `checklist`, `steps`, `tasks`, etc.) and normalized those into plain markdown checklist items so JSON never reaches user chat in conversational planning mode.
- **Behavior contract preserved:** Explicit save/write intent still routes to governed preview flows; save-after-checklist still works via recent saveable assistant artifacts; non-checklist lanes (script flow, weather saveability, investigation summarize/compare/expand/save/submit) are preserved.
- **Files changed:** `src/voxera/vera_web/app.py`, `tests/test_vera_session_characterization.py`, `docs/ARCHITECTURE.md`, `docs/ops.md`, `docs/CODEX_MEMORY.md`.
- **Tests added/updated:** Added repeated deterministic wedding/grocery/two-turn planning characterization tests that assert: checklist items are always present, no preview/draft/save/submit/queue leakage, no JSON leakage, no meta-only narration, and no hidden preview creation.

## 2026-03-24 — PR #TBD — fix(vera): hard-lock conversational checklist mode to deterministic in-chat rendering and ban preview/json leakage

- **Root cause:** Even with the six-phase sanitizer, the empty-text fallback (`return cleaned if cleaned else text`) silently restored the original unsanitized LLM output when sanitization stripped all content. This happened when the LLM produced ONLY preview/workflow/meta language with no list items — the sanitizer correctly stripped everything, then the fallback returned the original banned text verbatim. Additionally, no post-sanitization enforcement existed to catch edge cases where the sanitizer missed violations.
- **Fix — safe empty-text fallback:** Replaced the naive `return text` fallback with a three-tier safe fallback: (1) extract list items from original text and render as plain checklist, (2) if no items found and original has banned tokens, return a safe conversational prompt, (3) only return original text if it contains no banned content.
- **Fix — item extractor and renderer:** Added `_extract_list_items(text)` to extract list items regardless of format (numbered, bulleted, checkbox) and `_render_plain_checklist(items)` to render them as clean markdown. These enable deterministic re-rendering when sanitization produces empty or contaminated output.
- **Fix — post-sanitization enforcement layer:** Added `_enforce_conversational_checklist_output(text, raw_answer)` as a final safety net after the six-phase sanitizer. Scans for any remaining banned tokens or JSON payloads in non-list-item lines; if found, re-extracts items and renders deterministically. Applied at the `CONVERSATIONAL_ARTIFACT` code path after the sanitizer call.
- **Core rule (unchanged):** Conversational checklist mode must render the checklist artifact itself — not workflow narration, not preview language, not JSON payloads, not meta-only commentary. This must be deterministic, not probabilistic.
- **Files changed:** `src/voxera/vera_web/app.py` (`_extract_list_items`, `_render_plain_checklist`, `_enforce_conversational_checklist_output`, fixed empty-text fallback in `_sanitize_false_preview_claims_from_answer`), `tests/test_vera_session_characterization.py` (8 new tests), `docs/CODEX_MEMORY.md`.
- **Tests added:** (40) sanitizer empty fallback does not restore banned content, (41) empty fallback extracts items from mixed output, (42) grocery checklist with preview language deterministic, (43) two-turn planning with JSON payload stripped, (44) wedding checklist repeated 5 runs deterministic, (45) enforcement layer catches sanitizer edge case, (46) enforcement layer handles empty text.
- Validation: `ruff format --check .`, `ruff check .`, `mypy src/voxera`, `pytest -q`, `make security-check`, `make golden-check`, `make validation-check`, `make merge-readiness-check` — all pass.

## 2026-03-24 — PR #229 — fix(vera): deterministic checklist rendering with six-phase sanitizer — zero preview/JSON/meta leakage

- **Root cause:** The final response was still too LLM-shaped. Even with five-phase sanitization, the LLM could emit: (a) unfenced JSON payloads (`{"intent": "create_checklist", ...}`) that bypassed the fenced-only Phase 1 stripping, (b) novel meta-commentary phrasings ("Here's what I came up with", "I've broken it down") not covered by the original regex, (c) workflow narration using save-adjacent language.
- **Fix — six-phase sanitizer** (upgraded from five-phase):
  - Phase 1a: strip fenced JSON blocks (`` ```json...``` ``).
  - Phase 1b (NEW): strip unfenced multi-line JSON blocks (`{...\n...\n}`).
  - Phase 2: strip lines matching 55+ known false-claim phrases.
  - Phase 3 (nuclear): strip ANY non-list-item line with hard-banned tokens.
  - Phase 4: strip workflow narration lines.
  - Phase 5: strip meta-commentary lines when list items present (broadened regex — now catches "Here's what I came up with", "I've broken it down", "I've laid it out", "I've set it up", etc.).
  - Phase 6 (NEW): strip bare JSON payload lines matching `_BARE_JSON_PAYLOAD_RE` (`{"intent":...}`, `{"goal":...}`, `{"action":...}`, `{"write_file":...}`).
- **Core rule:** Conversational checklist mode must render the artifact itself — not workflow narration, not JSON payloads, not meta-commentary.
- **Prior fixes preserved:** `ExecutionMode` enum, `_classify_execution_mode()`, create-and-save fallback, `conversational_planning_active` continuation flag, save intent override, broader classifier.
- **Files changed:** `src/voxera/vera_web/app.py` (Phase 1b, Phase 6, `_BARE_JSON_PAYLOAD_RE`, `_has_list_content`, broader `_META_COMMENTARY_RE`), `tests/test_vera_session_characterization.py` (5 new tests), `docs/ARCHITECTURE.md`, `docs/CODEX_MEMORY.md`.
- **Tests added (cumulative 39):** Previous 34 + (35) unfenced JSON payload stripped, (36) bare goal JSON stripped, (37) multi-line unfenced JSON stripped, (38) broader meta-commentary stripped, (39) 10x deterministic final-render run with adversarial variants.
- Validation: `ruff format --check .`, `ruff check .`, `mypy src/voxera`, `pytest -q`, `make security-check`, `make golden-check`, `make validation-check`, `make merge-readiness-check` — all pass.

## 2026-03-23 — PR #TBD — fix(vera): answer checklist and structured planning requests conversationally instead of failing preview drafting

- **Root cause:** Checklist/planning/structured reasoning requests (e.g. "create a checklist for my wedding prep") were not classified as conversational answer-first turns. The preview builder (hidden compiler LLM) ran on every non-informational turn, and the conversational LLM naturally produced responses with phrases like "I've prepared your checklist" that triggered `_looks_like_preview_pane_claim()`. Since no actual governed preview existed, `_guardrail_false_preview_claim()` replaced the entire useful answer with "I was not able to prepare a governed preview for this request."
- **Fix — conversational answer-first classifier:** Added `_is_conversational_answer_first_request(message)` in `vera_web/app.py` that detects non-actionable structured reasoning/planning requests: checklists, plans, step-by-step guidance, brainstorming, organizing help, itineraries, to-do lists, etc. Excludes messages with explicit save/write/file intent (detected via `_SAVE_WRITE_FILE_SIGNAL_RE`).
- **Fix — preview builder gating:** When `conversational_answer_first_turn` is True, the preview builder (`generate_preview_builder_update()`) is skipped entirely — same as for informational web turns.
- **Fix — guardrail bypass:** `_guardrail_false_preview_claim()` is skipped for conversational-answer-first turns, so natural phrasing like "I've prepared" doesn't destroy the useful checklist answer.
- **Fix — control reply bypass:** `should_use_conversational_control_reply` excludes conversational-answer-first turns, so the LLM's actual answer is preserved instead of being replaced by a generic "Understood" message.
- **Save-after behavior:** The answer is stored as a saveable artifact via `append_session_turn` → `build_saveable_assistant_artifact()`, so "save that to a note" still creates a governed preview from the checklist content.
- **Classification boundary rule:** Answer-first for non-actionable reasoning outputs; preview only when there is actual save/write/submit/action intent or an active preview refinement context. When `pending_preview is not None`, answer-first bypass is disabled so existing preview refinement flows are not disrupted.
- **Review-pass fix — `_SAVE_WRITE_FILE_SIGNAL_RE` gap:** The initial save/write intent regex missed "save a checklist to a note" and "write a checklist to a file" (patterns where the object noun sits between the verb and the target). Added broader `save\s+\S+.*?\b(?:to|as|into)\s+(?:a\s+)?(?:my\s+)?(?:file|note|notes)\b` and matching write-form patterns to close these false-negative gaps without overmatching.
- **Files changed:** `src/voxera/vera_web/app.py` (classifier + save-intent regex fix + 3 gating changes), `tests/test_vera_session_characterization.py` (9 session-level tests), `tests/test_file_intent.py` (3 parametrized test classes with 34+ input variants), `docs/CODEX_MEMORY.md`.
- **Tests added:** 9 session-level characterization tests (checklist answer-first, save-after-checklist, planning variant, preview-claim-language tolerance, brainstorm, save-with-file-intent guard, active-preview-not-bypassed); 3 parametrized classifier unit test classes covering 34+ input variants including the save-to-note edge cases.
- Validation: `ruff format --check .`, `ruff check .`, `mypy src/voxera`, `pytest -q` (1408 passed), `make security-check`, `make golden-check`, `make validation-check`, `make merge-readiness-check` — all pass.
- **Remaining limitations:** The classifier uses keyword matching; very unusual phrasing may not be caught. "summarize this" and "explain this" are not yet classified as answer-first because they overlap with investigation derivation and writing-draft lanes — they are less likely to trigger the original bug pattern. The classifier will be expanded as new patterns emerge rather than trying to anticipate all possible structured reasoning request forms.

## 2026-03-23 — PR #TBD — docs(architecture): update architecture and operations docs for post-refactor ownership boundaries

- Updated `README.md` with a current ownership map for the refactored Vera, queue, panel, and config/path seams so contributors can see where new logic should land without reverse-engineering the latest PR series.
- Updated `docs/ARCHITECTURE.md` to document the extracted `src/voxera/vera/*` module boundaries, the queue lifecycle module split (`queue_execution`, `queue_approvals`, `queue_recovery`, supporting queue helpers), the panel route-family split, and the distinction between runtime/operator config and app/provider config.
- Updated `docs/ops.md` to reflect the same ownership guidance in the day-2 operator/developer runbook, including a route-module map for the panel and explicit guidance not to re-grow thin compatibility façades such as `vera/handoff.py`.
- Updated `docs/prompts/03-runtime-technical-overview.md` so the concise runtime model seen by role/prompt docs matches the refactored queue and Vera decomposition.
- Scope is documentation-only: no runtime semantics, queue contracts, or user-facing behavior changed in this PR.

## 2026-03-23 — PR #TBD — fix(vera): restore truthful preview drafting for targeted refinements and make weather answers saveable again

- Fixed a regression from the preview-drafting extraction: targeted code-file refinement turns like `add content to script.ps1 ...` once again travel through the governed code-draft path, so Vera gets the code-draft hint, the assistant-visible reply can contain the real updated script, and the authoritative preview stores that generated script instead of the raw refinement phrase.
- Fixed the paired UI-truth regression in `vera_web/app.py`: targeted refinement turns no longer force the generic preview-update acknowledgement when the turn is actually a code/writing draft update with meaningful assistant-authored content.
- Restored save-by-reference support for `save that as a note` / `save this as a note` phrasing so meaningful recent assistant artifacts — including current weather, hourly, and weekend weather answers — resolve back into normal governed save previews again.
- Added focused regression coverage for targeted script refinements and weather-answer saveability so future modularization changes do not silently reintroduce either regression.
- Root cause: the previous extraction accidentally treated targeted code refinements as literal-content preview edits instead of code-draft updates, and the referenced-content matcher failed to recognize `save that as ...` phrasing even though weather answers themselves were still meaningful saveable artifacts.
- Queue truth, preview truth, rename/path safety, and submit semantics remain unchanged; this PR only restores the broken refinement/saveability behaviors.

## 2026-03-23 — PR #TBD — refactor(vera): thin remaining handoff glue and compatibility leftovers

- Added `src/voxera/vera/preview_drafting.py` as the dedicated ownership boundary for Vera's remaining deterministic preview-drafting glue: narrow action-preview normalization, diagnostics preview shaping, save-by-reference note/file drafting, contextual refinement fallback, and drafting guidance examples.
- Reduced `src/voxera/vera/handoff.py` to an intentionally small compatibility façade that re-exports the stable handoff-facing drafting, submission, and investigation helper entrypoints without re-hiding those behavior clusters in one large file.
- Added `tests/test_vera_handoff_compat.py` to lock the import-stable compatibility surface so future cleanups can keep thinning `handoff.py` without silently breaking callers that still patch/import through that module.
- Updated `docs/ops.md` and `docs/ARCHITECTURE.md` to document the new ownership boundary and clarify that extending preview drafting should happen in `preview_drafting.py`, not by re-growing `handoff.py`.
- Root cause: after the earlier seam extractions, `handoff.py` still concentrated leftover deterministic preview-drafting glue plus compatibility aliases, making ownership ambiguous even though the major behavior seams already lived elsewhere.
- Queue truth, preview truth, investigation/saveability semantics, and Vera's user-facing handoff/submit UX contract were intentionally preserved; this PR is a conservative ownership cleanup only.

## 2026-03-23 — PR #TBD — refactor(vera): extract investigation derivation logic from handoff

- Added `src/voxera/vera/investigation_derivations.py` as the dedicated ownership boundary for Vera investigation derivation behavior: summarize/compare/expand intent detection, result subset selection, investigation-derived markdown/save-preview shaping, and preservation of the raw-investigation-vs-derived-artifact distinction.
- Kept `src/voxera/vera/handoff.py` behavior-preserving and materially thinner by delegating investigation derivation helpers into the new module while leaving existing handoff-facing entrypoints stable for callers and tests that still import those names from `handoff.py`.
- Added focused seam tests in `tests/test_vera_investigation_derivations.py` while preserving the broader contextual and web characterization anchors for compare/summarize/expand/save/submit flows.
- Root cause: investigation derivation behavior had accumulated inside `handoff.py` alongside saveability, draft revision, and preview submission concerns, making one of the densest remaining handoff seams harder to review and riskier to extract further.
- Queue truth, preview truth, investigation-derived save semantics, and the user-facing summarize/compare/expand UX contract were intentionally preserved; this PR is a narrow ownership extraction only.

## 2026-03-23 — PR #TBD — refactor(vera): extract preview submission and handoff normalization from handoff

- Added `src/voxera/vera/preview_submission.py` as the dedicated ownership boundary for Vera preview submission behavior: explicit/natural submit intent detection for the active preview, authoritative preview normalization before queue handoff, real queue submission acknowledgement shaping, and truthful no-preview submit responses.
- Kept `src/voxera/vera/handoff.py` behavior-preserving and materially thinner by delegating preview submission and normalization helpers into `preview_submission.py` while leaving broader handoff/saveability/investigation entry points stable for existing callers.
- Updated `src/voxera/vera_web/app.py` to route preview submit flows through the extracted module so session handoff state, linked-job registration, preview clearing, and submit failure behavior remain unchanged while the ownership seam is explicit.
- Added focused tests in `tests/test_vera_preview_submission.py` to anchor the extracted seam directly alongside the existing characterization/web-flow coverage.

## 2026-03-23 — PR #TBD — refactor(vera): extract active preview draft revision interpretation from handoff

- Added `src/voxera/vera/draft_revision.py` as the dedicated ownership boundary for Vera active preview draft revision interpretation: rename/save-as phrasing, explicit path updates, content refinement extraction, content fallback selection, append-mode toggles, and preview mutation shaping for active draft follow-ups.
- Kept `src/voxera/vera/handoff.py` behavior-preserving and materially thinner by delegating rename/path/content refinement handling into the new module while preserving the existing open-preview revision compatibility path and handoff-side entrypoints.
- Added focused seam tests in `tests/test_vera_draft_revision.py` while keeping the existing characterization anchors in `tests/test_file_intent.py` for rename/path safety, content/mode preservation, and fail-closed unsafe path handling.
- Root cause: active preview draft revision interpretation had accumulated inside `handoff.py` alongside saveability, preview drafting, and queue submission concerns, making one of Vera's most session-sensitive seams harder to review and riskier to modularize further.
- Queue truth, preview truth, submit semantics, saveable-artifact behavior, and the user-facing rename/path/content UX contract were intentionally preserved; this PR is a narrow ownership extraction only.

## 2026-03-22 — PR #TBD — refactor(vera): extract investigation flow orchestration from service

- Extracted Vera's explicit web/investigation lane orchestration into `src/voxera/vera/investigation_flow.py`, moving informational-web intent detection, query normalization, Brave result shaping, read-only investigation reply formatting, and read-only enrichment lookup ownership out of `src/voxera/vera/service.py`.
- Kept `src/voxera/vera/service.py` behavior-preserving and thin by delegating into the new investigation-flow module.  Compatibility aliases that were temporarily retained for existing tests/call sites have since been removed (see session-store cleanup entry above).
- Added focused regression coverage proving those service-level investigation compatibility hooks still control the delegated flow, so the extraction does not silently break existing characterization seams.
- Root cause: investigation-specific routing and result-shaping logic had accumulated inside the general Vera service orchestrator, making one of Vera's highest-risk behavioral lanes harder to reason about and riskier to modularize further.
- Queue truth, preview truth, investigation-derived save/compare/expand semantics, and the user-facing investigation UX contract were intentionally preserved; this PR is a narrow module-boundary extraction only.

## 2026-03-22 — PR #TBD — refactor(vera): extract weather flow orchestration from service

- Extracted Vera quick-weather routing and follow-up orchestration into `src/voxera/vera/weather_flow.py`, giving weather-question detection, missing-location handling, follow-up classification, fail-closed lookup behavior, and weather-lane continuity one dedicated module boundary.
- Kept `src/voxera/vera/service.py` behavior-preserving and thin by delegating to the new weather-flow module.  Compatibility aliases that were temporarily retained for existing call sites/tests have since been removed (see session-store cleanup entry above).
- Extended focused characterization coverage so `tests/test_vera_contextual_flows.py` now explicitly anchors `7 day` and `weekend` follow-ups in the structured weather lane in addition to the existing missing-location and `hourly` continuity checks.
- Root cause: weather quick-flow logic had accumulated inside the general Vera reply orchestrator, making one of the most session-sensitive seams harder to reason about and riskier to extract further.
- Queue truth, preview truth, and the user-visible weather UX contract were intentionally preserved; this PR is module-boundary cleanup only.

## 2026-03-22 — PR #TBD — chore(vera): expand characterization coverage and introduce narrower tests for session-sensitive Vera flows

- Started the Vera modularization safety-net pass without changing product behavior: kept the broad `tests/test_vera_web.py` coverage intact while adding narrower session-focused characterization files for saveability/preview revisions and contextual weather/investigation flows.
- Added `tests/vera_session_helpers.py` as a lightweight shared harness/builder layer for isolated Vera session setup, preview reads, derived-output reads, and representative weather/investigation fixtures so follow-on extraction PRs do not need to duplicate FastAPI/session boilerplate.
- Added targeted characterization coverage for: concise answer -> save, courtesy-turn save resolution, explanation saveability, active preview rename/save-as/path revision, unsafe path fail-closed preservation, weather missing-location -> follow-up -> hourly flow, invalid weather location fail-closed behavior, investigation compare/summarize/expand -> save -> submit, and explicit weather-investigation lane preservation.
- Root cause: high-value Vera behavior protection was becoming too concentrated inside broad mixed-flow integration coverage, which increases extraction fear and makes future modularization reviews noisier than necessary.
- Scope intentionally stayed bounded to tests/helpers/docs; queue semantics and intended Vera production behavior remain unchanged.

## 2026-03-22 — PR #TBD — fix(vera): fail closed on unsafe active preview path updates and make concise answers saveable

- Added path safety validation to `normalize_preview_payload()` — all preview payloads (deterministic and LLM-generated) now pass through `is_safe_notes_path` before being persisted. Unsafe paths (parent traversal, queue control-plane, outside workspace) raise ValueError and leave the existing preview unchanged.
- Fixed response truthfulness: when the LLM produces an unsafe path patch that gets rejected by normalization, the response explicitly says the update failed instead of claiming success. Broadened "updated the draft" detection in `_looks_like_preview_update_claim` to catch LLM phrasings that previously slipped through.
- Made concise meaningful assistant-authored answers saveable: lowered the minimum character threshold from 18 to 8 in `build_saveable_assistant_artifact()` so short factual answers like "2 + 2 is 4." are recognized as saveable content. All existing courtesy/low-information/control filters remain intact — "ok", "sure", "you're welcome" still don't win.
- Root causes: (1) `normalize_preview_payload` validated path format but never called `is_safe_notes_path`, allowing LLM-generated unsafe paths to bypass the deterministic safety gate; (2) `build_saveable_assistant_artifact` used `len(cleaned) < 18` which rejected concise factual answers.
- Added 12 focused tests covering path safety in normalize, concise answer saveability, courtesy exclusion, and fragment rejection.

## 2026-03-22 — PR #TBD — fix(vera): allow active preview rename to user-requested filename before submit

- Fixed active `write_file` preview rename: natural phrases like "call the note biggest.txt", "call this note biggest.txt", and "rename it to biggest.txt" now correctly update the authoritative preview target path.
- Added explicit path directive support: "use path: ~/VoxeraOS/notes/biggest.txt", "change the path to ...", and "set the path to ..." now apply as first-class revisions to the active preview.
- When the extracted target is already a full path (~/... or /home/...) it is used directly; bare filenames are placed in the current preview directory.
- All rename/path-update operations are gated by the existing `is_safe_notes_path` check — unsafe paths (parent traversal, queue control-plane) fail closed and leave the preview unchanged.
- Content and mode are preserved across rename/path changes; only the target path and goal text are updated.
- Root cause: `_extract_named_target()` regex only matched `call it X` / `call that X` but not `call the note X` / `call this note X`; also no patterns existed for explicit path directives.
- Added 8 focused tests covering: "call the note X", "save it as X", "use path: ...", "change the path to ...", content/mode preservation, unsafe path rejection, queue path rejection, and "rename it to X".

## 2026-03-21 — PR #TBD — fix(vera): stop weather hallucination and add quick live weather flow

- Added a dedicated Vera quick-weather lane backed by structured Open-Meteo weather data so ordinary weather/current-condition prompts no longer rely on freeform conversational generation for live facts.
- Hardened truthfulness behavior: if a location is missing Vera asks for it, and if the structured live lookup fails or cannot resolve the place clearly Vera explicitly refuses to guess current temperatures, conditions, or highs/lows.
- Added bounded weather session context so natural follow-ups like `hourly`, `7 day`, `weekly`, and `weekend` continue the same conversational weather flow instead of falling back to generic investigation result dumps.
- Preserved explicit investigation behavior for weather only when the user explicitly asks to search/browse/investigate, keeping generic multi-result result dumps available but no longer the default weather experience.
- Kept governed saveability intact: meaningful weather answers still flow through the recent saveable assistant artifact model, so `save that to a note` continues to produce preview-only write payloads.
- Added focused Vera web regressions for missing-location prompting, concise live weather answers, no-guess failure behavior, natural follow-up routing, explicit weather investigation fallback, and pending weather-offer acceptance.

## 2026-03-21 — PR #TBD — fix(vera): unify saveable assistant artifact resolution for governed note creation

- Added a bounded "recent saveable assistant artifact" layer in `src/voxera/vera/handoff.py` that classifies meaningful assistant-displayed content by artifact type (`info`, `explanation`, `summary`, `comparison`, `article`/`essay`/`writeup`, `code_explanation`) and filters out courtesy, queue/preview boilerplate, internal control text, and low-information replies.
- Extended Vera session state in `src/voxera/vera/service.py` to persist a bounded recent artifact list alongside the rolling turn transcript, keeping resolution authoritative within the active session without introducing cross-session memory.
- Updated the deterministic preview-authoring path so `save that`, `save it`, `put that in a note`, and similar follow-ups now resolve against the latest saveable assistant artifact, while existing investigation-derived save precedence and writing/code lanes remain intact.
- Preserved fail-closed behavior for plural/ambiguous references and preserved explicit preview-truth/handoff boundaries: the change only prepares governed previews and does not invent queue submissions.
- Added focused Vera web regressions covering weather-answer saveability and concise factual-answer `save it` note creation.

## 2026-03-20 — PR #TBD — fix(vera): route investigation-summary transforms into governed writing lane

- Tightened `src/voxera/vera/handoff.py` so derived follow-up save detection only claims true save/save-as style requests; transform prompts like `write a short article based on that summary` no longer get misclassified as derived-artifact saves.
- Patched `src/voxera/vera_web/app.py` to prefer the governed writing lane whenever a message is an explicit writing transform, even if a derived investigation summary/comparison/expanded-result is still active in session state.
- Added regressions in `tests/test_vera_web.py` covering the classifier boundary (`save it` still routes to derived save, transform prompts do not) plus the investigation-summary → article → `save it as brave-api-article.md` flow, verifying the saved preview contains article prose rather than the raw summary markdown.
- Bounded limitation remains unchanged: this only disambiguates transform-vs-save routing for single-document prose previews; it does not redesign broader writing workflows or add export/multi-file behavior.

## 2026-03-20 — PR #TBD — fix(vera/writing-lane): apply combined prose refinement + save-as updates before submit

- Patched `src/voxera/vera/handoff.py` so `save it as ...` / rename phrasing on an active preview is treated as a preview revision, not as an implicit submit of the stale prior preview.
- Verified the governed writing lane now updates both authoritative preview dimensions on combined turns: fresh assistant-authored prose replaces the previous draft body and the requested filename/path becomes the active preview path before explicit submit.
- Tightened prose-body extraction so saved writing artifacts drop leading assistant preface/setup lines before the first real title/heading/body block, preserving the clean document body in `write_file.content`. Explanation-style save-by-reference artifacts now pass through the same cleanup path, including conversational preamble stripping before the real explanation body.
- Added a regression in `tests/test_vera_web.py` covering the live Roman Empire flow with the exact phrase `make it more formal and save it as roman-empire-essay.md`, including the guarantee that no inbox job is enqueued until a later explicit submit.
- Scope remains bounded: this fix only changes active-preview revision vs submit disambiguation; it does not add document export formats or multi-file writing workflows.

## 2026-03-18 — PR #TBD — feat(vera): add governed document/article/essay draft lane with authoritative preview support

- Added a bounded prose draft classifier at `src/voxera/core/writing_draft_intent.py` for essays, articles, writeups, rewrite/formalize/expand asks, and plain-English script explanations.
- Extended `src/voxera/vera_web/app.py` to populate authoritative `write_file.content` for prose drafts from the assistant's actual reply, mirroring the governed code lane's preview-truth model.
- Patched a user-facing control leak: `<voxera_control>` transport blocks are now stripped from visible chat text and from prose preview-body extraction, while the authoritative preview/update path remains intact.
- Tightened prose-body extraction so authoritative writing previews store the actual essay/article body instead of wrapper phrases like "I've prepared a draft below" or overview summaries.
- Writing follow-ups now update active preview state instead of failing with "no prepared preview", and save-as filename refinements now preserve the exact requested prose filename through final submit.
- Save-by-reference resolution in `src/voxera/vera/handoff.py` now recognizes `explanation` references and filters out trivial courtesy assistant turns — including extended `You're very welcome ...` variants — so `thanks` does not break `save your previous explanation ...`.
- Narrowed `_is_informational_web_query()` in `src/voxera/vera/service.py` so ordinary compare/explain prompts stay conversational unless the user is explicit about web/latest/current/search intent.
- Added focused Vera web coverage for: explanation → essay expansion, rewrite → formalize + save-as, investigation summary → article, direct essay requests, courtesy-turn save-reference continuity, code → explanation → save explanation, and conversational compare prompts.
- Scope intentionally remains bounded: no docx/pdf export, no multi-file writing workflows, no fake preview/queue claims.

## 2026-03-17 — GitHub PR #TBD — fix(vera/code-lane): fix governed code-draft lane LLM persona override and all-or-nothing preview truthfulness

- **Root cause**: Vera's system prompt says "Not the payload drafter." The LLM never outputs code in fenced blocks; `extract_code_from_reply` always returned `None`; previews stayed permanently empty.
- **Fix — LLM persona override**: Added `_CODE_DRAFT_HINT` constant to `service.py`. When `is_code_draft_turn=True`, `app.py` appends the hint to the user message before calling `generate_vera_reply`. The hint tells the model to write the complete code in a fenced block for governed extraction. Session history stores the original un-augmented message. `generate_vera_reply` signature unchanged to avoid breaking test infrastructure.
- **Fix — all-or-nothing preview truthfulness (fourth-pass)**: When `_guardrail_false_preview_claim` strips a false claim AND the current preview has empty `write_file.content`, the empty placeholder shell is cleared. No orphaned empty previews. Placeholder previews without a false claim are preserved for refinement flows.
- **Fix — robust fenced-block regex**: `extract_code_from_reply` and related regex patterns updated from `r"```(?:[a-zA-Z0-9_+\-.]*)?\n"` to `r"```[^\n]*\n(.*?)```"` — tolerates trailing spaces, version strings, or other characters LLMs emit after language tags.
- **Files changed**: `src/voxera/vera/service.py` (hint constant + `build_vera_messages` flag), `src/voxera/vera_web/app.py` (pre-computed `is_code_draft_turn`, message augmentation, all-or-nothing clearing, regex hardening), `src/voxera/core/code_draft_intent.py` (regex hardening).
- **Tests added**: 11 new tests in `test_vera_web.py` covering hint injection, hint absence, real-world prompt flows (Python URL fetch, web scraper, bash disk/memory), `build_vera_messages` unit tests; 3 new tests in `test_code_draft_intent.py` for fence-line trailing space/version tolerance.
- All tests pass; all checks (`ruff format`, `ruff check`, `mypy`, `make merge-readiness-check`) pass.
- Remaining limitation: the hint adds ~60 tokens to the user message on every code-draft turn; this is intentional and bounded.

## 2026-03-16 — GitHub PR #TBD — fix(vera): singular save-by-reference defaults to latest assistant content

- Resolver behavior tightened for session-content save references: singular vague phrasing (for example `save that`, `put that in a file`) now deterministically resolves to the most recent substantial assistant-authored message in the active session.
- Conservative fail-closed behavior remains for plural/explicitly ambiguous references (for example `save both`, `save those`, `save previous two`).
- Investigation-derived save routing remains explicit (`comparison`/`summary` wording required), avoiding accidental capture of generic conversational `save that` requests.
- Added coverage in hidden-compiler and Vera web tests for latest-message preference and plural ambiguity refusal.
- Follow-up precedence refined: if a current derived investigation output is active, `save that ...` now resolves through investigation-derived save first; generic recent-assistant fallback applies only when no derived output is present.
- Recency nuance added: derived save precedence is not sticky forever; when a newer conversational assistant answer appears later in-session, singular `save that ...` follows that newer answer.
- Expanded investigation-result replies are now persisted as derived investigation text artifacts too, so `expand result N` can be followed by `save it`, `save it as <name>.md`, and then normal preview submission phrasing.

## 2026-03-15 — GitHub PR #TBD — feat(vera/diagnostics): fix diagnostics truth and surface operator-grade answer-first outputs across read and inspection flows

- **Service status correctness**: `service_status.py` now queries both system and user scopes via `systemctl` / `systemctl --user`. Voxera services running as user services are no longer incorrectly reported as inactive/dead. The primary scope is chosen by preferring whichever is active (user scope preferred when both are active). When scopes differ, both states are surfaced in the machine_payload (`other_scope`, `other_ActiveState`, `other_SubState`) and in operator output.
- **Recent logs correctness**: `recent_service_logs.py` now queries both `journalctl -u` (system) and `journalctl --user-unit` (user) scopes and prefers whichever has actual log content. The `"-- No entries --"` journalctl marker is filtered out. Scope is included in the machine_payload. Summary now says "No recent logs" when truly empty instead of misleading count-only output.
- **File read answer-first output**: `files_read_text.py` now includes bounded `content` (up to 2048 chars), `line_count`, and `content_truncated` in machine_payload. The result surfacing layer uses this to show actual file contents answer-first, e.g. `"Contents of a.txt (5 bytes, 1 lines):\nhello"`.
- **Result surfacing layer improvements** (`result_surfacing.py`):
  - File read extractor now prefers `content` from machine_payload (reliable), falls back to `latest_summary`, then to path+size metadata. Includes line count and truncation flag.
  - Service status extractor now surfaces scope label and cross-scope differences.
  - Recent logs extractor now surfaces scope context and says "No recent logs" only when line_count is 0 and log list is empty. Uses `"in the last Nm"` format.
- **Tests**: Added 11 new tests covering: file content from machine_payload, small file full content, large file truncation, service scope awareness, cross-scope differences, legacy no-scope payloads, log scope context, no-entries correctness, diagnostics partial data, directory exists, thin status fallback. Updated 4 existing tests to match new output formats. Added 3 new diagnostics pack tests for user-scope preference, user-scope log preference, and no-entries message.
- All existing tests pass; no regressions to queue delivery, live refresh, duplicate suppression, or review behavior.
- Intentional remaining limitations: content excerpt in machine_payload is capped at 2048 chars; binary files are not supported by the read_text skill; service scope check makes two subprocess calls instead of one.

## 2026-03-15 — GitHub PR #TBD — feat(vera/review): surface evidence-grounded result values across linked completions and review outputs

- Added `vera/result_surfacing.py`: a reusable, deterministic, evidence-grounded value extraction and formatting layer that inspects `step_summaries`/`machine_payload` from canonical execution evidence and produces concise, bounded result text for read/inspection-style operations.
- Supported result families: file read (content excerpt or path+size), file exists (exists/missing), file stat (key metadata), list_dir (bounded entry listing), service status (actual ActiveState/SubState), recent service logs (bounded log excerpt with line count), diagnostics snapshot (compact host/memory/load/disk summary), and process list (top processes with count).
- Integrated into `_format_completion_autosurface_message` in `vera/service.py`: linked completion messages now prefer value-forward text when available, falling back to existing status-oriented messaging when no structured value is present.
- Integrated into `review_message` in `vera/evidence_review.py`: review output includes `- Result:` line with the evidence-grounded result when available.
- Added `value_forward_text` field to `ReviewedJobEvidence` dataclass and `_build_completion_payload`.
- Boundedness enforced: text excerpts capped at 480 chars, log lines limited to last 8, directory entries limited to 12.
- Updated 2 existing test assertions in `test_vera_web.py` to match new value-forward output format.
- Added 24 new tests in `test_result_surfacing.py` covering all result families, fallback behavior, and boundedness.
- Added 7 new tests in `test_evidence_review.py` for value-forward review message surfacing across file read, exists, service status, recent logs, diagnostics, and fallback.
- All existing tests pass; no regressions to queue truth, live delivery, duplicate suppression, or investigation flows.

## 2026-03-14 — GitHub PR #TBD — feat(queue/missions): add bounded read-only system inspection workflow

- Added two new read-only system inspection skills: `system.disk_usage` (home partition usage via `shutil.disk_usage`) and `system.process_list` (process snapshot via `ps`, truncated to 50 entries).
- Both skills declare `state.read` capability, `risk=low`, `fs_scope=read_only`, `needs_network=false`, `exec_mode=local`, and emit canonical `skill_result.v1` payloads.
- Added `system_inspect` mission composing `system.status`, `system.disk_usage`, `system.process_list`, and `system.window_list` into one coherent bounded diagnostic snapshot.
- The workflow executes through the queue for canonical record keeping and audit evidence, despite being read-only and low-risk.
- No approvals required — all skills map to `read` effect class.
- Added focused tests covering: skill structured payloads, mission composition, read-only classification, simulation (zero approvals, not blocked), queue contract/intent propagation, lifecycle/evidence fields.
- Updated `docs/EXECUTION_SECURITY_MODEL.md` with system inspection skills boundary section.
- Updated `docs/ARCHITECTURE.md` mission list.

## 2026-03-14 — GitHub PR #TBD — hardening(cli): queue-first direct CLI mutation gate

- Added queue-first mutation gate to `voxera run`: mutating skills (effect class `write` or `execute`) are blocked from direct CLI execution by default.
- Read-only skills (all capabilities map to `read` effect class) continue to execute directly.
- Added explicit dev-mode override requiring both `VOXERA_DEV_MODE=1` env var and `--allow-direct-mutation` CLI flag — intentionally loud and double-gated.
- Added `is_skill_read_only()` helper in `skills/runner.py` for deterministic mutability classification based on `CAPABILITY_EFFECT_CLASS`.
- Blocked runs print actionable messaging: skill ID, effect classes, queue-first explanation, queue submission command, and dev-mode override syntax.
- Dry-run (`--dry-run`) bypasses the gate since it does not execute.
- Added 35 focused tests covering: `is_skill_read_only` unit tests, `_is_dev_mode` env parsing, run_impl integration (read-only allowed, mutating blocked, flag-without-dev-mode blocked, dev-mode override allowed, dry-run unaffected), all built-in skill classification, and effect-class helper coverage.
- Updated `docs/EXECUTION_SECURITY_MODEL.md` section 9 documenting the gate, dev-mode override, and classification model.
- Updated `CODEX.md` shipped-hardening list.

## 2026-03-14 — GitHub PR #TBD — fix(vera/planner): workspace-relative path shorthand and read intent routing

- Fixed path normalization gap: leading-`/` paths (e.g. `/skillpack-wave2/a.txt`) are now interpreted as workspace-root-relative shorthand → `~/VoxeraOS/notes/skillpack-wave2/a.txt`, not host absolute paths.
- Added `files.read_text` bounded intent classifier (`_classify_read`) for "read", "cat", "display", "print", "output" verbs.
- Reordered handoff routing: bounded file intent now runs before generic file-read goal so stat/info/read intents map to bounded skills instead of falling through to generic planner.
- Added `_WORKSPACE_RELATIVE_PATH_RE` for extracting `/path` tokens from text.
- Added `detect_blocked_file_intent()` that returns a human-readable refusal when an intent pattern matches but path safety blocks it (queue control-plane, parent traversal).
- Wired `detect_blocked_file_intent` into `vera_web/app.py` chat handler: blocked paths now short-circuit before reaching the LLM, preventing pseudo action JSON blobs in chat. No preview is created.
- Parent traversal and queue control-plane rejection remain fail-closed with clear explanations.
- Added 22 new focused tests covering workspace-relative shorthand, read intent, queue shorthand rejection, blocked path refusal (unit + web-level), and end-to-end preview normalization.
- Updated hidden-compiler payload guidance and preview payload schema docs.

## 2026-03-14 — GitHub PR #TBD — feat(vera/planner): bounded filesystem intent-to-workflow routing

- Added `file_intent.py` deterministic classifier that routes natural-language file requests to bounded file skills or the `file_organize` queue contract:
  - exists → `files.exists` inline step
  - stat/info → `files.stat` inline step
  - mkdir → `files.mkdir` inline step
  - delete → `files.delete_file` inline step
  - copy/move → `file_organize` structured contract
  - archive/organize → `file_organize` structured contract
- Extended preview payload schema to support `file_organize` and `steps` top-level keys in Vera handoff, enabling deterministic routing without cloud planner for clear bounded file intents.
- Wired file intent classifier into `handoff.py` `_draft_from_candidate_message()` so Vera prefers bounded file skills over generic fallback when user intent is clear.
- Updated hidden-compiler payload guidance, preview payload schema, and role docs (vera, hidden-compiler, planner) to document bounded file routing patterns.
- Added 31 focused tests covering intent classification, path safety, queue control-plane rejection, handoff integration, and preview normalization.
- Preserved fail-closed behavior: ambiguous paths, paths outside notes scope, and queue control-plane paths all return None (no preview drafted).
- All side effects remain behind preview/handoff/queue semantics — no direct mutations in chat.

## 2026-03-14 — GitHub PR #TBD — feat(queue/missions): add bounded notes archive workflow mission composition

- Added a product-grade bounded filesystem workflow mission `notes_archive_flow` that composes `files.exists`, `files.stat`, `files.mkdir`, `files.copy_file`, and `files.delete_file` as one coherent end-to-end notes archive flow.
- Added a structured queue contract `file_organize` (`source_path`, `destination_dir`, `mode`, `overwrite`, `delete_original`) that deterministically builds a governed multi-step mission on queue rails (including optional delete only when explicitly requested).
- Preserved trust boundaries and fail-closed security semantics: all file paths remain bounded to notes scope and control-plane `~/VoxeraOS/notes/queue/**` stays blocked (`path_blocked_scope`).
- Added focused queue execution and contract tests for successful composed file-organize jobs and blocked control-plane-path behavior, plus docs updates for operator/developer workflows.

## 2026-03-14 — GitHub PR #TBD — feat(queue/review): normalize non-success outcome taxonomy for evidence review

- Added additive structured-execution `normalized_outcome_class` shaping so reviewer/operator surfaces can distinguish approval blocks, policy denial, capability/path boundary blocks, dependency-missing runtime failures, generic runtime execution failures, cancellations, and artifact-evidence gaps.
- Updated Vera evidence review output to surface normalized outcome class directly and use class-specific next-step guidance while preserving canonical queue lifecycle truth.
- Added focused tests across structured execution consumers and evidence review for policy-denied, capability boundary mismatch, path-blocked scope, runtime dependency missing, and partial artifact-gap classification coverage.

## 2026-03-14 — GitHub PR #TBD — fix(skills/files): block queue control-plane paths from file skills

- Tightened confined path normalization to reject access to `~/VoxeraOS/notes/queue/**` for all notes-root file skills (`files.read_text`, `files.write_text`, `files.list_dir`, `files.copy_file`, `files.move_file`, `files.mkdir`, `files.exists`, `files.stat`, `files.delete_file`).
- Added deterministic `path_blocked_scope` error classification for control-plane trust-zone violations.
- Added focused regression tests to prove both source and destination denial for copy/move and direct denial for read/write/list against queue paths.

## 2026-03-14 — GitHub PR #TBD — feat(skills/files): bounded filesystem productivity wave 2

- Added bounded filesystem wave-2 skills with deterministic `skill_result` contracts:
  - `files.mkdir` (confined directory creation in notes scope)
  - `files.exists` (confined path existence checks)
  - `files.stat` (confined path metadata inspection)
  - `files.delete_file` (confined regular-file deletion with explicit `file.delete` capability)
- Preserved centralized path-boundary enforcement and fail-closed control-plane blocking for `~/VoxeraOS/notes/queue/**`.
- Added focused tests for happy paths, control-plane rejections, and manifest governance alignment for wave-2 skills.

## 2026-03-14 — GitHub PR #TBD — feat(skills/files): bounded filesystem productivity wave 1

- Added three additive filesystem skills with normalized manifest governance fields:
  - `files.list_dir` (read-only listing payload in `skill_result.machine_payload.entries`)
  - `files.copy_file` (bounded file copy within notes scope)
  - `files.move_file` (bounded file move/rename within notes scope)
- Preserved fail-closed path boundary semantics by reusing `normalize_confined_path` for both source and destination paths.
- Kept trust boundaries narrow: local-only execution, `needs_network=false`; inspection skill uses `fs_scope=read_only`, mutating skills use `fs_scope=workspace_only`.
- Added focused tests for metadata/scope expectations, path-boundary enforcement, and runtime behavior contracts.

## 2026-03-14 — GitHub PR #TBD — chore(skills): normalize built-in skill governance metadata baseline

- Normalized built-in skill manifests so comparable skills now consistently declare governance fields: `exec_mode`, `needs_network`, `fs_scope`, `output_schema`, and `output_artifacts`.
- Standardized local read-mostly skills to `fs_scope=read_only`; retained `workspace_only` for confined file skills; kept `system.open_url` explicit as `needs_network=true` + `fs_scope=broader`; left sandbox skill explicit with deterministic artifacts.
- Tightened capability normalization by mapping manifest `fs_scope` values explicitly (`workspace_only -> confined`, `read_only -> none`, `broader -> broader`) so review/approval capability declarations better reflect declared intent.
- Added focused tests to lock built-in metadata consistency and read-only fs-scope normalization behavior.
- Updated docs to make the baseline explicit for future skill additions.

## 2026-03-14 — GitHub PR #TBD — feat(web/ux): productization pass across Vera and VoxeraOS panel surfaces

- **Vera web (vera_web/)**: Overhauled chat UX with send-state management (disabled send button + spinner during in-flight), prevention of accidental double-sends via `isSubmitting` guard, Enter-to-send keyboard shortcut (Shift+Enter for newline), textarea auto-resize, and smooth message-in animations. Humanized role labels ("You" / "Vera"). Improved visual design: modern bubble styling with distinct user vs. Vera message treatment, polished empty state with instructional copy, redesigned composer, cleaner topbar with animated status indicator, condensed boundary notice.
- **Panel Vera page (panel/templates/vera.html)**: Replaced flat form+conversation layout with bubble-based chat layout matching the standalone Vera surface. Added send-state management with spinner, Enter-to-send, textarea auto-resize, and auto-scroll to latest message.
- **Panel home.html**: Humanized queue status labels (raw key paths → readable labels), humanized daemon health widget (raw field names → plain English), improved KPI card color-coding (warn/danger tones for non-zero failed/approval counts), improved approval command center scope column (raw `fs=` strings → readable tags), better queue details section with `queue-detail-row` pattern, humanized lock/security counter labels, added Vera link to nav.
- **Panel jobs.html**: Replaced raw artifact flag strings (`plan=Y|actions=N`) with semantic `.artifact-pill` badges (color-coded present/missing), improved lifecycle state display (structured column with step progress), semantic badge coloring per bucket type.
- **Panel job_detail.html**: Removed duplicate section labels, semantic bucket badge coloring, improved stdout/stderr section labels and error styling.
- **Panel assistant.html**: Humanized conversation role labels ("You" / "Voxera"), send-state management with inline spinner to prevent double-submits, improved advisory notice copy.
- **Panel CSS (panel.css)**: Added semantic badge variants (`badge-done`, `badge-failed`, `badge-approval`, `badge-pending`, `badge-inbox`, `badge-canceled`), `.artifact-pill` / `.artifact-pill.present` / `.artifact-pill.missing`, `.scope-tags`, `.spinner-inline`, `.queue-detail-row`, `.lifecycle-cell`, `.assistant-turn-role`, improved `.assistant-turn` line height. All new utilities are additive — no existing layout changed.
- **Vera CSS (vera.css)**: Full rework — modern dark theme, glassmorphism shell, animated accent dot, smooth message bubble animations, polished composer with circular send button and spinner overlay, responding-indicator dots, improved scrollbar, better empty state, dev panel compacted.
- Product identity preserved: Vera surfaces feel conversational/warm; VoxeraOS panel feels controlled/auditable. No blur between identities.
- No architecture changes, no truth boundary weakening, no fake streaming — all improvements are interaction/presentation layer only.

## 2026-03-13 — GitHub PR #TBD — feat(runtime/approval): enforce declared network boundary mismatch + surface capability boundary notes

- Added deterministic runtime enforcement for declared network boundary: when a skill declaration resolves to `network_scope=none` (`needs_network=false`) but runtime args request `network=true`, execution blocks before launch with `capability_boundary_mismatch` and structured `runtime_boundary_violation` evidence.
- Added capability-boundary review context in approval surfaces: pending approval/log payloads now include `capability_boundary_notes` derived from normalized execution capabilities (runtime mismatch notes, allowed domains/paths, declared secret refs when present).
- Extended canonical review/evidence shaping and Vera review output to include `capability_boundary_violation` so operators can see declared-vs-requested boundary mismatches directly from post-execution evidence.
- Added focused tests covering fail-closed boundary enforcement, approval payload visibility, and verifier message surfacing.

- Added forward-looking expected-artifact defaults for canonical assistant/queue lanes so new jobs carry explicit expectation intent into runtime review surfaces.
## 2026-03-13 — GitHub PR #TBD — feat(queue/verifier): surface execution capability declarations and expected-vs-observed artifact evidence

- Extended `execution_result` contract shaping to include additive capability/evidence context in `review_summary` and `evidence_bundle`: normalized `execution_capabilities` visibility plus deterministic expected-artifact observation (`status`, `expected`, `observed`, `missing`).
- Added deterministic expected-vs-observed artifact comparison helper logic (`observed|partial|missing|none_declared`) grounded in produced `artifact_families`/`artifact_refs` only.
- Updated structured execution consumer output to expose these additive review fields for downstream reviewers/verifiers.
- Hardened Vera evidence review output and next-step guidance to surface execution capability declarations and call out missing expected artifacts explicitly with evidence-grounded operator actions.
- Refined reviewer/verifier messaging for expected artifacts to distinguish fully observed, partial, missing, and none-declared cases with explicit state-aware next-step guidance (`succeeded`, `failed`, `canceled`, `awaiting_approval`).
- Added focused tests across queue execution contracts, structured consumers, and Vera review messaging for partial/missing expected artifact cases.
- Updated docs/prompts to reflect the new reviewer contract without altering queue lifecycle, policy/approval semantics, or execution surface area.

## 2026-03-13 — GitHub PR #TBD — feat(vera/enrichment): enrichment-to-preview bridge for grounded pronoun resolution

- Added read-only enrichment bridge: when an active preview exists and the user makes an informational web query, `run_web_enrichment` runs in the service layer and stores `{query, summary, retrieved_at_ms}` as `last_enrichment` in the session file.
- Standalone informational turns (no active preview) skip enrichment storage — behavior and routing unchanged.
- Hidden compiler now receives `enrichment_context` as optional read-only input in the context payload; it never performs web calls itself.
- Deterministic layer (`handoff.py`) uses `enrichment_context.summary` to resolve pronoun references like "put that into the file" into `write_file.content` when ungrounded against the active preview alone.
- Fail-closed preserved: if no enrichment exists and the pronoun reference is ambiguous, returns `no_change`/active preview unchanged.
- `_is_enrichment_turn` exception added to conversational-control-reply suppression so web results are surfaced in chat when an active preview exists.
- Updated docs: `web-investigation-rules.md` (enrichment bridge section), `hidden-compiler-payload-guidance.md` (section 4.1 enrichment_context grounding).

## 2026-03-13 — GitHub PR #TBD — feat(vera/compiler): improve active-preview semantic refinement while keeping strict JSON mutations

- Improved deterministic active-preview refinement interpretation for fluent follow-up language focused on `write_file.content`, `write_file.path`, and `write_file.mode`.
- Added semantic content refinement support for phrases like summary/news and formal-tone rewrites, while preserving fail-closed behavior for ambiguous references (for example `put that into the file` when `that` is ungrounded).
- Kept compiler contract strict: only preview mutation decisions (`replace_preview`, `patch_preview`, `no_change`) with valid preview JSON; no submission/runtime claims added.
- Added focused tests in hidden compiler + Vera web flows covering semantic content updates, fail-closed ambiguous references, and active-preview refinement stability.

## 2026-03-12 — GitHub PR #TBD — feat(vera/verifier): harden lifecycle-aware evidence-grounded review shaping

- Hardened Vera evidence review output shaping so "what happened?" responses are more deterministic and lifecycle-aware while remaining additive.
- Review summary selection now prefers normalized execution contract fields first (`review_summary.latest_summary`, then `evidence_bundle.review_summary.latest_summary`) before legacy fallback summaries.
- Review responses now surface normalized artifact/evidence context (`artifact_families`, `artifact_refs`, and selected `evidence_bundle.trace` fields) when available.
- Lifecycle-specific state handling and next-step guidance were expanded (`submitted`, `queued`, `planning`, `running`, `awaiting_approval`, `resumed`, terminal outcomes), preserving fail-closed semantics.
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

## 2026-03-15 — GitHub PR #TBD — feat(vera): deterministic linked queue completion ingestion foundation

- Added session-linked queue job registry for Vera handoffs, terminal completion ingestion using canonical queue truth, normalized completion payload extraction, and deterministic surfacing policy classification for later conversational behaviors.
- Explicitly deferred broad proactive auto-chat behavior; this PR is additive mechanical groundwork only.

## Vera linked completion auto-surfacing slice (PR #TBD)

- Extended the linked completion ingestion foundation with deterministic chat auto-surfacing for linked `read_only_success`, `mutating_success`, `approval_blocked`, and `failed` completions.
- On each Vera chat cycle, completion ingestion runs first; then at most one unsurfaced eligible completion (policy in `read_only_success|mutating_success|approval_blocked|failed`) is formatted with deterministic evidence-grounded text and appended as an assistant turn.
- Surfaced completions are marked in session artifact state via `surfaced_in_chat=true` and `surfaced_at_ms`, preventing repost spam on later turns.
- Mutating success is now auto-surfaced only when canonical metadata indicates true terminal completion (no pending/delegated downstream child work). Canceled, noisy, and manual-only classes remain intentionally unsurfaced; manual evidence review flow remains the path for those classes.

## 2026-03-16 — PR #TBD — feat(vera): add governed code/script draft lane with authoritative preview support

- Summary:
  - Added `src/voxera/core/code_draft_intent.py`: bounded deterministic classifier for code/script/config draft requests. Supports 30+ file types via `_LANGUAGE_REGISTRY`. Detects intent via verb + language keyword + subject noun OR explicit filename with code extension. Excludes save-by-reference requests. Produces a `write_file` payload with an empty content placeholder.
  - Extended `src/voxera/vera_web/app.py`: after `generate_vera_reply()`, code is extracted from the LLM reply (via `extract_code_from_reply`) and injected into the preview. This creates a real authoritative `write_file` preview backed by LLM-generated content, enabling "save it" → governed submit flow without any new LLM call.
  - Code draft replies are explicitly excluded from the conversational-control reply suppressor so code-containing answers are shown in chat (not replaced with "Understood").
  - Extended `src/voxera/vera/handoff.py` `_ACTIVE_PREVIEW_SUBMIT_PATTERNS` with 4 new patterns: `save it`, `save this`, `let's save it/this`, and `write it/this to file`. These only fire when `preview_available=True` (fail-closed).
  - Added `tests/test_code_draft_intent.py` with 63 unit tests covering all public functions.
  - Added 14 integration tests to `tests/test_vera_web.py` for the code draft lane (preview creation, code injection, fenced code in reply, "save it" submit flow, no-preview fallback).
  - Updated existing test `test_non_voxera_user_requested_json_content_is_still_allowed` → `test_json_config_request_creates_preview_and_shows_fenced_code` to reflect intentional new behavior.

- Design decisions:
  - Code draft classifier intentionally NOT wired into `_draft_from_candidate_message` / `maybe_draft_job_payload` to avoid routing conflicts with save-by-reference and structured note paths. Only runs post-LLM-reply in `app.py`.
  - Single-letter language tokens `c` and `r` excluded from `_LANGUAGE_RE` (too ambiguous); caught via explicit filenames (`main.c`, `analysis.r`). `md` bare token excluded for same reason; `markdown` keyword or `.md` filename works.
  - `go` included in `_LANGUAGE_RE` but requires a subject noun (script/program/config etc.) to prevent "go ahead" false positives.
  - Empty content placeholder in classifier output; actual code injected post-reply for authoritative, LLM-generated content.

- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`
  - `make security-check`
  - `make golden-check`
  - `make validation-check`
  - `make merge-readiness-check`

## 2026-03-17 — PR #TBD (cont.) — fix(vera): code draft lane product-correctness and UX hardening

- Summary (second-pass review):
  - **Code draft refinement gap fixed:** When a user refines an existing code draft ("actually use requests library"), the turn is now detected as a code draft update even though `is_code_draft_request()` does not match. Detection: active `write_file` preview with a code-type extension + fenced code block in the LLM reply. The reply is shown in chat (not suppressed) and the preview content is refreshed with the updated code.
  - **Pending preview fallback:** On refinement turns where neither the hidden compiler nor `classify_code_draft_intent` produce a target draft, the code injection block now falls back to the existing `pending_preview` as the target for content injection.
  - **Apostrophe fix:** `_ACTIVE_PREVIEW_SUBMIT_PATTERNS` pattern `\blets?\s+save` did not match "let's save it" (apostrophe). Changed to `\blet'?s\s+save`.
  - **"write that to file":** Added "that" as a pronoun alongside "it"/"this" in both `let's save` and `write X to file` patterns.
  - Added `has_code_file_extension(path)` to `code_draft_intent.py` for refinement detection.
  - Added 4 integration tests: refinement updates preview, refinement→save flow, "let's save it" with apostrophe, "write that to a file" submit.
  - Added 13 unit tests for `has_code_file_extension`.
- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`
  - `make security-check`
  - `make golden-check`
  - `make validation-check`
  - `make merge-readiness-check`

## 2026-03-17 — PR #TBD (cont.) — fix(vera/code-lane): enforce authoritative preview and truthful submit behavior

- Summary (third-pass correctness/truthfulness review):
  - **Root cause:** On code-draft turns, the LLM reply suppression is intentionally disabled so code blocks are shown. This also allowed raw false claims ("Check the Preview Pane") through. Two bugs exposed by manual testing: (1) LLM claims preview exists when no fenced code was produced → no preview visible; (2) LLM claims updated preview → no preview content.
  - **`_guardrail_false_preview_claim(text, preview_exists)`** added to `vera_web/app.py`: detects phrases like "preview pane", "check the preview", "in your preview" etc. in the text *outside* fenced code blocks; when `preview_exists=False`, strips the false claim and either preserves embedded code blocks with a truthful note or returns a plain "could not prepare preview" message.
  - **`_looks_like_preview_pane_claim(text)`** added: matches claim phrases in non-code text; delegates to the existing `_looks_like_preview_update_claim` for update claims.
  - **`_text_outside_code_blocks(text)`** added: strips fenced code blocks before claim detection to avoid false positives where code mentions "preview" as a variable/string.
  - **Empty-content preview truthfulness:** the preview-existence check passed to `_guardrail_false_preview_claim` treats a `write_file` preview with empty `content` as "no real preview". This catches the case where the builder creates a placeholder (no extractable code) but the LLM falsely claims the draft is ready. Placeholder previews are preserved for refinement flows — only the claim text is corrected.
  - **No regressions:** `test_content_refinement_phrase_script_text_updates_active_preview` confirmed: placeholder empty-content previews survive across turns; refinement turns still inject content correctly.
  - Added 9 integration tests to `tests/test_vera_web.py` covering: false preview claim stripping with no fenced code, empty-content preview claim stripping, submit without preview fails truthfully, go-ahead without preview fails truthfully, submit with real preview succeeds, code-in-chat with real preview (claim valid), false claim stripped but code blocks preserved, explicit write_file flow not regressed.
- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`
  - `make security-check`
  - `make golden-check`
  - `make validation-check`
  - `make merge-readiness-check`

## 2026-03-17 — PR #TBD (cont.) — fix(vera/code-lane): all-or-nothing preview population for code/script draft requests

- Summary (fourth-pass — authoritative preview population):
  - **Root cause (primary):** `extract_code_from_reply()` used `r"```(?:[a-zA-Z0-9_+\-.]*)?\n"` which required `\n` immediately after the language specifier. LLMs frequently emit a trailing space (e.g. ` ```python `) causing the extraction to silently return `None`, so the preview content was never injected.
  - **Root cause (secondary):** When code extraction failed, the empty `write_file` placeholder created by the hidden compiler was left behind. Guardrails caught false chat claims but the orphaned shell persisted in session state, creating a half-state visible as an empty Preview Pane.
  - **`extract_code_from_reply` regex hardened:** changed from `r"```(?:[a-zA-Z0-9_+\-.]*)?\n(.*?)```"` to `r"```[^\n]*\n(.*?)```"`. `[^\n]*` matches any content on the fence line (language tag, trailing spaces, version strings, etc.). Same pattern adopted in `_text_outside_code_blocks` and `_guardrail_false_preview_claim` code-block extraction for consistency.
  - **All-or-nothing cleanup:** after `_guardrail_false_preview_claim` runs, the code checks whether the guardrail modified the text. If it did (a false claim was stripped) and the current preview has empty `write_file.content`, the empty shell is cleared immediately. This makes failed code-draft attempts truly atomic — no orphaned previews.
  - **Refinement flow preserved:** placeholder previews created silently (LLM acknowledges without claiming preview is visible) are NOT cleared. Only previews where a false claim was caught get cleared. `test_content_refinement_phrase_script_text_updates_active_preview` continues to pass.
  - Added 3 new unit tests to `test_code_draft_intent.py`: fence with trailing space, multiple trailing spaces, version tag in language (e.g. `python3`).
  - Added 4 new integration tests to `test_vera_web.py`: explicit-filename code draft populates preview content, failed draft clears empty shell, placeholder survives when no false claim, trailing-space fence line extracts code.
  - Updated `test_no_false_preview_claim_when_builder_creates_empty_preview` to also assert `preview is None` (empty shell now cleared).
- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`
  - `make security-check`
  - `make golden-check`
  - `make validation-check`
  - `make merge-readiness-check`

## 2026-03-21 — PR #TBD — feat(vera-ui): lightweight main-screen guidance for Vera

- Summary:
  - Added a dedicated empty-state guidance layer to the standalone Vera UI so first-run users immediately see what Vera can do without a blocking onboarding flow.
  - Guidance is intentionally compact: one short "How to use Vera" explanation, one concise preview/submit truth note, and six grouped starter-prompt lanes: **Ask**, **Investigate**, **Save**, **Write**, **Code**, and **System**.
  - Prompt examples are clickable chips that populate the composer for quick-start usage; they do not auto-submit and therefore preserve the normal conversational boundary.
  - The guidance appears only in the no-turn empty state and disappears once a conversation begins, so normal Vera chat remains uncluttered.
  - Updated Vera web tests to cover empty-state rendering, example-group visibility, chip wiring presence, and guidance absence once chat turns exist.
- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`
  - `make security-check`
  - `make golden-check`
  - `make validation-check`
  - `make merge-readiness-check`

## 2026-03-22 — PR #TBD — refactor(vera): extract session-store ownership from Vera service

- Summary:
  - Added `src/voxera/vera/session_store.py` as the dedicated home for Vera session persistence/state ownership: session id/path helpers, session payload IO, turn history storage, preview/enrichment/weather/investigation/handoff helpers, saveable assistant artifact state, linked queue-job registry state, and session debug metadata.
  - Kept behavior stable by leaving `src/voxera/vera/service.py` as a compatibility facade: existing public helpers and internal call sites continue using the same names, now delegated to `session_store.py`.
  - Preserved the current session schema and preview/queue truth semantics; this extraction is intentionally narrow and does not redesign weather, investigation, completion surfacing, or preview lifecycle behavior.
  - Added an operations/testing note in `docs/ops.md` so future Vera modularization work knows session persistence/state helpers now belong in `session_store.py`.
- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`
  - `make security-check`
  - `make golden-check`
  - `make validation-check`
  - `make merge-readiness-check`

## 2026-04-04 — PR #TBD — fix(vera): bind explicit literal file content correctly in authored previews

- Root causes:
  - "containing exactly:" was not recognized as a content-extraction marker in `_normalize_structured_file_write_payload` or as an explicit content literal signal in `_message_has_explicit_content_literal`. This caused `write_file.content` to be empty for requests like "Create a file called X containing exactly: Y".
  - PR #284's linked-job continuation gating overcorrected: review/followup hint phrases without resolvable job context fell through to normal LLM flow instead of failing closed honestly. This broke 18 tests in `test_linked_job_review_continuation.py` and violated the product contract for structured fail-closed behavior.
  - The `files.write_text` skill did not include written content in `machine_payload`, unlike `files.read_text` which correctly includes a bounded content excerpt. This meant `_extract_file_write` in `result_surfacing.py` could never surface the actual written text — "What was the output?" returned only operator metadata for file-writing jobs. Additionally, the skill used `bytes_written` but the extractor expected `bytes`.
  - Successful writing-draft turns stacked triple-layer replies: authored content + LLM-generated preview narration + stock preview-state notice from `response_shaping.py`. The stock notice was appended unconditionally even when the LLM reply already contained its own preview/draft update narration.
  - `_extract_prose_body` in `writing_draft_intent.py` split only on double-newlines (`\n{2,}`), so LLM replies with single-newline or inline colon-delimited wrapper:body formatting leaked the wrapper line into preview content. Additionally, `_looks_like_preface_setup_sentence` did not recognize "note" or "summary/summariz" as preface keywords, so inline wrapper sentences like "Here is a short note summarizing the meeting:" were not stripped.
- Fixes:
  - Added `r"\bcontaining\s+exactly\s*:\s*(.+)$"` to content extraction markers in `preview_drafting.py`.
  - Added `containing\s+exactly\s*:` to `_message_has_explicit_content_literal` in `execution_mode.py`.
  - Restored review/followup dispatch to always enter the branch and fail closed honestly when no job target is resolvable, instead of falling through silently.
  - Added `_looks_like_authored_drafting_request()` anti-hijack guard in `chat_early_exit_dispatch.py` so drafting prompts with incidental review/followup hint substrings (e.g. "Write me a note about what happened at the meeting") are not hijacked.
  - Added bounded `content` excerpt, `content_truncated` flag, and `bytes` key (matching `_extract_file_write` contract) to `files_write_text.py` `machine_payload`, mirroring the existing pattern in `files_read_text.py`.
  - Added `_strip_llm_preview_narration()` in `response_shaping.py` to strip LLM-generated preview/draft narration from authored content before appending the canonical stock notice. Preserves authored content; removes duplicate narration; falls back gracefully when the entire text is pure narration (no stripping — stock notice appended normally).
  - Added Phase 3 to `_normalize_markdown_spacing` in `writing_draft_intent.py`: inserts blank lines after colon-terminated wrapper lines that match `_WRAPPER_PREFIX_RE`, so `_extract_prose_body` can separate and strip them. Added `:` to the sentence boundary regex in `_strip_leading_preface_sentences` so inline "wrapper: body" formats are split. Added "note", "summary", "summariz" to `_looks_like_preface_setup_sentence` keyword list.
- Files changed:
  - `src/voxera/vera/preview_drafting.py` — added "containing exactly:" extraction marker
  - `src/voxera/vera_web/execution_mode.py` — added "containing exactly:" to explicit content literal detection
  - `src/voxera/vera_web/chat_early_exit_dispatch.py` — restored fail-closed dispatch, added anti-hijack guard
  - `src/voxera_builtin_skills/files_write_text.py` — added bounded content to machine_payload, fixed key name
  - `src/voxera/vera_web/response_shaping.py` — strip LLM narration before appending stock notice on writing-draft turns
  - `src/voxera/core/writing_draft_intent.py` — wrapper:body normalization, colon sentence boundaries, note/summary preface keywords
  - `tests/test_draft_content_binding.py` — added 3 regression tests for explicit literal content
  - `tests/test_chat_early_exit_dispatch.py` — updated fall-through assertions to fail-closed
  - `tests/test_vera_chat_reliability.py` — updated fall-through assertions to fail-closed
  - `tests/test_vera_live_path_characterization.py` — updated fall-through assertions to fail-closed
  - `tests/test_vera_web.py` — updated fall-through assertions to fail-closed, added file-write output review e2e test
  - `tests/test_files_write_text.py` — added 2 regression tests for content in machine_payload
  - `tests/test_response_shaping.py` — added 4 regression tests for no-duplicate-narration
  - `tests/test_authored_draft_body_fidelity.py` — added 7 regression tests for wrapper/body extraction
- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`
  - `make merge-readiness-check`
  - `make golden-check`

## 2026-03-23 — PR #TBD — refactor(vera): extract saveable assistant artifact selection from handoff

- Summary:
  - Added `src/voxera/vera/saveable_artifacts.py` as the dedicated ownership boundary for Vera saveable assistant artifact selection: meaningful assistant-content filtering, courtesy/control exclusion, saveable artifact typing, recent artifact collection, and `"save that"` / `"save it"` target selection.
  - Kept behavior stable by leaving `src/voxera/vera/handoff.py` thinner and focused on handoff orchestration while importing the extracted saveability helpers for compatibility at existing call sites.
  - Updated `src/voxera/vera/session_store.py` and `src/voxera/vera/service.py` to import the extracted module directly where they persist or consume recent saveable assistant artifacts.
  - Added a focused test seam in `tests/test_file_intent.py` to keep courtesy-skipping and explanation-target selection covered alongside the existing Vera characterization anchors.
  - Updated `docs/ops.md` so future modularization work knows saveable assistant artifact logic now belongs in `saveable_artifacts.py`.
- Validation:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy src/voxera`
  - `pytest -q`
  - `make security-check`
  - `make golden-check`
  - `make validation-check`
  - `make merge-readiness-check`
