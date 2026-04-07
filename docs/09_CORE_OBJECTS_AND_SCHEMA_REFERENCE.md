# 09 — Core Objects and Schema Reference

This document is a reference for the canonical typed objects in VoxeraOS.
For machine-readable detail see `class_inventory.json`,
`function_inventory.json`, and `module_inventory.json`.

## Configuration models (`src/voxera/models.py`)

All configuration models use `pydantic.BaseModel` with
`ConfigDict(extra="forbid")` (strict, fail-closed).

### `BrainConfig`

| Field | Type | Notes |
| ----- | ---- | ----- |
| `type` | `Literal["gemini", "openai_compat"]` | Required. |
| `model` | `str` | Required. |
| `base_url` | `str | None` | For `openai_compat` providers. |
| `api_key_ref` | `str | None` | Keyring ref name. |
| `extra_headers` | `dict[str, str]` | Provider-specific headers. |

### `PolicyApprovals`

All fields are `Literal["allow", "ask", "deny"]`.

| Field | Default |
| ----- | ------- |
| `network_changes` | `ask` |
| `installs` | `ask` |
| `file_delete` | `ask` |
| `open_apps` | `allow` |
| `system_settings` | `ask` |

### `PrivacyConfig`

| Field | Type | Default |
| ----- | ---- | ------- |
| `cloud_allowed` | `bool` | `True` |
| `redact_logs` | `bool` | `True` |

### `WebInvestigationConfig`

| Field | Type | Default |
| ----- | ---- | ------- |
| `provider` | `Literal["brave"]` | `"brave"` |
| `api_key_ref` | `str | None` | `None` |
| `env_api_key_var` | `str` | `"BRAVE_API_KEY"` |
| `max_results` | `int` (1..10) | `5` |

### `AppConfig`

| Field | Type | Default |
| ----- | ---- | ------- |
| `mode` | `Literal["voice","gui","cli","mixed"]` | `"mixed"` |
| `max_replan_attempts` | `int` | `1` |
| `brain` | `dict[str, BrainConfig]` | `{}` |
| `policy` | `PolicyApprovals` | default |
| `privacy` | `PrivacyConfig` | default |
| `web_investigation` | `WebInvestigationConfig | None` | `None` |
| `skills_path` | `str | None` | `None` |
| `sandbox_image` | `str` | `"docker.io/library/ubuntu:24.04"` |
| `sandbox_memory` | `str` | `"512m"` |
| `sandbox_cpus` | `float` | `1.0` |
| `sandbox_pids_limit` | `int` | `256` |

### `SkillManifest`

| Field | Type | Default |
| ----- | ---- | ------- |
| `id` | `str` (non-empty) | required |
| `name` | `str` (non-empty) | required |
| `description` | `str` (non-empty) | required |
| `entrypoint` | `str` (`module:function`) | required |
| `capabilities` | `list[str]` | `[]` |
| `risk` | `Literal["low","medium","high"]` | `"low"` |
| `exec_mode` | `Literal["local","sandbox"]` | `"local"` |
| `needs_network` | `bool` | `False` |
| `fs_scope` | `Literal["workspace_only","read_only","broader"]` | `"workspace_only"` |
| `output_artifacts` | `list[str]` (unique non-empty) | `[]` |
| `output_schema` | `str | None` | `None` |
| `args` | `dict[str, dict[str, Any]]` | `{}` |

Field validators enforce non-empty strings, unique output artifacts, and
the `module:function` shape of `entrypoint`.

### Plan and Run models

| Class | Notes |
| ----- | ----- |
| `PlanStep` | `action`, `skill_id`, `args`, `requires_approval`, `capability`, `risk`, `policy_decision`, `reason`. |
| `PlanSimulation` | `title`, `goal`, `steps`, `approvals_required`. |
| `Plan` | Full plan object. |
| `RunResult` | Result of a single run/step. |

## Queue object model (`src/voxera/core/queue_object_model.py`)

```python
QueueLifecycleState = Literal[
    "queued", "planning", "running", "awaiting_approval", "resumed",
    "advisory_running", "done", "failed", "step_failed", "blocked", "canceled",
]

QUEUE_LIFECYCLE_STATES: frozenset[str] = frozenset({...})

COMPLETED_AT_LIFECYCLE_STATES: frozenset[str] = frozenset({
    "done", "failed", "step_failed", "blocked", "canceled",
})

TerminalOutcome = Literal["succeeded", "failed", "blocked", "denied", "canceled"]
TERMINAL_OUTCOMES: frozenset[str]

ArtifactFamily = Literal[
    "plan", "actions", "stdout", "stderr", "review_summary", "approval",
    "evidence_bundle", "execution_envelope", "execution_result",
    "step_results", "assistant_advisory", "job_intent",
]
ARTIFACT_FAMILIES: frozenset[str]

TRUTH_SURFACES: dict[str, str] = {
    "conversation": "interaction aid only; never authoritative for runtime outcomes",
    "preview":      "authoritative draft state before submit",
    "queue":        "authoritative submitted lifecycle/progression state",
    "artifact_evidence": "authoritative runtime-grounded post-execution outcome proof",
}
```

## Queue contracts (`src/voxera/core/queue_contracts.py`)

Canonical payload fields:

```
mission_id, goal, title, steps, enqueue_child, write_file,
file_organize, approval_required, _simple_intent, lineage, job_intent
```

Schema versions:

```
EXECUTION_ENVELOPE = 1
STEP_RESULT        = 1
EXECUTION_RESULT   = 1
EVIDENCE_BUNDLE    = 1
REVIEW_SUMMARY     = 1
```

Request kinds: `mission_id`, `file_organize`, `goal`, `inline_steps`,
`unknown`.

## Execution capability model (`src/voxera/core/execution_capabilities.py`)

```python
class SideEffectClass(Enum):
    CLASS_A  # no side effects
    CLASS_B  # moderate side effects
    CLASS_C  # broad side effects

class FilesystemScope(Enum):
    NONE
    CONFINED  # workspace-only
    BROADER

class NetworkScope(Enum):
    NONE
    READ_ONLY
    BROADER

class SandboxProfile(Enum):
    HOST_LOCAL
    SANDBOX_NO_NETWORK
    SANDBOX_NETWORK_SCOPED

@dataclass(frozen=True)
class ExecutionCapabilityDeclaration:
    side_effect_class: SideEffectClass
    filesystem_scope: FilesystemScope
    network_scope: NetworkScope
    sandbox_profile: SandboxProfile
    ...
```

## Capability semantics (`src/voxera/core/capability_semantics.py`)

Each capability declares:

- `effect_class`: `read | write | execute`
- `intent_class`: `read_only | mutating | destructive`
- `policy_field`: name of the `PolicyApprovals` field (when applicable)
- `resource_boundaries`: subset of `{filesystem, network, secrets, system}`
- `summary`: short operator-facing string

`manifest_capability_semantics(manifest)` returns the projected normalized
view used by the registry, policy engine, mission planner, and panel.

## Mission template (`src/voxera/core/missions.py`)

```python
@dataclass
class MissionStep:
    skill_id: str
    args: dict[str, Any]
    title: str | None = None

@dataclass
class MissionTemplate:
    id: str
    title: str
    goal: str
    steps: list[MissionStep]
    notes: str | None = None
```

## Vera session (`src/voxera/vera/session_store.py`)

Top-level fields persisted per `vera-<id>.json`:

- `session_id` (string, `vera-<24 hex>`)
- `created_at`, `updated_at`
- `turns: list[dict]` (max 8)
- `pending_job_preview: dict | None`
- `handoff: dict | None`
- `weather_context: dict | None`
- `linked_queue_jobs: list[dict]` (max 64)
- `shared_context: dict` (continuity object)
- `recent_saveable_assistant_artifacts: list[dict]` (max 8)
- `routing_debug: list[dict]`

## Voxera runtime config (`src/voxera/config.py`)

```python
@dataclass(frozen=True)
class VoxeraConfig:
    queue_root: Path
    panel_host: str
    panel_port: int
    panel_operator_user: str
    panel_operator_password: str | None
    panel_csrf_enabled: bool
    panel_enable_get_mutations: bool
    queue_lock_stale_s: float
    queue_failed_max_age_s: float | None
    queue_failed_max_count: int | None
    artifacts_retention_days: int | None
    artifacts_retention_max_count: int | None
    queue_prune_max_age_days: int | None
    ...
```

## Skill execution helpers (`src/voxera/skills/execution.py`)

```python
@dataclass
class JobPaths:
    job_id: str
    workspace_dir: Path
    artifacts_dir: Path

def generate_job_id() -> str: ...
def sanitize_command(cmd: list[str]) -> list[str]: ...
def sanitize_env(env: Mapping[str, str]) -> dict[str, str]: ...
```

Skill result envelope (`skills/result_contract.py`): `SKILL_RESULT_KEY`,
`build_skill_result(...)`. The standard output schema string is
`skill_result.v1`.
