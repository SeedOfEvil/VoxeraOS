from __future__ import annotations

import json
import subprocess
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from .config import capabilities_report_path, default_config_path, save_config, save_policy
from .models import AppConfig, BrainConfig, PolicyApprovals, PrivacyConfig
from .openrouter_catalog import (
    grouped_catalog,
    load_curated_openrouter_catalog,
    recommended_model_for_slot,
)
from .paths import ensure_dirs

console = Console()

_ModeLiteral = Literal["voice", "gui", "cli", "mixed"]
_BrainTypeLiteral = Literal["gemini", "openai_compat"]
_BrainSlotLiteral = Literal["primary", "fast", "reasoning", "fallback"]


@dataclass(frozen=True)
class ProviderChoice:
    slug: str
    label: str
    env_ref: str
    brain_type: _BrainTypeLiteral
    default_model: str
    default_base_url: str | None = None


@dataclass(frozen=True)
class BrainSlotSpec:
    key: _BrainSlotLiteral
    title: str
    description: str


BRAIN_SLOT_ORDER: tuple[BrainSlotSpec, ...] = (
    BrainSlotSpec("primary", "Primary brain", "Default balanced brain for most tasks."),
    BrainSlotSpec(
        "fast", "Fast brain", "Lower-latency model for quick checks and lightweight tasks."
    ),
    BrainSlotSpec(
        "reasoning", "Reasoning brain", "Higher-depth model for difficult planning and analysis."
    ),
    BrainSlotSpec(
        "fallback",
        "Fallback brain",
        "Resilience slot when previous choices fail or are unavailable.",
    ),
)


def _pick_mode() -> _ModeLiteral:
    console.print(Panel("Choose interaction mode", title="Voxera Setup"))
    return cast(
        _ModeLiteral,
        Prompt.ask("Mode", choices=["voice", "gui", "cli", "mixed"], default="mixed"),
    )


def _pick_brain_type() -> str:
    console.print(Panel("Choose where Vera (the brain) runs", title="Brain Source"))
    return Prompt.ask("Brain", choices=["local", "cloud"], default="cloud")


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


def _provider_catalog() -> list[ProviderChoice]:
    return [
        ProviderChoice(
            slug="openrouter",
            label="OpenRouter",
            env_ref="OPENROUTER_API_KEY",
            brain_type="openai_compat",
            default_model="openai/gpt-4o-mini",
            default_base_url="https://openrouter.ai/api/v1",
        ),
        ProviderChoice(
            slug="openai",
            label="OpenAI",
            env_ref="OPENAI_API_KEY",
            brain_type="openai_compat",
            default_model="gpt-4o-mini",
            default_base_url="https://api.openai.com/v1",
        ),
        ProviderChoice(
            slug="anthropic",
            label="Anthropic",
            env_ref="ANTHROPIC_API_KEY",
            brain_type="openai_compat",
            default_model="anthropic/claude-3.7-sonnet",
            default_base_url="https://api.anthropic.com/v1",
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
    if existing_ref:
        choice = Prompt.ask(
            Text(f"Auth for {provider.label} [keep/skip/replace]"),
            choices=["keep", "skip", "replace"],
            default="keep",
            show_choices=False,
        )
        if choice == "keep":
            return existing_ref
        if choice == "skip":
            return None
        replacement_ref = Prompt.ask(
            f"Enter env/key reference for {provider.label}",
            default=provider.env_ref,
        ).strip()
        return replacement_ref or provider.env_ref

    choice = Prompt.ask(
        Text(f"Auth for {provider.label} [skip/set]"),
        choices=["skip", "set"],
        default="skip",
        show_choices=False,
    )
    if choice == "skip":
        return None
    new_ref = Prompt.ask(
        f"Enter env/key reference for {provider.label}",
        default=provider.env_ref,
    ).strip()
    return new_ref or provider.env_ref


def _provider_summary_table() -> Table:
    table = Table(title="Supported Providers")
    table.add_column("Provider")
    table.add_column("Adapter")
    table.add_column("Default key ref")
    for provider in _provider_catalog():
        table.add_row(provider.slug, provider.brain_type, provider.env_ref)
    return table


def _choose_numbered_option(items: list[str], *, prompt: str, default_index: int = 1) -> int:
    for index, item in enumerate(items, start=1):
        console.print(f"  {index}. {item}")
    choice = Prompt.ask(
        prompt,
        choices=[str(i) for i in range(1, len(items) + 1)],
        default=str(default_index),
    )
    return int(choice) - 1


def _pick_openrouter_model(slot_key: _BrainSlotLiteral) -> str:
    recommended_id = recommended_model_for_slot(slot_key)
    catalog = load_curated_openrouter_catalog()
    by_vendor = grouped_catalog(catalog)
    model_lookup = {str(model["id"]): model for model in catalog}

    recommended = model_lookup.get(recommended_id)
    recommended_name = str(recommended["name"]) if recommended else recommended_id
    recommended_vendor = str(recommended["vendor"]) if recommended else "OpenAI"

    console.print(
        Panel(
            f"Recommended for {slot_key}: {recommended_id} ({recommended_name})\n"
            f"Vendor group: {recommended_vendor}",
            title=f"OpenRouter model ({slot_key})",
        )
    )

    mode = Prompt.ask(
        "Model selection mode",
        choices=["recommended", "browse", "manual"],
        default="recommended",
    )
    if mode == "recommended":
        return recommended_id
    if mode == "manual":
        return (
            Prompt.ask("Enter OpenRouter model id", default=recommended_id).strip()
            or recommended_id
        )

    vendor_labels = [f"{vendor} ({len(models)} models)" for vendor, models in by_vendor]
    default_vendor_index = 1
    for idx, (vendor, _) in enumerate(by_vendor, start=1):
        if vendor == recommended_vendor:
            default_vendor_index = idx
            break

    vendor_idx = _choose_numbered_option(
        vendor_labels,
        prompt="Choose vendor group",
        default_index=default_vendor_index,
    )
    vendor, vendor_models = by_vendor[vendor_idx]

    model_lines: list[str] = []
    for model in vendor_models:
        pricing = "-"
        if model.get("pricing_prompt") or model.get("pricing_completion"):
            pricing = (
                f"p:{model.get('pricing_prompt') or '-'} c:{model.get('pricing_completion') or '-'}"
            )
        model_lines.append(
            f"{model['id']} — {model['name']} (ctx={model.get('context_length') or '-'}, {pricing})"
        )

    default_model_index = 1
    for idx, model in enumerate(vendor_models, start=1):
        if model["id"] == recommended_id:
            default_model_index = idx
            break

    model_idx = _choose_numbered_option(
        model_lines,
        prompt=f"Choose model from {vendor}",
        default_index=default_model_index,
    )
    return str(vendor_models[model_idx]["id"])


def _configure_brain_slot(
    cfg: AppConfig,
    *,
    slot: BrainSlotSpec,
    existing: BrainConfig | None,
) -> None:
    console.print(Panel(slot.description, title=f"{slot.title} ({slot.key})"))
    providers = _provider_catalog()
    provider_map = {provider.slug: provider for provider in providers}
    selected_slug = Prompt.ask(
        f"Provider for {slot.key}",
        choices=list(provider_map.keys()),
        default="openrouter",
    )
    provider = provider_map[selected_slug]

    existing_ref = existing.api_key_ref if existing else None
    api_key_ref = _apply_provider_key_choice(provider, existing_ref=existing_ref)

    if provider.slug == "openrouter":
        model = _pick_openrouter_model(slot.key)
        brain = BrainConfig(
            type="openai_compat",
            model=model,
            base_url="https://openrouter.ai/api/v1",
            api_key_ref=api_key_ref,
        )
    elif provider.brain_type == "gemini":
        model = Prompt.ask(f"Model for {slot.key}", default=provider.default_model)
        brain = BrainConfig(type="gemini", model=model, api_key_ref=api_key_ref)
    else:
        model = Prompt.ask(f"Model for {slot.key}", default=provider.default_model)
        base_url = Prompt.ask(
            f"Base URL for {slot.key}",
            default=provider.default_base_url or "https://api.openai.com/v1",
        )
        brain = BrainConfig(
            type="openai_compat",
            model=model,
            base_url=base_url,
            api_key_ref=api_key_ref,
        )

    console.print(
        Panel(
            f"Provider: {provider.label}\nAdapter: {brain.type}\nModel: {brain.model}\n"
            f"Base URL: {brain.base_url or '(n/a)'}\nAPI key ref: {brain.api_key_ref or '(none)'}",
            title=f"Confirm {slot.key}",
        )
    )
    if Confirm.ask(f"Save this {slot.key} slot?", default=True):
        cfg.brain[slot.key] = brain
    else:
        console.print(f"[yellow]{slot.key} kept unchanged.[/yellow]")


def _configure_cloud_brains(cfg: AppConfig) -> None:
    console.print(_provider_summary_table())
    for slot in BRAIN_SLOT_ORDER:
        _configure_brain_slot(cfg, slot=slot, existing=cfg.brain.get(slot.key))


RUNTIME_SERVICE_UNITS: tuple[str, ...] = (
    "voxera-daemon.service",
    "voxera-panel.service",
    "voxera-vera.service",
)


def _systemctl_user(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args],
        check=False,
        text=True,
        capture_output=True,
    )


def _ensure_runtime_services_running() -> dict[str, list[str]]:
    started: list[str] = []
    failed: list[str] = []

    reload_result = _systemctl_user("daemon-reload")
    if reload_result.returncode != 0:
        console.print(
            "[yellow]Could not run 'systemctl --user daemon-reload'. "
            "Service startup may fail in this environment.[/yellow]"
        )

    for unit in RUNTIME_SERVICE_UNITS:
        enable_start = _systemctl_user("enable", "--now", unit)
        if enable_start.returncode != 0:
            failed.append(unit)
            detail = (enable_start.stderr or enable_start.stdout or "").strip()
            console.print(f"[yellow]Failed to start {unit}:[/yellow] {detail or 'unknown error'}")
            continue

        active = _systemctl_user("is-active", "--quiet", unit)
        if active.returncode == 0:
            started.append(unit)
        else:
            failed.append(unit)
            console.print(
                f"[yellow]{unit} was enabled/started but is not active yet. "
                f"Check 'systemctl --user status {unit}'.[/yellow]"
            )

    if started:
        console.print("Runtime services running: " + ", ".join(started))
    if failed:
        console.print("[yellow]Runtime services not ready:[/yellow] " + ", ".join(failed))
    return {"started": started, "failed": failed}


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


def _launch_choice(*, service_state: dict[str, list[str]]) -> None:
    console.print(
        Panel(
            "Setup is complete. You can optionally open local panels now:\n"
            "- VoxeraOS: http://127.0.0.1:8844/\n"
            "- Vera:     http://127.0.0.1:8790/",
            title="Finish",
        )
    )
    choice = Prompt.ask(
        "Open panel(s)",
        choices=["voxera", "vera", "both", "none"],
        default="none",
    )
    urls: list[str] = []
    failed = set(service_state.get("failed", []))
    if choice in {"voxera", "both"}:
        if "voxera-panel.service" in failed:
            console.print(
                "[yellow]Skipping Voxera panel open because voxera-panel.service is not running.[/yellow]"
            )
        else:
            urls.append("http://127.0.0.1:8844/")
    if choice in {"vera", "both"}:
        if "voxera-vera.service" in failed:
            console.print(
                "[yellow]Skipping Vera panel open because voxera-vera.service is not running.[/yellow]"
            )
        else:
            urls.append("http://127.0.0.1:8790/")
    for url in urls:
        try:
            opened = webbrowser.open(url)
            if opened:
                console.print(f"Opened {url}")
            else:
                console.print(f"[yellow]Could not auto-open {url}. Open it manually.[/yellow]")
        except Exception as exc:
            console.print(f"[yellow]Launch failed for {url}:[/yellow] {exc}")


def _print_what_next() -> None:
    console.print(
        Panel(
            "What you can do next:\n"
            "- voxera demo  # guided offline onboarding check\n"
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
        _configure_cloud_brains(cfg)
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
        "ts": time.time(),
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
    service_state = _ensure_runtime_services_running()
    _launch_choice(service_state=service_state)
    _print_what_next()
    return cfg
