from types import SimpleNamespace

from voxera import cli


class _FakeRegistry:
    def discover(self):
        return {}

    def get(self, skill_id):
        return SimpleNamespace(id=skill_id, name="x", description="x", risk="low")


class _FakeSimulation:
    def __init__(self, args):
        self._args = args

    def model_dump(self):
        return {"steps": [{"args": self._args}]}


class _FakeRunner:
    captured_args = None

    def __init__(self, _registry):
        pass

    def simulate(self, _manifest, args, policy):
        _FakeRunner.captured_args = args
        return _FakeSimulation(args)


def test_run_accepts_multiple_arg_options(monkeypatch):
    monkeypatch.setattr(cli, "load_config", lambda: SimpleNamespace(policy=object()))
    monkeypatch.setattr(cli, "SkillRegistry", _FakeRegistry)
    monkeypatch.setattr(cli, "SkillRunner", _FakeRunner)

    cli.run(
        skill_id="files.write_text",
        arg=["path=/tmp/test.txt", "text=hi"],
        dry_run=True,
    )

    assert _FakeRunner.captured_args == {"path": "/tmp/test.txt", "text": "hi"}
