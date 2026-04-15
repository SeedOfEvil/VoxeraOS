# 02 — Configuration and Runtime Surfaces

This document maps the configuration surfaces, runtime paths, services, and ports that VoxeraOS actually uses today. Everything here is grounded in the current repo.

## Operator surfaces

VoxeraOS exposes three operator surfaces, all backed by the same queue.

1. **CLI — `voxera`** (Typer app root: `src/voxera/cli.py`)
   - Entry point declared in `pyproject.toml`: `voxera = "voxera.cli:app"`.
   - Top-level commands (from `cli.py` + `tests/golden/voxera_help.txt`):
     `version`, `config-show`, `doctor`, `setup`, `demo`, `status`, `run`, `audit`, `panel`, `daemon`, `vera`.
   - Sub-apps registered on the root:
     `config`, `artifacts`, `skills`, `missions`, `queue`, `ops`, `inbox`, `secrets`.
2. **Web panel — `voxera panel`** (`src/voxera/panel/app.py`)
   - FastAPI app with `title="Voxera Panel"`. Default bind `127.0.0.1:8844`. Route families split into `routes_home`, `routes_jobs`, `routes_queue_control`, `routes_missions`, `routes_hygiene`, `routes_recovery`, `routes_bundle`, `routes_assistant`, `routes_automations`, `routes_voice`.
   - Shared auth/CSRF/mutation-guard plumbing lives in `panel/app.py` and is passed into each `register_*_routes(...)` call.
3. **Vera web — `voxera vera` / `make vera` / `voxera-vera.service`** (`src/voxera/vera_web/app.py`)
   - FastAPI app with `POST /chat`, `GET /chat/updates`, `POST /handoff`, `POST /clear`, `GET /vera/debug/session.json`. Default bind `127.0.0.1:8790`.

The panel and Vera can be run inline via CLI (`voxera panel`, `voxera vera`) or as user services.

## Config surfaces

Runtime configuration is split into two files and a few environment knobs. Both config loaders live in `src/voxera/config.py`:

- `load_config()` — loads operator / runtime config (`config.json` under the voxera user-config dir). Used for panel auth, queue toggles, mutation guards, etc.
- `load_app_config()` — loads application/provider config (`config.yml`) with curated brain slot defaults. Used by Vera and planner flows.
- `write_config_snapshot()` + `write_config_fingerprint()` — redacted snapshot + fingerprint written into the runtime data directory. Used by `voxera config snapshot` and by ops bundles.
- `load_runtime_env()` — loads `.env` if the caller opts in (`should_load_dotenv()` gate; tests explicitly disable with `VOXERA_LOAD_DOTENV=0`).

### Templates

- `config-templates/config.example.yml`
- `config-templates/policy.example.yml`

These are kept as living templates. They are not loaded at runtime automatically — they are reference material for operators bootstrapping a new config.

### First-run config guard

Runtime CLI surfaces (`vera`, `panel`, `daemon`, `queue *`, `inbox *`, `artifacts *`, `automation *`) check for a `config.yml` at the canonical config path before proceeding. If the file is missing, the command prints "No configuration found. Run voxera setup to get started." and exits with code 1. Setup, doctor, version, config show, and all help flows remain usable without config.

### Setup wizard

`voxera setup` runs `src/voxera/setup_wizard.py`. It:

- writes a starter `config.yml`
- offers to configure the four brain slots (`primary`, `fast`, `reasoning`, `fallback`) from the curated OpenRouter catalog (`src/voxera/data/openrouter_catalog.json`)
- runs a bounded post-setup validation step after config is written
- optionally starts the stack and opens the panel / Vera
- ends with a compact "Three things to try" next-steps block: `voxera doctor --quick`, `voxera vera`, `voxera panel`

The full post-setup command list is available via `voxera setup --verbose-next`.

#### Post-setup validation

After config is successfully written, setup automatically runs a bounded validation step (`_post_setup_validation`). This step:

1. Checks first-run brain readiness — verifies that at least one configured brain slot has a resolvable API key (environment variable or keyring via `get_secret`). Unconfigured optional slots are not first-run blockers and do not produce individual warnings. The summary produces exactly one "brain readiness" check: ok if any slot is usable, warn with a focused fix hint if none is.
2. Runs the quick doctor path (`run_quick_doctor()`) in-process. Failures are caught gracefully (e.g., queue not yet initialized). Only doctor checks that report an actual failure (`status == "fail"`) are surfaced — warn-level runtime state (daemon lock absent, no health events yet, etc.) is expected before the first daemon start and is left to `voxera doctor --quick`.
3. Renders a compact traffic-light summary using Rich:
   - **All pass (green):** "Setup complete. Try: voxera vera"
   - **Warnings (yellow):** lists failing checks with actionable fix hints, e.g., "Set OPENROUTER_API_KEY in your environment or run 'voxera secrets set OPENROUTER_API_KEY'."
   - **Failures (red):** lists checks needing attention with fix guidance and recommends `voxera doctor --quick`.

The validation step does not claim the system is ready unless all checks pass. It does not duplicate doctor business logic — brain config checks are setup-specific, and runtime checks delegate to the existing quick doctor surface. Setup completes normally even when warnings or failures are reported.

## Paths

From `src/voxera/paths.py`:

- `config_dir()` — XDG config dir via `platformdirs.user_config_path("voxera")`
- `data_dir()` — XDG data dir via `platformdirs.user_data_path("voxera")`
- `ensure_dirs()` — creates config + data + `audit/`
- `queue_root()` — `~/VoxeraOS/notes/queue`
- `queue_root_display()` — same, collapsed to `~/…`

The queue root is hard-coded to `~/VoxeraOS/notes/queue`. The repo README explicitly documents `~/VoxeraOS` as the officially supported workspace path for the alpha; several flows still assume that location.

### Queue directory contract

From `src/voxera/core/queue_daemon.py` and the queue modules under `core/`:

```
~/VoxeraOS/notes/queue/
├── inbox/                 submitted queue jobs waiting to be picked up
├── pending/               active jobs (planning, running, resumed)
│   └── approvals/         approval artifacts for paused jobs
├── done/                  terminal success jobs
├── failed/                terminal failed jobs (+ .error.json sidecars)
├── canceled/              terminal canceled jobs
├── recovery/              startup recovery quarantine
├── quarantine/            reconcile quarantine
├── _archive/              optional archive space
├── artifacts/             per-job runtime artifacts (plan.json, step_results.json, ...)
├── .daemon.lock           queue daemon single-writer lock
├── health.json            queue health snapshot
└── automations/
    └── .runner.lock       automation runner single-writer lock
```

Each job lives as `<bucket>/<job>.json` with an adjacent `<job>.state.json` lifecycle sidecar (managed by `queue_state.py`). Per-job runtime outputs live under `artifacts/<job>/`.

### Vera session artifacts

Vera persists session state under `queue_root()/artifacts/vera_sessions/<session_id>.json` (`src/voxera/vera/session_store.py`). This is queue-managed storage, not a separate data directory — Vera shares the queue root so everything stays in one audit-visible location.

## Systemd user services

Canonical units live under `deploy/systemd/user/`:

- `voxera-daemon.service`
  ```
  ExecStart=@VOXERA_PROJECT_DIR@/.venv/bin/voxera daemon
  ```
- `voxera-panel.service`
  ```
  ExecStart=@VOXERA_PROJECT_DIR@/.venv/bin/voxera panel --host 127.0.0.1 --port 8844
  ```
- `voxera-vera.service`
  ```
  ExecStart=@VOXERA_PROJECT_DIR@/.venv/bin/python -m uvicorn voxera.vera_web.app:app --host 127.0.0.1 --port 8790
  ```
- `voxera-automation.service` (one-shot, directly valid — uses `%h/VoxeraOS`, timer-owned, no `[Install]` section)
  ```
  ExecStart=%h/VoxeraOS/.venv/bin/voxera automation run-due-once
  ```
- `voxera-automation.timer` (`WantedBy=timers.target`)
  ```
  OnCalendar=minutely
  Persistent=true
  ```

The three long-running services declare `Restart=on-failure`, a 2-second restart backoff, and `WantedBy=default.target`. The daemon unit additionally caps `TimeoutStopSec=10` so graceful SIGTERM shutdown has room to write its shutdown sidecar. The automation service is `Type=oneshot` — it evaluates due definitions once per timer tick and exits. It is **timer-owned**: it has no `[Install]` section and is not enabled directly. Scheduling is owned entirely by `voxera-automation.timer`, which fires every minute with `Persistent=true` so missed ticks after a sleep/reboot are caught up on the next wake. The service stays directly addressable for status (`systemctl --user status voxera-automation.service`), logs (`journalctl --user -u voxera-automation.service`), and manual start for debugging.

`make services-install` rewrites `@VOXERA_PROJECT_DIR@` to the absolute project path, copies **all** units (including `voxera-automation.service`) into `$HOME/.config/systemd/user/`, runs `daemon-reload`, and then enables + starts the **enabled subset** with `systemctl --user`: `voxera-daemon.service`, `voxera-panel.service`, `voxera-vera.service`, and `voxera-automation.timer`. The automation service is copied but not directly enabled — the timer owns its cadence. The automation service uses `%h/VoxeraOS` directly (systemd resolves `%h` to the user's home directory) so it loads without the sed render step.

The top-level `systemd/` folder still carries `voxera-core.service` and `voxera-panel.service` as legacy references (they use `%h/VoxeraOS` directly). The supported path is `deploy/systemd/user/`.

## Ports

Current defaults (grep-verified against `Makefile`, `cli_runtime.py`, and the systemd units):

| Surface | Host | Port | Source |
|---|---|---|---|
| Panel (systemd) | `127.0.0.1` | `8844` | `deploy/systemd/user/voxera-panel.service` |
| Panel (`make panel` dev shortcut) | `127.0.0.1` | `8787` | `Makefile` |
| Vera | `127.0.0.1` | `8790` | `Makefile`, `deploy/systemd/user/voxera-vera.service`, `voxera vera` default |

The panel CLI accepts `--host` and `--port` overrides. The daemon binds no network ports; it is a filesystem-queue worker.

## Makefile — canonical targets

`make` targets (`Makefile`):

- **Format / lint / type / test**: `fmt`, `fmt-check`, `lint`, `type`, `test`, `check`
- **Golden surfaces**: `golden-update`, `golden-check`
- **Security**: `security-check` (runs `tests/test_security_redteam.py`)
- **Validation ladders**: `validation-check`, `full-validation-check`, `merge-readiness-check`, `premerge`
- **Typing ratchet**: `type-check`, `update-mypy-baseline`, `type-check-strict`
- **Dev servers**: `panel` (port 8787), `vera`
- **Daemon ops**: `daemon-restart`
- **Services**: `services-install`, `services-restart`, `services-status`, `services-stop`, `services-disable`, `vera-start`, `vera-stop`, `vera-restart`, `vera-status`, `vera-logs`
- **Release helpers**: `release-check`, `e2e`
- **Updates**: `update`, `update-fast` (wrap `scripts/update.sh`)

`merge-readiness-check = quality-check + release-check + security-check`. The CI required merge gate uses this label as-is — do not rename it without updating the GitHub workflow job name.

## Environment variables used at runtime

Observed from `Makefile` `TEST_ENV_PREFIX` and config-loading code:

- `VOXERA_LOAD_DOTENV` — 0 disables `.env` loading (used by tests).
- `VOXERA_DEV_MODE` — 1 plus `--allow-direct-mutation` enables direct CLI execution of mutating skills (intentionally loud; see `docs/EXECUTION_SECURITY_MODEL.md` § 9).
- `VOXERA_OPS_BUNDLE_DIR` — override ops bundle archive dir.
- `VOXERA_QUEUE_LOCK_STALE_S` — stale-lock threshold.
- `VOXERA_QUEUE_FAILED_MAX_AGE_S`, `VOXERA_QUEUE_FAILED_MAX_COUNT` — failed-bucket retention.
- `VOXERA_ARTIFACTS_RETENTION_DAYS`, `VOXERA_ARTIFACTS_RETENTION_MAX_COUNT` — artifacts retention.
- `VOXERA_QUEUE_PRUNE_MAX_AGE_DAYS`, `VOXERA_QUEUE_PRUNE_MAX_COUNT` — queue prune bounds.
- `VOXERA_PANEL_HOST`, `VOXERA_PANEL_PORT`, `VOXERA_PANEL_OPERATOR_USER`, `VOXERA_PANEL_OPERATOR_PASSWORD`, `VOXERA_PANEL_ENABLE_GET_MUTATIONS`, `VOXERA_PANEL_CSRF_ENABLED` — panel runtime toggles.
- `VOXERA_NOTIFY` — notification surface toggle.

Any variable not listed here should be treated as unsupported until it appears in `config.py` or a CLI option.

## Secrets

`src/voxera/secrets.py` wraps `keyring` with a file fallback. Used by:

- `voxera secrets set|get|unset` (see `cli.py`)
- provider key lookups (`OPENROUTER_API_KEY`) during brain initialization
- the Brave search client (`BRAVE_API_KEY`) used by Vera investigations

The secrets store is explicitly additive to `.env` — production setups should prefer the keyring path.

## Provider support (current reality)

From the README and the curated catalog under `src/voxera/data/openrouter_catalog.json`:

- **Officially tested**: OpenRouter, with Gemini 3 Flash (`google/gemini-3-flash-preview`) as the minimum supported slot.
- **Architecturally supported, not extensively validated**: Ollama and other OpenAI-compatible endpoints via the `brain/openai_compat.py` adapter.
- **Brain slots**: `primary`, `fast`, `reasoning`, `fallback` — set during `voxera setup`.
- **Fallback classification**: `brain/fallback.py` classifies provider failures as `TIMEOUT | AUTH | RATE_LIMIT | MALFORMED | NETWORK | UNKNOWN` and surfaces them through `voxera queue health` and `voxera doctor --quick`.

## Health and diagnostics

- `voxera queue health` — canonical queue health snapshot (grouped current state + recent history + counters). Supports `--json`, `--watch`, `--interval` (from `tests/golden/voxera_queue_health_help.txt`).
- `voxera doctor --quick` — runs quick provider readiness checks and summarises queue health.
- `voxera doctor --self-test` — fuller self test.
- `voxera ops capabilities` — prints the deterministic capabilities snapshot (`core/capabilities_snapshot.py`).
- `voxera ops bundle system` / `voxera ops bundle job <ref>` — incident bundles, archived by default under the data dir.
- `voxera automation list` — list saved automation definitions with key fields (id, enabled, trigger_kind, next_run_at_ms, last_run_at_ms, last_job_ref).
- `voxera automation show <id>` — detailed JSON view of a single automation definition.
- `voxera automation enable <id>` — set `enabled=True` and persist. Only changes the enabled flag; unrelated fields are preserved.
- `voxera automation disable <id>` — set `enabled=False` and persist. Only changes the enabled flag; unrelated fields are preserved.
- `voxera automation delete <id>` — delete the saved definition file only. History records under `automations/history/` are preserved as audit trail.
- `voxera automation history <id>` — show run history records for a definition, newest first. Uses the existing history file naming/linkage.
- `voxera automation run-now <id>` — force an immediate run of a single definition through the existing runner, bypassing the due-time check. Disabled definitions and unsupported trigger kinds are still rejected. Submits through the canonical inbox path — the queue remains the execution boundary.
- `voxera automation run-due-once` — automation runner entrypoint. Acquires the automation runner single-writer lock (`<queue_root>/automations/.runner.lock`) before evaluating definitions; if the lock is already held the command exits cleanly with a `BUSY` message and exit code 0. Evaluates saved automation definitions under `<queue_root>/automations/definitions/` and emits a normal canonical queue payload via the existing inbox path for every *enabled*, *supported* (`once_at`, `delay`, `recurring_interval`), due definition. One-shot triggers (`once_at`, `delay`) disable the definition after firing. Recurring triggers (`recurring_interval`) stay enabled and re-arm `next_run_at_ms` for the next interval. `--id <automation_id>` restricts the evaluation to a single definition (without lock). `recurring_cron` and `watch_path` are persisted but skipped by the runner. The `voxera-automation.timer` systemd unit invokes this command every minute.

### Voice subsystem status

`voxera doctor --quick` now includes symmetric `voice: stt status` and `voice: tts status` checks that report each subsystem's configuration and availability state. The checks load `VoiceFoundationFlags` from the runtime config and produce `STTStatus` (`voice/stt_status.py`) and `TTSStatus` (`voice/tts_status.py`) surfaces.

Status labels: `available` (foundation + input/output enabled + backend configured), `unconfigured` (enabled but no backend), `disabled` (foundation or input/output off). `available` means configured and enabled — it does NOT imply that transcription or synthesis has been tested or will succeed. Disabled-by-config is an intentional state and reports `ok`; enabled-but-unconfigured reports `warn` with actionable hints.

The STT request/response protocol (`voice/stt_protocol.py`) defines the canonical contract shapes for speech-to-text interactions. The STT backend adapter boundary (`voice/stt_adapter.py`) provides the protocol-to-runtime bridge: an `STTBackend` protocol interface, a `NullSTTBackend` for unconfigured systems, and `transcribe_stt_request()` / `transcribe_stt_request_async()` fail-soft entry points that consume `STTRequest` and always return a truthful `STTResponse`.

The TTS request/response protocol (`voice/tts_protocol.py`) defines the symmetric contract shapes for text-to-speech interactions. The TTS backend adapter boundary (`voice/tts_adapter.py`) provides the protocol-to-runtime bridge: a `TTSBackend` protocol interface, a `NullTTSBackend` for unconfigured systems, and `synthesize_tts_request()` / `synthesize_tts_request_async()` fail-soft entry points that consume `TTSRequest` and always return a truthful `TTSResponse`. The key distinction from the STT protocol: the response output is an artifact path (`audio_path`), not a transcript string. See `09_CORE_OBJECTS_AND_SCHEMA_REFERENCE.md` for the full TTS protocol and adapter schema shapes.

The first real STT backend is `WhisperLocalBackend` (`voice/whisper_backend.py`), which uses `faster-whisper` for local audio file transcription. It supports `audio_file` only — `microphone` and `stream` are explicitly unsupported and remain future work. The dependency is optional: install with `pip install voxera-os[whisper]`. If the dependency is missing, the backend honestly reports `backend_missing`. Model loading is lazy (first `transcribe()` call).

Backend selection is handled by `build_stt_backend(flags)` (`voice/stt_backend_factory.py`), which maps `VoiceFoundationFlags.voice_stt_backend` to the appropriate `STTBackend` implementation. When voice input is disabled, no backend is configured, or the backend identifier is unrecognized, it returns `NullSTTBackend`. When `voice_stt_backend` is `"whisper_local"`, it returns `WhisperLocalBackend`. The factory is the single point of backend selection logic.

The recommended entry point for audio-file transcription is `transcribe_audio_file(audio_path, flags, ...)` (`voice/input.py`). It builds an `STTRequest`, selects the backend via the factory (or uses a caller-supplied `backend` to avoid per-call model reload), and runs the request through `transcribe_stt_request()`. It always returns a truthful `STTResponse` — never raises on transcription failure. Only `audio_file` is supported as an input source; microphone and stream remain future work. An async variant `transcribe_audio_file_async(...)` runs the transcription in a thread via `asyncio.to_thread()` for use in async contexts (Vera chat, FastAPI routes).

See `09_CORE_OBJECTS_AND_SCHEMA_REFERENCE.md` for the full schema shapes.

Environment variables for voice configuration (loaded by `voice/flags.py`):
- `VOXERA_ENABLE_VOICE_FOUNDATION` — master toggle for the voice subsystem.
- `VOXERA_ENABLE_VOICE_INPUT` — enable STT input path.
- `VOXERA_ENABLE_VOICE_OUTPUT` — enable TTS output path.
- `VOXERA_VOICE_STT_BACKEND` — STT backend identifier.
- `VOXERA_VOICE_TTS_BACKEND` — TTS backend identifier.

Environment variables for Whisper backend configuration (loaded by `voice/whisper_backend.py`):
- `VOXERA_VOICE_STT_WHISPER_MODEL` — Whisper model size (default: `base`).
- `VOXERA_VOICE_STT_WHISPER_DEVICE` — compute device (default: `auto`).
- `VOXERA_VOICE_STT_WHISPER_COMPUTE_TYPE` — quantization type (default: `int8`).

Environment variables for Piper backend configuration (loaded by `voice/piper_backend.py`):
- `VOXERA_VOICE_TTS_PIPER_MODEL` — Piper model name or path (default: `en_US-lessac-medium`).
- `VOXERA_VOICE_TTS_PIPER_SPEAKER` — speaker id for multi-speaker models (optional; omitted when unset).

The first real TTS backend is `PiperLocalBackend` (`voice/piper_backend.py`), which uses `piper-tts` for local speech synthesis. It supports `wav` output only — other formats are explicitly unsupported and raise `TTSBackendUnsupportedError`. The dependency is optional: install with `pip install voxera-os[piper]`. If the dependency is missing, the backend honestly reports `backend_missing`. Voice loading is lazy (first `synthesize()` call).

Backend selection is handled by `build_tts_backend(flags)` (`voice/tts_backend_factory.py`), which maps `VoiceFoundationFlags.voice_tts_backend` to the appropriate `TTSBackend` implementation. When voice output is disabled, no backend is configured, or the backend identifier is unrecognized, it returns `NullTTSBackend`. When `voice_tts_backend` is `"piper_local"`, it returns `PiperLocalBackend`. The factory is the single point of backend selection logic.

The recommended entry point for text-to-speech synthesis is `synthesize_text(text, flags, ...)` (`voice/output.py`). It builds a `TTSRequest`, selects the backend via the factory (or uses a caller-supplied `backend` to avoid per-call model reload), and runs the request through `synthesize_tts_request()`. It always returns a truthful `TTSResponse` — never raises on synthesis failure. Output is artifact-oriented (`audio_path`), not playback-oriented. An async variant `synthesize_text_async(...)` runs the synthesis in a thread via `asyncio.to_thread()` for use in async contexts (Vera chat, FastAPI routes).

### Voice status panel surface

The operator panel includes a read-only voice status page at `GET /voice/status` (`panel/routes_voice.py`). This is a diagnostic surface — it shows configuration and availability state, not interactive audio controls.

The page shows:
- Voice foundation enabled/disabled
- STT status, backend, and dependency availability
- TTS status, backend, and dependency availability
- Reason strings when a subsystem is unavailable
- Install hints when a backend dependency is missing

A machine-readable JSON endpoint is available at `GET /voice/status.json`. Both endpoints require operator Basic auth.

The combined status is built by `voice/voice_status_summary.py::build_voice_status_summary(flags)`, which reuses the existing `build_stt_status` and `build_tts_status` surfaces and adds dependency checks for known backends (whisper_local checks `faster-whisper`; piper_local checks `piper-tts`). The summary is truthful: it never implies readiness when something is disabled, misconfigured, or missing a dependency.

### Voice TTS generation surface

The voice status page includes a minimal operator-facing TTS generation form that exercises the canonical `synthesize_text(...)` pipeline end to end. This is artifact-oriented — it produces a file path, not browser playback.

- `POST /voice/tts/generate` — HTML form submission. Text input required; voice_id and language optional. Calls `synthesize_text(text, flags, voice_id, language)` and renders the result inline on the voice status page. Requires operator auth + CSRF.
- `POST /voice/tts/generate.json` — JSON API equivalent. Returns `{"ok": true/false, "tts": {...}}`.

The result rendering is truthful:
- Success badge only shown when `status == "succeeded"` AND a real `audio_path` exists.
- Failure states (disabled, unconfigured, missing backend, synthesis error) show the canonical error and error_class from the TTS pipeline.
- Timing fields (audio duration, inference time, total elapsed) shown when available.
- Raw TTS response available in a collapsible detail section.

This is an operator/developer diagnostic tool, not a polished end-user voice experience.

See `08_TESTS_OPERATIONS_AND_CHANGE_SURFACES.md` for how these wire into STV validation.
