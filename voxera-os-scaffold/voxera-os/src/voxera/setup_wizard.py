from __future__ import annotations

import json
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.panel import Panel

from .models import AppConfig, BrainConfig, PolicyApprovals, PrivacyConfig
from .config import save_config, save_policy, capabilities_report_path
from .secrets import set_secret
from .paths import ensure_dirs

console = Console()

def _pick_mode() -> str:
    console.print(Panel("Choose interaction mode", title="Voxera Setup"))
    return Prompt.ask("Mode", choices=["voice", "gui", "cli", "mixed"], default="mixed")

def _pick_brain_type() -> str:
    console.print(Panel("Choose where Vera (the brain) runs", title="Brain Source"))
    return Prompt.ask("Brain", choices=["local", "cloud"], default="cloud")

def _cloud_provider() -> str:
    return Prompt.ask("Cloud provider adapter", choices=["gemini", "openai_compat"], default="gemini")

def _local_provider() -> str:
    return Prompt.ask("Local adapter", choices=["openai_compat"], default="openai_compat")

def _policy_defaults() -> PolicyApprovals:
    console.print(Panel("Safety policy defaults (you can edit later)", title="Policy"))
    p = PolicyApprovals()
    if Confirm.ask("Auto-allow opening apps?", default=True):
        p.open_apps = "allow"
    if Confirm.ask("Always ask before installs?", default=True):
        p.installs = "ask"
    if Confirm.ask("Always ask before network changes?", default=True):
        p.network_changes = "ask"
    if Confirm.ask("Always ask before deleting files?", default=True):
        p.file_delete = "ask"
    return p

async def run_setup() -> AppConfig:
    ensure_dirs()
    mode = _pick_mode()
    brain_source = _pick_brain_type()

    cfg = AppConfig(mode=mode)

    if brain_source == "cloud":
        adapter = _cloud_provider()
        model = Prompt.ask("Model name", default="gemini-3-flash")
        api_key = Prompt.ask("API key (will be stored securely)", password=True)
        ref_name = "CLOUD_API_KEY"
        set_secret(ref_name, api_key)
        cfg.brain["primary"] = BrainConfig(type=adapter, model=model, api_key_ref=ref_name)

        if Confirm.ask("Configure a local fallback (OpenAI-compatible endpoint)?", default=True):
            base_url = Prompt.ask("Local base URL", default="http://localhost:11434/v1")
            fb_model = Prompt.ask("Local model", default="llama3")
            cfg.brain["fallback"] = BrainConfig(type="openai_compat", model=fb_model, base_url=base_url)

    else:
        adapter = _local_provider()
        base_url = Prompt.ask("Local base URL", default="http://localhost:11434/v1")
        model = Prompt.ask("Local model", default="llama3")
        cfg.brain["primary"] = BrainConfig(type=adapter, model=model, base_url=base_url)

    console.print(Panel("Privacy posture", title="Privacy"))
    privacy = PrivacyConfig()
    privacy.cloud_allowed = Confirm.ask("Allow cloud calls for complex requests?", default=True)
    privacy.redact_logs = Confirm.ask("Redact logs (recommended)", default=True)
    cfg.privacy = privacy

    cfg.policy = _policy_defaults()

    save_config(cfg)
    save_policy({"approvals": cfg.policy.model_dump()})

    cap = {
        "ts": __import__("time").time(),
        "mode": cfg.mode,
        "brain": {k: v.model_dump() for k, v in cfg.brain.items()},
        "note": "Run 'voxera doctor' to execute live capability tests.",
    }
    capabilities_report_path().write_text(json.dumps(cap, indent=2), encoding="utf-8")

    console.print("\n✅ Setup complete.")
    console.print("Config: ~/.config/voxera/config.yml")
    console.print("Policy: ~/.config/voxera/policy.yml")
    console.print("Capabilities: ~/.local/share/voxera/capabilities.json\n")
    return cfg
