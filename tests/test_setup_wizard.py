from pathlib import Path

from voxera import setup_wizard


def test_provider_key_choice_keep_returns_existing(monkeypatch):
    monkeypatch.setattr(setup_wizard.Prompt, "ask", lambda *args, **kwargs: "keep")

    resolved = setup_wizard._apply_provider_key_choice(
        setup_wizard.ProviderChoice(
            slug="openrouter",
            label="OpenRouter",
            env_ref="OPENROUTER_API_KEY",
            brain_type="openai_compat",
            default_model="openai/gpt-4o-mini",
        ),
        existing_ref="OPENROUTER_API_KEY",
    )

    assert resolved == "OPENROUTER_API_KEY"


def test_provider_key_choice_skip_returns_none(monkeypatch):
    monkeypatch.setattr(setup_wizard.Prompt, "ask", lambda *args, **kwargs: "skip")

    resolved = setup_wizard._apply_provider_key_choice(
        setup_wizard.ProviderChoice(
            slug="openai",
            label="OpenAI",
            env_ref="OPENAI_API_KEY",
            brain_type="openai_compat",
            default_model="gpt-4o-mini",
        ),
        existing_ref=None,
    )

    assert resolved is None


def test_provider_key_choice_replace_sets_secret(monkeypatch):
    answers = iter(["replace", "sk-test"])
    monkeypatch.setattr(setup_wizard.Prompt, "ask", lambda *args, **kwargs: next(answers))
    called = {}

    def _fake_set_secret(ref: str, value: str) -> str:
        called["ref"] = ref
        called["value"] = value
        return f"keyring:{ref}"

    monkeypatch.setattr(setup_wizard, "set_secret", _fake_set_secret)

    resolved = setup_wizard._apply_provider_key_choice(
        setup_wizard.ProviderChoice(
            slug="anthropic",
            label="Anthropic",
            env_ref="ANTHROPIC_API_KEY",
            brain_type="openai_compat",
            default_model="anthropic/claude-3.7-sonnet",
        ),
        existing_ref="ANTHROPIC_API_KEY",
    )

    assert resolved == "ANTHROPIC_API_KEY"
    assert called == {"ref": "ANTHROPIC_API_KEY", "value": "sk-test"}


def test_confirm_write_config_defaults_to_keep_existing(tmp_path, monkeypatch):
    existing = tmp_path / "config.yml"
    existing.write_text("mode: mixed\n", encoding="utf-8")

    monkeypatch.setattr(setup_wizard.Confirm, "ask", lambda *args, **kwargs: False)

    assert setup_wizard._confirm_write_config(existing) is False


def test_confirm_write_config_true_when_missing(tmp_path):
    missing = Path(tmp_path / "missing.yml")
    assert setup_wizard._confirm_write_config(missing) is True
