from voxera.skills.result_contract import SKILL_RESULT_KEY
from voxera_builtin_skills import files_read_text


def test_read_text_allowed_path_succeeds(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    allowed_root.mkdir(parents=True)
    target = allowed_root / "ok.txt"
    target.write_text("ok", encoding="utf-8")
    monkeypatch.setattr(files_read_text, "ALLOWED_ROOT", allowed_root)

    rr = files_read_text.run(str(target))

    assert rr.ok is True
    assert rr.output == "ok"


def test_read_text_rejects_traversal(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    allowed_root.mkdir(parents=True)
    monkeypatch.setattr(files_read_text, "ALLOWED_ROOT", allowed_root)

    rr = files_read_text.run("../outside.txt")

    assert rr.ok is False
    assert rr.data[SKILL_RESULT_KEY]["error_class"] == "path_out_of_bounds"


def test_read_text_rejects_symlink_escape(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    outside = tmp_path / "outside.txt"
    allowed_root.mkdir(parents=True)
    outside.write_text("secret", encoding="utf-8")
    link = allowed_root / "link.txt"
    link.symlink_to(outside)
    monkeypatch.setattr(files_read_text, "ALLOWED_ROOT", allowed_root)

    rr = files_read_text.run(str(link))

    assert rr.ok is False
    assert rr.data[SKILL_RESULT_KEY]["error_class"] == "path_out_of_bounds"
