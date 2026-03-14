from voxera.skills.result_contract import SKILL_RESULT_KEY
from voxera_builtin_skills import files_move_file


def test_move_file_succeeds(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    allowed_root.mkdir(parents=True)
    source = allowed_root / "source.txt"
    destination = allowed_root / "renamed.txt"
    source.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(files_move_file, "ALLOWED_ROOT", allowed_root)

    rr = files_move_file.run(str(source), str(destination))

    assert rr.ok is True
    assert destination.read_text(encoding="utf-8") == "hello"
    assert not source.exists()


def test_move_file_rejects_existing_destination_without_overwrite(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    allowed_root.mkdir(parents=True)
    source = allowed_root / "source.txt"
    destination = allowed_root / "renamed.txt"
    source.write_text("hello", encoding="utf-8")
    destination.write_text("old", encoding="utf-8")
    monkeypatch.setattr(files_move_file, "ALLOWED_ROOT", allowed_root)

    rr = files_move_file.run(str(source), str(destination), overwrite=False)

    assert rr.ok is False
    assert rr.data[SKILL_RESULT_KEY]["error_class"] == "already_exists"


def test_move_file_rejects_outside_allowlist(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    allowed_root.mkdir(parents=True)
    source = allowed_root / "source.txt"
    source.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(files_move_file, "ALLOWED_ROOT", allowed_root)

    rr = files_move_file.run(str(source), str(tmp_path / "outside.txt"))

    assert rr.ok is False
    assert rr.data[SKILL_RESULT_KEY]["error_class"] == "path_out_of_bounds"
