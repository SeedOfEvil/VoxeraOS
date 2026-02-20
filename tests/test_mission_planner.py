import asyncio
import json

import pytest

from voxera.core.mission_planner import MissionPlannerError, _parse_planner_json, plan_mission
from voxera.models import AppConfig, BrainConfig
from voxera.skills.registry import SkillRegistry


def test_parse_planner_json_accepts_raw_json():
    parsed = _parse_planner_json('{"steps":[{"skill_id":"system.status","args":{}}]}')

    assert parsed["steps"][0]["skill_id"] == "system.status"


def test_parse_planner_json_rejects_fenced_json_block():
    with pytest.raises(MissionPlannerError, match="non-JSON output"):
        _parse_planner_json("""```json
{"steps":[{"skill_id":"system.status","args":{}}]}
```""")


def test_parse_planner_json_rejects_commentary_around_json():
    with pytest.raises(MissionPlannerError, match="non-JSON output"):
        _parse_planner_json("""I will now provide the mission plan.
{"steps":[{"skill_id":"system.status","args":{}}]}
""")


def test_parse_planner_json_rejects_non_object_json():
    with pytest.raises(MissionPlannerError, match="JSON object"):
        _parse_planner_json('[{"skill_id":"system.status","args":{}}]')


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


def test_plan_mission_rewrites_non_explicit_files_write_to_clipboard(monkeypatch):
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
                        '{"title":"Check","steps":[{"skill_id":"system.status","args":{}},{"skill_id":"files.write_text","args":{"path":"~/VoxeraOS/notes/result.txt","text":"Check complete"}}]}'
                    ),
                },
            )()
        ],
    )

    mission = asyncio.run(plan_mission("check machine", cfg=cfg, registry=reg))

    assert len(mission.steps) == 2
    assert mission.steps[0].skill_id == "system.status"
    assert mission.steps[1].skill_id == "clipboard.copy"
    assert mission.steps[1].args["text"] == "Check complete"


def test_plan_mission_keeps_files_write_text_for_explicit_write_goal(monkeypatch):
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
                        '{"title":"Write note","steps":[{"skill_id":"files.write_text","args":{"path":"~/VoxeraOS/notes/note.txt","text":"remember milk"}}]}'
                    ),
                },
            )()
        ],
    )

    mission = asyncio.run(
        plan_mission("Write remember milk to ~/VoxeraOS/notes/note.txt", cfg=cfg, registry=reg)
    )

    assert mission.steps[0].skill_id == "files.write_text"
    assert mission.steps[0].args["text"] == "remember milk"


def test_plan_mission_rewrites_non_explicit_sandbox_gui_or_network_exec_to_clipboard(monkeypatch):
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
                        json.dumps(
                            {
                                "title": "E2E Ask",
                                "steps": [
                                    {
                                        "skill_id": "sandbox.exec",
                                        "args": {
                                            "command": [
                                                "bash",
                                                "-lc",
                                                'title=$(xdotool getactivewindow getwindowname) && echo $title | grep "Example"',
                                            ]
                                        },
                                    },
                                    {
                                        "skill_id": "sandbox.exec",
                                        "args": {
                                            "command": [
                                                "bash",
                                                "-lc",
                                                "curl -I https://example.com",
                                            ]
                                        },
                                    },
                                ],
                            }
                        )
                    ),
                },
            )()
        ],
    )

    mission = asyncio.run(plan_mission("check app title and smoke", cfg=cfg, registry=reg))

    assert len(mission.steps) == 2
    assert all(step.skill_id == "clipboard.copy" for step in mission.steps)
    assert "Example" in mission.steps[0].args["text"]


def test_plan_mission_normalizes_sandbox_exec_string_command_to_argv(monkeypatch):
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
                        '{"title":"Shell","steps":[{"skill_id":"sandbox.exec","args":{"command":"echo HELLO-ARGV"}}]}'
                    ),
                },
            )()
        ],
    )

    mission = asyncio.run(plan_mission("Run a shell command", cfg=cfg, registry=reg))

    assert len(mission.steps) == 1
    step = mission.steps[0]
    assert step.skill_id == "sandbox.exec"
    assert isinstance(step.args["command"], list)
    assert step.args["command"] == ["bash", "-lc", "echo HELLO-ARGV"]


def test_plan_mission_rejects_empty_sandbox_exec_string_command(monkeypatch):
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
                        '{"title":"Shell","steps":[{"skill_id":"sandbox.exec","args":{"command":"   "}}]}'
                    ),
                },
            )()
        ],
    )

    with pytest.raises(MissionPlannerError, match="sandbox.exec command must be a non-empty list"):
        asyncio.run(plan_mission("Run a shell command", cfg=cfg, registry=reg))


def test_plan_mission_rejects_invalid_sandbox_exec_command_list(monkeypatch):
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
                        '{"title":"Shell","steps":[{"skill_id":"sandbox.exec","args":{"command":["bash","  ","echo HELLO-ARGV"]}}]}'
                    ),
                },
            )()
        ],
    )

    with pytest.raises(MissionPlannerError, match="sandbox.exec command must be a non-empty list"):
        asyncio.run(plan_mission("Run a shell command", cfg=cfg, registry=reg))


def test_plan_mission_keeps_explicit_shell_intent_for_sandbox_exec(monkeypatch):
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
                        '{"title":"Explicit","steps":[{"skill_id":"sandbox.exec","args":{"command":["bash","-lc","curl -I https://example.com"]}}]}'
                    ),
                },
            )()
        ],
    )

    mission = asyncio.run(
        plan_mission("Please run this shell command to test endpoint health", cfg=cfg, registry=reg)
    )

    assert len(mission.steps) == 1
    assert mission.steps[0].skill_id == "sandbox.exec"


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


def test_plan_mission_defaults_relative_ok_txt_for_allowed_notes_goal_without_path(monkeypatch):
    cfg = AppConfig(privacy={"cloud_allowed": False})
    reg = SkillRegistry()
    reg.discover()

    monkeypatch.setattr(
        "voxera.core.mission_planner._build_brain_candidates",
        lambda _cfg: (_ for _ in ()).throw(
            AssertionError(
                "LLM planner should not be called for allowed-notes implicit write goals"
            )
        ),
    )

    mission = asyncio.run(
        plan_mission(
            "Write a note under the allowed notes directory saying all good.",
            cfg=cfg,
            registry=reg,
        )
    )

    assert len(mission.steps) == 1
    step = mission.steps[0]
    assert step.skill_id == "files.write_text"
    assert step.args["path"] == "ok.txt"


def test_plan_mission_allowed_notes_goal_extracts_quoted_text_payload(monkeypatch):
    cfg = AppConfig(privacy={"cloud_allowed": False})
    reg = SkillRegistry()
    reg.discover()

    monkeypatch.setattr(
        "voxera.core.mission_planner._build_brain_candidates",
        lambda _cfg: (_ for _ in ()).throw(
            AssertionError(
                "LLM planner should not be called for allowed-notes implicit write goals"
            )
        ),
    )

    mission = asyncio.run(
        plan_mission(
            "Write the text 'BOUNCE' to a notes file under the allowed notes directory.",
            cfg=cfg,
            registry=reg,
        )
    )

    assert len(mission.steps) == 1
    step = mission.steps[0]
    assert step.skill_id == "files.write_text"
    assert step.args["path"] == "ok.txt"
    assert step.args["text"] == "BOUNCE"


def test_plan_mission_rewrites_placeholder_notes_path_to_relative(monkeypatch):
    cfg = AppConfig(
        privacy={"cloud_allowed": True},
        brain={
            "primary": BrainConfig(
                type="openai_compat", model="test-model", base_url="https://example.test/v1"
            )
        },
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
                        '{"title":"Write note","steps":[{"skill_id":"files.write_text","args":{"path":"/path/to/notes.txt","text":"ok"}}]}'
                    ),
                },
            )()
        ],
    )

    mission = asyncio.run(
        plan_mission(
            "Write under the allowed notes directory",
            cfg=cfg,
            registry=reg,
        )
    )

    assert len(mission.steps) == 1
    step = mission.steps[0]
    assert step.skill_id == "files.write_text"
    assert step.args["path"] == "ok.txt"
