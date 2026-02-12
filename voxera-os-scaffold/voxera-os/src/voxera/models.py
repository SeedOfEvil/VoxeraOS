from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal, Optional, Dict, Any, List

class BrainConfig(BaseModel):
    type: Literal["gemini", "openai_compat"]
    model: str
    base_url: Optional[str] = None  # for openai_compat
    api_key_ref: Optional[str] = None  # keyring ref name

class PolicyApprovals(BaseModel):
    network_changes: Literal["allow", "ask", "deny"] = "ask"
    installs: Literal["allow", "ask", "deny"] = "ask"
    file_delete: Literal["allow", "ask", "deny"] = "ask"
    open_apps: Literal["allow", "ask", "deny"] = "allow"
    system_settings: Literal["allow", "ask", "deny"] = "ask"

class PrivacyConfig(BaseModel):
    cloud_allowed: bool = True
    redact_logs: bool = True

class AppConfig(BaseModel):
    mode: Literal["voice", "gui", "cli", "mixed"] = "mixed"
    brain: Dict[str, BrainConfig] = Field(default_factory=dict)  # primary/fallback
    policy: PolicyApprovals = Field(default_factory=PolicyApprovals)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    skills_path: Optional[str] = None

class SkillManifest(BaseModel):
    id: str
    name: str
    description: str
    entrypoint: str  # python module:function
    capabilities: List[str] = Field(default_factory=list)
    risk: Literal["low", "medium", "high"] = "low"

class PlanStep(BaseModel):
    action: str
    skill_id: Optional[str] = None
    args: Dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = False
    capability: Optional[str] = None

class Plan(BaseModel):
    title: str
    goal: str
    steps: List[PlanStep]

class RunResult(BaseModel):
    ok: bool
    output: str = ""
    data: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
