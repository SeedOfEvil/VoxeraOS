from __future__ import annotations

from pathlib import Path

from voxera.models import SkillManifest
from voxera.skills.execution import (
    LocalRunner,
    PodmanSandboxRunner,
    ensure_job_paths,
    select_runner,
)
from voxera.skills.registry import SkillRegistry


def test_skill_manifest_defaults():
    manifest = SkillManifest(
        id="x",
        name="X",
        description="d",
        entrypoint="voxera_builtin_skills.system_status:run",
    )
    assert manifest.risk == "low"
    assert manifest.exec_mode == "local"
    assert manifest.needs_network is False
    assert manifest.fs_scope == "workspace_only"


def test_registry_parses_exec_metadata():
    reg = SkillRegistry()
    manifests = reg.discover()
    sandbox = manifests["sandbox.exec"]
    assert sandbox.exec_mode == "sandbox"
    assert sandbox.risk == "medium"
    assert sandbox.needs_network is False
    assert sandbox.fs_scope == "workspace_only"


def test_runner_selection_by_exec_mode():
    local_manifest = SkillManifest(
        id="x",
        name="x",
        description="x",
        entrypoint="voxera_builtin_skills.system_status:run",
    )
    sandbox_manifest = SkillManifest(
        id="y",
        name="y",
        description="y",
        entrypoint="voxera_builtin_skills.system_status:run",
        exec_mode="sandbox",
    )

    assert isinstance(select_runner(local_manifest), LocalRunner)
    assert isinstance(select_runner(sandbox_manifest), PodmanSandboxRunner)


def test_job_paths_creation(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    paths = ensure_job_paths("job-123")
    assert paths.workspace_dir == tmp_path / ".voxera" / "workspace" / "job-123"
    assert paths.artifacts_dir == tmp_path / ".voxera" / "artifacts" / "job-123"
    assert paths.workspace_dir.exists()
    assert paths.artifacts_dir.exists()
    assert (tmp_path / ".voxera" / "cache").exists()


def test_builtin_skill_metadata_has_foundational_governance_fields():
    reg = SkillRegistry()
    manifests = reg.discover()

    for manifest in manifests.values():
        assert manifest.output_schema == "skill_result.v1"

        if manifest.id == "sandbox.exec":
            assert manifest.fs_scope == "workspace_only"
            assert manifest.output_artifacts == [
                "stdout.txt",
                "stderr.txt",
                "runner.json",
                "command.txt",
            ]
            continue

        assert manifest.exec_mode == "local"
        if manifest.id in {
            "files.read_text",
            "files.write_text",
            "files.copy_file",
            "files.move_file",
            "files.mkdir",
            "files.delete_file",
        }:
            assert manifest.fs_scope == "workspace_only"
        elif manifest.id in {"files.list_dir", "files.exists", "files.stat"}:
            assert manifest.fs_scope == "read_only"
        elif manifest.id == "system.open_url":
            assert manifest.fs_scope == "broader"
            assert manifest.needs_network is True
        else:
            assert manifest.fs_scope == "read_only"
            assert manifest.needs_network is False

        assert manifest.output_artifacts == []
