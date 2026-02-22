from pathlib import Path


def test_update_script_uses_repo_root_as_project_dir():
    script = Path("scripts/update.sh").read_text(encoding="utf-8")
    assert 'project_dir="$repo_root"' in script
    assert "voxera-os-scaffold/voxera-os" not in script
