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
    assert "make vera" in readme
    assert "make vera-status" in readme
    assert "make vera-logs" in readme
    assert "make merge-readiness-check" in readme
    assert "make full-validation-check" in readme
    assert "make update-mypy-baseline" in readme
    assert "merge-readiness / merge-readiness" in readme

    ops = Path("docs/ops.md").read_text(encoding="utf-8")
    assert "voxera queue init" in ops
    assert "make update" in ops
    assert "from repository root" in ops.lower()
    assert "make merge-readiness-check" in ops
    assert "make full-validation-check" in ops
    assert "merge-readiness-logs" in ops
    assert "make vera-start" in ops
    assert "make vera-status" in ops
    assert "make vera-logs" in ops


def test_vera_service_contract_is_present():
    makefile = Path("Makefile").read_text(encoding="utf-8")
    assert (
        "VOXERA_UNITS := voxera-daemon.service voxera-panel.service voxera-vera.service voxera-automation.service voxera-automation.timer"
        in makefile
    )
    for target in (
        "vera:",
        "vera-start:",
        "vera-stop:",
        "vera-restart:",
        "vera-status:",
        "vera-logs:",
    ):
        assert target in makefile

    unit = Path("deploy/systemd/user/voxera-vera.service").read_text(encoding="utf-8")
    assert "Description=Voxera Vera (chat web app)" in unit
    assert ".venv/bin/python -m uvicorn voxera.vera_web.app:app" in unit
    assert "--host 127.0.0.1 --port 8790" in unit


def test_merge_readiness_workflow_exists():
    workflow = Path(".github/workflows/merge-readiness.yml").read_text(encoding="utf-8")
    assert "name: merge-readiness" in workflow
    assert "make quality-check" in workflow
    assert "make release-check" in workflow
    assert "Upload merge-readiness artifacts on failure" in workflow


def test_pre_push_hook_runs_merge_readiness():
    config = Path(".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "stages: [pre-push]" in config
    assert "make merge-readiness-check" in config
