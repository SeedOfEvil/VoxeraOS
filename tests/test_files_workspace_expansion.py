from voxera.skills.result_contract import SKILL_RESULT_KEY
from voxera_builtin_skills import (
    files_copy,
    files_find,
    files_grep_text,
    files_list_tree,
    files_move,
    files_rename,
)


def test_files_find_happy_path(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    (allowed_root / "docs").mkdir(parents=True)
    (allowed_root / "docs" / "alpha.txt").write_text("alpha", encoding="utf-8")
    (allowed_root / "docs" / "beta.md").write_text("beta", encoding="utf-8")
    monkeypatch.setattr(files_find, "ALLOWED_ROOT", allowed_root)

    rr = files_find.run(str(allowed_root / "docs"), glob="*.txt")

    assert rr.ok is True
    payload = rr.data[SKILL_RESULT_KEY]["machine_payload"]
    assert payload["result_count"] == 1
    assert payload["results"][0]["name"] == "alpha.txt"


def test_files_grep_text_happy_path(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    (allowed_root / "docs").mkdir(parents=True)
    target = allowed_root / "docs" / "sample.txt"
    target.write_text("hello\nneedle here\nbye\n", encoding="utf-8")
    monkeypatch.setattr(files_grep_text, "ALLOWED_ROOT", allowed_root)

    rr = files_grep_text.run(str(allowed_root / "docs"), pattern="needle")

    assert rr.ok is True
    payload = rr.data[SKILL_RESULT_KEY]["machine_payload"]
    assert payload["match_count"] == 1
    assert payload["matches"][0]["line_number"] == 2


def test_files_list_tree_happy_path(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    (allowed_root / "a" / "b").mkdir(parents=True)
    (allowed_root / "a" / "b" / "x.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(files_list_tree, "ALLOWED_ROOT", allowed_root)

    rr = files_list_tree.run(str(allowed_root), max_depth=3)

    assert rr.ok is True
    payload = rr.data[SKILL_RESULT_KEY]["machine_payload"]
    assert payload["directory_count"] >= 3
    assert payload["file_count"] == 1


def test_files_copy_move_and_rename_happy_path(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    (allowed_root / "src").mkdir(parents=True)
    (allowed_root / "dest").mkdir(parents=True)
    source = allowed_root / "src" / "hello.txt"
    source.write_text("hello", encoding="utf-8")

    monkeypatch.setattr(files_copy, "ALLOWED_ROOT", allowed_root)
    monkeypatch.setattr(files_move, "ALLOWED_ROOT", allowed_root)
    monkeypatch.setattr(files_rename, "ALLOWED_ROOT", allowed_root)

    rr_copy = files_copy.run(str(source), str(allowed_root / "dest" / "copy.txt"))
    assert rr_copy.ok is True

    rr_move = files_move.run(
        str(allowed_root / "dest" / "copy.txt"),
        str(allowed_root / "dest" / "moved.txt"),
    )
    assert rr_move.ok is True

    rr_rename = files_rename.run(str(allowed_root / "dest" / "moved.txt"), "renamed.txt")
    assert rr_rename.ok is True
    assert (allowed_root / "dest" / "renamed.txt").exists()


def test_files_copy_missing_source_reports_not_found(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    allowed_root.mkdir(parents=True)
    monkeypatch.setattr(files_copy, "ALLOWED_ROOT", allowed_root)

    rr = files_copy.run("missing.txt", "dest.txt")

    assert rr.ok is False
    assert rr.data[SKILL_RESULT_KEY]["error_class"] == "not_found"


def test_files_rename_rejects_invalid_new_name(tmp_path, monkeypatch):
    allowed_root = tmp_path / "notes"
    source = allowed_root / "src.txt"
    source.parent.mkdir(parents=True)
    source.write_text("x", encoding="utf-8")
    monkeypatch.setattr(files_rename, "ALLOWED_ROOT", allowed_root)

    rr = files_rename.run(str(source), "../escape")

    assert rr.ok is False
    assert rr.data[SKILL_RESULT_KEY]["error_class"] == "invalid_input"
