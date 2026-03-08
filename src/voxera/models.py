from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BrainConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["gemini", "openai_compat"]
    model: str
    base_url: str | None = None  # for openai_compat
    api_key_ref: str | None = None  # keyring ref name
    extra_headers: dict[str, str] = Field(
        default_factory=dict
    )  # optional provider-specific headers


class PolicyApprovals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    network_changes: Literal["allow", "ask", "deny"] = "ask"
    installs: Literal["allow", "ask", "deny"] = "ask"
    file_delete: Literal["allow", "ask", "deny"] = "ask"
    open_apps: Literal["allow", "ask", "deny"] = "allow"
    system_settings: Literal["allow", "ask", "deny"] = "ask"


class PrivacyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cloud_allowed: bool = True
    redact_logs: bool = True


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["voice", "gui", "cli", "mixed"] = "mixed"
    brain: dict[str, BrainConfig] = Field(default_factory=dict)  # primary/fallback
    policy: PolicyApprovals = Field(default_factory=PolicyApprovals)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    skills_path: str | None = None
    sandbox_image: str = "docker.io/library/ubuntu:24.04"
    sandbox_memory: str = "512m"
    sandbox_cpus: float = 1.0
    sandbox_pids_limit: int = 256


class SkillManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str
    entrypoint: str  # python module:function
    capabilities: list[str] = Field(default_factory=list)
    risk: Literal["low", "medium", "high"] = "low"
    exec_mode: Literal["local", "sandbox"] = "local"
    needs_network: bool = False
    fs_scope: Literal["workspace_only", "read_only", "broader"] = "workspace_only"
    output_artifacts: list[str] = Field(default_factory=list)
    output_schema: str | None = None
    args: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @field_validator("id", "name", "description", "entrypoint")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must be a non-empty string")
        return text

    @field_validator("entrypoint")
    @classmethod
    def _entrypoint_shape(cls, value: str) -> str:
        if ":" not in value:
            raise ValueError("must use 'module:function' format")
        module_name, function_name = value.split(":", 1)
        if not module_name.strip() or not function_name.strip():
            raise ValueError("must use 'module:function' format")
        return value

    @field_validator("capabilities")
    @classmethod
    def _capabilities_must_be_non_empty_strings(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("items must be non-empty strings")
        return normalized

    @field_validator("output_artifacts")
    @classmethod
    def _output_artifacts_must_be_unique_non_empty_strings(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("items must be non-empty strings")
        if len(set(normalized)) != len(normalized):
            raise ValueError("items must be unique")
        return normalized

    @field_validator("output_schema")
    @classmethod
    def _output_schema_non_empty_if_present(cls, value: str | None) -> str | None:
        if value is None:
            return None
        schema = value.strip()
        if not schema:
            raise ValueError("must be a non-empty string when provided")
        return schema


class PlanStep(BaseModel):
    action: str
    skill_id: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = False
    capability: str | None = None
    risk: Literal["low", "medium", "high"] = "low"
    policy_decision: Literal["allow", "ask", "deny"] | None = None
    reason: str | None = None


class PlanSimulation(BaseModel):
    title: str
    goal: str
    steps: list[PlanStep]
    approvals_required: int = 0
    blocked: bool = False
    summary: str = ""
    # Compact metadata from the runtime capabilities snapshot used during planning.
    capabilities_snapshot: dict[str, Any] = Field(default_factory=dict)
    # Sorted, distinct capability strings referenced by planned steps.
    capabilities_used: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    title: str
    goal: str
    steps: list[PlanStep]


class RunResult(BaseModel):
    ok: bool
    output: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
