from __future__ import annotations

import pytest
from pydantic import ValidationError

from voxera.config import load_app_config
from voxera.models import AppConfig, PlanStep


def test_app_config_valid_payload_still_loads() -> None:
    cfg = AppConfig.model_validate(
        {
            "mode": "mixed",
            "brain": {
                "primary": {
                    "type": "openai_compat",
                    "model": "google/gemini-3-flash-preview",
                    "base_url": "https://openrouter.ai/api/v1",
                    "api_key_ref": "OPENROUTER_API_KEY",
                    "extra_headers": {"X-Title": "VoxeraOS"},
                }
            },
            "policy": {"network_changes": "ask"},
            "privacy": {"cloud_allowed": True},
        }
    )

    assert cfg.mode == "mixed"
    assert cfg.brain["primary"].api_key_ref == "OPENROUTER_API_KEY"


def test_app_config_rejects_unknown_top_level_key() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AppConfig.model_validate({"mode": "mixed", "unknown_setting": True})


@pytest.mark.parametrize(
    "payload",
    [
        {
            "mode": "mixed",
            "brain": {
                "primary": {
                    "type": "openai_compat",
                    "model": "google/gemini-3-flash-preview",
                    "unknown_brain_key": "nope",
                }
            },
        },
        {
            "mode": "mixed",
            "policy": {"network_changes": "ask", "unknown_policy_key": "deny"},
        },
    ],
)
def test_app_config_rejects_unknown_nested_contract_keys(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AppConfig.model_validate(payload)


def test_load_app_config_surfaces_unknown_key_error_with_operator_hint(tmp_path) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text("mode: mixed\nunknown_setting: true\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Unknown config key"):
        load_app_config(path=config_path)


def test_internal_plan_payload_models_remain_permissive() -> None:
    step = PlanStep.model_validate(
        {"action": "system.status", "args": {}, "future_runtime_metadata": {"v": 2}}
    )

    assert step.action == "system.status"
