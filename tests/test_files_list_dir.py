from voxera.skills.result_contract import SKILL_RESULT_KEY
from voxera_builtin_skills import files_list_dir


def test_list_dir_allowed_path_succeeds(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    allowed_root.mkdir(parents=True)
    (allowed_root / "a.txt").write_text("a", encoding="utf-8")
    (allowed_root / "sub").mkdir()
    (allowed_root / ".hidden").write_text("h", encoding="utf-8")
    monkeypatch.setattr(files_list_dir, "ALLOWED_ROOT", allowed_root)

    rr = files_list_dir.run(str(allowed_root))

    assert rr.ok is True
    entries = rr.data[SKILL_RESULT_KEY]["machine_payload"]["entries"]
    assert [item["name"] for item in entries] == ["a.txt", "sub"]


def test_list_dir_include_hidden_includes_dotfiles(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    allowed_root.mkdir(parents=True)
    (allowed_root / ".hidden").write_text("h", encoding="utf-8")
    monkeypatch.setattr(files_list_dir, "ALLOWED_ROOT", allowed_root)

    rr = files_list_dir.run(str(allowed_root), include_hidden=True)

    assert rr.ok is True
    entries = rr.data[SKILL_RESULT_KEY]["machine_payload"]["entries"]
    assert [item["name"] for item in entries] == [".hidden"]


def test_list_dir_rejects_traversal(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    allowed_root.mkdir(parents=True)
    monkeypatch.setattr(files_list_dir, "ALLOWED_ROOT", allowed_root)

    rr = files_list_dir.run("../outside")

    assert rr.ok is False
    assert rr.data[SKILL_RESULT_KEY]["error_class"] == "path_out_of_bounds"
