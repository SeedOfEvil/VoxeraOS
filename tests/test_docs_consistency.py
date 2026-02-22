from pathlib import Path


def test_docs_use_repository_root_phrasing_for_make_commands():
    targets = [Path("README.md"), Path("docs/ops.md"), Path("docs/BOOTSTRAP.md")]
    for target in targets:
        text = target.read_text(encoding="utf-8")
        assert "voxera-os-scaffold/voxera-os" not in text


def test_core_operational_commands_are_documented():
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "voxera queue init" in readme
    assert "make services-install" in readme
    assert "make merge-readiness-check" in readme
    assert "merge-readiness / merge-readiness" in readme

    ops = Path("docs/ops.md").read_text(encoding="utf-8")
    assert "voxera queue init" in ops
    assert "make update" in ops
    assert "from repository root" in ops.lower()
    assert "make merge-readiness-check" in ops


def test_merge_readiness_workflow_exists():
    workflow = Path(".github/workflows/merge-readiness.yml").read_text(encoding="utf-8")
    assert "name: merge-readiness" in workflow
    assert "make merge-readiness-check" in workflow
