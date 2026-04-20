from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import yaml
from rich.console import Console
from rich.markup import escape as _markup_escape
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from .config import (
    capabilities_report_path,
    default_config_path,
    resolve_config_path,
    save_config,
    save_policy,
    update_runtime_config,
)
from .doctor import run_quick_doctor
from .models import AppConfig, BrainConfig, PolicyApprovals, PrivacyConfig, WebInvestigationConfig
from .openrouter_catalog import (
    grouped_catalog,
    load_curated_openrouter_catalog,
    recommended_model_for_slot,
)
from .paths import ensure_dirs
from .secrets import get_secret, set_secret
from .voice.stt_backend_factory import STT_BACKEND_WHISPER_LOCAL
from .voice.tts_backend_factory import (
    TTS_BACKEND_CHOICES,
    TTS_BACKEND_KOKORO_LOCAL,
    TTS_BACKEND_PIPER_LOCAL,
)

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
            default_model="google/gemini-3-flash-preview",
            default_base_url="https://openrouter.ai/api/v1",
        ),
        ProviderChoice(
            slug="openai",
            label="OpenAI",
            env_ref="OPENAI_API_KEY",
            brain_type="openai_compat",
            default_model="gpt-4.1-mini",
            default_base_url="https://api.openai.com/v1",
        ),
        ProviderChoice(
            slug="anthropic",
            label="Anthropic",
            env_ref="ANTHROPIC_API_KEY",
            brain_type="openai_compat",
            default_model="anthropic/claude-3.5-sonnet",
            default_base_url="https://api.anthropic.com/v1",
        ),
        ProviderChoice(
            slug="google",
            label="Google/Gemini",
            env_ref="GOOGLE_API_KEY",
            brain_type="gemini",
            default_model="gemini-3.1-flash-lite-preview",
        ),
        ProviderChoice(
            slug="gemini",
            label="Gemini (legacy env)",
            env_ref="GEMINI_API_KEY",
            brain_type="gemini",
            default_model="gemini-3.1-flash-lite-preview",
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


def _configure_web_investigation(cfg: AppConfig) -> None:
    console.print(Panel("Optional read-only web investigation for Vera", title="Web Investigation"))
    if not Confirm.ask(
        "Enable read-only web investigation via Brave Search?",
        default=False,
    ):
        cfg.web_investigation = None
        return

    key_ref = "BRAVE_API_KEY"
    while True:
        brave_api_key = Prompt.ask("Brave API key", password=True).strip()
        if brave_api_key:
            break
        console.print(
            "[yellow]Brave API key is required when web investigation is enabled.[/yellow]"
        )

    max_results_input = Prompt.ask("Brave max results (1-10)", default="5").strip()
    try:
        max_results = int(max_results_input)
    except ValueError:
        max_results = 5
    max_results = max(1, min(max_results, 10))

    storage = set_secret(key_ref, brave_api_key)
    console.print(f"Stored Brave API key via {storage}.")

    cfg.web_investigation = WebInvestigationConfig(
        provider="brave",
        api_key_ref=key_ref,
        env_api_key_var=key_ref,
        max_results=max_results,
    )


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


def _print_what_next(*, verbose: bool = False) -> None:
    if verbose:
        console.print(
            Panel(
                "All available post-setup commands:\n"
                "- voxera doctor --quick        # check config and connectivity\n"
                "- voxera doctor --self-test    # deeper self-test\n"
                "- voxera vera                  # start a conversation with Vera\n"
                "- voxera panel                 # open the operator dashboard\n"
                "- voxera demo                  # guided offline onboarding check\n"
                "- voxera demo --online         # opt-in provider readiness\n"
                "- voxera queue status          # queue state overview\n"
                "- voxera queue reconcile       # check queue consistency\n"
                "- voxera queue reconcile --fix # auto-fix inconsistencies\n"
                "- voxera queue prune           # remove old queue entries\n"
                "- voxera artifacts prune       # remove old artifacts",
                title="Next Steps (verbose)",
            )
        )
        return
    console.print(
        Panel(
            "Three things to try:\n\n"
            "  1. voxera doctor --quick  — check config and connectivity\n"
            "  2. voxera vera            — start a conversation with Vera\n"
            "  3. voxera panel           — open the operator dashboard\n\n"
            "Run 'voxera setup --verbose-next' for the full command list.",
            title="Next Steps",
        )
    )


def _check_brain_config(cfg: AppConfig) -> list[dict[str, str]]:
    """Check first-run brain readiness — is there at least one usable brain path?

    This is intentionally NOT a per-slot completeness audit.  The setup
    summary answers "can the user likely talk to Vera now?" — so we check
    whether *any* configured slot has a resolvable API key.  Unconfigured
    optional slots are not first-run blockers and do not produce warnings.
    """
    checks: list[dict[str, str]] = []
    if not cfg.brain:
        checks.append(
            {
                "check": "brain config",
                "status": "warn",
                "detail": "No brain slots configured.",
                "hint": "Re-run 'voxera setup' and configure at least one brain slot.",
            }
        )
        return checks

    # Scan every configured slot to find at least one usable brain path.
    usable_slots: list[str] = []
    first_broken: dict[str, str] | None = None

    for name, bc in cfg.brain.items():
        if not bc.api_key_ref:
            # Slot exists but has no key ref — skip silently.
            # Only matters if *no* slot ends up usable.
            continue
        found = bool(os.environ.get(bc.api_key_ref, "").strip())
        if not found:
            try:
                resolved = get_secret(bc.api_key_ref)
                found = resolved is not None and bool(resolved.strip())
            except Exception:
                pass
        if found:
            usable_slots.append(name)
        elif first_broken is None:
            first_broken = {
                "name": name,
                "ref": bc.api_key_ref,
            }

    if usable_slots:
        checks.append(
            {
                "check": "brain readiness",
                "status": "ok",
                "detail": f"Usable: {', '.join(usable_slots)}.",
                "hint": "",
            }
        )
    elif first_broken:
        ref = first_broken["ref"]
        checks.append(
            {
                "check": "brain readiness",
                "status": "warn",
                "detail": f"{ref} not found in environment or keyring.",
                "hint": (f"Set {ref} in your environment or run 'voxera secrets set {ref}'."),
            }
        )
    else:
        # All slots exist but none has an api_key_ref at all.
        checks.append(
            {
                "check": "brain readiness",
                "status": "warn",
                "detail": "No brain slot has an API key reference configured.",
                "hint": "Re-run 'voxera setup' and set an API key for at least one slot.",
            }
        )
    return checks


def _render_validation_summary(checks: list[dict[str, str]]) -> None:
    """Render a compact traffic-light validation summary."""
    if not checks:
        return
    icon = {"ok": "✅", "warn": "⚠️", "fail": "❌"}
    ok_count = sum(1 for c in checks if c["status"] == "ok")
    non_ok = [c for c in checks if c["status"] != "ok"]
    warn_count = sum(1 for c in checks if c["status"] == "warn")
    fail_count = sum(1 for c in checks if c["status"] == "fail")

    lines: list[str] = []
    if ok_count:
        lines.append(f"✅ {ok_count} check{'s' if ok_count != 1 else ''} passed")
    for c in non_ok:
        # Escape dynamic fields -- doctor hints can legitimately contain
        # square brackets (e.g. `pip install voxera-os[piper]`) that Rich
        # would otherwise consume as style markup.
        name_s = _markup_escape(str(c.get("check", "")))
        detail_s = _markup_escape(str(c.get("detail", "")))
        lines.append(f"{icon.get(c['status'], '⚠️')} {name_s}: {detail_s}")
        if c.get("hint"):
            hint_s = _markup_escape(str(c["hint"]))
            lines.append(f"   → {hint_s}")

    if fail_count:
        lines.append("")
        lines.append("Setup wrote config but some checks need attention.")
        lines.append("Run 'voxera doctor --quick' for full diagnostics.")
        border_style = "red"
    elif warn_count:
        lines.append("")
        lines.append(f"Setup complete with {warn_count} warning{'s' if warn_count != 1 else ''}.")
        lines.append("Run 'voxera doctor --quick' for full diagnostics.")
        border_style = "yellow"
    else:
        lines.append("")
        lines.append("Setup complete. Try: voxera vera")
        border_style = "green"

    console.print(Panel("\n".join(lines), title="Post-Setup Validation", border_style=border_style))


def _post_setup_validation(cfg: AppConfig) -> None:
    """Run bounded post-setup validation and render summary."""
    checks = _check_brain_config(cfg)
    with contextlib.suppress(Exception):
        for rc in run_quick_doctor():
            # The quick-doctor surface is designed for a running system.
            # After a fresh setup the daemon has not started, so most warn
            # checks ("no health event", "lock absent", …) are expected-
            # default-state noise, not actionable blockers.
            #
            # Exception: voice: * warn checks carry actionable setup-pointing
            # hints (install an optional dep, fix a Piper model path, pick a
            # backend).  Suppressing them here would make "Setup complete"
            # misleading for a user who just enabled voice and is missing a
            # dependency.  Fail-level checks always surface.
            status = rc["status"]
            name = str(rc.get("check", ""))
            if status in {"ok", "fail"} or (status == "warn" and name.startswith("voice:")):
                checks.append(rc)
    _render_validation_summary(checks)


# STT choices stay local to the wizard today -- the STT factory does
# not yet export a curated allow-list.  TTS choices are imported from
# the factory so the wizard and panel share the exact same source of
# truth; adding a new TTS backend flows through both surfaces
# automatically.
STT_BACKEND_CHOICES = (STT_BACKEND_WHISPER_LOCAL,)


def _configure_voice(*, runtime_config_path: Path | None = None) -> dict[str, object]:
    """Ask the operator about voice and persist the answers to runtime config JSON.

    Writes exactly the voice fields into ``~/.config/voxera/config.json`` (or the
    provided ``runtime_config_path``) using a partial merge so unrelated runtime
    keys are preserved.  Returns the voice answers that were written (or cleared)
    for downstream summary / testing.

    The wizard is voice-first-safe but does not assume voice is wanted.  If the
    operator declines the foundation the existing voice-related keys in the
    runtime config are cleared so state cannot go stale against the wizard's
    most recent answer.
    """
    console.print(
        Panel(
            "Voice foundation is optional.  If enabled, Vera can accept voice input "
            "(speech-to-text) and/or speak responses (text-to-speech).  Everything "
            "runs locally by default -- no audio is sent to the cloud.\n\n"
            "Note: voice answers are written to ~/.config/voxera/config.json "
            'as soon as you finish this step -- before the final "write config?" '
            "prompt -- so declining that prompt will not roll them back.  Re-run "
            "`voxera setup` to change them.",
            title="Voice Setup",
        )
    )

    # Read the existing runtime config so re-running setup can pre-fill the
    # Piper model prompt with the previously-stored value.  Absent / empty
    # files are fine; malformed JSON is surfaced cleanly so the wizard never
    # overwrites (or crashes on top of) a corrupted operator-managed file.
    runtime_path = resolve_config_path(runtime_config_path)
    existing_runtime: dict[str, object] = {}
    if runtime_path.exists():
        raw = ""
        with contextlib.suppress(Exception):
            raw = runtime_path.read_text(encoding="utf-8")
        if raw.strip():
            try:
                loaded = json.loads(raw)
            except json.JSONDecodeError as exc:
                console.print(
                    Panel(
                        f"The runtime config at {runtime_path} is not valid JSON "
                        f"({exc.msg} at line {exc.lineno}, column {exc.colno}), so "
                        "voice setup was skipped to avoid overwriting it.\n\n"
                        "Fix or remove the file, then re-run `voxera setup` to "
                        "configure voice.  The rest of setup will continue with "
                        "your existing voice answers untouched.",
                        title="Voice Setup skipped",
                        border_style="yellow",
                    )
                )
                return {
                    "enable_voice_foundation": False,
                    "enable_voice_input": False,
                    "enable_voice_output": False,
                    "voice_stt_backend": None,
                    "voice_tts_backend": None,
                    "voice_tts_piper_model": None,
                    "voice_stt_whisper_model": None,
                    "voice_tts_kokoro_model": None,
                    "voice_tts_kokoro_voices": None,
                    "voice_tts_kokoro_voice": None,
                }
            if isinstance(loaded, dict):
                existing_runtime = loaded
    existing_piper_model_raw = existing_runtime.get("voice_tts_piper_model")
    existing_piper_model = (
        str(existing_piper_model_raw).strip() if isinstance(existing_piper_model_raw, str) else ""
    )
    existing_kokoro_model_raw = existing_runtime.get("voice_tts_kokoro_model")
    existing_kokoro_model = (
        str(existing_kokoro_model_raw).strip() if isinstance(existing_kokoro_model_raw, str) else ""
    )
    existing_kokoro_voices_raw = existing_runtime.get("voice_tts_kokoro_voices")
    existing_kokoro_voices = (
        str(existing_kokoro_voices_raw).strip()
        if isinstance(existing_kokoro_voices_raw, str)
        else ""
    )
    existing_kokoro_voice_raw = existing_runtime.get("voice_tts_kokoro_voice")
    existing_kokoro_voice = (
        str(existing_kokoro_voice_raw).strip() if isinstance(existing_kokoro_voice_raw, str) else ""
    )

    answers: dict[str, object] = {
        "enable_voice_foundation": False,
        "enable_voice_input": False,
        "enable_voice_output": False,
        "voice_stt_backend": None,
        "voice_tts_backend": None,
        "voice_tts_piper_model": None,
        # Preserve any previously-saved whisper model selection by default;
        # the wizard does not prompt for it (that's a panel-only surface),
        # but declining the foundation below must clear it so state never
        # goes stale against the most recent answer.
        "voice_stt_whisper_model": existing_runtime.get("voice_stt_whisper_model") or None,
        # Preserve any previously-saved Kokoro paths / voice by default so
        # re-running the wizard without touching Kokoro does not silently
        # wipe them.  Cleared below when the operator declines the
        # foundation or picks a different backend.
        "voice_tts_kokoro_model": existing_kokoro_model or None,
        "voice_tts_kokoro_voices": existing_kokoro_voices or None,
        "voice_tts_kokoro_voice": existing_kokoro_voice or None,
    }

    enable_foundation = Confirm.ask("Enable voice foundation?", default=False)
    answers["enable_voice_foundation"] = enable_foundation

    if enable_foundation:
        enable_input = Confirm.ask("Enable speech-to-text (voice input)?", default=True)
        answers["enable_voice_input"] = enable_input
        if enable_input:
            stt_backend = Prompt.ask(
                "Speech-to-text backend",
                choices=list(STT_BACKEND_CHOICES),
                default=STT_BACKEND_WHISPER_LOCAL,
            )
            answers["voice_stt_backend"] = stt_backend
            console.print(
                "[dim]If faster-whisper is not installed, run "
                "`pip install voxera-os[whisper]`.[/dim]"
            )

        enable_output = Confirm.ask("Enable text-to-speech (voice output)?", default=True)
        answers["enable_voice_output"] = enable_output
        if enable_output:
            tts_backend = Prompt.ask(
                "Text-to-speech backend",
                choices=list(TTS_BACKEND_CHOICES),
                default=TTS_BACKEND_PIPER_LOCAL,
            )
            answers["voice_tts_backend"] = tts_backend
            if tts_backend == TTS_BACKEND_PIPER_LOCAL:
                console.print(
                    "[dim]If piper-tts is not installed, run `pip install voxera-os[piper]`.[/dim]"
                )
                # Pre-fill with the existing stored value so re-running the
                # wizard does not silently wipe a configured model path.
                if existing_piper_model:
                    piper_prompt = (
                        "Piper model (name or path to .onnx). "
                        "Press Enter to keep the current value, or type a new "
                        "name/path.  Type 'default' to clear and fall back to "
                        "'en_US-lessac-medium'"
                    )
                    piper_default = existing_piper_model
                else:
                    piper_prompt = (
                        "Piper model (name or path to .onnx). Leave blank to "
                        "use the default 'en_US-lessac-medium'"
                    )
                    piper_default = ""
                model = Prompt.ask(piper_prompt, default=piper_default).strip()
                if model.lower() == "default":
                    model = ""
                answers["voice_tts_piper_model"] = model or None
            elif tts_backend == TTS_BACKEND_KOKORO_LOCAL:
                console.print(
                    "[dim]If kokoro-onnx is not installed, run "
                    "`pip install voxera-os[kokoro]`.  Kokoro requires "
                    "operator-provided model (.onnx) and voices (.bin) "
                    "files; no default path is assumed.[/dim]"
                )
                # Kokoro: ask for the two required paths; pre-fill with
                # existing values so re-running the wizard never silently
                # wipes configured paths.  Empty means "leave unset" and
                # the status/doctor surface will report it truthfully.
                kokoro_model_path = Prompt.ask(
                    "Kokoro model path (absolute path to kokoro-*.onnx)",
                    default=existing_kokoro_model,
                ).strip()
                answers["voice_tts_kokoro_model"] = kokoro_model_path or None
                kokoro_voices_path = Prompt.ask(
                    "Kokoro voices path (absolute path to voices-*.bin)",
                    default=existing_kokoro_voices,
                ).strip()
                answers["voice_tts_kokoro_voices"] = kokoro_voices_path or None
                kokoro_voice_id = Prompt.ask(
                    "Kokoro voice id (blank for default 'af_sarah')",
                    default=existing_kokoro_voice,
                ).strip()
                answers["voice_tts_kokoro_voice"] = kokoro_voice_id or None

    # Persist answers.  When the foundation is disabled we explicitly null
    # every voice key so the runtime config reflects the wizard's answer
    # (no silently-stale backend choices or model selections from a prior
    # run).  When the foundation is enabled, the existing whisper model
    # selection is preserved untouched -- it's a panel-managed knob that
    # the wizard never prompts for.  Kokoro paths are persisted only
    # when Kokoro is the chosen backend; switching back to Piper clears
    # them so a subsequent doctor run does not warn about stale Kokoro
    # paths that are no longer in play.
    stt_whisper_model_update: object | None
    if answers["enable_voice_foundation"]:
        stt_whisper_model_update = answers["voice_stt_whisper_model"] or None
    else:
        stt_whisper_model_update = None
    if answers["voice_tts_backend"] == TTS_BACKEND_KOKORO_LOCAL:
        kokoro_model_update = answers["voice_tts_kokoro_model"] or None
        kokoro_voices_update = answers["voice_tts_kokoro_voices"] or None
        kokoro_voice_update = answers["voice_tts_kokoro_voice"] or None
    else:
        kokoro_model_update = None
        kokoro_voices_update = None
        kokoro_voice_update = None
    updates: dict[str, object | None] = {
        "enable_voice_foundation": bool(answers["enable_voice_foundation"]),
        "enable_voice_input": bool(answers["enable_voice_input"]),
        "enable_voice_output": bool(answers["enable_voice_output"]),
        "voice_stt_backend": answers["voice_stt_backend"],
        "voice_tts_backend": answers["voice_tts_backend"],
        "voice_tts_piper_model": answers["voice_tts_piper_model"],
        "voice_stt_whisper_model": stt_whisper_model_update,
        "voice_tts_kokoro_model": kokoro_model_update,
        "voice_tts_kokoro_voices": kokoro_voices_update,
        "voice_tts_kokoro_voice": kokoro_voice_update,
    }
    path = update_runtime_config(updates, config_path=runtime_config_path)
    console.print(f"Voice settings written to {path}.")
    return answers


async def run_setup(*, verbose_next: bool = False) -> AppConfig:
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
            "- Runtime ops + voice config: ~/.config/voxera/config.json "
            "(written by setup for voice, else operator-managed)",
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
    _configure_web_investigation(cfg)
    _configure_voice()

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

    console.print("\nConfig written.")
    console.print("App config (brain/mode/privacy): ~/.config/voxera/config.yml")
    console.print("Runtime ops config (panel/queue, optional): ~/.config/voxera/config.json")
    console.print("Policy: ~/.config/voxera/policy.yml")
    console.print("Capabilities: ~/.local/share/voxera/capabilities.json\n")
    _post_setup_validation(cfg)
    service_state = _ensure_runtime_services_running()
    _launch_choice(service_state=service_state)
    _print_what_next(verbose=verbose_next)
    return cfg
