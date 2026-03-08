from pathlib import Path

import pytest

from voxera.skills.registry import SkillRegistry


def test_discover_skills():
    reg = SkillRegistry()
    m = reg.discover()
    assert "system.status" in m
    assert "system.open_app" in m


def test_discover_with_report_classifies_incomplete_manifest(tmp_path: Path):
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "manifest.yml").write_text(
        """
id: demo.skill
name: Demo Skill
description: Missing capabilities and output schema.
entrypoint: voxera_builtin_skills.system_status:run
""".strip()
        + "\n",
        encoding="utf-8",
    )

    reg = SkillRegistry(skills_dir=tmp_path / "skills")
    report = reg.discover_with_report()

    assert report.counts["valid"] == 0
    assert report.counts["incomplete"] == 1
    assert report.counts["warning"] == 0
    assert any(issue.reason_code == "missing_capability_metadata" for issue in report.issues)


def test_discover_with_report_flags_malformed_capabilities(tmp_path: Path):
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "manifest.yml").write_text(
        """
id: demo.skill
name: Demo Skill
description: Malformed capabilities
entrypoint: voxera_builtin_skills.system_status:run
capabilities: ["state.read", ""]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    reg = SkillRegistry(skills_dir=tmp_path / "skills")
    report = reg.discover_with_report()

    assert report.counts["invalid"] == 1
    assert any(issue.reason_code == "malformed_schema" for issue in report.issues)


def test_discover_malformed_manifest_raises(tmp_path: Path):
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "manifest.yml").write_text(
        """
id: demo.skill
name: Demo Skill
description: unknown cap
entrypoint: voxera_builtin_skills.system_status:run
capabilities: ["made.up.cap"]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    reg = SkillRegistry(skills_dir=tmp_path / "skills")
    with pytest.raises(ValueError, match="Invalid skill manifest"):
        reg.discover()


def test_discover_with_report_mixed_valid_and_invalid_is_stable(tmp_path: Path):
    valid_dir = tmp_path / "skills" / "valid"
    valid_dir.mkdir(parents=True)
    (valid_dir / "manifest.yml").write_text(
        """
id: demo.valid
name: Demo Valid
description: Valid skill
entrypoint: voxera_builtin_skills.system_status:run
capabilities: ["state.read"]
output_schema: skill_result.v1
""".strip()
        + "\n",
        encoding="utf-8",
    )

    bad_dir = tmp_path / "skills" / "bad"
    bad_dir.mkdir(parents=True)
    (bad_dir / "manifest.yml").write_text(
        """
id: demo.bad
name: Demo Bad
description: Invalid capability
entrypoint: voxera_builtin_skills.system_status:run
capabilities: ["unknown.cap"]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    reg = SkillRegistry(skills_dir=tmp_path / "skills")
    report = reg.discover_with_report()

    assert sorted(report.valid.keys()) == ["demo.valid"]
    assert report.counts == {"valid": 1, "invalid": 1, "incomplete": 0, "warning": 0, "total": 2}
    assert [issue.reason_code for issue in report.issues] == ["unknown_capability_metadata"]
