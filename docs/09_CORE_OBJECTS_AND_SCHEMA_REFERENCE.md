# 09 — Core Objects and Schema Reference

This document is a short index of the data shapes and core objects that flow through VoxeraOS. It is grounded in `src/voxera/core/`, `src/voxera/models.py`, and the canonical contract docs under `docs/QUEUE_*.md` / `docs/EXECUTION_SECURITY_MODEL.md`. Use it as a cross-reference, not as a spec — the code is authoritative.

## Queue job payload (submit-time)

From `core/queue_contracts.py`. Submitted payloads are free to carry additive fields, but the execution contract is grounded on:

```jsonc
{
  "mission_id": "system_inspect",          // optional
  "goal": "...",                            // optional
  "title": "...",                           // optional
  "steps": [                                // optional
    { "skill_id": "...", "args": { ... } }
  ],
  "enqueue_child": { ... },                 // optional (strict object)
  "write_file": { ... },                    // optional (strict object)
  "file_organize": { ... },                 // optional (strict object)
  "approval_required": false,               // strict boolean
  "_simple_intent": { ... },                // routing hint
  "lineage": {                              // metadata-only observability
    "parent_job_id": "...",
    "root_job_id": "...",
    "orchestration_depth": 1,
    "sequence_index": 0,
    "lineage_role": "child"
  },
  "job_intent": {                           // enriched intent sidecar
    "request_kind": "goal",
    "goal": "...",
    "schema_version": 1
  }
}
```

Unknown keys inside the strict object contracts (`enqueue_child`, `write_file`, `file_organize`) are rejected fail-closed.

## `job_intent.json`

`core/queue_job_intent.py::enrich_queue_job_payload(...)` produces / enriches the `job_intent.json` sidecar.

Typical shape:

```jsonc
{
  "request_kind": "mission_id" | "goal" | "file_organize" | "inline_steps" | "unknown",
  "goal": "string",
  "title": "string",
  "mission_id": "string",
  "schema_version": 1,
  "source": "cli" | "panel" | "vera" | "inbox",
  "notes": "string"
}
```

Derivation order for `request_kind`:

1. `job_intent.request_kind`
2. payload `kind`
3. structural inference from top-level fields
4. fallback `unknown`

## `.state.json` lifecycle sidecar

`core/queue_state.py::read_job_state`, `write_job_state`, `update_job_state_snapshot`. Lives next to the job file in its current bucket.

Typical fields:

- `lifecycle_state` — one of: `queued`, `planning`, `running`, `awaiting_approval`, `resumed`, `advisory_running`, `done`, `failed`, `step_failed`, `blocked`, `canceled`. Invalid values normalize fail-closed to `blocked`.
- `current_step_index` — integer, advancing per step.
- `step_outcomes` — per-step terminal info (status, reason, policy decision).
- `approval_status` — `none`, `pending`, `approved`, `denied`.
- `terminal_outcome` — `succeeded`, `failed`, `blocked`, `denied`, `canceled` (when terminal).
- `summary` — compact human-readable summary.
- `updated_at_ms` — monotonic update timestamp.

The state sidecar is queue-owned; producers never write it.

## `step_results.json`

`core/queue_contracts.py::build_structured_step_results`. Canonical per-step structured results:

```jsonc
{
  "steps": [
    {
      "step_index": 0,
      "skill_id": "files.exists",
      "args": { "path": "..." },
      "capability": "files.read",
      "effect_class": "read",
      "status": "succeeded" | "failed" | "blocked" | "denied" | "canceled",
      "outcome_class": "...",
      "stdout": "...",
      "stderr": "...",
      "result": { ... },                 // skill_result.v1 payload
      "started_at_ms": 0,
      "finished_at_ms": 0
    }
  ],
  "schema_version": 1
}
```

## `execution_envelope.json` + `execution_result.json`

`core/queue_contracts.py::build_execution_result`, `refresh_execution_result_artifact_contract`.

The envelope carries the lifecycle + step execution metadata used by consumers during long-running jobs. The result is the terminal normalized surface, carrying:

- canonical terminal outcome
- normalized step results
- `artifact_families`
- `artifact_refs` (`[{artifact_family, artifact_path}, ...]`)
- `review_summary`
- `evidence_bundle` (including `trace`)
- `normalized_outcome_class` (via the resolver)
- `intent_route` evidence (for goal-kind jobs; see `simple_intent`)

Consumers always read via `core/queue_result_consumers.resolve_structured_execution(...)`. Never re-derive outcome independently.

## Lineage metadata

`core/queue_contracts.py::extract_lineage_metadata`, `compute_child_lineage`.

```jsonc
{
  "parent_job_id": "string",
  "root_job_id": "string",
  "orchestration_depth": 0,
  "sequence_index": 0,
  "lineage_role": "root" | "child"
}
```

Lineage is metadata-only. It does not bypass policy, approval, or capability boundaries. Child jobs are submitted through the normal queue intake and go through the normal governance pipeline.

## Capability semantics

`core/capability_semantics.py::manifest_capability_semantics(manifest)`.

Shape:

```jsonc
{
  "effect_class": "read" | "write" | "execute",
  "intent_class": "read_only" | "mutating" | "destructive",
  "resource_boundaries": {
    "filesystem": true|false,
    "network": true|false,
    "secrets": true|false,
    "system": true|false
  },
  "policy_mapping": {
    "<capability>": "<policy field>"
  }
}
```

`CAPABILITY_EFFECT_CLASS` (re-exported through `policy.py`) is the source of truth for the direct-CLI mutation gate classification.

## Skill manifest (`SkillManifest`)

Defined in `src/voxera/models.py` (Pydantic). Matches the `manifest.yml` schema described in `06_SKILLS_CAPABILITIES_AND_BUILTINS.md`. Fields include `id`, `name`, `description`, `entrypoint`, `capabilities`, `risk`, `exec_mode`, `needs_network`, `fs_scope`, `output_schema`, `output_artifacts`, `args`, and optional `network_scope`, `allowed_domains`, `allowed_paths`, `secret_refs`, `sandbox_profile`, `expected_artifacts`.

## `skill_result.v1`

`skills/result_contract.py::extract_skill_result`. Every built-in skill returns a payload that normalizes to:

```jsonc
{
  "schema": "skill_result.v1",
  "status": "succeeded" | "failed" | "blocked",
  "result": { ... },        // skill-specific structured result
  "summary": "string",
  "artifacts": [ ... ]      // optional file references
}
```

## `MissionTemplate` / `MissionStep`

`core/missions.py`:

```python
@dataclass(frozen=True)
class MissionStep:
    skill_id: str
    args: dict[str, Any]

@dataclass(frozen=True)
class MissionTemplate:
    id: str
    title: str
    goal: str
    steps: list[MissionStep]
    notes: str | None = None
```

## `PlanSimulation` / `PlanStep` / `RunResult`

From `src/voxera/models.py`. These are the mission runner simulation/run shapes used by dry-run and the queue mission expansion path.

- `PlanStep` carries `skill_id`, `args`, `capability`, `policy_decision`, and `approvals_required`.
- `PlanSimulation` aggregates steps plus mission-level capability summary.
- `RunResult` carries outcome + per-step detail for a realized mission run.

`MissionRunner.simulate(...)` in `core/missions.py` builds `PlanSimulation`; `MissionRunner.run(...)` realizes it to `RunResult`.

## `capabilities_snapshot`

`core/capabilities_snapshot.py::generate_capabilities_snapshot()` produces a deterministic snapshot of:

- discovered skills
- their declared capabilities
- normalized capability semantics
- generation timestamp (`generated_ts_ms`; scrubbed to 0 in deterministic dry-run)

Used by:

- `voxera ops capabilities`
- the panel capabilities surface
- mission planner dry-runs (can be frozen once per invocation via `--freeze-capabilities-snapshot`)

## `HiddenCompilerDecision`

`src/voxera/vera/service.py::HiddenCompilerDecision`. Bounded action shape Vera uses when the brain produces a preview-update decision:

```jsonc
{
  "action": "replace_preview" | "patch_preview" | "no_change",
  "intent_type": "new_intent" | "refinement" | "unclear",
  "updated_preview": { ... },   // only with replace_preview
  "patch": { ... }              // only with patch_preview
}
```

Any payload with extra keys or mismatched action/payload combinations is rejected fail-closed.

## `AutomationDefinition`

`src/voxera/automation/models.py::AutomationDefinition` (Pydantic, `extra="forbid"`). Durable record that describes a *future* governed queue submission. PR1 added the data model and file-backed storage. The runner (`src/voxera/automation/runner.py`) fires `once_at`, `delay`, and `recurring_interval` trigger kinds; `recurring_cron` and `watch_path` are persisted but explicitly skipped by the runner. When the runner acts on a definition it emits a normal canonical queue job into `inbox/` via `core/inbox.add_inbox_payload` on the `automation_runner` source lane — the queue stays the execution boundary.

```jsonc
{
  "id": "demo-automation",                 // ^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$
  "title": "Demo automation",              // non-empty, stripped
  "description": "",                        // stripped; may be empty
  "enabled": true,                          // default true
  "trigger_kind": "once_at",               // one of the supported kinds below
  "trigger_config": { "run_at_ms": 0 },    // per-kind strict shape
  "payload_template": { "goal": "..." },   // canonical queue payload shape
  "created_at_ms": 0,                       // positive int epoch-ms
  "updated_at_ms": 0,                       // >= created_at_ms
  "last_run_at_ms": null,                   // positive int epoch-ms | null
  "next_run_at_ms": null,                   // positive int epoch-ms | null
  "last_job_ref": null,                     // non-empty string | null
  "run_history_refs": [],                   // list of non-empty strings
  "policy_posture": "standard",            // "standard" | "strict_review"
  "created_from": "cli"                    // "vera" | "panel" | "cli"
}
```

Supported `trigger_kind` values and their strict `trigger_config` shapes. `once_at`, `delay`, and `recurring_interval` are actively fired by the runner; `recurring_cron` and `watch_path` are persisted but skipped with an explicit reason.

| Kind | Required shape | Runner behavior |
|---|---|---|
| `once_at` | `{"run_at_ms": <positive int>}` | Due when `now_ms >= run_at_ms` and not already fired. One-shot. |
| `delay` | `{"delay_ms": <positive int>}` | Due when `now_ms >= created_at_ms + delay_ms` and not already fired. One-shot. |
| `recurring_interval` | `{"interval_ms": <positive int>}` | Due when `now_ms >= next_run_at_ms` (or `created_at_ms + interval_ms` on first pass). Re-arms after each fire. |
| `recurring_cron` | `{"cron": <non-empty str>}` | Persisted, skipped by runner. Cron parsing is deferred. |
| `watch_path` | `{"path": <non-empty str>, "event": "created"\|"modified"\|"deleted"}` | Persisted, skipped by runner. `event` defaults to `"created"` when omitted. |

`payload_template` must carry at least one canonical queue request field (`mission_id`, `goal`, `steps`, `file_organize`, or `write_file`). The `write_file` and `file_organize` shapes are validated via the same extractors the queue daemon uses at intake (`core/queue_contracts.py::extract_write_file_request` / `extract_file_organize_request`), so an automation definition that validates here would also survive queue intake verbatim. Unknown trigger kinds, unknown `trigger_config` keys, malformed or empty `payload_template`, non-int timestamps, `bool` / `float` leaking into int fields, and id shapes outside `AUTOMATION_ID_PATTERN` are all rejected fail-closed.

Storage lives under `<queue_root>/automations/definitions/<id>.json` via `src/voxera/automation/store.py`. Saves are atomic (`.json.tmp` → `Path.replace`), JSON is sorted for deterministic diffs, and `list_automation_definitions(...)` is best-effort by default so a single malformed file cannot hide the rest of the inventory (`strict=True` surfaces every failure for tooling).

### Vera automation preview shape

Vera can now draft automation definitions conversationally via `vera/automation_preview.py`. The preview shape is:

```jsonc
{
  "preview_type": "automation_definition",    // distinguishes from other preview types
  "title": "Run Diagnostics (every 1 hour)", // inferred from user intent
  "description": "",                          // optional
  "trigger_kind": "recurring_interval",       // one of the supported trigger kinds
  "trigger_config": { "interval_ms": 3600000 }, // per-kind strict shape
  "payload_template": { "goal": "run diagnostics" }, // canonical queue payload shape
  "enabled": true,
  "created_from": "vera",
  "explanation": "..."                        // operator-facing explanation of what will happen
}
```

Submit converts this preview into a durable `AutomationDefinition` and saves it to the automation store. Submit does NOT emit a queue job. Execution happens only through the automation runner → queue path. The submit acknowledgment is truthful: it says the definition was saved, not that it was executed.

### Vera automation lifecycle management

After saving, Vera can manage automation definitions conversationally via `vera/automation_lifecycle.py`. Supported actions: show, enable, disable, delete, run-now, history/status. Reference resolution uses session context (`active_topic: automation:<id>`), explicit id, title match, or single-definition fallback — ambiguous references fail closed with a clarification request.

- **show** — describes the saved definition from the canonical store: title, id, enabled, trigger, action, timing, history summary.
- **enable / disable** — mutates the `enabled` flag on the saved definition via the existing store semantics. Preserves all other fields.
- **delete** — removes the definition file. History records under `automations/history/` are preserved as audit trail.
- **run-now** — forces immediate evaluation through `process_automation_definition(defn, queue_root, force=True)`. Queue-submitting only — Vera does not execute payloads directly.
- **history** — surfaces canonical run records from `list_history_records()`. When no history exists, says so truthfully. Does not hallucinate execution.

The runner writes one JSON file per run event into the sibling `<queue_root>/automations/history/` directory: `auto-<automation_id>-<run_id>.json`. Each record is schema_version 1 and carries `automation_id`, `run_id`, `triggered_at_ms`, `trigger_kind`, `outcome` (`submitted` | `skipped` | `error`), `queue_job_ref` (the `inbox-*.json` filename when submitted), a short `message`, and a `payload_summary` + sha256 `payload_hash` of the saved `payload_template`. History records are write-once. `list_history_records(queue_root, automation_id)` returns all records for a given automation id, newest first, skipping malformed files. After a successful fire the definition is updated with `last_run_at_ms`, `last_job_ref`, and an appended `run_history_refs` entry. One-shot triggers (`once_at`, `delay`) set `enabled=false` and `next_run_at_ms=null`. Recurring triggers (`recurring_interval`) keep `enabled=true` and set `next_run_at_ms = fired_at_ms + interval_ms`.

## STT request/response protocol

`voice/stt_protocol.py`. Protocol-layer contract for speech-to-text interactions. This defines data shapes only — it does not perform transcription.

### `STTRequest`

Frozen dataclass. Built via `build_stt_request(...)`.

```jsonc
{
  "request_id": "uuid-string",                // auto-generated or caller-supplied
  "input_source": "microphone" | "audio_file" | "stream",
  "language": "en-US",                         // BCP-47 locale hint; nullable
  "session_id": "string",                      // correlation id; nullable
  "created_at_ms": 1712900000000,              // epoch-ms
  "schema_version": 1,
  "audio_path": "/path/to/audio.wav"           // nullable; required for audio_file backends
}
```

Unknown `input_source` values are rejected fail-closed (`ValueError`).

`audio_path` is an optional additive field (schema version remains 1 — existing consumers are unaffected). Required when `input_source` is `audio_file` and a file-based backend (e.g. `WhisperLocalBackend`) is used.

### `STTResponse`

Frozen dataclass. Built via `build_stt_response(...)` or `build_stt_unavailable_response(...)`.

```jsonc
{
  "request_id": "uuid-string",
  "status": "succeeded" | "failed" | "unavailable" | "unsupported",
  "transcript": "transcribed text",            // nullable; whitespace-normalized
  "language": "en-US",                         // nullable
  "error": "reason string",                    // nullable
  "error_class": "disabled" | "backend_missing" | "backend_error" | "timeout" | "unsupported_source" | "empty_audio",
  "backend": "provider-name",                  // nullable
  "started_at_ms": 0,                          // nullable
  "finished_at_ms": 0,                         // nullable
  "schema_version": 1,
  "inference_ms": 150,                         // nullable; adapter-reported inference time
  "audio_duration_ms": 3500                    // nullable; adapter-reported audio duration
}
```

Unknown `status` values normalize fail-closed to `"unavailable"`.

`error_class` is intentionally not validated — backends may define their own error classes beyond the canonical constants. This matches the `CanonicalSkillResult.error_class` passthrough policy.

`stt_request_as_dict(request)` / `stt_response_as_dict(response)` serialize to plain dicts for JSON/logging/audit.

## TTS request/response protocol

`voice/tts_protocol.py`. Protocol-layer contract for text-to-speech interactions. This defines data shapes only — it does not perform synthesis. No backend is wired yet.

### `TTSRequest`

Frozen dataclass. Built via `build_tts_request(...)`.

```jsonc
{
  "request_id": "uuid-string",                // auto-generated or caller-supplied
  "text": "Hello world",                       // required; non-empty after strip
  "voice_id": "default",                       // nullable; voice/speaker selection hint
  "language": "en-US",                         // BCP-47 locale hint; nullable
  "speed": 1.0,                                // clamped to [0.1, 10.0]; default 1.0
  "output_format": "wav" | "mp3" | "ogg" | "raw",  // validated; default "wav"
  "session_id": "string",                      // correlation id; nullable
  "created_at_ms": 1712900000000,              // epoch-ms
  "schema_version": 1
}
```

Empty/whitespace-only `text` is rejected (`ValueError`). Unknown `output_format` values are rejected fail-closed (`ValueError`).

### `TTSResponse`

Frozen dataclass. Built via `build_tts_response(...)` or `build_tts_unavailable_response(...)`.

```jsonc
{
  "request_id": "uuid-string",
  "status": "succeeded" | "failed" | "unavailable" | "unsupported",
  "audio_path": "/path/to/output.wav",         // nullable; artifact path on success
  "audio_duration_ms": 3500,                   // nullable; duration of output audio
  "error": "reason string",                    // nullable
  "error_class": "disabled" | "backend_missing" | "backend_error" | "timeout" | "unsupported_format" | "empty_text",
  "backend": "provider-name",                  // nullable
  "started_at_ms": 0,                          // nullable
  "finished_at_ms": 0,                         // nullable
  "schema_version": 1,
  "inference_ms": 150                          // nullable; adapter-reported synthesis time
}
```

Key distinction from STTResponse: the output artifact is a file path (`audio_path`), not a transcript string.

Unknown `status` values normalize fail-closed to `"unavailable"`.

`error_class` is intentionally not validated — backends may define their own error classes beyond the canonical constants. This matches the `CanonicalSkillResult.error_class` passthrough policy.

`tts_request_as_dict(request)` / `tts_response_as_dict(response)` serialize to plain dicts for JSON/logging/audit.

## STT backend adapter boundary

`voice/stt_adapter.py`. Runtime adapter interface for speech-to-text backends and the fail-soft transcription entry point. This is the protocol-to-runtime bridge layer — it consumes `STTRequest` and returns `STTResponse` through an explicit adapter boundary.

### `STTBackend` (Protocol)

Structural interface (mirrors the `Brain` protocol in `brain/base.py`). Implementations do not need to inherit — they only satisfy the structural signature.

```python
class STTBackend(Protocol):
    @property
    def backend_name(self) -> str: ...
    def supports_source(self, input_source: str) -> bool: ...
    def transcribe(self, request: STTRequest) -> STTAdapterResult: ...
```

`supports_source(input_source)` allows callers to check source support upfront (e.g. for UI gating) without triggering a full transcription attempt.

### `STTAdapterResult`

Frozen dataclass. Adapter-internal result shape returned by `STTBackend.transcribe()`.

```jsonc
{
  "transcript": "transcribed text",            // nullable
  "language": "en-US",                         // nullable
  "error": "reason string",                    // nullable
  "error_class": "custom_error",               // nullable; passthrough
  "inference_ms": 150,                         // nullable; adapter-reported inference time
  "audio_duration_ms": 3500                    // nullable; adapter-reported audio duration
}
```

### `NullSTTBackend`

Default adapter when no real backend is configured. Always returns an honest `STTAdapterResult` with `error_class="backend_missing"` — never pretends transcription occurred. `supports_source()` returns `False` for all sources. Accepts an optional `reason` keyword argument at construction to distinguish "not configured" from "unrecognized backend" in error messages (default: `"No STT backend is configured"`).

### `WhisperLocalBackend`

First real STT backend. Uses `faster-whisper` (CTranslate2-based Whisper) for local audio file transcription. See `voice/whisper_backend.py`.

- Supports `audio_file` only. `microphone` and `stream` are explicitly unsupported.
- Lazy model loading on first `transcribe()` call.
- Optional dependency: install with `pip install voxera-os[whisper]`.
- Configuration via env vars: `VOXERA_VOICE_STT_WHISPER_MODEL` (default: `base`), `VOXERA_VOICE_STT_WHISPER_DEVICE` (default: `auto`), `VOXERA_VOICE_STT_WHISPER_COMPUTE_TYPE` (default: `int8`).
- Missing `faster-whisper` dependency returns truthful `backend_missing` — never crashes.
- Reports `inference_ms` and `audio_duration_ms` timing fields when transcription succeeds.

### `transcribe_stt_request(request, adapter=None) -> STTResponse`

Canonical fail-soft transcription entry point. Never raises. Returns a truthful `STTResponse` for every path:

### `transcribe_stt_request_async(request, adapter=None) -> STTResponse`

Async wrapper around `transcribe_stt_request`. Runs the synchronous backend path in a thread via `asyncio.to_thread()`. Preserves all fail-soft semantics.

| Condition | Status | Error class |
|---|---|---|
| No adapter (`None`) | `unavailable` | `backend_missing` |
| Adapter raises `STTBackendUnsupportedError` | `unsupported` | `unsupported_source` |
| Adapter raises unexpected exception | `failed` | `backend_error` |
| Adapter result with availability error (`disabled`, `backend_missing`) | `unavailable` | passthrough |
| Adapter result with runtime error (any other `error_class`) | `failed` | passthrough |
| Empty/whitespace transcript after normalization | `failed` | `empty_audio` |
| Valid transcript | `succeeded` | (none) |

Transcript normalization reuses `voice/input.py::normalize_transcript_text()`.

### `STTBackendUnsupportedError`

Exception raised by adapters when they do not support the requested input source. Caught by `transcribe_stt_request` and mapped to an `unsupported` response.

## STT backend factory

`voice/stt_backend_factory.py`. Runtime backend selection from `VoiceFoundationFlags`.

### `build_stt_backend(flags) -> STTBackend`

Maps `VoiceFoundationFlags.voice_stt_backend` to the appropriate `STTBackend` implementation. Returns `NullSTTBackend` when voice input is disabled, no backend is configured, or the backend identifier is unrecognized. Returns `WhisperLocalBackend` when `voice_stt_backend` is `"whisper_local"` (case-insensitive, whitespace-trimmed).

Supported backend identifiers:

| Identifier | Backend | Notes |
|---|---|---|
| `"whisper_local"` | `WhisperLocalBackend` | Local Whisper via faster-whisper |
| (empty / None / unrecognized) | `NullSTTBackend` | Truthful unavailable |

### `transcribe_audio_file(audio_path, flags, ..., backend=None) -> STTResponse`

`voice/input.py`. Recommended entry point for audio-file transcription through the canonical STT pipeline. Builds an `STTRequest` with `input_source="audio_file"`, selects the backend via `build_stt_backend(flags)` (or uses a caller-supplied `backend`), and runs the request through `transcribe_stt_request()`. Always returns a truthful `STTResponse`. Only `audio_file` is supported — microphone and stream remain future work.

Pass a pre-built `backend` to reuse an existing `STTBackend` instance across calls — this avoids re-constructing the backend (and potentially re-loading heavy models like Whisper) on every invocation.

### `transcribe_audio_file_async(audio_path, flags, ..., backend=None) -> STTResponse`

`voice/input.py`. Async variant of `transcribe_audio_file`. Runs the synchronous transcription in a thread via `asyncio.to_thread()` so it does not block the event loop. Use from async contexts (Vera chat, FastAPI routes). Preserves all fail-soft semantics.

## STT status surface

`voice/stt_status.py`. Observable status surface for speech-to-text configuration and availability. Symmetric with the TTS status surface. `available=true` means the subsystem is configured and enabled, NOT that transcription has been tested or will succeed.

### `STTStatus`

Frozen dataclass. Built via `build_stt_status(flags)`.

```jsonc
{
  "configured": true,                          // backend string is present
  "available": true,                           // foundation + input enabled + configured
  "enabled": true,                             // foundation + input enabled
  "backend": "provider-name",                  // nullable
  "status": "available" | "unconfigured" | "disabled",
  "reason": "voice_foundation_disabled" | "voice_input_disabled" | "voice_stt_backend_not_configured" | null,
  "schema_version": 1
}
```

`stt_status_as_dict(status)` serializes to a plain dict for JSON/health payloads.

## TTS status surface

`voice/tts_status.py`. Observable status surface for text-to-speech configuration and availability. This is a truthful status surface — `available=true` means the subsystem is configured and enabled, NOT that synthesis has been tested or will succeed.

### `TTSStatus`

Frozen dataclass. Built via `build_tts_status(flags, *, last_error=None)`.

```jsonc
{
  "configured": true,                          // backend string is present
  "available": true,                           // foundation + output enabled + configured
  "enabled": true,                             // foundation + output enabled
  "backend": "provider-name",                  // nullable
  "status": "available" | "unconfigured" | "disabled" | "unavailable",
  "reason": "voice_foundation_disabled" | "voice_output_disabled" | "voice_tts_backend_not_configured" | null,
  "last_error": "string",                      // nullable passthrough from health
  "schema_version": 1
}
```

`tts_status_as_dict(status)` serializes to a plain dict for JSON/health payloads.

## `job` refs, stems, and ids

- Canonical job id is the queue filename stem (`<job>.json` → `<job>`).
- Artifacts and sidecars resolve by that stem.
- Lineage links jobs without replacing per-job truth.

When referring to a job externally (CLI, panel URL, Vera chat), the stem is the stable reference. `core/queue_inspect.py::lookup_job(...)` resolves the stem back to the current bucket.
