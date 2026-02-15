import asyncio

import pytest

from voxera.core.mission_planner import MissionPlannerError, plan_mission
from voxera.models import AppConfig, BrainConfig
from voxera.skills.registry import SkillRegistry


class _FakeBrain:
    def __init__(self, text: str):
        self.text = text

    async def generate(self, messages, tools=None):
        class _Resp:
            def __init__(self, body: str):
                self.text = body

        return _Resp(self.text)


def test_plan_mission_from_cloud_json(monkeypatch):
    cfg = AppConfig(
        brain={
            "primary": BrainConfig(
                type="openai_compat",
                model="test-model",
                base_url="https://example.test/v1",
            )
        }
    )
    reg = SkillRegistry()
    reg.discover()

    monkeypatch.setattr(
        "voxera.core.mission_planner._build_brain_candidates",
        lambda _cfg: [type("C", (), {"name": "primary", "brain": _FakeBrain('{"title":"Quick prep","notes":"cloud test","steps":[{"skill_id":"system.status","args":{}}]}')})()],
    )

    mission = asyncio.run(plan_mission("check machine", cfg=cfg, registry=reg))

    assert mission.id == "cloud_planned"
    assert mission.title == "Quick prep"
    assert mission.steps[0].skill_id == "system.status"


def test_plan_mission_rejects_unknown_skill(monkeypatch):
    cfg = AppConfig(
        brain={
            "primary": BrainConfig(
                type="openai_compat",
                model="test-model",
                base_url="https://example.test/v1",
            )
        }
    )
    reg = SkillRegistry()
    reg.discover()

    monkeypatch.setattr(
        "voxera.core.mission_planner._build_brain_candidates",
        lambda _cfg: [type("C", (), {"name": "primary", "brain": _FakeBrain('{"steps":[{"skill_id":"system.missing","args":{}}]}')})()],
    )

    with pytest.raises(MissionPlannerError, match="unknown skill"):
        asyncio.run(plan_mission("check machine", cfg=cfg, registry=reg))


def test_plan_mission_normalizes_single_arg_alias(monkeypatch):
    cfg = AppConfig(
        brain={
            "primary": BrainConfig(
                type="openai_compat",
                model="test-model",
                base_url="https://example.test/v1",
            )
        }
    )
    reg = SkillRegistry()
    reg.discover()

    monkeypatch.setattr(
        "voxera.core.mission_planner._build_brain_candidates",
        lambda _cfg: [type("C", (), {"name": "primary", "brain": _FakeBrain('{"title":"Work mode","steps":[{"skill_id":"system.open_app","args":{"app_name":"Firefox"}}]}')})()],
    )

    mission = asyncio.run(plan_mission("start work mode", cfg=cfg, registry=reg))

    assert mission.steps[0].skill_id == "system.open_app"
    assert mission.steps[0].args == {"name": "firefox"}


def test_plan_mission_uses_fallback(monkeypatch):
    cfg = AppConfig(
        brain={
            "primary": BrainConfig(type="openai_compat", model="primary", base_url="https://example.test/v1"),
            "fast": BrainConfig(type="openai_compat", model="fast", base_url="https://example.test/v1"),
        }
    )
    reg = SkillRegistry()
    reg.discover()

    class _FailingBrain:
        async def generate(self, messages, tools=None):
            class _Resp:
                text = "not-json"

            return _Resp()

    monkeypatch.setattr(
        "voxera.core.mission_planner._build_brain_candidates",
        lambda _cfg: [
            type("C", (), {"name": "primary", "brain": _FailingBrain()})(),
            type("C", (), {"name": "fast", "brain": _FakeBrain('{"steps":[{"skill_id":"system.status","args":{}}]}')})(),
        ],
    )

    mission = asyncio.run(plan_mission("check machine", cfg=cfg, registry=reg))
    assert mission.steps[0].skill_id == "system.status"
    assert "fast" in (mission.notes or "")
