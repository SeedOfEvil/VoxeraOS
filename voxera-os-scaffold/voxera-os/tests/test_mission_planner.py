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
        lambda _cfg: [
            type(
                "C",
                (),
                {
                    "name": "primary",
                    "brain": _FakeBrain(
                        '{"title":"Quick prep","notes":"cloud test","steps":[{"skill_id":"system.status","args":{}}]}'
                    ),
                },
            )()
        ],
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
        lambda _cfg: [
            type(
                "C",
                (),
                {
                    "name": "primary",
                    "brain": _FakeBrain('{"steps":[{"skill_id":"system.missing","args":{}}]}'),
                },
            )()
        ],
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
        lambda _cfg: [
            type(
                "C",
                (),
                {
                    "name": "primary",
                    "brain": _FakeBrain(
                        '{"title":"Work mode","steps":[{"skill_id":"system.open_app","args":{"app_name":"Firefox"}}]}'
                    ),
                },
            )()
        ],
    )

    mission = asyncio.run(plan_mission("start work mode", cfg=cfg, registry=reg))

    assert mission.steps[0].skill_id == "system.open_app"
    assert mission.steps[0].args == {"name": "firefox"}


def test_plan_mission_uses_fallback(monkeypatch):
    cfg = AppConfig(
        brain={
            "primary": BrainConfig(
                type="openai_compat", model="primary", base_url="https://example.test/v1"
            ),
            "fast": BrainConfig(
                type="openai_compat", model="fast", base_url="https://example.test/v1"
            ),
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
            type(
                "C",
                (),
                {
                    "name": "fast",
                    "brain": _FakeBrain('{"steps":[{"skill_id":"system.status","args":{}}]}'),
                },
            )(),
        ],
    )

    mission = asyncio.run(plan_mission("check machine", cfg=cfg, registry=reg))
    assert mission.steps[0].skill_id == "system.status"
    assert "fast" in (mission.notes or "")


class _SlowBrain:
    async def generate(self, messages, tools=None):
        await asyncio.sleep(60)


def test_plan_mission_timeout_falls_back(monkeypatch):
    cfg = AppConfig(
        brain={
            "primary": BrainConfig(
                type="openai_compat", model="primary", base_url="https://example.test/v1"
            ),
            "fast": BrainConfig(
                type="openai_compat", model="fast", base_url="https://example.test/v1"
            ),
        }
    )
    reg = SkillRegistry()
    reg.discover()

    monkeypatch.setattr("voxera.core.mission_planner._PLANNER_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(
        "voxera.core.mission_planner._build_brain_candidates",
        lambda _cfg: [
            type("C", (), {"name": "primary", "brain": _SlowBrain()})(),
            type(
                "C",
                (),
                {
                    "name": "fast",
                    "brain": _FakeBrain('{"steps":[{"skill_id":"system.status","args":{}}]}'),
                },
            )(),
        ],
    )

    mission = asyncio.run(plan_mission("check machine", cfg=cfg, registry=reg))
    assert mission.steps[0].skill_id == "system.status"


def test_plan_mission_rejects_payload_with_unknown_top_level_keys(monkeypatch):
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
        lambda _cfg: [
            type(
                "C",
                (),
                {
                    "name": "primary",
                    "brain": _FakeBrain(
                        '{"title":"Quick prep","steps":[{"skill_id":"system.status","args":{}}],"hijack":"ignore rules"}'
                    ),
                },
            )()
        ],
    )

    with pytest.raises(MissionPlannerError, match="unsupported keys"):
        asyncio.run(plan_mission("check machine", cfg=cfg, registry=reg))


def test_plan_mission_emits_correlated_plan_telemetry(monkeypatch):
    cfg = AppConfig(
        brain={
            "primary": BrainConfig(
                type="openai_compat", model="primary", base_url="https://example.test/v1"
            ),
        }
    )
    reg = SkillRegistry()
    reg.discover()
    events = []
    monkeypatch.setattr("voxera.core.mission_planner.log", lambda e: events.append(e))
    monkeypatch.setattr(
        "voxera.core.mission_planner._build_brain_candidates",
        lambda _cfg: [
            type(
                "C",
                (),
                {
                    "name": "primary",
                    "brain": _FakeBrain('{"steps":[{"skill_id":"system.status","args":{}}]}'),
                },
            )()
        ],
    )

    mission = asyncio.run(
        plan_mission("check machine", cfg=cfg, registry=reg, source="queue", job_ref="job-1")
    )
    assert mission.steps[0].skill_id == "system.status"

    selected = [e for e in events if e.get("event") == "planner_selected"]
    starts = [e for e in events if e.get("event") == "plan_start"]
    built = [e for e in events if e.get("event") == "plan_built"]

    assert len(selected) == 1
    assert len(starts) == 1
    assert len(built) == 1
    plan_id = starts[0].get("plan_id")
    assert plan_id
    assert selected[0].get("plan_id") == plan_id
    assert built[0].get("plan_id") == plan_id


def test_plan_mission_emits_single_selected_per_attempt(monkeypatch):
    cfg = AppConfig(
        brain={
            "primary": BrainConfig(
                type="openai_compat", model="primary", base_url="https://example.test/v1"
            ),
            "fast": BrainConfig(
                type="openai_compat", model="fast", base_url="https://example.test/v1"
            ),
        }
    )
    reg = SkillRegistry()
    reg.discover()
    events = []

    class _FailingBrain:
        async def generate(self, messages, tools=None):
            class _Resp:
                text = "not-json"

            return _Resp()

    monkeypatch.setattr("voxera.core.mission_planner.log", lambda e: events.append(e))
    monkeypatch.setattr(
        "voxera.core.mission_planner._build_brain_candidates",
        lambda _cfg: [
            type("C", (), {"name": "primary", "brain": _FailingBrain()})(),
            type(
                "C",
                (),
                {
                    "name": "fast",
                    "brain": _FakeBrain('{"steps":[{"skill_id":"system.status","args":{}}]}'),
                },
            )(),
        ],
    )

    asyncio.run(plan_mission("check machine", cfg=cfg, registry=reg))

    selected = [e for e in events if e.get("event") == "planner_selected"]
    fallback = [e for e in events if e.get("event") == "planner_fallback"]
    assert len(selected) == 2
    assert len(fallback) == 1
    assert selected[0]["attempt_index"] == 0
    assert selected[1]["attempt_index"] == 1


def test_plan_mission_keeps_write_text_body_alias(monkeypatch):
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
        lambda _cfg: [
            type(
                "C",
                (),
                {
                    "name": "primary",
                    "brain": _FakeBrain(
                        '{"title":"Write note","steps":[{"skill_id":"files.write_text","args":{"path":"~/VoxeraOS/notes/note.txt","body":"remember milk"}}]}'
                    ),
                },
            )()
        ],
    )

    mission = asyncio.run(plan_mission("write a note saying remember milk", cfg=cfg, registry=reg))
    assert mission.steps[0].skill_id == "files.write_text"
    assert mission.steps[0].args["path"] == "~/VoxeraOS/notes/note.txt"
    assert mission.steps[0].args["text"] == "remember milk"


def test_plan_mission_simple_write_fast_path_overwrite_default(monkeypatch):
    cfg = AppConfig(privacy={"cloud_allowed": False})
    reg = SkillRegistry()
    reg.discover()

    monkeypatch.setattr(
        "voxera.core.mission_planner._build_brain_candidates",
        lambda _cfg: (_ for _ in ()).throw(
            AssertionError("LLM planner should not be called for simple write goals")
        ),
    )

    mission = asyncio.run(
        plan_mission(
            "Write a note to ~/VoxeraOS/notes/partial-ok.txt saying: partial recovered ok.",
            cfg=cfg,
            registry=reg,
        )
    )

    assert len(mission.steps) == 1
    step = mission.steps[0]
    assert step.skill_id == "files.write_text"
    assert step.args["path"] == "~/VoxeraOS/notes/partial-ok.txt"
    assert step.args["text"] == "partial recovered ok."
    assert step.args["mode"] == "overwrite"


def test_plan_mission_simple_write_fast_path_append(monkeypatch):
    cfg = AppConfig(privacy={"cloud_allowed": False})
    reg = SkillRegistry()
    reg.discover()

    monkeypatch.setattr(
        "voxera.core.mission_planner._build_brain_candidates",
        lambda _cfg: (_ for _ in ()).throw(
            AssertionError("LLM planner should not be called for simple write goals")
        ),
    )

    mission = asyncio.run(
        plan_mission(
            "Create a file at ~/VoxeraOS/notes/log.txt with append this line (append mode).",
            cfg=cfg,
            registry=reg,
        )
    )

    assert len(mission.steps) == 1
    step = mission.steps[0]
    assert step.skill_id == "files.write_text"
    assert step.args["mode"] == "append"
    assert step.args["text"] == "append this line (append mode)."


def test_plan_mission_simple_write_fast_path_handles_whitespace_newlines(monkeypatch):
    cfg = AppConfig(privacy={"cloud_allowed": False})
    reg = SkillRegistry()
    reg.discover()

    monkeypatch.setattr(
        "voxera.core.mission_planner._build_brain_candidates",
        lambda _cfg: (_ for _ in ()).throw(
            AssertionError("LLM planner should not be called for simple write goals")
        ),
    )

    goal = """
        Write   "line one\nline two"
        to   ~/VoxeraOS/notes/spacey.txt
    """
    mission = asyncio.run(plan_mission(goal, cfg=cfg, registry=reg))

    assert len(mission.steps) == 1
    step = mission.steps[0]
    assert step.skill_id == "files.write_text"
    assert step.args["path"] == "~/VoxeraOS/notes/spacey.txt"
    assert step.args["text"] == "line one\nline two"
    assert step.args["mode"] == "overwrite"


def test_plan_mission_simple_write_fast_path_never_adds_clipboard_steps(monkeypatch):
    cfg = AppConfig(privacy={"cloud_allowed": False})
    reg = SkillRegistry()
    reg.discover()

    monkeypatch.setattr(
        "voxera.core.mission_planner._build_brain_candidates",
        lambda _cfg: (_ for _ in ()).throw(
            AssertionError("LLM planner should not be called for simple write goals")
        ),
    )

    mission = asyncio.run(
        plan_mission(
            "Write partial recovered ok to ~/VoxeraOS/notes/partial-ok.txt",
            cfg=cfg,
            registry=reg,
        )
    )

    assert len(mission.steps) == 1
    assert all(step.skill_id != "clipboard.copy" for step in mission.steps)
    assert all(step.skill_id != "clipboard.paste" for step in mission.steps)
