from pathlib import Path

from rich.text import Text

from voxera import setup_wizard
from voxera.models import AppConfig, BrainConfig


def _provider() -> setup_wizard.ProviderChoice:
    return setup_wizard.ProviderChoice(
        slug="openrouter",
        label="OpenRouter",
        env_ref="OPENROUTER_API_KEY",
        brain_type="openai_compat",
        default_model="openai/gpt-4o-mini",
    )


def test_provider_key_choice_keep_returns_existing(monkeypatch):
    calls: list[str] = []

    def _ask(prompt: str, **kwargs):
        calls.append(prompt)
        return "keep"

    monkeypatch.setattr(setup_wizard.Prompt, "ask", _ask)

    resolved = setup_wizard._apply_provider_key_choice(
        _provider(), existing_ref="OPENROUTER_API_KEY"
    )

    assert resolved == "OPENROUTER_API_KEY"
    assert isinstance(calls[0], Text)
    assert calls[0].plain == "Auth for OpenRouter [keep/skip/replace]"


def test_provider_key_choice_skip_clears_existing(monkeypatch):
    monkeypatch.setattr(setup_wizard.Prompt, "ask", lambda *args, **kwargs: "skip")

    resolved = setup_wizard._apply_provider_key_choice(
        _provider(), existing_ref="OPENROUTER_API_KEY"
    )

    assert resolved is None


def test_provider_key_choice_replace_uses_new_reference(monkeypatch):
    answers = iter(["replace", "OPENROUTER_ALT_KEY"])
    monkeypatch.setattr(setup_wizard.Prompt, "ask", lambda *args, **kwargs: next(answers))

    resolved = setup_wizard._apply_provider_key_choice(
        _provider(), existing_ref="OPENROUTER_API_KEY"
    )

    assert resolved == "OPENROUTER_ALT_KEY"


def test_provider_key_choice_set_when_missing(monkeypatch):
    calls: list[str] = []
    answers = iter(["set", "OPENROUTER_API_KEY"])

    def _ask(prompt: str, **kwargs):
        calls.append(prompt)
        return next(answers)

    monkeypatch.setattr(setup_wizard.Prompt, "ask", _ask)

    resolved = setup_wizard._apply_provider_key_choice(_provider(), existing_ref=None)

    assert resolved == "OPENROUTER_API_KEY"
    assert isinstance(calls[0], Text)
    assert calls[0].plain == "Auth for OpenRouter [skip/set]"


def test_pick_openrouter_model_prefers_recommended(monkeypatch):
    monkeypatch.setattr(
        setup_wizard,
        "load_curated_openrouter_catalog",
        lambda: [
            {
                "vendor": "OpenAI",
                "id": "openai/gpt-4o-mini",
                "name": "GPT-4o mini",
                "context_length": 128000,
                "pricing_prompt": "0.15",
                "pricing_completion": "0.60",
            }
        ],
    )
    monkeypatch.setattr(setup_wizard, "grouped_catalog", lambda models: [("OpenAI", models)])
    monkeypatch.setattr(setup_wizard.Prompt, "ask", lambda *args, **kwargs: "recommended")

    model = setup_wizard._pick_openrouter_model("primary")

    assert model == "openai/gpt-4o-mini"


def test_pick_openrouter_model_manual_path(monkeypatch):
    monkeypatch.setattr(
        setup_wizard,
        "load_curated_openrouter_catalog",
        lambda: [
            {
                "vendor": "OpenAI",
                "id": "openai/gpt-4o-mini",
                "name": "GPT-4o mini",
            }
        ],
    )
    monkeypatch.setattr(setup_wizard, "grouped_catalog", lambda models: [("OpenAI", models)])
    answers = iter(["manual", "openai/gpt-4.1-mini"])
    monkeypatch.setattr(setup_wizard.Prompt, "ask", lambda *args, **kwargs: next(answers))

    model = setup_wizard._pick_openrouter_model("primary")

    assert model == "openai/gpt-4.1-mini"


def test_configure_cloud_brains_runs_sequential_slots(monkeypatch):
    cfg = AppConfig(
        brain={
            "primary": BrainConfig(type="openai_compat", model="a"),
            "fast": BrainConfig(type="openai_compat", model="b"),
            "reasoning": BrainConfig(type="openai_compat", model="c"),
            "fallback": BrainConfig(type="openai_compat", model="d"),
        }
    )
    seen: list[str] = []

    monkeypatch.setattr(setup_wizard, "_provider_summary_table", lambda: "providers")

    def _slot(cfg_obj, *, slot, existing):
        assert existing is not None
        seen.append(slot.key)

    monkeypatch.setattr(setup_wizard, "_configure_brain_slot", _slot)

    setup_wizard._configure_cloud_brains(cfg)

    assert seen == ["primary", "fast", "reasoning", "fallback"]


def test_launch_choice_opens_selected_panels(monkeypatch):
    monkeypatch.setattr(setup_wizard.Prompt, "ask", lambda *args, **kwargs: "both")
    opened: list[str] = []
    monkeypatch.setattr(setup_wizard.webbrowser, "open", lambda url: opened.append(url) or True)

    setup_wizard._launch_choice()

    assert opened == ["http://127.0.0.1:8844", "http://127.0.0.1:8000"]


def test_confirm_write_config_defaults_to_keep_existing(tmp_path, monkeypatch):
    existing = tmp_path / "config.yml"
    existing.write_text("mode: mixed\n", encoding="utf-8")

    monkeypatch.setattr(setup_wizard.Confirm, "ask", lambda *args, **kwargs: False)

    assert setup_wizard._confirm_write_config(existing) is False


def test_confirm_write_config_true_when_missing(tmp_path):
    missing = Path(tmp_path / "missing.yml")
    assert setup_wizard._confirm_write_config(missing) is True
