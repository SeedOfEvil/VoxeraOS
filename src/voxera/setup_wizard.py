from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from .config import capabilities_report_path, default_config_path, save_config, save_policy
from .models import AppConfig, BrainConfig, PolicyApprovals, PrivacyConfig
from .paths import ensure_dirs
from .secrets import set_secret

console = Console()

_ModeLiteral = Literal["voice", "gui", "cli", "mixed"]
_BrainTypeLiteral = Literal["gemini", "openai_compat"]


@dataclass(frozen=True)
class ProviderChoice:
    slug: str
    label: str
    env_ref: str
    brain_type: _BrainTypeLiteral
    default_model: str


def _pick_mode() -> _ModeLiteral:
    console.print(Panel("Choose interaction mode", title="Voxera Setup"))
    return cast(
        _ModeLiteral,
        Prompt.ask("Mode", choices=["voice", "gui", "cli", "mixed"], default="mixed"),
    )


def _pick_brain_type() -> str:
    console.print(Panel("Choose where Vera (the brain) runs", title="Brain Source"))
    return Prompt.ask("Brain", choices=["local", "cloud"], default="cloud")


def _cloud_provider() -> str:
    return Prompt.ask(
        "Cloud provider adapter",
        choices=["gemini", "openai_compat", "openrouter"],
        default="openrouter",
    )


def _local_provider() -> str:
    return Prompt.ask("Local adapter", choices=["openai_compat"], default="openai_compat")


def _validated_brain_type(value: str) -> _BrainTypeLiteral:
    if value not in {"gemini", "openai_compat"}:
        raise ValueError(f"Unsupported brain type: {value}")
    return cast(_BrainTypeLiteral, value)


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


def _configure_openrouter_brains(cfg: AppConfig, api_key_ref: str) -> None:
    console.print(
        Panel(
            "OpenRouter recommended setup: register app headers + multiple model tiers.",
            title="OpenRouter",
        )
    )
    base_url = "https://openrouter.ai/api/v1"
    referer = Prompt.ask("HTTP-Referer header (your app URL)", default="https://localhost")
    title = Prompt.ask("X-Title header (your app name)", default="Voxera OS")
    headers = {"HTTP-Referer": referer, "X-Title": title}

    fast_model = Prompt.ask("Fast/general model", default="google/gemini-2.5-flash")
    balanced_model = Prompt.ask("Balanced quality model", default="openai/gpt-4o-mini")
    reasoning_model = Prompt.ask("Deep reasoning model", default="anthropic/claude-3.7-sonnet")
    fallback_model = Prompt.ask("Fallback/cost model", default="meta-llama/llama-3.3-70b-instruct")

    cfg.brain["primary"] = BrainConfig(
        type="openai_compat",
        model=balanced_model,
        base_url=base_url,
        api_key_ref=api_key_ref,
        extra_headers=headers,
    )
    cfg.brain["fast"] = BrainConfig(
        type="openai_compat",
        model=fast_model,
        base_url=base_url,
        api_key_ref=api_key_ref,
        extra_headers=headers,
    )
    cfg.brain["reasoning"] = BrainConfig(
        type="openai_compat",
        model=reasoning_model,
        base_url=base_url,
        api_key_ref=api_key_ref,
        extra_headers=headers,
    )
    cfg.brain["fallback"] = BrainConfig(
        type="openai_compat",
        model=fallback_model,
        base_url=base_url,
        api_key_ref=api_key_ref,
        extra_headers=headers,
    )


def _provider_catalog() -> list[ProviderChoice]:
    return [
        ProviderChoice(
            slug="openrouter",
            label="OpenRouter",
            env_ref="OPENROUTER_API_KEY",
            brain_type="openai_compat",
            default_model="openai/gpt-4o-mini",
        ),
        ProviderChoice(
            slug="openai",
            label="OpenAI",
            env_ref="OPENAI_API_KEY",
            brain_type="openai_compat",
            default_model="gpt-4o-mini",
        ),
        ProviderChoice(
            slug="anthropic",
            label="Anthropic",
            env_ref="ANTHROPIC_API_KEY",
            brain_type="openai_compat",
            default_model="anthropic/claude-3.7-sonnet",
        ),
        ProviderChoice(
            slug="google",
            label="Google/Gemini",
            env_ref="GOOGLE_API_KEY",
            brain_type="gemini",
            default_model="gemini-2.5-flash",
        ),
        ProviderChoice(
            slug="gemini",
            label="Gemini (legacy env)",
            env_ref="GEMINI_API_KEY",
            brain_type="gemini",
            default_model="gemini-2.5-flash",
        ),
    ]


def _apply_provider_key_choice(provider: ProviderChoice, *, existing_ref: str | None) -> str | None:
    has_existing_ref = bool(existing_ref)
    default_choice = "keep" if has_existing_ref else "skip"
    choice = Prompt.ask(
        f"{provider.label} auth",
        choices=["keep", "skip", "replace"],
        default=default_choice,
        show_choices=False,
    )
    if choice == "keep":
        return existing_ref
    if choice == "skip":
        return None

    key_value = Prompt.ask(
        f"Enter {provider.env_ref} value (stored securely when possible)",
        password=True,
    )
    set_secret(provider.env_ref, key_value)
    console.print(
        f"Stored {provider.env_ref}. You can also export it in your shell if preferred for runtime."
    )
    return provider.env_ref


def _configure_provider_catalog(cfg: AppConfig) -> None:
    lines = []
    for p in _provider_catalog():
        lines.append(f"- {p.label}: {p.env_ref}")
    console.print(
        Panel(
            "Provider catalog (provider/model naming is supported via openai_compat adapters):\n"
            + "\n".join(lines),
            title="Providers",
        )
    )

    primary = Prompt.ask(
        "Primary provider",
        choices=[p.slug for p in _provider_catalog()],
        default="openrouter",
    )
    selected = {p.slug: p for p in _provider_catalog()}[primary]

    model = Prompt.ask("Primary model", default=selected.default_model)
    existing_primary = cfg.brain.get("primary")
    ref = _apply_provider_key_choice(
        provider=selected, existing_ref=existing_primary.api_key_ref if existing_primary else None
    )
    if selected.slug == "openrouter":
        if ref:
            _configure_openrouter_brains(cfg, ref)
        else:
            cfg.brain["primary"] = BrainConfig(
                type="openai_compat",
                model=model,
                base_url="https://openrouter.ai/api/v1",
            )
    elif selected.brain_type == "gemini":
        cfg.brain["primary"] = BrainConfig(type="gemini", model=model, api_key_ref=ref)
    else:
        base_url = Prompt.ask("OpenAI-compatible base URL", default="https://api.openai.com/v1")
        cfg.brain["primary"] = BrainConfig(
            type="openai_compat",
            model=model,
            base_url=base_url,
            api_key_ref=ref,
        )


def _confirm_write_config(path: Path) -> bool:
    if not path.exists():
        return True
    console.print(
        Panel(
            "Existing app config detected. Default is to KEEP current config unchanged.",
            title="Config Safety",
        )
    )
    return Confirm.ask("Overwrite existing config.yml with setup answers?", default=False)


def _print_what_next() -> None:
    console.print(
        Panel(
            "What you can do next:\n"
            "- voxera demo  # offline, safe + repeatable\n"
            "- voxera demo --online  # opt-in provider readiness\n"
            "- voxera queue status\n"
            "- voxera queue reconcile\n"
            "- voxera queue reconcile --fix\n"
            "- voxera queue prune\n"
            "- voxera artifacts prune\n"
            "- voxera doctor --quick\n"
            "- voxera doctor --self-test  # optional deeper checks\n\n"
            "Daemon reliability features: single-writer lock, graceful shutdown, and startup recovery.",
            title="Next Steps",
        )
    )


async def run_setup() -> AppConfig:
    ensure_dirs()
    mode = _pick_mode()
    brain_source = _pick_brain_type()

    config_path = default_config_path()
    existing = AppConfig()
    if config_path.exists():
        try:
            obj = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            existing = AppConfig.model_validate(obj)
        except Exception:
            existing = AppConfig()

    cfg = AppConfig(mode=cast(_ModeLiteral, mode), brain=dict(existing.brain))

    console.print(
        Panel(
            "Config separation:\n"
            "- App/brain config: ~/.config/voxera/config.yml (written by setup)\n"
            "- Runtime ops config: ~/.config/voxera/config.json (optional, operator-managed)",
            title="Config Files",
        )
    )

    if brain_source == "cloud":
        _ = _cloud_provider()
        _configure_provider_catalog(cfg)
    else:
        adapter = _local_provider()
        base_url = Prompt.ask("Local base URL", default="http://localhost:11434/v1")
        model = Prompt.ask("Local model", default="llama3")
        cfg.brain["primary"] = BrainConfig(
            type=_validated_brain_type(adapter), model=model, base_url=base_url
        )

    console.print(Panel("Privacy posture", title="Privacy"))
    privacy = PrivacyConfig()
    privacy.cloud_allowed = Confirm.ask("Allow cloud calls for complex requests?", default=True)
    privacy.redact_logs = Confirm.ask("Redact logs (recommended)", default=True)
    cfg.privacy = privacy

    cfg.policy = _policy_defaults()

    if _confirm_write_config(config_path):
        save_config(cfg, path=config_path)
    else:
        console.print("Keeping existing config.yml unchanged.")
        cfg = existing

    save_policy({"approvals": cfg.policy.model_dump()})

    cap = {
        "ts": __import__("time").time(),
        "mode": cfg.mode,
        "brain": {k: v.model_dump() for k, v in cfg.brain.items()},
        "note": "Run 'voxera doctor' to execute live capability tests.",
    }
    capabilities_report_path().write_text(json.dumps(cap, indent=2), encoding="utf-8")

    console.print("\n✅ Setup complete.")
    console.print("App config (brain/mode/privacy): ~/.config/voxera/config.yml")
    console.print("Runtime ops config (panel/queue, optional): ~/.config/voxera/config.json")
    console.print("Policy: ~/.config/voxera/policy.yml")
    console.print("Capabilities: ~/.local/share/voxera/capabilities.json\n")
    _print_what_next()
    return cfg
