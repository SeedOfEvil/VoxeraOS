from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from voxera.models import AppConfig
from voxera.skills.registry import SkillRegistry
from voxera.skills.runner import SkillRunner


@pytest.mark.skipif(shutil.which("podman") is None, reason="podman not installed")
def test_sandbox_exec_runs_in_podman(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    reg = SkillRegistry()
    reg.discover()
    manifest = reg.get("sandbox.exec")
    runner = SkillRunner(reg, config=AppConfig())

    result = runner.run(
        manifest,
        args={"command": ["bash", "-lc", "echo hi; touch /work/ok"]},
        policy=AppConfig().policy,
    )

    assert result.ok is True
    workspace = Path(result.data["workspace_dir"])
    artifacts = Path(result.data["artifacts_dir"])
    assert (workspace / "ok").exists()
    assert "hi" in (artifacts / "stdout.txt").read_text(encoding="utf-8")
    assert (artifacts / "runner.json").exists()
    assert (artifacts / "stderr.txt").exists()
    assert (artifacts / "command.txt").exists()


@pytest.mark.skipif(shutil.which("podman") is None, reason="podman not installed")
def test_sandbox_exec_network_blocked_default(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    reg = SkillRegistry()
    reg.discover()
    manifest = reg.get("sandbox.exec")
    runner = SkillRunner(reg, config=AppConfig())

    result = runner.run(
        manifest,
        args={"command": ["bash", "-lc", "getent hosts example.com >/dev/null"]},
        policy=AppConfig().policy,
    )
    assert result.ok is False
