from __future__ import annotations

import json
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import httpx
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from .config import capabilities_report_path, default_config_path, save_config, save_policy
from .models import AppConfig, BrainConfig, PolicyApprovals, PrivacyConfig
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
class OpenRouterModel:
    model_id: str
    name: str
    context_length: int | None
    pricing_prompt: str | None
    pricing_completion: str | None
    supported_parameters: tuple[str, ...]


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
    has_existing_ref = bool(existing_ref)
    if has_existing_ref:
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


def _fetch_openrouter_models() -> list[OpenRouterModel]:
    response = httpx.get("https://openrouter.ai/api/v1/models", timeout=10.0)
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ValueError(
            "OpenRouter models API returned invalid payload shape: expected top-level data[]"
        )

    models: list[OpenRouterModel] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        model_id = str(entry.get("id") or "").strip()
        if not model_id:
            continue
        name = str(entry.get("name") or model_id)
        context_raw = entry.get("context_length")
        context_length = context_raw if isinstance(context_raw, int) else None

        pricing_prompt: str | None = None
        pricing_completion: str | None = None
        pricing_raw = entry.get("pricing")
        if isinstance(pricing_raw, dict):
            p_prompt = pricing_raw.get("prompt")
            p_completion = pricing_raw.get("completion")
            pricing_prompt = str(p_prompt) if p_prompt is not None else None
            pricing_completion = str(p_completion) if p_completion is not None else None

        params: tuple[str, ...] = ()
        params_raw = entry.get("supported_parameters")
        if isinstance(params_raw, list):
            normalized = [str(param) for param in params_raw if isinstance(param, str)]
            params = tuple(sorted(set(normalized)))

        models.append(
            OpenRouterModel(
                model_id=model_id,
                name=name,
                context_length=context_length,
                pricing_prompt=pricing_prompt,
                pricing_completion=pricing_completion,
                supported_parameters=params,
            )
        )

    return sorted(models, key=lambda item: item.model_id)


def _display_openrouter_models(models: list[OpenRouterModel], *, limit: int = 15) -> None:
    table = Table(title=f"OpenRouter live catalog ({len(models)} models)")
    table.add_column("#", justify="right")
    table.add_column("id")
    table.add_column("name")
    table.add_column("context")
    table.add_column("pricing")
    table.add_column("params")

    for idx, item in enumerate(models[:limit], start=1):
        pricing = "-"
        if item.pricing_prompt or item.pricing_completion:
            pricing = f"p:{item.pricing_prompt or '-'} c:{item.pricing_completion or '-'}"
        params = ", ".join(item.supported_parameters[:3]) if item.supported_parameters else "-"
        table.add_row(
            str(idx),
            item.model_id,
            item.name,
            str(item.context_length or "-"),
            pricing,
            params,
        )
    console.print(table)


def _pick_openrouter_model(default_model: str) -> str:
    models: list[OpenRouterModel] = []
    while True:
        try:
            models = _fetch_openrouter_models()
            break
        except Exception as exc:
            console.print(f"[yellow]Could not fetch OpenRouter catalog:[/yellow] {exc}")
            choice = Prompt.ask(
                "OpenRouter model source",
                choices=["retry", "manual"],
                default="retry",
            )
            if choice == "manual":
                return (
                    Prompt.ask("Enter OpenRouter model id", default=default_model).strip()
                    or default_model
                )

    query = (
        Prompt.ask("Optional search/filter (id or name contains, blank = all)", default="")
        .strip()
        .lower()
    )
    filtered = [
        model
        for model in models
        if not query or query in model.model_id.lower() or query in model.name.lower()
    ]
    if not filtered:
        console.print(
            "[yellow]No models matched the filter. Falling back to manual entry.[/yellow]"
        )
        return (
            Prompt.ask("Enter OpenRouter model id", default=default_model).strip() or default_model
        )

    _display_openrouter_models(filtered)
    selection = Prompt.ask(
        "Select model by number (or type model id)",
        default="1",
    ).strip()
    if selection.isdigit():
        index = int(selection)
        if 1 <= index <= min(len(filtered), 15):
            return filtered[index - 1].model_id

    raw = selection.strip()
    return raw or default_model


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
        model = _pick_openrouter_model(provider.default_model)
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


def _launch_choice() -> None:
    console.print(
        Panel(
            "Setup is complete. You can optionally open local panels now:\n"
            "- voxera: http://127.0.0.1:8844\n"
            "- vera:   http://127.0.0.1:8000",
            title="Finish",
        )
    )
    choice = Prompt.ask(
        "Open panel(s)",
        choices=["voxera", "vera", "both", "none"],
        default="none",
    )
    urls: list[str] = []
    if choice in {"voxera", "both"}:
        urls.append("http://127.0.0.1:8844")
    if choice in {"vera", "both"}:
        urls.append("http://127.0.0.1:8000")
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
    _launch_choice()
    _print_what_next()
    return cfg
